# -*- coding: utf-8 -*-
"""Orders feature — queries."""
from dataclasses import dataclass

from py_mediatR import IRequest, IResponse


@dataclass
class GetOrderStatus(IRequest):
    order_id: str


@dataclass
class GetOrderStatusResponse(IResponse):
    order_id: str
    status: str


class GetOrderStatusHandler:
    def handle(self, req: GetOrderStatus) -> GetOrderStatusResponse:
        return GetOrderStatusResponse(order_id=req.order_id, status="SHIPPED")
