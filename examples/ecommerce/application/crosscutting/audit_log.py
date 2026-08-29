# -*- coding: utf-8 -*-
"""Shared audit trail — every cross-cutting component appends here so the
demo can assert the exact pipeline execution order."""
from typing import List

AUDIT: List[str] = []
