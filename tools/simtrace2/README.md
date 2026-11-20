# SimTrace2 launchers

Two ways to start `simtrace2-remsim` at the only moment it can work: after a
SimTrace2 graph is armed. The card the daemon opens on vpcd's
`Virtual PCD 00 00` is the graph itself, so started first it finds no card and
exits.

| Script | Use it with | What it does |
|---|---|---|
| `relay-daemon.sh` | `plexus up` and the browser | Waits for Run, then starts the daemon |
| `relay-up.sh` | nothing else | Arms a rig with `plexus run`, starts the daemon, follows the trace |

Both take the daemon command from `plexus remsim`, the same line the console
prints once armed, with `-n` set to the Virtual PCD's reader number. Extra
arguments go to the daemon (`--synthetic-atr`, `-T t1`). Both refuse `-k`.

```bash
bash tools/simtrace2/relay-daemon.sh        # then press Run in the browser
bash tools/simtrace2/relay-up.sh            # headless, examples/simtrace2-relay.plexus.yaml
```

The full walkthrough, setup and failure table are in
[Running on hardware](../../docs/hardware.md#a-simtrace2-relay).
