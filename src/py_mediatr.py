"""Lowercase alias for :mod:`py_mediatR`.

The distribution name normalises to ``py-mediatr``, so users naturally reach for
``import py_mediatr``. That fails on case-sensitive filesystems, hence this shim.
It re-exports the same module objects, so identity and isinstance checks hold.
"""

import sys as _sys

import py_mediatR as _impl
from py_mediatR import *  # noqa: F401,F403
from py_mediatR import __all__, __version__  # noqa: F401

_sys.modules.setdefault("py_mediatr.py_mediatR", _impl.py_mediatR)
