# -*- coding: utf-8 -*-
"""Reports feature — async handlers, ambient CancellationToken, performance.

* FetchExchangeRates: ASYNC handler that also reads the ambient
  CancellationToken via current_cancellation_token() (the documented
  py_mediatR deviation from .NET's parameter-passing style).
* SlowReport: deliberately exceeds PerformanceBehavior's threshold_ms.
"""
import asyncio
import time
from dataclasses import dataclass

from py_mediatR import (
    IRequest, IResponse, CancellationToken, current_cancellation_token,
)


@dataclass
class FetchExchangeRates(IRequest):
    base: str


@dataclass
class FetchExchangeRatesResponse(IResponse):
    base: str
    rates: int
    had_ambient_token: bool


class FetchExchangeRatesHandler:
    async def handle(self, req: FetchExchangeRates) -> FetchExchangeRatesResponse:
        token = current_cancellation_token()  # ambient CT; .none if absent
        token.throw_if_cancellation_requested()  # cooperative cancel point
        await asyncio.sleep(0.005)  # simulated I/O
        return FetchExchangeRatesResponse(
            base=req.base, rates=31,
            had_ambient_token=token is not CancellationToken.none)


@dataclass
class SlowReport(IRequest):
    delay_ms: int


@dataclass
class SlowReportResponse(IResponse):
    waited_ms: int


class SlowReportHandler:
    def handle(self, req: SlowReport) -> SlowReportResponse:
        time.sleep(req.delay_ms / 1000.0)  # triggers perf warning (> threshold)
        return SlowReportResponse(waited_ms=req.delay_ms)


@dataclass
class ExportReport(IRequest):
    rows: int


@dataclass
class ExportReportResponse(IResponse):
    rows: int
    token_injected: bool


class ExportReportHandler:
    """Declares `cancellation_token` in handle() -> py_mediatR injects the
    active token DIRECTLY (.NET Handle(request, ct) style), no ambient lookup
    needed."""

    def handle(self, req: ExportReport,
               cancellation_token=None) -> ExportReportResponse:
        injected = (cancellation_token is not None
                    and cancellation_token is not CancellationToken.none)
        return ExportReportResponse(rows=req.rows, token_injected=injected)


@dataclass
class LongJob(IRequest):
    steps: int


class LongJobHandler:
    """Cooperative loop — cancelled mid-flight by cts.cancel_after()."""

    async def handle(self, req: LongJob) -> int:
        token = current_cancellation_token()
        for i in range(req.steps):
            token.throw_if_cancellation_requested()
            await asyncio.sleep(0.005)
        return req.steps
