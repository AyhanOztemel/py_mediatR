# -*- coding: utf-8 -*-
"""Composition root — DI container + Mediator wiring.

Everything cross-cutting is assembled HERE, in one place:
  * ServiceContainer with singleton / scoped / transient lifetimes
  * ALL built-in behaviors (Logging, Tracing, Performance, Authorization,
    Validation, Caching, Retry, Transaction) + a custom AuditTrailBehavior
  * pre/post processors, exception handlers (state-based) + actions
  * auto-discovery of every handler under application/features
    (handlers with constructor dependencies are resolved via
    handler_factory=container.resolve)
"""
import logging
from pathlib import Path

from py_mediatR import (
    Mediator, ServiceContainer, PublishStrategy,
    LoggingBehavior, TracingBehavior, PerformanceBehavior,
    AuthorizationBehavior, ValidationBehavior, CachingBehavior,
    RetryBehavior, TransactionBehavior,
)

from ecommerce.application.crosscutting.behaviors import (
    AuditTrailBehavior, StreamAuditBehavior, SearchKeywordValidator,
)
# Importing this module runs the @handler/@behavior decorators BEFORE the
# Mediator is built (explicit registration path — see the module docstring).
import ecommerce.application.explicit_registrations  # noqa: F401
from ecommerce.application.crosscutting.processors import (
    AuditPreProcessor, MetricsPostProcessor,
)
from ecommerce.application.crosscutting.exceptions import (
    InvoiceNotFoundHandler, AlertExceptionAction,
)
from ecommerce.application.features.products.queries import SearchProducts
from ecommerce.infrastructure.persistence.repositories import (
    InMemoryUserRepository, OrderUnitOfWork, AuditWriter,
)
from ecommerce.infrastructure.persistence.session import session_factory
from ecommerce.infrastructure.services.gateways import EmailGateway, PaymentGateway

EXAMPLES_ROOT = Path(__file__).resolve().parents[2]  # .../examples

# Demo policy: the current user has every permission except "users.delete".
GRANTED_PERMISSIONS = {"users.create", "orders.place"}


def permission_checker(request, permission: str) -> bool:
    return permission in GRANTED_PERMISSIONS


def build_container() -> ServiceContainer:
    container = ServiceContainer()
    container.register_singleton(InMemoryUserRepository)
    container.register_singleton(EmailGateway)
    container.register_singleton(PaymentGateway)
    container.register_scoped(OrderUnitOfWork)
    container.register_transient(AuditWriter)
    return container


def build_mediator(container: ServiceContainer) -> Mediator:
    SearchKeywordValidator.applies_to = (SearchProducts,)

    return Mediator(
        auto_discover=True,
        project_root=EXAMPLES_ROOT,
        scan_paths=["ecommerce/application/features"],
        use_cache=False,
        handler_factory=container.resolve,  # DI bridge for ctor-injected handlers
        behaviors=[
            AuditTrailBehavior(),                          # order -110 (custom)
            LoggingBehavior(level=logging.DEBUG),          # order -100
            TracingBehavior(),                             # order  -95 (no-op w/o otel)
            PerformanceBehavior(threshold_ms=25.0),        # order  -90
            AuthorizationBehavior(permission_checker),     # order  -85
            ValidationBehavior(validators=[SearchKeywordValidator()]),  # -80
            CachingBehavior(max_size=256),                 # order  -70
            RetryBehavior(max_attempts=5, delay=0.001, backoff=1.5,
                          on_exceptions=(ConnectionError,)),
            TransactionBehavior(session_factory),
        ],
        stream_behaviors=[StreamAuditBehavior()],
        pre_processors=[AuditPreProcessor()],
        post_processors=[MetricsPostProcessor()],
        exception_handlers=[InvoiceNotFoundHandler()],
        exception_actions=[AlertExceptionAction()],
        default_publish_strategy=PublishStrategy.SEQUENTIAL,
        polymorphic_publish=True,   # derived notification -> base handlers too
        discover_behaviors=True,    # pick up @behavior-decorated TagBehavior
    )
