# -*- coding: utf-8 -*-
"""py_mediatR — Flask ornegi.

Calistirma:  python flask_app/app.py   (examples/ klasorunden)
Tarayici :  http://127.0.0.1:8112

Flask senkron calistigi icin mediator.send() dogrudan route icinde kullanilir.
Scoped servisler icin her istekte mediator.create_scope(container) acilir.
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

from dataclasses import asdict

from flask import Flask, jsonify, request

from ecommerce.composition.bootstrap import build_container, build_mediator
from ecommerce.application.features.users.commands import CreateUser
from ecommerce.application.features.users.queries import GetUser
from ecommerce.application.features.products.queries import SearchProducts
from ecommerce.application.features.billing.commands import FindInvoice
from ecommerce.application.features.orders.commands import PlaceOrder

container = build_container()
mediator = build_mediator(container)

app = Flask(__name__)

INDEX_HTML = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>py_mediatR Flask Demo</title></head>
<body><h1>py_mediatR — Flask Demo</h1>
<p>Senkron rotalarda mediator.send() doğrudan çağrılır.</p>
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


@app.get("/")
def index():
    return INDEX_HTML


@app.get("/users/olustur")
def user_olustur():
    with mediator.trace() as flow:
        cevap = mediator.send(
            CreateUser(name=request.args.get("name", "Ayhan"),
                       email=request.args.get("email", "ayhan@ornek.com")))
    return jsonify(aciklama="CreateUser komutu islendi (DI ile handler)",
                   cevap=asdict(cevap),
                   cagri_zinciri=zincir(flow))


@app.get("/users/<user_id>")
def user_getir(user_id: str):
    with mediator.trace() as flow:
        cevap = mediator.send(GetUser(user_id=user_id))
    return jsonify(aciklama="GetUser sorgusu; dict cevap dataclass'a cevrildi",
                   cevap=asdict(cevap),
                   cagri_zinciri=zincir(flow))


@app.get("/products/ara")
def product_ara():
    with mediator.trace() as flow:
        cevap = mediator.send(
            SearchProducts(keyword=request.args.get("keyword", "laptop")))
    return jsonify(aciklama="SearchProducts onbelleklidir — ayni keyword "
                            "ikinci kez handler'i calistirmaz",
                   cevap=asdict(cevap),
                   cagri_zinciri=zincir(flow))


@app.get("/invoices/<invoice_id>")
def invoice_bul(invoice_id: str):
    with mediator.trace() as flow:
        cevap = mediator.send(FindInvoice(invoice_id=invoice_id))
    return jsonify(aciklama="Fatura yoksa IExceptionHandler found=False "
                            "iceren yedek cevap dondurur",
                   cevap=asdict(cevap),
                   cagri_zinciri=zincir(flow))


@app.get("/orders/ver")
def order_ver():
    with mediator.trace() as flow:
        with mediator.create_scope(container) as scoped:
            cevap = scoped.send(PlaceOrder(sku=request.args.get("sku", "SKU-7"),
                                           qty=int(request.args.get("qty", "1"))))
    return jsonify(aciklama="PlaceOrder transactional=True; scoped "
                            "OrderUnitOfWork istek bitince dispose edildi",
                   cevap=asdict(cevap),
                   cagri_zinciri=zincir(flow))


if __name__ == "__main__":
    print("py_mediatR: Flask demosu")
    print("Tarayicida acin: http://127.0.0.1:8112")
    app.run(host="127.0.0.1", port=8112, debug=False, use_reloader=False)
