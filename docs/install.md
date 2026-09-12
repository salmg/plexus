# Install

Two halves, and you only need the first to get somewhere.

- **The editor** needs Node 20 or newer. Nothing else. No reader, no Python.
- **The runtime** needs Python 3.8 or newer, and only when you want to arm a
  graph for real.

Do the editor first. It ships a simulator, so you can build a rig, run it and
read the trace before deciding whether the rest is worth installing.

---

## 1. Get the code

Plexus keeps atrium as a git submodule, so clone with it:

```bash
git clone --recurse-submodules https://github.com/salmg/plexus
cd plexus
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init vendor/atrium
```

`vendor/atrium` is pinned at a commit and **never edited**. Upgrading it is an
explicit, reviewable step:

```bash
git submodule update --remote vendor/atrium
```

## 2. The editor

```bash
cd ui
npm install
npm run dev
```

Open <http://localhost:5173>.

You should see the example rig, and the top bar should say **simulated**:

![The editor on first run](images/01-overview.png)

That badge matters. It is how you know nothing is touching hardware — and
later, how you know something is.

### Checking it works

Press **Run**. The simulator pushes a plausible contactless transaction through
whatever chain is drawn, and you should see node counters climb, the edges go
green, and the trace scroll in the console:

![A running graph](images/08-running.png)

Press **Stop**. If that worked, the editor is installed.

> The APDUs the simulator sends are *illustrative, not a capture*. The
> structures are shaped like the real thing so the trace reads correctly to
> someone who knows EMV, but no card emitted those bytes and nothing should be
> concluded from them.

## 3. The runtime

Only needed to arm a graph for real. The model, the checks and the CLI need
nothing but PyYAML:

```bash
cd ..            # back to the repository root
pip install -e .
```

Check it:

```bash
plexus nodes
```

```
   capture            card       terminal(answers)                  Recorded capture
 · pcsc               card       terminal(answers)                  PC/SC reader
   probe              terminal   card(asks)                         Fingerprint probe
   mutation           transform  terminal(answers) card(asks)       Mutation
   ...

· needs hardware or a daemon this build cannot start; the rest run anywhere.
```

The dot is the honest part: those nodes resolve in a graph and refuse at bind
time, naming what they need.

Now run a rig that needs nothing at all:

```bash
plexus run examples/probe-a-capture.plexus.yaml
```

```
info  Armed 3 nodes: probe-1 -> mutation-1 -> capture-1
    31.2 -> 00A404000E325041592E5359532E444446303100
    32.6 <- 6F20840E325041592E5359532E4444463031A50E…9000  1.4 ms
    ...
    34.3 <- 70145A0854133300123456785F24032512315F3401019000  0.0 ms
    34.4 <- 70145A0854133300123456785F24032512315F3401079000  rewritten by mutation-1

4 exchanges, 0 errors.
```

Those last two lines are the point: what the card said, and what the terminal
was told. The rewrite is atrium's own mutation engine, not a stand-in.

### The server the editor talks to

```bash
pip install -e ".[runtime]"
plexus serve
```

```
Plexus runtime on ws://127.0.0.1:8900/ws
Point the editor at it:  http://localhost:5173/?runtime=ws://127.0.0.1:8900/ws
```

Open that second URL. The top bar now says `127.0.0.1:8900/ws` instead of
**simulated**, Run arms the graph for real, and the console reaches into it.

`serve` binds loopback. Binding anywhere else **refuses to start** without a
token — see [What it refuses](security.md).

## 4. Optional: the tests

```bash
pip install -e ".[dev]"
pytest
```

90 tests. They cover the model, the checks, the engine end to end against a
recorded capture, the WebSocket protocol, and every hole that has been found
and closed.

---

## Platform notes

**Windows.** Everything above works. The editor is unaffected. For the runtime,
note that Git Bash cannot execute a Microsoft Store Python — it reports
`Permission denied`, which reads exactly like absence. Use PowerShell, or a
`python.org` install.

**The hardware half needs Linux.** Not Plexus's doing — that is where `pcscd`,
`vpcd` and `simtrace2-remsim` live, and atrium's own setup guide assumes it.
You can draw and check a graph anywhere; you arm it where the readers are.

**Air-gapped benches.** Nothing in the editor fetches anything at run time: no
web fonts, no icon CDN, no analytics. `npm install` is the only step that needs
a network, and it can be done elsewhere and copied.

---

Next: **[Your first rig](first-run.md)** — build and run a relay from nothing.
