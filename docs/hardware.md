# Running on hardware

The editor and the simulator get you a long way. This is what happens when you
want a real card on the other end.

---

## The shape of it

```
  your laptop              the rig (Linux, readers attached)
  ┌───────────┐            ┌───────────────────────────────────┐
  │ a browser │            │ plexus up                         │
  │           │  ws://…    │  ├─ the editor, served from here  │
  │           │  ───────►  │  ├─ the engine                    │
  └───────────┘            │  ├─ plexus/adapters → atrium      │
                           │  └─ pcscd, vpcd, simtrace2-remsim │
                           └───────────────────────────────────┘
```

The editor never touches hardware. It sends a graph; the runtime re-checks it,
binds what it names, and streams back what crosses.

## Start the runtime

On the rig, one command serves the editor beside it:

```bash
pip install -e ".[runtime]"
plexus up
```

The top bar swaps **simulated** for the runtime's address. That badge is the
only thing standing between a simulated result and a claim about a card, so it
is always on screen.

To drive the rig from an editor somewhere else — a dev server, or a copy you
already have open — start the runtime on its own and use the URL it prints:

```bash
plexus serve
```

```
Plexus runtime on ws://127.0.0.1:8900/ws
Point the editor at it:  http://localhost:5173/?runtime=ws://127.0.0.1:8900/ws
```

### Across machines

The readers are rarely on the laptop with the browser. Two options, and the
first is better:

**Tunnel it.** Leave the runtime on loopback and forward the port:

```bash
ssh -L 8900:127.0.0.1:8900 you@rig
```

Then open <http://127.0.0.1:8900/> on the laptop. `plexus up` on the rig served
the editor; the tunnel makes it local; and every check on the far end sees
loopback, for the Host header and the Origin alike. Nothing is exposed, no
token is needed, and there is nothing to configure. This is the arrangement to
reach for.

**Or bind it, with a token.** `serve` **refuses to start** on a non-loopback
address without one:

```bash
export PLEXUS_API_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
export PLEXUS_ALLOWED_HOSTS=rig.local
export PLEXUS_ALLOWED_ORIGINS=http://laptop.local:5173
plexus serve --host 0.0.0.0
```

Then `?runtime=ws://rig.local:8900/ws?token=…`. Read
[What it refuses](security.md) before doing this.

If you use `plexus up` rather than `serve` here, the editor is served *from the
rig*, so the origin to allow is the runtime's own address —
`PLEXUS_ALLOWED_ORIGINS=http://rig.local:8900`. **The runtime does not trust a
page merely because it served it.** That is deliberate: "the page came from
this address" is a claim about DNS, and the allow-list is where an operator
says which names they meant. `tests/test_security.py` pins it.

## The workspace

Every path a graph names — a capture to read, a trace to write — resolves
inside the directory the runtime was started in. A graph cannot reach outside
it:

```
Refusing to write /etc/cron.d/x outside the workspace
(/home/you/plexus). Choose a path inside it, or start the runtime with
PLEXUS_WORKSPACE set.
```

Widen it deliberately:

```bash
PLEXUS_WORKSPACE=/srv/captures plexus serve
```

A graph cannot widen it for you. That is the point.

---

## What is not built

**This is the honest part of this page.** The engine binds, drives, rewrites,
reports and releases — but the only card it can bind today is a recorded
capture, and the only terminal is the fingerprint probe.

Every other node **resolves in a graph and refuses at bind time, naming what it
needs**:

```
error: The pcsc node is not implemented in this build.
       It needs pyscard and a reader attached to this machine.
```

That is deliberate. A card node that silently pretends is the worst possible
failure in a tool whose output is evidence, so the missing ones say so rather
than being absent from the palette and making the graph look corrupt.

`plexus nodes` marks them with a dot.

### What the adapters have to do

The seams are already the right shape, which is why this is adapter work rather
than design work:

| Node | atrium already provides |
|---|---|
| PC/SC reader | `transport.local.LocalCardTransport` |
| ACR122U field | `transport.contactless.ContactlessTransport` |
| NFCGate session | `transport.nfcgate.NFCGateTransport` |
| Remote card | `transport.remote.RemoteCardTransport` |
| ACR122U target | `nfc.emulator` — the PN532 in target mode |
| Virtual reader | `virtual_card.VirtualCard` + `relay_os.RelayOS` |
| SimTrace2 | the `simtrace2-remsim` daemon, discovered not launched |

`CardTransport` is `connect` / `get_atr` / `transmit` / `disconnect`, which is
exactly what a card-side node has to be. The four transports above are
therefore close to drop-in — what they need is device discovery, which is the
one thing the engine currently returns empty:

```python
def devices(self) -> List[Any]:
    """Empty in this build, and deliberately so rather than invented."""
    return []
```

Everything in `plexus/adapters/` is the only place that imports atrium. When
atrium moves, what breaks is in there.

---

## Why a laptop can run a whole graph

Worth understanding, because it is what makes the engine testable at all.

Atrium splits cleanly in half, and only one half needs hardware.
`mutation_engine`, `transport.recorded`, `emv_logger` and `card_fingerprint`
are **pure standard library** — they import with no reader attached and no
pyscard installed. `transport.local`, `transport.contactless` and the emulator
are the other half.

So a graph made of a capture and some transforms is a complete, honest relay
that runs anywhere, and that is what the test suite drives: bind, drive,
rewrite, report, release, through atrium's real mutation engine.

```bash
pytest
```

When you write a hardware adapter, test the *engine* against that rig and keep
the hardware for testing the adapter. Reaching for a reader to check a graph
change is the slow path and it does not prove more.
