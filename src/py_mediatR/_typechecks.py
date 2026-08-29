# -*- coding: utf-8 -*-
"""py_mediatR._typechecks — internal isinstance/issubclass helpers.

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
    call = getattr(fn, "__call__", None)
    return bool(call and inspect.iscoroutinefunction(call))


def _is_async_gen_callable(fn: Any) -> bool:
    """Async generator fonksiyonu mu?"""
    if inspect.isasyncgenfunction(fn):
        return True
    call = getattr(fn, "__call__", None)
    return bool(call and inspect.isasyncgenfunction(call))
