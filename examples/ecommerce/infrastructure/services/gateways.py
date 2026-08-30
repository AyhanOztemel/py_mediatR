"""External-service gateways (fakes) — injected into handlers via DI."""
from ecommerce.application.crosscutting.audit_log import AUDIT


class EmailGateway:
    def send(self, to: str, subject: str) -> None:
        AUDIT.append(f"email:{to}:{subject}")


class PaymentGateway:
    """Fails twice, then succeeds — drives the RetryBehavior demo."""

    def __init__(self) -> None:
        self.attempts = 0

    def charge(self, amount: float) -> str:
        self.attempts += 1
        if self.attempts < 3:
            raise ConnectionError(f"payment gateway timeout #{self.attempts}")
        return f"charged:{amount:.2f}"
