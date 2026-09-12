# The runtime protocol

One WebSocket, JSON frames, no framing of our own. The editor
(`ui/src/runtime/ws.ts`) and the runtime are the two ends of this document, and
neither may grow a field the other has not agreed to.

The simulator in `ui/src/runtime/mock.ts` emits the same events in the same
order without a socket. That is the point of it: a panel built against the
simulator works against a card.

## Connecting

The editor dials `ws://<host>:<port>/ws` and sends `hello` first.

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
| `result` | `{id, lines, level?}` — the answer to one `command` |
| `error` | `{message}` |
| `batch` | `{frames}` — any of the above, applied in order |

`t` is milliseconds since the run started, not a wall clock. `hex` is uppercase
with no separators.

### Batching is expected, not optional

A relay at speed produces more telemetry than a browser wants as individual
messages. A runtime should coalesce a tick's worth of `node`, `edge` and `apdu`
frames into one `batch` — roughly 20–50 ms of traffic per frame is a sensible
target. The editor unwraps `batch` recursively and applies the contents in
order, so batching is invisible to everything above the transport.

### The trace on the wire is not the trace on disk

The editor keeps a ring buffer of the last few hundred APDUs, because that is
what a person can scroll. The full trace belongs in a file the runtime writes.
A runtime is free to drop `apdu` frames under load — it must not drop `node`,
`edge` or `state`.

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
