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
plexus check relay.plexus.yaml
plexus show  relay.plexus.yaml
plexus nodes
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

- **The Python runtime.** The graph model, the checks, the catalogue loader and
  the CLI are written; the engine that binds hardware and moves APDUs is not.
  It needs the rig, not a laptop.
- **The atrium adapters.** `plexus/adapters/` does not exist yet. The seams it
  will attach to are named above.
- **Host-layer nodes.** ISO 8583 is a second channel in the model (`iso8583`)
  with no nodes on it yet. The interesting one is a DE55 bridge, where the two
  halves atrium describes actually meet.
- **Nothing Python here has been executed.** It was written on a Windows
  machine with no Python installed. The TypeScript is typechecked, linted,
  built and driven in a browser; the Python is not.
