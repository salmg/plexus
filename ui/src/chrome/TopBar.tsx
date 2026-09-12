/**
 * The top bar: what graph is open, what the runtime is doing, and the one
 * control that matters — arm or stop.
 *
 * Run is disabled while any check is an error.  That is deliberate: the checks
 * exist so a graph that would fail as a timeout or a hardware fault fails here
 * instead, with a sentence saying why.
 */

import { useRef, useState } from "react";

import { fromYaml, GraphParseError, toYaml } from "../graph/serialize";
import type { Problem } from "../graph/validate";
import { hasBlockingProblem } from "../graph/validate";
import { emptyGraph } from "../graph/types";
import type { PlexusGraph } from "../graph/types";
import { ImportReview } from "./ImportReview";
import { useGraph } from "../store/useGraph";
import { isRunning, useRuntime } from "../store/useRuntime";

interface TopBarProps {
  problems: Problem[];
  consoleOpen: boolean;
  onToggleConsole: () => void;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
}

export function TopBar({
  problems,
  consoleOpen,
  onToggleConsole,
  inspectorOpen,
  onToggleInspector,
}: TopBarProps) {
  const graph = useGraph((s) => s.graph);
  const dirty = useGraph((s) => s.dirty);
  const theme = useGraph((s) => s.theme);
  const setTheme = useGraph((s) => s.setTheme);
  const setGraph = useGraph((s) => s.setGraph);
  const markClean = useGraph((s) => s.markClean);
  const undo = useGraph((s) => s.undo);
  const redo = useGraph((s) => s.redo);
  const canUndo = useGraph((s) => s.past.length > 0);
  const canRedo = useGraph((s) => s.future.length > 0);

  const state = useRuntime((s) => s.state);
  const runtime = useRuntime((s) => s.runtime);
  const run = useRuntime((s) => s.run);
  const stop = useRuntime((s) => s.stop);

  const fileInput = useRef<HTMLInputElement | null>(null);

  const running = isRunning(state);
  const blocked = hasBlockingProblem(problems);
  const errors = problems.filter((p) => p.level === "error").length;
  const warnings = problems.length - errors;

  const save = () => {
    const blob = new Blob([toYaml(graph)], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${graph.name.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}.plexus.yaml`;
    a.click();
    URL.revokeObjectURL(url);
    markClean();
  };

  // Parsed and shown before it is loaded. A graph file is something people
  // send each other, and it names hardware, addresses and paths — opening one
  // should not be the same gesture as agreeing to all of that.
  const [pending, setPending] = useState<{ graph: PlexusGraph; filename: string } | null>(
    null,
  );

  const open = async (file: File) => {
    try {
      setPending({ graph: fromYaml(await file.text()), filename: file.name });
    } catch (err) {
      const message =
        err instanceof GraphParseError
          ? err.message
          : `Could not read that file: ${err instanceof Error ? err.message : String(err)}`;
      window.alert(message);
    }
  };

  return (
    <header className="topbar">
      <div className="topbar__brand">
        Plexus
        <small>node canvas for atrium</small>
      </div>

      <span className="topbar__name" title={graph.name}>
        {graph.name}
        {dirty && <span className="topbar__dirty"> •</span>}
      </span>

      <div className="topbar__spacer" />

      {(errors > 0 || warnings > 0) && (
        <span className={`badge ${errors > 0 ? "badge--danger" : "badge--warn"}`}>
          {errors > 0 && `${errors} error${errors === 1 ? "" : "s"}`}
          {errors > 0 && warnings > 0 && ", "}
          {warnings > 0 && `${warnings} warning${warnings === 1 ? "" : "s"}`}
        </span>
      )}

      <span className="badge" title="Where the graph would actually run">
        {runtime.label}
      </span>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={undo}
        disabled={!canUndo}
        title="Undo"
      >
        Undo
      </button>
      <button
        type="button"
        className="btn btn--ghost"
        onClick={redo}
        disabled={!canRedo}
        title="Redo"
      >
        Redo
      </button>

      <button type="button" className="btn" onClick={() => fileInput.current?.click()}>
        Open
      </button>
      <input
        ref={fileInput}
        type="file"
        accept=".yaml,.yml"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void open(file);
          e.target.value = "";
        }}
      />
      <button type="button" className="btn" onClick={save}>
        Save
      </button>
      <button
        type="button"
        className="btn"
        onClick={() => setGraph(emptyGraph(), { clean: true })}
      >
        New
      </button>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={onToggleConsole}
        aria-pressed={consoleOpen}
        title="Toggle the console"
      >
        Console
      </button>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={onToggleInspector}
        aria-pressed={inspectorOpen}
        title="Toggle the inspector"
      >
        Inspector
      </button>

      <button
        type="button"
        className="btn btn--ghost btn--icon"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        title={theme === "dark" ? "Light mode" : "Dark mode"}
      >
        {theme === "dark" ? "☾" : "☀"}
      </button>

      {running ? (
        <button type="button" className="btn btn--danger" onClick={() => void stop()}>
          Stop
        </button>
      ) : (
        <button
          type="button"
          className="btn btn--primary"
          disabled={blocked}
          title={blocked ? "Fix the errors in the checks list first" : "Bind devices and start"}
          onClick={() => void run(graph)}
        >
          Run
        </button>
      )}

      {pending && (
        <ImportReview
          graph={pending.graph}
          filename={pending.filename}
          onCancel={() => setPending(null)}
          onLoad={() => {
            setGraph(pending.graph, { clean: true });
            setPending(null);
          }}
        />
      )}
    </header>
  );
}
