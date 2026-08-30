"""Users feature — queries. Shows dict -> dataclass coercion: the handler
returns a plain dict and py_mediatR coerces it into GetUserResponse."""
from dataclasses import dataclass

from py_mediatR import IRequest, IResponse

from ecommerce.infrastructure.persistence.repositories import InMemoryUserRepository


@dataclass
class GetUser(IRequest):
    user_id: str


@dataclass
class GetUserResponse(IResponse):
    user_id: str
    name: str


class GetUserHandler:
    def __init__(self, repo: InMemoryUserRepository) -> None:
        self.repo = repo

    def handle(self, req: GetUser) -> GetUserResponse:
        user = self.repo.get(req.user_id)
        name = user.name if user else "unknown"
        return {"user_id": req.user_id, "name": name}  # coerced to dataclass
