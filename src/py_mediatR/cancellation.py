# -*- coding: utf-8 -*-
"""py_mediatR.cancellation — CancellationToken / CancellationTokenSource.

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


# ============================================================================
# v6.3: CANCELLATION TOKEN (.NET CancellationToken/CancellationTokenSource paritesi)
# ============================================================================
# .NET semantiği birebir: KOOPERATİF iptal — token iptal edildiğinde çalışan
# kod zorla durdurulmaz; kontrol noktalarında OperationCancelledError fırlatılır.
#   • send/send_async/create_stream/publish/publish_async artık opsiyonel
#     `cancellation_token=` kabul eder (verilmezse davranış birebir v6.2).
#   • Handler `handle(self, req, cancellation_token=...)` imzası bildirirse
#     token otomatik enjekte edilir (.NET Handle(request, ct) muadili).
#   • Bildirmeyen handler/behavior'lar `current_cancellation_token()` ile erişir
#     (contextvar — async task'lere otomatik taşınır).
#   • CancellationTokenSource: cancel(), cancel_after(saniye), register(cb),
#     create_linked(...) (.NET CreateLinkedTokenSource).
#   • create_stream: her item arasında token kontrol edilir → gerçek streaming
#     iptali.
# Tamamı ADDITIF — token verilmeyen çağrılarda sıfır ek maliyet.
#
# v6.6 NOT — .NET'ten BİLİNÇLİ SAPMA: .NET MediatR token'ı her pipeline
# bileşenine (behavior/processor/exception handler/publisher) doğrudan
# parametre olarak geçirir. Burada ise token yalnızca handler imzasına
# enjekte edilir; diğer bileşenler current_cancellation_token() (contextvar)
# ile erişir. Contextvar asyncio task'lerine ve thread'lere kopyalanarak
# taşınır — izolasyon testlerle doğrulanmıştır. Bu bir parity özelliği değil,
# Python'a uygun belgelenmiş bir tasarım tercihidir.

import contextvars as _contextvars
import threading as _threading


class OperationCancelledError(Exception):
    """v6.3: .NET OperationCanceledException muadili — iptal sinyali."""
    def __init__(self, message: str = "The operation was cancelled.") -> None:
        super().__init__(message)


class CancellationToken:
    """
    v6.3: .NET CancellationToken muadili (salt-okunur görünüm).
    CancellationTokenSource üzerinden üretilir; `CancellationToken.none`
    hiç iptal edilmeyen paylaşımlı token'dır (.NET CancellationToken.None).
    """
    __slots__ = ("_source",)

    none: "CancellationToken" = None  # aşağıda atanır

    def __init__(self, source: Optional["CancellationTokenSource"] = None) -> None:
        self._source = source

    @property
    def is_cancellation_requested(self) -> bool:
        return self._source is not None and self._source.is_cancellation_requested

    @property
    def can_be_cancelled(self) -> bool:
        return self._source is not None

    def throw_if_cancellation_requested(self) -> None:
        if self.is_cancellation_requested:
            raise OperationCancelledError(
                "Cancellation requested"
                + (f": {self._source.reason}" if self._source.reason else "."))

    def register(self, callback: Callable[[], None]) -> "CancellationTokenRegistration":
        """
        İptal anında (veya zaten iptalse hemen) çağrılacak callback kaydet.
        v6.5: .NET gibi CancellationTokenRegistration döndürür — dispose() /
        unregister() ile kayıt sökülür (context manager olarak da kullanılabilir).
        """
        if self._source is None:
            # none token hiç iptal olmaz → no-op registration
            return CancellationTokenRegistration(None, None)
        return self._source._register(callback)


CancellationToken.none = CancellationToken(None)


class CancellationTokenRegistration:
    """
    v6.5: .NET CancellationTokenRegistration muadili.
    dispose()/unregister() callback kaydını kaynaktan söker (idempotent).

        reg = token.register(cb)
        ...
        reg.dispose()          # veya: with token.register(cb): ...
    """
    __slots__ = ("_source", "_callback")

    def __init__(self, source: Optional["CancellationTokenSource"],
                 callback: Optional[Callable[[], None]]) -> None:
        self._source = source
        self._callback = callback

    def dispose(self) -> None:
        src, self._source = self._source, None
        cb, self._callback = self._callback, None
        if src is not None and cb is not None:
            src._unregister(cb)

    unregister = dispose  # alias

    def __enter__(self) -> "CancellationTokenRegistration":
        return self

    def __exit__(self, *exc) -> None:
        self.dispose()


class CancellationTokenSource:
    """
    v6.3: .NET CancellationTokenSource muadili.

    Kullanım:
        cts = CancellationTokenSource()
        cts.cancel_after(2.0)                 # 2 sn sonra otomatik iptal
        await mediator.send_async(req, cancellation_token=cts.token)
        ...
        cts.cancel(reason="kullanıcı vazgeçti")

    Linked (.NET CreateLinkedTokenSource):
        linked = CancellationTokenSource.create_linked(cts1.token, cts2.token)
    """
    __slots__ = ("_cancelled", "_reason", "_lock", "_callbacks", "_timer",
                 "_linked_regs", "token")

    def __init__(self, cancel_after: Optional[float] = None) -> None:
        self._cancelled = False
        self._reason: Optional[str] = None
        self._lock = Lock()
        self._callbacks: List[Callable[[], None]] = []
        self._timer: Optional[_threading.Timer] = None
        self._linked_regs: List["CancellationTokenRegistration"] = []
        self.token = CancellationToken(self)
        if cancel_after is not None:
            self.cancel_after(cancel_after)

    @property
    def is_cancellation_requested(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    def cancel(self, reason: Optional[str] = None) -> None:
        # nogil: iptal + callback listesi tekilliği lock ile korunur
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            self._reason = reason
            callbacks, self._callbacks = self._callbacks, []
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        for cb in callbacks:
            try:
                cb()
            except Exception as e:
                _debug_log(f"⚠️ Cancellation callback hatası: {e}")

    def cancel_after(self, seconds: float) -> None:
        """.NET CancelAfter — süre dolunca otomatik iptal (daemon timer)."""
        with self._lock:
            if self._cancelled:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = _threading.Timer(
                seconds, lambda: self.cancel(reason=f"timeout ({seconds}s)"))
            self._timer.daemon = True
            self._timer.start()

    def _register(self, callback: Callable[[], None]) -> "CancellationTokenRegistration":
        fire_now = False
        with self._lock:
            if self._cancelled:
                fire_now = True
            else:
                self._callbacks.append(callback)
        if fire_now:
            callback()
            return CancellationTokenRegistration(None, None)  # sökülecek kayıt yok
        return CancellationTokenRegistration(self, callback)

    def _unregister(self, callback: Callable[[], None]) -> None:
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass  # zaten fire edildi veya sökülmüş — idempotent

    def dispose(self) -> None:
        """
        v6.5: .NET CancellationTokenSource.Dispose muadili.
        Timer'ı durdurur, bekleyen callback'leri ve (linked source ise) üst
        token'lardaki kayıtları söker. İptal ETMEZ.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._callbacks = []
            regs, self._linked_regs = self._linked_regs, []
        for reg in regs:
            reg.dispose()

    def __enter__(self) -> "CancellationTokenSource":
        return self

    def __exit__(self, *exc) -> None:
        self.dispose()

    @staticmethod
    def create_linked(*tokens: CancellationToken) -> "CancellationTokenSource":
        """
        Verilen token'lardan biri iptal olunca iptal olan yeni source.
        v6.5: üst token kayıtları linked source'ta tutulur; linked iptal
        olduğunda veya dispose() edildiğinde üst kaynaklardan sökülür
        (callback sızıntısı yok).
        """
        linked = CancellationTokenSource()

        def _on_parent_cancel():
            linked.cancel(reason="linked token iptal edildi")
            linked.dispose()  # diğer üst kaynaklardaki kayıtları da sök

        regs = [t.register(_on_parent_cancel) for t in tokens]
        with linked._lock:
            if linked._cancelled:
                pass  # zaten iptal — kayıtlar aşağıda hemen sökülür
            else:
                linked._linked_regs.extend(regs)
                regs = []
        for reg in regs:
            reg.dispose()
        return linked


# ---- v6.3: ambient token (contextvar — async task'lere otomatik taşınır) ----

_CURRENT_CT: "_contextvars.ContextVar[CancellationToken]" = \
    _contextvars.ContextVar("mediatr_cancellation_token")


def current_cancellation_token() -> CancellationToken:
    """
    Aktif send/publish çağrısının token'ını döndürür (yoksa
    CancellationToken.none). Handler/behavior içinden erişim:

        def handle(self, req):
            ct = current_cancellation_token()
            for chunk in work:
                ct.throw_if_cancellation_requested()
    """
    return _CURRENT_CT.get(CancellationToken.none)


@lru_cache(maxsize=2048)
def _handle_accepts_ct(handler_cls: type) -> bool:
    """handle() imzasında `cancellation_token` parametresi var mı? (cached)"""
    try:
        return "cancellation_token" in inspect.signature(
            handler_cls.handle).parameters
    except (TypeError, ValueError):
        return False


def _invoke_handle(handler: Any, message: Any) -> Any:
    """
    v6.3 tek çağrı noktası: handler `cancellation_token` parametresi
    bildiriyorsa ambient token enjekte edilir (.NET Handle(request, ct)),
    bildirmiyorsa v6.2 ile birebir aynı çağrı yapılır.
    """
    if _handle_accepts_ct(type(handler)):
        return handler.handle(
            message, cancellation_token=current_cancellation_token())
    return handler.handle(message)
