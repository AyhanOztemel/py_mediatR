"""Orders feature — async notification subscribers.
Used with PublishStrategy.SEQUENTIAL and PARALLEL_WHENALL in main.py."""
import asyncio
from dataclasses import dataclass

from py_mediatR import INotification, INotificationHandler

from ecommerce.application.crosscutting.audit_log import AUDIT


@dataclass
class OrderShipped(INotification):
    order_id: str


class SmsSubscriber(INotificationHandler):
    order = 0

    async def handle(self, n: OrderShipped):
        await asyncio.sleep(0.001)
        AUDIT.append(f"sms:{n.order_id}")


class PushSubscriber(INotificationHandler):
    order = 1

    async def handle(self, n: OrderShipped):
        await asyncio.sleep(0.001)
        AUDIT.append(f"push:{n.order_id}")
