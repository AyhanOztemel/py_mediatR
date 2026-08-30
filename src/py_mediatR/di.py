"""py_mediatR.di — ServiceContainer, scopes, auto-wiring.

v6.7.0'da tek dosyalik `py_mediatR.py` alt modullere ayrildi.
Kod govdesi birebir aynidir. Genel API icin `import py_mediatR` kullanin;
bu modul bir uygulama detayidir ve dogrudan import edilmesi gerekmez.
"""

import inspect
from contextlib import contextmanager
from threading import Lock, RLock
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Tuple,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from ._config import _debug_log
from .mediator import Mediator

# ============================================================================
# v6.2: TAM DI KATMANI — ServiceContainer / Scoped Lifetime / Auto-Wiring
# ============================================================================
# .NET Microsoft.Extensions.DependencyInjection paritesi:
#   • singleton / scoped / transient yaşam döngüleri (scoped YENİ)
#   • Type-hint tabanlı constructor auto-wiring (bağımlılık grafiği otomatik)
#   • ServiceScope: `using var scope = provider.CreateScope()` muadili —
#     sync/async context manager + IDisposable/IAsyncDisposable dispose
#   • scoped_mediator / ServiceScope.mediator: istek başına scope'a bağlı
#     Mediator (FastAPI request-scope senaryosu)
#   • ServiceContainer, v6.1 köprüsüyle geriye dönük uyumlu:
#     `.resolve` + `.registrations` + `.register_transient` duck-type imzasını
#     sağladığı için doğrudan `Mediator(handler_factory=container)` çalışır.
# Tamamı ADDITIF — mevcut v6.1 API'sine hiçbir davranış değişikliği yok.


class DIResolutionError(TypeError):
    """v6.2: raised when the DI container cannot resolve a service."""
    pass


_NON_INJECTABLE = (int, float, str, bool, bytes, bytearray,
                   list, dict, set, tuple, frozenset)


def _is_injectable_type(tp: Any) -> bool:
    """Auto-wiring'in çözmeyi deneyeceği tip mi? (builtin/primitive değil)"""
    return (inspect.isclass(tp)
            and not issubclass(tp, _NON_INJECTABLE)
            and tp.__module__ != "builtins")


def _unwrap_hint(tp: Any) -> Tuple[Any, bool]:
    """
    v6.7: reduce a type hint to the class the container should resolve.

    Returns ``(concrete_type_or_None, optional)``. Before v6.7 auto-wiring
    required ``inspect.isclass(tp)``, so the extremely common
    ``def __init__(self, db: Optional[DbSession])`` raised DIResolutionError
    even though DbSession was registered.

    Handled: ``Optional[X]`` / ``X | None`` -> (X, True);
    ``Annotated[X, ...]`` -> unwrapped (get_type_hints already strips it when
    include_extras is False, this covers the raw-annotation fallback);
    a genuine multi-type ``Union[A, B]`` stays ambiguous -> (None, ...).
    """
    optional = False
    # Annotated[X, ...] -> X
    if get_origin(tp) is not None and hasattr(tp, "__metadata__"):
        tp = get_args(tp)[0]

    origin = get_origin(tp)
    if origin is Union or type(tp).__name__ == "UnionType":  # PEP 604 X | None
        args = [a for a in get_args(tp) if a is not type(None)]
        optional = len(args) != len(get_args(tp))
        if len(args) != 1:
            return None, optional          # Union[A, B] is ambiguous
        tp = args[0]
        if get_origin(tp) is not None and hasattr(tp, "__metadata__"):
            tp = get_args(tp)[0]

    return (tp if inspect.isclass(tp) else None), optional


class _ServiceRegistration:
    __slots__ = ("cls", "lifetime", "factory", "instance")

    def __init__(self, cls: type, lifetime: str,
                 factory: Optional[Callable] = None,
                 instance: Any = None) -> None:
        self.cls = cls
        self.lifetime = lifetime
        self.factory = factory
        self.instance = instance


def _track_disposable(inst: Any, bucket: List[Any]) -> None:
    if any(hasattr(inst, m) for m in ("dispose", "close", "adispose", "aclose")):
        bucket.append(inst)


class ServiceScope:
    """
    v6.2: .NET IServiceScope muadili — scope boyunca 'scoped' servisler tek
    instance paylaşır; scope kapanınca dispose/close (async: adispose/aclose)
    ters sırada çağrılır.

    Kullanım:
        with container.create_scope() as scope:
            m = scope.mediator(base_mediator)
            m.send(CreateOrder(...))          # aynı scope → aynı DB session

        async with container.create_scope() as scope:
            m = scope.mediator(base_mediator)
            await m.send_async(...)
    """
    __slots__ = ("_container", "_cache", "_lock", "_disposables", "_closed")

    def __init__(self, container: "ServiceContainer") -> None:
        self._container = container
        self._cache: Dict[type, Any] = {}
        self._lock = RLock()
        self._disposables: List[Any] = []
        self._closed = False

    # ---- çözümleme ----

    def resolve(self, cls: type) -> Any:
        if self._closed:
            raise DIResolutionError("Scope is disposed; resolve() cannot be called.")
        return self._container._resolve(cls, scope=self, stack=())

    def _get_or_create(self, cls: type, builder: Callable[[], Any]) -> Any:
        inst = self._cache.get(cls)
        if inst is None:
            # nogil: double-checked locking — scoped instance tekilliği garanti
            with self._lock:
                inst = self._cache.get(cls)
                if inst is None:
                    inst = builder()
                    self._cache[cls] = inst
                    _track_disposable(inst, self._disposables)
        return inst

    def _track_transient(self, inst: Any) -> None:
        with self._lock:
            _track_disposable(inst, self._disposables)

    def mediator(self, base_mediator: "Mediator") -> "Mediator":
        """Bu scope'a bağlı Mediator klonu döndürür (registry'ler paylaşılır)."""
        return _bind_mediator_to_resolver(base_mediator, self.resolve)

    # ---- yaşam döngüsü ----

    def dispose(self) -> None:
        """Scoped/transient instance'ları ters sırada kapat (sync)."""
        if self._closed:
            return
        self._closed = True
        for inst in reversed(self._disposables):
            for m in ("dispose", "close"):
                fn = getattr(inst, m, None)
                if callable(fn) and not inspect.iscoroutinefunction(fn):
                    try:
                        fn()
                    except Exception as e:
                        _debug_log(f"⚠️ Scope dispose hatası ({type(inst).__name__}): {e}")
                    break
        self._disposables.clear()
        self._cache.clear()

    async def adispose(self) -> None:
        """Async dispose — adispose/aclose öncelikli, yoksa sync fallback."""
        if self._closed:
            return
        self._closed = True
        for inst in reversed(self._disposables):
            done = False
            for m in ("adispose", "aclose"):
                fn = getattr(inst, m, None)
                if callable(fn):
                    try:
                        await fn()
                    except Exception as e:
                        _debug_log(f"⚠️ Scope adispose hatası ({type(inst).__name__}): {e}")
                    done = True
                    break
            if not done:
                for m in ("dispose", "close"):
                    fn = getattr(inst, m, None)
                    if callable(fn) and not inspect.iscoroutinefunction(fn):
                        try:
                            fn()
                        except Exception as e:
                            _debug_log(f"⚠️ Scope dispose hatası ({type(inst).__name__}): {e}")
                        break
        self._disposables.clear()
        self._cache.clear()

    def __enter__(self) -> "ServiceScope":
        return self

    def __exit__(self, *exc) -> None:
        self.dispose()

    async def __aenter__(self) -> "ServiceScope":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.adispose()


class ServiceContainer:
    """
    v6.2: Auto-wiring destekli DI container (.NET IServiceCollection +
    IServiceProvider muadili).

    Yaşam döngüleri:
        register_singleton(cls)  → container ömrü boyunca tek instance
        register_scoped(cls)     → scope (örn. HTTP isteği) başına tek instance
        register_transient(cls)  → her resolve'da yeni instance

    Auto-wiring:
        Factory verilmezse cls.__init__ parametrelerinin type hint'leri okunur
        ve her bağımlılık recursive resolve edilir (dairesel bağımlılık tespiti
        dahil). Default değeri olan çözülemeyen parametreler atlanır.

    Kullanım:
        container = ServiceContainer()
        container.register_singleton(Config)
        container.register_scoped(DbSession)          # istek başına 1 session
        container.register_transient(UserRepository)  # ctor'unda DbSession var

        mediator = Mediator(handler_factory=container)   # v6.1 köprüsü
        with container.create_scope() as scope:
            scope.mediator(mediator).send(CreateUser(...))
    """
    __slots__ = ("registrations", "_singletons", "_lock", "_disposables")

    def __init__(self) -> None:
        self.registrations: Dict[type, _ServiceRegistration] = {}
        self._singletons: Dict[type, Any] = {}
        self._lock = RLock()
        self._disposables: List[Any] = []

    # ---- kayıt API'si ----

    def register_singleton(self, cls: type,
                           factory: Optional[Callable] = None) -> "ServiceContainer":
        with self._lock:  # v6.4 nogil: kayıt mutasyonları kilitli
            self.registrations[cls] = _ServiceRegistration(cls, "singleton", factory)
        return self

    def register_scoped(self, cls: type,
                        factory: Optional[Callable] = None) -> "ServiceContainer":
        with self._lock:  # v6.4 nogil
            self.registrations[cls] = _ServiceRegistration(cls, "scoped", factory)
        return self

    def register_transient(self, cls: type,
                           factory: Optional[Callable] = None) -> "ServiceContainer":
        with self._lock:  # v6.4 nogil
            self.registrations[cls] = _ServiceRegistration(cls, "transient", factory)
        return self

    def register_instance(self, cls: type, instance: Any) -> "ServiceContainer":
        """Hazır instance'ı singleton olarak kaydet (.NET AddSingleton(obj))."""
        with self._lock:  # v6.4 nogil
            self.registrations[cls] = _ServiceRegistration(cls, "singleton",
                                                           instance=instance)
            self._singletons[cls] = instance
        return self

    # ---- çözümleme ----

    def resolve(self, cls: type) -> Any:
        """Root scope'tan çözümle. Scoped servisler için create_scope() gerekir."""
        return self._resolve(cls, scope=None, stack=())

    def create_scope(self) -> ServiceScope:
        return ServiceScope(self)

    def _resolve(self, cls: type, scope: Optional[ServiceScope],
                 stack: Tuple[type, ...],
                 singleton_owner: Optional[type] = None) -> Any:
        reg = self.registrations.get(cls)

        if reg is None:
            # Kayıtsız sınıf → implicit transient + auto-wiring
            # (.NET'te hata olur; discovery tabanlı handler'lar için pratik)
            if not _is_injectable_type(cls):
                raise DIResolutionError(
                    f"{cls!r} is not registered and cannot be auto-wired "
                    f"(builtin/primitive type).")
            inst = self._build(cls, None, scope, stack, singleton_owner)
            if scope is not None:
                scope._track_transient(inst)
            return inst

        if reg.lifetime == "singleton":
            inst = self._singletons.get(cls)
            if inst is None:
                # nogil: double-checked locking
                with self._lock:
                    inst = self._singletons.get(cls)
                    if inst is None:
                        # v6.7: build singleton dependencies WITHOUT the scope.
                        # Passing it let a singleton capture the first request's
                        # scoped instance and keep using it after that scope was
                        # disposed (classic captive dependency).
                        inst = self._build(cls, reg, None, stack,
                                           singleton_owner or cls)
                        self._singletons[cls] = inst
                        _track_disposable(inst, self._disposables)
            return inst

        if reg.lifetime == "scoped":
            if singleton_owner is not None:
                raise DIResolutionError(
                    f"Captive dependency: singleton "
                    f"'{singleton_owner.__name__}' cannot consume scoped "
                    f"service '{cls.__name__}'. The singleton would hold on to "
                    f"the first scope's instance forever and keep using it "
                    f"after that scope was disposed. Register "
                    f"'{singleton_owner.__name__}' as scoped, or inject a "
                    f"factory/ServiceContainer and resolve '{cls.__name__}' "
                    f"per operation. (.NET DI rejects this too.)")
            if scope is None:
                raise DIResolutionError(
                    f"{cls.__name__} is registered as 'scoped' and cannot be "
                    f"resolved from the root container. Use "
                    f"`with container.create_scope() as scope:` and call "
                    f"scope.resolve() (same rule as .NET).")
            return scope._get_or_create(
                cls, lambda: self._build(cls, reg, scope, stack,
                                         singleton_owner))

        # transient
        inst = self._build(cls, reg, scope, stack, singleton_owner)
        if scope is not None:
            scope._track_transient(inst)
        return inst

    # ---- inşa ----

    def _build(self, cls: type, reg: Optional[_ServiceRegistration],
               scope: Optional[ServiceScope],
               stack: Tuple[type, ...],
               singleton_owner: Optional[type] = None) -> Any:
        if cls in stack:
            chain = " -> ".join(t.__name__ for t in stack + (cls,))
            raise DIResolutionError(f"Circular dependency: {chain}")
        stack = stack + (cls,)

        if reg is not None:
            if reg.instance is not None:
                return reg.instance
            if reg.factory is not None:
                try:
                    n_req = sum(
                        1 for p in inspect.signature(reg.factory).parameters.values()
                        if p.default is inspect.Parameter.empty
                        and p.kind not in (inspect.Parameter.VAR_POSITIONAL,
                                           inspect.Parameter.VAR_KEYWORD))
                except (TypeError, ValueError):
                    n_req = 0
                # factory(resolver) imzası desteklenir → scope-aware çözücü geçilir
                if n_req >= 1:
                    resolver = scope if scope is not None else self
                    return reg.factory(resolver)
                return reg.factory()

        return self._autowire(cls, scope, stack, singleton_owner)

    def _autowire(self, cls: type, scope: Optional[ServiceScope],
                  stack: Tuple[type, ...],
                  singleton_owner: Optional[type] = None) -> Any:
        init = getattr(cls, "__init__", None)
        if init is None or init is object.__init__:
            return cls()

        try:
            hints = get_type_hints(init)
        except Exception:
            hints = getattr(init, "__annotations__", {}) or {}

        try:
            sig = inspect.signature(init)
        except (TypeError, ValueError):
            return cls()

        kwargs: Dict[str, Any] = {}
        for name, p in list(sig.parameters.items())[1:]:  # self atla
            if p.kind in (inspect.Parameter.VAR_POSITIONAL,
                          inspect.Parameter.VAR_KEYWORD):
                continue
            raw = hints.get(name)
            # v6.7: unwrap Optional[X] / X | None / Annotated[X, ...] so that
            # ordinary Python signatures auto-wire instead of erroring out.
            tp, is_optional = _unwrap_hint(raw) if raw is not None else (None, False)
            has_default = p.default is not inspect.Parameter.empty

            if tp is not None and (tp in self.registrations
                                   or _is_injectable_type(tp)):
                try:
                    kwargs[name] = self._resolve(tp, scope, stack,
                                                 singleton_owner)
                except DIResolutionError:
                    # Optional[...] with no default -> inject None rather than
                    # failing the whole graph; a real default always wins.
                    if has_default:
                        continue
                    if is_optional:
                        kwargs[name] = None
                    else:
                        raise
            elif has_default:
                continue
            else:
                detail = ("type hint is missing or not injectable"
                          if raw is None else
                          f"hint {raw!r} is ambiguous (a Union of several "
                          f"concrete types cannot be auto-wired)")
                raise DIResolutionError(
                    f"Cannot resolve parameter '{name}' of "
                    f"{cls.__name__}.__init__: {detail}, and it has no default "
                    f"value. Add a type hint, give it a default, or register a "
                    f"factory with the container.")
        return cls(**kwargs)

    # ---- yaşam döngüsü ----

    def dispose(self) -> None:
        """Singleton'ları ters sırada kapat (container ömrü sonu)."""
        with self._lock:
            disposables, self._disposables = self._disposables, []
            self._singletons.clear()
        for inst in reversed(disposables):
            for m in ("dispose", "close"):
                fn = getattr(inst, m, None)
                if callable(fn) and not inspect.iscoroutinefunction(fn):
                    try:
                        fn()
                    except Exception as e:
                        _debug_log(f"⚠️ Container dispose hatası "
                                   f"({type(inst).__name__}): {e}")
                    break


# ---- v6.2: scope'a bağlı Mediator klonu ------------------------------------

def _bind_mediator_to_resolver(base: "Mediator",
                               resolve: Callable[[type], Any]) -> "Mediator":
    """
    Mediator'ın scope'a bağlı hafif klonunu üretir:
      • Registry'ler (handler/notification/behavior) REFERANSLA paylaşılır —
        klon maliyeti O(1), sonradan register edilenler de görünür.
      • handler_factory = scope.resolve, lifetime = "transient" → her send'de
        çözümleme scope üzerinden yapılır; 'scoped' kayıtlı bağımlılıklar
        scope cache'inden gelir (istek başına tek DB session senaryosu).
      • Chain/singleton cache'leri klona özel — scope'lar birbirini kirletmez.
    """
    clone = object.__new__(Mediator)
    for slot in Mediator.__slots__:
        setattr(clone, slot, getattr(base, slot))
    clone._handler_factory = resolve
    clone._handler_lifetime = "transient"
    clone._singleton_cache = {}
    clone._compiled_chains = {}
    clone._compiled_async_chains = {}
    clone._compiled_stream_chains = {}
    clone._chain_lock = Lock()
    return clone


@contextmanager
def scoped_mediator(base_mediator: "Mediator",
                    container: ServiceContainer) -> Iterator["Mediator"]:
    """
    v6.2: `using var scope = provider.CreateScope()` muadili kısayol.

        with scoped_mediator(mediator, container) as m:
            m.send(CreateOrder(...))   # scoped servisler bu blokta paylaşılır
        # blok sonu → scope dispose (DB session kapanır)

    Not: Deferred (parametreli-ctor) handler'ların discovery'de korunması için
    base Mediator'ı `Mediator(handler_factory=container)` ile oluşturun.
    """
    scope = container.create_scope()
    try:
        yield scope.mediator(base_mediator)
    finally:
        scope.dispose()


# ---- v6.2: FastAPI köprüsü --------------------------------------------------

def make_fastapi_mediator_dependency(base_mediator: "Mediator",
                                     container: ServiceContainer):
    """
    v6.2: FastAPI için istek başına scope'lu mediator dependency üretir
    (fastapi import edilmez — sıfır bağımlılık korunur).

        mediator = Mediator(handler_factory=container)
        get_mediator = make_fastapi_mediator_dependency(mediator, container)

        @app.post("/orders")
        async def create_order(cmd: CreateOrderDto,
                               m: Mediator = Depends(get_mediator)):
            return await m.send_async(CreateOrder(**cmd.model_dump()))

    Her istek kendi ServiceScope'unu alır; yanıt dönünce scope async dispose
    edilir (scoped DB session'lar kapanır).
    """
    async def _dependency():
        scope = container.create_scope()
        try:
            yield scope.mediator(base_mediator)
        finally:
            await scope.adispose()
    return _dependency
