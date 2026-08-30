# py_mediatR

[![PyPI](https://img.shields.io/pypi/v/py-mediatR.svg)](https://pypi.org/project/py-mediatR/)
[![Python](https://img.shields.io/pypi/pyversions/py-mediatR.svg)](https://pypi.org/project/py-mediatR/)
[![CI](https://github.com/AyhanOztemel/py_mediatR/actions/workflows/ci.yml/badge.svg)](https://github.com/AyhanOztemel/py_mediatR/actions/workflows/ci.yml)
[![Lisans: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/AyhanOztemel/py_mediatR/blob/main/LICENSE)

> English version: [README.md](https://github.com/AyhanOztemel/py_mediatR/blob/main/README.md)

Python için yüksek performanslı **CQRS / Mediator** uygulaması;
[.NET MediatR](https://github.com/jbogard/MediatR) esinlidir (kısmi anlamsal
parite, ayrıca hazır caching/retry/transaction behavior'ları gibi ekstralar) —
sıfır bağımlılık, free-threaded (GIL'siz) çalışmaya hazır.

**v6.7:** çağrı zinciri izleme (`mediator.trace()`), transaction temizliğinde
ve sync-over-async köprüsünde sessiz hataların kaldırılması, captive
dependency reddi, `Optional[T]` otomatik enjeksiyonu, `py.typed` paketleme.
Bkz. [6.7'de yenilikler](#67de-yenilikler).

**v6.4:** tek event loop'lu async pipeline, notification hatalarının varsayılan
olarak yukarı taşınması, açık `ExceptionHandlerState`, tip belirtilmiş
`send() -> TResponse`, JSON discovery cache (pickle yok), gerçek LRU cache,
thread-safe kayıt defterleri.

## 6.7'de yenilikler

Hiçbir şey kaldırılmadı ve hiçbir imza katılaşmadı — 6.7, 6.6'nın yerine
doğrudan geçer.

### Çağrı zincirini görün

Mediator kimin kimi çağırdığını gizler; bu da "handler'ım neden çalışmadı?"
sorusunu zorlaştırır. `trace()` tek bir gönderimi ağaç olarak kaydeder:

```python
with mediator.trace() as flow:
    mediator.send(SearchProducts(keyword="laptop"))
    mediator.send(SearchProducts(keyword="laptop"))   # önbellekten döner
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

Yeniden denemeler tekrar eden handler düğümleri olarak görünür; bir istisna
yalnızca onu **fırlatan** düğümde açık yazılır, üst düğümler
`!! (propagated)` ile işaretlenir. İzleme varsayılan olarak kapalıdır ve pasifken
adım başına yalnızca bir `ContextVar` okuması maliyeti vardır. `flow.steps()` ve
`flow.find(label)` aynı veriyi assert'ler için sunar; `render(unicode=False)`
ASCII karakterleri zorlar.

### Hatalar artık sessiz değil

- `TransactionBehavior` başarısız bir `rollback()`/`close()` çağrısını artık
  yutmuyor. Temizlik hatası loglanır ve `add_note()` ile asıl istisnaya
  iliştirilir; `raise_on_cleanup_failure=True` ile `TransactionCleanupError`
  olarak fırlatılır.
- Çalışan bir event loop içinden senkron `send()` ile async session göndermek
  artık yanlış loop üzerinde rollback yapmak yerine baştan `TypeError` ile
  reddedilir.
- Sync-over-async köprüsü 30 sn bütçeyle bekler
  (`MEDIATR_SYNC_BRIDGE_TIMEOUT`, `<=0` devre dışı bırakır) ve çağıran thread'i
  sonsuza kadar dondurmak yerine `SyncBridgeTimeoutError` fırlatır.
- Ateşle-unut notification task'ları güçlü referansla tutulur; böylece
  `PARALLEL_NOWAIT` aboneleri iş ortasında çöp toplayıcıya yem olmaz.
- Otomatik keşif (auto-discovery) import hataları `mediatr.discovery` logger'ında
  uyarı olarak yazılır (`MEDIATR_DISCOVERY_WARNINGS=0` ile susturulur).

### DI doğruluğu

- Scoped bir servisi tüketen singleton artık `DIResolutionError` ile reddedilir
  — .NET'in captive-dependency kuralı.
- `Optional[T]`, `T | None` ve `Annotated[...]` constructor ipuçları açılır;
  üretilemeyen isteğe bağlı bir bağımlılık `None` olarak enjekte edilir.

### Paketleme

- `py.typed` paketle birlikte gelir; tip denetleyiciler satır içi
  annotation'ları kullanır.
- Büyük/küçük harf duyarlı dosya sistemlerinde normalize edilmiş dağıtım adının
  yanıltmaması için `import py_mediatr` (tamamı küçük harf) da çalışır.
- Uygulama, 4 600 satırlık tek modülden odaklı modüllerden oluşan bir pakete
  ayrıldı. Import ettiğiniz hiçbir şey değişmedi —
  [Paket yapısı](#paket-yapısı) bölümüne bakın.

## Özellikler

- **Request/Response** — `IRequest[TResponse]` generic'leri, senkron `send()` ve async `send_async()`
- **Notification (pub/sub)** — çoklu handler, sıralama, SEQUENTIAL / PARALLEL_WHENALL / PARALLEL_NOWAIT stratejileri, özel publisher
- **Pipeline behavior'ları** — `IPipelineBehavior` middleware + 8 hazır behavior (Logging, Performance, Validation, Caching, Retry, Transaction, Authorization, Tracing)
- **Streaming** — `IStreamRequest` + `create_stream()` (`IAsyncEnumerable<T>` muadili)
- **Pre/Post processor, exception handler ve action'lar**
- **Dependency Injection** — **singleton / scoped / transient** ömürlü `ServiceContainer`, tip ipucuna dayalı constructor auto-wiring, `ServiceScope` (sync+async), istek başına scope'lu mediator, FastAPI köprüsü
- **CancellationToken** — `cancel_after` destekli `CancellationTokenSource`, bağlı (linked) token'lar, handler'a enjeksiyon (`handle(self, req, cancellation_token=...)`), akış iptali
- **Otomatik keşif** — projeyi tarayarak (önbellekli) handler bulma veya açık `@handler` / `@behavior` dekoratörleri
- **Çağrı zinciri izleme** — `mediator.trace()` behavior'ları, handler'ı ve aboneleri ağaç olarak basar
- **Free-threaded hazır** — Python 3.13t/3.14t (GIL'siz) altında güvenli

## Kurulum

```bash
pip install py-mediatR              # çekirdek (sıfır bağımlılık)
pip install "py-mediatR[pydantic]"  # isteğe bağlı pydantic model dönüşümü
```

## Hızlı başlangıç

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

### DI + scoped ömür (istek başına DB session)

```python
from py_mediatR import Mediator, ServiceContainer, scoped_mediator

container = ServiceContainer()
container.register_singleton(Config)
container.register_scoped(DbSession)        # scope/istek başına bir tane
container.register_transient(UserRepository)

mediator = Mediator(handler_factory=container)
with scoped_mediator(mediator, container) as m:
    m.send(CreateUser(name="Ada"))          # handler'lar aynı scoped session'ı paylaşır
# scope kapandı -> session.close()
```

### CancellationToken

```python
from py_mediatR import CancellationTokenSource, OperationCancelledError

cts = CancellationTokenSource(cancel_after=2.0)   # zaman aşımı
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

## Handler nasıl bulunur?

**İsimlendirme kuralı yoktur.** Bir sınıfın handler olmasını sağlayan şey,
`handle()` parametresindeki *tip annotation'ıdır* — sınıf adının hiçbir önemi
yoktur:

```python
class GetUser(IRequest):
    user_id: str

class BuIsimHicOnemliDegil:           # "GetUserHandler" değil — yine de bulunur
    def handle(self, req: GetUser):   # <- sözleşme annotation'dır
        return {"user_id": req.user_id}
```

Kurallar tam olarak şöyle:

| Tür | Nasıl tespit edilir |
|---|---|
| Request handler | `handle()` içinde bir `IRequest` alt sınıfıyla annotate edilmiş parametre var |
| Stream handler | aynısı, ama annotation bir `IStreamRequest` alt sınıfı |
| Notification handler | `handle()` içinde bir `INotification` alt sınıfıyla annotate edilmiş parametre var |

Canınızı yakabilecek ayrıntılar:

- Annotation import anında **çözülebilir** olmalıdır. Yanlış ya da import
  edilemeyen bir forward reference, sınıfı hata vermek yerine görünmez kılar.
- Yalnızca **bir** `IRequest` parametresine izin verilir; iki tane olursa
  `TypeError` fırlar.
- Fazladan parametreler sorun değildir — `cancellation_token` adına göre
  enjekte edilir.
- `__init__`'i argüman alan bir handler *ertelenmiş* (deferred) kaydedilir ve
  gönderim anında `handler_factory` (sizin `ServiceContainer`'ınız) üzerinden
  çözülür.
- Request tipi başına bir handler; notification'ların birden çok abonesi olabilir.

### Cevap dönüşümü — isimlendirmenin önem kazandığı tek yer

Bir handler `dict` döndürürse py_mediatR bunu **aynı modülde adına göre** bulduğu
bir response sınıfına dönüştürür: `GetUser` → `GetUserResponse`
(ya da `GetUserRequest` → `GetUserResponse`). Açık bir `-> GetUserResponse`
dönüş annotation'ı önceliklidir. İkisi de yoksa `dict` olduğu gibi döner.

### Keşif kapsamı

`Mediator()`, `project_root` altında `*.py` dosyalarını özyinelemeli tarar;
`venv`, `.venv`, `env`, `site-packages`, `__pycache__`, `.git`, `node_modules`,
`.tox`, `.nox`, `.eggs`, `build`, `dist`, `migrations` ve çeşitli `.*_cache`
klasörlerini atlar. `Mediator(scan_paths=[...])` ile daraltın. Import hataları
`mediatr.discovery` logger'ında uyarı olarak yazılır — asla sessiz kalmaz.

Büyük kod tabanlarında açık kaydı tercih edin:

```python
mediator = Mediator(auto_discover=False)
mediator.register_handler(GetUser, GetUserHandler())
```

ya da dekore edip keşfin toplamasına izin verin:

```python
from py_mediatR import handler, behavior

@handler
class GetUserHandler:
    def handle(self, req: GetUser): ...
```

## Pipeline

Her `send()` aynı soğan katmanlarından geçer. Behavior'lar `order` değerine göre
sıralanır: **en küçük önce, en dışta** — düşük `order` ilk başlar, en son biter:

```
send(request)
└─ behavior (order -100)          ← en dışta
   └─ behavior (order -50)
      ├─ pre-processor            ← yalnızca yan etki
      ├─ HANDLER                  ← iş mantığınız
      └─ post-processor           ← cevabı görür, değiştiremez
```

Bir istisna oluştuğunda: eşleşen her `IExceptionAction` bunu (yutmadan) gözler,
ardından eşleşen ilk `IExceptionHandler` istisnayı bir yedek cevapla
değiştirebilir.

Gerçek bir istek için asıl ağacı basmak üzere `mediator.trace()` kullanın —
"hangi behavior neyi çağırdı" sorusunun tek yetkili cevabı budur.

### Hazır behavior'lar ve sözleşmeleri

Her hazır behavior **request** üzerinden bir nitelik okur. Nitelik yoksa
behavior no-op olur; yani her istek yalnızca ihtiyacı olana dahil olur.

| Behavior | `order` | Request'te dahil olma niteliği | Etkisi |
|---|---|---|---|
| `LoggingBehavior` | -100 | — (her zaman) | giriş/çıkış loglar |
| `TracingBehavior` | -95 | — (her zaman) | istek başına correlation id |
| `PerformanceBehavior` | -90 | — (her zaman) | süre eşiği aşılınca uyarır |
| `AuthorizationBehavior` | -85 | `requires_permission` | `UnauthorizedError` ile reddeder; handler hiç çalışmaz |
| `ValidationBehavior` | -80 | `validate()` metodu | handler'dan önce hata fırlatır |
| `CachingBehavior` | -70 | `cacheable = True` | TTL + LRU; isabet olursa handler tamamen atlanır |
| `RetryBehavior` | -60 | — (her zaman) | istisnada tekrar dener, üstel geri çekilme + jitter |
| `TransactionBehavior` | -50 | `transactional = True` | başarıda commit, hatada rollback |

```python
from dataclasses import dataclass
from py_mediatR import IRequest

@dataclass
class PlaceOrder(IRequest):
    sku: str
    qty: int = 1

    transactional = True                 # TransactionBehavior devreye girer
    requires_permission = "orders.write" # AuthorizationBehavior bunu kontrol eder

    def validate(self) -> None:          # ValidationBehavior bunu çağırır
        if self.qty < 1:
            raise ValueError("qty must be positive")

@dataclass
class SearchProducts(IRequest):
    keyword: str
    cacheable = True                     # CachingBehavior cevabı saklar
```

Kablolama — `AuthorizationBehavior` ve `TransactionBehavior` bir geri çağırma
(callback) istediğinden yalnızca listelenmez, örneklenir:

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

### Kendi behavior'ınızı yazmak

`next_handler()` zincirin geri kalanını çalıştırır. Onu çağırmamak handler'ı
tamamen kısa devre yaptırır — `CachingBehavior` bir önbellek isabetini tam
olarak böyle sunar:

```python
from py_mediatR import IPipelineBehavior

class AuditBehavior(IPipelineBehavior):
    order = -110                 # LoggingBehavior'dan düşük -> en dışta çalışır
    applies_to = PlaceOrder      # isteğe bağlı; None (varsayılan) = her istek

    def handle(self, request, next_handler):
        audit.write(f"-> {type(request).__name__}")
        response = next_handler()
        audit.write(f"<- {type(request).__name__}")
        return response
```

`applies_to` bir behavior'ı tek bir request tipiyle sınırlar. Async pipeline'da
`handle()` `async def` olabilir ve `next_handler()` awaitable döner.

### Pre/post processor'lar

İkisi de yalnızca yan etkilidir ve `order` ile `applies_to` destekler. Post
processor cevabı *görür* ama değiştiremez — buna ihtiyacınız varsa bir
behavior'dan farklı bir değer döndürün.

```python
from py_mediatR import IRequestPreProcessor, IRequestPostProcessor

class AuditPre(IRequestPreProcessor):
    def process(self, request):                 # async def olabilir
        audit.write(type(request).__name__)

class MetricsPost(IRequestPostProcessor):
    def process(self, request, response):       # async def olabilir
        metrics.increment(type(request).__name__)
```

### Exception handler ile action farkı

| | Amaç | Dönüş değeri |
|---|---|---|
| `IExceptionHandler.handle` | hatayı bir yedek cevapla değiştirmek | değer döndürmek istisnayı yutar; `raise` yukarı taşır |
| `IExceptionAction.execute` | yalnızca gözlemek (log, alarm, metrik) | yok sayılır — istisna yukarı taşınmaya devam eder |

İkisi de `exception_type` (varsayılan `Exception`), `applies_to` ve `order`
üzerinden filtrelenir. Eşleşen tüm action'lar çalışır; değer döndüren **ilk**
eşleşen handler kazanır.

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

### Validator'lar (FluentValidation tarzı)

Doğrulama bağımlılık gerektiriyorsa onu request'in dışında tutun:

```python
from py_mediatR import IValidator, ValidationBehavior

class CreateUserValidator(IValidator):
    applies_to = CreateUser          # ya da tip demeti; None = hepsi
    def validate(self, request):
        if "@" not in request.email:
            raise ValueError(f"invalid e-mail: {request.email}")

mediator = Mediator(behaviors=[ValidationBehavior(validators=[CreateUserValidator()])])
```

Eşleşen validator'lar `order` sırasıyla, ardından request'in kendi `validate()`
metodu çalışır.

### Notification'lar

Olay başına birden çok abone, `order` ile sıralanır. Nasıl çalışacaklarını seçin:

```python
from py_mediatR import PublishStrategy

mediator.publish(UserRegistered(user_id="U-1"))                       # SEQUENTIAL
await mediator.publish_async(evt, strategy=PublishStrategy.PARALLEL_WHENALL)
await mediator.publish_async(evt, strategy=PublishStrategy.PARALLEL_NOWAIT)
```

| Strateji | Anlamı |
|---|---|
| `SEQUENTIAL` (varsayılan) | `order` sırasıyla teker teker; ilk hata yukarı taşınır |
| `PARALLEL_WHENALL` | eşzamanlı, hepsini bekler |
| `PARALLEL_NOWAIT` | ateşle-unut; task'lar güçlü referansla tutulur, GC edilemezler |

Abone hataları varsayılan olarak yukarı taşınır. Eski hoşgörülü davranış için
`Mediator(swallow_notification_errors=True)` verin. `polymorphic_publish=True`
ile bir alt sınıfı yayınlamak taban tip abonelerini de tetikler (.NET
kovaryansı).

### Streaming

`create_stream()` elemanları üretildikçe verir; `IStreamPipelineBehavior`
generator'ı sarar:

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

## Örnekler

Tek bir katmanlı çekirdeği paylaşan dört çalıştırılabilir uygulama
[`examples/`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/README.tr.md)
altındadır — konsol, FastAPI, Flask ve Django; her birinin kendi README'si var:

- [`examples/console_app`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/console_app/README.tr.md)
  — buradan başlayın: her biri kendi çağrı zinciri ağacını basan on adım
- [`examples/fastapi_app`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/fastapi_app/README.tr.md)
  — async route'lar, istek başına bir DI scope'u
- [`examples/flask_app`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/flask_app/README.tr.md)
  — senkron route'lar, açık scope'lar
- [`examples/django_app`](https://github.com/AyhanOztemel/py_mediatR/blob/main/examples/django_app/README.tr.md)
  — handler'larını tanımayan view'lar

Örnekler wheel içinde dağıtılmaz; depoda yer alır.

## Paket yapısı

Import'larınızı üst paketten yapın. Desteklenen tek yüzey budur:

```python
from py_mediatR import Mediator, IRequest, IPipelineBehavior
```

`__all__` içindeki her isim oradan yeniden dışa aktarılır; dolayısıyla aşağıdaki
yapı bir uygulama detayıdır — kaynağı okumanıza yardımcı olsun diye
belgelenmiştir, buradan import etmeniz için değil.

| Modül | İçeriği |
|---|---|
| `contracts.py` | `IRequest`, `IResponse`, `INotification`, `I*` arayüzleri, `PublishStrategy`, `@handler` / `@behavior` |
| `mediator.py` | `Mediator`, `ISender` / `IPublisher`, pipeline derlemesi, `send` / `publish` / `create_stream` |
| `di.py` | `ServiceContainer`, `ServiceScope`, `scoped_mediator`, `make_fastapi_mediator_dependency` |
| `discovery.py` | proje tarama, tip ipucuna dayalı handler çözümleme, `discover_handlers` |
| `behaviors.py` | hazır sekiz behavior |
| `cancellation.py` | `CancellationToken`, `CancellationTokenSource`, `current_cancellation_token` |
| `tracing.py` | `FlowNode`, `FlowTrace`, `trace_flow` — `mediator.trace()`'in bastığı ağaç |
| `coercion.py` | dict → dataclass/pydantic dönüşümü, sync-over-async köprüsü |
| `_config.py`, `_typechecks.py` | bayraklar, sentinel'ler, iç tip kontrolleri |

`py_mediatR.py_mediatR` hâlâ import edilebilir ve bölmeden önceki tüm isimleri
aynen sunar; tek modüllü yapıya göre yazılmış kod değişiklik gerektirmeden
çalışmaya devam eder.

## Lisans

MIT — Ayhan Öztemel
