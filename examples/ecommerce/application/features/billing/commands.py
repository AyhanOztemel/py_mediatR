# -*- coding: utf-8 -*-
"""Billing feature — retry + exception-handling demos.

* ChargeCard: PaymentGateway fails twice -> RetryBehavior retries until OK.
* FindInvoice: missing key raises KeyError -> InvoiceNotFoundHandler
  (state-based IExceptionHandler) returns a fallback response instead.
"""
from dataclasses import dataclass

from py_mediatR import IRequest, IResponse

from ecommerce.infrastructure.services.gateways import PaymentGateway


@dataclass
class ChargeCard(IRequest):
    amount: float


@dataclass
class ChargeCardResponse(IResponse):
    receipt: str
    attempts: int


class ChargeCardHandler:
    def __init__(self, gateway: PaymentGateway) -> None:
        self.gateway = gateway

    def handle(self, req: ChargeCard) -> ChargeCardResponse:
        receipt = self.gateway.charge(req.amount)  # raises twice, then OK
        return ChargeCardResponse(receipt=receipt, attempts=self.gateway.attempts)


@dataclass
class FindInvoice(IRequest):
    invoice_id: str


@dataclass
class FindInvoiceResponse(IResponse):
    invoice_id: str
    amount: float
    found: bool = True


class FindInvoiceHandler:
    DB = {"INV-1": 100.0, "INV-2": 250.0}

    def handle(self, req: FindInvoice) -> FindInvoiceResponse:
        return FindInvoiceResponse(invoice_id=req.invoice_id,
                                   amount=self.DB[req.invoice_id])  # KeyError if absent
