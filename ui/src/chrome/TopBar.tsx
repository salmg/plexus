/**
 * The top bar: what graph is open, what the runtime is doing, and the one
 * control that matters — arm or stop.
 *
 * Run is disabled while any check is an error.  That is deliberate: the checks
 * exist so a graph that would fail as a timeout or a hardware fault fails here
 * instead, with a sentence saying why.
 */

import { useRef, useState } from "react";

import { FILE_EXTENSION, fromYaml, GraphParseError, toYaml } from "../graph/serialize";
import type { Problem } from "../graph/validate";
import { hasBlockingProblem } from "../graph/validate";
import { emptyGraph } from "../graph/types";
import type { PlexusGraph } from "../graph/types";
import { LOCALES, LOCALE_NAMES, tn, useLocale, useT } from "../i18n";
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
  const t = useT();
  const [locale, setLocale] = useLocale();

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
  const detail = useRuntime((s) => s.detail);
  const runtime = useRuntime((s) => s.runtime);
  const run = useRuntime((s) => s.run);
  const stop = useRuntime((s) => s.stop);

  const fileInput = useRef<HTMLInputElement | null>(null);

  // Parsed and shown before it is loaded. A graph file is something people
  // send each other, and it names hardware, addresses and paths — opening one
  // should not be the same gesture as agreeing to all of that.
  const [pending, setPending] = useState<{ graph: PlexusGraph; filename: string } | null>(
    null,
  );

  const running = isRunning(state);
  const blocked = hasBlockingProblem(problems);
  const errors = problems.filter((p) => p.level === "error").length;
  const warnings = problems.length - errors;

  const save = () => {
    const blob = new Blob([toYaml(graph)], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const stem = graph.name.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase();
    a.download = `${stem}${FILE_EXTENSION}`;
    a.click();
    URL.revokeObjectURL(url);
    markClean();
  };

  const open = async (file: File) => {
    try {
      setPending({ graph: fromYaml(await file.text()), filename: file.name });
    } catch (err) {
      // Translated. A refused file is the one error a person meets while
      // simply opening something somebody sent them, and it was the only
      // untranslated string the interface had.
      const message =
        err instanceof GraphParseError
          ? t(err.key, err.vars)
          : t("topbar.unreadable", {
              message: err instanceof Error ? err.message : String(err),
            });
      window.alert(message);
    }
  };

  return (
    <header className="topbar">
      {/* The wordmark is the credit. A product's own name is the thing people
          click to find out who made it, so it carries the link rather than a
          separate byline competing with Run for the eye. */}
      <a
        className="topbar__brand"
        href="https://academy.salmg.net"
        target="_blank"
        rel="noreferrer noopener"
        title={t("app.by")}
      >
        Plexus
        <small>{t("app.tagline")}</small>
      </a>

      <span className="topbar__name" title={graph.name}>
        {graph.name}
        {dirty && <span className="topbar__dirty"> •</span>}
      </span>

      <div className="topbar__spacer" />

      {(errors > 0 || warnings > 0) && (
        <span className={`badge ${errors > 0 ? "badge--danger" : "badge--warn"}`}>
          {errors > 0 && tn("count.errors", errors)}
          {errors > 0 && warnings > 0 && ", "}
          {warnings > 0 && tn("count.warnings", warnings)}
        </span>
      )}

      {/*
        The runtime, and why it is unhappy when it is.

        `detail` was written on every state change and read by nothing, so the
        reason a run faulted — the one sentence that says what to fix — reached
        the store and stopped there. It rides the badge the state already has.
      */}
      <span
        className={`badge${state === "faulted" ? " badge--danger" : ""}`}
        title={detail ?? t("topbar.runtime.title")}
      >
        {runtime.label}
        {state === "faulted" && detail && (
          <span className="badge__detail"> — {detail}</span>
        )}
      </span>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={undo}
        disabled={!canUndo}
        title={t("topbar.undo")}
      >
        {t("topbar.undo")}
      </button>
      <button
        type="button"
        className="btn btn--ghost"
        onClick={redo}
        disabled={!canRedo}
        title={t("topbar.redo")}
      >
        {t("topbar.redo")}
      </button>

      <button type="button" className="btn" onClick={() => fileInput.current?.click()}>
        {t("topbar.open")}
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
        {t("topbar.save")}
      </button>
      <button
        type="button"
        className="btn"
        onClick={() => setGraph(emptyGraph(), { clean: true })}
      >
        {t("topbar.new")}
      </button>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={onToggleConsole}
        aria-pressed={consoleOpen}
        title={t("topbar.console.title")}
      >
        {t("topbar.console")}
      </button>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={onToggleInspector}
        aria-pressed={inspectorOpen}
        title={t("topbar.inspector.title")}
      >
        {t("topbar.inspector")}
      </button>

      {/* A select rather than a flag: a flag is a country, and a language is
          not. Two entries today, and it stays right at more. */}
      <select
        className="select topbar__locale"
        value={locale}
        onChange={(e) => setLocale(e.target.value as typeof locale)}
        aria-label={t("topbar.language")}
        title={t("topbar.language")}
      >
        {LOCALES.map((code) => (
          <option key={code} value={code}>
            {LOCALE_NAMES[code]}
          </option>
        ))}
      </select>

      <button
        type="button"
        className="btn btn--ghost btn--icon"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        aria-label={t(theme === "dark" ? "topbar.theme.toLight" : "topbar.theme.toDark")}
        title={t(theme === "dark" ? "topbar.theme.toLight" : "topbar.theme.toDark")}
      >
        {theme === "dark" ? "☾" : "☀"}
      </button>

      {running ? (
        <button type="button" className="btn btn--danger" onClick={() => void stop()}>
          {t("topbar.stop")}
        </button>
      ) : (
        <button
          type="button"
          className="btn btn--primary"
          disabled={blocked}
          title={t(blocked ? "topbar.run.blocked" : "topbar.run.ready")}
          onClick={() => void run(graph)}
        >
          {t("topbar.run")}
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
