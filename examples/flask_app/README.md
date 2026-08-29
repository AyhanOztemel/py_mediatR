# flask_app — synchronous routes, explicit scopes

> Türkçe sürüm: [README.tr.md](README.tr.md)

```bash
cd examples
pip install -r requirements.txt
python flask_app/app.py
# http://127.0.0.1:8112
```

Handlers, behaviors and services come from the shared
[`ecommerce/`](../ecommerce) core — this app only adds the HTTP layer. For what
a handler is and how it is discovered, see the [root README](../../README.md).

## What is Flask-specific here

### 1. `send()` straight from the route

Flask views are synchronous, so no bridging is needed:

```python
container = build_container()
mediator = build_mediator(container)

@app.get("/users/<user_id>")
def user_getir(user_id: str):
    cevap = mediator.send(GetUser(user_id=user_id))
    return jsonify(cevap=asdict(cevap))
```

The module-level `mediator` is safe to share: it is stateless per dispatch and
the compiled pipeline is read-only after the first call for a request type.

### 2. Scoped services need an explicit scope

Flask has no dependency-injection hook comparable to FastAPI's `Depends`, so
the request scope is opened by hand where a scoped service is involved:

```python
@app.get("/orders/ver")
def order_ver():
    with mediator.create_scope(container) as scoped:
        cevap = scoped.send(PlaceOrder(sku=..., qty=...))
    return jsonify(cevap=asdict(cevap))
```

`create_scope(container)` yields a mediator bound to a fresh `ServiceScope`.
Leaving the `with` block disposes it, so the scoped `OrderUnitOfWork` behind
`PlaceOrder` is released with the response — and disposed on the way out even
if the view raised.

Routes with no scoped dependency (`GetUser`, `SearchProducts`) use the shared
`mediator` directly. If you would rather not think about which is which, open
a scope in a `before_request` hook and store it on `flask.g`.

### 3. dataclass → JSON

Handlers return dataclasses, and `jsonify` cannot serialise those, hence
`asdict(cevap)` in every route. FastAPI does this conversion itself; Flask does
not.

## The call chain in the response body

Every route wraps its call in `mediator.trace()` and returns the tree as JSON:

```python
def zincir(flow):
    return flow.render(unicode=True).splitlines()

with mediator.trace() as flow:
    cevap = mediator.send(SearchProducts(keyword=keyword))
return jsonify(cevap=asdict(cevap), cagri_zinciri=zincir(flow))
```

The trace cursor lives in a `ContextVar` and is scoped to the `with` block, so
concurrent requests do not mix their trees. Drop it in production — it is a
demo affordance, not middleware.

## Routes

| Route | Shows |
|---|---|
| `/users/olustur?name=&email=` | command, handler built by the DI container |
| `/users/<user_id>` | query; handler returns a `dict`, coerced to `GetUserResponse` |
| `/products/ara?keyword=` | `cacheable=True` — call it twice, the second tree stops at `CachingBehavior` |
| `/invoices/<invoice_id>` | try `INV-YOK`: handler raises `KeyError`, exception handler returns `found=False` |
| `/orders/ver?sku=&qty=` | `transactional=True`, inside an explicit scope |

## Headless check

`python -m ci_smoke` (from `examples/`) drives this app through Flask's
`test_client()` — no server, no port. It asserts every route returns 200 and
carries a non-empty call chain.
