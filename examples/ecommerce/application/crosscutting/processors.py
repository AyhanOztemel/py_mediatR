# -*- coding: utf-8 -*-
"""Pre/post processors — run before/after the handler, inside all behaviors."""
from py_mediatR import IRequestPreProcessor, IRequestPostProcessor

from ecommerce.application.crosscutting.audit_log import AUDIT


class AuditPreProcessor(IRequestPreProcessor):
    def process(self, request):
        AUDIT.append(f"pre:{type(request).__name__}")


class MetricsPostProcessor(IRequestPostProcessor):
    def process(self, request, response):
        AUDIT.append(f"post:{type(request).__name__}")
