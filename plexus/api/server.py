"""
The runtime's half of PROTOCOL.md.

One WebSocket at /ws, one editor per connection, one engine per editor.

**On binding.** This drives card hardware and can open sockets to other
machines, so it takes the same position atrium's `serve` does: loopback needs
no token because the operating system already decides who can connect, and
anything else refuses to start without one. That is a deliberate refusal rather
than a warning — an open control plane for a card relay is not something to
leave to a log line nobody reads.

**On the Host header.** DNS rebinding defeats an origin check on its own: an
attacker's page re-resolves its own name to 127.0.0.1 and is then same-origin.
Checking the Host header breaks that, because the request still arrives
carrying the attacker's name.

**On the Origin header, which is the one that matters.** A browser does *not*
apply the same-origin policy to WebSockets. Any page the operator visits can
open ``ws://127.0.0.1:8900/ws``, and the Host header on that handshake is
honestly ``127.0.0.1:8900`` — so a Host check alone lets it through. Measured,
before this was here: a connection with ``Origin: https://evil.example.com``
was accepted and could have armed a card relay. The browser is a confused
deputy, and "loopback is safe because the OS decides who can connect" is not
true when the thing connecting is the operator's own browser on someone else's
instructions.

So the Origin is checked against an allow-list. A missing Origin is allowed,
because that is a native client — the CLI, a script, a test — and this attack
needs a browser, which always sends one and never lets a page forge it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from typing import Optional, Set

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from plexus.api.session import PROTOCOL_VERSION, ProtocolMismatch, Runtime, Session
from plexus.runtime import node_implementations

log = logging.getLogger(__name__)

TOKEN_VARIABLE = "PLEXUS_API_TOKEN"
HOSTS_VARIABLE = "PLEXUS_ALLOWED_HOSTS"
ORIGINS_VARIABLE = "PLEXUS_ALLOWED_ORIGINS"

_DEFAULT_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"}

#: The largest frame that will be parsed. A graph is a few kilobytes; anything
#: past this is someone filling memory rather than describing a rig.
MAX_FRAME_BYTES = 1_000_000


def is_loopback(host: str) -> bool:
    """
    Is this a name or address for this machine?

    Case-folded, because a hostname is case-insensitive and this was not:
    `plexus serve --host LOCALHOST` refused to start, telling the operator
    they were binding beyond loopback when they were not.
    """
    host = (host or "").strip().lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def allowed_hosts() -> Set[str]:
    extra = os.environ.get(HOSTS_VARIABLE, "")
    return _DEFAULT_HOSTS | {h.strip().lower() for h in extra.split(",") if h.strip()}


def host_ok(header: Optional[str]) -> bool:
    if not header:
        return False
    if header.startswith("["):  # bracketed IPv6, with or without a port
        name = header.split("]")[0] + "]"
    else:
        name = header.rsplit(":", 1)[0] if header.count(":") == 1 else header
    name = name.lower()
    if name in allowed_hosts():
        return True
    try:
        return ipaddress.ip_address(name.strip("[]")).is_loopback
    except ValueError:
        return False


def origin_ok(origin: Optional[str]) -> bool:
    """
    May a page from this origin drive the runtime?

    No Origin at all is a native client and is allowed — a browser always sends
    one and a page cannot forge it, so this check is exactly as strong as the
    browser's own guarantee. A loopback origin is the editor. Anything else has
    to be named in PLEXUS_ALLOWED_ORIGINS.
    """
    if origin is None or origin == "":
        return True
    if origin == "null":
        # A sandboxed iframe or a file:// page. Nothing legitimate here does
        # that, and it is precisely what a hostile embed looks like.
        return False

    configured = {
        o.strip().rstrip("/").lower()
        for o in os.environ.get(ORIGINS_VARIABLE, "").split(",")
        if o.strip()
    }
    if origin.rstrip("/").lower() in configured:
        return True

    try:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return bool(parsed.hostname) and is_loopback(parsed.hostname)


def guard_bind(host: str) -> None:
    """Refuse to serve a card relay to the network without authentication."""
    if is_loopback(host):
        return
    if os.environ.get(TOKEN_VARIABLE):
        log.warning(
            "Serving on %s — reachable beyond this machine. Token auth is on.", host
        )
        return
    raise SystemExit(
        "\nRefusing to bind %s without authentication.\n\n"
        "This would let anyone who can reach this port drive a card relay.\n"
        "Either bind loopback (--host 127.0.0.1, the default), or set a token:\n\n"
        '    $env:%s = python -c "import secrets;print(secrets.token_urlsafe(32))"\n\n'
        "Also set %s to the name you serve on.\n" % (host, TOKEN_VARIABLE, HOSTS_VARIABLE)
    )


def token_ok(offered: Optional[str]) -> bool:
    """
    Constant-time when a token is configured; open when one is not.

    Compared as bytes because `compare_digest` on `str` refuses anything
    outside ASCII — it raised TypeError on a non-ASCII token, which is an
    unhandled exception during the handshake rather than a closed socket. A
    caller who can choose the token can choose one that crashes the check.
    """
    expected = os.environ.get(TOKEN_VARIABLE)
    if not expected:
        return True
    if not offered:
        return False
    return secrets.compare_digest(
        offered.encode("utf-8", "surrogatepass"),
        expected.encode("utf-8", "surrogatepass"),
    )


async def _too_big(socket: WebSocket, size: int) -> None:
    await socket.send_json(
        {
            "type": "error",
            "message": "Frame is %d bytes; the limit is %d." % (size, MAX_FRAME_BYTES),
        }
    )


def create_app() -> FastAPI:
    # One runtime for the process, not one per socket. PROTOCOL.md promises
    # that losing the socket is not losing the run; a per-connection engine
    # made that false, and made a browser reload release the card in the middle
    # of a transaction.
    runtime = Runtime()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        # The run outlives a socket, not the process. Whatever is still bound
        # when the server goes down is released here, and this is the only
        # place left that releases without being asked.
        await asyncio.get_event_loop().run_in_executor(None, runtime.engine.stop)

    app = FastAPI(
        title="Plexus runtime",
        version="0.1.0",
        description="Binds a Plexus graph to real hardware and drives it.",
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    @app.get("/api/health")
    async def health(request: Request) -> JSONResponse:
        """
        Enough to tell a runtime is there and what it can bind.

        Behind the same Host check as the socket. It had none at all, so a
        page on any origin could read which nodes this build can bind and
        whether a token is set — a map of the machine, handed out for the
        asking. The answer stays open to anything that passes that check,
        because the editor reads it before it has a token to offer and
        because "is a runtime there" is the question it exists to answer.
        """
        if not host_ok(request.headers.get("host")):
            return JSONResponse({"error": "refused"}, status_code=403)
        return JSONResponse(
            {
                "protocol": PROTOCOL_VERSION,
                "nodes": node_implementations(),
                "authenticated": bool(os.environ.get(TOKEN_VARIABLE)),
            }
        )

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        if not host_ok(socket.headers.get("host")):
            await socket.close(code=4403)
            return

        # The one that stops a page the operator happens to be visiting from
        # arming a card relay. See the module docstring.
        if not origin_ok(socket.headers.get("origin")):
            log.warning(
                "refused a websocket from origin %r", socket.headers.get("origin")
            )
            await socket.close(code=4403)
            return

        offered = socket.query_params.get("token") or socket.headers.get("x-plexus-token")
        if not token_ok(offered):
            await socket.close(code=4401)
            return

        await socket.accept()
        session = Session(socket.send_json, runtime)
        await session.start()

        try:
            while True:
                # Read the raw ASGI message rather than `receive_text()`:
                # that does `message["text"]`, so one binary frame killed the
                # session with a KeyError and left the socket open with nobody
                # reading it. A client that sends bytes gets told so.
                incoming = await socket.receive()
                if incoming["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(incoming.get("code", 1000))

                raw = incoming.get("text")
                if raw is None:
                    payload = incoming.get("bytes")
                    if payload is None:
                        continue
                    # Decoded rather than refused: `send_json(mode="binary")`
                    # is a legal way to send a frame, and PROTOCOL.md does not
                    # say which. What is refused is bytes that are not UTF-8.
                    if len(payload) > MAX_FRAME_BYTES:
                        await _too_big(socket, len(payload))
                        continue
                    try:
                        raw = payload.decode("utf-8")
                    except UnicodeDecodeError:
                        await socket.send_json(
                            {"type": "error", "message": "Frames are UTF-8 JSON."}
                        )
                        continue

                # `len` counts characters and the cap is in bytes — one
                # character can be four of them, so a frame inside the
                # character count could still be four times the limit. The
                # cheap check runs first, so a flood of oversized frames is
                # rejected without encoding any of them.
                size = len(raw)
                if size <= MAX_FRAME_BYTES:
                    size = len(raw.encode("utf-8"))
                if size > MAX_FRAME_BYTES:
                    await _too_big(socket, size)
                    continue

                try:
                    message = json.loads(raw)
                except ValueError:
                    await socket.send_json(
                        {"type": "error", "message": "Frames are JSON."}
                    )
                    continue
                if not isinstance(message, dict):
                    await socket.send_json(
                        {"type": "error", "message": "Frames are objects."}
                    )
                    continue
                await session.dispatch(message)
        except WebSocketDisconnect:
            if session.engine.running:
                log.info("editor disconnected; the relay is still running")
            else:
                log.info("editor disconnected")
        except ProtocolMismatch:
            await socket.close(code=4400)
        except Exception:  # noqa: BLE001 - one bad editor must not kill the process
            log.exception("session failed")
        finally:
            # Detaches this socket's listener and cancels its tasks. It does
            # *not* release the hardware: the run belongs to the process, and
            # the promise in PROTOCOL.md is that a reconnecting editor finds it
            # still going. `stop`, or the process ending, releases it.
            await session.close()

    return app


app = create_app()


#: Any `token=…` in a URL, however it is spelled.
_TOKEN_IN_URL = re.compile(r"([?&](?:token|x-plexus-token)=)[^&\s\"']+", re.IGNORECASE)


class RedactToken(logging.Filter):
    """
    Keeps the token out of the access log.

    A browser cannot set a header on a WebSocket, so `?token=…` is the only way
    to authenticate one from a page — and uvicorn's access log writes the path
    it was given, query string and all. The secret then sits in a log file, in
    a terminal's scrollback, and in whatever collects either, which is a worse
    place for it than the environment variable it came from.

    Applied in `serve()` rather than at import: a library consumer's logging is
    theirs to configure.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                _TOKEN_IN_URL.sub(r"\1[redacted]", a) if isinstance(a, str) else a
                for a in (
                    record.args
                    if isinstance(record.args, tuple)
                    else (record.args,)
                )
            )
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_URL.sub(r"\1[redacted]", record.msg)
        return True


def serve(host: str = "127.0.0.1", port: int = 8900, reload: bool = False) -> None:
    """Run the server. Refuses a non-loopback bind without a token."""
    import uvicorn

    guard_bind(host)
    for name in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(RedactToken())

    uvicorn.run(
        "plexus.api.server:app" if reload else app,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
