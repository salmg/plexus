"""
The runtime's half of PROTOCOL.md.

One WebSocket at /ws, one editor per connection, one engine per editor.

**On binding.** This drives card hardware and can open sockets to other
machines, so it takes the same position atrium's `serve` does: loopback needs
no token because the operating system already decides who can connect, and
anything else refuses to start without one. That is a deliberate refusal rather
than a warning — an open control plane for a card relay is not something to
leave to a log line nobody reads.

**On the Host header.** A browser enforces same-origin, but DNS rebinding
defeats it: an attacker's page re-resolves its own name to 127.0.0.1 and is
then treated as same-origin. Checking the Host header breaks that, because the
request still arrives carrying the attacker's name.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from typing import Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from plexus.api.session import PROTOCOL_VERSION, ProtocolMismatch, Session
from plexus.runtime import node_implementations

log = logging.getLogger(__name__)

TOKEN_VARIABLE = "PLEXUS_API_TOKEN"
HOSTS_VARIABLE = "PLEXUS_ALLOWED_HOSTS"

_DEFAULT_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"}


def is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
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


def guard_bind(host: str) -> None:
    """Refuse to serve a card relay to the network without authentication."""
    if is_loopback(host):
        return
    if os.environ.get(TOKEN_VARIABLE):
        log.warning("Serving on %s — reachable beyond this machine. Token auth is on.", host)
        return
    raise SystemExit(
        "\nRefusing to bind %s without authentication.\n\n"
        "This would let anyone who can reach this port drive a card relay.\n"
        "Either bind loopback (--host 127.0.0.1, the default), or set a token:\n\n"
        '    $env:%s = python -c "import secrets;print(secrets.token_urlsafe(32))"\n\n'
        "Also set %s to the name you serve on.\n" % (host, TOKEN_VARIABLE, HOSTS_VARIABLE)
    )


def token_ok(offered: Optional[str]) -> bool:
    """Constant-time when a token is configured; open when one is not."""
    expected = os.environ.get(TOKEN_VARIABLE)
    if not expected:
        return True
    return bool(offered) and secrets.compare_digest(offered, expected)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Plexus runtime",
        version="0.1.0",
        description="Binds a Plexus graph to real hardware and drives it.",
    )

    @app.get("/api/health")
    async def health() -> JSONResponse:
        """Enough to tell a runtime is there and what it can bind."""
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

        offered = socket.query_params.get("token") or socket.headers.get("x-plexus-token")
        if not token_ok(offered):
            await socket.close(code=4401)
            return

        await socket.accept()
        session = Session(socket.send_json)
        await session.start()

        try:
            while True:
                message = await socket.receive_json()
                if not isinstance(message, dict):
                    await socket.send_json(
                        {"type": "error", "message": "Frames are objects."}
                    )
                    continue
                await session.handle(message)
        except WebSocketDisconnect:
            log.info("editor disconnected")
        except ProtocolMismatch:
            await socket.close(code=4400)
        except Exception:  # noqa: BLE001 - one bad editor must not kill the process
            log.exception("session failed")
        finally:
            # The relay keeps hardware claimed, so releasing it is the one
            # thing that must happen however this connection ended.
            await session.close()

    return app


app = create_app()


def serve(host: str = "127.0.0.1", port: int = 8900, reload: bool = False) -> None:
    """Run the server. Refuses a non-loopback bind without a token."""
    import uvicorn

    guard_bind(host)
    uvicorn.run(
        "plexus.api.server:app" if reload else app,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
