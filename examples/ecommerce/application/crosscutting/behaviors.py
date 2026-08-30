"""Custom pipeline behavior + validator (wired explicitly in bootstrap.py,
NOT auto-discovered)."""
import inspect

from py_mediatR import (
    IPipelineBehavior,
    IStreamPipelineBehavior,
    IValidator,
    RequestHandlerDelegate,
    StreamHandlerDelegate,
)

from ecommerce.application.crosscutting.audit_log import AUDIT


class AuditTrailBehavior(IPipelineBehavior):
    """Outermost behavior: records ->Request / <-Request around the whole
    pipeline. Awaitable-aware, so it works on sync AND async chains."""
    order = -110  # before LoggingBehavior (-100)

    def handle(self, request, next_handler: RequestHandlerDelegate):
        name = type(request).__name__
        AUDIT.append(f"->{name}")
        result = next_handler()
        if inspect.isawaitable(result):
            return self._finish_async(name, result)
        AUDIT.append(f"<-{name}")
        return result

    async def _finish_async(self, name, awaitable):
        try:
            return await awaitable
        finally:
            AUDIT.append(f"<-{name}")


class StreamAuditBehavior(IStreamPipelineBehavior):
    """Stream pipeline behavior — wraps every IStreamRequest: records
    ~>Request before the first item and <~Request after the last one."""

    async def handle(self, request, next_handler: StreamHandlerDelegate):
        name = type(request).__name__
        AUDIT.append(f"~>{name}")
        async for item in next_handler():
            yield item
        AUDIT.append(f"<~{name}")


class SearchKeywordValidator(IValidator):
    """External validator (FluentValidation style) — ValidationBehavior runs it
    only for the request types listed in `applies_to`."""

    def validate(self, request) -> None:
        if not request.keyword.strip():
            raise ValueError("keyword must not be empty")


# applies_to is resolved lazily in bootstrap.py to avoid an import cycle:
# bootstrap assigns SearchKeywordValidator.applies_to = (SearchProducts,)
