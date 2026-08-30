"""Regression coverage for the v6.7.1 typing and packaging hardening."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any, get_type_hints

import py_mediatR
import py_mediatr


def _public_annotations() -> Iterator[tuple[str, Any]]:
    """Yield every callable annotation exposed through the supported API."""
    for public_name in py_mediatR.__all__:
        value = getattr(py_mediatR, public_name)
        if inspect.isfunction(value):
            yield public_name, value
            continue
        if not inspect.isclass(value):
            continue

        for member_name, raw_member in vars(value).items():
            if member_name.startswith("_") and member_name != "__init__":
                continue
            if isinstance(raw_member, (classmethod, staticmethod)):
                yield f"{public_name}.{member_name}", raw_member.__func__
            elif isinstance(raw_member, property):
                for suffix, accessor in (
                    ("getter", raw_member.fget),
                    ("setter", raw_member.fset),
                    ("deleter", raw_member.fdel),
                ):
                    if accessor is not None:
                        yield f"{public_name}.{member_name}.{suffix}", accessor
            elif inspect.isfunction(raw_member):
                yield f"{public_name}.{member_name}", raw_member


def test_every_public_annotation_resolves_at_runtime() -> None:
    failures: list[str] = []
    checked = 0
    for label, target in _public_annotations():
        checked += 1
        try:
            get_type_hints(target)
        except Exception as exc:  # noqa: BLE001 - the assertion reports every failure
            failures.append(f"{label}: {type(exc).__name__}: {exc}")

    assert checked >= 100, "Public API scan unexpectedly found too few callables"
    assert not failures, "Unresolvable public annotations:\n" + "\n".join(failures)


def test_lowercase_import_is_the_same_public_api() -> None:
    assert py_mediatr.Mediator is py_mediatR.Mediator
    assert py_mediatr.__all__ == py_mediatR.__all__
    assert py_mediatr.__version__ == py_mediatR.__version__ == "6.7.1"
