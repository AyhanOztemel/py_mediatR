# -*- coding: utf-8 -*-
"""Headless smoke test for the three web examples.

Drives FastAPI, Flask and Django through their in-process test clients and
asserts every route answers and reports a call chain. Run from `examples/`:

    python -m ci_smoke
"""
import json
import os
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLES_DIR.parent
for p in (str(REPO_ROOT), str(EXAMPLES_DIR), str(REPO_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

FAILURES: list = []


def check(label: str, payload: dict) -> None:
    chain = payload.get("cagri_zinciri")
    if not chain:
        FAILURES.append(f"{label}: response carries no call chain")
        return
    print(f"[OK ] {label}")
    for line in chain:
        print(f"       {line}")


def run_fastapi() -> None:
    from fastapi.testclient import TestClient

    from fastapi_app.app import app

    client = TestClient(app)
    routes = [
        "/users/olustur?name=Ayhan&email=ayhan@ornek.com",
        "/users/U-ayhan",
        "/products/ara?keyword=laptop",
        "/invoices/INV-YOK",
        "/orders/ver?sku=SKU-7&qty=2",
        "/orders/akis?count=3",
    ]
    for route in routes:
        response = client.get(route)
        if response.status_code != 200:
            FAILURES.append(f"fastapi {route}: HTTP {response.status_code}")
            continue
        check(f"fastapi {route}", response.json())


def run_flask() -> None:
    from flask_app.app import app

    client = app.test_client()
    routes = [
        "/users/olustur?name=Ayhan&email=ayhan@ornek.com",
        "/users/U-ayhan",
        "/products/ara?keyword=laptop",
        "/invoices/INV-YOK",
        "/orders/ver?sku=SKU-7&qty=2",
    ]
    for route in routes:
        response = client.get(route)
        if response.status_code != 200:
            FAILURES.append(f"flask {route}: HTTP {response.status_code}")
            continue
        check(f"flask {route}", response.get_json())


def run_django() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                          "examples.django_app.mediator_demo.settings")
    django.setup()
    from django.test import Client

    client = Client()
    routes = [
        "/users/olustur?name=Ayhan&email=ayhan@ornek.com",
        "/users/U-ayhan",
        "/products/ara?keyword=laptop",
        "/invoices/INV-YOK",
        "/orders/ver?sku=SKU-7&qty=2",
    ]
    for route in routes:
        response = client.get(route)
        if response.status_code != 200:
            FAILURES.append(f"django {route}: HTTP {response.status_code}")
            continue
        check(f"django {route}", json.loads(response.content))


def main() -> int:
    for name, fn in (("FastAPI", run_fastapi),
                     ("Flask", run_flask),
                     ("Django", run_django)):
        print(f"\n=== {name} ===")
        fn()

    print()
    if FAILURES:
        for line in FAILURES:
            print(f"[FAIL] {line}")
        print(f"RESULT: {len(FAILURES)} failed")
        return 1
    print("RESULT: all web example routes passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
