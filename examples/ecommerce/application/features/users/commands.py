"""Users feature — commands.

Shows: request.validate() (ValidationBehavior), requires_permission
(AuthorizationBehavior), DI constructor injection (handler_factory),
dataclass responses.
"""
from dataclasses import dataclass

from py_mediatR import IRequest, IResponse

from ecommerce.domain.entities import User
from ecommerce.infrastructure.persistence.repositories import InMemoryUserRepository
from ecommerce.infrastructure.services.gateways import EmailGateway


@dataclass
class CreateUser(IRequest):
    name: str
    email: str

    def validate(self) -> None:  # called by ValidationBehavior
        if "@" not in self.email:
            raise ValueError(f"invalid e-mail: {self.email}")


@dataclass
class CreateUserResponse(IResponse):
    user_id: str
    name: str


class CreateUserHandler:
    """Constructor has dependencies -> auto-discovery defers it and the
    mediator resolves it through handler_factory=container.resolve."""

    def __init__(self, repo: InMemoryUserRepository, email: EmailGateway) -> None:
        self.repo = repo
        self.email = email

    def handle(self, req: CreateUser) -> CreateUserResponse:
        user = User(user_id=f"U-{req.name.lower()}", name=req.name, email=req.email)
        self.repo.add(user)
        self.email.send(req.email, "welcome")
        return CreateUserResponse(user_id=user.user_id, name=user.name)


@dataclass
class DeleteUser(IRequest):
    user_id: str
    requires_permission = "users.delete"  # checked by AuthorizationBehavior


@dataclass
class DeleteUserResponse(IResponse):
    user_id: str
    deleted: bool


class DeleteUserHandler:
    def handle(self, req: DeleteUser) -> DeleteUserResponse:
        return DeleteUserResponse(user_id=req.user_id, deleted=True)
