# When it goes wrong

Mostly failures that look like something else. Every one of these has actually
happened.

---

## The editor

### The graph is four specks in a corner

React Flow fitted the view before the layout gave the canvas a height, saw a
zero-sized box and clamped to minimum zoom. Fixed, but if you see it again: the
fit retries on a timer and on `visibilitychange`, so bring the window forward.

### Nothing renders in the console, but commands work

The terminal paints on `requestAnimationFrame`, and a **hidden or backgrounded
window stops animation frames entirely**. The buffer is fine; the paint is not.
Bring the window forward.

To tell the difference, check the buffer rather than the DOM:

```js
window.__term.buffer.active.getLine(0).translateToString(true)
```

If that has your text and the screen does not, it is the window.

### The console renders in the wrong font, or not at all

`--font-mono` is read out of the computed style and handed to xterm as a font
string. A **wrapped CSS value keeps its newline**, which is invalid inline CSS —
the terminal silently falls back to the UI font and can end up drawing nothing.
Keep the font tokens on one line. `token()` in `Console.tsx` also collapses
whitespace, so both halves of that have to break for it to return.

### Run is disabled and I cannot see why

Open the **Inspector**. Below 1100px wide it folds away automatically, which
hides the checks list — the badge in the top bar still shows the count, but the
sentences are in the panel. The Inspector button brings it back.

### A wire will not land

That is the point, and the reason is on screen for three seconds. The common
ones:

| | |
|---|---|
| *Both ports ask / answer* | An edge joins one that asks to one that answers. Card port on the right, terminal port on the left. |
| *The card port on X is already wired* | Each port carries exactly one conversation. Fanning a card out to two terminals is two graphs, not one. |
| *That would close a loop* | |

### Switching language leaves some text in English

Two possibilities. If it is a node title, summary or caveat, that key is not
translated yet and the fallback is doing its job — see
[Languages](languages.md). If it is a whole panel that did not change, a memo
over translated output is missing `t` in its dependency array.

### Opening a file does nothing

A `.plexus.yaml` that will not parse reports why in an alert rather than
loading. A file that parses shows the review sheet first — it does not load
until you press Load.

---

## The runtime

### `plexus: command not found`

`pip install -e .` was not run, or not in the environment on your `PATH`.
`python -m plexus.cli` does the same thing without the entry point.

### `No catalogue at …/plexus/schema/catalogue.json`

The Python side reads the catalogue the editor exports. Regenerate it:

```bash
cd ui && npm run export:catalogue
```

### `No atrium checkout at …/vendor/atrium`

The submodule was not fetched:

```bash
git submodule update --init vendor/atrium
```

An uninitialised submodule is an **empty directory that exists**, which is why
the check is for a file inside it rather than the directory.

### `The pcsc node is not implemented in this build`

It is not a broken graph. That node needs hardware adapters that are not
written yet, and it says which. `plexus nodes` marks every one of them.
See [Running on hardware](hardware.md).

### `Capture … is outside the workspace`

Paths resolve inside the directory the runtime was started in. Either move the
file in, or say where the workspace is:

```bash
PLEXUS_WORKSPACE=/srv/captures plexus serve
```

A graph cannot widen that itself — see [What it refuses](security.md).

### `Refusing to bind 0.0.0.0 without authentication`

Working as intended. Either bind loopback and use an SSH tunnel, or set
`PLEXUS_API_TOKEN`.

### The editor will not connect, and nothing is logged

Check the runtime's output for:

```
refused a websocket from origin 'https://…'
```

The Origin allow-list is loopback plus `PLEXUS_ALLOWED_ORIGINS`. If the editor
is served from another host, name it there.

Then check `?runtime=` actually reached the app — the top bar shows the address
it is talking to, and still says **simulated** if the parameter was dropped.

### The trace goes quiet but Stop is still offered

That is correct. **A terminal that runs out of script has not stopped:** the
graph is still armed, the card is still bound, and `tx` still reaches it. The
nodes move from *live* to *ready* and the runtime stays `running` until you
stop it.

### `N trace frames dropped keeping up`

The runtime is producing more trace than the socket is draining, so it thinned
the trace rather than stalling a relay that is holding a card. Telemetry and
state are never dropped. The full trace belongs in a trace-tap file, not in the
browser.

---

## Windows

### Python is "missing" but it is installed

Git Bash cannot execute a Microsoft Store Python — it exits `Permission denied`,
which reads exactly like absence. **PowerShell runs the same binary fine.**
Always confirm an interpreter from PowerShell before concluding it is not there.

```powershell
python --version      # Python 3.8.10
```

### `npm` or `node` not recognised

They may be installed but absent from the `PATH` a subprocess inherits:

```powershell
$env:Path = "C:\Program Files\nodejs;" + $env:Path
```

### The screenshot script cannot find Chrome

```bash
CHROME="C:/Program Files/Google/Chrome/Application/chrome.exe" npm run screenshots
```

It looks in the usual places for Chrome and Chromium on all three platforms
first, and tells you where it looked.

---

## Filing something

Useful in a report, roughly in order of how much it saves:

1. The `.plexus.yaml`, or enough of it to draw the same shape.
2. Whether the top bar said **simulated** or an address.
3. `plexus check <file>` output.
4. The console, including the lines before the one that looks wrong.
5. `plexus nodes`, if a node refused to bind.
