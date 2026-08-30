"""py_mediatR — Django ornegi (kendi kendine calisir).

Calistirma:  python django_app/manage.py   (examples/ klasorunden)
Tarayici :  http://127.0.0.1:8113
Argumansiz cagrildiginda otomatik olarak runserver baslatir.
"""
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EXAMPLES_DIR.parent
REPO_SRC = REPO_ROOT / "src"
for p in (str(REPO_ROOT), str(EXAMPLES_DIR), str(REPO_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> None:
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                          "examples.django_app.mediator_demo.settings")
    from django.core.management import execute_from_command_line

    argv = sys.argv
    if len(argv) == 1:
        print("py_mediatR: Django demosu")
        print("Tarayicida acin: http://127.0.0.1:8113")
        argv = [argv[0], "runserver", "127.0.0.1:8113", "--noreload"]
    execute_from_command_line(argv)


if __name__ == "__main__":
    main()
