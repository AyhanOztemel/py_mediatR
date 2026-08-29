# console_app — çağrı zincirini okuyun

> English version: [README.md](README.md)

py_mediatR'ın ne yaptığını görmenin en hızlı yolu. On adım; web sunucusu yok,
veritabanı yok. Her adım isteği, cevabı ve o cevabı üreten **çağrı zinciri
ağacını** basar.

```bash
cd examples
python console_app/main.py
```

Standart kütüphane dışında bağımlılık gerektirmez. Handler'lar, behavior'lar ve
servisler paylaşılan [`ecommerce/`](../ecommerce) çekirdeğinden gelir; yani
burada gördüğünüz kablolama üç web örneğinin kullandığının aynısıdır.

## Ağaç nasıl okunur

Her adım çağrısını `mediator.trace()` içine alır:

```python
with mediator.trace() as flow:
    cevap = mediator.send(CreateUser(name="Ayhan", email="ayhan@ornek.com"))
print(flow.render())
```

`render()` soğanı yukarıdan aşağı basar — en dıştaki behavior önce, handler
merkezde:

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

`[...]` içindeki notlar, ilgili behavior'ın kendi düğümüne iliştirdiği
açıklamalardır; `!!` bir istisnayı işaret eder. Hata metni yalnızca onu
**fırlatan** düğümde açık yazılır — hatayı sadece yukarı taşıyan üst düğümler
`!! (propagated)` gösterir, aksi hâlde derin bir pipeline'da aynı mesaj her
satırda tekrarlanırdı.

## On adım

| # | Ne gösterir | Ağaçta görünen |
|---|---|---|
| 1 | `send(CreateUser)` | behavior sırasının tamamı, handler'ı saran pre/post processor'lar |
| 2 | `send(GetUser)` | handler `dict` döndürür; `GetUser` → `GetUserResponse` dönüşümü |
| 3 | doğrulama | `ValidationBehavior` hata fırlatır — dal handler'ın **üstünde** biter |
| 4 | yetkilendirme | `requires_permission` reddeder, ağaçta hiç handler düğümü oluşmaz |
| 5 | önbellek | ikinci çağrı `CachingBehavior   [CACHE HIT - handler NOT called]` ile biter |
| 6 | yeniden deneme | `RetryBehavior`'ın **üç** `TransactionBehavior` çocuğu vardır — ikisi başarısız, biri başarılı |
| 7 | exception handler | handler `KeyError` fırlatır, ardından `on-error: InvoiceNotFoundHandler` yedek cevabı döndürür |
| 8 | `publish` | tek olay, sırayla üç abone düğümü |
| 9 | `create_stream` | `stream(...)` kökü, handler'da `[3 item(s) yielded]` |
| 10 | iptal | `OperationCancelledError` tam olarak `LongJobHandler`'da işaretlenir |

Üzerinde durulmaya değer olan 6. adımdır. Yeniden denemeler loglarda genelde
görünmez; burada `RetryBehavior` altında üç kardeş alt ağaç olarak dururlar:

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

## Konsol kodlaması

`render(show_timing=True, unicode=None)`. `unicode=None` iken karakterler
konsol yoklanarak seçilir: kodlama destekliyorsa `├─ └─ │`, desteklemiyorsa
`|- \`- |` — böylece ağaç Windows cp1254 terminalinde `UnicodeEncodeError`
fırlatmak yerine sağ salim basılır. `unicode=True` / `unicode=False` ile
zorlayın ya da `PYTHONIOENCODING=utf-8` ile çalıştırın. `show_timing=False`,
`(0.69 ms)` eklerini kaldırır; testte ağaç karşılaştırırken istediğiniz budur.

## Sonraki adım

- Kurallar, behavior tabloları ve tam pipeline referansı:
  [kök README](../../README.tr.md)
- Bu adımların sürdüğü katmanlı çekirdek: [`ecommerce/`](../ecommerce)
- Aynı çekirdeğin HTTP hâli: [fastapi_app](../fastapi_app/README.tr.md) ·
  [flask_app](../flask_app/README.tr.md) · [django_app](../django_app/README.tr.md)
