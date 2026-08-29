# -*- coding: utf-8 -*-
"""Exception handling components.

* InvoiceNotFoundHandler — modern v6.4+ STATE-BASED handler:
  handle(request, exception, state) + state.set_handled(response).
  The pipeline swallows the exception and returns the fallback response.
* AlertExceptionAction — observes every exception WITHOUT swallowing it
  (.NET IRequestExceptionAction equivalent).
"""
from py_mediatR import IExceptionHandler, IExceptionAction, ExceptionHandlerState

from ecommerce.application.crosscutting.audit_log import AUDIT
from ecommerce.application.features.billing.commands import FindInvoiceResponse


class InvoiceNotFoundHandler(IExceptionHandler):
    exception_type = KeyError

    def handle(self, request, exception, state: ExceptionHandlerState):
        AUDIT.append(f"exc-handled:{type(exception).__name__}")
        AUDIT.append(f"state-type-ok:{isinstance(state, ExceptionHandlerState)}")
        state.set_handled(
            FindInvoiceResponse(invoice_id=getattr(request, "invoice_id", "?"),
                                amount=0.0, found=False))


class AlertExceptionAction(IExceptionAction):
    exception_type = Exception

    def execute(self, request, exception):
        AUDIT.append(f"alert:{type(request).__name__}:{type(exception).__name__}")
