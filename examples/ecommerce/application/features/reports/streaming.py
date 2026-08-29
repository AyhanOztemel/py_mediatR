# -*- coding: utf-8 -*-
"""Reports feature — streaming (IStreamRequest -> async generator handler).
Consumed with `async for` via mediator.create_stream()."""
import asyncio
from dataclasses import dataclass

from py_mediatR import IStreamRequest


@dataclass
class StreamOrderFeed(IStreamRequest):
    count: int


class StreamOrderFeedHandler:
    async def handle(self, req: StreamOrderFeed):
        for i in range(req.count):
            await asyncio.sleep(0.001)
            yield {"seq": i, "order_id": f"ORD-{i:03d}"}
