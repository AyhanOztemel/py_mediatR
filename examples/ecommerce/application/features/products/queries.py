"""Products feature — caching demos.

* SearchProducts: `cacheable = True` dataclass -> CachingBehavior builds a
  canonical (collision-proof) key from its fields automatically.
* QuoteLookup: MUTABLE request (list field) -> defines the explicit v6.6
  cache_key() protocol so the key stays stable and collision-proof.
"""
from dataclasses import dataclass, field
from typing import List

from py_mediatR import IRequest, IResponse

CALL_COUNTS = {"search": 0, "quote": 0}


@dataclass
class SearchProducts(IRequest):
    keyword: str
    cacheable: bool = True


@dataclass
class SearchProductsResponse(IResponse):
    keyword: str
    hits: int


class SearchProductsHandler:
    def handle(self, req: SearchProducts) -> SearchProductsResponse:
        CALL_COUNTS["search"] += 1
        return SearchProductsResponse(keyword=req.keyword, hits=len(req.keyword))


@dataclass
class QuoteLookup(IRequest):
    skus: List[str] = field(default_factory=list)  # mutable -> not hashable
    cacheable: bool = True

    def cache_key(self):  # v6.6 explicit cache-key protocol
        return tuple(sorted(self.skus))


@dataclass
class QuoteLookupResponse(IResponse):
    total: float


class QuoteLookupHandler:
    PRICES = {"A1": 10.0, "B2": 25.5, "C3": 7.25}

    def handle(self, req: QuoteLookup) -> QuoteLookupResponse:
        CALL_COUNTS["quote"] += 1
        return QuoteLookupResponse(
            total=sum(self.PRICES.get(s, 0.0) for s in req.skus))
