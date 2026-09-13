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
| `devices` | `{devices}` — the full list, every time; not a delta |
| `implementations` | `{needs}` — node types this runtime *cannot* bind, keyed to what each one wants |
| `result` | `{id, lines, level?}` — the answer to one `command` |
| `error` | `{message}` |
| `batch` | `{frames}` — any of the above, applied in order |

`t` is milliseconds since the run started, not a wall clock. `hex` is uppercase
with no separators.

### What a runtime cannot bind

`implementations` is sent once, in the reply to `hello`. A key present in
`needs` is a node type this runtime has no implementation for, and the value
is the sentence it would refuse with — the same sentence, from the same
table, that `arm` raises:

```json
{ "type": "implementations",
  "needs": { "pcsc": "pyscard and a reader attached to this machine",
             "nfcgate": "atrium's NFCGate transport and a reachable server" } }
```

An empty object means everything binds, which is what the simulator sends.
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
