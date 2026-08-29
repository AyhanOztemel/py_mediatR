# console_app — read the call chain

> Türkçe sürüm: [README.tr.md](README.tr.md)

The fastest way to see what py_mediatR actually does. Ten steps, no web
server, no database. Every step prints the request, the response, and the
**call-chain tree** that produced it.

```bash
cd examples
python console_app/main.py
```

No dependencies beyond the standard library. Handlers, behaviors and services
come from the shared [`ecommerce/`](../ecommerce) core, so what you see here is
the same wiring the three web examples use.

## How to read the tree

Every step wraps its call in `mediator.trace()`:

```python
with mediator.trace() as flow:
    cevap = mediator.send(CreateUser(name="Ayhan", email="ayhan@ornek.com"))
print(flow.render())
```

`render()` prints the onion top-down — outermost behavior first, handler at
the centre:

```
send(CreateUser)   (0.69 ms)
└─ behavior: AuditTrailBehavior   (0.61 ms)
   └─ behavior: LoggingBehavior   (0.58 ms)
      └─ behavior: TracingBehavior   (0.54 ms)
         └─ behavior: PerformanceBehavior   (0.53 ms)
            └─ behavior: AuthorizationBehavior   (0.50 ms)
               └─ behavior: ValidationBehavior   (0.49 ms)
                  └─ behavior: CachingBehavior   [not cacheable - pass through]   (0.47 ms)
                     └─ behavior: RetryBehavior   (0.44 ms)
                        └─ behavior: TransactionBehavior   (0.40 ms)
                           ├─ pre: AuditPreProcessor
                           ├─ HANDLER: CreateUserHandler   (0.07 ms)
                           └─ post: MetricsPostProcessor
```

Annotations in `[...]` are notes a behavior attached to its own node; `!!`
marks an exception. The error text is spelled out only at the node that
**raised** it — ancestors that merely let it propagate show `!! (propagated)`,
otherwise a deep pipeline repeats the same message on every line.

## The ten steps

| # | Shows | What the tree makes visible |
|---|---|---|
| 1 | `send(CreateUser)` | full behavior order, pre/post processors around the handler |
| 2 | `send(GetUser)` | handler returns a `dict`; `GetUser` → `GetUserResponse` coercion |
| 3 | validation | `ValidationBehavior` raises — the branch stops **above** the handler |
| 4 | authorization | `requires_permission` denies before any handler node appears |
| 5 | caching | second call ends at `CachingBehavior   [CACHE HIT - handler NOT called]` |
| 6 | retry | `RetryBehavior` has **three** `TransactionBehavior` children — two failed, one succeeded |
| 7 | exception handler | handler raises `KeyError`, then `on-error: InvoiceNotFoundHandler` returns a fallback |
| 8 | `publish` | one event, three subscriber nodes in order |
| 9 | `create_stream` | `stream(...)` root, `[3 item(s) yielded]` on the handler |
| 10 | cancellation | `OperationCancelledError` pinpointed at `LongJobHandler` |

Step 6 is the one worth staring at. Retries are usually invisible in logs;
here they are three sibling subtrees under `RetryBehavior`:

```
└─ behavior: RetryBehavior   [retry 2/5 after ConnectionError, retry 3/5 after ConnectionError]
   ├─ behavior: TransactionBehavior   !! (propagated)   (0.28 ms)
   │  ├─ pre: AuditPreProcessor
   │  └─ HANDLER: ChargeCardHandler   !! ConnectionError: payment gateway timeout #1
   ├─ behavior: TransactionBehavior   !! (propagated)   (0.06 ms)
   │  ├─ pre: AuditPreProcessor
   │  └─ HANDLER: ChargeCardHandler   !! ConnectionError: payment gateway timeout #2
   └─ behavior: TransactionBehavior   (0.07 ms)
      ├─ pre: AuditPreProcessor
      ├─ HANDLER: ChargeCardHandler
      └─ post: MetricsPostProcessor
```

## Console encoding

`render(show_timing=True, unicode=None)`. With `unicode=None` the glyphs are
chosen by probing the console: `├─ └─ │` where the encoding supports them,
`|- \`- |` where it does not — so the tree survives a Windows cp1254 terminal
instead of raising `UnicodeEncodeError`. Force either style with
`unicode=True` / `unicode=False`, or run with `PYTHONIOENCODING=utf-8`.
`show_timing=False` drops the `(0.69 ms)` suffixes, which is what you want
when diffing trees in a test.

## Next

- Conventions, behavior tables and the full pipeline reference: [root README](../../README.md)
- The layered core these steps drive: [`ecommerce/`](../ecommerce)
- Same core behind HTTP: [fastapi_app](../fastapi_app/README.md) ·
  [flask_app](../flask_app/README.md) · [django_app](../django_app/README.md)
