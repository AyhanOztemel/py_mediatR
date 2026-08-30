"""py_mediatR._config — runtime flags, sentinels, internal logging.

v6.7.0'da tek dosyalik `py_mediatR.py` alt modullere ayrildi.
Kod govdesi birebir aynidir. Genel API icin `import py_mediatR` kullanin;
bu modul bir uygulama detayidir ve dogrudan import edilmesi gerekmez.
"""

import logging
import os
from typing import (
    Any,
)

# ============================================================================
# CONFIGURATION
# ============================================================================
DEBUG_MODE = os.getenv("MEDIATR_DEBUG", "").lower() in ("1", "true", "yes")

# v6.7: time budget (seconds) for the sync-over-async bridge. Prevents a hung
# coroutine from blocking the calling thread — and the event loop — forever.
SYNC_BRIDGE_TIMEOUT_DEFAULT = 30.0

_UNSET: Any = object()

# v6.7: strong references to fire-and-forget tasks. asyncio holds only weak
# references to running tasks, so a task nobody references can be garbage
# collected before it finishes (see CPython asyncio.create_task docs).
_BACKGROUND_TASKS: "set" = set()

# Pydantic desteği (opsiyonel)
_PydBaseModel: Any = None
try:
    from pydantic import BaseModel as _ImportedPydBaseModel
except ImportError:  # pragma: no cover - opsiyonel bağımlılık
    pass
else:
    _PydBaseModel = _ImportedPydBaseModel



# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _debug_log(msg: str) -> None:
    if DEBUG_MODE:
        print(f"[MediatR] {msg}")


_internal_logger = logging.getLogger("mediatr")
