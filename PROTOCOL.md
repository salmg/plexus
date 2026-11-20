# The runtime protocol

One WebSocket, JSON frames, no framing of our own. The editor
(`ui/src/runtime/ws.ts`) and the runtime are the two ends of this document, and
neither may grow a field the other has not agreed to.

The simulator in `ui/src/runtime/mock.ts` emits the same events in the same
order without a socket. That is the point of it: a panel built against the
simulator works against a card.

## Connecting

The editor dials `ws://<host>:<port>/ws` and sends `hello` first.

Which host and port, when nothing says? `?runtime=` in the URL wins, then
`VITE_PLEXUS_RUNTIME` from the build. With neither, the editor asks its own
origin for `/api/health`: a reply carrying a numeric `protocol` means a runtime
served this page, and the socket is `ws` beside it — `wss://` if the page came
over HTTPS. No reply, and it stays on the simulator. That probe is why
`plexus up` needs no address pasted anywhere, and why an editor opened from a
file or a static host is quietly simulated rather than broken.

```json
{ "type": "hello", "version": 1 }
```

A runtime that does not speak version 1 replies with `error` and closes. The
editor reconnects with exponential backoff from 500 ms to 8 s. **Losing the
socket is not losing the run**: the relay keeps going, and on reconnect the
runtime replays current state (`state`, `devices`, and one `node` frame per
bound node) so the editor can catch up without a restart.

## Editor → runtime

| Frame | Shape | Meaning |
|---|---|---|
| `hello` | `{version}` | First frame, always |
| `arm` | `{graph}` | Bind the hardware this graph names and start |
| `stop` | `{}` | Release everything and stop |
| `command` | `{id, line}` | One console line; `id` is echoed back on `result` |
| `devices` | `{}` | Look for hardware again; answered with `devices` then `implementations` |

`graph` is the same object the editor saves as `.plexus.yaml`, as JSON.
`plexus.graph.Graph` reads it.

A runtime must refuse to arm a graph that fails its own validation, and must
run that validation itself rather than trusting the editor's — a graph arriving
over a socket is untrusted input that names hardware and dials addresses.

## Runtime → editor

| Frame | Shape |
|---|---|
| `state` | `{state, detail?}` — one of `stopped`, `arming`, `running`, `stopping`, `faulted` |
| `node` | `{id, telemetry}` — partial `{status, apdus, errors, lastMs, meanMs, detail}` |
| `edge` | `{id, telemetry}` — partial `{apdus, lastMs, bytesOut, bytesIn}` |
| `apdu` | `{event}` — `{seq, t, edge, dir, hex, ms?, mutatedBy?, note?}` |
| `log` | `{line}` — `{t, level, source, text}` |
| `devices` | `{devices, problems}` — the full list, every time; not a delta. `problems` says, per device kind, why none is ready |
| `implementations` | `{needs}` — node types this runtime *cannot* bind, keyed to what each one wants |
| `result` | `{id, lines, level?}` — the answer to one `command` |
| `error` | `{message}` |
| `batch` | `{frames}` — any of the above, applied in order |

`t` is milliseconds since the run started, not a wall clock. `hex` is uppercase
with no separators.

### What a runtime cannot bind

`implementations` is sent in the reply to `hello`, and again after each
`devices` request, because whether `pcsc` binds depends on the reader and
pyscard the machine has now. A key present in
`needs` is a node type this runtime has no implementation for, and the value
is the sentence it would refuse with — the same sentence, from the same
table, that `arm` raises:

```json
{ "type": "implementations",
  "needs": { "pcsc": "pyscard in this Python (pip install -e \".[hardware]\")",
             "nfcgate": "atrium's NFCGate transport and a reachable server" } }
```

An empty object means everything binds, which is what the simulator sends.

### Why a device is not ready

`devices` carries `problems`, keyed by device kind, one sentence for a person
for each kind that is not ready. A kind that is ready has no key, so `{}` means
everything discovered is usable.

- `pcsc`: atrium not checked out, pyscard not installed in the runtime's Python
  (naming the interpreter), pcscd not answering, no reader attached, or only
  atrium's virtual reader visible.
- `simtrace2`: not Linux, no atrium checkout, or no board on USB. A board
  with no `simtrace2-remsim` running is **not** a problem: that is the state
  to arm in, because the daemon can only start once the graph is armed (see
  [Armed, then the daemon](#armed-then-the-daemon)). Its `detail` says so.

An editor that predates it ignores it and shows the list it always did.

```json
{ "type": "devices",
  "devices": [{ "id": "simtrace2:1-2.3", "kind": "simtrace2", "name": "SimTrace2 (USB 1-2.3)",
                "host": "", "available": true, "detail": "simtrace2-remsim starts after Run" }],
  "problems": {
    "pcsc": "pyscard is not installed in the Python running Plexus (/home/me/plexus/.venv/bin/python), so no PC/SC reader can be opened. …" } }
```

An editor asks again with a `devices` frame of its own, so a reader plugged in
after the page connected shows up without a reload. A runtime that predates it
answers `error` ("Unknown frame"), which changes nothing else.
The editor greys those nodes in the palette and refuses to arm a graph that
uses one, so a rig that cannot run says so before it opens a reader instead
of after.

It travels on the socket rather than over `/api/health`, which carries the same
answer from the same function. Whether that fetch is even readable depends on
where the editor came from: served by the runtime it is same-origin and works,
which is how the editor finds the runtime in the first place; served by a dev
server on another port it is cross-origin with no CORS behind it, and the
browser will not let the page read the reply. Measured, by a palette that
marked nothing.

The socket has neither problem — it is already open and has already passed the
Origin check — so the answer rides on it and no panel depends on the fetch.

### A finished terminal is not a stopped runtime

A scripted terminal — a probe, a replay — runs out of script. That is not
`stopped`: the graph is still armed, the card is still bound, and `tx` still
reaches it. The runtime stays `running`, moves its nodes from `live` back to
`ready`, and says so in a `log`. Only `stop` releases anything.

An editor should therefore keep offering Stop after the traffic goes quiet, and
must not treat silence as the end of a run. A relay terminal never finishes at
all; it loops until stopped.

### Armed, then the daemon

A terminal may speak right after the `Armed …` line, in `log` frames whose
`source` is its node id. The SimTrace2 does: the daemon that feeds it,
`simtrace2-remsim`, opens its card once at startup on vpcd's
`Virtual PCD 00 00`, and that card is the armed graph. Started earlier it
finds no card and exits. So the line after `Armed` is the command to start it
now, with `-n` set to the Virtual PCD's PC/SC reader number, and a `warn`
comes before it when a daemon was already running (it opened some other card,
or none) or the reader number could not be found. When the real terminal's
first command arrives, one more `info` line says the relay is live. State
stays `running` throughout; nothing waits for the daemon.

```json
{ "type": "log", "line": { "t": 0.4, "level": "info", "source": "st",
  "text": "Start simtrace2-remsim now, in a second terminal (…): sudo simtrace2-remsim --usb-vendor 1d50 --usb-product 60e3 --usb-path 1-2.3 --usb-config 1 -n 0" } }
```

### Batching is expected, not optional

A relay at speed produces more telemetry than a browser wants as individual
messages. A runtime should coalesce a tick's worth of `node`, `edge` and `apdu`
frames into one `batch` — roughly 20–50 ms of traffic per frame is a sensible
target. The editor unwraps `batch` recursively and applies the contents in
order, so batching is invisible to everything above the transport.

### The trace on the wire is not the trace on disk

The editor keeps a ring buffer of the last few hundred APDUs, because that is
what a person can scroll. The full trace belongs in a file the runtime writes.
A runtime is free to drop `apdu` and `log` frames under load — it must not drop
`node`, `edge` or `state`. Those two rules together are only satisfiable if
`node` and `edge` frames are treated as *state* rather than as events: a relay
emits one of each per node and per edge on every exchange, so a runtime that
queues them and refuses to drop them grows without limit. Keep the newest value
per id and send that, which is all the editor does with them anyway. The Python
runtime coalesces exactly that way, reports how many `apdu` and `log` frames it
thinned in a `log` line, and does all of it in constant time — the queue is
written from the thread driving the card, and work proportional to how far
behind the listener is turns a slow listener into a dropped tap.

## Console commands

`command` carries a raw line; the runtime parses it. The editor knows nothing
about the verbs, so a runtime can add its own. These are the ones the simulator
implements and a real runtime should too:

```
help                  what this runtime understands
status                state, graph, chain, expected budget
nodes                 each node, its state and its counters
edges                 each edge and what has crossed it
devices               hardware the runtime can see
tx <hex>              push one command APDU through the chain
pause / resume        hold traffic without unbinding
```

The Python runtime adds one more, because it has a node the simulator does not:

```
release [node] [hex]  let a held breakpoint go, with different bytes or not
```

A breakpoint announces itself the moment it catches something — a `log` frame
naming the node and the bytes, and a `node` frame whose detail says what is
held — because those are exactly what you need in order to release it. `tx`
refuses while anything is held rather than waiting its turn: the runtime reads
frames on one thread, so a parked `tx` would block the `release` that would
free it.

`clear` is handled in the editor and never reaches the runtime.

## What this protocol deliberately does not do

- **No authentication.** Same position atrium takes: bind loopback, or reach it
  through an SSH tunnel. A runtime that binds anything else should refuse to
  start without a token, the way `atrium.py serve` does.
- **No graph push from the runtime.** The editor owns the document. A runtime
  that wants to suggest a change says so in a `log` line.
- **No binary frames.** APDUs are small and hex is debuggable. If this ever
  becomes the bottleneck it will be obvious in a profile, and the fix is
  batching before it is binary.
