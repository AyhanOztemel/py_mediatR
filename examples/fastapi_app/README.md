# fastapi_app — async routes, one DI scope per request

> Türkçe sürüm: [README.tr.md](README.tr.md)

```bash
cd examples
pip install -r requirements.txt
python fastapi_app/app.py
# http://127.0.0.1:8111   (Swagger: /docs)
```

Handlers, behaviors and services come from the shared
[`ecommerce/`](../ecommerce) core — this app only adds the HTTP layer. For what
a handler is and how it is discovered, see the [root README](../../README.md).

## What is FastAPI-specific here

### 1. A scoped mediator as a dependency

```python
container = build_container()
base_mediator = build_mediator(container)
get_mediator = make_fastapi_mediator_dependency(base_mediator, container)

@app.get("/users/{user_id}")
async def user_getir(user_id: str, m: Mediator = Depends(get_mediator)):
    return await m.send_async(GetUser(user_id=user_id))
```

`make_fastapi_mediator_dependency(base_mediator, container)` returns an async
generator dependency. Per request it opens `container.create_scope()`, yields a
mediator bound to that scope, and on the way out `await scope.adispose()` — so
scoped services (the `OrderUnitOfWork` behind `PlaceOrder`) are released when
the response is sent, even if the route raised.

py_mediatR does **not** import FastAPI. The factory only needs `Depends` on
your side; the library stays zero-dependency.

### 2. `send_async`, not `send`

Routes are `async def`, so an event loop is already running. Use
`await m.send_async(...)`. Calling the sync `m.send(...)` from inside a running
loop pushes the work through the sync bridge thread — it works, but it is
wasted overhead in an async route.

### 3. Streaming

`create_stream` returns an async generator, which is the natural shape for a
FastAPI route:

```python
@app.get("/orders/akis")
async def order_akis(count: int = 5, m: Mediator = Depends(get_mediator)):
    return [item async for item in m.create_stream(StreamOrderFeed(count=count))]
```

Swap the list comprehension for a `StreamingResponse` to stream to the client
instead of buffering.

## The call chain in the response body

Every route wraps its call in `m.trace()` and returns the tree as JSON, so you
can see the pipeline from the browser without reading logs:

```python
def zincir(flow):
    return flow.render(unicode=True).splitlines()

with m.trace() as flow:
    cevap = await m.send_async(SearchProducts(keyword=keyword))
return {"cevap": cevap, "cagri_zinciri": zincir(flow)}
```

`trace()` is scoped to the `with` block and stored in a `ContextVar`, so
concurrent requests do not mix their trees. Drop it in production — it is a
demo affordance, not middleware.

## Routes

| Route | Shows |
|---|---|
| `/users/olustur?name=&email=` | command, handler built by the DI container |
| `/users/{user_id}` | query; handler returns a `dict`, coerced to `GetUserResponse` |
| `/products/ara?keyword=` | `cacheable=True` — call it twice, the second tree stops at `CachingBehavior` |
| `/invoices/{invoice_id}` | try `INV-YOK`: handler raises `KeyError`, exception handler returns `found=False` |
| `/orders/ver?sku=&qty=` | `transactional=True`, scoped `OrderUnitOfWork` |
| `/orders/akis?count=` | `create_stream` |

## Headless check

`python -m ci_smoke` (from `examples/`) drives this app through FastAPI's
in-process `TestClient` — no server, no port. It asserts every route returns
200 and carries a non-empty call chain.
