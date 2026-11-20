#!/usr/bin/env bash
#
# relay-daemon.sh: start simtrace2-remsim the moment a SimTrace2 graph arms.
#
# Use it with `plexus up`, so the browser keeps its console and the node
# animations. Start this first, in a second terminal, then press Run in the
# browser. It notices, and brings the daemon up behind you.
#
# WHY THE ORDER MATTERS
#   simtrace2-remsim is a PC/SC client of vpcd's "Virtual PCD 00 00" reader.
#   The card it opens there is not a physical card: it IS the Plexus graph,
#   presented through atrium's VirtualCard. The daemon opens that card once, at
#   startup, so it can only start after Plexus has armed. Started first, it
#   finds an empty reader and exits.
#
# NEVER PASS -k
#   It does not retry the card open, which happens before its retry loop. It
#   loops on with no card and no ATR, and crashes on the first complete command.
#   This script refuses it.
#
# HOW IT NOTICES THE ARMING
#   vpcd listens on port 35963 inside pcscd, and the SimTrace2 node connects to
#   it when the graph arms. An established connection on that port means there
#   is now a card on the virtual reader. Watching the socket stays out of the
#   relay's way; opening a PC/SC connection to probe the reader would not.
#
# USAGE
#   tools/simtrace2/relay-daemon.sh                    # the only board
#   tools/simtrace2/relay-daemon.sh --synthetic-atr    # extra flags go to the daemon
#   DEVICE=simtrace2:1-2.2 tools/simtrace2/relay-daemon.sh
#
# ENVIRONMENT
#   DEVICE           which board (simtrace2:<usb path>); the only one when unset
#   REMSIM           a simtrace2-remsim to run instead of the one atrium finds
#   LD_LIBRARY_PATH  carried through sudo, for a build against ~/.local/osmo
#   WAIT             seconds to wait for Run (default 300)
#   VPCD_PORT        vpcd's port (default 35963)
#   PLEXUS           the plexus command (default: on the PATH, else ./.venv)
set -uo pipefail

# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

VPCD_PORT="${VPCD_PORT:-35963}"
WAIT="${WAIT:-300}"

refuse_keep_running "$@" || exit 1
command -v ss >/dev/null || { echo "ss not found: sudo apt install iproute2" >&2; exit 1; }
plexus="$(find_plexus)" || exit 1
daemon_argv "$plexus" "$@" || exit 1

echo "Daemon command: ${DAEMON[*]}"

# Take the password NOW, before the wait. Otherwise the prompt lands at the
# moment the graph arms, with the payment terminal already waiting on a card.
if uses_sudo; then
  sudo -v || exit 1
fi

vpicc_attached() {
  ss -tn state established 2>/dev/null \
    | grep -qE "[:.]${VPCD_PORT}([[:space:]]|$)"
}

if vpicc_attached; then
  echo "Something is already connected to vpcd on port ${VPCD_PORT}: an armed"
  echo "graph, or atrium's own relay."
else
  echo "Waiting for Plexus to arm. Press Run in the browser now."
  echo "(watching for a connection to vpcd on port ${VPCD_PORT}; giving up after ${WAIT}s)"
  waited=0
  until vpicc_attached; do
    sleep 1
    waited=$((waited + 1))
    # Keep the sudo timestamp fresh through a long wait.
    if uses_sudo && [ $((waited % 60)) -eq 0 ]; then
      sudo -n -v 2>/dev/null
    fi
    if [ "$waited" -ge "$WAIT" ]; then
      {
        echo "Timed out: nothing connected to vpcd on port ${VPCD_PORT}."
        echo
        echo "Check, in this order:"
        echo "  1. Run succeeded in the browser: its console says 'Armed'."
        echo "  2. plexus up runs on this machine, where pcscd has vpcd loaded"
        echo "     (pcsc_scan -r lists Virtual PCD 00 00)."
        echo "  3. atrium's own relay is stopped. Only one program can hold"
        echo "     the virtual reader."
      } >&2
      exit 1
    fi
  done
  echo "Armed after ${waited}s."
fi

# The connection appears when the SimTrace2 node binds, a moment before the
# card does. A card that then fails to open releases it again, so look twice.
sleep 1
if ! vpicc_attached; then
  echo "The connection to vpcd went away again: that Run failed. Read the" >&2
  echo "browser console, fix what it says, and start this again." >&2
  exit 1
fi

echo "Starting the daemon. Ctrl-C to stop it; press Stop in the browser too."
echo
exec "${DAEMON[@]}"
