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

## Trying the agent

The **EMV agent** is a terminal whose next command a language model writes,
reading each answer before it decides the next — atrium's agent, narrowed to the
one thing a terminal does: ask. It needs a model back end, not a reader, so it
runs on the same laptop as everything else. The only new thing is a key.

Set one of these — atrium follows whichever it finds and names the one to set if
it finds none:

```bash
export ANTHROPIC_API_KEY=sk-ant-…                 # Claude
export OPENAI_API_KEY=sk-…                         # OpenAI
export ATRIUM_LLM_BASE_URL=http://localhost:11434/v1 \
  ATRIUM_LLM_MODEL=llama3.1                         # a local model (Ollama, vLLM, …)
```

Then run the rig that reads the same recorded card the probe does, but with the
agent driving:

```bash
plexus run examples/agent-a-capture.plexus.yaml
```

The trace scrolls as it does for any terminal — except the commands are the
model's, chosen from the last reply rather than a script — and it prints a short
summary of what the card turned out to be when it calls it done. In the editor
it is the same: drop an **EMV agent**, wire its card port to a capture (or any
card node), give it a task, and press **Run**.

Two things the node holds to, both by design:

- **It stops.** A model that never calls finish is halted at a ceiling — a
  bounded number of turns, and of commands — so a runaway costs tokens, not an
  endless relay. Stop halts it between commands like any other run.
- **It is pointed at a card, and drives it on its own.** That is the whole point
  of the node, and the reason to point it only at a card you own or have written
  permission to test — the same terms as everything else here.

With no key set it refuses at bind and names the variable to set, exactly as a
hardware node names the reader it wants. `plexus nodes` marks it with a dot until
a model is configured.

---

## What is not built

**This is the honest part of this page.** The engine binds, drives, rewrites,
reports and releases. The cards it can bind today are a recorded capture and a
contact card in a **PC/SC reader**; the terminals are the fingerprint probe and
the EMV agent, the agent only once a model back end is configured
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or a local `ATRIUM_LLM_BASE_URL`).

The PC/SC reader is discovered when the editor connects, and again whenever
you press **Look again** under the reader picker: plug one in, press it, and the
reader appears, `plexus nodes` stops marking `pcsc` with a dot, and
`/api/health` reports it `ready`. It needs pyscard, which is the `hardware`
extra, installed into the same Python that runs Plexus. On Linux pyscard builds
from source, so the PC/SC headers and swig come first:

```bash
sudo apt install pcscd libpcsclite-dev swig python3-dev   # Linux only
pip install -e ".[hardware]"
```

On a machine with no reader, or no pyscard, `pcsc` is `pending` again and says
which: the picker, `devices` in the console and `plexus nodes` all print the
same sentence. The cases it tells apart are atrium not checked out, pyscard not
in this Python (naming the interpreter), pcscd not answering, no reader at all,
and only atrium's virtual reader (`Virtual PCD`) visible. The virtual reader is
never offered as a card slot, because it is where atrium presents a card to a
terminal.

Every other hardware node **resolves in a graph and refuses at bind time, naming
what it needs**:

```
error: The cardproxy node is not implemented in this build.
       It needs atrium's remote transport and a running card proxy.
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
| PC/SC reader | `transport.local.LocalCardTransport` — **done** |
| ACR122U field | `transport.contactless.ContactlessTransport` |
| NFCGate session | `transport.nfcgate.NFCGateTransport` |
| Remote card | `transport.remote.RemoteCardTransport` |
| ACR122U target | `nfc.emulator` — the PN532 in target mode |
| Virtual reader | `virtual_card.VirtualCard` + `relay_os.RelayOS` |
| SimTrace2 | the `simtrace2-remsim` daemon, discovered not launched |

`CardTransport` is `connect` / `get_atr` / `transmit` / `disconnect`, which is
exactly what a card-side node has to be. The four transports above are
therefore close to drop-in. The PC/SC reader is the worked example: the node is
`PcscCard` in `plexus/nodes/card.py`, a thin wrapper over
`LocalCardTransport`, and the readers it binds come from device discovery,
which the engine now answers from what atrium can see:

```python
def devices(self) -> List[Any]:
    """Hardware the runtime can see right now."""
    return discover_devices()
```

`discover_devices` asks `plexus.adapters.atrium.pcsc_discovery`, the one place
atrium's `core.readers` is reached, and `discover_hardware` returns the same
list with the reason it is empty. Add a transport by giving discovery the
kind it reports and writing the node beside `PcscCard`.

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
