import asyncio

import pytest
from py_mediatR import (
    CancellationTokenSource,
    DIResolutionError,
    INotification,
    IRequest,
    IStreamRequest,
    Mediator,
    OperationCancelledError,
    ServiceContainer,
    current_cancellation_token,
    scoped_mediator,
)


class Ping(IRequest):
    pass


class PingHandler:
    def handle(self, req: Ping, cancellation_token=None):
        return ("pong", cancellation_token.is_cancellation_requested)


class APing(IRequest):
    pass


class APingHandler:
    async def handle(self, req: APing):
        return "apong"


class Nums(IStreamRequest):
    pass


class NumsHandler:
    async def handle(self, req: Nums):
        for i in range(10):
            yield i


class Evt(INotification):
    pass


@pytest.fixture
def mediator():
    m = Mediator(auto_discover=False)
    m.register_handler(Ping, PingHandler())
    m.register_handler(APing, APingHandler())
    m.register_handler(Nums, NumsHandler())
    return m


def test_send(mediator):
    assert mediator.send(Ping()) == ("pong", False)


def test_send_async(mediator):
    assert asyncio.run(mediator.send_async(APing())) == "apong"


def test_notification(mediator):
    seen = []

    class EvtHandler:
        def handle(self, n: Evt):
            seen.append(1)

    mediator.register_notification_handler(Evt, EvtHandler())
    mediator.publish(Evt())
    assert seen == [1]


def test_cancellation(mediator):
    cts = CancellationTokenSource()
    cts.cancel()
    with pytest.raises(OperationCancelledError):
        mediator.send(Ping(), cancellation_token=cts.token)


def test_ct_injection(mediator):
    cts = CancellationTokenSource()
    assert mediator.send(Ping(), cancellation_token=cts.token) == ("pong", False)


def test_stream_cancellation(mediator):
    async def run():
        cts = CancellationTokenSource()
        got = []
        with pytest.raises(OperationCancelledError):
            async for i in mediator.create_stream(Nums(),
                                                  cancellation_token=cts.token):
                got.append(i)
                if i == 2:
                    cts.cancel()
        assert got == [0, 1, 2]
    asyncio.run(run())


class Config:
    pass


class DbSession:
    def __init__(self, config: Config):
        self.config = config
        self.closed = False

    def close(self):
        self.closed = True


def test_di_scoped():
    c = ServiceContainer()
    c.register_singleton(Config)
    c.register_scoped(DbSession)

    with pytest.raises(DIResolutionError):
        c.resolve(DbSession)

    with c.create_scope() as scope:
        s1 = scope.resolve(DbSession)
        assert scope.resolve(DbSession) is s1
    assert s1.closed


def test_scoped_mediator():
    c = ServiceContainer()
    c.register_singleton(Config)
    m = Mediator(auto_discover=False, handler_factory=c)
    m.register_handler(Ping, PingHandler)
    with scoped_mediator(m, c) as sm:
        assert sm.send(Ping()) == ("pong", False)


def test_linked_tokens():
    a, b = CancellationTokenSource(), CancellationTokenSource()
    linked = CancellationTokenSource.create_linked(a.token, b.token)
    b.cancel()
    assert linked.token.is_cancellation_requested


# ---- v6.4 regression tests --------------------------------------------------

from dataclasses import dataclass, field

from py_mediatR import CachingBehavior, IExceptionHandler, IPipelineBehavior


def test_notification_errors_propagate_by_default(mediator):
    class Boom(INotification):
        pass

    class BoomHandler:
        def handle(self, n):
            raise RuntimeError("boom")

    mediator.register_notification_handler(Boom, BoomHandler())
    with pytest.raises(RuntimeError):
        mediator.publish(Boom())


def test_sync_behavior_async_chain_same_loop():
    class Q(IRequest):
        pass

    class QH:
        async def handle(self, req):
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            loop.call_soon(fut.set_result, "ok")
            return await fut

    class PassThrough(IPipelineBehavior):
        def handle(self, request, next_handler):
            return next_handler()

    m = Mediator(auto_discover=False, behaviors=[PassThrough()])
    m.register_handler(Q, QH())
    assert asyncio.run(m.send_async(Q())) == "ok"


class _Fail(IRequest):
    pass


class _FailHandler:
    def handle(self, req):
        raise ValueError("x")


def test_exception_handler_none_reraises():
    class LegacyNone(IExceptionHandler):
        exception_type = ValueError

        def handle(self, request, exc):
            return None

    m = Mediator(auto_discover=False, exception_handlers=[LegacyNone()])
    m.register_handler(_Fail, _FailHandler())
    with pytest.raises(ValueError):
        m.send(_Fail())


def test_exception_handler_state():
    class StateH(IExceptionHandler):
        exception_type = ValueError

        def handle(self, request, exc, state):
            state.set_handled("fallback")

    m = Mediator(auto_discover=False, exception_handlers=[StateH()])
    m.register_handler(_Fail, _FailHandler())
    assert m.send(_Fail()) == "fallback"


def test_caching_unhashable_dataclass_field():
    @dataclass
    class CQ(IRequest):
        cacheable = True
        items: list = field(default_factory=lambda: [1, 2])

    class CQH:
        calls = 0

        def handle(self, req):
            CQH.calls += 1
            return sum(req.items)

    m = Mediator(auto_discover=False, behaviors=[CachingBehavior()])
    m.register_handler(CQ, CQH())
    assert m.send(CQ()) == 3
    assert m.send(CQ()) == 3
    assert CQH.calls == 1


# ---- v6.5 regression tests --------------------------------------------------

from py_mediatR import CancellationTokenRegistration


def test_cache_same_repr_different_content_no_collision():
    @dataclass
    class Q(IRequest):
        cacheable = True
        data: dict = field(default_factory=dict)

    class QH:
        calls = 0

        def handle(self, req):
            QH.calls += 1
            return sum(req.data["xs"])

    m = Mediator(auto_discover=False, behaviors=[CachingBehavior()])
    m.register_handler(Q, QH())
    assert m.send(Q(data={"xs": [1, 2]})) == 3
    assert m.send(Q(data={"xs": [5, 6]})) == 11   # farkli icerik karismamali
    assert m.send(Q(data={"xs": [1, 2]})) == 3    # cache hit
    assert QH.calls == 2


def test_cache_max_size_bounds():
    with pytest.raises(ValueError):
        CachingBehavior(max_size=-1)

    @dataclass
    class Q(IRequest):
        cacheable = True
        x: int = 1

    class QH:
        calls = 0

        def handle(self, req):
            QH.calls += 1
            return req.x

    m = Mediator(auto_discover=False, behaviors=[CachingBehavior(max_size=0)])
    m.register_handler(Q, QH())
    assert m.send(Q()) == 1
    assert m.send(Q()) == 1
    assert QH.calls == 2  # cache devre disi


def test_ct_registration_dispose():
    cts = CancellationTokenSource()
    fired = []
    reg = cts.token.register(lambda: fired.append(1))
    assert isinstance(reg, CancellationTokenRegistration)
    reg.dispose()
    cts.cancel()
    assert fired == []


def test_linked_source_cleans_parent_callbacks():
    a, b = CancellationTokenSource(), CancellationTokenSource()
    linked = CancellationTokenSource.create_linked(a.token, b.token)
    b.cancel()
    assert linked.token.is_cancellation_requested
    assert len(a._callbacks) == 0

    a2, b2 = CancellationTokenSource(), CancellationTokenSource()
    linked2 = CancellationTokenSource.create_linked(a2.token, b2.token)
    linked2.dispose()
    assert len(a2._callbacks) == 0 and len(b2._callbacks) == 0
    assert not linked2.token.is_cancellation_requested


def test_override_handler_non_lifo():
    class P(IRequest):
        pass

    def mk(name):
        class H:
            def handle(self, req):
                return name
        return H()

    m = Mediator(auto_discover=False)
    m.register_handler(P, mk("base"))
    cm1 = m.override_handler(P, mk("one"))
    cm1.__enter__()
    cm2 = m.override_handler(P, mk("two"))
    cm2.__enter__()
    assert m.send(P()) == "two"
    cm1.__exit__(None, None, None)      # LIFO disi kapanis
    assert m.send(P()) == "two"
    cm2.__exit__(None, None, None)
    assert m.send(P()) == "base"


# ---- v6.6 regression tests --------------------------------------------------

from py_mediatR import IPipelineBehavior as _IPB
from py_mediatR import IRequestPostProcessor, IRequestPreProcessor


def test_cache_fixed_hash_no_collision():
    class HashRequest(IRequest):
        cacheable = True

        def __init__(self, value):
            self.value = value

        def __hash__(self):
            return 7  # kasitli sabit hash

        def __eq__(self, other):
            return (isinstance(other, HashRequest)
                    and other.value == self.value)

    class H:
        calls = 0

        def handle(self, req):
            H.calls += 1
            return req.value

    m = Mediator(auto_discover=False, behaviors=[CachingBehavior()])
    m.register_handler(HashRequest, H())
    assert m.send(HashRequest("A")) == "A"
    assert m.send(HashRequest("B")) == "B"
    assert m.send(HashRequest("A")) == "A"
    assert H.calls == 2


def test_cache_key_protocol():
    class CKReq(IRequest):
        cacheable = True

        def __init__(self, v):
            self.v = v

        def cache_key(self):
            return self.v

    class H:
        calls = 0

        def handle(self, req):
            H.calls += 1
            return req.v * 2

    m = Mediator(auto_discover=False, behaviors=[CachingBehavior()])
    m.register_handler(CKReq, H())
    assert m.send(CKReq(3)) == 6
    assert m.send(CKReq(3)) == 6
    assert m.send(CKReq(4)) == 8
    assert H.calls == 2


def test_ambient_ct_in_pipeline_components():
    seen = {}

    class B(_IPB):
        def handle(self, request, next_handler):
            seen["behavior"] = current_cancellation_token().can_be_cancelled
            return next_handler()

    class Pre(IRequestPreProcessor):
        def process(self, request):
            seen["pre"] = current_cancellation_token().can_be_cancelled

    class Post(IRequestPostProcessor):
        def process(self, request, response):
            seen["post"] = current_cancellation_token().can_be_cancelled

    class P(IRequest):
        pass

    class H:
        def handle(self, req):
            return "ok"

    m = Mediator(auto_discover=False, behaviors=[B()],
                 pre_processors=[Pre()], post_processors=[Post()])
    m.register_handler(P, H())
    cts = CancellationTokenSource()
    assert m.send(P(), cancellation_token=cts.token) == "ok"
    assert seen == {"behavior": True, "pre": True, "post": True}
    seen.clear()
    assert m.send(P()) == "ok"
    assert seen == {"behavior": False, "pre": False, "post": False}


def test_ambient_ct_parallel_task_isolation():
    class AQ(IRequest):
        pass

    class AQH:
        async def handle(self, req):
            await asyncio.sleep(0.01)
            return current_cancellation_token().can_be_cancelled

    m = Mediator(auto_discover=False)
    m.register_handler(AQ, AQH())

    async def run():
        c = CancellationTokenSource()
        return await asyncio.gather(
            m.send_async(AQ(), cancellation_token=c.token),
            m.send_async(AQ()),
            m.send_async(AQ(),
                         cancellation_token=CancellationTokenSource().token),
        )

    assert asyncio.run(run()) == [True, False, True]
