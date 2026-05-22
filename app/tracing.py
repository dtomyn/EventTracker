"""Real-time execution tracer for EventTracker.

Provides:
- @instrument decorator for sync/async function instrumentation
- TraceMiddleware for HTTP request tracing
- SSE pub/sub for live span streaming to visualization dashboard
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import sqlite3
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncGenerator, Callable, ContextManager, Iterator, TypeVar


_T = TypeVar("_T")

# ── Layer color scheme ──────────────────────────────────────────────────────

LAYER_COLORS = {
    "route": "#3b82f6",      # blue
    "service": "#f97316",    # orange
    "db": "#22c55e",         # green
    "ai": "#ef4444",         # red
}


# ── TraceSpan dataclass ─────────────────────────────────────────────────────

@dataclass
class TraceSpan:
    """Represents a single function call in the execution trace."""

    span_id: str               # uuid4 hex, ~12 chars
    trace_id: str              # per-request ID from middleware
    parent_id: str | None      # span_id of parent, None for root
    layer: str                 # "route" | "service" | "db" | "ai"
    name: str                  # function name (qualified)
    start_ms: float            # monotonic time * 1000 at span start
    end_ms: float | None = None  # set when span ends, None while running
    depth: int = 0             # call stack depth
    data_summary: str = ""     # safe 120-char summary of args/return
    error: str | None = None   # exception class name if raised

    def duration_ms(self) -> float | None:
        """Return duration in milliseconds, or None if still running."""
        if self.end_ms is None:
            return None
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization, including computed fields."""
        d = asdict(self)
        d["duration_ms"] = self.duration_ms()
        d["color"] = LAYER_COLORS.get(self.layer, "#6b7280")
        return d


# ── Per-request context (ContextVar) ────────────────────────────────────────

_current_trace_id: ContextVar[str | None] = ContextVar(
    "_current_trace_id", default=None
)
_current_span_stack: ContextVar[list[TraceSpan]] = ContextVar(
    "_current_span_stack", default=[]
)


# ── Global subscriber state ─────────────────────────────────────────────────

_subscribers: list[asyncio.Queue[dict]] = []
_ring_buffer: deque[dict] = deque(maxlen=200)
_subscribers_lock = asyncio.Lock()


# ── Span lifecycle ──────────────────────────────────────────────────────────

def _summarize_args(args: tuple, kwargs: dict) -> str:
    """Produce a safe, 120-char summary of function arguments.

    Never raises; treats sqlite3.Connection as "db" to avoid noise.
    """
    try:
        parts = []
        for a in args:
            # Hide connection objects
            if isinstance(a, sqlite3.Connection):
                parts.append("db")
            elif isinstance(a, (str, int, float, bool)):
                s = str(a)
                if len(s) < 60:
                    parts.append(repr(a))
            else:
                parts.append(type(a).__name__)

        for k, v in kwargs.items():
            if isinstance(v, (str, int, float, bool)):
                s = str(v)
                if len(s) < 40:
                    parts.append(f"{k}={v!r}")

        summary = ", ".join(parts)
        return summary[:120]
    except Exception:
        return ""


def _begin_span(layer: str, fn_name: str, args: tuple, kwargs: dict) -> TraceSpan:
    """Create and record a new span.

    Pushes the span onto the current call stack and broadcasts span_start event.
    """
    trace_id = _current_trace_id.get() or "no-trace"
    stack = _current_span_stack.get()
    parent_id = stack[-1].span_id if stack else None
    depth = len(stack)

    span = TraceSpan(
        span_id=uuid.uuid4().hex[:12],
        trace_id=trace_id,
        parent_id=parent_id,
        layer=layer,
        name=fn_name,
        start_ms=time.monotonic() * 1000,
        depth=depth,
        data_summary=_summarize_args(args, kwargs),
    )

    # Push onto stack (create new list to avoid mutation issues)
    new_stack = stack + [span]
    _current_span_stack.set(new_stack)

    # Broadcast to SSE clients
    _broadcast({"event": "span_start", "span": span.to_dict()})

    return span


def _end_span(span: TraceSpan, *, error: Exception | None = None) -> None:
    """Mark span as complete and broadcast span_end event."""
    span.end_ms = time.monotonic() * 1000
    if error is not None:
        span.error = type(error).__name__

    # Pop from stack
    stack = _current_span_stack.get()
    new_stack = [s for s in stack if s.span_id != span.span_id]
    _current_span_stack.set(new_stack)

    # Broadcast to SSE clients
    _broadcast({"event": "span_end", "span": span.to_dict()})


def _broadcast(span_dict: dict) -> None:
    """Fire-and-forget broadcast to all SSE clients.

    Safe to call from sync or async context. Removes dead (full) queues.
    """
    _ring_buffer.append(span_dict)

    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(span_dict)
        except asyncio.QueueFull:
            dead.append(q)

    for q in dead:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


# ── Instrumentation decorator ──────────────────────────────────────────────

def instrument(layer: str, name: str | None = None) -> Callable:
    """Decorator factory for sync, async, and async-generator functions.

    Usage:
        @instrument("service")
        def save_entry(...): ...

        @instrument("ai")
        async def generate_entry_suggestion(...): ...

        @instrument("service")
        async def stream_event_chat_events(...): ...  # async generator
    """

    def decorator(fn: Callable) -> Callable:
        fn_name = name or fn.__qualname__

        if inspect.isasyncgenfunction(fn):
            # Async generator: stream results
            @functools.wraps(fn)
            async def asyncgen_wrapper(*args: Any, **kwargs: Any) -> AsyncGenerator:
                span = _begin_span(layer, fn_name, args, kwargs)
                try:
                    async for item in fn(*args, **kwargs):
                        yield item
                    _end_span(span)
                except Exception as exc:
                    _end_span(span, error=exc)
                    raise

            return asyncgen_wrapper

        elif inspect.iscoroutinefunction(fn):
            # Async function
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                span = _begin_span(layer, fn_name, args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                    _end_span(span)
                    return result
                except Exception as exc:
                    _end_span(span, error=exc)
                    raise

            return async_wrapper

        else:
            # Sync function
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                span = _begin_span(layer, fn_name, args, kwargs)
                try:
                    result = fn(*args, **kwargs)
                    _end_span(span)
                    return result
                except Exception as exc:
                    _end_span(span, error=exc)
                    raise

            return sync_wrapper

    return decorator


# ── Context manager instrumentation ────────────────────────────────────────

def make_instrumented_connection_context(
    original_factory: Callable[..., ContextManager[_T]],
) -> Callable[..., ContextManager[_T]]:
    """Wrap a context manager factory to measure the duration of the with-block.

    The standard @instrument decorator only wraps the factory call, not the
    context block itself. This wrapper measures the full duration inside the
    with statement.

    Usage:
        connection_context = make_instrumented_connection_context(connection_context)
    """

    @functools.wraps(original_factory)
    @contextmanager
    def wrapper(*args: Any, **kwargs: Any) -> Iterator[_T]:
        span = _begin_span("db", "connection_context", args, kwargs)
        try:
            with original_factory(*args, **kwargs) as cm:
                yield cm
            _end_span(span)
        except Exception as exc:
            _end_span(span, error=exc)
            raise

    return wrapper


# ── SSE subscription management ─────────────────────────────────────────────

async def subscribe() -> asyncio.Queue[dict]:
    """Register a new SSE client and replay recent spans.

    Returns a queue that will receive {"event": "span_start"|"span_end", "span": {...}}.
    """
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)

    # Replay ring buffer so late joiners see context
    for span_dict in _ring_buffer:
        q.put_nowait(span_dict)

    async with _subscribers_lock:
        _subscribers.append(q)

    return q


async def unsubscribe(q: asyncio.Queue[dict]) -> None:
    """Unregister an SSE client."""
    async with _subscribers_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


# ── HTTP Middleware ────────────────────────────────────────────────────────

class TraceMiddleware:
    """Raw ASGI middleware that traces each HTTP request.

    Creates a root "route" span for every request, excluding /dev/tracer
    endpoints to avoid recursive tracing.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Skip tracing the tracer itself
        if path.startswith("/dev/tracer"):
            await self.app(scope, receive, send)
            return

        # Initialize per-request trace context
        trace_id = uuid.uuid4().hex[:16]
        _current_trace_id.set(trace_id)
        _current_span_stack.set([])

        # Create root span for the request
        method = scope.get("method", "???")
        root_span = _begin_span("route", f"{method} {path}", (), {})

        try:
            await self.app(scope, receive, send)
            _end_span(root_span)
        except Exception as exc:
            _end_span(root_span, error=exc)
            raise
