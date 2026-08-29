# -*- coding: utf-8 -*-
"""py_mediatR.behaviors — built-in pipeline behaviors.

v6.7.0'da tek dosyalik `py_mediatR.py` alt modullere ayrildi.
Kod govdesi birebir aynidir. Genel API icin `import py_mediatR` kullanin;
bu modul bir uygulama detayidir ve dogrudan import edilmesi gerekmez.
"""

import inspect
import sys
import os
import asyncio
import contextvars
import importlib
import logging
import random
import time
import json
import hashlib
from enum import Enum
from pathlib import Path
from contextlib import contextmanager
from typing import (
    Dict, List, Type, Tuple, Any, Iterable, Callable, Optional, Awaitable,
    AsyncIterator, Iterator, Union, Generic, TypeVar, get_type_hints,
    get_args, get_origin,
)
from dataclasses import is_dataclass, fields
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, RLock, Thread

from ._config import _debug_log
from .contracts import IPipelineBehavior, IRequest, IValidator, UnauthorizedError
from .tracing import _flow_note
from .coercion import _maybe_await, _sync_run_coro


# ============================================================================
# BUILT-IN PIPELINE BEHAVIORS (hazır cross-cutting concerns)
# Tümü hem sync (send) hem async (send_async) zincirde çalışacak şekilde
# next_handler() dönüşünü _maybe_await ile sarar (async path'te awaitable gelir).
# ============================================================================

class LoggingBehavior(IPipelineBehavior):
    """Request giriş/çıkışını ve hataları loglar."""
    order = -100  # En dışta

    def __init__(self, logger: Optional[logging.Logger] = None,
                 level: int = logging.INFO) -> None:
        self.logger = logger or logging.getLogger("mediatr.logging")
        self.level = level

    def handle(self, request, next_handler):
        name = type(request).__name__
        self.logger.log(self.level, f"→ {name}")
        try:
            response = next_handler()
        except Exception as e:
            self.logger.error(f"✗ {name} FAILED: {type(e).__name__}: {e}")
            raise
        if inspect.isawaitable(response):
            # v6.4: async zincirde aynı loop'ta await edip sonra logla
            async def _log_after_await():
                try:
                    resolved = await response
                except Exception as e:
                    self.logger.error(f"✗ {name} FAILED: {type(e).__name__}: {e}")
                    raise
                self.logger.log(self.level, f"← {name} OK")
                return resolved
            return _log_after_await()
        self.logger.log(self.level, f"← {name} OK")
        return response


class PerformanceBehavior(IPipelineBehavior):
    """Handler süresini ölçer, eşik aşılırsa UYARI loglar."""
    order = -90

    def __init__(self, threshold_ms: float = 500.0,
                 logger: Optional[logging.Logger] = None) -> None:
        self.threshold_ms = threshold_ms
        self.logger = logger or logging.getLogger("mediatr.perf")

    def _report(self, request, start: float) -> None:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms > self.threshold_ms:
            self.logger.warning(
                f"⚠️ Slow handler: {type(request).__name__} = "
                f"{elapsed_ms:.2f}ms (threshold: {self.threshold_ms}ms)"
            )

    def handle(self, request, next_handler):
        start = time.perf_counter()
        try:
            response = next_handler()
        except Exception:
            self._report(request, start)
            raise
        if inspect.isawaitable(response):
            # v6.4: async zincirde gerçek süre await sonrası ölçülür
            async def _measure_after_await():
                try:
                    return await response
                finally:
                    self._report(request, start)
            return _measure_after_await()
        self._report(request, start)
        return response


class ValidationBehavior(IPipelineBehavior):
    """
    request.validate() metodu varsa çağırır. Hata fırlatırsa pipeline durur.
    Pydantic/dataclass request'leriyle sorunsuz çalışır.

    v6: FluentValidation muadili — ayrı IValidator sınıfları da verilebilir:
        ValidationBehavior(validators=[CreateUserValidator(), ...])
    Önce eşleşen IValidator'lar (order sırasıyla), sonra request.validate()
    çalışır. `validators` verilmezse davranış v5 ile birebir aynıdır.
    """
    order = -80

    def __init__(self, validators: Optional[List[IValidator]] = None) -> None:
        self.validators: List[IValidator] = sorted(
            validators or [], key=lambda v: getattr(v, "order", 0))

    @staticmethod
    def _validator_applies(validator, request) -> bool:
        at = getattr(validator, "applies_to", None)
        if at is None:
            return True
        types = at if isinstance(at, tuple) else (at,)
        return isinstance(request, types)

    def handle(self, request, next_handler):
        for v in self.validators:
            if self._validator_applies(v, request):
                v.validate(request)
        validate = getattr(request, "validate", None)
        if callable(validate):
            validate()
        return next_handler()


class CachingBehavior(IPipelineBehavior):
    """
    Response cache (TTL + LRU eviction).
    Yalnızca `cacheable = True` niteliğine sahip request'leri cache'ler.
    Dataclass ve hashable request'ler otomatik desteklenir.

    NOT: Sync chain'de doğrudan cache döner. Async chain'de next_handler()
    awaitable döndüğü için, cache MISS halinde awaitable'ı çağıran tarafa
    (chain runner) bırakırız — caching için await edilmiş değeri saklamak
    adına async-safe sarmalama kullanılır.
    """
    order = -70

    def __init__(self, ttl_seconds: Optional[float] = None,
                 max_size: int = 1000) -> None:
        if max_size < 0:
            raise ValueError(f"max_size negatif olamaz: {max_size}")
        # v6.5: max_size=0 → cache açıkça DEVRE DIŞI (her istek handler'a gider)
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._cache: Dict[Any, Tuple[Any, float]] = {}
        self._lock = RLock()

    @staticmethod
    def _freeze(value: Any, _depth: int = 0):
        """
        v6.5: değeri hashable canonical forma çevirir (list→tuple, dict→sorted
        tuple, set→frozenset, nested dataclass→alan tuple'ı). repr KULLANILMAZ —
        aynı repr'li farklı içerikler çakışamaz. Çevrilemiyorsa TypeError.
        """
        if _depth > 16:
            raise TypeError("cache key depth limit exceeded")
        if isinstance(value, (list, tuple)):
            return ("__seq__", tuple(
                CachingBehavior._freeze(v, _depth + 1) for v in value))
        if isinstance(value, dict):
            items = tuple(
                (CachingBehavior._freeze(k, _depth + 1),
                 CachingBehavior._freeze(v, _depth + 1))
                for k, v in value.items())
            return ("__map__", tuple(sorted(items, key=repr)))  # repr sadece SIRALAMA için
        if isinstance(value, (set, frozenset)):
            return ("__set__", frozenset(
                CachingBehavior._freeze(v, _depth + 1) for v in value))
        if is_dataclass(value) and not isinstance(value, type):
            return ("__dc__", type(value), tuple(
                (f.name, CachingBehavior._freeze(getattr(value, f.name), _depth + 1))
                for f in fields(value)))
        hash(value)  # unhashable ise TypeError → çağıran cache'i atlar
        return value

    def _key(self, request: Any):
        # v6.6: açık cache_key() protokolü — kararlı/çakışmaya dayanıklı anahtar
        # üretmek isteyen (özellikle mutable) request'ler için önerilen yol.
        ck = getattr(request, "cache_key", None)
        if callable(ck):
            try:
                key = (type(request), ("__ck__", ck()))
                hash(key)
                return key
            except Exception:
                return None
        if is_dataclass(request):
            try:
                key = (type(request), self._freeze(request))
                hash(key)
                return key
            except Exception:
                return None  # v6.5: güvenli anahtar üretilemiyor → cache DIŞI
        try:
            hash(request)
        except TypeError:
            return None
        # v6.6: ham hash DEĞİL, nesnenin KENDİSİ anahtardır — dict, hash
        # çakışmasında __eq__ ile ayırt eder (eşit olmayan request'ler asla
        # aynı cache girdisini paylaşamaz). Not: request mutable ise anahtar
        # kararlılığı çağıranın sorumluluğundadır; bunun için cache_key()
        # protokolü tercih edilmelidir.
        return (type(request), request)

    def _get(self, key):
        now = time.time()
        with self._lock:
            if key in self._cache:
                value, ts = self._cache[key]
                if self.ttl_seconds is None or (now - ts) < self.ttl_seconds:
                    # v6.4 gerçek LRU: hit'te recency tazele (sona taşı)
                    del self._cache[key]
                    self._cache[key] = (value, ts)
                    return (True, value)
                del self._cache[key]
        return (False, None)

    def _set(self, key, response):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self.max_size:
                # v6.4 gerçek LRU: en az kullanılan = insertion-order başı
                del self._cache[next(iter(self._cache))]
            self._cache[key] = (response, time.time())

    def handle(self, request, next_handler):
        if self.max_size == 0 or not getattr(request, "cacheable", False):
            _flow_note("not cacheable - pass through")
            return next_handler()

        key = self._key(request)
        if key is None:
            _flow_note("no safe cache key - pass through")
            return next_handler()

        hit, value = self._get(key)
        if hit:
            _flow_note("CACHE HIT - handler NOT called")
            return value

        _flow_note("cache miss")
        response = next_handler()

        # Async path: response awaitable ise, await edilmiş değeri cache'leyen
        # bir coroutine döndür (chain runner await edecek).
        if inspect.isawaitable(response):
            async def _cache_after_await():
                resolved = await response
                self._set(key, resolved)
                return resolved
            return _cache_after_await()

        self._set(key, response)
        return response

    def invalidate(self, request_type: Optional[Type[IRequest]] = None) -> None:
        with self._lock:
            if request_type is None:
                self._cache.clear()
            else:
                self._cache = {k: v for k, v in self._cache.items()
                               if k[0] is not request_type}


class RetryBehavior(IPipelineBehavior):
    """
    Handler hata verirse yeniden dener. Exponential backoff destekli.
    Sync path'te time.sleep, async path'te asyncio.sleep kullanır.

    v6: jitter — her beklemeye [0, jitter] sn rastgele ek süre katılır
    (thundering-herd önlemi). Default 0.0 → v5 davranışı birebir korunur.
    """
    order = -60

    def __init__(self, max_attempts: int = 3, delay: float = 0.1,
                 backoff: float = 2.0,
                 on_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
                 jitter: float = 0.0) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.delay = delay
        self.backoff = backoff
        self.on_exceptions = on_exceptions
        self.jitter = max(0.0, float(jitter))

    def _wait_time(self, current_delay: float) -> float:
        if self.jitter:
            return current_delay + random.uniform(0.0, self.jitter)
        return current_delay

    def handle(self, request, next_handler):
        # Async path tespiti: ilk çağrıyı dene, awaitable ise async retry'a geç.
        first = None
        try:
            first = next_handler()
        except self.on_exceptions as e:
            # Sync hata — sync retry döngüsü
            return self._sync_retry(request, next_handler, e)

        if inspect.isawaitable(first):
            return self._async_retry(request, next_handler, first)
        return first

    def _sync_retry(self, request, next_handler, first_exc):
        last_exc = first_exc
        current_delay = self.delay
        for attempt in range(2, self.max_attempts + 1):
            _debug_log(f"🔁 Retry {attempt - 1}/{self.max_attempts - 1} "
                       f"for {type(request).__name__}: {type(last_exc).__name__}")
            _flow_note(f"retry {attempt}/{self.max_attempts} "
                       f"after {type(last_exc).__name__}")
            time.sleep(self._wait_time(current_delay))
            current_delay *= self.backoff
            try:
                return next_handler()
            except self.on_exceptions as e:
                last_exc = e
        raise last_exc

    async def _async_retry(self, request, next_handler, first_awaitable):
        current_delay = self.delay
        last_exc: Optional[BaseException] = None
        # İlk denemeyi await et
        try:
            return await first_awaitable
        except self.on_exceptions as e:
            last_exc = e
        for attempt in range(2, self.max_attempts + 1):
            _debug_log(f"🔁 Async retry {attempt - 1}/{self.max_attempts - 1} "
                       f"for {type(request).__name__}: {type(last_exc).__name__}")
            _flow_note(f"retry {attempt}/{self.max_attempts} "
                       f"after {type(last_exc).__name__}")
            await asyncio.sleep(self._wait_time(current_delay))
            current_delay *= self.backoff
            try:
                return await _maybe_await(next_handler())
            except self.on_exceptions as e:
                last_exc = e
        raise last_exc  # type: ignore[misc]


class TransactionCleanupError(RuntimeError):
    """
    v6.7: rollback()/close() failed while unwinding a transaction.

    Raised only when TransactionBehavior(raise_on_cleanup_failure=True), or on
    the async path when there is no business exception to preserve. In the
    default configuration the cleanup failure is logged at ERROR and attached
    to the original exception as a note instead of replacing it.
    """


class TransactionBehavior(IPipelineBehavior):
    """
    Transaction boundary: başarıda commit, hata'da rollback.
    Sadece request `transactional = True` ise devreye girer.

    session_factory: begin/commit/rollback/close destekleyen session döner.
    Async path'te session.commit/rollback awaitable ise await edilir.
    """
    order = -50

    def __init__(self, session_factory: Callable[[], Any],
                 logger: Optional[logging.Logger] = None,
                 raise_on_cleanup_failure: bool = False) -> None:
        self.session_factory = session_factory
        self.logger = logger or logging.getLogger("mediatr.transaction")
        # v6.7: opt-in strict mode. Default False keeps the original business
        # exception as the one the caller sees; the cleanup failure is always
        # logged at ERROR and attached to that exception as a note.
        self.raise_on_cleanup_failure = raise_on_cleanup_failure

    def handle(self, request, next_handler):
        if not getattr(request, "transactional", False):
            return next_handler()

        session = self.session_factory()
        self._guard_async_session(session)
        result = None
        try:
            result = next_handler()
        except Exception as exc:
            self._rollback_sync(session, exc)
            self._close_sync(session, exc)
            raise

        if inspect.isawaitable(result):
            return self._finish_async(session, result)

        # Sync commit
        try:
            if hasattr(session, "commit"):
                session.commit()
        finally:
            self._close_sync(session, None)
        return result

    # ---- v6.7: fail fast instead of leaking a transaction ----

    @staticmethod
    def _is_loop_bound_session(session: Any) -> bool:
        """Does this session's rollback/commit require an event loop?"""
        return any(inspect.iscoroutinefunction(getattr(session, name, None))
                   for name in ("rollback", "commit", "close"))

    def _guard_async_session(self, session: Any) -> None:
        """
        v6.7: an async session driven from sync send() *inside a running loop*
        cannot be cleaned up correctly — the bridge would run rollback() on a
        different event loop and real drivers (asyncpg, SQLAlchemy AsyncSession)
        reject that. Previously the failure was swallowed and the transaction
        silently stayed open. Now it is refused up front, mirroring what
        send() already does for async handlers.
        """
        if not self._is_loop_bound_session(session):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop -> asyncio.run bridge is safe enough
        self._close_best_effort(session)
        raise TypeError(
            f"{type(session).__name__} is an async session but the request was "
            f"dispatched through the synchronous send() from inside a running "
            f"event loop. Rollback/commit would have to run on a different "
            f"loop, which loop-bound drivers reject — the transaction would "
            f"stay open and the connection would leak. Use "
            f"'await mediator.send_async(request)' instead.")

    def _close_best_effort(self, session: Any) -> None:
        """Close a session we are about to abandon; never masks the caller's error."""
        cl = getattr(session, "close", None)
        if not cl:
            return
        try:
            res = cl()
            if inspect.isawaitable(res):
                res.close()  # never awaited -> avoid 'never awaited' warning
        except Exception as e:  # noqa: BLE001
            self.logger.warning("Could not close abandoned session %s: %s",
                                type(session).__name__, e)

    async def _finish_async(self, session, awaitable):
        try:
            result = await awaitable
        except Exception as exc:
            await self._cleanup_async(session, "rollback", exc)
            await self._cleanup_async(session, "close", exc)
            raise
        try:
            commit = getattr(session, "commit", None)
            if commit:
                await _maybe_await(commit())
        finally:
            await self._cleanup_async(session, "close", None)
        return result

    async def _cleanup_async(self, session, step: str,
                             original: Optional[BaseException]):
        """
        v6.7: async counterpart of _cleanup_sync. Previously a failing rollback
        here replaced the caller's business exception; now the original error
        stays on top and the cleanup failure is logged and noted.
        """
        fn = getattr(session, step, None)
        if not fn:
            return
        try:
            await _maybe_await(fn())
        except Exception as cleanup_exc:  # noqa: BLE001
            detail = (f"Transaction {step}() FAILED on "
                      f"{type(session).__name__}: "
                      f"{type(cleanup_exc).__name__}: {cleanup_exc}. "
                      f"The transaction may still be open and the connection "
                      f"may have leaked.")
            self.logger.error(detail, exc_info=True)
            if original is not None and hasattr(original, "add_note"):
                original.add_note(f"[py_mediatR] {detail}")
            if self.raise_on_cleanup_failure or original is None:
                raise TransactionCleanupError(detail) from cleanup_exc

    def _cleanup_sync(self, session, step: str, original: Optional[BaseException]):
        """
        v6.7: run rollback/close on the sync path WITHOUT swallowing failures.

        Until v6.7 this was `except Exception: pass`. A failed rollback left the
        transaction open and the connection leaked while the caller saw only the
        original business error and reasonably assumed the write was undone.
        Now the failure is logged with a traceback and attached to the original
        exception; with raise_on_cleanup_failure=True it is raised outright.
        """
        fn = getattr(session, step, None)
        if not fn:
            return
        try:
            res = fn()
            if inspect.isawaitable(res):
                _sync_run_coro(res)
        except Exception as cleanup_exc:  # noqa: BLE001
            detail = (f"Transaction {step}() FAILED on "
                      f"{type(session).__name__}: "
                      f"{type(cleanup_exc).__name__}: {cleanup_exc}. "
                      f"The transaction may still be open and the connection "
                      f"may have leaked.")
            self.logger.error(detail, exc_info=True)
            if original is not None and hasattr(original, "add_note"):
                original.add_note(f"[py_mediatR] {detail}")  # Python 3.11+
            if self.raise_on_cleanup_failure:
                raise TransactionCleanupError(detail) from cleanup_exc

    def _rollback_sync(self, session, original: Optional[BaseException] = None):
        self._cleanup_sync(session, "rollback", original)

    def _close_sync(self, session, original: Optional[BaseException] = None):
        self._cleanup_sync(session, "close", original)

    @staticmethod
    async def _rollback_async(session):
        rb = getattr(session, "rollback", None)
        if rb:
            await _maybe_await(rb())

    @staticmethod
    async def _close_async(session):
        cl = getattr(session, "close", None)
        if cl:
            await _maybe_await(cl())


class AuthorizationBehavior(IPipelineBehavior):
    """
    v6: Yetkilendirme cross-cutting'i (.NET projelerindeki AuthorizationBehavior
    kalıbının muadili). Request üzerindeki `requires_permission` niteliğine
    (str veya str iterable'ı) bakar; her izin için `permission_checker` çağrılır.
    Herhangi biri False dönerse UnauthorizedError fırlatılır ve handler
    HİÇ çalışmaz. `requires_permission` yoksa davranış no-op'tur.

    permission_checker(request, permission) -> bool  (sync veya async olabilir)

    Örnek:
        class DeleteUserCommand(IRequest):
            requires_permission = "users.delete"

        mediator = Mediator(behaviors=[
            AuthorizationBehavior(lambda req, perm: current_user.has(perm)),
        ])
    """
    order = -85  # Validation'dan (-80) ÖNCE — yetkisiz istekte validasyon bile çalışmasın

    def __init__(self, permission_checker: Callable[[IRequest, str], Any]) -> None:
        self.permission_checker = permission_checker

    @staticmethod
    def _deny(perm: str, request) -> UnauthorizedError:
        return UnauthorizedError(
            f"'{perm}' izni yok: {type(request).__name__} reddedildi.")

    def handle(self, request, next_handler):
        required = getattr(request, "requires_permission", None)
        if required:
            perms = (required,) if isinstance(required, str) else tuple(required)
            for i, perm in enumerate(perms):
                allowed = self.permission_checker(request, perm)
                if inspect.isawaitable(allowed):
                    # v6.4: async checker aynı loop üzerinde await edilir
                    return self._handle_async(request, next_handler,
                                              perms, i, allowed)
                if not allowed:
                    raise self._deny(perm, request)
        return next_handler()

    async def _handle_async(self, request, next_handler, perms, i, first_awaitable):
        allowed = await first_awaitable
        if not allowed:
            raise self._deny(perms[i], request)
        for perm in perms[i + 1:]:
            allowed = self.permission_checker(request, perm)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                raise self._deny(perm, request)
        return await _maybe_await(next_handler())


class TracingBehavior(IPipelineBehavior):
    """
    v6: OpenTelemetry span açan gözlemlenebilirlik behavior'ı.
    opentelemetry kurulu değilse sessizce no-op'a düşer — sıfır-bağımlılık
    ilkesi korunur.

        mediator = Mediator(behaviors=[TracingBehavior()])
    """
    order = -95  # Logging (-100) ile Performance (-90) arasında

    def __init__(self, tracer: Any = None, service_name: str = "py_mediatR") -> None:
        if tracer is None:
            try:
                from opentelemetry import trace  # opsiyonel bağımlılık
                tracer = trace.get_tracer(service_name)
            except ImportError:
                tracer = None
        self.tracer = tracer

    def handle(self, request, next_handler):
        if self.tracer is None:
            return next_handler()
        response = None
        span_cm = self.tracer.start_as_current_span(type(request).__name__)
        span = span_cm.__enter__()
        try:
            response = next_handler()
        except BaseException as e:
            try:
                span.record_exception(e)
            except Exception:
                pass
            span_cm.__exit__(type(e), e, e.__traceback__)
            raise
        if inspect.isawaitable(response):
            # v6.4: async zincirde span await bitene kadar açık kalır
            async def _trace_after_await():
                try:
                    resolved = await response
                except BaseException as e:
                    try:
                        span.record_exception(e)
                    except Exception:
                        pass
                    span_cm.__exit__(type(e), e, e.__traceback__)
                    raise
                span_cm.__exit__(None, None, None)
                return resolved
            return _trace_after_await()
        span_cm.__exit__(None, None, None)
        return response
