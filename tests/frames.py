"""
Reading frames from a test WebSocket, with a deadline.

Its own module because two suites need it, and because the alternative —
`from tests.test_server import collect` — only resolves when pytest happens to
be started from the repository root. pytest puts this directory on `sys.path`
for every test module in it, so `from frames import collect` resolves from
anywhere.

Everything here exists for one reason: `receive_json` is a `queue.get()` with
no timeout. A test waiting for a frame the runtime will never send hung the
whole suite rather than failing it — the worst failure mode available, because
it reads as a broken machine, and on the machine that wrote the fixture it
never happened at all.
"""

from __future__ import annotations

import json
import os
import queue
import time
from typing import Any, Dict, List

import anyio


def unwrap(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A batch is a frame carrying frames; everything else is one."""
    if frame.get("type") == "batch":
        out: List[Dict[str, Any]] = []
        for inner in frame["frames"]:
            out.extend(unwrap(inner))
        return out
    return [frame]


# How long any wait in this file may take before it is a failure rather than a
# wait. Deliberately generous: the point is not to be tight, it is to be finite.
FRAME_BUDGET_S = float(os.environ.get("PLEXUS_TEST_FRAME_BUDGET", "20"))


class Timeout(Exception):
    """The server sent nothing before the deadline."""


def pull(socket, deadline: float) -> Dict[str, Any]:
    """
    One frame, or `Timeout` once the deadline has passed.

    `socket.receive_json()` is a `queue.get()` with no timeout, so every wait
    in this file used to be unbounded. `limit` counts reads, and a read that
    never returns never reaches the next count — so a test waiting for a frame
    the runtime will not send hung the suite instead of failing it. That is the
    worst failure mode available: it reads as a broken machine, and on the
    machine that wrote the capture it never happened at all.

    Reaching into starlette's internals is deliberate. The outbound frames are
    where a deadline can be applied, and starlette keeps them in a different
    place depending on its version — so both are known about here, and neither
    being found is an error rather than a silent unbounded read.

    That fallback used to be `return socket.receive_json()`, with a comment
    saying it was for "a starlette that moved the queue". Starlette moved the
    queue: 0.44 has `_send_queue`, 1.6 has an anyio stream read through a
    portal. Python 3.8 resolves the old one and 3.12 the new, so the suite
    passed on the floor and hung on the version a rig actually runs — in
    `test_waiting_for_a_frame_that_never_comes_fails_rather_than_hanging`,
    which is the test that exists to prove this cannot happen.
    """
    remaining = max(deadline - time.monotonic(), 0.0)

    # starlette < 1.0 — a plain queue of outbound messages.
    outbound = getattr(socket, "_send_queue", None)
    if outbound is not None:
        try:
            message = outbound.get(timeout=remaining)
        except queue.Empty:
            raise Timeout() from None
    else:
        # starlette >= 1.0 — an anyio memory stream, read through the blocking
        # portal the test session already owns.
        stream = getattr(socket, "_send_rx", None)
        portal = getattr(socket, "portal", None)
        if stream is None or portal is None:  # pragma: no cover - a third shape
            raise RuntimeError(
                "No way to read this starlette TestClient with a deadline: it "
                "has neither `_send_queue` nor `_send_rx`. Refusing rather "
                "than reading without one, because an unbounded read hangs the "
                "suite instead of failing it."
            )

        async def _receive() -> Any:
            with anyio.fail_after(remaining):
                return await stream.receive()

        try:
            message = portal.call(_receive)
        except TimeoutError:
            raise Timeout() from None
    if isinstance(message, BaseException):
        raise message
    if message.get("type") == "websocket.close":
        raise AssertionError(
            "the server closed the socket (code %s) instead of answering."
            % message.get("code")
        )
    text = message.get("text")
    if text is None:
        text = message["bytes"].decode("utf-8")
    return json.loads(text)


def collect(
    socket, *, until: str, limit: int = 400, budget: float = FRAME_BUDGET_S
) -> List[Dict[str, Any]]:
    """Read until a frame of `until` type arrives, flattening batches."""
    seen: List[Dict[str, Any]] = []
    deadline = time.monotonic() + budget
    for _ in range(limit):
        try:
            arrived = unwrap(pull(socket, deadline))
        except Timeout:
            raise AssertionError(
                "no %r frame within %.1fs; last frames: %r"
                % (until, budget, seen[-5:])
            ) from None
        for frame in arrived:
            seen.append(frame)
            if frame.get("type") == until:
                return seen
    raise AssertionError("no %r frame in %d reads: %r" % (until, limit, seen[-5:]))


def collect_state(
    socket, want: str, limit: int = 400, budget: float = FRAME_BUDGET_S
) -> List[Dict[str, Any]]:
    """
    Read until a *particular* state arrives.

    A run passes through several — arming, running, stopping, stopped — so
    waiting for "a state frame" catches whichever came first and tells you
    nothing.
    """
    seen: List[Dict[str, Any]] = []
    deadline = time.monotonic() + budget
    for _ in range(limit):
        try:
            arrived = unwrap(pull(socket, deadline))
        except Timeout:
            break
        for frame in arrived:
            seen.append(frame)
            if frame.get("type") == "state" and frame.get("state") == want:
                return seen
    states = [f.get("state") for f in seen if f.get("type") == "state"]
    raise AssertionError("never reached %r; saw %r" % (want, states))
def collect_until(
    socket, done, limit: int = 400, budget: float = FRAME_BUDGET_S
) -> List[Dict[str, Any]]:
    """
    Read until `done(seen)` is true.

    Never wait for something the runtime does not promise. This docstring said
    exactly that while the code did the opposite: `limit` bounded the number of
    reads, not the wait, and the wait was the unbounded half. `pull` puts a
    deadline on it, so a condition the runtime will never meet now fails with
    the frames it did send.
    """
    seen: List[Dict[str, Any]] = []
    deadline = time.monotonic() + budget
    for _ in range(limit):
        try:
            seen.extend(unwrap(pull(socket, deadline)))
        except Timeout:
            raise AssertionError(
                "condition never met within %.1fs; last frames: %r"
                % (budget, seen[-5:])
            ) from None
        if done(seen):
            return seen
    raise AssertionError("condition never met; last frames: %r" % seen[-5:])
