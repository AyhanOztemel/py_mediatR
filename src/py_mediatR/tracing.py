"""py_mediatR.tracing — FlowNode / FlowTrace call-chain tracing.

v6.7.0'da tek dosyalik `py_mediatR.py` alt modullere ayrildi.
Kod govdesi birebir aynidir. Genel API icin `import py_mediatR` kullanin;
bu modul bir uygulama detayidir ve dogrudan import edilmesi gerekmez.
"""

import contextvars
import sys
import time
from contextlib import contextmanager
from threading import Lock
from typing import (
    Iterator,
    List,
    Optional,
)

# ============================================================================
# v6.7 — FLOW TRACE
# Answers the question every mediator hides: "what actually called what?".
# A dispatch is recorded as a tree — send(X) -> behaviors in wrapping order ->
# pre-processors -> handler -> post-processors -> notifications.
# Zero cost when inactive: one ContextVar lookup per instrumented step.
# ============================================================================

_FLOW: "contextvars.ContextVar[Optional[FlowTrace]]" = contextvars.ContextVar(
    "mediatr_flow", default=None)
_FLOW_CURSOR: "contextvars.ContextVar[Optional[FlowNode]]" = \
    contextvars.ContextVar("mediatr_flow_cursor", default=None)


class FlowNode:
    """A single step in a recorded dispatch."""
    __slots__ = ("kind", "label", "detail", "children", "error",
                 "_start", "elapsed_ms")

    def __init__(self, kind: str, label: str, detail: Optional[str] = None) -> None:
        self.kind = kind                 # send | publish | stream | behavior |
        self.label = label               # pre | handler | post | notification |
        self.detail = detail             # exception-handler | exception-action
        self.children: List["FlowNode"] = []
        self.error: Optional[str] = None
        self._start = time.perf_counter()
        self.elapsed_ms = 0.0

    def close(self, error: Optional[BaseException] = None) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        if error is not None:
            self.error = f"{type(error).__name__}: {error}"

    def note(self, detail: str) -> None:
        self.detail = detail if not self.detail else f"{self.detail}, {detail}"


_KIND_TAG = {
    "send": "send", "publish": "publish", "stream": "stream",
    "behavior": "behavior", "pre": "pre", "handler": "HANDLER",
    "post": "post", "notification": "subscriber",
    "exception-handler": "on-error", "exception-action": "on-error(action)",
}


class FlowTrace:
    """
    Recorded call tree for one or more dispatches.

    Usage:
        with mediator.trace() as flow:
            mediator.send(CreateUser(name="Ayhan"))
        print(flow.render())

    Rendering is plain text; pass unicode=False for consoles that cannot
    print box-drawing characters.
    """
    __slots__ = ("roots", "_lock")

    def __init__(self) -> None:
        self.roots: List[FlowNode] = []
        self._lock = Lock()

    # ---- recording ----

    def _attach(self, node: FlowNode) -> None:
        parent = _FLOW_CURSOR.get()
        with self._lock:
            (parent.children if parent is not None else self.roots).append(node)

    # ---- inspection ----

    def steps(self) -> List[FlowNode]:
        """Flattened depth-first list of every recorded step."""
        out: List[FlowNode] = []

        def walk(nodes):
            for n in nodes:
                out.append(n)
                walk(n.children)

        walk(self.roots)
        return out

    def find(self, label: str) -> Optional[FlowNode]:
        for n in self.steps():
            if n.label == label:
                return n
        return None

    # ---- rendering ----

    def render(self, show_timing: bool = True, unicode: Optional[bool] = None) -> str:
        if unicode is None:
            unicode = _console_supports_unicode()
        glyphs = (("|- ", "`- ", "|  ", "   ") if not unicode
                  else ("├─ ", "└─ ", "│  ", "   "))
        lines: List[str] = []

        def fmt(node: FlowNode) -> str:
            tag = _KIND_TAG.get(node.kind, node.kind)
            text = f"{tag}: {node.label}" if node.kind != "send" else \
                   f"{node.label}"
            if node.kind in ("send", "publish", "stream"):
                text = f"{node.kind}({node.label})"
            if node.detail:
                text += f"   [{node.detail}]"
            if node.error:
                # An error is spelled out only where it originates; ancestors
                # that merely let it propagate get a short marker, otherwise a
                # deep pipeline repeats the same message on every line.
                inherited = any(c.error == node.error for c in node.children)
                text += "   !! (propagated)" if inherited else f"   !! {node.error}"
            if show_timing and node.elapsed_ms >= 0.05:
                text += f"   ({node.elapsed_ms:.2f} ms)"
            return text

        def walk(nodes: List[FlowNode], prefix: str) -> None:
            for i, node in enumerate(nodes):
                last = i == len(nodes) - 1
                lines.append(prefix + (glyphs[1] if last else glyphs[0]) + fmt(node))
                walk(node.children, prefix + (glyphs[3] if last else glyphs[2]))

        for root in self.roots:
            lines.append(fmt(root))
            walk(root.children, "")
        return "\n".join(lines)

    def print(self, *, title: Optional[str] = None,
              show_timing: bool = True) -> None:
        if title:
            print(title)
        print(self.render(show_timing=show_timing))

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.render()


def _console_supports_unicode() -> bool:
    try:
        "├─└│".encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError, AttributeError):
        return False


@contextmanager
def trace_flow() -> Iterator[FlowTrace]:
    """
    Record every dispatch made inside the block as a call tree.

        with trace_flow() as flow:
            mediator.send(CreateUser(...))
        flow.print()
    """
    trace = FlowTrace()
    token = _FLOW.set(trace)
    cursor_token = _FLOW_CURSOR.set(None)
    try:
        yield trace
    finally:
        _FLOW_CURSOR.reset(cursor_token)
        _FLOW.reset(token)


def _flow_begin(kind: str, label: str, detail: Optional[str] = None):
    """Open a trace node. Returns None (and costs one ContextVar get) when off."""
    trace = _FLOW.get()
    if trace is None:
        return None
    node = FlowNode(kind, label, detail)
    trace._attach(node)
    return node, _FLOW_CURSOR.set(node)


def _flow_end(handle, error: Optional[BaseException] = None) -> None:
    if handle is None:
        return
    node, token = handle
    node.close(error)
    _FLOW_CURSOR.reset(token)


def _flow_note(detail: str) -> None:
    """Annotate the innermost open node (e.g. 'cache hit', 'attempt 3/3')."""
    node = _FLOW_CURSOR.get()
    if node is not None:
        node.note(detail)
