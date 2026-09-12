/**
 * The console.
 *
 * A real terminal rather than a log pane, because the useful thing during a
 * relay is not reading the trace — it is reaching into the running process:
 * pushing an APDU by hand, holding traffic, asking what a node has actually
 * seen.  Commands go straight to the runtime, so the same console works
 * against the simulator and against a card.
 *
 * The APDU stream is echoed here too, dimmed, and can be muted when you want
 * to type without the floor moving.
 */

import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";

import type { ApduEvent, LogLine } from "../graph/types";
import { useRuntime } from "../store/useRuntime";

// The four colours below are ANSI slots, not literal colours, and the theme
// below maps each slot onto a token — so the prompt is green on the dark ground
// and blue on the light one without this file knowing either.
const PROMPT = "\x1b[32mplexus\x1b[0m\x1b[2m>\x1b[0m ";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
const RED = "\x1b[31m";
const AMBER = "\x1b[33m";

/**
 * Read a design token as a single-line string.
 *
 * A wrapped CSS value keeps its newline in the computed style, and handing
 * that to xterm as a font family is invalid inline CSS — the terminal falls
 * back to the UI font and draws nothing. Collapsing whitespace here means the
 * tokens can be formatted however reads best.
 */
function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .replace(/\s+/g, " ")
    .trim();
  return value || fallback;
}

function themeFromCss(): Record<string, string> {
  const read = token;
  const accent = read("--accent", "#46d9a8");
  return {
    background: read("--surface-sunken", "#0e1116"),
    foreground: read("--text", "#dfe5ee"),
    cursor: accent,
    selectionBackground: read("--accent-wash", "rgba(70,217,168,0.25)"),
    green: accent,
    brightGreen: accent,
    red: read("--danger", "#e5534b"),
    brightRed: read("--danger", "#e5534b"),
    yellow: read("--warn", "#e0a145"),
    brightYellow: read("--warn", "#e0a145"),
    blue: read("--port-card", "#e0a145"),
    cyan: read("--port-terminal", "#3fb6c4"),
  };
}

export function Console() {
  const host = useRef<HTMLDivElement | null>(null);
  const term = useRef<Terminal | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const [muted, setMuted] = useState(false);
  const send = useRuntime((s) => s.send);

  // The terminal is built once and lives outside React's render cycle, so it
  // reads these through refs rather than closing over a stale value.
  const mutedRef = useRef(muted);
  const sendRef = useRef(send);

  useEffect(() => {
    mutedRef.current = muted;
  }, [muted]);

  useEffect(() => {
    sendRef.current = send;
  }, [send]);

  useEffect(() => {
    if (!host.current) return;

    const terminal = new Terminal({
      fontFamily: token("--font-mono", "Consolas, monospace"),
      fontSize: 12.5,
      lineHeight: 1.35,
      cursorBlink: true,
      convertEol: true,
      theme: themeFromCss(),
      scrollback: 2000,
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(host.current);
    fitAddon.fit();

    term.current = terminal;
    fit.current = fitAddon;

    // Handy from the browser console while developing; not shipped.
    if (import.meta.env.DEV) {
      (window as unknown as Record<string, unknown>).__term = terminal;
    }

    terminal.writeln(`${DIM}Plexus console. Type "help".${RESET}`);
    terminal.write(PROMPT);

    /* ── Line editor ──────────────────────────────────────────────────── */

    let line = "";
    let cursor = 0;
    const history: string[] = [];
    let historyAt = 0;
    let busy = false;

    const redraw = () => {
      terminal.write(`\r\x1b[K${PROMPT}${line}`);
      const back = line.length - cursor;
      if (back > 0) terminal.write(`\x1b[${back}D`);
    };

    const submit = async () => {
      const input = line;
      terminal.write("\r\n");
      line = "";
      cursor = 0;

      if (input.trim() === "") {
        terminal.write(PROMPT);
        return;
      }

      history.push(input);
      historyAt = history.length;

      if (input.trim() === "clear") {
        terminal.clear();
        terminal.write(PROMPT);
        return;
      }

      busy = true;
      try {
        const result = await sendRef.current(input);
        const colour = result.level === "error" ? RED : "";
        for (const out of result.lines) {
          terminal.writeln(`${colour}${out}${colour ? RESET : ""}`);
        }
      } catch (err) {
        terminal.writeln(`${RED}${err instanceof Error ? err.message : String(err)}${RESET}`);
      } finally {
        busy = false;
        terminal.write(PROMPT);
      }
    };

    const disposable = terminal.onData((data) => {
      if (busy) return;

      switch (data) {
        case "\r":
          void submit();
          return;
        case "\x7f":
          if (cursor > 0) {
            line = line.slice(0, cursor - 1) + line.slice(cursor);
            cursor -= 1;
            redraw();
          }
          return;
        case "\x03":
          terminal.write("^C\r\n");
          line = "";
          cursor = 0;
          terminal.write(PROMPT);
          return;
        case "\x0c":
          terminal.clear();
          redraw();
          return;
        case "\x1b[A":
          if (historyAt > 0) {
            historyAt -= 1;
            line = history[historyAt] ?? "";
            cursor = line.length;
            redraw();
          }
          return;
        case "\x1b[B":
          historyAt = Math.min(historyAt + 1, history.length);
          line = history[historyAt] ?? "";
          cursor = line.length;
          redraw();
          return;
        case "\x1b[D":
          if (cursor > 0) {
            cursor -= 1;
            terminal.write("\x1b[D");
          }
          return;
        case "\x1b[C":
          if (cursor < line.length) {
            cursor += 1;
            terminal.write("\x1b[C");
          }
          return;
        case "\x1b[H":
          cursor = 0;
          redraw();
          return;
        case "\x1b[F":
          cursor = line.length;
          redraw();
          return;
        default:
          // Printable runs only; anything with a control character is ignored
          // rather than echoed as mojibake.
          if (/^[\x20-\x7e]+$/.test(data)) {
            line = line.slice(0, cursor) + data + line.slice(cursor);
            cursor += data.length;
            redraw();
          }
      }
    });

    /* ── Live stream ──────────────────────────────────────────────────── */

    let apduCursor = useRuntime.getState().apdus.length;
    let logCursor = useRuntime.getState().logs.length;

    const echo = (text: string) => {
      // Print above the prompt and put the half-typed line back underneath.
      terminal.write(`\r\x1b[K${text}\r\n`);
      redraw();
    };

    const unsubscribe = useRuntime.subscribe((state) => {
      if (state.apdus.length < apduCursor) apduCursor = 0;
      if (state.logs.length < logCursor) logCursor = 0;

      const newLogs = state.logs.slice(logCursor);
      logCursor = state.logs.length;
      for (const log of newLogs) echo(formatLog(log));

      const newApdus = state.apdus.slice(apduCursor);
      apduCursor = state.apdus.length;
      if (!mutedRef.current) {
        for (const event of newApdus) echo(formatApdu(event));
      }
    });

    const observer = new ResizeObserver(() => {
      try {
        fitAddon.fit();
      } catch {
        // fit() throws while the pane is collapsed to zero height; harmless.
      }
    });
    observer.observe(host.current);

    return () => {
      observer.disconnect();
      unsubscribe();
      disposable.dispose();
      terminal.dispose();
      term.current = null;
      fit.current = null;
    };
  }, []);

  // Repaint xterm's own colours when the app theme flips.
  useEffect(() => {
    const root = document.documentElement;
    const apply = () => {
      if (term.current) term.current.options.theme = themeFromCss();
    };
    const observer = new MutationObserver(apply);
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  return (
    <section className="console" aria-label="Console">
      <div className="panel-head">
        <span>Console</span>
        <div className="console__tools">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setMuted((m) => !m)}
            aria-pressed={muted}
          >
            {muted ? "Stream off" : "Stream on"}
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => term.current?.clear()}
          >
            Clear
          </button>
        </div>
      </div>
      <div className="console__term" ref={host} />
    </section>
  );
}

function formatApdu(event: ApduEvent): string {
  const arrow = event.dir === "command" ? "->" : "<-";
  const time = `${DIM}${event.t.toFixed(0).padStart(6)}ms${RESET}`;
  const body = wrapHex(event.hex);
  const tail = [
    event.ms !== undefined ? `${event.ms.toFixed(1)}ms` : "",
    event.mutatedBy ? `${AMBER}mutated by ${event.mutatedBy}${RESET}` : "",
    event.note ? `${DIM}${event.note}${RESET}` : "",
  ]
    .filter(Boolean)
    .join("  ");
  return `${time} ${arrow} ${body}${tail ? "  " + tail : ""}`;
}

/** Long APDUs are truncated in the console; the full bytes live in the trace. */
function wrapHex(hex: string): string {
  if (hex.length <= 48) return hex;
  return `${hex.slice(0, 48)}${DIM}…+${(hex.length - 48) / 2}B${RESET}`;
}

function formatLog(line: LogLine): string {
  const colour = line.level === "error" ? RED : line.level === "warn" ? AMBER : DIM;
  return `${colour}${line.source}: ${line.text}${RESET}`;
}
