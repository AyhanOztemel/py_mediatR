"""py_mediatR._typechecks — internal isinstance/issubclass helpers.

v6.7.0'da tek dosyalik `py_mediatR.py` alt modullere ayrildi.
Kod govdesi birebir aynidir. Genel API icin `import py_mediatR` kullanin;
bu modul bir uygulama detayidir ve dogrudan import edilmesi gerekmez.
"""

import inspect
from typing import (
    Any,
)

from .contracts import INotification, IRequest, IResponse, IStreamRequest

# ============================================================================
# INTERNAL TYPE CHECKS
# ============================================================================

def _is_request_type(tp: Any) -> bool:
    try:
        return inspect.isclass(tp) and issubclass(tp, IRequest)
    except TypeError:
        return False


def _is_stream_request_type(tp: Any) -> bool:
    try:
        return inspect.isclass(tp) and issubclass(tp, IStreamRequest)
    except TypeError:
        return False


def _is_response_type(tp: Any) -> bool:
    try:
        return inspect.isclass(tp) and issubclass(tp, IResponse)
    except TypeError:
        return False


def _is_notification_type(tp: Any) -> bool:
    try:
        return inspect.isclass(tp) and issubclass(tp, INotification)
    except TypeError:
        return False


def _is_async_callable(fn: Any) -> bool:
    """Coroutine fonksiyonu mu? (bound method dahil)"""
    if inspect.iscoroutinefunction(fn):
        return True
    # functools.partial / callable instance __call__
    return callable(fn) and inspect.iscoroutinefunction(fn.__call__)


def _is_async_gen_callable(fn: Any) -> bool:
    """Async generator fonksiyonu mu?"""
    if inspect.isasyncgenfunction(fn):
        return True
    return callable(fn) and inspect.isasyncgenfunction(fn.__call__)
