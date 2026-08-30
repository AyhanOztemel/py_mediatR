# py_mediatR — Örnekler (Türkçe)

> English version: [README.md](README.md)

Tek bir katmanlı uygulama çekirdeğini (`ecommerce/`, Clean-Architecture
tarzı, dikey dilimler) paylaşan dört çalıştırılabilir örnek uygulama:

| Uygulama | Çalıştırma (`examples/` içinden) | Ne gösterir |
|----------|----------------------------------|-------------|
| **[Konsol](console_app/README.tr.md)** | `python console_app/main.py` | 10 adımlık demo; her adım kendi çağrı zinciri ağacını yazar — buradan başlayın |
| **[FastAPI](fastapi_app/README.tr.md)** | `python fastapi_app/app.py` → http://127.0.0.1:8111 | `make_fastapi_mediator_dependency` (istek başına scope'lu mediator), async send, streaming endpoint, `/docs` Swagger |
| **[Flask](flask_app/README.tr.md)** | `python flask_app/app.py` → http://127.0.0.1:8112 | route içinde senkron `mediator.send()`, scoped servisler için istek başına `create_scope()` |
| **[Django](django_app/README.tr.md)** | `python django_app/manage.py` → http://127.0.0.1:8113 | Django view'ları içinde mediator, kendi kendine `runserver` başlatır |
| **Tam kapsam testi** | `python ecommerce/main.py` | ileri düzey: 52 `__all__` export'unun TAMAMINI çalıştıran 81 kontrol (doğrulama aracıdır, eğitim değildir) |
| **Başsız web testi** | `python -m ci_smoke` | üç web uygulamasını sunucu/port açmadan, süreç içi test client'larıyla çalıştırır |

Konsol uygulaması ve kapsam testi için kurulum gerekmez — `../src` dizinini
`sys.path`'e otomatik eklerler. Web uygulamaları için ek olarak:

```bash
pip install -r requirements.txt   # fastapi, uvicorn, flask, django
```

## Konsol uygulamasıyla başlayın

Çıktısı bilerek sadedir. Her adım isteği, cevabı ve o cevabı üreten çağrı
zincirini yazar:

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
  Ne oldu?    : Odeme servisi ilk 2 denemede ConnectionError verdi.
                RetryBehavior sessizce tekrar denedi; 3. denemede
                islem basarili oldu.
```

Hata metni yalnızca hatayı **fırlatan** düğümde açık yazılır; onu sadece
yukarı taşıyan üst düğümler `!! (propagated)` işaretiyle geçilir — aksi
hâlde derin bir pipeline'da aynı mesaj her satırda tekrarlanırdı.

10 adım: komut gönderme · sorgu (dict→dataclass dönüşümü) · doğrulama
hatası · yetki reddi · önbellek · yeniden deneme · hata yedeği (fallback) ·
olay yayınlama (pub/sub) · akış (streaming) · iptal (cancellation).
[Ağacı okuma rehberi →](console_app/README.tr.md)

## Web endpoint'leri (üç framework'te de aynı)

| Endpoint | Özellik |
|----------|---------|
| `/users/olustur?name=..&email=..` | komut + DI constructor injection |
| `/users/<id>` | sorgu, dict → dataclass dönüşümü |
| `/products/ara?keyword=..` | `CachingBehavior` (tekrar = önbellekten) |
| `/invoices/<id>` (`INV-YOK` deneyin) | `IExceptionHandler` yedek cevabı |
| `/orders/ver?sku=..&qty=..` | `TransactionBehavior` + scoped servisler |
| `/orders/akis?count=..` (yalnız FastAPI) | `create_stream()` ile streaming |

Her cevap ayrıca bir `cagri_zinciri` alanı taşır: o isteğin
`mediator.trace()` ile üretilmiş çağrı zinciri ağacı.

## Klasör yapısı

```
examples/
├── console_app/              # anlatımlı 10 adımlık demo (buradan başlayın)
│   ├── main.py
│   └── README.md / README.tr.md
├── fastapi_app/              # port 8111  (app.py + README[.tr].md)
├── flask_app/                # port 8112  (app.py + README[.tr].md)
├── django_app/               # port 8113
│   ├── manage.py             # argümansız çalıştırılınca runserver başlatır
│   ├── mediator_demo/        # minimal settings + urls + views
│   └── README.md / README.tr.md
├── ci_smoke.py               # üç web uygulaması için başsız sürücü
├── requirements.txt          # sadece web framework'leri (kütüphane sıfır bağımlı)
└── ecommerce/                # PAYLAŞILAN katmanlı çekirdek + tam kapsam testi
    ├── main.py               # 52 export'un tamamını doğrulayan 81 kontrol
    ├── domain/               # saf entity'ler
    ├── application/
    │   ├── crosscutting/     # behavior'lar, processor'lar, exception handler'lar
    │   └── features/         # dikey dilimler: users, orders, products,
    │                         #   billing, reports (otomatik keşfedilir)
    ├── infrastructure/       # repository'ler (DI ömürleri), gateway'ler
    └── composition/          # bootstrap.py — DI + Mediator kablolaması
```

## Tasarım notları

* **Composition root**: tüm kablolama `ecommerce/composition/bootstrap.py`
  içindedir; dört uygulama sadece `build_container()` + `build_mediator()`
  çağırır.
* **Dikey dilimler**: her feature klasörü kendi command, query ve
  event'lerine sahiptir; .NET'te MediatR'ın tipik kullanımını yansıtır.
* **Ambient cancellation**: .NET'ten farklı olarak py_mediatR
  `CancellationToken`'ı bir `contextvar` üzerinden taşır
  (`current_cancellation_token()`); `cancellation_token` parametresi
  bildiren handler'lara token yine doğrudan enjekte edilir.
* **Import yüzeyi**: buradaki her dosya yalnızca üst düzey `py_mediatR`
  paketinden import eder, alt modüllerinden (`py_mediatR.mediator` vb.) değil —
  bkz. [Paket yapısı](../README.tr.md#paket-yapısı).
* `ecommerce/main.py` testi kasıtlı `[PASS]` satırları ve bilinçli hata
  logları basar — bu bir kapsam doğrulama aracıdır. İnsan tarafından
  okunabilir anlatım için `console_app/main.py` kullanın.
