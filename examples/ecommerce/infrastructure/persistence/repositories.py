# -*- coding: utf-8 -*-
"""Repositories — registered in the ServiceContainer with different lifetimes."""
import itertools
from typing import Dict, Optional

from ecommerce.domain.entities import User, Order

_scope_counter = itertools.count(1)


class InMemoryUserRepository:
    """Lifetime: SINGLETON — one shared instance for the whole app."""

    def __init__(self) -> None:
        self._users: Dict[str, User] = {}

    def add(self, user: User) -> None:
        self._users[user.user_id] = user

    def get(self, user_id: str) -> Optional[User]:
        return self._users.get(user_id)


class OrderUnitOfWork:
    """Lifetime: SCOPED — one instance per scope (e.g. per web request)."""

    def __init__(self) -> None:
        self.scope_no = next(_scope_counter)
        self.orders: Dict[str, Order] = {}

    def add(self, order: Order) -> None:
        self.orders[order.order_id] = order


class AuditWriter:
    """Lifetime: TRANSIENT — a fresh instance on every resolve."""

    def write(self, line: str) -> str:
        return f"audit-written:{line}"
