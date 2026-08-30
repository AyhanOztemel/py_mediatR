"""Django view'lari — her biri istegi mediator uzerinden isler."""
import logging
from dataclasses import asdict

from django.http import HttpResponse, JsonResponse
from ecommerce.application.features.billing.commands import FindInvoice
from ecommerce.application.features.orders.commands import PlaceOrder
from ecommerce.application.features.products.queries import SearchProducts
from ecommerce.application.features.users.commands import CreateUser
from ecommerce.application.features.users.queries import GetUser
from ecommerce.composition.bootstrap import build_container, build_mediator

logging.getLogger("mediatr").setLevel(logging.CRITICAL)

container = build_container()
mediator = build_mediator(container)

INDEX_HTML = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>py_mediatR Django Demo</title></head>
<body><h1>py_mediatR — Django Demo</h1>
<p>Her view isteği mediator üzerinden işler; view handler'ı tanımaz.</p>
<ul>
<li><a href="/users/olustur?name=Ayhan&email=ayhan@ornek.com">/users/olustur</a> — komut (CreateUser)</li>
<li><a href="/users/U-ayhan">/users/U-ayhan</a> — sorgu (GetUser)</li>
<li><a href="/products/ara?keyword=laptop">/products/ara</a> — önbellekli sorgu</li>
<li><a href="/invoices/INV-1">/invoices/INV-1</a> — fatura (INV-YOK ile fallback'i deneyin)</li>
<li><a href="/orders/ver?sku=SKU-7&qty=2">/orders/ver</a> — transactional komut (scope'lu)</li>
</ul></body></html>"""


def zincir(flow):
    """Cagri zincirini JSON'a konabilecek satir listesine cevirir."""
    return flow.render(unicode=True).splitlines()


def index(_request):
    return HttpResponse(INDEX_HTML)


def user_olustur(request):
    with mediator.trace() as flow:
        cevap = mediator.send(
            CreateUser(name=request.GET.get("name", "Ayhan"),
                       email=request.GET.get("email", "ayhan@ornek.com")))
    return JsonResponse({"aciklama": "CreateUser komutu islendi (DI ile handler)",
                         "cevap": asdict(cevap),
                         "cagri_zinciri": zincir(flow)})


def user_getir(request, user_id: str):
    with mediator.trace() as flow:
        cevap = mediator.send(GetUser(user_id=user_id))
    return JsonResponse({"aciklama": "GetUser sorgusu; dict cevap dataclass'a cevrildi",
                         "cevap": asdict(cevap),
                         "cagri_zinciri": zincir(flow)})


def product_ara(request):
    with mediator.trace() as flow:
        cevap = mediator.send(
            SearchProducts(keyword=request.GET.get("keyword", "laptop")))
    return JsonResponse({"aciklama": "SearchProducts onbelleklidir — ayni keyword "
                                     "ikinci kez handler'i calistirmaz",
                         "cevap": asdict(cevap),
                         "cagri_zinciri": zincir(flow)})


def invoice_bul(request, invoice_id: str):
    with mediator.trace() as flow:
        cevap = mediator.send(FindInvoice(invoice_id=invoice_id))
    return JsonResponse({"aciklama": "Fatura yoksa IExceptionHandler found=False "
                                     "iceren yedek cevap dondurur",
                         "cevap": asdict(cevap),
                         "cagri_zinciri": zincir(flow)})


def order_ver(request):
    with mediator.trace() as flow:
        with mediator.create_scope(container) as scoped:
            cevap = scoped.send(PlaceOrder(sku=request.GET.get("sku", "SKU-7"),
                                           qty=int(request.GET.get("qty", "1"))))
    return JsonResponse({"aciklama": "PlaceOrder transactional=True; scoped "
                                     "OrderUnitOfWork istek bitince dispose edildi",
                         "cevap": asdict(cevap),
                         "cagri_zinciri": zincir(flow)})
