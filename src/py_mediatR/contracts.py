"""py_mediatR.contracts — public base types, marker interfaces, decorators.

v6.7.0'da tek dosyalik `py_mediatR.py` alt modullere ayrildi.
Kod govdesi birebir aynidir. Genel API icin `import py_mediatR` kullanin;
bu modul bir uygulama detayidir ve dogrudan import edilmesi gerekmez.
"""

import inspect
from enum import Enum
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Generic,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
)

# ============================================================================
# BASE TYPES (v3/v4 uyumlu + YENİ stream/abstraction'lar)
# ============================================================================

# v6: Generic response tipi — .NET IRequest<TResponse> muadili.
# `class GetUser(IRequest["UserDto"])` yazılırsa mypy/pyright send() dönüşünü
# UserDto olarak çıkarır. Eski `class X(IRequest)` kullanımı AYNEN çalışır.
TResponse = TypeVar("TResponse")


class IRequest(Generic[TResponse]):
    """Tüm Request (Command/Query) sınıflarının base'i — DEĞİŞMEDİ.
    .NET: IRequest / IRequest<TResponse>

    v6: Generic[TResponse] eklendi (yalnızca statik tip çıkarımı için;
    runtime davranışı değişmedi, parametresiz kalıtım geçerlidir).
    """
    pass


class IResponse:
    """Tüm Response sınıflarının base'i — DEĞİŞMEDİ."""
    pass


class IStreamRequest(IRequest[TResponse], Generic[TResponse]):
    """
    Streaming request base'i. Handler bir (async) generator döndürür.
    v6.4: Generic[TResponse] — create_stream() dönüşü AsyncIterator[TResponse]
    olarak çıkarılır.
    .NET MediatR: IStreamRequest<TResponse> muadili.

    Örnek:
        class CountUp(IStreamRequest):
            n: int

        class CountUpHandler:
            async def handle(self, req: CountUp):
                for i in range(req.n):
                    yield i

        async for item in mediator.create_stream(CountUp(n=3)):
            print(item)
    """
    pass


class INotification:
    """
    Pub/Sub event base class'ı. (.NET: INotification)

    IRequest ile farkı:
      • IRequest → 1 handler (komut/sorgu)
      • INotification → N handler (olay bildirimi)

    Örnek:
        class OrderPlaced(INotification):
            order_id: str

        class SendEmail(INotificationHandler):
            def handle(self, n: OrderPlaced): ...

        class UpdateStock(INotificationHandler):
            def handle(self, n: OrderPlaced): ...

        mediator.publish(OrderPlaced(order_id="..."))  # Her iki handler da çalışır
    """
    pass


class INotificationHandler:
    """
    Notification handler base'i. handle(notification) uygulamalı.
    (.NET: INotificationHandler<TNotification>)

    Sınıf nitelikleri:
        order → birden fazla handler arasında çalışma sırası (küçük önce)
    """
    order: int = 0

    def handle(self, notification: INotification) -> None:
        raise NotImplementedError


# ----- Cross-cutting abstractions -----

class IPipelineBehavior:
    """
    .NET MediatR IPipelineBehavior<TRequest, TResponse> muadili.
    Handler çevresinde sarmalanır. Hem ÖNCE hem SONRA iş yapabilir.

    handle(request, next) imzası:
        - next() çağrılırsa zincirin bir sonraki halkası çalışır ve response döner.
        - next() çağrılmazsa handler HİÇ çalışmaz (short-circuit).
        - Async pipeline'da handle() coroutine olabilir; next() awaitable döner.

    Sınıf nitelikleri:
        order        → sıralama (küçük olan DIŞTA, önce başlar/en son biter)
        applies_to   → sadece belirli request tipine uygulansın istenirse (None=hepsi)

    Örnek:
        class LoggingBehavior(IPipelineBehavior):
            order = -100
            def handle(self, request, next):
                print(f"→ {type(request).__name__}")
                resp = next()
                print(f"← {type(request).__name__}")
                return resp
    """
    order: int = 0
    applies_to: Optional[Type[IRequest]] = None

    def handle(self, request: IRequest, next_handler: "RequestHandlerDelegate") -> Any:
        raise NotImplementedError


class IStreamPipelineBehavior:
    """
    .NET MediatR IStreamPipelineBehavior<TRequest, TResponse> muadili.
    Stream handler çevresinde sarmalanır.

    handle(request, next) -> async generator:
        next() bir async iterator döndürür; behavior her item'ı sarmalayabilir.

    Örnek:
        class StreamLogging(IStreamPipelineBehavior):
            async def handle(self, request, next):
                async for item in next():
                    yield item
    """
    order: int = 0
    applies_to: Optional[Type[IStreamRequest]] = None

    def handle(self, request: IStreamRequest,
               next_handler: "StreamHandlerDelegate") -> AsyncIterator[Any]:
        raise NotImplementedError


class IRequestPreProcessor:
    """
    Handler'dan HEMEN ÖNCE çalışır. Yalnızca side-effect (log, validate, enrich) içindir.
    Response'u etkileyemez; next() kavramı yoktur.
    (.NET: IRequestPreProcessor<TRequest>)
    process() sync veya async (coroutine) olabilir.

    Örnek:
        class AuditPreProcessor(IRequestPreProcessor):
            def process(self, request):
                audit_log.write(f"Request: {type(request).__name__}")
    """
    order: int = 0
    applies_to: Optional[Type[IRequest]] = None

    def process(self, request: IRequest) -> Awaitable[None] | None:
        raise NotImplementedError


class IRequestPostProcessor:
    """
    Handler'dan HEMEN SONRA çalışır. Response'u GÖRÜR, ama değiştiremez.
    Log, metric, notification dispatch gibi side-effect'ler için idealdir.
    (.NET: IRequestPostProcessor<TRequest, TResponse>)
    process() sync veya async (coroutine) olabilir.

    Örnek:
        class MetricsPostProcessor(IRequestPostProcessor):
            def process(self, request, response):
                metrics.increment(f"handled.{type(request).__name__}")
    """
    order: int = 0
    applies_to: Optional[Type[IRequest]] = None

    def process(self, request: IRequest, response: Any) -> Awaitable[None] | None:
        raise NotImplementedError


class IExceptionHandler:
    """
    Handler'dan (veya behavior zincirinden) fırlatılan exception'ları yakalar.
    (.NET: IRequestExceptionHandler<TRequest, TResponse, TException>)

    handle(request, exception) dönüşü:
      • Bir değer return edilirse → o değer response olur (exception yutulur).
      • raise edilirse → yeni/aynı exception yukarı propagate olur.
      • Async pipeline'da handle() coroutine olabilir.

    Sınıf nitelikleri:
        exception_type → sadece bu tipte/türevde exception için
        applies_to     → sadece bu request tipi için
        order          → birden fazla handler varsa eşleşen ilki kullanılır

    Örnek:
        class NotFoundHandler(IExceptionHandler):
            exception_type = KeyError
            def handle(self, request, exc):
                return NotFoundResponse()
    """
    order: int = 0
    applies_to: Optional[Type[IRequest]] = None
    exception_type: Type[BaseException] = Exception

    def handle(self, request: IRequest, exception: BaseException) -> Any:
        raise exception


class IExceptionAction:
    """
    .NET MediatR IRequestExceptionAction<TRequest, TException> muadili.
    Exception'ı YUTMADAN gözlemler (log/metric/alert). handle() çalıştıktan sonra
    exception yine de propagate olmaya devam eder (bir IExceptionHandler onu
    yutmadıkça). Birden çok action sırayla çalışır.

    Sınıf nitelikleri:
        exception_type / applies_to / order → IExceptionHandler ile aynı semantik.

    Örnek:
        class AlertAction(IExceptionAction):
            exception_type = Exception
            def execute(self, request, exc):
                alerting.notify(f"{type(request).__name__} failed: {exc}")
    """
    order: int = 0
    applies_to: Optional[Type[IRequest]] = None
    exception_type: Type[BaseException] = Exception

    def execute(self, request: IRequest, exception: BaseException) -> None:
        raise NotImplementedError


class IValidator:
    """
    v6: FluentValidation muadili — request başına ayrı validator sınıfı.
    ValidationBehavior(validators=[...]) ile pipeline'a bağlanır.

    Sınıf nitelikleri:
        applies_to → tek tip veya tuple; None = tüm request'ler
        order      → çalışma sırası (küçük önce)

    Örnek:
        class CreateUserValidator(IValidator):
            applies_to = CreateUserCommand
            def validate(self, request):
                if not request.email:
                    raise ValueError("email zorunlu")
    """
    order: int = 0
    applies_to: Optional[Any] = None  # Type veya Tuple[Type, ...]

    def validate(self, request: IRequest) -> None:
        raise NotImplementedError


class UnauthorizedError(PermissionError):
    """v6: AuthorizationBehavior yetki reddettiğinde fırlatılır."""
    pass


class _DeferredHandler:
    """
    v6 iç tip: ctor'u parametreli (DI bekleyen) handler sınıfı sarmalayıcısı.
    Discovery anında instantiate EDİLEMEZ; Mediator handler_factory verilmişse
    send/publish anında factory üzerinden çözülür. Factory yoksa Mediator
    init'te v5 davranışına dönülerek sessizce (debug log ile) elenir.
    """
    __slots__ = ("cls",)

    def __init__(self, cls: type) -> None:
        self.cls = cls

    def __repr__(self) -> str:  # debug kolaylığı
        return f"<Deferred {self.cls.__name__}>"


# ----- v6: Explicit (decorator) kayıt — auto-discovery'ye alternatif -----
# Auto-discovery import side-effect'lerine duyarlıdır; büyük projelerde
# aşağıdaki decorator'larla açık kayıt tercih edilebilir. İki mekanizma
# birlikte çalışır; explicit kayıt discovery'yi ezmez, tamamlar.

_EXPLICIT_HANDLERS: List[Tuple[type, Optional[type], Optional[type]]] = []
_EXPLICIT_BEHAVIORS: List[type] = []


def handler(cls=None, *, request_type: Optional[type] = None,
            response_type: Optional[type] = None):
    """
    Handler sınıfını explicit kaydeder.

    Kullanım:
        @handler
        class GetUserHandler:
            def handle(self, req: GetUser) -> UserDto: ...

        @handler(request_type=GetUser, response_type=UserDto)  # tip ipucu yoksa
        class GetUserHandler: ...
    """
    def _wrap(c: type) -> type:
        _EXPLICIT_HANDLERS.append((c, request_type, response_type))
        return c
    if cls is not None and inspect.isclass(cls):
        return _wrap(cls)
    return _wrap


def behavior(cls: type) -> type:
    """
    IPipelineBehavior sınıfını explicit kaydeder. Mediator(discover_behaviors=True)
    ile parametresiz (veya factory üzerinden) instantiate edilip pipeline'a eklenir.

        @behavior
        class AuditBehavior(IPipelineBehavior): ...
    """
    _EXPLICIT_BEHAVIORS.append(cls)
    return cls


# ----- Delegate type aliases (.NET RequestHandlerDelegate parity) -----
# Sync zincirde next_handler() çağrısı response döndürür.
# Async zincirde next_handler() awaitable döndürebilir.
RequestHandlerDelegate = Callable[[], Any]
# Stream zincirinde next_handler() bir async iterator döndürür.
StreamHandlerDelegate = Callable[[], AsyncIterator[Any]]


# ----- Notification publish strategies (.NET parity) -----

class PublishStrategy(Enum):
    """
    Notification dağıtım stratejisi (.NET MediatR custom publisher muadili).

      SEQUENTIAL ........... Handler'lar sırayla (order'a göre) çalışır. (default)
      PARALLEL_WHENALL ..... Tüm async handler'lar aynı anda başlatılır, hepsi
                             beklenir (asyncio.gather). Yalnızca *_async path'te.
      PARALLEL_NOWAIT ...... Handler'lar fire-and-forget başlatılır (beklenmez).
                             Yalnızca *_async path'te.
    """
    SEQUENTIAL = "sequential"
    PARALLEL_WHENALL = "parallel_whenall"
    PARALLEL_NOWAIT = "parallel_nowait"
