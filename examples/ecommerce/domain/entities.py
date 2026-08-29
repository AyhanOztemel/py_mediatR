# -*- coding: utf-8 -*-
"""Domain layer — pure entities. No py_mediatR dependency here."""
from dataclasses import dataclass


@dataclass
class User:
    user_id: str
    name: str
    email: str


@dataclass
class Product:
    sku: str
    title: str
    price: float


@dataclass
class Order:
    order_id: str
    sku: str
    qty: int
    status: str = "NEW"


@dataclass
class Invoice:
    invoice_id: str
    order_id: str
    amount: float
