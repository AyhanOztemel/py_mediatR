# py_mediatR_v6_NoGil_new2.py - Ultra-Optimized CQRS/MediatR v6.6
# (MediatR-inspired / partial semantic parity)
"""
High-performance MediatR/CQRS implementation for Python.

.NET `MediatR` kütüphanesinden esinlenen (MediatR-inspired, kısmi semantik
parite), yüksek performanslı, sıfır-bağımlılıklı (Pydantic opsiyonel) bir
CQRS/Mediator implementasyonudur. Bazı özellikler (.NET çekirdeğinde olmayan
Caching/Retry/Transaction behavior'ları gibi) bu kütüphaneye özgüdür; bazı
API'ler (sync send/publish gibi) bilinçli olarak .NET'ten farklıdır.

================================================================================
v6.6 — DÜZELTMELER (v6.5 sıkı kontrol raporu bulguları)
================================================================================
  • Hash çakışması blokörü ........ Hashlenebilir request'lerde anahtar artık
                                    ham hash() değeri DEĞİL, nesnenin kendisidir
                                    — dict, çakışmada __eq__ ile ayırt eder;
                                    eşit olmayan request'ler aynı cache girdisini
                                    paylaşamaz (sessiz yanlış cevap imkânsız).
  • cache_key() protokolü ......... Request `cache_key()` tanımlarsa anahtar
                                    ondan üretilir — mutable request'lerde
                                    kararlı anahtar için önerilen yol.
  • Ambient CT — bilinçli sapma ... Token'ın behavior/processor/exception
                                    handler/publisher'a doğrudan parametre
                                    yerine current_cancellation_token()
                                    (contextvar) ile taşınması .NET MediatR'dan
                                    BİLİNÇLİ BİR SAPMADIR; parity özelliği
                                    olarak sunulmaz. Contextvar sync/async ve
                                    paralel task izolasyon testleri eklendi.
  • Dokümantasyon ................. Tarihsel sürüm notlarındaki eskimiş
                                    ifadeler mevcut davranışa işaret eden
                                    notlarla ayrıldı.

================================================================================
v6.5 — DÜZELTMELER (v6.4 sıkı kontrol raporu bulguları)
================================================================================
  • Cache anahtarı çakışması ...... repr fallback KALDIRILDI. Anahtarlar
                                    canonical freeze ile üretilir (list→tuple,
                                    dict→sorted items, set→frozenset, nested
                                    dataclass→alan tuple'ı). Güvenli anahtar
                                    üretilemeyen request cache DIŞI bırakılır —
                                    sessiz yanlış cevap imkânsız.
  • CachingBehavior max_size ...... Negatif → ValueError; 0 → cache açıkça
                                    devre dışı (StopIteration hatası giderildi).
  • CT registration yaşam döngüsü . token.register() artık
                                    CancellationTokenRegistration döndürür
                                    (dispose/unregister, context manager).
                                    CancellationTokenSource.dispose() eklendi.
  • Linked source temizliği ....... create_linked kayıtları linked source'ta
                                    tutulur; iptal veya dispose'ta üst
                                    kaynaklardan sökülür (callback sızıntısı yok).
  • override_handler .............. Token/stack tabanlı sahiplik — çakışan ve
                                    LIFO-dışı kapanan override'lar registry'yi
                                    bozmaz; tüm mutasyonlar lock altında.
  • ISender / IPublisher .......... Sözleşmeler Mediator ile uyumlu: generic
                                    TResponse dönüşü + cancellation_token.

================================================================================
v6.4 — DÜZELTMELER (inceleme raporu bulguları)
================================================================================
  • Async pipeline tek loop ....... Sync behavior + async next() artık AYNI
                                    event loop üzerinde çalışır; ayrı thread /
                                    ayrı event loop köprüsü kaldırıldı
                                    ("Future attached to a different loop" yok).
                                    Logging/Performance/Authorization/Tracing
                                    behavior'ları awaitable-aware yapıldı.
  • Notification hata politikası .. swallow_notification_errors default=False —
                                    .NET ForeachAwaitPublisher gibi hatalar
                                    propagate edilir (yutma artık opt-in).
  • ExceptionHandlerState ......... .NET RequestExceptionHandlerState muadili:
                                    handle(req, exc, state) → state.set_handled().
                                    Legacy 2-param handler'da `return None` artık
                                    hatayı YUTMAZ (re-raise edilir).
  • Gerçek tip çıkarımı ........... send/send_async → TResponse,
                                    create_stream → AsyncIterator[TResponse].
  • NoGIL registry okumaları ...... get_registered_* / get_pipeline_info lock +
                                    snapshot; ServiceContainer.register_* kilitli.
  • CachingBehavior ............... Anahtar hashability kontrolü (unhashable
                                    alanlar repr fallback — TARİHSEL NOT:
                                    v6.5'te canonical freeze ile değiştirildi,
                                    v6.6'da hash yerine nesne-eşitliği anahtarı
                                    geldi) + GERÇEK LRU
                                    (hit'te recency tazelenir).
  • Discovery cache JSON .......... pickle kaldırıldı → .mediatr_cache.json
                                    (kod çalıştırma riski yok).
  • Monkey-patch kaldırıldı ....... CT'li send/send_async/create_stream/publish/
                                    publish_async/create_scope artık Mediator
                                    sınıf gövdesinde.

================================================================================
v6.3 — YENİ ÖZELLİKLER (CancellationToken — tam geriye dönük uyumlu, additif)
================================================================================
  • CancellationToken(Source) ..... .NET paritesi: kooperatif iptal —
                                    cancel(), cancel_after(sn), register(cb),
                                    create_linked(), CancellationToken.none.
  • send/send_async/publish/....... Opsiyonel `cancellation_token=` parametresi;
    create_stream                   verilmezse davranış birebir v6.2 (sıfır maliyet).
  • Handler ct enjeksiyonu ........ `handle(self, req, cancellation_token=...)`
                                    imzası bildiren handler'a token otomatik
                                    geçilir (.NET Handle(request, ct)).
  • current_cancellation_token() .. Handler/behavior içinden ambient erişim
                                    (contextvar — async task'lere taşınır).
  • Streaming iptali .............. create_stream her item arasında token kontrolü.
  • OperationCancelledError ....... .NET OperationCanceledException muadili.

================================================================================
v6.2 — YENİ ÖZELLİKLER (Tam DI katmanı — tam geriye dönük uyumlu, additif)
================================================================================
  • ServiceContainer .............. Auto-wiring destekli DI container
                                    (.NET IServiceCollection/IServiceProvider).
                                    Type-hint tabanlı constructor injection,
                                    dairesel bağımlılık tespiti.
  • Scoped lifetime ............... singleton/transient'e ek 3. yaşam döngüsü:
                                    scope (örn. HTTP isteği) başına tek instance
                                    (.NET AddScoped muadili).
  • ServiceScope .................. CreateScope() muadili; sync+async context
                                    manager, dispose/close + adispose/aclose
                                    ters sırada otomatik çağrılır.
  • scoped_mediator / create_scope  Scope'a bağlı Mediator klonu — istek başına
                                    aynı scoped servisleri paylaşan handler'lar.
  • make_fastapi_mediator_dependency FastAPI Depends() köprüsü (fastapi import
                                    edilmeden) — istek başına scope + dispose.
  • ServiceContainer, v6.1 duck-type imzasını (resolve/registrations/
    register_transient) sağlar → `Mediator(handler_factory=container)` çalışır.

================================================================================
v6 — YENİ ÖZELLİKLER (DI + cross-cutting — tam geriye dönük uyumlu)
================================================================================
  • handler_factory / lifetime .... Constructor injection (.NET IServiceProvider
                                    muadili). "singleton" | "transient".
                                    Parametreli ctor'lu handler'lar artık
                                    discovery'de atlanmaz, DEFERRED kaydedilir.
  • Generic IRequest[TResponse] ... send/send_async/create_stream dönüş tipi
                                    çıkarımı (.NET IRequest<TResponse> muadili;
                                    v6.4+ imzalarıyla etkin).
                                    Eski `class X(IRequest)` aynen çalışır.
  • polymorphic_publish ........... (opt-in) Türetilmiş notification publish
                                    edildiğinde base handler'lar da tetiklenir
                                    (.NET covariance davranışı).
  • IValidator + ValidationBehavior FluentValidation muadili — request başına
                                    ayrı validator sınıfları.
  • AuthorizationBehavior ......... `requires_permission` niteliğine bakan
                                    yetkilendirme cross-cutting'i.
  • TracingBehavior ............... OpenTelemetry span (opsiyonel bağımlılık).
  • @handler / @behavior .......... Explicit (decorator) kayıt — kırılgan
                                    auto-discovery'ye alternatif; birlikte de
                                    kullanılabilir.
  • override_handler / reset ...... Test desteği (mock handler, temiz state).
  • custom publisher .............. publish_async'e özel dağıtım stratejisi
                                    callable'ı (.NET INotificationPublisher).
  • RetryBehavior jitter .......... Thundering-herd önlemi.
  • Güvenli event-loop köprüsü .... get_event_loop() kaldırıldı; çalışan loop
                                    varken sync köprü ayrı thread'de kurulur.
  • Cache v6 formatı .............. Discovery cache'e instance değil sınıf yolu
                                    yazılır. v6.4+: format JSON'dur
                                    (.mediatr_cache.json); eski pickle cache
                                    dosyaları OKUNMAZ, discovery yeniden koşar.
  • applies_to genişletildi ....... Tuple (çoklu tip) + applies_to_subclasses.
  • Free-threaded (nogil) uyumu ... Singleton cache lock'lu; add_*/register_*
                                    metodları lock + copy-on-write (rebind);
                                    publish snapshot kopya üzerinde döner.
                                    Temel dispatch/registry/container yolları
                                    3.14t GIL-off stres testinden geçmiştir;
                                    kapsam dışı özel senaryolar için garanti
                                    verilmez.

================================================================================
v5 — YENİ ÖZELLİKLER (.NET MediatR PARİTESİ — tam geriye dönük uyumlu)
================================================================================
  • ISender / IPublisher .......... Interface segregation (.NET ISender/IPublisher)
  • IStreamRequest + create_stream  Streaming (IAsyncEnumerable<T> muadili)
  • IStreamPipelineBehavior ....... Stream pipeline middleware
  • Async pipeline (gerçek) ....... Behavior/pre/post/exc artık async-aware.
                                    send_async tam async zincir derler.
  • Async pre/post/exception ...... process()/handle() coroutine olabilir
  • Notification publish strategy . SEQUENTIAL / PARALLEL_WHENALL / PARALLEL_NOWAIT
  • Notification handler ordering . `order` artık publish'te uygulanır
  • IExceptionAction .............. (.NET IRequestExceptionAction) — yutmadan
                                    side-effect (log/metric) yapan exc gözlemci
  • RequestHandlerDelegate ........ next() artık tipli (callable) — .NET delegate
  • send/publish/create_stream .... ISender/IPublisher metotları net ayrıldı

================================================================================
v4 DAVRANIŞI KORUNDU
================================================================================
  • IPipelineBehavior, IRequestPreProcessor, IRequestPostProcessor
  • IExceptionHandler, INotification + publish()
  • Built-in behaviors (Logging/Performance/Validation/Caching/Retry/Transaction)
  • send_async(), compiled pipeline cache, fast-path, __slots__

================================================================================
v3 DAVRANIŞI KORUNDU
================================================================================
  • Mediator(auto_discover=True), mediator.send(MyRequest(...))
  • IRequest / IResponse base class'ları değişmedi
  • Auto-discovery, cache, parallel, discover_handlers(...) aynı imza
"""

# NOTE: v6.7.0'dan itibaren gercek kod alt modullerdedir. Bu modul yalnizca
# geriye donuk uyumluluk icindir: `py_mediatR.py_mediatR` yolunu ve o yolun
# tum isimlerini (public + private) aynen korur.

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

from ._config import (  # noqa: F401
    DEBUG_MODE,
    SYNC_BRIDGE_TIMEOUT_DEFAULT,
    _UNSET,
    _BACKGROUND_TASKS,
    _PydBaseModel,
    _debug_log,
    _internal_logger,
)
from .contracts import (  # noqa: F401
    TResponse,
    IRequest,
    IResponse,
    IStreamRequest,
    INotification,
    INotificationHandler,
    IPipelineBehavior,
    IStreamPipelineBehavior,
    IRequestPreProcessor,
    IRequestPostProcessor,
    IExceptionHandler,
    IExceptionAction,
    IValidator,
    UnauthorizedError,
    _DeferredHandler,
    _EXPLICIT_HANDLERS,
    _EXPLICIT_BEHAVIORS,
    handler,
    behavior,
    RequestHandlerDelegate,
    StreamHandlerDelegate,
    PublishStrategy,
)
from ._typechecks import (  # noqa: F401
    _is_request_type,
    _is_stream_request_type,
    _is_response_type,
    _is_notification_type,
    _is_async_callable,
    _is_async_gen_callable,
)
from .tracing import (  # noqa: F401
    _FLOW,
    _FLOW_CURSOR,
    FlowNode,
    _KIND_TAG,
    FlowTrace,
    _console_supports_unicode,
    trace_flow,
    _flow_begin,
    _flow_end,
    _flow_note,
)
from .coercion import (  # noqa: F401
    coerce_to_model,
    _maybe_await,
    SyncBridgeTimeoutError,
    _sync_bridge_timeout,
    _sync_run_coro,
)
from .cancellation import (  # noqa: F401
    OperationCancelledError,
    CancellationToken,
    CancellationTokenRegistration,
    CancellationTokenSource,
    _CURRENT_CT,
    current_cancellation_token,
    _handle_accepts_ct,
    _invoke_handle,
)
from .behaviors import (  # noqa: F401
    LoggingBehavior,
    PerformanceBehavior,
    ValidationBehavior,
    CachingBehavior,
    RetryBehavior,
    TransactionCleanupError,
    TransactionBehavior,
    AuthorizationBehavior,
    TracingBehavior,
)
from .discovery import (  # noqa: F401
    _find_project_root,
    _iter_classes_in_module,
    _find_request_param_and_return_type,
    _find_notification_param,
    _infer_response_by_naming,
    _FRAMEWORK_BASE_NAMES,
    _is_framework_internal,
    _try_build_handler_entry,
    _try_build_notification_handler_entry,
    _cls_path,
    _load_cls,
    _instantiate_or_defer,
    _serialize_registries,
    _deserialize_registries,
    _compute_cache_key,
    _SKIP_DIRS,
    _should_skip_file,
    _collect_python_files,
    _process_file,
    _quick_file_list,
    _display_path,
    _run_discovery,
    discover_handlers,
    discover_all,
    discover_all_v4,
)
from .mediator import (  # noqa: F401
    ISender,
    IPublisher,
    IMediator,
    ExceptionHandlerState,
    _exc_handler_accepts_state,
    _invoke_exception_handler,
    container_handler_factory,
    Mediator,
    _as_async_iterator,
)
from .di import (  # noqa: F401
    DIResolutionError,
    _NON_INJECTABLE,
    _is_injectable_type,
    _unwrap_hint,
    _ServiceRegistration,
    _track_disposable,
    ServiceScope,
    ServiceContainer,
    _bind_mediator_to_resolver,
    scoped_mediator,
    make_fastapi_mediator_dependency,
)

# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # v3 exports (tam uyumlu)
    "IRequest", "IResponse", "Mediator", "discover_handlers",
    # v4
    "INotification", "INotificationHandler",
    "IPipelineBehavior", "IRequestPreProcessor", "IRequestPostProcessor",
    "IExceptionHandler",
    "LoggingBehavior", "PerformanceBehavior", "ValidationBehavior",
    "CachingBehavior", "RetryBehavior", "TransactionBehavior",
    "discover_all", "discover_all_v4", "coerce_to_model",
    # v5 — .NET parity
    "IStreamRequest", "IStreamPipelineBehavior",
    "IExceptionAction",
    "ISender", "IPublisher", "IMediator",
    "PublishStrategy",
    "RequestHandlerDelegate", "StreamHandlerDelegate",
    # v6 — DI & cross-cutting
    "TResponse",
    "IValidator", "UnauthorizedError",
    "AuthorizationBehavior", "TracingBehavior",
    "handler", "behavior",
    # v6.1 — IoC köprüsü (additif)
    "container_handler_factory",
    # v6.2 — Tam DI katmanı (additif)
    "ServiceContainer", "ServiceScope", "DIResolutionError",
    "scoped_mediator", "make_fastapi_mediator_dependency",
    # v6.3 — CancellationToken (additif)
    "CancellationToken", "CancellationTokenSource",
    "OperationCancelledError", "current_cancellation_token",
    # v6.4 — düzeltmeler
    "ExceptionHandlerState",
    # v6.5 — düzeltmeler
    "CancellationTokenRegistration",
    # v6.7 — flow trace + louder failures
    "trace_flow", "FlowTrace", "FlowNode",
    "TransactionCleanupError", "SyncBridgeTimeoutError",
]
