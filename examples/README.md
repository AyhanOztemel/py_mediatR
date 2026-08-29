# py_mediatR — Examples (English)

> Türkçe sürüm için: [README.tr.md](README.tr.md)

Four runnable example apps sharing one layered application core
(`ecommerce/`, Clean-Architecture style, vertical slices):

| App | Run (from `examples/`) | What it shows |
|-----|------------------------|---------------|
| **[Console](console_app/README.md)** | `python console_app/main.py` | 10 step-by-step demos, each printing its call-chain tree — start here |
| **[FastAPI](fastapi_app/README.md)** | `python fastapi_app/app.py` → http://127.0.0.1:8111 | `make_fastapi_mediator_dependency` (per-request scoped mediator), async send, streaming endpoint, Swagger at `/docs` |
| **[Flask](flask_app/README.md)** | `python flask_app/app.py` → http://127.0.0.1:8112 | sync `mediator.send()` in routes, `create_scope()` per request for scoped services |
| **[Django](django_app/README.md)** | `python django_app/manage.py` → http://127.0.0.1:8113 | mediator inside Django views, self-starting `runserver` |
| **Full coverage suite** | `python ecommerce/main.py` | advanced: 71 assertions exercising ALL 47 `__all__` exports (verification tool, not a tutorial) |
| **Headless web smoke test** | `python -m ci_smoke` | drives all three web apps through their in-process test clients — no server, no port |

No installation needed for the console app and coverage suite — they add
`../src` to `sys.path` automatically. The web apps additionally need:

```bash
pip install -r requirements.txt   # fastapi, uvicorn, flask, django
```

## Start with the console app

Its output is intentionally simple. Every step prints the request, the
response, and the call chain that produced them:

```
[Adim 6] Yeniden deneme (retry) — gecici hatada otomatik tekrar
  Gonderilen  : ChargeCard(amount=49.9)
  Donen cevap : ChargeCardResponse(receipt='charged:49.90', attempts=3)
  Cagri zinciri:
    ...
       └─ behavior: RetryBehavior   [retry 2/5 after ConnectionError, ...]
          ├─ behavior: TransactionBehavior   !! (propagated)
          │  └─ HANDLER: ChargeCardHandler   !! ConnectionError: ... #1
          ├─ behavior: TransactionBehavior   !! (propagated)
          │  └─ HANDLER: ChargeCardHandler   !! ConnectionError: ... #2
          └─ behavior: TransactionBehavior
             └─ HANDLER: ChargeCardHandler
  Ne oldu?    : the payment service failed twice with ConnectionError;
                RetryBehavior silently retried until it succeeded.
```

The 10 steps: send command · send query (dict→dataclass coercion) ·
validation error · authorization denial · caching · retry · exception
fallback · publish/subscribe · streaming · cancellation.
[Reading the tree →](console_app/README.md)

## Web endpoints (same on all three frameworks)

| Endpoint | Feature |
|----------|---------|
| `/users/olustur?name=..&email=..` | command + DI constructor injection |
| `/users/<id>` | query, dict → dataclass coercion |
| `/products/ara?keyword=..` | `CachingBehavior` (repeat = cache hit) |
| `/invoices/<id>` (try `INV-YOK`) | `IExceptionHandler` fallback response |
| `/orders/ver?sku=..&qty=..` | `TransactionBehavior` + scoped services |
| `/orders/akis?count=..` (FastAPI only) | streaming via `create_stream()` |

Every response also carries a `cagri_zinciri` field: the call-chain tree for
that request, rendered by `mediator.trace()`.

## Layout

```
examples/
├── console_app/              # narrative 10-step demo (start here)
│   ├── main.py
│   └── README.md / README.tr.md
├── fastapi_app/              # port 8111  (app.py + README[.tr].md)
├── flask_app/                # port 8112  (app.py + README[.tr].md)
├── django_app/               # port 8113
│   ├── manage.py             # self-starts runserver when run with no args
│   ├── mediator_demo/        # minimal settings + urls + views
│   └── README.md / README.tr.md
├── ci_smoke.py               # headless driver for the three web apps
├── requirements.txt          # web frameworks only (library itself has zero deps)
└── ecommerce/                # SHARED layered core + full coverage suite
    ├── main.py               # 71-check verification of all 47 exports
    ├── domain/               # pure entities
    ├── application/
    │   ├── crosscutting/     # behaviors, processors, exception handlers
    │   └── features/         # vertical slices: users, orders, products,
    │                         #   billing, reports (auto-discovered)
    ├── infrastructure/       # repositories (DI lifetimes), gateways
    └── composition/          # bootstrap.py — DI + Mediator wiring
```

## Design notes

* **Composition root**: all wiring lives in `ecommerce/composition/bootstrap.py`;
  the four apps just call `build_container()` + `build_mediator()`.
* **Vertical slices**: each feature folder owns its commands, queries and
  events, mirroring typical MediatR usage in .NET.
* **Ambient cancellation**: unlike .NET, py_mediatR flows the
  `CancellationToken` through a `contextvar`
  (`current_cancellation_token()`); handlers declaring a
  `cancellation_token` parameter still get it injected directly.
* **Import surface**: every file here imports from the top-level `py_mediatR`
  package only, never from its submodules (`py_mediatR.mediator` and friends) —
  see [Package layout](../README.md#package-layout).
* The `ecommerce/main.py` suite prints intentional `[PASS]` lines and
  deliberate error logs — it is a coverage verification tool. For a human
  readable walkthrough, use `console_app/main.py`.
