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

## A SimTrace2 relay

A real payment terminal on one side, a real card on the other, Plexus in the
middle:

```
terminal -> SimTrace2 board -> simtrace2-remsim -> vpcd "Virtual PCD 00 00"
         -> atrium's VirtualCard on port 35963 -> the graph -> your card
```

### Run first, then the daemon

`simtrace2-remsim` is a PC/SC client of the Virtual PCD reader, and the card it
opens there is not a physical card: **it is the graph**, presented through
atrium's `VirtualCard`. The daemon opens that card once, at startup, before its
relay loop. So the order is forced:

1. **Press Run** (or `plexus run`). The SimTrace2 node connects to vpcd, and
   the Virtual PCD now holds a card.
2. **Then start the daemon.** Right after `Armed`, the console prints the exact
   command for your board. `plexus remsim` prints the same line in a shell:

   ```
   sudo simtrace2-remsim --usb-vendor 1d50 --usb-product 60e3 --usb-path 2-2.1 --usb-config 1 -n 0
   ```

3. **Present the card at the terminal.** The first command the terminal sends
   logs `simtrace2-remsim is relaying`.

Started before Run, the daemon finds an empty reader, prints
`unable to open card on reader 'Virtual PCD 00 00'` and exits. Plexus used to
refuse Run until a daemon was up, which made the relay impossible: the node's
old "refuse to start if the daemon is not already up" switch is gone, and a
graph that still carries it (`assertOnly`) loads and runs. A daemon that is
already running when you press Run gets a warning, because it opened some other
card, or none.

**Which reader the daemon opens.** `simtrace2-remsim` picks its reader by
number alone, 0 unless `-n` says otherwise. The Virtual PCD is usually 0
(atrium's README says the same), but not always. When it is not, the daemon opens
the real card directly: the terminal gets a working relay and Plexus sees
nothing. So Plexus looks the number up by name and puts `-n` in the command.
When it cannot (no pyscard, pcscd not answering), the console warns and leaves
`-n` out; compare with `pcsc_scan -r`.

**Never pass `-k`.** It looks like the fix for "no smart card inserted", and it
corrupts the run. The card open happens before the retry loop, and a failed open
jumps to the bottom of that loop, so with `-k` the daemon goes round again with
no card channel and an empty ATR. The ATR push is skipped, so the terminal works
against whatever ATR the board still holds from an earlier run (a trace that
looks almost right and is wrong), and the first complete command dereferences
the missing channel and crashes. If the daemon says no card is inserted, press
Run first.

### The launchers

`tools/simtrace2/` starts the daemon at the right moment, with the command from
`plexus remsim`. With the editor:

```bash
plexus up                                  # terminal 1
bash tools/simtrace2/relay-daemon.sh       # terminal 2, before pressing Run
```

`relay-daemon.sh` takes the sudo password first, waits for a connection on
vpcd's port 35963 (arming opens it), checks a second later that the Run held,
and starts the daemon. It watches the socket rather than opening the reader,
which would get in the relay's way.

Headless, for scripted captures:

```bash
bash tools/simtrace2/relay-up.sh                                   # examples/simtrace2-relay.plexus.yaml
bash tools/simtrace2/relay-up.sh my-rig.plexus.yaml --synthetic-atr
```

`relay-up.sh` arms the rig with `plexus run --timeout 0`, waits for the `Armed`
line, starts the daemon and follows the output until Ctrl-C, which releases the
card and prints the summary. `plexus run` builds its own engine with a
stdout-only sink, so a browser shows nothing of it. **Use one way or the other,
never both**: only one program can hold the Virtual PCD.

Both pass extra arguments to the daemon, refuse `-k`, and carry
`LD_LIBRARY_PATH` through sudo. `DEVICE` picks the board (`simtrace2:<usb path>`)
when there are several, `REMSIM` runs another binary, `WAIT` and `TIMEOUT` bound
the waits.

### Setup, once

```bash
sudo apt install -y pcscd pcsc-tools libpcsclite-dev swig python3-dev iproute2 \
    autoconf automake libtool help2man build-essential
git clone https://github.com/frankmorgner/vsmartcard.git
cd vsmartcard/virtualsmartcard
autoreconf --verbose --install && ./configure --sysconfdir=/etc && make && sudo make install
sudo systemctl restart pcscd
pcsc_scan -r
```

`pcsc_scan -r` must list `Virtual PCD 00 00` beside your reader; nothing
downstream works until it does. The `--sysconfdir=/etc` flag puts vpcd's reader
definition where pcscd reads it, and the distribution package is often too old.
Install Plexus with both extras, `pip install -e ".[runtime,hardware]"`, into
the Python that runs it. From a GitHub ZIP, `vendor/atrium` is empty: export
`PLEXUS_ATRIUM` to an atrium checkout in the shell that runs `plexus up`.

`simtrace2-remsim` itself comes from the SIMtrace2 T=1 tree, built against a
libosmocore under `~/.local/osmo`. atrium finds the binary in the usual build
directories (`~/simtrace2/host/src/` among them) or on the PATH, and `REMSIM`
overrides it. Such a build needs `LD_LIBRARY_PATH=$HOME/.local/osmo/lib` to
load, and sudo drops that variable: export it before running a launcher, which
carries it through, or put `env LD_LIBRARY_PATH=…` after `sudo` by hand.
atrium's README has a udev rule that hands the board to your login session
instead of root.

### The rig file

`examples/simtrace2-relay.plexus.yaml` is SimTrace2 → trace → PC/SC reader with
one bench's two devices, which are the values to change: the board by its USB
path (`lsusb -t`, or read it from `plexus remsim`), the reader by any fragment of
its name that matches exactly one reader (`pcsc:SCR 3310`). Then
`plexus check examples/simtrace2-relay.plexus.yaml`.

The trace node writes `logs/trace.jsonl`, flushed per line so a killed process
still leaves a usable trace. The path resolves inside `PLEXUS_WORKSPACE`, or the
directory you start Plexus from when that is unset. `detail` sets how much each
line carries:

| `detail` | Each line carries |
|---|---|
| `raw` | the command and response hex |
| `tlv` | plus every TLV in the response, flattened |
| `annotated` | plus each known tag's name and the status word decoded |

### When it fails

| What you see | What it means |
|---|---|
| `unable to open card on reader 'Virtual PCD 00 00'` (`No smart card inserted`) | The graph is not armed. Press Run first; never add `-k`. |
| The terminal transacts, but Plexus shows no traffic | The daemon opened a real reader: its `-n` is wrong. Compare the command with `pcsc_scan -r`. |
| The console warns a daemon was already running | It opened its card before this Run. Stop it and start it again. |
| `Card was removed` (`SCARD_W_REMOVED_CARD`) mid-trace | The chain dropped: Stop pressed, the card lost contact, or atrium took the reader. Run again, then restart the daemon. |
| `No reader matching '…'` | The PC/SC device fragment matches no reader, or several. |
| A crash on the first complete command | `-k` was passed. |
| `error while loading shared libraries: libosmo…` | sudo dropped `LD_LIBRARY_PATH`. Export it and use a launcher. |
| The terminal powers the card, then sends nothing | Try `--synthetic-atr`. The Verifone measured in the T=1 research README completed only with it. |
| `atrium is not checked out` | A ZIP download: set `PLEXUS_ATRIUM`. |

A power cycle from the terminal is not passed to the card, which stays open for
the whole run. A terminal that expects a cold reset between transactions sees a
card that kept its state, so stop and Run again between transactions if the
second one behaves oddly.

---

## What is not built

**This is the honest part of this page.** The engine binds, drives, rewrites,
reports and releases. The cards it can bind today are a recorded capture and a
contact card in a **PC/SC reader**; the terminals are the fingerprint probe,
the EMV agent (once a model back end is configured: `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, or a local `ATRIUM_LLM_BASE_URL`), and a real payment
terminal through a **SimTrace2**.

The SimTrace2 is found the way atrium's dashboard finds it: the board by its
USB id (1d50:60e3) in `/sys`, the `simtrace2-remsim` serving it in `/proc`.
Neither is started by Plexus. Binding connects atrium's `VirtualCard` to vpcd,
the console then prints the daemon command, and every command the terminal
sends goes through the chain (see [A SimTrace2 relay](#a-simtrace2-relay)).
What is tested without the board: everything from vpcd inwards, against a
stand-in vpcd speaking vpcd's framing. What still needs a rig: the board, the
daemon, the launchers and a real terminal.

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
| SimTrace2 | `api/routes/simtrace.py` discovery + `virtual_card.VirtualCard` — **done**, rig run owed |

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
