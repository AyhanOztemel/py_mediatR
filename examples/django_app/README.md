# django_app — views that do not know their handlers

> Türkçe sürüm: [README.tr.md](README.tr.md)

```bash
cd examples
pip install -r requirements.txt
python django_app/manage.py
# http://127.0.0.1:8113
```

`manage.py` with no arguments starts `runserver 127.0.0.1:8113 --noreload`;
with arguments it behaves like an ordinary `manage.py`.

Handlers, behaviors and services come from the shared
[`ecommerce/`](../ecommerce) core — this app only adds the HTTP layer. For what
a handler is and how it is discovered, see the [root README](../../README.md).

## What is Django-specific here

### 1. A deliberately tiny project

There is no `startproject` skeleton, no app, no database:

```python
# mediator_demo/settings.py
ROOT_URLCONF = "examples.django_app.mediator_demo.urls"
MIDDLEWARE = []
INSTALLED_APPS = []
```

`INSTALLED_APPS` is empty on purpose — the point is that the mediator is
orthogonal to the ORM. Nothing in py_mediatR needs a Django app registry.

The settings module is addressed as
`examples.django_app.mediator_demo.settings`, so the **repository root** must
be on `sys.path`, not just `examples/`. `manage.py` inserts repo root,
`examples/` and `src/` before touching Django; if you bootstrap Django
yourself, do the same or the import will fail.

### 2. The mediator is module-level, views stay thin

```python
container = build_container()
mediator = build_mediator(container)

def user_getir(request, user_id: str):
    cevap = mediator.send(GetUser(user_id=user_id))
    return JsonResponse({"cevap": asdict(cevap)})
```

The view names a request type and nothing else. It does not import
`GetUserHandler`, does not know a cache or a validator ran, and does not change
when a behavior is added to the pipeline. Function views are used here; a
`View.get()` method is identical — the mediator has no opinion.

### 3. Scoped services need an explicit scope

Django gives no per-request DI hook, so the scope is opened by hand where a
scoped service is involved:

```python
def order_ver(request):
    with mediator.create_scope(container) as scoped:
        cevap = scoped.send(PlaceOrder(sku=..., qty=...))
    return JsonResponse({"cevap": asdict(cevap)})
```

Leaving the `with` block disposes the scope, releasing the scoped
`OrderUnitOfWork` behind `PlaceOrder` — including when the view raised. In a
real project this belongs in middleware that opens a scope per request and
stores the scoped mediator on `request`.

### 4. dataclass → JSON

`JsonResponse` cannot serialise dataclasses, hence `asdict(cevap)` in every
view.

## The call chain in the response body

Every view wraps its call in `mediator.trace()` and returns the tree as JSON:

```python
def zincir(flow):
    return flow.render(unicode=True).splitlines()

with mediator.trace() as flow:
    cevap = mediator.send(SearchProducts(keyword=...))
return JsonResponse({"cevap": asdict(cevap), "cagri_zinciri": zincir(flow)})
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

`python -m ci_smoke` (from `examples/`) drives this app through Django's
in-process test client — no server, no port. It sets
`DJANGO_SETTINGS_MODULE=examples.django_app.mediator_demo.settings`, then
asserts every route returns 200 and carries a non-empty call chain.
