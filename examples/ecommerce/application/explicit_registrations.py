"""Explicit (decorator) registration — the alternative to auto-discovery.

This module is deliberately OUTSIDE the auto-discovery scan path
(`application/features`): Ping/PingHandler reach the mediator ONLY through
the @handler decorator, and TagBehavior only through @behavior +
Mediator(discover_behaviors=True). bootstrap.py imports this module so the
decorators run before the Mediator is constructed.
"""
from dataclasses import dataclass

from py_mediatR import IPipelineBehavior, IRequest, IResponse, behavior, handler

from ecommerce.application.crosscutting.audit_log import AUDIT


@dataclass
class Ping(IRequest):
    payload: str


@dataclass
class PingResponse(IResponse):
    payload: str
    echoed: bool


@handler
class PingHandler:
    def handle(self, req: Ping) -> PingResponse:
        return PingResponse(payload=req.payload, echoed=True)


@behavior
class TagBehavior(IPipelineBehavior):
    """Discovered via @behavior + discover_behaviors=True; applies_to limits
    it to Ping so the rest of the pipeline is untouched."""
    applies_to = Ping
    order = -5

    def handle(self, request, next_handler):
        AUDIT.append(f"tag:{type(request).__name__}")
        return next_handler()
