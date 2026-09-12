# The node reference

Fifteen node types, in three groups. Every one maps onto something that already
exists in atrium — a `CardTransport`, a `SmartcardOS`, or the
`on_command`/`on_response` pair its mutation engine exposes. Plexus did not
invent these shapes; it found them.

**Ready** means this build can bind it. **Needs hardware** means it resolves in
a graph and refuses at bind time, naming what it wants. `plexus nodes` prints
the same list from the code, so it cannot drift from this page's claims —
trust the command over the table if they ever disagree.

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

Presents a virtual card to a real terminal over the contact interface.

The daemon needs raw USB access and is started by hand. **Plexus never launches
it** — having a request escalate privileges means either a password prompt with
nowhere to go or a passwordless sudo rule that turns the API into a root shell.
This node checks it is up and binds to it.

### Virtual reader · needs virtualsmartcard with vpcd loaded into pcscd

Presents a card to anything on this machine that speaks PC/SC.

### EMV agent · needs a configured model back end

A synthetic terminal: the model writes its own commands and reads the answers.
It drives the card on its own — point it at a card you own.

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

A playbook that does not parse **stops the arm**. Atrium's loader logs a bad
document and carries on with defaults, which is the worst available outcome:
the node binds, the run starts, and you believe mutations are armed while
nothing is. Plexus parses it first.

### Trace tap

Decodes TLV and records the exchange. Changes nothing.

| Field | |
|---|---|
| Detail | `raw` · `tlv` · `annotated` |
| Write to | a path **inside the workspace** — see [What it refuses](security.md) |

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

### Timing

Asks the terminal for more time with S(WTX) when the card is slow. Buys time on
contact; a contactless kernel may refuse it and drop the transaction.

Only the node that owns the RF or contact interface can actually send an S(WTX)
block, so this one decides *when* one is warranted and records it.

### Breakpoint

Holds each matching command until you release it from the console.

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

A contact card in a reader attached to the runtime's host.

### ACR122U field · needs pyscard, an ACR122U, and atrium's contactless transport

A contactless card sitting in an ACR122U's RF field.

**One ACR122U cannot be a target and an initiator at once.** Emulating and
reading needs two — and Plexus refuses the graph that tries, because the
failure that comes out of trying is a timeout, not a message.

### NFCGate session · needs atrium's NFCGate transport and a reachable server

A card held in an Android phone's field, reached through an NFCGate server.

| Field | |
|---|---|
| Server host, port | the NFCGate server, default `5566` |
| Session | which session to join |
| CA certificate | for a TLS server; empty means plaintext on the local network |

Latency here is the phone, the Wi-Fi and the server — about 35 ms each way,
the biggest single cost in most graphs, and enough on its own to put a
contactless chain into warning.

### Remote card · needs atrium's remote transport and a running card proxy

A reader on another machine, reached over atrium's card proxy.

| Field | |
|---|---|
| Proxy host, port | default `7654` |
| Pairing secret | without it the link is unauthenticated |

---

## Host layer — not built

ISO 8583 exists as a second channel in the model with no nodes on it yet. The
interesting one is a **DE55 bridge**: field 55 of an authorisation carries ICC
data as BER-TLV — the same encoding a card emits — so it is where the two
halves atrium describes actually meet. Today that meeting is prose in atrium's
README; on a canvas it would be a node you could draw.

---

## Adding a node type

The catalogue is **data, never code**. One entry in
`ui/src/catalogue/index.ts` gives a node type its palette entry, its icon, its
ports, a generated inspector form, its validation and its latency cost. There
is no per-node React component.

```bash
cd ui
npm run export:catalogue     # regenerates plexus/schema/catalogue.json
```

Both sides read that JSON, so the description exists once. Then write the
adapter in `plexus/nodes/`, and add its Spanish strings to
`ui/src/i18n/es.ts` — an untranslated node reads in English inside an
otherwise Spanish interface rather than showing a blank.
