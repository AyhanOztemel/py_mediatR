# -*- coding: utf-8 -*-
"""Orders feature — commands. `transactional = True` activates
TransactionBehavior (begin/commit on success, rollback on failure)."""
from dataclasses import dataclass

from py_mediatR import IRequest, IResponse

from ecommerce.domain.entities import Order
from ecommerce.infrastructure.persistence.repositories import OrderUnitOfWork


@dataclass
class PlaceOrder(IRequest):
    sku: str
    qty: int
    transactional: bool = True  # -> TransactionBehavior wraps this request


@dataclass
class PlaceOrderResponse(IResponse):
    order_id: str
    total_qty: int


class PlaceOrderHandler:
    def __init__(self, uow: OrderUnitOfWork) -> None:
        self.uow = uow  # SCOPED dependency

    def handle(self, req: PlaceOrder) -> PlaceOrderResponse:
        order = Order(order_id=f"ORD-{req.sku}", sku=req.sku, qty=req.qty)
        self.uow.add(order)
        return PlaceOrderResponse(order_id=order.order_id, total_qty=req.qty)


@dataclass
class CancelOrder(IRequest):
    order_id: str


@dataclass
class CancelOrderResponse(IResponse):
    order_id: str
    status: str


class CancelOrderHandler:
    def handle(self, req: CancelOrder) -> CancelOrderResponse:
        return CancelOrderResponse(order_id=req.order_id, status="CANCELLED")
