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

    Reaching into `_send_queue` is deliberate. It is where the app's outbound
    frames land, and it is the only place a deadline can be applied.
    """
    outbound = getattr(socket, "_send_queue", None)
    if outbound is None:  # pragma: no cover - a starlette that moved the queue
        return socket.receive_json()
    try:
        message = outbound.get(timeout=max(deadline - time.monotonic(), 0.0))
    except queue.Empty:
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
