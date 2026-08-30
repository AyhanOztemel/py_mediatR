"""
py_mediatR v6.6 — FULL-PIPELINE layered example (e-commerce).

Run from the `examples/` directory:

    python -m ecommerce.main

or directly:

    python ecommerce/main.py

Covers every pipeline feature of the library:
  01 auto-discovery (deep package layout) + DI handler factory
  02 command + ctor injection + dict->dataclass coercion
  03 cross-cutting order (behavior -> pre -> handler -> post -> behavior)
  04 AuthorizationBehavior (UnauthorizedError)
  05 ValidationBehavior (request.validate() + external IValidator)
  06 CachingBehavior (dataclass key + v6.6 cache_key() protocol)
  07 RetryBehavior (transient failures)
  08 TransactionBehavior + scoped_mediator (begin/commit)
  09 state-based exception handler + exception action
  10 PerformanceBehavior threshold warning
  11 notifications: ordered sync subscribers + async publish strategies
  12 async send + streaming (IStreamRequest)
  13 DI lifetimes: singleton / scoped / transient
  14 CancellationToken: pre-cancel, registration dispose, linked sources,
     ambient current_cancellation_token() inside handlers
  15 override_handler (temporary test double)
  16 @handler / @behavior explicit (decorator) registration
  17 polymorphic publish (.NET covariance)
  18 direct cancellation_token injection into handle(req, cancellation_token)
  19 CachingBehavior TTL expiry + manual register_handler
  20 handler_lifetime="transient" vs "singleton"
  21 swallow_notification_errors (propagate vs swallow)
  22 use_cache=True — JSON discovery cache
  23 custom publisher (INotificationPublisher equivalent)
  24 PublishStrategy.PARALLEL_NOWAIT (fire-and-forget)
  25 cancel_after() — timeout cancels a running async handler
  26 make_fastapi_mediator_dependency (per-request scoped mediator)
  27 interfaces (ISender/IPublisher/IMediator), delegate/TResponse exports,
     coerce_to_model, standalone discover_handlers/discover_all(_v4),
     container_handler_factory
  28 runtime pipeline mutation (add_*), get_pipeline_info(), reset()
  (+ IStreamPipelineBehavior asserted in section 12)
"""
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1]          # .../examples
REPO_SRC = EXAMPLES_DIR.parent / "src"                      # .../py_mediatR/src
for p in (str(EXAMPLES_DIR), str(REPO_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from py_mediatR import (  # noqa: E402
    CachingBehavior,
    CancellationTokenRegistration,
    CancellationTokenSource,
    DIResolutionError,
    IMediator,
    INotification,
    IPublisher,
    IRequest,
    ISender,
    Mediator,
    OperationCancelledError,
    PublishStrategy,
    ServiceScope,
    TResponse,
    UnauthorizedError,
    coerce_to_model,
    container_handler_factory,
    discover_all,
    discover_all_v4,
    discover_handlers,
    make_fastapi_mediator_dependency,
)

from ecommerce.application.crosscutting.audit_log import AUDIT  # noqa: E402
from ecommerce.application.crosscutting.behaviors import AuditTrailBehavior  # noqa: E402
from ecommerce.application.crosscutting.exceptions import (  # noqa: E402
    AlertExceptionAction,
    InvoiceNotFoundHandler,
)
from ecommerce.application.crosscutting.processors import (  # noqa: E402
    AuditPreProcessor,
    MetricsPostProcessor,
)
from ecommerce.application.explicit_registrations import Ping  # noqa: E402
from ecommerce.application.features.billing.commands import (  # noqa: E402
    ChargeCard,
    FindInvoice,
    FindInvoiceHandler,
)
from ecommerce.application.features.orders.commands import (  # noqa: E402
    CancelOrder,
    CancelOrderHandler,
    PlaceOrder,
)
from ecommerce.application.features.orders.events import OrderShipped  # noqa: E402
from ecommerce.application.features.orders.queries import (  # noqa: E402
    GetOrderStatus,
    GetOrderStatusResponse,
)
from ecommerce.application.features.products.queries import (  # noqa: E402
    CALL_COUNTS,
    QuoteLookup,
    SearchProducts,
    SearchProductsHandler,
)
from ecommerce.application.features.reports.queries import (  # noqa: E402
    ExportReport,
    FetchExchangeRates,
    LongJob,
    SlowReport,
)
from ecommerce.application.features.reports.streaming import StreamOrderFeed  # noqa: E402
from ecommerce.application.features.users.commands import (  # noqa: E402
    CreateUser,
    CreateUserHandler,
    CreateUserResponse,
    DeleteUser,
)
from ecommerce.application.features.users.events import (  # noqa: E402
    AccountClosed,
    UserRegistered,
)
from ecommerce.application.features.users.queries import GetUser, GetUserResponse  # noqa: E402
from ecommerce.composition.bootstrap import (  # noqa: E402
    EXAMPLES_ROOT,
    build_container,
    build_mediator,
)
from ecommerce.infrastructure.persistence.repositories import (  # noqa: E402
    AuditWriter,
    InMemoryUserRepository,
    OrderUnitOfWork,
)

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def banner(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


async def async_part2(mediator, container) -> None:
    banner("23 Custom publisher (INotificationPublisher equivalent)")
    AUDIT.clear()

    async def reverse_publisher(handlers, notification):
        for h in reversed(handlers):  # custom dispatch: reversed order
            await h.handle(notification)

    await mediator.publish_async(OrderShipped(order_id="ORD-9"),
                                 publisher=reverse_publisher)
    check("custom publisher dispatched in reverse order",
          AUDIT == ["push:ORD-9", "sms:ORD-9"], str(AUDIT))

    banner("24 PublishStrategy.PARALLEL_NOWAIT (fire-and-forget)")
    AUDIT.clear()
    await mediator.publish_async(OrderShipped(order_id="ORD-NW"),
                                 strategy=PublishStrategy.PARALLEL_NOWAIT)
    fired_immediately = len(AUDIT) < 2  # returned without waiting for subscribers
    await asyncio.sleep(0.05)           # let the background tasks finish
    check("publish returned before subscribers finished", fired_immediately)
    check("subscribers completed in the background",
          sorted(AUDIT) == ["push:ORD-NW", "sms:ORD-NW"], str(AUDIT))

    banner("25 cancel_after() — timeout cancels a running handler")
    cts = CancellationTokenSource()
    cts.cancel_after(0.02)  # .NET CancelAfter
    try:
        await mediator.send_async(LongJob(steps=50), cancellation_token=cts.token)
        check("cancel_after stops the running LongJob", False, "job completed")
    except OperationCancelledError:
        check("cancel_after stops the running LongJob", True)

    banner("26 make_fastapi_mediator_dependency (per-request scope)")
    dependency = make_fastapi_mediator_dependency(mediator, container)
    agen = dependency()
    scoped = await agen.__anext__()  # what FastAPI's Depends() would do
    order = await scoped.send_async(PlaceOrder(sku="SKU-99", qty=1))
    await agen.aclose()              # request finished -> scope disposed
    check("scoped mediator from FastAPI dependency handled the request",
          order.order_id == "ORD-SKU-99", repr(order))


def main() -> int:
    container = build_container()
    mediator = build_mediator(container)

    banner("01 Auto-discovery (deep package layout)")
    handlers = mediator.get_registered_handlers()
    check("request handlers discovered", len(handlers) >= 11, f"got {len(handlers)}")
    check("stream handler discovered",
          "StreamOrderFeed" in mediator.get_registered_stream_handlers())
    check("notification handlers discovered",
          len(mediator.get_registered_notification_handlers().get("UserRegistered", [])) == 3)

    banner("02 Command + DI ctor injection + coercion")
    resp = mediator.send(CreateUser(name="Ada", email="ada@example.com"))
    check("CreateUser handled (deferred handler via container.resolve)",
          isinstance(resp, CreateUserResponse) and resp.user_id == "U-ada", repr(resp))
    check("EmailGateway side effect", "email:ada@example.com:welcome" in AUDIT)
    got = mediator.send(GetUser(user_id="U-ada"))
    check("dict return coerced to GetUserResponse dataclass",
          isinstance(got, GetUserResponse) and got.name == "Ada", repr(got))

    banner("03 Cross-cutting execution order")
    AUDIT.clear()
    mediator.send(CancelOrder(order_id="ORD-1"))
    check("behavior/pre/post order",
          AUDIT == ["->CancelOrder", "pre:CancelOrder", "post:CancelOrder", "<-CancelOrder"],
          str(AUDIT))

    banner("04 AuthorizationBehavior")
    try:
        mediator.send(DeleteUser(user_id="U-ada"))
        check("users.delete denied", False, "no exception raised")
    except UnauthorizedError:
        check("users.delete denied -> UnauthorizedError", True)

    banner("05 ValidationBehavior")
    try:
        mediator.send(CreateUser(name="Bad", email="not-an-email"))
        check("request.validate() rejects bad e-mail", False)
    except ValueError:
        check("request.validate() rejects bad e-mail", True)
    try:
        mediator.send(SearchProducts(keyword="   "))
        check("external IValidator rejects empty keyword", False)
    except ValueError:
        check("external IValidator rejects empty keyword", True)

    banner("06 CachingBehavior + cache_key() protocol")
    CALL_COUNTS["search"] = 0
    a = mediator.send(SearchProducts(keyword="laptop"))
    b = mediator.send(SearchProducts(keyword="laptop"))
    check("dataclass request cached (1 handler call)",
          CALL_COUNTS["search"] == 1 and a == b, f"calls={CALL_COUNTS['search']}")
    c = mediator.send(SearchProducts(keyword="phone"))
    check("different request -> cache miss", CALL_COUNTS["search"] == 2 and c.hits == 5)
    CALL_COUNTS["quote"] = 0
    q1 = mediator.send(QuoteLookup(skus=["B2", "A1"]))
    q2 = mediator.send(QuoteLookup(skus=["A1", "B2"]))  # same key: sorted tuple
    check("cache_key() protocol: order-insensitive hit (1 call)",
          CALL_COUNTS["quote"] == 1 and q1.total == q2.total == 35.5,
          f"calls={CALL_COUNTS['quote']}")

    banner("07 RetryBehavior")
    r = mediator.send(ChargeCard(amount=99.9))
    check("succeeded after transient failures", r.attempts == 3 and r.receipt == "charged:99.90",
          repr(r))

    banner("08 TransactionBehavior + scoped_mediator")
    AUDIT.clear()
    with mediator.create_scope(container) as scoped:
        order = scoped.send(PlaceOrder(sku="SKU-42", qty=3))
    check("order placed in scope", order.order_id == "ORD-SKU-42")
    check("tx open+commit+close (no rollback)",
          "tx:open" in AUDIT and "tx:commit" in AUDIT
          and "tx:close" in AUDIT and "tx:rollback" not in AUDIT,
          str([a for a in AUDIT if a.startswith("tx:")]))

    banner("09 Exception handler (state-based) + action")
    AUDIT.clear()
    inv = mediator.send(FindInvoice(invoice_id="INV-404"))
    check("KeyError swallowed -> fallback response",
          inv.found is False and inv.amount == 0.0, repr(inv))
    check("state handler + action both ran",
          "exc-handled:KeyError" in AUDIT and "alert:FindInvoice:KeyError" in AUDIT,
          str(AUDIT))
    check("state param is an ExceptionHandlerState instance",
          "state-type-ok:True" in AUDIT, str(AUDIT))

    banner("10 PerformanceBehavior (expect a WARNING log above)")
    slow = mediator.send(SlowReport(delay_ms=40))  # threshold is 25 ms
    check("slow request still returns", slow.waited_ms == 40)

    banner("11 Notifications: ordering + publish strategies")
    AUDIT.clear()
    mediator.publish(UserRegistered(user_id="U-ada"))
    check("sequential ordered subscribers",
          AUDIT == ["welcome-email:U-ada", "crm-sync:U-ada", "analytics:U-ada"], str(AUDIT))

    async def async_part() -> None:
        AUDIT.clear()
        await mediator.publish_async(OrderShipped(order_id="ORD-7"),
                                     strategy=PublishStrategy.SEQUENTIAL)
        check("async publish SEQUENTIAL", AUDIT == ["sms:ORD-7", "push:ORD-7"], str(AUDIT))
        AUDIT.clear()
        await mediator.publish_async(OrderShipped(order_id="ORD-8"),
                                     strategy=PublishStrategy.PARALLEL_WHENALL)
        check("async publish PARALLEL_WHENALL",
              sorted(AUDIT) == ["push:ORD-8", "sms:ORD-8"], str(AUDIT))

        banner("12 Async send + streaming + IStreamPipelineBehavior")
        fx = await mediator.send_async(FetchExchangeRates(base="EUR"))
        check("async handler", fx.rates == 31 and fx.had_ambient_token is False, repr(fx))
        AUDIT.clear()
        seqs = [item["seq"] async for item in mediator.create_stream(StreamOrderFeed(count=5))]
        check("streaming yields 5 items", seqs == [0, 1, 2, 3, 4], str(seqs))
        check("IStreamPipelineBehavior wrapped the stream",
              "~>StreamOrderFeed" in AUDIT and "<~StreamOrderFeed" in AUDIT, str(AUDIT))

        banner("14b Ambient CancellationToken inside async handler")
        cts = CancellationTokenSource()
        fx2 = await mediator.send_async(FetchExchangeRates(base="USD"),
                                        cancellation_token=cts.token)
        check("current_cancellation_token() visible in handler",
              fx2.had_ambient_token is True)
        cts.cancel()
        try:
            await mediator.send_async(FetchExchangeRates(base="GBP"),
                                      cancellation_token=cts.token)
            check("cancelled token stops async send", False)
        except OperationCancelledError:
            check("cancelled token stops async send", True)

    asyncio.run(async_part())

    banner("13 DI lifetimes")
    s1, s2 = container.resolve(InMemoryUserRepository), container.resolve(InMemoryUserRepository)
    check("singleton: same instance", s1 is s2)
    t1, t2 = container.resolve(AuditWriter), container.resolve(AuditWriter)
    check("transient: new instance each resolve", t1 is not t2)
    with container.create_scope() as sc1:
        check("create_scope() returns a ServiceScope", isinstance(sc1, ServiceScope))
        a1, a2 = sc1.resolve(OrderUnitOfWork), sc1.resolve(OrderUnitOfWork)
    with container.create_scope() as sc2:
        b1 = sc2.resolve(OrderUnitOfWork)
    check("scoped: shared inside scope, fresh across scopes",
          a1 is a2 and a1 is not b1,
          f"scopes={a1.scope_no},{b1.scope_no}")
    try:
        container.resolve(OrderUnitOfWork)  # scoped from root -> .NET rule
        check("scoped resolve from root raises DIResolutionError", False)
    except DIResolutionError:
        check("scoped resolve from root raises DIResolutionError", True)
    ready = AuditWriter()
    container.register_instance(AuditWriter, ready)  # .NET AddSingleton(obj)
    check("register_instance returns the exact object",
          container.resolve(AuditWriter) is ready)

    banner("14 CancellationToken lifecycle")
    cts = CancellationTokenSource()
    try:
        cts.cancel(reason="user clicked stop")
        mediator.send(GetOrderStatus(order_id="X"), cancellation_token=cts.token)
        check("pre-cancelled token stops sync send", False)
    except OperationCancelledError:
        check("pre-cancelled token stops sync send", True)

    hits = []
    src = CancellationTokenSource()
    reg_kept = src.token.register(lambda: hits.append("kept"))
    reg_gone = src.token.register(lambda: hits.append("disposed"))
    check("register() returns CancellationTokenRegistration",
          isinstance(reg_kept, CancellationTokenRegistration))
    reg_gone.dispose()                     # v6.5 registration lifecycle
    src.cancel()
    check("registration dispose removes callback", hits == ["kept"], str(hits))

    parent = CancellationTokenSource()
    linked = CancellationTokenSource.create_linked(parent.token)
    parent.cancel()
    check("linked source cancelled by parent", linked.token.is_cancellation_requested)
    parent2 = CancellationTokenSource()
    linked2 = CancellationTokenSource.create_linked(parent2.token)
    linked2.dispose()                      # v6.5: unhooks from parent, no leak
    parent2.cancel()
    check("disposed linked source stays un-cancelled",
          not linked2.token.is_cancellation_requested)

    banner("15 override_handler (test double)")
    class FakeStatusHandler:
        def handle(self, req: GetOrderStatus) -> GetOrderStatusResponse:
            return GetOrderStatusResponse(order_id=req.order_id, status="FAKE")

    with mediator.override_handler(GetOrderStatus, FakeStatusHandler()):
        check("override active",
              mediator.send(GetOrderStatus(order_id="O1")).status == "FAKE")
    check("original restored",
          mediator.send(GetOrderStatus(order_id="O1")).status == "SHIPPED")

    banner("16 @handler / @behavior explicit registration")
    AUDIT.clear()
    pong = mediator.send(Ping(payload="hello"))
    check("@handler-decorated PingHandler resolved (not auto-discovered)",
          pong.echoed is True and pong.payload == "hello", repr(pong))
    check("@behavior-decorated TagBehavior ran (applies_to=Ping)",
          "tag:Ping" in AUDIT, str(AUDIT))
    AUDIT.clear()
    mediator.send(GetOrderStatus(order_id="O2"))
    check("TagBehavior skipped for other requests",
          not any(a.startswith("tag:") for a in AUDIT), str(AUDIT))

    banner("17 Polymorphic publish (.NET covariance)")
    AUDIT.clear()
    mediator.publish(AccountClosed(user_id="U-ada"))
    check("derived AccountClosed triggered BASE AccountEvent subscriber",
          "account-event:AccountClosed:U-ada" in AUDIT, str(AUDIT))

    banner("18 Direct cancellation_token injection")
    cts = CancellationTokenSource()
    exp = mediator.send(ExportReport(rows=10), cancellation_token=cts.token)
    check("handle(req, cancellation_token) got the token injected",
          exp.token_injected is True, repr(exp))
    exp2 = mediator.send(ExportReport(rows=10))
    check("no token given -> CancellationToken.none injected",
          exp2.token_injected is False, repr(exp2))

    banner("19 CachingBehavior TTL expiry + manual register_handler")
    m_ttl = Mediator(auto_discover=False,
                     behaviors=[CachingBehavior(ttl_seconds=0.05, max_size=16)])
    m_ttl.register_handler(SearchProducts, SearchProductsHandler())
    CALL_COUNTS["search"] = 0
    m_ttl.send(SearchProducts(keyword="ttl"))
    m_ttl.send(SearchProducts(keyword="ttl"))
    check("hit before TTL expiry (1 call)", CALL_COUNTS["search"] == 1,
          f"calls={CALL_COUNTS['search']}")
    import time as _time
    _time.sleep(0.06)
    m_ttl.send(SearchProducts(keyword="ttl"))
    check("miss after TTL expiry (2 calls)", CALL_COUNTS["search"] == 2,
          f"calls={CALL_COUNTS['search']}")

    banner("20 handler_lifetime: transient vs singleton")

    @dataclass
    class CountInstances(IRequest):
        pass

    class CountingHandler:
        created = 0

        def __init__(self) -> None:
            CountingHandler.created += 1

        def handle(self, req: CountInstances) -> int:
            return CountingHandler.created

    m_tr = Mediator(auto_discover=False, handler_factory=container.resolve,
                    handler_lifetime="transient")
    m_tr.register_handler(CountInstances, CountingHandler)  # class -> deferred
    m_tr.send(CountInstances())
    m_tr.send(CountInstances())
    check("transient: new handler instance per send", CountingHandler.created == 2,
          f"created={CountingHandler.created}")
    CountingHandler.created = 0
    m_sg = Mediator(auto_discover=False, handler_factory=container.resolve)
    m_sg.register_handler(CountInstances, CountingHandler)
    m_sg.send(CountInstances())
    m_sg.send(CountInstances())
    check("singleton (default): one instance reused", CountingHandler.created == 1,
          f"created={CountingHandler.created}")

    banner("21 swallow_notification_errors")

    @dataclass
    class Boom(INotification):
        pass

    class BoomSubscriber:
        def handle(self, n: Boom):
            raise RuntimeError("subscriber exploded")

    m_err = Mediator(auto_discover=False)  # default: errors PROPAGATE (.NET parity)
    m_err.register_notification_handler(Boom, BoomSubscriber())
    try:
        m_err.publish(Boom())
        check("default: subscriber error propagates", False, "no exception")
    except Exception:
        check("default: subscriber error propagates", True)
    m_sw = Mediator(auto_discover=False, swallow_notification_errors=True)
    m_sw.register_notification_handler(Boom, BoomSubscriber())
    try:
        m_sw.publish(Boom())
        check("swallow_notification_errors=True: publish survives", True)
    except Exception as e:
        check("swallow_notification_errors=True: publish survives", False, repr(e))

    banner("22 use_cache=True (JSON discovery cache)")
    cache_file = EXAMPLES_ROOT / ".mediatr_cache.json"
    if cache_file.exists():
        cache_file.unlink()
    m_c1 = Mediator(auto_discover=True, project_root=EXAMPLES_ROOT,
                    scan_paths=["ecommerce/application/features"], use_cache=True,
                    handler_factory=container.resolve)
    check("discovery cache file written", cache_file.exists(), str(cache_file))
    m_c2 = Mediator(auto_discover=True, project_root=EXAMPLES_ROOT,
                    scan_paths=["ecommerce/application/features"], use_cache=True,
                    handler_factory=container.resolve)
    check("second build loads same handlers from cache",
          len(m_c2.get_registered_handlers()) == len(m_c1.get_registered_handlers()))
    cache_file.unlink()  # keep the repo clean

    banner("27 Interfaces, delegates & standalone discovery API")
    check("Mediator implements IMediator = ISender + IPublisher",
          isinstance(mediator, IMediator) and isinstance(mediator, ISender)
          and isinstance(mediator, IPublisher))
    from typing import TypeVar
    check("TResponse generic TypeVar exported", isinstance(TResponse, TypeVar))
    coerced = coerce_to_model({"user_id": "U-x", "name": "X"}, GetUserResponse)
    check("coerce_to_model() standalone dict->dataclass",
          isinstance(coerced, GetUserResponse) and coerced.name == "X", repr(coerced))
    scan = ["ecommerce/application/features"]
    req_reg = discover_handlers(project_root=EXAMPLES_ROOT, scan_paths=scan)
    check("discover_handlers() finds request handlers standalone",
          CreateUser in req_reg, f"got {len(req_reg)}")
    r3, s3, n3 = discover_all(project_root=EXAMPLES_ROOT, scan_paths=scan)
    check("discover_all() returns request+stream+notification registries",
          CreateUser in r3 and StreamOrderFeed in s3 and UserRegistered in n3)
    r4, n4 = discover_all_v4(project_root=EXAMPLES_ROOT, scan_paths=scan)
    check("discover_all_v4() legacy 2-tuple", CreateUser in r4 and UserRegistered in n4)
    factory = container_handler_factory(container)  # explicit IoC bridge
    m_cf = Mediator(auto_discover=False, handler_factory=factory)
    m_cf.register_handler(CreateUser, CreateUserHandler)  # class -> deferred
    cf_resp = m_cf.send(CreateUser(name="Cf", email="cf@example.com"))
    check("container_handler_factory resolves deferred handler",
          cf_resp.user_id == "U-cf", repr(cf_resp))

    banner("28 Runtime pipeline mutation (add_*) + get_pipeline_info + reset")
    m_rt = Mediator(auto_discover=False)
    m_rt.register_handler(CancelOrder, CancelOrderHandler())
    m_rt.register_handler(FindInvoice, FindInvoiceHandler())
    m_rt.add_behavior(AuditTrailBehavior())
    m_rt.add_pre_processor(AuditPreProcessor())
    m_rt.add_post_processor(MetricsPostProcessor())
    m_rt.add_exception_handler(InvoiceNotFoundHandler())
    m_rt.add_exception_action(AlertExceptionAction())
    AUDIT.clear()
    m_rt.send(CancelOrder(order_id="ORD-RT"))
    check("add_behavior/add_pre/add_post active after construction",
          AUDIT == ["->CancelOrder", "pre:CancelOrder",
                    "post:CancelOrder", "<-CancelOrder"], str(AUDIT))
    inv_rt = m_rt.send(FindInvoice(invoice_id="INV-404"))
    check("add_exception_handler/action active after construction",
          inv_rt.found is False and any(a.startswith("alert:FindInvoice") for a in AUDIT))
    info = m_rt.get_pipeline_info()
    check("get_pipeline_info() reports the pipeline",
          any(b["name"] == "AuditTrailBehavior" for b in info["behaviors"])
          and any(h["name"] == "InvoiceNotFoundHandler"
                  for h in info["exception_handlers"]), str(info))
    m_rt.reset()
    check("reset() clears all registrations",
          len(m_rt.get_registered_handlers()) == 0)

    asyncio.run(async_part2(mediator, container))

    print(f"\n{'=' * 66}\nRESULT: {PASS} passed, {FAIL} failed\n{'=' * 66}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
