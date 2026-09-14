# The node reference

Fifteen node types, in three groups. Every one maps onto something that already
exists in atrium — a `CardTransport`, a `SmartcardOS`, or the
`on_command`/`on_response` pair its mutation engine exposes. Plexus did not
invent these shapes; it found them.

**Ready** means this build can bind it. **Needs hardware** means it resolves in
a graph and refuses at bind time, naming what it wants. `plexus nodes` prints
the same list from the code, so it cannot drift from this page's claims —
trust the command over the table if they ever disagree.

The editor asks the same question of whichever runtime it is connected to, and
answers it in two places: a node it cannot bind is dimmed in the palette with
what it needs, and a graph that uses one is an error in the checks rather than
a failure after Run. Both say the sentence you see here, because both come from
the table the refusal comes from.

None of that applies to the simulator, which invents traffic for anything — so
on a laptop with nothing plugged in, every node is drawable and runnable, and
that is the point of it.

---

## Terminal side — nodes that only ask

These go on the left. Each has one **card port**, and it needs something that
answers.

### Fingerprint probe · ready

Runs a discovery sequence and records what the card answered: SELECT PPSE, then
each application it advertises, GET PROCESSING OPTIONS, then the records its
AFL points at. Not a payment — the part of a transaction that tells you what a
card *is*, and the part a recorded capture can answer honestly.

| Field | |
|---|---|
| Depth | `quick` (AID scan and ATR/ATS) · `standard` (plus records and DOLs) · `deep` (plus an SFI sweep) |

The GPO it builds sends zeroes of exactly the length the card's PDOL asked for.
A probe is not trying to be approved; it is trying to get a well-formed answer.

### ACR122U target · needs pyscard, an ACR122U, and atrium's PN532 emulator

| Field | |
|---|---|
| Reader | which ACR122U, when more than one is attached |
| Offer long responses as 61 XX | split a response the frame cannot carry, and let the terminal ask for the rest |

Emulates a contactless card to a terminal in whose field it sits.

Worth knowing, all three from atrium's own measurements:

- The PN532 forces the first UID byte to `0x08`, the random-UID marker. **A
  terminal that pins a specific UID will not see the card's own.**
- One frame per exchange: 253 bytes for the relayed response. Longer needs
  ISO 14443-4 chaining, which is not implemented — it reports the limit rather
  than truncating.
- Every APDU costs a USB round trip. Contactless kernels enforce a timing
  budget and may abandon the transaction. This is a property of relaying over
  USB, not a bug.

### SimTrace2 · needs a SimTrace2 and `simtrace2-remsim` in a root shell

| Field | |
|---|---|
| Device | which SimTrace2, when more than one is attached |
| Refuse to start if the daemon is not already up | the only safe default: the alternative is Plexus launching a root process |

Presents a virtual card to a real terminal over the contact interface.

The daemon needs raw USB access and is started by hand. **Plexus never launches
it** — having a request escalate privileges means either a password prompt with
nowhere to go or a passwordless sudo rule that turns the API into a root shell.
This node checks it is up and binds to it.

### Virtual reader · needs virtualsmartcard with vpcd loaded into pcscd

Presents a card to anything on this machine that speaks PC/SC.

| Field | |
|---|---|
| vpcd host | where vpcd is listening — `127.0.0.1` unless you moved it |
| vpcd port | its port, `35963` by default |

### EMV agent · needs a configured model back end

A synthetic terminal: the model writes its own commands and reads the answers.
It drives the card on its own — point it at a card you own.

| Field | |
|---|---|
| Task | what to ask it to find out, in a sentence |
| Model | which back end answers |
| Brute-force SFI range | sweep every short file identifier rather than only what the AFL points at |

See [Trying the agent](hardware.md#trying-the-agent) for the key it needs and a
rig to run it against with no reader.

---

## Transforms — nodes that sit in the conversation

These go in the middle, any number of them. Each has a **terminal port** on the
left and a **card port** on the right. All are ready.

### Mutation

Rewrites PDOL, AFL, DOL and response tags as the conversation passes. This is
**atrium's mutation engine**, not a reimplementation: the playbook field takes
atrium's own format, and files in `vendor/atrium/playbooks/` paste in unchanged.

The playbook is config on the node rather than a path, because the graph is the
document — a rig you hand someone should not depend on a YAML file beside it
that they do not have.

| Field | |
|---|---|
| Playbook | atrium's rules, in its own YAML. Existing playbooks paste in unchanged |
| Log every rewrite | writes each one to the playbook's `log_path`, inside the workspace |

A playbook that does not parse **stops the arm**. Atrium's loader logs a bad
document and carries on with defaults, which is the worst available outcome:
the node binds, the run starts, and you believe mutations are armed while
nothing is. Plexus parses it first.

### Trace tap

Records the exchange and changes nothing.

| Field | |
|---|---|
| Detail | `raw` · `tlv` · `annotated` |
| Write to | a path **inside the workspace** — see [What it refuses](security.md) |

One JSON object per exchange, flushed as it is written — a trace still buffered
when the process is killed is a trace of the part you did not need.

What `Detail` puts in each line:

| | |
|---|---|
| `raw` | the command and the response, as hex |
| `tlv` | plus every TLV in the response, flattened, descending into templates |
| `annotated` | plus the name of each tag this build knows, and what the status word means |

```json
{"cmd": "00B2010C00",
 "resp": "70145A08...9000",
 "tlv": [{"tag": "5A", "value": "5413330012345678", "name": "Application PAN"}],
 "status": {"sw": "9000", "meaning": "OK"}}
```

The tag table is deliberately not exhaustive — it is the set a relay actually
turns up, because a half-remembered name is worse than none. A tag that is not
in it keeps its hex.

### Injection

Fires an extra command at the card around a trigger instruction.

| Field | |
|---|---|
| Trigger INS | one byte, e.g. `AE` for GENERATE AC |
| When | before the command · after the response |
| APDU to inject | hex, e.g. `80CA9F3600` |
| Fire every time | otherwise once per session |

The injected command reaches the card **out of band** — past everything
downstream of this node. That is the dashed wire on the canvas, and it is drawn
rather than hidden because it spends the round trip twice: free on contact, and
enough to time a contactless tap out.

You have to draw it. Without the dashed wire the node passes traffic through
and injects nothing, and the checks say so before you press Run. The card end
of that wire is the one place a port takes two edges — the chain arrives on it
and so does the dashed line — because "past everything downstream" means
exactly that.

`examples/inject-out-of-band.plexus.yaml` is the rig, and it needs no hardware:

```bash
plexus run examples/inject-out-of-band.plexus.yaml
```

The injected exchange is marked `injected by` in the trace and on its own edge,
so you can tell it from traffic the terminal asked for.

### Timing

Asks the terminal for more time with S(WTX) when the card is slow. Buys time on
contact; a contactless kernel may refuse it and drop the transaction.

| Field | |
|---|---|
| WTX multiplier | how much more time to ask for, 1 to 59 |
| Request above (ms) | how slow the card has to be before it asks |

Only the node that owns the RF or contact interface can actually send an S(WTX)
block, so this one decides *when* one is warranted and records it.

### Breakpoint

Holds each matching command until you release it from the console.

| Field | |
|---|---|
| Break on INS | one byte, e.g. `AE` for GENERATE AC. Empty catches every command |
| Allow editing the held bytes | whether `release <hex>` may substitute something else |

Stops the clock dead — any real terminal will time out while you think, which
is why it is a node you add deliberately rather than a mode.

When it catches something it says so in the console, naming the node and the
bytes it is holding. Let it go with:

```
release                   the held command, unchanged
release 80A8000002831234  the same exchange with different bytes
release breakpoint-1      when more than one is holding
```

Releasing does not disarm it, so the next matching command is caught too —
which is how you step through a transaction. `tx` refuses while anything is
held, and stopping the runtime always disarms rather than merely releasing, so
you cannot wedge a run with one.

---

## Card side — nodes that only answer

These go on the right. Each has one **terminal port**.

### Recorded capture · ready

Replays a saved session. No hardware, no card, repeatable — and the reason a
whole graph can be tested on a laptop.

| Field | |
|---|---|
| Capture file | a path inside the workspace; JSONL of `{"cmd": …, "resp": …}` |
| Strict replay | fail on a command the capture does not contain, rather than improvising |

**What a replayed card cannot do** is answer a challenge it has never seen. A
terminal picks its own unpredictable number, and the cryptogram in the capture
was computed over a different one. GENERATE AC replies are stale by
construction. Everything before that replays honestly, and atrium logs every
fallback match — so a response served by a looser rule than exact match is
visible as the weaker claim it is.

### PC/SC reader · needs pyscard and a reader on this machine

| Field | |
|---|---|
| Reader | which reader, when more than one is attached |

A contact card in a reader attached to the runtime's host.

### ACR122U field · needs pyscard, an ACR122U, and atrium's contactless transport

| Field | |
|---|---|
| Reader | which ACR122U, when more than one is attached |

A contactless card sitting in an ACR122U's RF field.

**One ACR122U cannot be a target and an initiator at once.** Emulating and
reading needs two — and Plexus refuses the graph that tries, because the
failure that comes out of trying is a timeout, not a message.

### NFCGate session · needs atrium's NFCGate transport and a reachable server

A card held in an Android phone's field, reached through an NFCGate server.

| Field | |
|---|---|
| Server host | where the NFCGate server is |
| Server port | its port, `5566` by default |
| Session | which session to join |
| CA certificate | for a TLS server; empty means plaintext on the local network |

Latency here is the phone, the Wi-Fi and the server — about 35 ms each way,
the biggest single cost in most graphs, and enough on its own to put a
contactless chain into warning.

### Remote card · needs atrium's remote transport and a running card proxy

A reader on another machine, reached over atrium's card proxy.

| Field | |
|---|---|
| Proxy host | where the card proxy is listening |
| Proxy port | its port, `7654` by default |
| Pairing secret | without it the link is unauthenticated |

---

## Host layer — nodes on the other channel

The conversation above the card. Where the card side is terminal ⇄ card
speaking APDUs, this is **acquirer ⇄ gateway ⇄ issuer** speaking ISO 8583, and
atrium's `host/` package has the whole of it — framing, dialects, pack and
unpack, a passive relay, a mutating proxy, corpus replay and cryptogram
verification. None of it imports pyscard, so it runs with no reader attached.

These sit on the **`iso8583` channel**, so they wire to each other and never to
an apdu node — a wire between the layers is refused as a channel mismatch. The
two meet at **DE55** instead: field 55 of an authorisation carries ICC data as
BER-TLV, the same encoding a card emits, which is why both halves of the
toolkit parse and mutate it with one TLV core.

Their ports are named the way the apdu ones are — a requester for what it wants
on the other end, a responder for whoever asks it. So a node that needs
somewhere to send an authorisation has a **host port**, and one that answers an
authorisation has an **acquirer port**.

> **None of them binds yet, and not for want of an adapter.** The engine drives
> one shape of conversation: the card's `transmit(command) → response`, where a
> terminal owns the loop and every step is a round trip. A host link is `send`
> and `receive` over a framed socket — messages are not strictly paired, either
> end may speak first, and a reply can arrive for a request two ahead. That is a
> second driving model beside the first, not another transport. They draw,
> check, cost out, save and share meanwhile; `plexus run` refuses them by name.

### Corpus replay · needs an engine that drives the host layer

Plays the acquirer from a captured conversation, with nothing in front of it.
Asks; has a host port.

| Field | |
|---|---|
| Corpus file | the captured conversation to replay |
| Mode | `verbatim`, or `freshen` |

**Verbatim** resends exactly what was captured, STAN and RRN included — and a
host that approves a byte-identical replay has no duplicate detection at all,
which is the question it asks. **Freshen** rewrites only the volatile routing
fields and leaves DE55 and its cryptogram untouched, which asks the sharper one:
is the ARQC actually bound to those fields?

### Payment host · needs an engine that drives the host layer, and a host to reach

An acquirer, gateway or issuer reached over TCP with MLI framing. Answers; has
an acquirer port.

| Field | |
|---|---|
| Host | where the switch is |
| Port | its port, `8583` by default |
| Dialect | `ISO 8583:1987`, `BASE24` or `Postilion` |
| TLS | wrap the link |

A dialect is a guess about somebody else's system, which is why it is a file in
atrium rather than code. Getting the framing wrong degrades to "could not read
that" rather than to a corrupted link.

### Host mutation · needs an engine that drives the host layer

Rewrites data elements and DE55 tags as an authorisation passes. Sits in the
middle: an acquirer port on the left, a host port on the right.

| Field | |
|---|---|
| Playbook | atrium's host playbook format; files from `host/playbooks/` paste in unchanged |
| Log every rewrite | one line per mutation that fired |

The default playbook is atrium's `amount-mismatch`, which is the clearest
demonstration of why this layer exists: the amount appears twice in an
authorisation — as DE4, which the switch routes and authorises on, and as tag
9F02 inside DE55, which the cryptogram was computed over. Raising one and not
the other asks which copy the far side actually believes.

A rewritten message is re-encoded from its decoded form, so the byte-exactness
a passive relay guarantees is given up for the messages a rule actually changed.
One that did not decode cleanly is never mutated, and a failed re-encode
forwards the original.

---

## Adding a node type

The catalogue is **data, never code**. One entry in
`ui/src/catalogue/index.ts` gives a node type its palette entry, its icon, its
ports, a generated inspector form, its validation and its latency cost. There
is no per-node React component.

```bash
cd ui
npm run export:catalogue     # regenerates plexus/schema/catalogue.json
                             # (needs Node 22.18+: it imports the .ts)
```

Both sides read that JSON, so the description exists once. Then write the
adapter in `plexus/nodes/`, and add its Spanish strings to
`ui/src/i18n/es.ts` — an untranslated node reads in English inside an
otherwise Spanish interface rather than showing a blank.
