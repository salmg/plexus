# Shared by relay-daemon.sh and relay-up.sh. Sourced, never run on its own.
#
# The daemon command is never written here. It comes from `plexus remsim`,
# which is the same line the Plexus console prints once a SimTrace2 graph is
# armed: atrium's binary lookup and sudo setting, the board's USB path, and
# `-n` set to the PC/SC reader number of vpcd's "Virtual PCD 00 00", found by
# name. simtrace2-remsim picks its reader by number alone, and with a real
# reader at number 0 it would open the real card directly: the terminal gets a
# working relay and Plexus sees nothing.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

#: The daemon's argv, filled by daemon_argv.
DAEMON=()

# The plexus command: $PLEXUS, else plexus on the PATH, else this checkout's venv.
find_plexus() {
  if [ -n "${PLEXUS:-}" ]; then
    echo "$PLEXUS"
  elif command -v plexus >/dev/null 2>&1; then
    command -v plexus
  elif [ -x "$repo/.venv/bin/plexus" ]; then
    echo "$repo/.venv/bin/plexus"
  else
    echo "plexus is not on the PATH and $repo/.venv/bin/plexus does not exist." >&2
    echo "Activate the venv Plexus is installed in, or set PLEXUS=/path/to/plexus." >&2
    return 1
  fi
}

# -k looks like the fix for "no smart card inserted". It is not.
refuse_keep_running() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -k | --keep-running)
        echo "Refusing -k." >&2
        echo >&2
        echo "simtrace2-remsim opens its card once, before its relay loop. With -k a" >&2
        echo "failed open does not retry: it loops on with no card and no ATR, the" >&2
        echo "terminal sees whatever stale ATR the board still holds, and the first" >&2
        echo "complete command crashes it. 'No smart card inserted' means Plexus is" >&2
        echo "not armed yet: press Run first." >&2
        return 1
        ;;
    esac
  done
}

# Fills DAEMON with the simtrace2-remsim command, then any extra arguments
# (--synthetic-atr, -T t1...). $1 is the plexus command.
#
#   DEVICE           which board, simtrace2:<usb path> or the bare path
#   REMSIM           a simtrace2-remsim to run instead of the one atrium finds
#   LD_LIBRARY_PATH  carried through sudo, which drops it
daemon_argv() {
  local plexus="$1"
  shift
  local line
  line="$("$plexus" remsim ${DEVICE:+--device "$DEVICE"})" || return 1
  # atrium shell-quotes every argument, so the line splits back into the argv
  # it was made from.
  eval "DAEMON=($line)"

  local at=0
  [ "${DAEMON[0]}" = sudo ] && at=1
  [ -n "${REMSIM:-}" ] && DAEMON[at]="$REMSIM"
  local bin="${DAEMON[at]}"
  if [ ! -x "$bin" ] && ! command -v "$bin" >/dev/null 2>&1; then
    echo "No simtrace2-remsim at '$bin'." >&2
    echo "Build the host tools (host/ in the SIMtrace2 T=1 tree), then point at" >&2
    echo "the result: REMSIM=/path/to/host/src/simtrace2-remsim" >&2
    return 1
  fi

  # A simtrace2-remsim built against a libosmocore under $HOME needs
  # LD_LIBRARY_PATH to load, and sudo drops it from the environment.
  if [ "$at" = 1 ] && [ -n "${LD_LIBRARY_PATH:-}" ]; then
    DAEMON=(sudo env "LD_LIBRARY_PATH=$LD_LIBRARY_PATH" "${DAEMON[@]:1}")
  fi
  DAEMON+=("$@")
}

uses_sudo() {
  [ "${DAEMON[0]}" = sudo ]
}
