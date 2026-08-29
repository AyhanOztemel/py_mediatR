# -*- coding: utf-8 -*-
"""py_mediatR.discovery — handler auto-discovery and project scanning.

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
from .contracts import (  # noqa: F401
    INotification,
    IRequest,
    IResponse,
    IStreamRequest,
    _DeferredHandler,
)
from ._typechecks import (  # noqa: F401
    _is_notification_type,
    _is_request_type,
    _is_response_type,
    _is_stream_request_type,
)


# ============================================================================
# PROJECT ROOT DETECTION (CACHED) — v3 ile aynı
# ============================================================================

@lru_cache(maxsize=1)
def _find_project_root() -> Path:
    main_mod = sys.modules.get("__main__")
    if main_mod and hasattr(main_mod, "__file__") and main_mod.__file__:
        current = Path(main_mod.__file__).resolve().parent
    else:
        current = Path.cwd()

    _debug_log(f"🔍 Starting directory search from: {current}")

    markers = [
        'requirements.txt', 'setup.py', 'pyproject.toml', 'Pipfile',
        '.git', 'main.py', 'app.py', 'manage.py',
    ]

    checked_dirs = []
    for parent in [current] + list(current.parents):
        checked_dirs.append(str(parent))
        for marker in markers:
            if (parent / marker).exists():
                _debug_log(f"✅ Project root found: {parent} (marker: {marker})")
                return parent
        if parent == Path.home() or parent == parent.parent:
            break

    _debug_log(f"⚠️ No markers found, using starting directory: {current}")
    return current


# ============================================================================
# OPTIMIZED DISCOVERY
# ============================================================================

def _iter_classes_in_module(module) -> Iterable[type]:
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if getattr(obj, "__module__", None) == module.__name__:
            yield obj


@lru_cache(maxsize=2048)
def _find_request_param_and_return_type(cls_name: str, module_name: str) -> Tuple:
    """Cached type hint çıkarımı (request handler için)."""
    if module_name not in sys.modules:
        return (None, None)

    module = sys.modules[module_name]
    cls = getattr(module, cls_name, None)

    if not cls or not hasattr(cls, "handle"):
        return (None, None)

    try:
        sig = inspect.signature(cls.handle)
        params = list(sig.parameters.values())

        if len(params) < 2:
            return (None, None)

        module_dict = module.__dict__
        hints = get_type_hints(cls.handle, globalns=module_dict, localns=None)

        request_param_type = None
        request_param_count = 0

        for p in params[1:]:
            if p.name in hints:
                tp = hints[p.name]
                if _is_request_type(tp):
                    request_param_count += 1
                    if request_param_type is None:
                        request_param_type = tp

        if request_param_count == 0:
            return (None, None)
        if request_param_count > 1:
            raise TypeError(f"{cls_name}.handle has multiple IRequest parameters.")

        return_type = hints.get("return", None)
        return (request_param_type, return_type)

    except Exception as e:
        _debug_log(f"Type hint extraction failed for {cls_name}: {e}")
        return (None, None)


@lru_cache(maxsize=2048)
def _find_notification_param(cls_name: str, module_name: str) -> Optional[Type]:
    """Cached type hint çıkarımı (notification handler için)."""
    if module_name not in sys.modules:
        return None

    module = sys.modules[module_name]
    cls = getattr(module, cls_name, None)

    if not cls or not hasattr(cls, "handle"):
        return None

    try:
        sig = inspect.signature(cls.handle)
        params = list(sig.parameters.values())
        if len(params) < 2:
            return None

        hints = get_type_hints(cls.handle, globalns=module.__dict__, localns=None)
        for p in params[1:]:
            if p.name in hints:
                tp = hints[p.name]
                if _is_notification_type(tp):
                    return tp
        return None
    except Exception as e:
        _debug_log(f"Notification type hint extraction failed for {cls_name}: {e}")
        return None


def _infer_response_by_naming(req_type: Type[IRequest],
                              module_dict: dict) -> Optional[Type[IResponse]]:
    req_name = getattr(req_type, "__name__", "")
    cand = req_name[:-7] + "Response" if req_name.endswith("Request") else req_name + "Response"
    resp_cls = module_dict.get(cand) if module_dict else None
    if resp_cls and _is_response_type(resp_cls):
        return resp_cls
    return None


# py_mediatR'ın KENDİ abstraction/base sınıfları — bunlar handler DEĞİLDİR ve
# discovery sırasında (örn. py_mediatR.py proje içindeyse) yakalanmamalıdır.
_FRAMEWORK_BASE_NAMES = frozenset({
    "IRequest", "IResponse", "IStreamRequest",
    "INotification", "INotificationHandler",
    "IPipelineBehavior", "IStreamPipelineBehavior",
    "IRequestPreProcessor", "IRequestPostProcessor",
    "IExceptionHandler", "IExceptionAction",
    "ISender", "IPublisher", "IMediator",
    # Built-in behaviors da handle() taşır ama request handler değildir
    "LoggingBehavior", "PerformanceBehavior", "ValidationBehavior",
    "CachingBehavior", "RetryBehavior", "TransactionBehavior",
    # v6 eklemeleri
    "IValidator", "AuthorizationBehavior", "TracingBehavior",
    "UnauthorizedError", "_DeferredHandler",
})


def _is_framework_internal(cls: type) -> bool:
    """Sınıf py_mediatR'ın kendi iç bileşeni mi? (modül adı + sınıf adı ile)."""
    mod = getattr(cls, "__module__", "") or ""
    # Bu dosyanın modül adı ne olursa olsun (py_mediatR / paket.py_mediatR) yakala
    if mod == __name__ or mod.endswith(".py_mediatR") or mod == "py_mediatR":
        return True
    # v6.7: kütüphane alt modüllere ayrıldı — kökü paket adı olan her şey içseldir.
    if mod.split(".", 1)[0] in ("py_mediatR", "py_mediatr"):
        return True
    if cls.__name__ in _FRAMEWORK_BASE_NAMES:
        return True
    return False


def _try_build_handler_entry(cls: type):
    """
    Request handler yakalama (IRequest parametresi olan handle metodu).
    Döner: (req_type, handler_instance, resp_type, is_stream)
    """
    if not inspect.isclass(cls):
        return (None, None, None, False)

    # Framework'ün kendi base sınıflarını handler sanma
    if _is_framework_internal(cls):
        return (None, None, None, False)

    req_type, return_type = _find_request_param_and_return_type(cls.__name__, cls.__module__)
    if req_type is None:
        return (None, None, None, False)

    is_stream = _is_stream_request_type(req_type)

    module = sys.modules.get(cls.__module__)
    module_dict = module.__dict__ if module else None

    resp_type = None
    if not is_stream:
        if return_type is not None and _is_response_type(return_type):
            resp_type = return_type
        if resp_type is None:
            resp_type = _infer_response_by_naming(req_type, module_dict)

    try:
        handler_instance = cls()
        return (req_type, handler_instance, resp_type, is_stream)
    except TypeError as e:
        # v6: Parametreli ctor (DI bekleyen handler) — atlamak yerine DEFERRED
        # kaydet. Mediator'a handler_factory verilirse çözülür; verilmezse
        # Mediator init'te v5 davranışıyla (debug log) elenir.
        _debug_log(f"⏳ Deferred (ctor needs DI): {cls.__name__} - {e}")
        return (req_type, _DeferredHandler(cls), resp_type, is_stream)


def _try_build_notification_handler_entry(cls: type):
    """Notification handler yakalama (INotification parametresi olan handle metodu)."""
    if not inspect.isclass(cls):
        return (None, None)

    if _is_framework_internal(cls):
        return (None, None)

    notif_type = _find_notification_param(cls.__name__, cls.__module__)
    if notif_type is None:
        return (None, None)

    try:
        return (notif_type, cls())
    except TypeError as e:
        _debug_log(f"⏳ Deferred notif (ctor needs DI): {cls.__name__} - {e}")
        return (notif_type, _DeferredHandler(cls))


# ----- v6 cache serileştirme: instance DEĞİL sınıf yolu yazılır -----
# Eski format handler instance'larını pickle'lıyordu; bu hem kırılgandı
# (unpicklable handler → cache patlar) hem de pickle deserialization güvenlik
# riski taşıyordu. v6 formatı yalnızca "modul:QualName" string'leri saklar,
# yüklerken sınıflar import edilip yeniden instantiate edilir.

def _cls_path(obj: Any) -> str:
    cls = obj.cls if isinstance(obj, _DeferredHandler) else (
        obj if inspect.isclass(obj) else type(obj))
    return f"{cls.__module__}:{cls.__qualname__}"


def _load_cls(path: str) -> Optional[type]:
    try:
        mod_name, _, qualname = path.partition(":")
        module = importlib.import_module(mod_name)
        obj: Any = module
        for part in qualname.split("."):
            obj = getattr(obj, part)
        return obj if inspect.isclass(obj) else None
    except Exception as e:
        _debug_log(f"⚠️ Cache class load failed for '{path}': {e}")
        return None


def _instantiate_or_defer(cls: type) -> Any:
    """cls() dene; parametreli ctor ise deferred sarmala (DI ile çözülür)."""
    try:
        return cls()
    except TypeError:
        return _DeferredHandler(cls)


def _serialize_registries(request_registry: dict, stream_registry: dict,
                          notification_registry: dict) -> tuple:
    req = {_cls_path(rt): (_cls_path(h), _cls_path(resp) if resp else None)
           for rt, (h, resp) in request_registry.items()}
    stream = {_cls_path(rt): _cls_path(h) for rt, h in stream_registry.items()}
    notif = {_cls_path(nt): [_cls_path(h) for h in hs]
             for nt, hs in notification_registry.items()}
    return req, stream, notif


def _deserialize_registries(req: dict, stream: dict, notif: dict) -> tuple:
    request_registry: dict = {}
    stream_registry: dict = {}
    notification_registry: dict = {}

    for rt_path, (h_path, resp_path) in req.items():
        rt, h_cls = _load_cls(rt_path), _load_cls(h_path)
        if rt is None or h_cls is None:
            raise ValueError(f"Cache entry unresolvable: {rt_path}")
        resp = _load_cls(resp_path) if resp_path else None
        request_registry[rt] = (_instantiate_or_defer(h_cls), resp)

    for rt_path, h_path in stream.items():
        rt, h_cls = _load_cls(rt_path), _load_cls(h_path)
        if rt is None or h_cls is None:
            raise ValueError(f"Cache entry unresolvable: {rt_path}")
        stream_registry[rt] = _instantiate_or_defer(h_cls)

    for nt_path, h_paths in notif.items():
        nt = _load_cls(nt_path)
        if nt is None:
            raise ValueError(f"Cache entry unresolvable: {nt_path}")
        handlers = []
        for hp in h_paths:
            h_cls = _load_cls(hp)
            if h_cls is None:
                raise ValueError(f"Cache entry unresolvable: {hp}")
            handlers.append(_instantiate_or_defer(h_cls))
        notification_registry[nt] = handlers

    return request_registry, stream_registry, notification_registry


def _compute_cache_key(files: list) -> str:
    parts = []
    for f in files:
        try:
            st = f.stat()
            parts.append(f"{f.as_posix()}|{st.st_mtime_ns}|{st.st_size}")
        except Exception:
            pass
    return hashlib.md5('||'.join(sorted(parts)).encode()).hexdigest() if parts else ""


# v6.7: directories auto-discovery must never import. Previously only venv /
# .venv / site-packages / __pycache__ were skipped, so a full-project scan
# imported build artifacts, vendored JS tooling and Django migrations — each
# import runs that module's top-level code.
_SKIP_DIRS = frozenset({
    "venv", ".venv", "env", "site-packages", "__pycache__",
    ".git", ".hg", ".svn", "node_modules",
    ".tox", ".nox", ".eggs", "build", "dist",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ipynb_checkpoints",
    "migrations",
})


def _should_skip_file(file: Path) -> bool:
    try:
        return (
            file.name == "__init__.py"
            or file.name.startswith("_")
            or any(part in _SKIP_DIRS or part.endswith(".egg-info")
                   for part in file.parts)
        )
    except Exception:
        return False


def _collect_python_files(project_root: Path, scan_paths: Optional[list] = None) -> list:
    seen: set = set()
    files: list = []
    priority_dirs = ("handlers", "requests_responses", "domain", "application", "cqrs",
                     "notifications", "events")

    def _add_file(f: Path):
        if _should_skip_file(f):
            return
        try:
            key = str(f.resolve())
        except Exception:
            key = str(f)
        if key not in seen:
            seen.add(key)
            files.append(f)

    if scan_paths:
        for sp in scan_paths:
            target_path = project_root / sp
            if target_path.is_file() and target_path.suffix == ".py":
                _add_file(target_path)
            elif target_path.exists():
                for f in sorted(target_path.rglob("*.py")):
                    _add_file(f)
        return files

    all_files = [f for f in project_root.rglob("*.py") if not _should_skip_file(f)]

    priority_files: list = []
    other_files: list = []
    for f in all_files:
        try:
            relative_parts = f.relative_to(project_root).parts
        except Exception:
            relative_parts = f.parts

        if any(part in priority_dirs for part in relative_parts):
            priority_files.append(f)
        else:
            other_files.append(f)

    for f in sorted(priority_files):
        _add_file(f)
    for f in sorted(other_files):
        _add_file(f)

    return files


def _process_file(file: Path, project_root: Path,
                  request_registry: dict,
                  stream_registry: dict,
                  notification_registry: dict,
                  lock: Optional[Any] = None) -> Tuple[bool, Optional[str]]:
    """
    Dosyayı import et, içindeki request / stream / notification handler'larını kaydet.
    Thread-safe: lock verilirse kullanır.
    """
    try:
        relative_path = file.relative_to(project_root)
        module_name = str(relative_path.with_suffix('')).replace(os.sep, '.')
        _debug_log(f"   📄 {module_name}")

        try:
            if module_name in sys.modules:
                module = sys.modules[module_name]
            else:
                module = importlib.import_module(module_name)
        except ImportError as e:
            error_msg = f"ImportError: {e}"
            _debug_log(f"      ❌ {error_msg}")
            return (False, error_msg)
        except Exception as e:
            error_msg = f"Import failed: {type(e).__name__}: {e}"
            _debug_log(f"      ❌ {error_msg}")
            return (False, error_msg)

        for cls in _iter_classes_in_module(module):
            # 1) Request / Stream handler?
            req_t, inst, resp_t, is_stream = _try_build_handler_entry(cls)
            if req_t:
                if is_stream:
                    if lock is not None:
                        with lock:
                            if req_t not in stream_registry:
                                stream_registry[req_t] = inst
                                _debug_log(f"      🌊 Stream Handler: {cls.__name__} → {req_t.__name__}")
                    else:
                        if req_t not in stream_registry:
                            stream_registry[req_t] = inst
                            _debug_log(f"      🌊 Stream Handler: {cls.__name__} → {req_t.__name__}")
                else:
                    if lock is not None:
                        with lock:
                            if req_t not in request_registry:
                                request_registry[req_t] = (inst, resp_t)
                                _debug_log(f"      ✅ Handler: {cls.__name__} → {req_t.__name__}")
                    else:
                        if req_t not in request_registry:
                            request_registry[req_t] = (inst, resp_t)
                            _debug_log(f"      ✅ Handler: {cls.__name__} → {req_t.__name__}")
                continue  # Bir sınıf aynı anda hem request hem notification handler olamaz

            # 2) Notification handler?
            notif_t, notif_inst = _try_build_notification_handler_entry(cls)
            if notif_t:
                if lock is not None:
                    with lock:
                        notification_registry.setdefault(notif_t, []).append(notif_inst)
                        _debug_log(f"      🔔 Notif Handler: {cls.__name__} → {notif_t.__name__}")
                else:
                    notification_registry.setdefault(notif_t, []).append(notif_inst)
                    _debug_log(f"      🔔 Notif Handler: {cls.__name__} → {notif_t.__name__}")

        return (True, None)

    except ValueError:
        error_msg = "File outside project root"
        _debug_log(f"   ⚠️  {error_msg}: {file}")
        return (False, error_msg)
    except Exception as e:
        error_msg = f"Unexpected error: {type(e).__name__}: {e}"
        _debug_log(f"   ❌ {error_msg}")
        return (False, error_msg)


def _quick_file_list(project_root: Path,
                     scan_paths: Optional[list] = None) -> list:
    return _collect_python_files(project_root, scan_paths)


def _display_path(file, root) -> str:
    """Path for humans. An explicit scan_path may sit outside project_root, in
    which case relative_to() raises — the absolute path is fine there."""
    try:
        return str(file.relative_to(root))
    except ValueError:
        return str(file)


def _run_discovery(
    from_main: bool = True,
    project_root=None,
    scan_paths: Optional[list] = None,
    parallel: bool = False,
    use_cache: bool = False,
) -> Tuple[Dict[Type[IRequest], Tuple[Any, Optional[Type[IResponse]]]],
           Dict[Type[IStreamRequest], Any],
           Dict[Type[INotification], List[Any]]]:
    """
    İç tarayıcı: request + stream + notification handler'ları tek geçişte bulur.
    """
    request_registry: Dict[Type[IRequest], Tuple[Any, Optional[Type[IResponse]]]] = {}
    stream_registry: Dict[Type[IStreamRequest], Any] = {}
    notification_registry: Dict[Type[INotification], List[Any]] = {}
    failed_imports: list = []

    if project_root is None:
        project_root_path = _find_project_root()
    else:
        project_root_path = Path(project_root).resolve()

    project_root_str = str(project_root_path)
    _debug_log("=" * 60)
    _debug_log(f"📁 Project root: {project_root_str}")
    _debug_log("=" * 60)

    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
        _debug_log(f"✅ Added to sys.path[0]: {project_root_str}")

    # v6.4: pickle kaldırıldı — cache artık veri-odaklı JSON (kod çalıştırma
    # riski yok). Eski .mediatr_cache.pkl dosyaları yok sayılır.
    cache_file = project_root_path / '.mediatr_cache.json'

    if use_cache:
        try:
            if cache_file.exists():
                start_time = time.time()
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                if cached_data.get("format") != "v6.4":
                    raise ValueError("Unknown cache format")
                cached_key = cached_data["key"]
                cached_file_strs = cached_data["files"]
                cached_req, cached_stream, cached_notif = _deserialize_registries(
                    {k: tuple(v) for k, v in cached_data["req"].items()},
                    cached_data["stream"],
                    cached_data["notif"])

                cached_paths = [Path(p) for p in cached_file_strs]
                current_key = _compute_cache_key(cached_paths)
                quick_scan = _quick_file_list(project_root_path, scan_paths)

                if cached_key == current_key and set(cached_file_strs) == {str(f) for f in quick_scan}:
                    _debug_log(f"✅ Cache loaded in {time.time() - start_time:.4f}s")
                    return cached_req, cached_stream, cached_notif
                else:
                    _debug_log("⚠️ Cache invalid, re-discovering...")
        except Exception as e:
            _debug_log(f"⚠️ Cache load failed: {e}, proceeding with discovery")

    scanned_files = _collect_python_files(project_root_path, scan_paths)

    if scan_paths:
        _debug_log(f"📂 Scanning explicit paths: {scan_paths}")
    else:
        _debug_log("📂 Scanning full project (priority directories first)")

    lock = Lock()
    if parallel and len(scanned_files) > 1:
        _debug_log("🚀 Using parallel processing...")
        with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as executor:
            futures = {
                executor.submit(_process_file, f, project_root_path,
                                request_registry, stream_registry,
                                notification_registry, lock): f
                for f in scanned_files
            }
            for future in as_completed(futures):
                success, error = future.result()
                if not success and error:
                    failed_imports.append(
                        (_display_path(futures[future], project_root_path), error))
    else:
        for file in scanned_files:
            success, error = _process_file(
                file, project_root_path, request_registry,
                stream_registry, notification_registry, None
            )
            if not success and error:
                failed_imports.append((_display_path(file, project_root_path), error))

    # __main__ modülü
    if from_main:
        app_main_module = sys.modules.get("__main__")
        if app_main_module and hasattr(app_main_module, "__file__"):
            _debug_log("📄 Scanning __main__ module...")
            for cls in _iter_classes_in_module(app_main_module):
                req_t, inst, resp_t, is_stream = _try_build_handler_entry(cls)
                if req_t:
                    if is_stream:
                        if req_t not in stream_registry:
                            stream_registry[req_t] = inst
                            _debug_log(f"🌊 Stream Handler in __main__: {cls.__name__}")
                    elif req_t not in request_registry:
                        request_registry[req_t] = (inst, resp_t)
                        _debug_log(f"✅ Handler in __main__: {cls.__name__}")
                    continue

                notif_t, notif_inst = _try_build_notification_handler_entry(cls)
                if notif_t:
                    notification_registry.setdefault(notif_t, []).append(notif_inst)
                    _debug_log(f"🔔 Notif Handler in __main__: {cls.__name__}")

    # Cache kaydet (v6.4 JSON formatı — sınıf yolları; bkz. _serialize_registries)
    if use_cache and (request_registry or stream_registry or notification_registry):
        try:
            current_key = _compute_cache_key(scanned_files)
            file_strs = [str(f) for f in scanned_files]
            req_p, stream_p, notif_p = _serialize_registries(
                request_registry, stream_registry, notification_registry)
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({"format": "v6.4", "req": req_p, "stream": stream_p,
                           "notif": notif_p, "key": current_key,
                           "files": file_strs}, f)
            _debug_log(f"💾 Cache saved (v6.4 JSON format)")
        except Exception as e:
            _debug_log(f"⚠️ Cache save failed: {e}")

    _debug_log("=" * 60)
    _debug_log(f"🎯 Request handlers: {len(request_registry)}")
    _debug_log(f"🌊 Stream handlers: {len(stream_registry)}")
    _debug_log(f"🔔 Notification bindings: "
               f"{sum(len(v) for v in notification_registry.values())} "
               f"({len(notification_registry)} event types)")

    if failed_imports:
        # v6.7: these used to be visible only with MEDIATR_DEBUG=1. A handler
        # file that fails to import is silently absent from the registry, and
        # the user later sees an unrelated "no handler registered" error. Make
        # the real cause visible by default; silence with
        # MEDIATR_DISCOVERY_WARNINGS=0.
        _debug_log(f"⚠️ Failed imports ({len(failed_imports)}):")
        for file_path, error in failed_imports[:3]:
            _debug_log(f"   ❌ {file_path}: {error}")
        if os.getenv("MEDIATR_DISCOVERY_WARNINGS", "1").lower() not in (
                "0", "false", "no"):
            log = logging.getLogger("mediatr.discovery")
            shown = "; ".join(f"{p} ({e})" for p, e in failed_imports[:3])
            more = (f" (+{len(failed_imports) - 3} more)"
                    if len(failed_imports) > 3 else "")
            log.warning(
                "Auto-discovery could not import %d file(s); any handlers they "
                "define are NOT registered: %s%s. Set MEDIATR_DEBUG=1 for the "
                "full list, restrict the scan with "
                "Mediator(scan_paths=[...]), or silence this with "
                "MEDIATR_DISCOVERY_WARNINGS=0.",
                len(failed_imports), shown, more)

    _debug_log("=" * 60)
    return request_registry, stream_registry, notification_registry


def discover_handlers(
    from_main: bool = True,
    project_root=None,
    scan_paths: Optional[list] = None,
    parallel: bool = False,
    use_cache: bool = False,
) -> Dict[Type[IRequest], Tuple[Any, Optional[Type[IResponse]]]]:
    """
    v3 geriye dönük uyumlu wrapper — yalnızca request handler sözlüğünü döndürür.
    Stream/notification handler'larını da istiyorsan `discover_all()` kullan.
    """
    req_reg, _stream_reg, _notif_reg = _run_discovery(
        from_main=from_main, project_root=project_root,
        scan_paths=scan_paths, parallel=parallel, use_cache=use_cache,
    )
    return req_reg


def discover_all(
    from_main: bool = True,
    project_root=None,
    scan_paths: Optional[list] = None,
    parallel: bool = False,
    use_cache: bool = False,
) -> Tuple[Dict[Type[IRequest], Tuple[Any, Optional[Type[IResponse]]]],
           Dict[Type[IStreamRequest], Any],
           Dict[Type[INotification], List[Any]]]:
    """
    Request + Stream + Notification handler sözlüklerini döndürür.

    v5 NOT: Dönüş artık 3'lü tuple (req, stream, notif).
    v4 ile uyum için `discover_all_v4()` ikili tuple (req, notif) döndürür.
    """
    return _run_discovery(
        from_main=from_main, project_root=project_root,
        scan_paths=scan_paths, parallel=parallel, use_cache=use_cache,
    )


def discover_all_v4(
    from_main: bool = True,
    project_root=None,
    scan_paths: Optional[list] = None,
    parallel: bool = False,
    use_cache: bool = False,
) -> Tuple[Dict[Type[IRequest], Tuple[Any, Optional[Type[IResponse]]]],
           Dict[Type[INotification], List[Any]]]:
    """v4 geriye dönük uyumlu: (request_registry, notification_registry)."""
    req, _stream, notif = _run_discovery(
        from_main=from_main, project_root=project_root,
        scan_paths=scan_paths, parallel=parallel, use_cache=use_cache,
    )
    return req, notif
