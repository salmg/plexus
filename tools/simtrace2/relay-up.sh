#!/usr/bin/env bash
#
# relay-up.sh: the whole SimTrace2 relay, headless, in one command.
#
# The unattended way: no browser, no node animations. It arms the graph with
# `plexus run`, waits for the "Armed" line, then starts simtrace2-remsim and
# follows the trace. For scripted captures.
#
# For the browser console and the animations, run `plexus up` and
# relay-daemon.sh instead. Never both at once: only one program can hold vpcd's
# virtual reader, so whichever arms second fails.
#
# relay-daemon.sh explains why the daemon must start after the graph arms, and
# why -k is refused.
#
# USAGE
#   tools/simtrace2/relay-up.sh                                 # the example rig
#   tools/simtrace2/relay-up.sh my-rig.plexus.yaml              # another rig
#   tools/simtrace2/relay-up.sh my-rig.plexus.yaml --synthetic-atr
#   TIMEOUT=600 DEVICE=simtrace2:1-2.2 tools/simtrace2/relay-up.sh
#
# ENVIRONMENT
#   TIMEOUT          seconds before Plexus stops the relay (default 0: Ctrl-C)
#   DEVICE, REMSIM, LD_LIBRARY_PATH, PLEXUS   as in relay-daemon.sh
set -uo pipefail

# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

RIG="$repo/examples/simtrace2-relay.plexus.yaml"
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
  RIG="$1"
  shift
fi
TIMEOUT="${TIMEOUT:-0}"

refuse_keep_running "$@" || exit 1
plexus="$(find_plexus)" || exit 1
[ -r "$RIG" ] || { echo "No rig file at $RIG" >&2; exit 1; }
"$plexus" check "$RIG" || exit 1
daemon_argv "$plexus" "$@" || exit 1

# Cache the sudo credential now, so the daemon does not stop for a password
# at the moment the payment terminal is waiting for a card.
if uses_sudo; then
  sudo -v || exit 1
fi

log="$(mktemp -t plexus-relay.XXXXXX)"
plexus_pid=""
daemon_pid=""
tail_pid=""

cleanup() {
  trap - EXIT INT TERM
  if [ -n "$daemon_pid" ]; then
    if uses_sudo; then sudo kill "$daemon_pid" 2>/dev/null; else kill "$daemon_pid" 2>/dev/null; fi
  fi
  # `plexus run` takes SIGTERM as Ctrl-C: it releases the card and prints its
  # summary rather than vanishing mid-exchange.
  [ -n "$plexus_pid" ] && kill -TERM "$plexus_pid" 2>/dev/null
  wait "$plexus_pid" 2>/dev/null
  [ -n "$tail_pid" ] && kill "$tail_pid" 2>/dev/null
  wait 2>/dev/null
  echo
  grep -E "exchanges, [0-9]+ errors" "$log" 2>/dev/null
  echo "Full Plexus output: $log"
}
trap cleanup EXIT INT TERM

# PYTHONUNBUFFERED matters: without it the "Armed" line sits in a pipe buffer
# and the wait below never sees it.
echo "Arming Plexus with $RIG"
PYTHONUNBUFFERED=1 "$plexus" run "$RIG" --timeout "$TIMEOUT" >"$log" 2>&1 &
plexus_pid=$!

# The Armed line is logged once every node is bound, card included, which is
# exactly when the daemon can open its card.
armed=0
for _ in $(seq 150); do
  if grep -qE "Armed [0-9]+ nodes" "$log" 2>/dev/null; then
    armed=1
    break
  fi
  if ! kill -0 "$plexus_pid" 2>/dev/null; then
    plexus_pid=""
    echo "Plexus exited before arming:" >&2
    cat "$log" >&2
    exit 1
  fi
  sleep 0.2
done
if [ "$armed" != 1 ]; then
  echo "Plexus did not arm within 30 s:" >&2
  cat "$log" >&2
  exit 1
fi

echo "Armed. Starting: ${DAEMON[*]}"
"${DAEMON[@]}" &
daemon_pid=$!

echo "Relay is up. Present the card at the terminal. Ctrl-C to stop."
echo
# In the background and waited for, because bash runs a trap only once the
# foreground command returns: a `tail -f` in front would hold off the cleanup
# for as long as Plexus runs. `wait` returns as soon as a signal arrives.
tail -n +1 -f "$log" --pid="$plexus_pid" &
tail_pid=$!
wait "$tail_pid"
