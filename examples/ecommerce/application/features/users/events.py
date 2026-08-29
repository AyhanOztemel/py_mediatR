# -*- coding: utf-8 -*-
"""Users feature — notification (pub/sub) with ORDERED sync subscribers.
`order` controls execution order under PublishStrategy.SEQUENTIAL."""
from dataclasses import dataclass

from py_mediatR import INotification, INotificationHandler

from ecommerce.application.crosscutting.audit_log import AUDIT


@dataclass
class UserRegistered(INotification):
    user_id: str


class WelcomeEmailSubscriber(INotificationHandler):
    order = 0

    def handle(self, n: UserRegistered):
        AUDIT.append(f"welcome-email:{n.user_id}")


class CrmSyncSubscriber(INotificationHandler):
    order = 1

    def handle(self, n: UserRegistered):
        AUDIT.append(f"crm-sync:{n.user_id}")


class AnalyticsSubscriber(INotificationHandler):
    order = 2

    def handle(self, n: UserRegistered):
        AUDIT.append(f"analytics:{n.user_id}")


# --- Polymorphic publish (.NET covariance) ----------------------------------
# The subscriber listens to the BASE type; with polymorphic_publish=True,
# publishing the DERIVED AccountClosed also triggers it.

@dataclass
class AccountEvent(INotification):
    user_id: str


@dataclass
class AccountClosed(AccountEvent):
    reason: str = "user-request"


class AccountAuditSubscriber(INotificationHandler):
    def handle(self, n: AccountEvent):
        AUDIT.append(f"account-event:{type(n).__name__}:{n.user_id}")
