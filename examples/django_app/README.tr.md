# django_app — handler'larını tanımayan view'lar

> English version: [README.md](README.md)

```bash
cd examples
pip install -r requirements.txt
python django_app/manage.py
# http://127.0.0.1:8113
```

`manage.py` argümansız çalıştırıldığında `runserver 127.0.0.1:8113 --noreload`
başlatır; argümanla çağrıldığında sıradan bir `manage.py` gibi davranır.

Handler'lar, behavior'lar ve servisler paylaşılan
[`ecommerce/`](../ecommerce) çekirdeğinden gelir — bu uygulama yalnızca HTTP
katmanını ekler. Handler'ın ne olduğu ve nasıl keşfedildiği için
[kök README](../../README.tr.md).

## Burada Django'ya özgü olan ne?

### 1. Bilinçli olarak minik bir proje

`startproject` iskeleti yok, app yok, veritabanı yok:

```python
# mediator_demo/settings.py
ROOT_URLCONF = "examples.django_app.mediator_demo.urls"
MIDDLEWARE = []
INSTALLED_APPS = []
```

`INSTALLED_APPS` bilerek boştur — amaç mediator'ın ORM'den bağımsız olduğunu
göstermektir. py_mediatR'ın hiçbir parçası Django app registry'sine ihtiyaç
duymaz.

Settings modülü `examples.django_app.mediator_demo.settings` olarak
adreslenir; dolayısıyla `sys.path` üzerinde yalnızca `examples/` değil,
**depo kökü** de bulunmalıdır. `manage.py`, Django'ya dokunmadan önce depo
kökünü, `examples/` ve `src/` dizinlerini ekler; Django'yu kendiniz
başlatıyorsanız aynısını yapın, yoksa import başarısız olur.

### 2. Mediator modül düzeyinde, view'lar ince

```python
container = build_container()
mediator = build_mediator(container)

def user_getir(request, user_id: str):
    cevap = mediator.send(GetUser(user_id=user_id))
    return JsonResponse({"cevap": asdict(cevap)})
```

View yalnızca bir request tipinin adını verir, başka bir şey yapmaz.
`GetUserHandler`'ı import etmez, bir cache ya da validator çalıştığını bilmez ve
pipeline'a yeni bir behavior eklendiğinde değişmez. Burada fonksiyon view'ları
kullanılmıştır; bir `View.get()` metodu da aynıdır — mediator'ın bu konuda bir
görüşü yoktur.

### 3. Scoped servisler açık bir scope ister

Django istek başına bir DI kancası sunmaz; bu yüzden scoped bir servis işin
içindeyse scope elle açılır:

```python
def order_ver(request):
    with mediator.create_scope(container) as scoped:
        cevap = scoped.send(PlaceOrder(sku=..., qty=...))
    return JsonResponse({"cevap": asdict(cevap)})
```

`with` bloğundan çıkmak scope'u kapatır ve `PlaceOrder`'ın arkasındaki scoped
`OrderUnitOfWork`'ü serbest bırakır — view hata fırlatmış olsa bile. Gerçek bir
projede bu iş, istek başına scope açıp scope'lu mediator'ı `request` üzerinde
saklayan bir middleware'e aittir.

### 4. dataclass → JSON

`JsonResponse` dataclass serileştiremez; her view'daki `asdict(cevap)` bu
yüzdendir.

## Cevap gövdesindeki çağrı zinciri

Her view çağrısını `mediator.trace()` içine alır ve ağacı JSON olarak
döndürür:

```python
def zincir(flow):
    return flow.render(unicode=True).splitlines()

with mediator.trace() as flow:
    cevap = mediator.send(SearchProducts(keyword=...))
return JsonResponse({"cevap": asdict(cevap), "cagri_zinciri": zincir(flow)})
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

`python -m ci_smoke` (`examples/` içinden) bu uygulamayı Django'nun süreç içi
test client'ı ile sürer — sunucu yok, port yok.
`DJANGO_SETTINGS_MODULE=examples.django_app.mediator_demo.settings` ayarını
yapar, ardından her route'un 200 döndürdüğünü ve boş olmayan bir çağrı zinciri
taşıdığını doğrular.
