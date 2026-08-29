# -*- coding: utf-8 -*-
"""py_mediatR — FastAPI ornegi.

Calistirma:  python fastapi_app/app.py   (examples/ klasorunden)
Tarayici :  http://127.0.0.1:8111

`make_fastapi_mediator_dependency` her HTTP istegi icin scope'lu bir
mediator uretir; scoped servisler istek bitince otomatik dispose edilir.
"""
import logging
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = EXAMPLES_DIR.parent / "src"
for p in (str(EXAMPLES_DIR), str(REPO_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.getLogger("mediatr").setLevel(logging.CRITICAL)

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from py_mediatR import Mediator, make_fastapi_mediator_dependency

from ecommerce.composition.bootstrap import build_container, build_mediator
from ecommerce.application.features.users.commands import CreateUser
from ecommerce.application.features.users.queries import GetUser
from ecommerce.application.features.products.queries import SearchProducts
from ecommerce.application.features.billing.commands import FindInvoice
from ecommerce.application.features.orders.commands import PlaceOrder
from ecommerce.application.features.reports.streaming import StreamOrderFeed

container = build_container()
base_mediator = build_mediator(container)
get_mediator = make_fastapi_mediator_dependency(base_mediator, container)

app = FastAPI(title="py_mediatR FastAPI Demo")

INDEX_HTML = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>py_mediatR FastAPI Demo</title></head>
<body><h1>py_mediatR — FastAPI Demo</h1>
<p>Her istek, istek başına scope'lu bir mediator üzerinden işlenir.</p>
<ul>
<li><a href="/users/olustur?name=Ayhan&email=ayhan@ornek.com">/users/olustur</a> — komut (CreateUser)</li>
<li><a href="/users/U-ayhan">/users/U-ayhan</a> — sorgu (GetUser, dict→dataclass)</li>
<li><a href="/products/ara?keyword=laptop">/products/ara</a> — önbellekli sorgu (SearchProducts)</li>
<li><a href="/invoices/INV-1">/invoices/INV-1</a> — fatura bul (INV-YOK ile fallback'i deneyin)</li>
<li><a href="/orders/ver?sku=SKU-7&qty=2">/orders/ver</a> — transactional komut (PlaceOrder)</li>
<li><a href="/orders/akis?count=5">/orders/akis</a> — streaming (create_stream)</li>
<li><a href="/docs">/docs</a> — Swagger arayüzü</li>
</ul></body></html>"""


def zincir(flow):
    """Cagri zincirini JSON'a konabilecek satir listesine cevirir."""
    return flow.render(unicode=True).splitlines()


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.get("/users/olustur")
async def user_olustur(name: str, email: str,
                       m: Mediator = Depends(get_mediator)):
    with m.trace() as flow:
        cevap = await m.send_async(CreateUser(name=name, email=email))
    return {"aciklama": "CreateUser komutu CreateUserHandler tarafindan islendi",
            "cevap": cevap,
            "cagri_zinciri": zincir(flow)}


@app.get("/users/{user_id}")
async def user_getir(user_id: str, m: Mediator = Depends(get_mediator)):
    with m.trace() as flow:
        cevap = await m.send_async(GetUser(user_id=user_id))
    return {"aciklama": "GetUser sorgusu; handler dict dondurdu, "
                        "py_mediatR dataclass'a cevirdi",
            "cevap": cevap,
            "cagri_zinciri": zincir(flow)}


@app.get("/products/ara")
async def product_ara(keyword: str, m: Mediator = Depends(get_mediator)):
    with m.trace() as flow:
        cevap = await m.send_async(SearchProducts(keyword=keyword))
    return {"aciklama": "SearchProducts onbelleklidir — ayni keyword ikinci "
                        "kez handler'i calistirmaz (CachingBehavior)",
            "cevap": cevap,
            "cagri_zinciri": zincir(flow)}


@app.get("/invoices/{invoice_id}")
async def invoice_bul(invoice_id: str, m: Mediator = Depends(get_mediator)):
    with m.trace() as flow:
        cevap = await m.send_async(FindInvoice(invoice_id=invoice_id))
    return {"aciklama": "Fatura yoksa KeyError firlar ama IExceptionHandler "
                        "found=False iceren yedek cevap dondurur",
            "cevap": cevap,
            "cagri_zinciri": zincir(flow)}


@app.get("/orders/ver")
async def order_ver(sku: str, qty: int = 1,
                    m: Mediator = Depends(get_mediator)):
    with m.trace() as flow:
        cevap = await m.send_async(PlaceOrder(sku=sku, qty=qty))
    return {"aciklama": "PlaceOrder transactional=True — TransactionBehavior "
                        "commit/rollback yonetir; OrderUnitOfWork scoped'dur",
            "cevap": cevap,
            "cagri_zinciri": zincir(flow)}


@app.get("/orders/akis")
async def order_akis(count: int = 5, m: Mediator = Depends(get_mediator)):
    with m.trace() as flow:
        elemanlar = [item async for item
                     in m.create_stream(StreamOrderFeed(count=count))]
    return {"aciklama": "create_stream() ile async generator'dan tek tek "
                        "toplanan elemanlar",
            "elemanlar": elemanlar,
            "cagri_zinciri": zincir(flow)}


if __name__ == "__main__":
    import uvicorn
    print("py_mediatR: FastAPI demosu")
    print("Tarayicida acin: http://127.0.0.1:8111")
    print("Swagger arayuzu: http://127.0.0.1:8111/docs")
    uvicorn.run(app, host="127.0.0.1", port=8111)
