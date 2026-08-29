# fastapi_app — async route'lar, istek başına bir DI scope'u

> English version: [README.md](README.md)

```bash
cd examples
pip install -r requirements.txt
python fastapi_app/app.py
# http://127.0.0.1:8111   (Swagger: /docs)
```

Handler'lar, behavior'lar ve servisler paylaşılan
[`ecommerce/`](../ecommerce) çekirdeğinden gelir — bu uygulama yalnızca HTTP
katmanını ekler. Handler'ın ne olduğu ve nasıl keşfedildiği için
[kök README](../../README.tr.md).

## Burada FastAPI'ye özgü olan ne?

### 1. Dependency olarak scope'lu mediator

```python
container = build_container()
base_mediator = build_mediator(container)
get_mediator = make_fastapi_mediator_dependency(base_mediator, container)

@app.get("/users/{user_id}")
async def user_getir(user_id: str, m: Mediator = Depends(get_mediator)):
    return await m.send_async(GetUser(user_id=user_id))
```

`make_fastapi_mediator_dependency(base_mediator, container)` bir async
generator dependency döndürür. Her istekte `container.create_scope()` açar, o
scope'a bağlı bir mediator verir ve çıkışta `await scope.adispose()` çağırır —
böylece scoped servisler (`PlaceOrder`'ın arkasındaki `OrderUnitOfWork`) cevap
gönderildiğinde, route hata fırlatmış olsa bile serbest bırakılır.

py_mediatR FastAPI'yi **import etmez**. Fabrika yalnızca sizin tarafınızda
`Depends` ister; kütüphane sıfır bağımlı kalır.

### 2. `send` değil, `send_async`

Route'lar `async def` olduğundan zaten çalışan bir event loop vardır.
`await m.send_async(...)` kullanın. Çalışan bir loop içinden senkron
`m.send(...)` çağırmak işi sync köprü thread'ine iter — çalışır, ama async bir
route'ta boşa maliyettir.

### 3. Streaming

`create_stream` bir async generator döndürür; bu da FastAPI route'u için doğal
biçimdir:

```python
@app.get("/orders/akis")
async def order_akis(count: int = 5, m: Mediator = Depends(get_mediator)):
    return [item async for item in m.create_stream(StreamOrderFeed(count=count))]
```

Tamponlamak yerine istemciye akıtmak isterseniz liste üretecini bir
`StreamingResponse` ile değiştirin.

## Cevap gövdesindeki çağrı zinciri

Her route çağrısını `m.trace()` içine alır ve ağacı JSON olarak döndürür;
böylece pipeline'ı log okumadan, tarayıcıdan görebilirsiniz:

```python
def zincir(flow):
    return flow.render(unicode=True).splitlines()

with m.trace() as flow:
    cevap = await m.send_async(SearchProducts(keyword=keyword))
return {"cevap": cevap, "cagri_zinciri": zincir(flow)}
```

`trace()` `with` bloğuyla sınırlıdır ve bir `ContextVar` içinde tutulur; bu
yüzden eşzamanlı istekler ağaçlarını birbirine karıştırmaz. Üretimde çıkarın —
bu bir middleware değil, demo kolaylığıdır.

## Route'lar

| Route | Ne gösterir |
|---|---|
| `/users/olustur?name=&email=` | komut; handler'ı DI container üretir |
| `/users/{user_id}` | sorgu; handler `dict` döndürür, `GetUserResponse`'a çevrilir |
| `/products/ara?keyword=` | `cacheable=True` — iki kez çağırın, ikinci ağaç `CachingBehavior`'da biter |
| `/invoices/{invoice_id}` | `INV-YOK` deneyin: handler `KeyError` fırlatır, exception handler `found=False` döndürür |
| `/orders/ver?sku=&qty=` | `transactional=True`, scoped `OrderUnitOfWork` |
| `/orders/akis?count=` | `create_stream` |

## Başsız kontrol

`python -m ci_smoke` (`examples/` içinden) bu uygulamayı FastAPI'nin süreç içi
`TestClient`'ı ile sürer — sunucu yok, port yok. Her route'un 200 döndürdüğünü
ve boş olmayan bir çağrı zinciri taşıdığını doğrular.
