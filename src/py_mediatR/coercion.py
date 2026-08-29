# -*- coding: utf-8 -*-
"""py_mediatR.coercion — dict->model coercion and the sync-over-async bridge.

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

from ._config import SYNC_BRIDGE_TIMEOUT_DEFAULT, _PydBaseModel, _UNSET


def coerce_to_model(value: Any, target: Type) -> Any:
    """dict → Pydantic/dataclass model dönüşümü. Gerekmiyorsa erken döner."""
    if target is None:
        return value

    # Pydantic
    if _PydBaseModel is not None:
        try:
            if issubclass(target, _PydBaseModel):
                if isinstance(value, target):
                    return value
                if isinstance(value, dict):
                    try:
                        return target.model_validate(value)           # v2
                    except AttributeError:
                        return target.parse_obj(value)                # v1
                if isinstance(value, _PydBaseModel):
                    try:
                        return target.model_validate(value.model_dump())
                    except AttributeError:
                        return target.parse_obj(value.dict())
                return value
        except TypeError:
            pass
        except Exception:
            pass

    # Dataclass
    if is_dataclass(target):
        if isinstance(value, target):
            return value
        if isinstance(value, dict):
            try:
                allow = {f.name for f in fields(target)}
                data = {k: v for k, v in value.items() if k in allow}
                return target(**data)
            except Exception:
                pass

    return value


async def _maybe_await(value: Any) -> Any:
    """Awaitable ise await eder, değilse aynen döner."""
    if inspect.isawaitable(value):
        return await value
    return value


class SyncBridgeTimeoutError(TimeoutError):
    """
    v6.7: The sync-over-async bridge exceeded its time budget.

    Raised when a coroutine that had to be resolved synchronously (because a
    sync entry point was used from inside a running event loop) did not finish
    within ``MEDIATR_SYNC_BRIDGE_TIMEOUT`` seconds. Before v6.7 this situation
    blocked the calling thread — and therefore the whole event loop — forever.
    """


def _sync_bridge_timeout() -> Optional[float]:
    """Time budget for the sync-over-async bridge (0 or negative = no limit)."""
    raw = os.getenv("MEDIATR_SYNC_BRIDGE_TIMEOUT", "").strip()
    if not raw:
        return SYNC_BRIDGE_TIMEOUT_DEFAULT
    try:
        seconds = float(raw)
    except ValueError:
        return SYNC_BRIDGE_TIMEOUT_DEFAULT
    return seconds if seconds > 0 else None


def _sync_run_coro(value: Any, timeout: Optional[float] = _UNSET) -> Any:
    """
    Resolve an awaitable (or a plain value) SYNCHRONOUSLY.

      • No event loop running on this thread -> resolved with asyncio.run.
      • A loop IS running (e.g. a sync entry point was called from async code)
        -> the awaitable is resolved on a SEPARATE thread with its own loop to
        avoid a deadlock, and the result is bridged back.

    v6.7: the bridge thread is joined with a timeout (default 30s, override via
    MEDIATR_SYNC_BRIDGE_TIMEOUT; <=0 disables the limit). Without it a hung
    coroutine froze the calling thread indefinitely — under ASGI that means the
    entire server stops responding.

    NOTE: the bridge runs the coroutine on a DIFFERENT event loop. Objects that
    are bound to their creating loop (asyncpg connections, SQLAlchemy
    AsyncSession, asyncio primitives) will refuse to run there. Callers must
    surface that error rather than swallow it.
    """
    if not inspect.isawaitable(value):
        return value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop on this thread — safe path
        return asyncio.run(_maybe_await(value))

    # A loop is running -> resolve on a separate thread (bridge)
    box: Dict[str, Any] = {}

    def _runner():
        try:
            box["value"] = asyncio.run(_maybe_await(value))
        except BaseException as e:  # noqa: BLE001 — carried across the bridge
            box["error"] = e

    budget = _sync_bridge_timeout() if timeout is _UNSET else timeout
    t = Thread(target=_runner, daemon=True,
               name="mediatr-sync-bridge")
    t.start()
    t.join(budget)
    if t.is_alive():
        raise SyncBridgeTimeoutError(
            f"Coroutine did not complete within {budget}s on the "
            f"sync-over-async bridge. A synchronous entry point was called "
            f"from inside a running event loop; use the async API "
            f"(send_async/publish_async) instead, or raise the budget via "
            f"MEDIATR_SYNC_BRIDGE_TIMEOUT.")
    if "error" in box:
        raise box["error"]
    return box.get("value")
