"""Regression tests for the v6.7 hardening pass and the flow trace."""
import asyncio
import logging
import sys
from typing import Optional

import pytest

from py_mediatR import (
    DIResolutionError, IPipelineBehavior, IRequest, INotification, Mediator,
    ServiceContainer, SyncBridgeTimeoutError, TransactionBehavior,
    TransactionCleanupError, trace_flow,
)


# --------------------------------------------------------------------------- #
# DI: captive dependency
# --------------------------------------------------------------------------- #
class DbSession:
    pass


class CacheSingleton:
    def __init__(self, session: DbSession) -> None:
        self.session = session


def test_singleton_cannot_capture_a_scoped_service():
    c = ServiceContainer()
    c.register_scoped(DbSession)
    c.register_singleton(CacheSingleton)

    with pytest.raises(DIResolutionError, match="Captive dependency"):
        c.resolve(CacheSingleton)


def test_scoped_service_still_resolves_inside_a_scope():
    c = ServiceContainer()
    c.register_scoped(DbSession)
    with c.create_scope() as scope:
        assert isinstance(scope.resolve(DbSession), DbSession)


# --------------------------------------------------------------------------- #
# DI: Optional / PEP 604 hints
# --------------------------------------------------------------------------- #
class OptionalOld:
    def __init__(self, session: Optional[DbSession]) -> None:
        self.session = session


class OptionalNew:
    def __init__(self, session: "DbSession | None") -> None:
        self.session = session


@pytest.mark.parametrize("cls", [OptionalOld, OptionalNew])
def test_optional_hints_are_unwrapped_and_injected(cls):
    c = ServiceContainer()
    c.register_transient(DbSession)
    c.register_transient(cls)
    assert isinstance(c.resolve(cls).session, DbSession)


class NeedsConfig:
    def __init__(self, dsn: str) -> None:  # str is not something DI can supply
        self.dsn = dsn


class OptionalUnbuildable:
    def __init__(self, dep: Optional[NeedsConfig]) -> None:
        self.dep = dep


class OptionalUnbuildableNew:
    def __init__(self, dep: "NeedsConfig | None") -> None:
        self.dep = dep


@pytest.mark.parametrize("cls", [OptionalUnbuildable, OptionalUnbuildableNew])
def test_unresolvable_optional_becomes_none_instead_of_failing(cls):
    c = ServiceContainer()
    c.register_transient(cls)
    assert c.resolve(cls).dep is None


# --------------------------------------------------------------------------- #
# TransactionBehavior: no more silent cleanup failures
# --------------------------------------------------------------------------- #
class Work(IRequest):
    transactional = True


class BoomHandler:
    def handle(self, req):
        raise ValueError("handler exploded")


class RollbackAlsoFails:
    def rollback(self):
        raise RuntimeError("rollback exploded")

    def close(self):
        pass

    def commit(self):
        pass


def _mediator_with_transaction(session, **kwargs):
    m = Mediator(auto_discover=False)
    m.register_handler(Work, BoomHandler())
    m.add_behavior(TransactionBehavior(lambda: session, **kwargs))
    return m


def test_cleanup_failure_is_never_silent(caplog):
    m = _mediator_with_transaction(RollbackAlsoFails())
    with caplog.at_level(logging.ERROR, logger="mediatr.transaction"):
        with pytest.raises(ValueError) as info:
            m.send(Work())
    assert "rollback() FAILED" in caplog.text

    # Exception.add_note is 3.11+; on 3.10 the log above is the only channel.
    if sys.version_info >= (3, 11):
        notes = " ".join(getattr(info.value, "__notes__", []))
        assert "rollback() FAILED" in notes


def test_cleanup_failure_can_be_promoted_to_an_error():
    m = _mediator_with_transaction(RollbackAlsoFails(),
                                   raise_on_cleanup_failure=True)
    with pytest.raises(TransactionCleanupError):
        m.send(Work())


class AsyncSessionLike:
    async def rollback(self):
        pass

    async def commit(self):
        pass

    async def close(self):
        pass


def test_async_session_through_sync_send_in_a_loop_is_refused():
    m = Mediator(auto_discover=False)
    m.register_handler(Work, BoomHandler())
    m.add_behavior(TransactionBehavior(AsyncSessionLike))

    async def run():
        with pytest.raises(TypeError, match="async session"):
            m.send(Work())

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Sync-over-async bridge budget
# --------------------------------------------------------------------------- #
def test_sync_bridge_gives_up_instead_of_blocking_forever(monkeypatch):
    from py_mediatR import py_mediatR as impl

    monkeypatch.setenv("MEDIATR_SYNC_BRIDGE_TIMEOUT", "0.05")

    async def never():
        await asyncio.sleep(30)

    # The bridge thread only comes into play when a loop is already running.
    async def run():
        with pytest.raises(SyncBridgeTimeoutError):
            impl._sync_run_coro(never())

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Discovery failures are audible
# --------------------------------------------------------------------------- #
def test_discovery_import_failures_are_logged(tmp_path, caplog):
    broken = tmp_path / "broken_handler.py"
    broken.write_text("import a_module_that_does_not_exist\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="mediatr.discovery"):
        Mediator(scan_paths=[str(tmp_path)])

    assert any("broken_handler" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# Flow trace
# --------------------------------------------------------------------------- #
class Echo(IRequest):
    pass


class EchoHandler:
    def handle(self, req):
        return "echo"


class Marker(IPipelineBehavior):
    def handle(self, request, next_handler):
        return next_handler()


class Fired(INotification):
    pass


class SubA:
    def handle(self, note):
        pass


class SubB:
    def handle(self, note):
        pass


def _traced_mediator():
    m = Mediator(auto_discover=False)
    m.register_handler(Echo, EchoHandler())
    m.add_behavior(Marker())
    m.register_notification_handler(Fired, SubA())
    m.register_notification_handler(Fired, SubB())
    return m


def test_trace_records_the_behavior_handler_chain():
    m = _traced_mediator()
    with m.trace() as flow:
        assert m.send(Echo()) == "echo"

    labels = [(n.kind, n.label) for n in flow.steps()]
    assert labels == [("send", "Echo"), ("behavior", "Marker"),
                      ("handler", "EchoHandler")]
    assert "HANDLER: EchoHandler" in flow.render()


def test_trace_records_every_subscriber_of_a_publish():
    m = _traced_mediator()
    with m.trace() as flow:
        m.publish(Fired())

    subs = [n.label for n in flow.steps() if n.kind == "notification"]
    assert subs == ["SubA", "SubB"]


def test_trace_marks_where_an_exception_originated():
    m = Mediator(auto_discover=False)
    m.register_handler(Work, BoomHandler())
    m.add_behavior(Marker())

    with trace_flow() as flow:
        with pytest.raises(ValueError):
            m.send(Work())

    rendered = flow.render(unicode=False)
    # Spelled out once, at the handler; ancestors only say it passed through.
    assert rendered.count("handler exploded") == 1
    assert "(propagated)" in rendered


def test_tracing_is_inert_when_no_trace_is_active():
    m = _traced_mediator()
    assert m.send(Echo()) == "echo"  # must not raise or record anything

    with m.trace() as flow:
        pass
    assert flow.render() == ""


def test_render_falls_back_to_ascii_glyphs():
    m = _traced_mediator()
    with m.trace() as flow:
        m.send(Echo())

    ascii_art = flow.render(unicode=False)
    assert "`- " in ascii_art
    assert "└" not in ascii_art
