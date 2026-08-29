# flask_app — senkron route'lar, açık scope'lar

> English version: [README.md](README.md)

```bash
cd examples
pip install -r requirements.txt
python flask_app/app.py
# http://127.0.0.1:8112
```

Handler'lar, behavior'lar ve servisler paylaşılan
[`ecommerce/`](../ecommerce) çekirdeğinden gelir — bu uygulama yalnızca HTTP
katmanını ekler. Handler'ın ne olduğu ve nasıl keşfedildiği için
[kök README](../../README.tr.md).

## Burada Flask'a özgü olan ne?

### 1. Route'un içinden doğrudan `send()`

Flask view'ları senkron çalışır, köprüye gerek yoktur:

```python
container = build_container()
mediator = build_mediator(container)

@app.get("/users/<user_id>")
def user_getir(user_id: str):
    cevap = mediator.send(GetUser(user_id=user_id))
    return jsonify(cevap=asdict(cevap))
```

Modül düzeyindeki `mediator` paylaşılabilir: gönderim başına durum tutmaz ve
derlenmiş pipeline, bir request tipi için ilk çağrıdan sonra salt okunurdur.

### 2. Scoped servisler açık bir scope ister

Flask'ta FastAPI'nin `Depends`'ine denk bir DI kancası yoktur; bu yüzden scoped
bir servis işin içindeyse istek scope'u elle açılır:

```python
@app.get("/orders/ver")
def order_ver():
    with mediator.create_scope(container) as scoped:
        cevap = scoped.send(PlaceOrder(sku=..., qty=...))
    return jsonify(cevap=asdict(cevap))
```

`create_scope(container)` taze bir `ServiceScope`'a bağlı mediator verir. `with`
bloğundan çıkmak scope'u kapatır; böylece `PlaceOrder`'ın arkasındaki scoped
`OrderUnitOfWork` cevapla birlikte serbest bırakılır — view hata fırlatmış olsa
bile.

Scoped bağımlılığı olmayan route'lar (`GetUser`, `SearchProducts`) doğrudan
paylaşılan `mediator`'ı kullanır. Hangisinin hangisi olduğunu düşünmek
istemiyorsanız scope'u bir `before_request` kancasında açıp `flask.g` üzerinde
saklayın.

### 3. dataclass → JSON

Handler'lar dataclass döndürür ve `jsonify` bunları serileştiremez; her
route'taki `asdict(cevap)` bu yüzdendir. FastAPI bu dönüşümü kendi yapar,
Flask yapmaz.

## Cevap gövdesindeki çağrı zinciri

Her route çağrısını `mediator.trace()` içine alır ve ağacı JSON olarak
döndürür:

```python
def zincir(flow):
    return flow.render(unicode=True).splitlines()

with mediator.trace() as flow:
    cevap = mediator.send(SearchProducts(keyword=keyword))
return jsonify(cevap=asdict(cevap), cagri_zinciri=zincir(flow))
```

İzleme imleci bir `ContextVar` içinde yaşar ve `with` bloğuyla sınırlıdır; bu
yüzden eşzamanlı istekler ağaçlarını birbirine karıştırmaz. Üretimde çıkarın —
bu bir middleware değil, demo kolaylığıdır.

## Route'lar

| Route | Ne gösterir |
|---|---|
| `/users/olustur?name=&email=` | komut; handler'ı DI container üretir |
| `/users/<user_id>` | sorgu; handler `dict` döndürür, `GetUserResponse`'a çevrilir |
| `/products/ara?keyword=` | `cacheable=True` — iki kez çağırın, ikinci ağaç `CachingBehavior`'da biter |
| `/invoices/<invoice_id>` | `INV-YOK` deneyin: handler `KeyError` fırlatır, exception handler `found=False` döndürür |
| `/orders/ver?sku=&qty=` | `transactional=True`, açık bir scope içinde |

## Başsız kontrol

`python -m ci_smoke` (`examples/` içinden) bu uygulamayı Flask'ın
`test_client()`'ı ile sürer — sunucu yok, port yok. Her route'un 200
döndürdüğünü ve boş olmayan bir çağrı zinciri taşıdığını doğrular.
