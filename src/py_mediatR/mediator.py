"""py_mediatR.mediator — Mediator, ISender / IPublisher, pipeline compilation.

v6.7.0'da tek dosyalik `py_mediatR.py` alt modullere ayrildi.
Kod govdesi birebir aynidir. Genel API icin `import py_mediatR` kullanin;
bu modul bir uygulama detayidir ve dogrudan import edilmesi gerekmez.
"""

import asyncio
import inspect
import logging
from contextlib import contextmanager
from functools import lru_cache
from threading import Lock
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    Type,
)

from ._config import _BACKGROUND_TASKS, _debug_log
from ._typechecks import _is_async_callable, _is_response_type, _is_stream_request_type
from .cancellation import _CURRENT_CT, CancellationToken, _invoke_handle
from .coercion import _maybe_await, _sync_run_coro, coerce_to_model
from .contracts import (  # noqa: F401
    _EXPLICIT_BEHAVIORS,
    _EXPLICIT_HANDLERS,
    IExceptionAction,
    IExceptionHandler,
    INotification,
    IPipelineBehavior,
    IRequest,
    IRequestPostProcessor,
    IRequestPreProcessor,
    IResponse,
    IStreamPipelineBehavior,
    IStreamRequest,
    PublishStrategy,
    TResponse,
    _DeferredHandler,
)
from .discovery import (  # noqa: F401
    _find_request_param_and_return_type,
    _instantiate_or_defer,
    discover_all,
)
from .tracing import _FLOW, FlowNode, _flow_begin, _flow_end, _flow_note, trace_flow

# ============================================================================
# SENDER / PUBLISHER INTERFACES (.NET ISender / IPublisher parity)
# ============================================================================

class ISender:
    """.NET ISender muadili — send / send_async / create_stream sözleşmesi.
    v6.5: generic TResponse çıkarımı ve cancellation_token, Mediator ile uyumlu.
    """
    def send(self, request: "IRequest[TResponse]", *,
             cancellation_token: Optional["CancellationToken"] = None
             ) -> TResponse:
        raise NotImplementedError

    async def send_async(self, request: "IRequest[TResponse]", *,
                         cancellation_token: Optional["CancellationToken"] = None
                         ) -> TResponse:
        raise NotImplementedError

    def create_stream(self, request: "IStreamRequest[TResponse]", *,
                      cancellation_token: Optional["CancellationToken"] = None
                      ) -> AsyncIterator[TResponse]:
        raise NotImplementedError


class IPublisher:
    """.NET IPublisher muadili — publish / publish_async sözleşmesi.
    v6.5: cancellation_token sözleşmesi Mediator ile uyumlu.
    """
    def publish(self, notification: INotification, *,
                cancellation_token: Optional["CancellationToken"] = None) -> None:
        raise NotImplementedError

    async def publish_async(self, notification: INotification,
                            strategy: Optional[PublishStrategy] = None,
                            publisher: Optional[Callable] = None, *,
                            cancellation_token: Optional["CancellationToken"] = None
                            ) -> None:
        raise NotImplementedError


class IMediator(ISender, IPublisher):
    """.NET IMediator muadili — ISender + IPublisher birleşimi."""
    pass


class _ServiceContainerLike(Protocol):
    """Minimal container contract needed by :meth:`Mediator.create_scope`."""

    def create_scope(self) -> Any:
        """Create a disposable service scope."""
        ...


# ============================================================================
# MEDIATOR
# ============================================================================

# ============================================================================
# v6.1 (additif): IoC CONTAINER KÖPRÜSÜ
# ============================================================================

class ExceptionHandlerState:
    """
    v6.4: .NET RequestExceptionHandlerState<TResponse> muadili.
    Exception handler hatayı işlediğini AÇIKÇA belirtmelidir:

        class MyExcHandler(IExceptionHandler):
            exception_type = ValueError
            def handle(self, request, exc, state):
                state.set_handled(FallbackResponse())

    state.set_handled(...) çağrılmazsa exception yeniden fırlatılır.
    Geriye dönük uyumluluk: 2 parametreli eski imza `handle(request, exc)`
    hâlâ çalışır; ancak v6.4'ten itibaren `None` dönüşü "işlenmedi" sayılır
    (None artık hatayı sessizce YUTMAZ).
    """
    __slots__ = ("handled", "response")

    def __init__(self) -> None:
        self.handled = False
        self.response: Any = None

    def set_handled(self, response: Any = None) -> None:
        self.handled = True
        self.response = response


@lru_cache(maxsize=512)
def _exc_handler_accepts_state(handler_cls: Any) -> bool:
    """handle() imzasında 3. parametre (state) var mı? (cached)"""
    try:
        params = [p for p in inspect.signature(handler_cls.handle).parameters.values()
                  if p.kind not in (inspect.Parameter.VAR_POSITIONAL,
                                    inspect.Parameter.VAR_KEYWORD)]
        if params and params[0].name == "self":  # unbound fonksiyon
            params = params[1:]
        return len(params) >= 3  # (request, exc, state)
    except (TypeError, ValueError):
        return False


def _invoke_exception_handler(eh: Any, request: Any, exc: BaseException):
    """
    v6.4 tek nokta: state-aware çağrı.
    Dönüş: (handled: bool, response, awaitable_or_None)
      • 3 parametreli handler → state.handled belirleyici
      • 2 parametreli (legacy) → dönüş None DEĞİLSE handled sayılır
    Async handler'lar için awaitable döner; çağıran await edip aynı kurala uyar.
    """
    if _exc_handler_accepts_state(type(eh)):  # type: ignore[arg-type]
        state = ExceptionHandlerState()
        result = eh.handle(request, exc, state)
        if inspect.isawaitable(result):
            async def _await_state():
                await result
                return (state.handled, state.response)
            return (None, None, _await_state())
        return (state.handled, state.response, None)

    result = eh.handle(request, exc)
    if inspect.isawaitable(result):
        async def _await_legacy():
            value = await result
            return (value is not None, value)
        return (None, None, _await_legacy())
    return (result is not None, result, None)


def container_handler_factory(container) -> Callable[[type], Any]:
    """
    🆕 v6.1: py_autowired/ioc_container tarzı bir IoC container'ı
    Mediator'ün handler_factory'sine bağlayan köprü.

    Container'dan beklenenler (duck typing):
      • resolve(cls)            — kayıtlı tipi çözer
      • registrations (dict)    — kayıt sözlüğü (opsiyonel ama önerilir)
      • register_transient(cls) — kayıt-anında ekleme (opsiyonel)

    Davranış:
      Handler sınıfı container'da kayıtlıysa doğrudan resolve edilir.
      Kayıtlı DEĞİLSE transient olarak anında kaydedilip resolve edilir —
      böylece container'ın annotation-tabanlı constructor injection'ı
      handler bağımlılıklarını da doldurur.

    Kullanım:
        mediator = Mediator(handler_factory=container_handler_factory(container))
        # veya kısaca (v6.1 otomatik sarmalama):
        mediator = Mediator(handler_factory=container)
    """
    regs = getattr(container, "registrations", None)
    register_transient = getattr(container, "register_transient", None)
    _bridge_lock = Lock()

    def _factory(cls: type) -> Any:
        if regs is not None and cls not in regs:
            if register_transient is None:
                raise RuntimeError(
                    f"{cls.__name__} is not registered in the container and the "
                    f"container does not support register_transient.")
            # nogil: kayıt-anında ekleme yarışını kilitle
            with _bridge_lock:
                if cls not in regs:
                    register_transient(cls)
        return container.resolve(cls)

    return _factory


class Mediator(IMediator):
    """
    CQRS Mediator — .NET MediatR paritesi. v3/v4 API tam korunmuştur.

    En basit kullanım (v3 uyumlu):
        mediator = Mediator(auto_discover=True)
        response = mediator.send(MyRequest(...))

    Cross-cutting behaviors ile:
        mediator = Mediator(
            auto_discover=True,
            behaviors=[LoggingBehavior(), PerformanceBehavior(threshold_ms=200)],
        )

    Notification pub/sub:
        mediator.publish(OrderPlaced(order_id="123"))
        await mediator.publish_async(OrderPlaced(...), PublishStrategy.PARALLEL_WHENALL)

    Async:
        await mediator.send_async(MyQuery(...))

    Streaming:
        async for item in mediator.create_stream(MyStreamQuery(...)):
            ...
    """

    __slots__ = (
        "_handlers",
        "_stream_handlers",
        "_notification_handlers",
        "_behaviors",
        "_stream_behaviors",
        "_pre_processors",
        "_post_processors",
        "_exception_handlers",
        "_exception_actions",
        "_compiled_chains",
        "_compiled_async_chains",
        "_compiled_stream_chains",
        "_chain_lock",
        "_swallow_notification_errors",
        "_default_publish_strategy",
        # v6
        "_handler_factory",
        "_handler_lifetime",
        "_singleton_cache",
        "_polymorphic_publish",
        "_custom_publisher",
        # v6.5
        "_override_stacks",
    )

    def __init__(
        self,
        *,
        auto_discover: bool = True,
        project_root=None,
        scan_paths: Optional[list] = None,
        parallel: bool = False,
        use_cache: bool = False,
        behaviors: Optional[List[IPipelineBehavior]] = None,
        stream_behaviors: Optional[List[IStreamPipelineBehavior]] = None,
        pre_processors: Optional[List[IRequestPreProcessor]] = None,
        post_processors: Optional[List[IRequestPostProcessor]] = None,
        exception_handlers: Optional[List[IExceptionHandler]] = None,
        exception_actions: Optional[List[IExceptionAction]] = None,
        # v6.4: default False — .NET ForeachAwaitPublisher gibi hatalar
        # PROPAGATE edilir. Eski (hata yutan) davranış için açıkça True verin.
        swallow_notification_errors: bool = False,
        default_publish_strategy: PublishStrategy = PublishStrategy.SEQUENTIAL,
        # ---- v6 (hepsi opsiyonel; default'lar v5 davranışını birebir korur) ----
        handler_factory: Optional[Callable[[type], Any]] = None,
        handler_lifetime: str = "singleton",
        polymorphic_publish: bool = False,
        custom_publisher: Optional[Callable[[List[Any], INotification], Awaitable[None]]] = None,
        use_explicit_registrations: bool = True,
        discover_behaviors: bool = False,
    ) -> None:
        """
        Args:
            auto_discover: handler'ları otomatik tara (default: True)
            project_root: proje kök dizini (None → otomatik tespit)
            scan_paths: sadece bu dizinleri tara (None → tüm proje)
            parallel: paralel import (büyük projeler için True)
            use_cache: discovery cache (default: False)
            behaviors: pipeline behavior listesi
            stream_behaviors: stream pipeline behavior listesi
            pre_processors: request öncesi processor listesi
            post_processors: request sonrası processor listesi
            exception_handlers: exception yutabilen handler listesi
            exception_actions: exception'ı yutmadan gözlemleyen action listesi
            swallow_notification_errors: publish sırasında handler hataları yutulsun mu?
            default_publish_strategy: publish_async için varsayılan strateji

            --- v6 ---
            handler_factory: DI container köprüsü — handler sınıfını alıp
                instance döndüren callable (örn. `container.resolve`).
                Verilirse parametreli-ctor'lu (deferred) handler'lar da çözülür.
            handler_lifetime: "singleton" (default, v5 davranışı) veya
                "transient" (her send/publish'te factory'den yeni instance).
                "transient" için handler_factory zorunludur.
            polymorphic_publish: True ise türetilmiş notification publish
                edildiğinde base tip handler'ları da tetiklenir (.NET covariance).
            custom_publisher: publish_async için özel dağıtım stratejisi
                (.NET INotificationPublisher muadili) —
                async def pub(handlers, notification) -> None
            use_explicit_registrations: @handler/@behavior decorator kayıtlarını
                işle (default: True — decorator kullanılmadıysa etkisi yoktur).
            discover_behaviors: @behavior ile işaretlenen behavior sınıflarını
                otomatik instantiate edip pipeline'a ekle (default: False).
        """
        self._handlers: Dict[Type[IRequest], Tuple[Any, Optional[Type[IResponse]]]] = {}
        self._stream_handlers: Dict[Type[IStreamRequest], Any] = {}
        self._notification_handlers: Dict[Type[INotification], List[Any]] = {}

        self._behaviors: List[IPipelineBehavior] = sorted(
            behaviors or [], key=lambda b: getattr(b, "order", 0))
        self._stream_behaviors: List[IStreamPipelineBehavior] = sorted(
            stream_behaviors or [], key=lambda b: getattr(b, "order", 0))
        self._pre_processors: List[IRequestPreProcessor] = sorted(
            pre_processors or [], key=lambda p: getattr(p, "order", 0))
        self._post_processors: List[IRequestPostProcessor] = sorted(
            post_processors or [], key=lambda p: getattr(p, "order", 0))
        self._exception_handlers: List[IExceptionHandler] = sorted(
            exception_handlers or [], key=lambda h: getattr(h, "order", 0))
        self._exception_actions: List[IExceptionAction] = sorted(
            exception_actions or [], key=lambda a: getattr(a, "order", 0))

        self._compiled_chains: Dict[Type[IRequest], Callable[[Any], Any]] = {}
        self._compiled_async_chains: Dict[Type[IRequest], Callable[[Any], Awaitable[Any]]] = {}
        self._compiled_stream_chains: Dict[Type[IStreamRequest], Callable[[Any], AsyncIterator[Any]]] = {}
        self._chain_lock = Lock()
        self._swallow_notification_errors = swallow_notification_errors
        self._default_publish_strategy = default_publish_strategy
        self._override_stacks: Dict[type, List[tuple]] = {}

        # ---- v6 alanları ----
        if handler_lifetime not in ("singleton", "transient"):
            raise ValueError("handler_lifetime must be 'singleton' or 'transient'.")
        if handler_lifetime == "transient" and handler_factory is None:
            raise ValueError("handler_factory is required when "
                             "handler_lifetime='transient'.")
        # ---- v6.1 (additif): handler_factory'ye doğrudan IoC container
        # nesnesi de verilebilir — `.resolve` metodu olan callable-olmayan
        # nesneler otomatik olarak container_handler_factory ile sarmalanır.
        # Mevcut callable kullanımları birebir aynen çalışır.
        if (handler_factory is not None
                and not callable(handler_factory)
                and hasattr(handler_factory, "resolve")):
            handler_factory = container_handler_factory(handler_factory)
        self._handler_factory = handler_factory
        self._handler_lifetime = handler_lifetime
        self._singleton_cache: Dict[type, Any] = {}
        self._polymorphic_publish = polymorphic_publish
        self._custom_publisher = custom_publisher

        if auto_discover:
            req_reg, stream_reg, notif_reg = discover_all(
                from_main=True,
                project_root=project_root,
                scan_paths=scan_paths,
                parallel=parallel,
                use_cache=use_cache,
            )
            self._handlers.update(req_reg)
            self._stream_handlers.update(stream_reg)
            for notif_type, handlers in notif_reg.items():
                self._notification_handlers.setdefault(notif_type, []).extend(handlers)
            # Notification handler'larını order'a göre sırala
            for notif_type in self._notification_handlers:
                self._notification_handlers[notif_type].sort(
                    key=lambda h: getattr(h, "order", 0))

            # v6: factory YOKSA deferred (DI bekleyen) handler'lar v5 davranışı
            # gibi elenir — böylece geriye dönük davranış birebir korunur.
            if self._handler_factory is None:
                self._drop_deferred_entries()

            if (not self._handlers and not self._stream_handlers
                    and not self._notification_handlers):
                import warnings
                warnings.warn(
                    "⚠️ No handlers discovered! Ensure:\n"
                    "  1. Handler classes have 'handle(self, request: IRequest)' method\n"
                    "  2. Request classes inherit from IRequest/IStreamRequest/INotification\n"
                    "  3. Handlers are in discoverable directories\n"
                    "  4. No import errors (set MEDIATR_DEBUG=1 for logs)",
                    RuntimeWarning,
                    stacklevel=2,
                )
            else:
                _debug_log(
                    f"✅ Mediator initialized: {len(self._handlers)} request handlers, "
                    f"{len(self._stream_handlers)} stream handlers, "
                    f"{sum(len(v) for v in self._notification_handlers.values())} notif handlers"
                )

        # v6: @handler decorator'ı ile explicit kaydedilen handler'lar
        if use_explicit_registrations and _EXPLICIT_HANDLERS:
            self._apply_explicit_handlers()

        # v6: @behavior decorator'ı ile işaretlenen behavior'lar (opt-in)
        if discover_behaviors and _EXPLICIT_BEHAVIORS:
            for beh_cls in _EXPLICIT_BEHAVIORS:
                try:
                    inst = (self._handler_factory(beh_cls)
                            if self._handler_factory else beh_cls())
                    self.add_behavior(inst)
                    _debug_log(f"🧩 Behavior registered: {beh_cls.__name__}")
                except Exception as e:
                    _debug_log(f"⚠️ Behavior init failed: {beh_cls.__name__}: {e}")

    # ---------------------------------------------------- v6 init yardımcıları

    def _drop_deferred_entries(self) -> None:
        """Factory yokken deferred handler'ları ele (v5 uyumlu davranış)."""
        for rt in [rt for rt, (h, _) in self._handlers.items()
                   if isinstance(h, _DeferredHandler)]:
            _debug_log(f"⏳→🗑 Deferred dropped (no factory): "
                       f"{self._handlers[rt][0].cls.__name__}")
            del self._handlers[rt]
        for rt in [rt for rt, h in self._stream_handlers.items()
                   if isinstance(h, _DeferredHandler)]:
            _debug_log(f"⏳→🗑 Deferred stream dropped: "
                       f"{self._stream_handlers[rt].cls.__name__}")
            del self._stream_handlers[rt]
        for nt in list(self._notification_handlers.keys()):
            kept = [h for h in self._notification_handlers[nt]
                    if not isinstance(h, _DeferredHandler)]
            if kept:
                self._notification_handlers[nt] = kept
            else:
                del self._notification_handlers[nt]

    def _apply_explicit_handlers(self) -> None:
        """@handler ile kaydedilen sınıfları registry'lere işler."""
        for cls, req_t, resp_t in _EXPLICIT_HANDLERS:
            if req_t is None:
                req_t, ret = _find_request_param_and_return_type(
                    cls.__name__, cls.__module__)
                if req_t is None:
                    _debug_log(f"⚠️ @handler {cls.__name__}: request tipi çözülemedi "
                               f"(tip ipucu veya request_type verin).")
                    continue
                if resp_t is None and ret is not None and _is_response_type(ret):
                    resp_t = ret
            entry: Any
            if self._handler_factory is not None:
                entry = _DeferredHandler(cls)
            else:
                entry = _instantiate_or_defer(cls)
                if isinstance(entry, _DeferredHandler):
                    _debug_log(f"⚠️ @handler {cls.__name__}: parametreli ctor için "
                               f"handler_factory gerekli — atlandı.")
                    continue
            if _is_stream_request_type(req_t):
                self._stream_handlers.setdefault(req_t, entry)
                self._compiled_stream_chains.pop(req_t, None)
            else:
                self._handlers.setdefault(req_t, (entry, resp_t))
                self._compiled_chains.pop(req_t, None)
                self._compiled_async_chains.pop(req_t, None)
            _debug_log(f"📌 Explicit handler: {cls.__name__} → {req_t.__name__}")

    # -------------------------------------------- v6 handler resolve (DI/lifetime)

    def _materialize(self, entry: Any) -> Any:
        """
        Registry girdisini kullanılabilir handler instance'ına çevirir.
        Factory yok + entry instance → sıfır ek maliyet (aynen döner).
        """
        if isinstance(entry, _DeferredHandler):
            if self._handler_factory is None:
                raise RuntimeError(
                    f"{entry.cls.__name__} DI bekliyor; Mediator'a "
                    f"handler_factory verin.")
            if self._handler_lifetime == "transient":
                return self._handler_factory(entry.cls)
            # nogil/free-threaded güvenliği: check-then-act yarışını lock ile
            # kapat — aksi halde iki thread aynı singleton'ı iki kez yaratabilir.
            inst = self._singleton_cache.get(entry.cls)
            if inst is None:
                with self._chain_lock:
                    inst = self._singleton_cache.get(entry.cls)
                    if inst is None:
                        inst = self._handler_factory(entry.cls)
                        self._singleton_cache[entry.cls] = inst
            return inst
        if self._handler_factory is not None and self._handler_lifetime == "transient":
            return self._handler_factory(type(entry))
        return entry

    def _make_resolver(self, entry: Any) -> Callable[[], Any]:
        """
        Chain derlemesi için handler çözücü closure üretir.
        Statik durumda (factory yok / singleton instance) doğrudan sabit
        instance döndüren lambda üretilir → chain performansı v5 ile aynı.
        """
        if (not isinstance(entry, _DeferredHandler)
                and not (self._handler_factory and self._handler_lifetime == "transient")):
            return lambda: entry
        return lambda: self._materialize(entry)

    # ---- internal: applies_to filtresi ----
    @staticmethod
    def _applies(x, req_type) -> bool:
        """
        v6: applies_to artık tek tip VEYA tuple olabilir.
        applies_to_subclasses = True ise issubclass ile eşleşir
        (default False → v5'in `is` semantiği birebir korunur).
        """
        at = getattr(x, "applies_to", None)
        if at is None:
            return True
        types = at if isinstance(at, tuple) else (at,)
        if getattr(x, "applies_to_subclasses", False):
            try:
                return issubclass(req_type, types)
            except TypeError:
                return False
        return any(t is req_type for t in types)

    def _has_pipeline(self) -> bool:
        return bool(self._behaviors or self._pre_processors
                    or self._post_processors or self._exception_handlers
                    or self._exception_actions)

    # ------------------------------------------------------------------ send

    def _send_core(self, request: IRequest) -> Any:
        """
        Request'i pipeline üzerinden handler'a gönder, response döndür (SYNC).
        Karmaşıklık: O(1) dict lookup + derlenmiş chain.
        """
        if isinstance(request, IStreamRequest):
            raise TypeError(
                f"{type(request).__name__} is an IStreamRequest; use "
                f"create_stream() instead of send().")

        pair = self._handlers.get(type(request))
        if pair is None:
            available = [req.__name__ for req in self._handlers.keys()]
            raise ValueError(
                f"❌ Handler not found for: {type(request).__name__}\n"
                f"   Registered handlers: {available or 'None'}\n"
                f"   Set MEDIATR_DEBUG=1 for discovery details."
            )

        handler, resp_type = pair
        span = _flow_begin("send", type(request).__name__)
        try:
            # FAST PATH — hiçbir cross-cutting yoksa sıfır overhead
            if not self._has_pipeline():
                # v6: deferred/transient handler'ı çöz (factory yoksa no-op)
                if isinstance(handler, _DeferredHandler) or self._handler_factory is not None:
                    handler = self._materialize(handler)
                inner = _flow_begin("handler", type(handler).__name__,
                                    "no pipeline - fast path")
                try:
                    result = _invoke_handle(handler, request)
                finally:
                    _flow_end(inner)
                if inspect.isawaitable(result):
                    raise TypeError(
                        f"{type(handler).__name__}.handle returned an awaitable; use "
                        f"send_async() instead of the synchronous send().")
                return coerce_to_model(result, resp_type) if resp_type else result

            # SLOW PATH — derlenmiş sync chain
            chain = self._get_or_build_chain(type(request), handler, resp_type)
            result = chain(request)
            if inspect.isawaitable(result):
                raise TypeError(
                    "The pipeline produced an awaitable result; use send_async().")
            return result
        except BaseException as e:
            _flow_end(span, e)
            span = None
            raise
        finally:
            _flow_end(span)

    async def _send_async_core(self, request: IRequest) -> Any:
        """
        Async handler + async pipeline desteği. Handler sync de olabilir async de.
        Behavior/pre/post/exception handler'lar coroutine olabilir.
        """
        if isinstance(request, IStreamRequest):
            raise TypeError(
                f"{type(request).__name__} is an IStreamRequest; use "
                f"create_stream().")

        pair = self._handlers.get(type(request))
        if pair is None:
            raise ValueError(f"❌ Handler not found for: {type(request).__name__}")

        handler, resp_type = pair
        span = _flow_begin("send", type(request).__name__, "async")
        try:
            # FAST PATH
            if not self._has_pipeline():
                if isinstance(handler, _DeferredHandler) or self._handler_factory is not None:
                    handler = self._materialize(handler)
                inner = _flow_begin("handler", type(handler).__name__,
                                    "no pipeline - fast path")
                try:
                    result = await _maybe_await(_invoke_handle(handler, request))
                finally:
                    _flow_end(inner)
                return coerce_to_model(result, resp_type) if resp_type else result

            # SLOW PATH — derlenmiş async chain
            chain = self._get_or_build_async_chain(type(request), handler, resp_type)
            return await chain(request)
        except BaseException as e:
            _flow_end(span, e)
            span = None
            raise
        finally:
            _flow_end(span)

    # ---------------------------------------------------------- create_stream

    def _create_stream_core(self, request: IStreamRequest) -> AsyncIterator[Any]:
        """
        Streaming request'i çalıştırır ve bir async iterator döndürür.
        Stream pipeline behavior'lar uygulanır.

        Kullanım:
            async for item in mediator.create_stream(MyStream(...)):
                ...
        """
        if not isinstance(request, IStreamRequest):
            raise TypeError(
                f"{type(request).__name__} is not an IStreamRequest; use send().")

        handler = self._stream_handlers.get(type(request))
        if handler is None:
            available = [r.__name__ for r in self._stream_handlers.keys()]
            raise ValueError(
                f"❌ Stream handler not found for: {type(request).__name__}\n"
                f"   Registered stream handlers: {available or 'None'}"
            )

        chain = self._get_or_build_stream_chain(type(request), handler)
        trace = _FLOW.get()
        if trace is None:
            return chain(request)
        return self._traced_stream(trace, request, handler, chain)

    def _traced_stream(self, trace, request, handler, chain):
        # Stream behaviors wrap async generators, so the ContextVar cursor used
        # everywhere else cannot survive a yield. The chain shape is static, so
        # the nodes are built directly instead.
        root = FlowNode("stream", type(request).__name__)
        trace._attach(root)
        parent = root
        for beh in self._stream_behaviors:
            if not self._applies(beh, type(request)):
                continue
            node = FlowNode("behavior", type(beh).__name__)
            parent.children.append(node)
            parent = node
        name = (handler.cls.__name__ if isinstance(handler, _DeferredHandler)
                else type(handler).__name__)
        leaf = FlowNode("handler", name)
        parent.children.append(leaf)
        opened = [root, *self._spine(root)]

        async def run():
            count = 0
            try:
                async for item in chain(request):
                    count += 1
                    yield item
            except Exception as e:
                for n in reversed(opened):
                    n.close(e)
                raise
            leaf.note(f"{count} item(s) yielded")
            for n in reversed(opened):
                n.close()

        return run()

    @staticmethod
    def _spine(node: "FlowNode") -> List["FlowNode"]:
        out = []
        while node.children:
            node = node.children[0]
            out.append(node)
        return out

    # ---------------------------------------------------------------- publish

    def _handlers_for_notification(self, notification: INotification) -> List[Any]:
        """
        v6: notification için handler listesi.
        polymorphic_publish=True ise MRO üzerinde yürünür — türetilmiş event
        publish edildiğinde base tip handler'ları da tetiklenir (.NET covariance).
        Default (False) davranış v5 ile birebir aynıdır (exact-type lookup).
        """
        ntype = type(notification)
        if not self._polymorphic_publish:
            # nogil: dict'teki paylaşılan listenin SNAPSHOT kopyası döndürülür —
            # eşzamanlı register_notification_handler iterate'i etkileyemez.
            return list(self._notification_handlers.get(ntype, []))

        merged: List[Any] = []
        seen: set = set()
        for base in ntype.__mro__:
            if not (inspect.isclass(base) and issubclass(base, INotification)):
                continue
            for h in self._notification_handlers.get(base, []):
                if id(h) not in seen:
                    seen.add(id(h))
                    merged.append(h)
        merged.sort(key=lambda h: getattr(h, "order", 0))
        return merged

    def _publish_core(self, notification: INotification) -> None:
        """
        Notification'ı kayıtlı tüm handler'lara SYNC ve SIRALI (order) dağıt.
        swallow_notification_errors=False ise ilk exception fırlatılır.

        v6 düzeltmesi: bir handler async ise bu artık swallow mekanizmasına
        TAKILMADAN her zaman TypeError fırlatır — v5'te default ayarlarda bu
        hata sessizce yutuluyordu ve handler HİÇ çalışmadan kayboluyordu.
        """
        handlers = self._handlers_for_notification(notification)
        if not handlers:
            _debug_log(f"ℹ️  No subscribers for {type(notification).__name__}")
            return

        errors = []
        root = _flow_begin("publish", type(notification).__name__,
                           f"{len(handlers)} subscriber(s)")
        try:
            for h in handlers:
                h = self._materialize(h)  # v6: deferred/transient çözümü
                sp = _flow_begin("notification", type(h).__name__)
                try:
                    result = _invoke_handle(h, notification)
                except Exception as e:
                    _flow_end(sp, e)
                    sp = None
                    errors.append((h, e))
                    if not self._swallow_notification_errors:
                        logging.getLogger("mediatr.notifications").exception(
                            f"Handler failed for {type(notification).__name__}")
                        raise
                    continue
                finally:
                    _flow_end(sp)

                if inspect.iscoroutine(result):
                    result.close()  # RuntimeWarning'i önle
                    raise TypeError(
                        f"{type(h).__name__}.handle is async; use publish_async().")
        finally:
            _flow_end(root)

        if errors:
            logging.getLogger("mediatr.notifications").error(
                f"{len(errors)} handler(s) failed for {type(notification).__name__}: "
                + ", ".join(f"{h.__class__.__name__}={type(e).__name__}" for h, e in errors)
            )

    async def _publish_async_core(
        self,
        notification: INotification,
        strategy: Optional[PublishStrategy] = None,
        publisher: Optional[Callable[[List[Any], INotification], Awaitable[None]]] = None,
    ) -> None:
        """
        Async publish. strategy ile dağıtım davranışı seçilir:
          SEQUENTIAL        → order sırasıyla, her biri await edilir (default)
          PARALLEL_WHENALL  → asyncio.gather ile paralel; hepsi beklenir
          PARALLEL_NOWAIT   → fire-and-forget; beklenmez (task'ler oluşturulur)

        v6: publisher — özel dağıtım stratejisi (.NET INotificationPublisher):
          async def my_pub(handlers, notification) -> None
        Verilirse (veya ctor'da custom_publisher varsa) strategy yerine o çalışır.
        """
        handlers = self._handlers_for_notification(notification)
        if not handlers:
            return
        handlers = [self._materialize(h) for h in handlers]  # v6: DI çözümü

        pub = publisher or self._custom_publisher
        strategy = strategy or self._default_publish_strategy
        root = _flow_begin(
            "publish", type(notification).__name__,
            f"{len(handlers)} subscriber(s), "
            + ("custom publisher" if pub is not None
               else getattr(strategy, "name", str(strategy))))
        try:
            if pub is not None:
                await pub(handlers, notification)
                return

            if strategy is PublishStrategy.SEQUENTIAL:
                await self._publish_sequential(notification, handlers)
            elif strategy is PublishStrategy.PARALLEL_WHENALL:
                await self._publish_whenall(notification, handlers)
            elif strategy is PublishStrategy.PARALLEL_NOWAIT:
                self._publish_nowait(notification, handlers)
            else:
                await self._publish_sequential(notification, handlers)
        finally:
            _flow_end(root)

    # ------------------------------------------------- v6.4 PUBLIC API (+ CT)
    # v6.3'teki monkey-patch blokları kaldırıldı; cancellation_token destekli
    # public metodlar artık sınıf gövdesinde. (.NET Send(request, ct) muadili)

    def send(self, request: "IRequest[TResponse]", *,
             cancellation_token: Optional["CancellationToken"] = None) -> TResponse:
        if cancellation_token is None:
            return self._send_core(request)
        cancellation_token.throw_if_cancellation_requested()
        tok = _CURRENT_CT.set(cancellation_token)
        try:
            return self._send_core(request)
        finally:
            _CURRENT_CT.reset(tok)

    async def send_async(self, request: "IRequest[TResponse]", *,
                         cancellation_token: Optional["CancellationToken"] = None
                         ) -> TResponse:
        if cancellation_token is None:
            return await self._send_async_core(request)
        cancellation_token.throw_if_cancellation_requested()
        tok = _CURRENT_CT.set(cancellation_token)
        try:
            return await self._send_async_core(request)
        finally:
            _CURRENT_CT.reset(tok)

    def create_stream(self, request: "IStreamRequest[TResponse]", *,
                      cancellation_token: Optional["CancellationToken"] = None
                      ) -> AsyncIterator[TResponse]:
        if cancellation_token is None:
            return self._create_stream_core(request)
        cancellation_token.throw_if_cancellation_requested()

        async def _cancellable_stream():
            tok = _CURRENT_CT.set(cancellation_token)
            try:
                async for item in self._create_stream_core(request):
                    # .NET IAsyncEnumerable + ct semantiği: her item arası kontrol
                    cancellation_token.throw_if_cancellation_requested()
                    yield item
            finally:
                _CURRENT_CT.reset(tok)
        return _cancellable_stream()

    def publish(self, notification: INotification, *,
                cancellation_token: Optional["CancellationToken"] = None) -> None:
        if cancellation_token is None:
            return self._publish_core(notification)
        cancellation_token.throw_if_cancellation_requested()
        tok = _CURRENT_CT.set(cancellation_token)
        try:
            return self._publish_core(notification)
        finally:
            _CURRENT_CT.reset(tok)

    async def publish_async(
        self,
        notification: INotification,
        strategy: Optional[PublishStrategy] = None,
        publisher: Optional[Callable[[List[Any], INotification], Awaitable[None]]] = None,
        *,
        cancellation_token: Optional["CancellationToken"] = None,
    ) -> None:
        if cancellation_token is None:
            return await self._publish_async_core(notification, strategy, publisher)
        cancellation_token.throw_if_cancellation_requested()
        tok = _CURRENT_CT.set(cancellation_token)
        try:
            return await self._publish_async_core(notification, strategy, publisher)
        finally:
            _CURRENT_CT.reset(tok)

    def create_scope(self, container: _ServiceContainerLike):
        """v6.2: `with mediator.create_scope(container) as m: m.send(...)`"""
        from .di import scoped_mediator  # deferred: di imports Mediator
        return scoped_mediator(self, container)  # type: ignore[arg-type]

    def trace(self):
        """
        v6.7: record what actually happens during a dispatch, as a call tree.

            with mediator.trace() as flow:
                mediator.send(CreateUser(name="Ayhan"))
            flow.print()

            send(CreateUser)
            ├─ behavior: LoggingBehavior
            │  └─ behavior: ValidationBehavior
            │     └─ HANDLER: CreateUserHandler
            └─ publish(UserCreated)
               └─ subscriber: WelcomeEmailHandler

        Works for send/send_async/publish/publish_async/create_stream and for
        any mediator, including scope-bound clones. Recording is off by default
        and costs one ContextVar lookup per step when inactive.
        """
        return trace_flow()

    async def _publish_sequential(self, notification, handlers):
        errors = []
        for h in handlers:
            try:
                sp = _flow_begin("notification", type(h).__name__)
                try:
                    await _maybe_await(_invoke_handle(h, notification))
                finally:
                    _flow_end(sp)
            except Exception as e:
                errors.append((h, e))
                if not self._swallow_notification_errors:
                    raise
        if errors:
            logging.getLogger("mediatr.notifications").error(
                f"{len(errors)} async handler(s) failed for {type(notification).__name__}")

    async def _publish_whenall(self, notification, handlers):
        async def _run(h):
            sp = _flow_begin("notification", type(h).__name__, "parallel")
            try:
                return await _maybe_await(_invoke_handle(h, notification))
            except BaseException as e:
                _flow_end(sp, e)
                sp = None
                raise
            finally:
                _flow_end(sp)
        results = await asyncio.gather(
            *[_run(h) for h in handlers], return_exceptions=True)
        errors = [(h, r) for h, r in zip(handlers, results, strict=True)
                  if isinstance(r, Exception)]
        if errors and not self._swallow_notification_errors:
            # İlk hatayı fırlat
            raise errors[0][1]
        if errors:
            logging.getLogger("mediatr.notifications").error(
                f"{len(errors)} parallel handler(s) failed for "
                f"{type(notification).__name__}")

    def _publish_nowait(self, notification, handlers):
        # v6: publish_async içinden çağrılır → çalışan loop garanti;
        # deprecated get_event_loop yerine get_running_loop.
        loop = asyncio.get_running_loop()
        for h in handlers:
            try:
                coro_or_val = _invoke_handle(h, notification)
                if inspect.isawaitable(coro_or_val):
                    # v6.7: asyncio only keeps a WEAK reference to running
                    # tasks; without a strong reference the GC can collect a
                    # fire-and-forget task mid-flight and the handler silently
                    # never completes. Keep it alive until it is done.
                    task = loop.create_task(
                        self._safe_await(coro_or_val, h, notification))
                    _BACKGROUND_TASKS.add(task)
                    task.add_done_callback(_BACKGROUND_TASKS.discard)
            except Exception as e:
                if not self._swallow_notification_errors:
                    raise
                logging.getLogger("mediatr.notifications").error(
                    f"Handler {type(h).__name__} failed (nowait): {e}")

    async def _safe_await(self, awaitable, handler, notification):
        sp = _flow_begin("notification", type(handler).__name__,
                         "fire-and-forget")
        try:
            await awaitable
        except Exception as e:
            _flow_end(sp, e)
            sp = None
            logging.getLogger("mediatr.notifications").error(
                f"Fire-and-forget handler {type(handler).__name__} failed for "
                f"{type(notification).__name__}: {e}")
        finally:
            _flow_end(sp)

    # ------------------------------------------------- exception dispatch util

    def _run_exception_actions(self, request, exc, applicable_actions):
        """Run exception actions on the SYNC path (never swallow silently)."""
        log = logging.getLogger("mediatr.exceptions")
        for action in applicable_actions:
            if isinstance(exc, action.exception_type):
                _flow_end(_flow_begin("exception-action", type(action).__name__,
                                      f"observing {type(exc).__name__}"))
                try:
                    res = action.execute(request, exc)
                    if inspect.isawaitable(res):
                        # v6.7: previously this was dropped with a debug-only
                        # note, so telemetry/alerting actions silently never
                        # ran. Resolve it through the bridge and, if that is
                        # impossible, say so at WARNING level.
                        try:
                            _sync_run_coro(res)
                        except Exception as bridge_exc:  # noqa: BLE001
                            log.warning(
                                "Async exception action %s could not run on the "
                                "synchronous send() path (%s: %s). Use "
                                "send_async() so async exception actions are "
                                "awaited properly.",
                                type(action).__name__,
                                type(bridge_exc).__name__, bridge_exc)
                except Exception as ae:  # noqa: BLE001
                    log.error("Exception action %s itself failed: %s: %s",
                              type(action).__name__, type(ae).__name__, ae,
                              exc_info=True)

    async def _run_exception_actions_async(self, request, exc, applicable_actions):
        log = logging.getLogger("mediatr.exceptions")
        for action in applicable_actions:
            if isinstance(exc, action.exception_type):
                _flow_end(_flow_begin("exception-action", type(action).__name__,
                                      f"observing {type(exc).__name__}"))
                try:
                    await _maybe_await(action.execute(request, exc))
                except Exception as ae:  # noqa: BLE001
                    log.error("Exception action %s itself failed: %s: %s",
                              type(action).__name__, type(ae).__name__, ae,
                              exc_info=True)

    # ------------------------------------------------------- SYNC chain build

    def _get_or_build_chain(self, req_type, handler, resp_type):
        """Sync pipeline zincirini derler ve cache'ler."""
        chain = self._compiled_chains.get(req_type)
        if chain is not None:
            return chain

        with self._chain_lock:
            chain = self._compiled_chains.get(req_type)
            if chain is not None:
                return chain

            applicable_behaviors = [b for b in self._behaviors if self._applies(b, req_type)]
            applicable_pre = [p for p in self._pre_processors if self._applies(p, req_type)]
            applicable_post = [p for p in self._post_processors if self._applies(p, req_type)]
            applicable_exc = sorted(
                [h for h in self._exception_handlers if self._applies(h, req_type)],
                key=lambda h: getattr(h, "order", 0))
            applicable_act = sorted(
                [a for a in self._exception_actions if self._applies(a, req_type)],
                key=lambda a: getattr(a, "order", 0))

            resolve = self._make_resolver(handler)  # v6: DI/lifetime çözücü

            def core(request):
                for pre_processor in applicable_pre:
                    sp = _flow_begin("pre", type(pre_processor).__name__)
                    try:
                        pre_processor.process(request)
                    finally:
                        _flow_end(sp)
                actual = resolve()
                sp = _flow_begin("handler", type(actual).__name__)
                try:
                    result = _invoke_handle(actual, request)
                except BaseException as e:
                    _flow_end(sp, e)
                    raise
                _flow_end(sp)
                coerced = coerce_to_model(result, resp_type) if resp_type else result
                for post_processor in applicable_post:
                    sp = _flow_begin("post", type(post_processor).__name__)
                    try:
                        post_processor.process(request, coerced)
                    finally:
                        _flow_end(sp)
                return coerced

            final = core
            for behavior in reversed(applicable_behaviors):
                def _make(beh, nxt):
                    beh_name = type(beh).__name__

                    def wrapped(req):
                        sp = _flow_begin("behavior", beh_name)
                        try:
                            return beh.handle(req, lambda: nxt(req))
                        except BaseException as e:
                            _flow_end(sp, e)
                            sp = None
                            raise
                        finally:
                            _flow_end(sp)
                    return wrapped
                final = _make(behavior, final)

            if applicable_exc or applicable_act:
                core_chain = final

                def with_exc(request):
                    try:
                        return core_chain(request)
                    except BaseException as e:
                        # 1) Action'lar: gözlemle (yutma)
                        self._run_exception_actions(request, e, applicable_act)
                        # 2) Handler'lar: v6.4 — açık handled state; işlenmezse re-raise
                        for eh in applicable_exc:
                            if isinstance(e, eh.exception_type):
                                sp = _flow_begin("exception-handler",
                                                 type(eh).__name__,
                                                 f"caught {type(e).__name__}")
                                try:
                                    handled, response, aw = _invoke_exception_handler(
                                        eh, request, e)
                                    if aw is not None:
                                        handled, response = _sync_run_coro(aw)
                                    _flow_note("handled -> fallback response"
                                               if handled else "not handled")
                                finally:
                                    _flow_end(sp)
                                if handled:
                                    return response
                        raise
                final = with_exc

            self._compiled_chains[req_type] = final
            return final

    # ------------------------------------------------------ ASYNC chain build

    def _get_or_build_async_chain(self, req_type, handler, resp_type):
        """Async pipeline zincirini derler ve cache'ler (gerçek async-aware)."""
        chain = self._compiled_async_chains.get(req_type)
        if chain is not None:
            return chain

        with self._chain_lock:
            chain = self._compiled_async_chains.get(req_type)
            if chain is not None:
                return chain

            applicable_behaviors = [b for b in self._behaviors if self._applies(b, req_type)]
            applicable_pre = [p for p in self._pre_processors if self._applies(p, req_type)]
            applicable_post = [p for p in self._post_processors if self._applies(p, req_type)]
            applicable_exc = sorted(
                [h for h in self._exception_handlers if self._applies(h, req_type)],
                key=lambda h: getattr(h, "order", 0))
            applicable_act = sorted(
                [a for a in self._exception_actions if self._applies(a, req_type)],
                key=lambda a: getattr(a, "order", 0))

            resolve = self._make_resolver(handler)  # v6: DI/lifetime çözücü

            async def core(request):
                for pre_processor in applicable_pre:
                    sp = _flow_begin("pre", type(pre_processor).__name__)
                    try:
                        await _maybe_await(pre_processor.process(request))
                    finally:
                        _flow_end(sp)
                actual = resolve()
                sp = _flow_begin("handler", type(actual).__name__)
                try:
                    result = await _maybe_await(_invoke_handle(actual, request))
                except BaseException as e:
                    _flow_end(sp, e)
                    raise
                _flow_end(sp)
                coerced = coerce_to_model(result, resp_type) if resp_type else result
                for post_processor in applicable_post:
                    sp = _flow_begin("post", type(post_processor).__name__)
                    try:
                        await _maybe_await(post_processor.process(request, coerced))
                    finally:
                        _flow_end(sp)
                return coerced

            final = core
            for behavior in reversed(applicable_behaviors):
                beh_is_async = _is_async_callable(behavior.handle)

                def _make(beh, nxt, is_async):
                    beh_name = type(beh).__name__
                    if is_async:
                        # ASYNC behavior: handle() bir coroutine; next() coroutine
                        # döndürür ve behavior içinde `await next()` edilir.
                        async def wrapped_async(req):
                            def _next():
                                return nxt(req)  # coroutine döner
                            sp = _flow_begin("behavior", beh_name)
                            try:
                                return await beh.handle(req, _next)
                            except BaseException as e:
                                _flow_end(sp, e)
                                sp = None
                                raise
                            finally:
                                _flow_end(sp)
                        return wrapped_async
                    else:
                        # v6.4: SYNC behavior async zincirde AYNI event loop üzerinde
                        # çalışır. next() alt zincirin coroutine'ini döndürür; behavior
                        # bunu (awaitable-aware ise) olduğu gibi geri verir ve burada
                        # await edilir. Ayrı thread/event-loop köprüsü KALDIRILDI —
                        # "Future attached to a different loop" hatası artık oluşmaz.
                        # Not: sonucu senkron materialize etmek isteyen saf-sync custom
                        # behavior'lar async pipeline için awaitable-aware olmalıdır.
                        async def wrapped_sync(req):
                            def _next():
                                return nxt(req)  # coroutine döner
                            sp = _flow_begin("behavior", beh_name)
                            try:
                                result = beh.handle(req, _next)
                                if inspect.isawaitable(result):
                                    return await result
                                return result
                            except BaseException as e:
                                _flow_end(sp, e)
                                sp = None
                                raise
                            finally:
                                _flow_end(sp)
                        return wrapped_sync

                final = _make(behavior, final, beh_is_async)

            if applicable_exc or applicable_act:
                core_chain = final

                async def with_exc(request):
                    try:
                        return await core_chain(request)
                    except BaseException as e:
                        await self._run_exception_actions_async(request, e, applicable_act)
                        # v6.4 — açık handled state; işlenmezse re-raise
                        for eh in applicable_exc:
                            if isinstance(e, eh.exception_type):
                                sp = _flow_begin("exception-handler",
                                                 type(eh).__name__,
                                                 f"caught {type(e).__name__}")
                                try:
                                    handled, response, aw = _invoke_exception_handler(
                                        eh, request, e)
                                    if aw is not None:
                                        handled, response = await aw
                                    _flow_note("handled -> fallback response"
                                               if handled else "not handled")
                                finally:
                                    _flow_end(sp)
                                if handled:
                                    return response
                        raise
                final = with_exc

            self._compiled_async_chains[req_type] = final
            return final

    # ----------------------------------------------------- STREAM chain build

    def _get_or_build_stream_chain(self, req_type, handler):
        """Stream pipeline zincirini derler. Dönüş: req -> async iterator."""
        chain = self._compiled_stream_chains.get(req_type)
        if chain is not None:
            return chain

        with self._chain_lock:
            chain = self._compiled_stream_chains.get(req_type)
            if chain is not None:
                return chain

            applicable = [b for b in self._stream_behaviors if self._applies(b, req_type)]

            resolve = self._make_resolver(handler)  # v6: DI/lifetime çözücü

            def core(request):
                # Handler bir async generator döndürmeli (ya da senkron iterable).
                result = _invoke_handle(resolve(), request)
                return _as_async_iterator(result)

            final = core
            for behavior in reversed(applicable):
                def _make(beh, nxt):
                    def wrapped(req):
                        def _next():
                            return nxt(req)
                        return beh.handle(req, _next)
                    return wrapped
                final = _make(behavior, final)

            self._compiled_stream_chains[req_type] = final
            return final

    # -------------------------------------------------------- manual register

    def register_handler(self, request_type, handler, response_type=None):
        """
        Manuel request handler kaydı. İlgili chain cache'leri invalide edilir.

        v6: `handler` artık SINIF da olabilir — factory varsa deferred (DI)
        kaydedilir, yoksa parametresiz instantiate edilir.
        """
        if inspect.isclass(handler):
            handler = (_DeferredHandler(handler) if self._handler_factory
                       else handler())
        with self._chain_lock:  # nogil: eşzamanlı kayıt/derleme serileşir
            if _is_stream_request_type(request_type):
                self._stream_handlers[request_type] = handler
                self._compiled_stream_chains.pop(request_type, None)
                _debug_log(f"🌊 Manually registered stream: {request_type.__name__}")
                return
            self._handlers[request_type] = (handler, response_type)
            self._compiled_chains.pop(request_type, None)
            self._compiled_async_chains.pop(request_type, None)
        _debug_log(f"✅ Manually registered: {request_type.__name__} → {handler.__class__.__name__}")

    # ------------------------------------------------------- v6 test desteği

    def _invalidate_chains_locked(self, request_type) -> None:
        self._compiled_chains.pop(request_type, None)
        self._compiled_async_chains.pop(request_type, None)
        self._compiled_stream_chains.pop(request_type, None)

    @contextmanager
    def override_handler(self, request_type, handler, response_type=None):
        """
        Test için handler'ı GEÇİCİ değiştirir; blok bitince eski handler ve
        chain cache'leri geri yüklenir.

            with mediator.override_handler(GetUser, FakeGetUserHandler()):
                assert mediator.send(GetUser(id=1)).name == "test"

        v6.5: token/stack tabanlı sahiplik — iç içe veya LIFO-dışı kapanan
        çakışmalı override'lar registry'yi bozmaz; tüm mutasyonlar
        _chain_lock altında yapılır. Aktif override varken register_handler
        çağrılırsa son override kapanınca override öncesi duruma dönülür.
        """
        if inspect.isclass(handler):
            handler = (_DeferredHandler(handler) if self._handler_factory
                       else handler())
        is_stream = _is_stream_request_type(request_type)
        token = object()  # bu context'in sahiplik kimliği
        with self._chain_lock:
            registry = self._stream_handlers if is_stream else self._handlers
            stack = self._override_stacks.get(request_type)
            if stack is None:
                stack = self._override_stacks[request_type] = [
                    ("__base__", request_type in registry,
                     registry.get(request_type))]
            entry = handler if is_stream else (handler, response_type)
            stack.append((token, entry))
            registry[request_type] = entry
            self._invalidate_chains_locked(request_type)
        try:
            yield self
        finally:
            with self._chain_lock:
                registry = self._stream_handlers if is_stream else self._handlers
                stack = self._override_stacks.get(request_type)
                if stack is not None:
                    for i in range(len(stack) - 1, 0, -1):
                        if stack[i][0] is token:
                            del stack[i]
                            break
                    if len(stack) == 1:  # sadece base kaldı → orijinali geri yükle
                        _, had_old, old_entry = stack[0]
                        if had_old:
                            registry[request_type] = old_entry
                        else:
                            registry.pop(request_type, None)
                        del self._override_stacks[request_type]
                    else:  # kalan en üst override etkin olur
                        registry[request_type] = stack[-1][1]
                self._invalidate_chains_locked(request_type)

    def reset(self) -> None:
        """
        Tüm kayıtları ve derlenmiş chain'leri temizler (test izolasyonu için).
        Behavior/processor listeleri de sıfırlanır.
        """
        with self._chain_lock:  # nogil: rebind → eşzamanlı okuyucular eski snapshot'ı görür
            self._handlers = {}
            self._stream_handlers = {}
            self._notification_handlers = {}
            self._behaviors = []
            self._stream_behaviors = []
            self._pre_processors = []
            self._post_processors = []
            self._exception_handlers = []
            self._exception_actions = []
            self._compiled_chains = {}
            self._compiled_async_chains = {}
            self._compiled_stream_chains = {}
            self._singleton_cache = {}
            self._override_stacks = {}
        _debug_log("🧹 Mediator reset")

    def register_stream_handler(self, request_type, handler):
        """Manuel stream handler kaydı."""
        with self._chain_lock:  # nogil: eşzamanlı kayıt/derleme serileşir
            self._stream_handlers[request_type] = handler
            self._compiled_stream_chains.pop(request_type, None)
        _debug_log(f"🌊 Manually registered stream: {request_type.__name__}")

    def register_notification_handler(self, notification_type, handler):
        """Manuel notification handler kaydı (order'a göre yeniden sıralanır).
        v6: `handler` sınıf da olabilir (bkz. register_handler).

        nogil notu: liste in-place sort edilmez; sıralanmış YENİ liste dict'e
        atomik rebind edilir (copy-on-write) — eşzamanlı publish, eski listenin
        tutarlı snapshot'ını görmeye devam eder.
        """
        if inspect.isclass(handler):
            handler = (_DeferredHandler(handler) if self._handler_factory
                       else handler())
        with self._chain_lock:
            old = self._notification_handlers.get(notification_type, [])
            self._notification_handlers[notification_type] = sorted(
                old + [handler], key=lambda h: getattr(h, "order", 0))
        _debug_log(f"🔔 Manually registered notif: "
                   f"{notification_type.__name__} → {handler.__class__.__name__}")

    # nogil notu (tüm add_* metodları): paylaşılan liste in-place mutate
    # edilmez; sıralanmış YENİ liste attribute'a atomik rebind edilir.
    # Böylece o an chain derleyen/iterate eden başka bir thread eski listenin
    # tutarlı snapshot'ını görür. Lock, yazarlar arası yarışları serileştirir.

    def add_behavior(self, behavior):
        with self._chain_lock:
            self._behaviors = sorted(
                self._behaviors + [behavior], key=lambda b: getattr(b, "order", 0))
            self._compiled_chains.clear()
            self._compiled_async_chains.clear()

    def add_stream_behavior(self, behavior):
        with self._chain_lock:
            self._stream_behaviors = sorted(
                self._stream_behaviors + [behavior], key=lambda b: getattr(b, "order", 0))
            self._compiled_stream_chains.clear()

    def add_pre_processor(self, processor):
        with self._chain_lock:
            self._pre_processors = sorted(
                self._pre_processors + [processor], key=lambda p: getattr(p, "order", 0))
            self._compiled_chains.clear()
            self._compiled_async_chains.clear()

    def add_post_processor(self, processor):
        with self._chain_lock:
            self._post_processors = sorted(
                self._post_processors + [processor], key=lambda p: getattr(p, "order", 0))
            self._compiled_chains.clear()
            self._compiled_async_chains.clear()

    def add_exception_handler(self, handler):
        with self._chain_lock:
            self._exception_handlers = sorted(
                self._exception_handlers + [handler], key=lambda h: getattr(h, "order", 0))
            self._compiled_chains.clear()
            self._compiled_async_chains.clear()

    def add_exception_action(self, action):
        with self._chain_lock:
            self._exception_actions = sorted(
                self._exception_actions + [action], key=lambda a: getattr(a, "order", 0))
            self._compiled_chains.clear()
            self._compiled_async_chains.clear()

    # ------------------------------------------------------------ inspection

    def get_registered_handlers(self):
        with self._chain_lock:  # v6.4 nogil: snapshot altında iterate
            items = list(self._handlers.items())
        return {
            req_type.__name__: handler.__class__.__name__
            for req_type, (handler, _) in items
        }

    def get_registered_stream_handlers(self):
        with self._chain_lock:  # v6.4 nogil: snapshot altında iterate
            items = list(self._stream_handlers.items())
        return {
            req_type.__name__: handler.__class__.__name__
            for req_type, handler in items
        }

    def get_registered_notification_handlers(self):
        with self._chain_lock:  # v6.4 nogil: snapshot altında iterate
            items = [(nt, list(hs))
                     for nt, hs in self._notification_handlers.items()]
        return {
            notif_type.__name__: [h.__class__.__name__ for h in handlers]
            for notif_type, handlers in items
        }

    def get_pipeline_info(self):
        with self._chain_lock:  # v6.4 nogil: tutarlı snapshot
            return self._pipeline_info_locked()

    def _pipeline_info_locked(self):
        return {
            "behaviors": [
                {"name": b.__class__.__name__,
                 "order": getattr(b, "order", 0),
                 "applies_to": getattr(getattr(b, "applies_to", None), "__name__", "ALL")}
                for b in self._behaviors
            ],
            "stream_behaviors": [b.__class__.__name__ for b in self._stream_behaviors],
            "pre_processors": [p.__class__.__name__ for p in self._pre_processors],
            "post_processors": [p.__class__.__name__ for p in self._post_processors],
            "exception_handlers": [
                {"name": h.__class__.__name__,
                 "exception_type": h.exception_type.__name__}
                for h in self._exception_handlers
            ],
            "exception_actions": [
                {"name": a.__class__.__name__,
                 "exception_type": a.exception_type.__name__}
                for a in self._exception_actions
            ],
            "compiled_chains": len(self._compiled_chains),
            "compiled_async_chains": len(self._compiled_async_chains),
            "compiled_stream_chains": len(self._compiled_stream_chains),
            # v6
            "handler_lifetime": self._handler_lifetime,
            "handler_factory": bool(self._handler_factory),
            "polymorphic_publish": self._polymorphic_publish,
        }


# ============================================================================
# STREAM HELPER
# ============================================================================

def _as_async_iterator(obj: Any) -> AsyncIterator[Any]:
    """
    Handler dönüşünü async iterator'a normalize eder:
      • async generator / async iterator → aynen
      • sync generator / iterable        → async sarmalayıcı
      • awaitable (iterable döndüren)     → await edip normalize et
    """
    # Async iterator?
    if hasattr(obj, "__anext__"):
        return obj
    if hasattr(obj, "__aiter__"):
        return obj.__aiter__()

    # Awaitable (örn. async fonksiyon iterable döndürdü)
    if inspect.isawaitable(obj):
        async def _from_awaitable():
            resolved = await obj
            async for item in _as_async_iterator(resolved):
                yield item
        return _from_awaitable()

    # Sync iterable
    if hasattr(obj, "__iter__"):
        async def _from_sync():
            for item in obj:
                yield item
        return _from_sync()

    raise TypeError(
        f"Stream handler returned a non-iterable / non-async-iterable: "
        f"{type(obj).__name__}")
