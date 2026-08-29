# py_mediatR

> Türkçe sürüm: [README.tr.md](https://github.com/AyhanOztemel/py_mediatR/blob/main/README.tr.md)

High-performance **CQRS / Mediator** implementation for Python, inspired by
[.NET MediatR](https://github.com/jbogard/MediatR) (partial semantic parity,
plus extras like built-in caching/retry/transaction behaviors) — zero
dependencies, free-threaded (no-GIL) ready.

**v6.7:** call-chain tracing (`mediator.trace()`), no more silent failures in
transaction cleanup or the sync-over-async bridge, captive dependencies
rejected, `Optional[T]` autowiring, `py.typed` shipped. See
[What's new in 6.7](#whats-new-in-67).

**v6.4:** single-event-loop async pipeline, notification errors propagate by
default, explicit `ExceptionHandlerState`, typed `send() -> TResponse`,
JSON discovery cache (no pickle), real LRU caching, thread-safe registries.

## What's new in 6.7

Nothing was removed and no signature became stricter — 6.7 is a drop-in
replacement for 6.6.

### See the call chain

A mediator hides who calls what, which makes "why didn't my handler run?" hard
to answer. `trace()` records one dispatch as a tree:

```python
with mediator.trace() as flow:
    mediator.send(SearchProducts(keyword="laptop"))
    mediator.send(SearchProducts(keyword="laptop"))   # served from cache
print(flow.render())
```

```
send(SearchProducts)   (0.35 ms)
└─ behavior: LoggingBehavior   (0.29 ms)
   └─ behavior: ValidationBehavior   (0.22 ms)
      └─ behavior: CachingBehavior   [cache miss]   (0.20 ms)
         ├─ pre: AuditPreProcessor
         ├─ HANDLER: SearchProductsHandler   (0.07 ms)
         └─ post: MetricsPostProcessor
send(SearchProducts)   (0.14 ms)
└─ behavior: LoggingBehavior   (0.11 ms)
   └─ behavior: ValidationBehavior   (0.06 ms)
      └─ behavior: CachingBehavior   [CACHE HIT - handler NOT called]
```

Retries show up as repeated handler nodes, and an exception is spelled out at
the node that raised it while ancestors are marked `!! (propagated)`. Tracing is
off by default and costs one `ContextVar` lookup per step when inactive.
`flow.steps()` and `flow.find(label)` expose the same data for assertions;
`render(unicode=False)` forces ASCII glyphs.

### Failures are no longer silent

- `TransactionBehavior` no longer swallows a failing `rollback()`/`close()`.
  The cleanup error is logged and attached to the original exception via
  `add_note()`, or raised as `TransactionCleanupError` with
  `raise_on_cleanup_failure=True`.
- Dispatching an async session through the synchronous `send()` from inside a
  running loop is now refused up front with a `TypeError` instead of rolling
  back on the wrong event loop.
- The sync-over-async bridge joins with a 30s budget
  (`MEDIATR_SYNC_BRIDGE_TIMEOUT`, `<=0` disables) and raises
  `SyncBridgeTimeoutError` rather than freezing the calling thread forever.
- Fire-and-forget notification tasks are kept strongly referenced, so
  `PARALLEL_NOWAIT` subscribers can no longer be garbage-collected mid-flight.
- Auto-discovery import errors are logged as warnings on the
  `mediatr.discovery` logger (silence with `MEDIATR_DISCOVERY_WARNINGS=0`).

### DI correctness

- A singleton consuming a scoped service is now rejected with
  `DIResolutionError` — .NET's captive-dependency rule.
- `Optional[T]`, `T | None` and `Annotated[...]` constructor hints are
  unwrapped; an optional dependency that cannot be built is injected as `None`.

### Packaging

- `py.typed` ships, so type checkers use the inline annotations.
- `import py_mediatr` (all lowercase) also works, for case-sensitive
  filesystems where the normalised distribution name misleads.
- The implementation was split from one 4 600-line module into a package of
  focused modules. Nothing you import changes — see
  [Package layout](#package-layout).

## Features

- **Request/Response** — `IRequest[TResponse]` generics, sync `send()` and async `send_async()`
- **Notifications (pub/sub)** — multiple handlers, ordering, SEQUENTIAL / PARALLEL_WHENALL / PARALLEL_NOWAIT strategies, custom publisher
- **Pipeline behaviors** — `IPipelineBehavior` middleware + 8 built-ins (Logging, Performance, Validation, Caching, Retry, Transaction, Authorization, Tracing)
- **Streaming** — `IStreamRequest` + `create_stream()` (`IAsyncEnumerable<T>` equivalent)
- **Pre/Post processors, exception handlers & actions**
- **Dependency Injection** — `ServiceContainer` with **singleton / scoped / transient** lifetimes, type-hint based constructor auto-wiring, `ServiceScope` (sync+async), request-scoped mediator, FastAPI bridge
- **CancellationToken** — `CancellationTokenSource` with `cancel_after`, linked tokens, handler injection (`handle(self, req, cancellation_token=...)`), streaming cancellation
- **Auto-discovery** — handlers found by scanning the project (with cache), or explicit `@handler` / `@behavior` decorators
- **Call-chain tracing** — `mediator.trace()` renders behaviors, handler and subscribers as a tree
- **Free-threaded ready** — safe under Python 3.13t/3.14t (no-GIL)

## Install

```bash
pip install py-mediatR            # core (zero dependencies)
pip install "py-mediatR[pydantic]"  # optional pydantic model coercion
```

## Quick start

```python
from py_mediatR import Mediator, IRequest

class Ping(IRequest):
    pass

class PingHandler:
    def handle(self, req: Ping) -> str:
        return "pong"

mediator = Mediator(auto_discover=False)
mediator.register_handler(Ping, PingHandler())
print(mediator.send(Ping()))  # pong
```

### DI + scoped lifetime (per-request DB session)

```python
from py_mediatR import Mediator, ServiceContainer, scoped_mediator

container = ServiceContainer()
container.register_singleton(Config)
container.register_scoped(DbSession)        # one per scope/request
container.register_transient(UserRepository)

mediator = Mediator(handler_factory=container)
with scoped_mediator(mediator, container) as m:
    m.send(CreateUser(name="Ada"))          # handlers share the scoped session
# scope disposed -> session.close()
```

### CancellationToken

```python
from py_mediatR import CancellationTokenSource, OperationCancelledError

cts = CancellationTokenSource(cancel_after=2.0)   # timeout
try:
    result = await mediator.send_async(SlowQuery(), cancellation_token=cts.token)
except OperationCancelledError:
    ...
```

### FastAPI

```python
from py_mediatR import make_fastapi_mediator_dependency
get_mediator = make_fastapi_mediator_dependency(mediator, container)

@app.post("/orders")
async def create_order(cmd: CreateOrderDto, m=Depends(get_mediator)):
    return await m.send_async(CreateOrder(**cmd.model_dump()))
```

## How a handler is found

There is **no naming convention**. A class becomes a handler because of the
*type annotation* on its `handle()` parameter — the class name is irrelevant:

```python
class GetUser(IRequest):
    user_id: str

class ThisNameDoesNotMatter:          # not "GetUserHandler" — still found
    def handle(self, req: GetUser):   # <- the annotation is the contract
        return {"user_id": req.user_id}
```

The rules, exactly:

| Kind | Detected by |
|---|---|
| Request handler | `handle()` has a parameter annotated with an `IRequest` subclass |
| Stream handler | same, but the annotation is an `IStreamRequest` subclass |
| Notification handler | `handle()` has a parameter annotated with an `INotification` subclass |

Details that bite:

- The annotation must be **resolvable** at import time. A wrong or unimportable
  forward reference makes the class invisible rather than raising.
- Exactly **one** `IRequest` parameter is allowed; two raises `TypeError`.
- Extra parameters are fine — `cancellation_token` is injected by name.
- A handler whose `__init__` takes arguments is registered *deferred* and
  resolved through `handler_factory` (your `ServiceContainer`) at dispatch time.
- One handler per request type; notifications may have many.

### Response coercion — the one place naming matters

If a handler returns a `dict`, py_mediatR coerces it into a response class
found **by name in the same module**: `GetUser` → `GetUserResponse`
(or `GetUserRequest` → `GetUserResponse`). An explicit `-> GetUserResponse`
return annotation takes priority. With neither, the `dict` is returned as-is.

### Discovery scope

`Mediator()` scans `project_root` recursively for `*.py`, skipping `venv`,
`.venv`, `env`, `site-packages`, `__pycache__`, `.git`, `node_modules`, `.tox`,
`.nox`, `.eggs`, `build`, `dist`, `migrations` and the various `.*_cache` dirs.
Narrow it with `Mediator(scan_paths=[...])`. Import errors are logged as
warnings on the `mediatr.discovery` logger — they are never silent.

Prefer explicitness on large codebases:

```python
mediator = Mediator(auto_discover=False)
mediator.register_handler(GetUser, GetUserHandler())
```

or decorate and let discovery collect them:

```python
from py_mediatR import handler, behavior

@handler
class GetUserHandler:
    def handle(self, req: GetUser): ...
```

## The pipeline

Every `send()` runs through the same onion. Behaviors are sorted by `order`,
**smallest first, outermost** — a low `order` starts first and finishes last:

```
send(request)
└─ behavior (order -100)          ← outermost
   └─ behavior (order -50)
      ├─ pre-processor            ← side effects only
      ├─ HANDLER                  ← your business logic
      └─ post-processor           ← sees the response, cannot change it
```

On an exception: every matching `IExceptionAction` observes it (without
swallowing), then the first matching `IExceptionHandler` may replace it with a
fallback response.

Use `mediator.trace()` to print the actual tree for a real request — that is
the authoritative answer to "which behavior called what".

### Built-in behaviors and their contracts

Each built-in reads an attribute off the **request**. If the attribute is
absent the behavior is a no-op, so a request opts in to exactly what it needs.

| Behavior | `order` | Opt-in on the request | Effect |
|---|---|---|---|
| `LoggingBehavior` | -100 | — (always) | logs entry/exit |
| `TracingBehavior` | -95 | — (always) | correlation id per request |
| `PerformanceBehavior` | -90 | — (always) | warns past a duration threshold |
| `AuthorizationBehavior` | -85 | `requires_permission` | denies with `UnauthorizedError`; handler never runs |
| `ValidationBehavior` | -80 | `validate()` method | raises before the handler |
| `CachingBehavior` | -70 | `cacheable = True` | TTL + LRU; a hit skips the handler entirely |
| `RetryBehavior` | -60 | — (always) | re-runs on exception, exponential backoff + jitter |
| `TransactionBehavior` | -50 | `transactional = True` | commit on success, rollback on error |

```python
from dataclasses import dataclass
from py_mediatR import IRequest

@dataclass
class PlaceOrder(IRequest):
    sku: str
    qty: int = 1

    transactional = True                 # TransactionBehavior engages
    requires_permission = "orders.write" # AuthorizationBehavior checks this

    def validate(self) -> None:          # ValidationBehavior calls this
        if self.qty < 1:
            raise ValueError("qty must be positive")

@dataclass
class SearchProducts(IRequest):
    keyword: str
    cacheable = True                     # CachingBehavior stores the response
```

Wiring them up — note that `AuthorizationBehavior` and `TransactionBehavior`
need a callback, so they are constructed, not just listed:

```python
from py_mediatR import (
    Mediator, LoggingBehavior, ValidationBehavior, CachingBehavior,
    RetryBehavior, TransactionBehavior, AuthorizationBehavior,
)

mediator = Mediator(behaviors=[
    LoggingBehavior(),
    AuthorizationBehavior(lambda req, perm: current_user.has(perm)),
    ValidationBehavior(),
    CachingBehavior(ttl_seconds=60, max_size=1000),
    RetryBehavior(max_attempts=3, delay=0.1, backoff=2.0, jitter=0.05),
    TransactionBehavior(session_factory=lambda: SessionLocal()),
])
```

### Writing your own behavior

`next_handler()` runs the rest of the chain. Not calling it short-circuits the
handler entirely — that is exactly how `CachingBehavior` serves a hit:

```python
from py_mediatR import IPipelineBehavior

class AuditBehavior(IPipelineBehavior):
    order = -110                 # lower than LoggingBehavior -> runs outermost
    applies_to = PlaceOrder      # optional; None (default) means every request

    def handle(self, request, next_handler):
        audit.write(f"-> {type(request).__name__}")
        response = next_handler()
        audit.write(f"<- {type(request).__name__}")
        return response
```

`applies_to` narrows a behavior to one request type. In the async pipeline
`handle()` may be `async def`, and `next_handler()` returns an awaitable.

### Pre/post processors

Both are side-effect only and support `order` and `applies_to`. A post
processor *sees* the response but cannot replace it — return a different value
from a behavior if you need that.

```python
from py_mediatR import IRequestPreProcessor, IRequestPostProcessor

class AuditPre(IRequestPreProcessor):
    def process(self, request):                 # may be async def
        audit.write(type(request).__name__)

class MetricsPost(IRequestPostProcessor):
    def process(self, request, response):       # may be async def
        metrics.increment(type(request).__name__)
```

### Exception handlers vs actions

| | Purpose | Return value |
|---|---|---|
| `IExceptionHandler.handle` | replace the error with a fallback response | returning a value swallows the exception; `raise` propagates |
| `IExceptionAction.execute` | observe only (log, alert, metric) | ignored — the exception keeps propagating |

Both filter on `exception_type` (default `Exception`), `applies_to` and
`order`. All matching actions run; the **first** matching handler that returns
a value wins.

```python
from py_mediatR import IExceptionHandler, IExceptionAction

class InvoiceNotFound(IExceptionHandler):
    exception_type = KeyError
    applies_to = FindInvoice
    def handle(self, request, exc):
        return FindInvoiceResponse(invoice_id=request.invoice_id, found=False)

class AlertAction(IExceptionAction):
    exception_type = Exception
    def execute(self, request, exc):
        alerting.notify(f"{type(request).__name__} failed: {exc}")
```

### Validators (FluentValidation style)

Keep validation out of the request when it needs dependencies:

```python
from py_mediatR import IValidator, ValidationBehavior

class CreateUserValidator(IValidator):
    applies_to = CreateUser          # or a tuple of types; None = all
    def validate(self, request):
        if "@" not in request.email:
            raise ValueError(f"invalid e-mail: {request.email}")

mediator = Mediator(behaviors=[ValidationBehavior(validators=[CreateUserValidator()])])
```

Matching validators run in `order`, then the request's own `validate()`.

### Notifications

Multiple subscribers per event, ordered by `order`. Choose how they run:

```python
from py_mediatR import PublishStrategy

mediator.publish(UserRegistered(user_id="U-1"))                       # SEQUENTIAL
await mediator.publish_async(evt, strategy=PublishStrategy.PARALLEL_WHENALL)
await mediator.publish_async(evt, strategy=PublishStrategy.PARALLEL_NOWAIT)
```

| Strategy | Semantics |
|---|---|
| `SEQUENTIAL` (default) | one after another, in `order`; first error propagates |
| `PARALLEL_WHENALL` | concurrent, waits for all |
| `PARALLEL_NOWAIT` | fire-and-forget; tasks are strongly referenced so they cannot be GC'd |

Subscriber errors propagate by default. Pass
`Mediator(swallow_notification_errors=True)` for the older forgiving behavior.
With `polymorphic_publish=True`, publishing a subclass also triggers base-type
subscribers (.NET covariance).

### Streaming

`create_stream()` yields items as they are produced, and
`IStreamPipelineBehavior` wraps the generator:

```python
from py_mediatR import IStreamRequest, IStreamPipelineBehavior

class StreamOrders(IStreamRequest):
    count: int = 100

class StreamOrdersHandler:
    async def handle(self, req: StreamOrders):
        for i in range(req.count):
            yield {"seq": i}

class StreamAudit(IStreamPipelineBehavior):
    async def handle(self, request, next_handler):
        async for item in next_handler():
            yield item

async for order in mediator.create_stream(StreamOrders(count=10)):
    ...
```

## Examples

Four runnable apps sharing one layered core live in
[`examples/`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/README.md)
— console, FastAPI, Flask and Django, each with its own README:

- [`examples/console_app`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/console_app/README.md)
  — start here: ten steps, each printing its call-chain tree
- [`examples/fastapi_app`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/fastapi_app/README.md)
  — async routes, one DI scope per request
- [`examples/flask_app`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/flask_app/README.md)
  — synchronous routes, explicit scopes
- [`examples/django_app`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/django_app/README.md)
  — views that do not know their handlers

The examples are not shipped in the wheel; they live in the repository.

## Package layout

Import from the top-level package. That is the only supported surface:

```python
from py_mediatR import Mediator, IRequest, IPipelineBehavior
```

Every name in `__all__` is re-exported there, so the layout below is an
implementation detail — it is documented to help you read the source, not to
be imported from.

| Module | Holds |
|---|---|
| `contracts.py` | `IRequest`, `IResponse`, `INotification`, the `I*` interfaces, `PublishStrategy`, `@handler` / `@behavior` |
| `mediator.py` | `Mediator`, `ISender` / `IPublisher`, pipeline compilation, `send` / `publish` / `create_stream` |
| `di.py` | `ServiceContainer`, `ServiceScope`, `scoped_mediator`, `make_fastapi_mediator_dependency` |
| `discovery.py` | project scanning, type-hint based handler resolution, `discover_handlers` |
| `behaviors.py` | the eight built-in behaviors |
| `cancellation.py` | `CancellationToken`, `CancellationTokenSource`, `current_cancellation_token` |
| `tracing.py` | `FlowNode`, `FlowTrace`, `trace_flow` — what `mediator.trace()` renders |
| `coercion.py` | dict → dataclass/pydantic coercion, the sync-over-async bridge |
| `_config.py`, `_typechecks.py` | flags, sentinels, internal type predicates |

`py_mediatR.py_mediatR` remains importable and still exposes every name it did
before the split, so code written against the single-module layout keeps
working unchanged.

## License

MIT — Ayhan Öztemel
