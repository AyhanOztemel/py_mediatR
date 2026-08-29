# -*- coding: utf-8 -*-
"""Fake DB session used by TransactionBehavior. The factory opens the
transaction; the behavior calls commit (success) / rollback (error) + close."""
from ecommerce.application.crosscutting.audit_log import AUDIT


class FakeSession:
    def __init__(self) -> None:
        AUDIT.append("tx:open")

    def commit(self) -> None:
        AUDIT.append("tx:commit")

    def rollback(self) -> None:
        AUDIT.append("tx:rollback")

    def close(self) -> None:
        AUDIT.append("tx:close")


def session_factory() -> FakeSession:
    return FakeSession()
