# Plexus

A node canvas for [atrium](https://github.com/salmg/atrium). Wire readers,
phones, captures and mutations into a relay you can see, run, and reach into
while it is running.

> **Authorized use only.** For security research on cards, terminals and hosts
> you own or have written permission to test. Same terms as atrium.

Atrium can already do all of this. What it cannot do is *show* you the rig:
today a topology lives in a CLI flag soup (`--nfcgate --nfcgate-host … --reader
… --from-file …`) that is hard to read, harder to share, and impossible to see
the shape of. Plexus makes the rig the document.

---

## An edge is a conversation

This is the one idea everything else follows from, and it is where a
node-graph tool for this domain usually goes wrong.

In n8n — or any dataflow editor — an edge is a one-way push and an execution
runs to completion. Neither is true here. A command APDU travels one way, a
response comes back along the same wire, and the graph stays up for thousands
of round trips until you stop it. So ports do not hold directions, they hold
roles:

- **requester** — I ask. Something on the other end has to answer.
- **responder** — I answer. I present myself as the thing being asked.

On the `apdu` channel those read as the **card port** (amber, on the right: it
wants a card to talk to) and the **terminal port** (teal, on the left: it
behaves as a card would). An edge is legal when two ports share a channel and
hold opposite roles, and it is always drawn requester → responder — the
direction a command travels.

```
  ACR122U target ──► Trace tap ──► Mutation ──► NFCGate session
   (asks)                                        (answers)
      card ●────────● terminal    card ●────────● terminal
```

The payoff is that a whole class of atrium's runtime errors become edges you
cannot draw. `transport/source.py` spends a paragraph explaining that the
reader presenting the emulated card cannot also hold the card being relayed,
because the failure that comes out of trying is a timeout rather than a
message. That is a rule about a graph. Here it is enforced when you reach for
the second node, with the reason on screen.

## What it checks before it will arm

- **Every requester is answered.** A node with nothing to ask cannot run.
- **No device is claimed twice.** A node does not contain a reader, it *binds*
  one, and the second claim is refused by name.
- **Cross-machine edges are authenticated.** An edge between two hosts that
  carries card traffic with no pairing secret is flagged, because the security
  model becomes a visible property of the picture rather than a README section.
- **The chain fits in a tap.** Each node declares what it costs; a contactless
  chain that sums past what a kernel will tolerate says so before you plug
  anything in. These thresholds are rules of thumb, not figures from a
  specification — see `TAP_BUDGET_WARN_MS`.

## The console

Dark mode is not a colour scheme, it is the working mode: the canvas dims and a
real terminal sits under it. During a relay the useful thing is rarely reading
the trace — it is reaching into the running process.

```
plexus> tx 80CA9F1700
plexus> nodes
acr122u-target-1         1284 apdu     6.2 ms
mutation-1               1284 apdu     1.1 ms
nfcgate-1                1284 apdu    34.8 ms
plexus> pause
```

The same console works against the simulator and against a card, because both
answer the same protocol.

---

## Running it

```bash
git clone --recurse-submodules https://github.com/salmg/plexus
cd plexus/ui
npm install
npm run dev
```

Open http://localhost:5173. It starts on a simulated runtime — no reader, no
atrium, no Python — with an example rig loaded so there is something to read.
The top bar always says which runtime is answering, so a simulated result is
never mistaken for a card.

To point it at a real runtime:

```bash
npm run dev        # then open http://localhost:5173/?runtime=ws://rig.local:8900/ws
```

The graph model and the checks also run headless, with only PyYAML installed:

```bash
pip install -e .
plexus check examples/contactless-relay.plexus.yaml
plexus show  examples/contactless-relay.plexus.yaml
plexus nodes
```

`show` prints the chain and what it is expected to cost:

```
contactless relay to a phone

  ├─ ACR122U target  (acr122u.target, 6 ms)  @workbench
  ├─ Trace tap  (trace, 0 ms)  @workbench
  ├─ CVM bypass  (mutation, 1 ms)  @workbench
  └─ NFCGate session  (nfcgate, 35 ms)  @phone

  42 ms each way, 84 ms out and back.
```

And `run` arms the graph for real. One rig needs nothing at all — a fingerprint
probe asking, a recorded capture answering, a mutation node between them:

```bash
plexus run examples/probe-a-capture.plexus.yaml
```

```
info  Armed 3 nodes: probe-1 -> mutation-1 -> capture-1
    31.2 -> 00A404000E325041592E5359532E444446303100
    32.6 <- 6F20840E325041592E5359532E4444463031A50E…9000  1.4 ms
    32.8 -> 00A4040007A000000004101000
    34.1 <- 6F178407A0000000041010A50C500A4D61737465…9000  1.3 ms
    34.2 -> 80A8000002830000
    34.2 <- 770A82021C009404080101009000  0.0 ms
    34.3 -> 00B2010C00
    34.3 <- 70145A0854133300123456785F24032512315F3401019000  0.0 ms
    34.4 <- 70145A0854133300123456785F24032512315F3401079000  rewritten by mutation-1

4 exchanges, 0 errors.
```

The last two lines are the point: what the card said, and what the terminal was
told. `plexus nodes` marks which node types this build can actually bind — the
rest name the hardware or daemon they need and refuse at bind time.

Tests:

```bash
pip install -e ".[dev]"
pytest
```

## Layout

```
schema/catalogue.json   generated — the one description of every node type
ui/                     the editor (React, @xyflow/react, xterm.js)
  src/graph/            model, validation, YAML
  src/catalogue/        node types — data, never code
  src/runtime/          mock runtime, WebSocket runtime
  src/canvas/           React Flow canvas and the node card
  src/panels/           palette, inspector, console
plexus/                 the runtime (Python)
  graph.py              the model again, and the same checks
  catalogue.py          reads schema/catalogue.json
  runtime/              engine, the chain, the frames it emits
  nodes/                card / terminal / transform implementations
  adapters/             the only place that imports atrium
  cli.py                check, show, run, nodes
examples/               rigs you can open and one you can run
tests/                  the model, and the engine end to end
vendor/atrium/          submodule, never edited
PROTOCOL.md             the WebSocket contract between the two
```

### One catalogue, two readers

A node type's ports, config fields and latency cost are described once, in
`ui/src/catalogue/index.ts`, and exported to `schema/catalogue.json` with
`npm run export:catalogue`. The Python side loads that file. Writing the table
twice would guarantee drift, and the first symptom would be a graph that draws
in the editor and refuses to run — or worse, runs differently.

### Atrium is a submodule, not a fork

`vendor/atrium` is pinned at a commit and never edited. Upgrades are an
explicit bump you can review:

```bash
git submodule update --remote vendor/atrium
```

Every node in the catalogue maps onto something that already exists there: a
`CardTransport` implementation, a `SmartcardOS`, or the `on_command` /
`on_response` pair the mutation engine exposes. A mutation node's config *is*
an atrium playbook — the files in `vendor/atrium/playbooks/` paste in unchanged.

## What is not built yet

Being straight about the line:

- **The hardware nodes.** The engine binds, drives, rewrites, reports and
  releases — but the only card it can bind today is a recorded capture and the
  only terminal is the fingerprint probe. PC/SC, the ACR122U in either mode,
  NFCGate, the card proxy, SimTrace2 and vpcd all resolve, and all refuse at
  bind time naming what they need. They are adapter work against seams that
  already exist in atrium, and they need the rig to be worth writing.
- **The WebSocket server.** `PROTOCOL.md` is the contract and
  `ui/src/runtime/ws.ts` is the editor's half; the runtime's half is not
  written, so the editor still talks to its simulator. The engine already emits
  exactly the frames the protocol describes — `plexus/runtime/events.py` is
  those frames — so the server is a transport and little else.
- **Host-layer nodes.** ISO 8583 is a second channel in the model (`iso8583`)
  with no nodes on it yet. The interesting one is a DE55 bridge, where the two
  halves atrium describes actually meet.

### Why a laptop can run a whole graph

Atrium splits cleanly in half, and only one half needs hardware.
`mutation_engine`, `transport.recorded`, `emv_logger` and `card_fingerprint`
are pure standard library — they import with no reader attached and no pyscard
installed. So a graph made of a capture and some transforms is a complete,
honest relay that runs anywhere, and that is what the test suite drives: bind,
drive, rewrite, report, release, through atrium's real mutation engine.

Everything Plexus asks of atrium it found rather than invented.
`CardTransport` is connect / get_atr / transmit / disconnect, which is exactly
a card-side node. `MutationEngine` is `on_command(cmd) -> cmd` and
`on_response(cmd, resp) -> resp`, which is exactly a transform. All of it goes
through `plexus/adapters/`, and nothing else in the codebase imports atrium.

### The two implementations have to agree

The editor and the runtime apply the same rules to the same graph, in different
languages. `tests/test_graph.py` pins the places where that agreement is
observable — the example rig's 84 ms round trip, its two warnings, the chain
order — so a change to one side that quietly disagrees with the other fails a
test rather than surfacing as a graph that draws fine and runs differently.

One case is worth keeping: `test_the_thresholds_are_reachable_by_a_graph_someone_could_draw`
is a regression on a check that did nothing. The contactless timing thresholds
were first compared against the one-way sum at 120/300 ms, while the dearest
chain the catalogue allows is 43 ms one way — so the warning could never fire
and the error needed 79 stacked mutation nodes. They now measure the round trip
against 60/150 ms, and the test fails if the catalogue ever drifts back out of
their reach.
