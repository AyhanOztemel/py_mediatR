from django.urls import path
from examples.django_app.mediator_demo.views import (
    index,
    invoice_bul,
    order_ver,
    product_ara,
    user_getir,
    user_olustur,
)

urlpatterns = [
    path("", index),
    path("users/olustur", user_olustur),
    path("users/<str:user_id>", user_getir),
    path("products/ara", product_ara),
    path("invoices/<str:invoice_id>", invoice_bul),
    path("orders/ver", order_ver),
]
