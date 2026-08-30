"""py_mediatR — basit ve anlasilir konsol ornegi.

Calistirma:  python console_app/main.py   (examples/ klasorunden)
Her adim ne gonderildigini, ne dondugunu ve neden oldugunu tek tek anlatir.
Katmanli uygulama cekirdegi `ecommerce/` klasorunden yeniden kullanilir.
"""
import asyncio
import logging
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1]          # .../examples
REPO_SRC = EXAMPLES_DIR.parent / "src"                      # .../src (kurulum gerekmez)
for p in (str(EXAMPLES_DIR), str(REPO_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Kutuphanenin kendi log satirlari bu ornekte kafa karistirmasin diye kapatilir.
logging.basicConfig(level=logging.WARNING)
logging.getLogger("mediatr").setLevel(logging.CRITICAL)

from ecommerce.application.crosscutting.audit_log import AUDIT
from ecommerce.application.features.billing.commands import ChargeCard, FindInvoice
from ecommerce.application.features.products.queries import CALL_COUNTS, SearchProducts
from ecommerce.application.features.reports.queries import LongJob
from ecommerce.application.features.reports.streaming import StreamOrderFeed
from ecommerce.application.features.users.commands import CreateUser, DeleteUser
from ecommerce.application.features.users.events import UserRegistered
from ecommerce.application.features.users.queries import GetUser
from ecommerce.composition.bootstrap import build_container, build_mediator
from py_mediatR import (
    CancellationTokenSource,
    OperationCancelledError,
    UnauthorizedError,
)


def adim(no: int, baslik: str) -> None:
    print()
    print("-" * 64)
    print(f"[Adim {no}] {baslik}")
    print("-" * 64)


def satir(etiket: str, deger) -> None:
    print(f"  {etiket:<12}: {deger}")


def agac(flow) -> None:
    """Cagri zincirini agac olarak yazar: kim kimi tetikledi."""
    metin = flow.render()
    if not metin:
        return
    print("  Cagri zinciri:")
    for line in metin.splitlines():
        print(f"    {line}")


def main() -> None:
    print("=" * 64)
    print("  py_mediatR — Konsol Ornegi (adim adim, anlasilir cikti)")
    print("=" * 64)

    container = build_container()
    mediator = build_mediator(container)

    # ------------------------------------------------------------------
    adim(1, "Komut gonderme — mediator.send(CreateUser)")
    istek = CreateUser(name="Ayhan", email="ayhan@ornek.com")
    with mediator.trace() as flow:
        cevap = mediator.send(istek)
    satir("Gonderilen", istek)
    satir("Donen cevap", cevap)
    agac(flow)
    print("  Ne oldu?    : Mediator komutu CreateUserHandler'a iletti.")
    print("                Handler'in ihtiyac duydugu repository ve e-posta")
    print("                servisi DI container'dan otomatik enjekte edildi.")

    # ------------------------------------------------------------------
    adim(2, "Sorgu gonderme — mediator.send(GetUser)")
    istek = GetUser(user_id="U-ayhan")
    with mediator.trace() as flow:
        cevap = mediator.send(istek)
    satir("Gonderilen", istek)
    satir("Donen cevap", cevap)
    agac(flow)
    print("  Ne oldu?    : Handler duz bir dict dondurdu; py_mediatR bunu")
    print("                otomatik olarak GetUserResponse dataclass'ina cevirdi.")

    # ------------------------------------------------------------------
    adim(3, "Dogrulama — gecersiz e-posta ValidationBehavior'da yakalanir")
    istek = CreateUser(name="Hatali", email="epostadegil")
    satir("Gonderilen", istek)
    with mediator.trace() as flow:
        try:
            mediator.send(istek)
            hata = None
        except ValueError as e:
            hata = e
    if hata is None:
        print("  Ne oldu?    : BEKLENMEDIK — hata firlamadi!")
    else:
        satir("Firlayan hata", f"ValueError: {hata}")
        agac(flow)
        print("  Ne oldu?    : ValidationBehavior, istegin validate() metodunu")
        print("                calistirdi; e-posta gecersiz oldugu icin komut")
        print("                handler'a HIC ULASMADAN reddedildi.")

    # ------------------------------------------------------------------
    adim(4, "Yetkilendirme — izin yoksa AuthorizationBehavior engeller")
    istek = DeleteUser(user_id="U-ayhan")
    satir("Gonderilen", istek)
    with mediator.trace() as flow:
        try:
            mediator.send(istek)
            hata = None
        except UnauthorizedError as e:
            hata = e
    if hata is None:
        print("  Ne oldu?    : BEKLENMEDIK — hata firlamadi!")
    else:
        satir("Firlayan hata", f"UnauthorizedError: {hata}")
        agac(flow)
        print("  Ne oldu?    : DeleteUser komutu 'users.delete' izni istiyor;")
        print("                mevcut kullanicida bu izin yok. Komut handler'a")
        print("                ulasmadan geri cevrildi.")

    # ------------------------------------------------------------------
    adim(5, "Onbellek (cache) — ayni sorgu ikinci kez handler'i CALISTIRMAZ")
    istek = SearchProducts(keyword="laptop")
    with mediator.trace() as flow:
        mediator.send(istek)
        ikinci = mediator.send(istek)
    satir("Gonderilen", f"{istek}  (arka arkaya 2 kez)")
    satir("Donen cevap", ikinci)
    satir("Handler", f"{CALL_COUNTS['search']} kez calisti (2 degil!)")
    agac(flow)
    print("  Ne oldu?    : CachingBehavior ilk cevabi sakladi; ikinci cagri")
    print("                handler'a gitmeden dogrudan onbellekten dondu.")

    # ------------------------------------------------------------------
    adim(6, "Yeniden deneme (retry) — gecici hatada otomatik tekrar")
    istek = ChargeCard(amount=49.90)
    with mediator.trace() as flow:
        cevap = mediator.send(istek)
    satir("Gonderilen", istek)
    satir("Donen cevap", cevap)
    agac(flow)
    print("  Ne oldu?    : Odeme servisi ilk 2 denemede ConnectionError verdi.")
    print(f"                RetryBehavior sessizce tekrar denedi; {cevap.attempts}.")
    print("                denemede islem basarili oldu. Cagiran kod hicbir")
    print("                hata gormedi.")

    # ------------------------------------------------------------------
    adim(7, "Hata yedegi (fallback) — olmayan fatura uygulamayi COKERTMEZ")
    istek = FindInvoice(invoice_id="INV-YOK")
    with mediator.trace() as flow:
        cevap = mediator.send(istek)
    satir("Gonderilen", istek)
    satir("Donen cevap", cevap)
    agac(flow)
    print("  Ne oldu?    : Handler KeyError firlatti ama uygulama COKMEDI.")
    print("                InvoiceNotFoundHandler (IExceptionHandler) hatayi")
    print("                yakaladi ve found=False iceren guvenli bir yedek")
    print("                cevap dondurdu.")

    # ------------------------------------------------------------------
    adim(8, "Olay yayinlama — mediator.publish ile 3 abone sirayla calisir")
    AUDIT.clear()
    olay = UserRegistered(user_id="U-ayhan")
    with mediator.trace() as flow:
        mediator.publish(olay)
    satir("Yayinlanan", olay)
    satir("Aboneler", " -> ".join(AUDIT))
    agac(flow)
    print("  Ne oldu?    : Tek bir olay yayinlandi; hosgeldin e-postasi, CRM")
    print("                senkronu ve analitik aboneleri sirayla tetiklendi.")
    print("                Yayinlayan kod abonelerin varligindan habersizdir.")

    # ------------------------------------------------------------------
    adim(9, "Akis (streaming) — sonuclar tek tek, geldikce islenir")

    async def akis_demo():
        elemanlar = []
        async for item in mediator.create_stream(StreamOrderFeed(count=3)):
            elemanlar.append(item)
        return elemanlar

    with mediator.trace() as flow:
        elemanlar = asyncio.run(akis_demo())
    satir("Gonderilen", "StreamOrderFeed(count=3)")
    for e in elemanlar:
        satir("Gelen eleman", e)
    agac(flow)
    print("  Ne oldu?    : create_stream() ile sonuclar liste halinde degil,")
    print("                async generator ile TEK TEK aktarildi (buyuk veri")
    print("                setlerinde bellek dostu).")

    # ------------------------------------------------------------------
    adim(10, "Iptal (cancellation) — uzun islem zaman asiminda durdurulur")

    async def iptal_demo():
        cts = CancellationTokenSource()
        cts.cancel_after(0.02)  # 20 ms sonra iptal et
        try:
            await mediator.send_async(LongJob(steps=50),
                                      cancellation_token=cts.token)
            return "tamamlandi (BEKLENMEDIK)"
        except OperationCancelledError:
            return "OperationCancelledError — islem yarida kesildi"

    with mediator.trace() as flow:
        sonuc = asyncio.run(iptal_demo())
    satir("Gonderilen", "LongJob(steps=50)  (zaman asimi: 20 ms)")
    satir("Sonuc", sonuc)
    agac(flow)
    print("  Ne oldu?    : .NET'teki CancellationToken gibi: islem her adimda")
    print("                iptal isteginin gelip gelmedigini kontrol etti ve")
    print("                zaman asiminda kendini guvenle sonlandirdi.")

    # ------------------------------------------------------------------
    print()
    print("=" * 64)
    print("  Bitti — 10 adimin tamami calisti.")
    print("  Katmanli cekirdek: ecommerce/  |  Web ornekleri: fastapi_app/,")
    print("  flask_app/, django_app/  |  Tam kapsam testi: ecommerce/main.py")
    print("=" * 64)


if __name__ == "__main__":
    main()
