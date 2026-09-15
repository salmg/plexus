/**
 * The top bar: what graph is open, what the runtime is doing, and the one
 * control that matters — arm or stop.
 *
 * Run is disabled while any check is an error.  That is deliberate: the checks
 * exist so a graph that would fail as a timeout or a hardware fault fails here
 * instead, with a sentence saying why.
 */

import { useEffect, useRef, useState } from "react";

import { FILE_EXTENSION, fromYaml, GraphParseError, toYaml } from "../graph/serialize";
import type { Problem } from "../graph/validate";
import { hasBlockingProblem } from "../graph/validate";
import { emptyGraph } from "../graph/types";
import type { PlexusGraph } from "../graph/types";
import { LOCALES, LOCALE_NAMES, tn, useLocale, useT } from "../i18n";
import { ImportReview } from "./ImportReview";
import { useGraph } from "../store/useGraph";
import { useLayout } from "../store/useLayout";
import { isRunning, useRuntime } from "../store/useRuntime";

interface TopBarProps {
  problems: Problem[];
}

export function TopBar({ problems }: TopBarProps) {
  const t = useT();
  const [locale, setLocale] = useLocale();

  const paletteOpen = useLayout((s) => s.palette);
  const inspectorOpen = useLayout((s) => s.inspector);
  const consoleOpen = useLayout((s) => s.console);
  const narrow = useLayout((s) => s.narrow);
  const toggle = useLayout((s) => s.toggle);
  const focus = useLayout((s) => s.focus);
  const toggleFocus = useLayout((s) => s.toggleFocus);

  // Below `narrow` there is not room for eight controls and Run, and Run is
  // not the one that gives way. The rest move into a menu behind one button.
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (event: MouseEvent) => {
      if (!menu.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  // Opening a panel from the menu closes the menu: on a phone it covers the
  // thing it just opened.
  const show = (panel: "palette" | "inspector" | "console") => {
    toggle(panel);
    setMenuOpen(false);
  };

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

  /**
   * Everything that is not the wordmark, the badges, or Run.
   *
   * One list, rendered inline on a wide bar and inside a sheet on a narrow
   * one — rather than two lists that drift, so a control added here reaches a
   * phone without anybody remembering to add it twice.
   */
  const controls = (
    <>
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

      <button
        type="button"
        className="btn"
        onClick={() => {
          fileInput.current?.click();
          setMenuOpen(false);
        }}
      >
        {t("topbar.open")}
      </button>
      <button
        type="button"
        className="btn"
        onClick={() => {
          save();
          setMenuOpen(false);
        }}
      >
        {t("topbar.save")}
      </button>
      <button
        type="button"
        className="btn"
        onClick={() => {
          setGraph(emptyGraph(), { clean: true });
          setMenuOpen(false);
        }}
      >
        {t("topbar.new")}
      </button>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={() => show("palette")}
        aria-pressed={paletteOpen}
        title={t("topbar.palette.title")}
      >
        {t("topbar.palette")}
      </button>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={() => show("console")}
        aria-pressed={consoleOpen}
        title={t("topbar.console.title")}
      >
        {t("topbar.console")}
      </button>

      <button
        type="button"
        className="btn btn--ghost"
        onClick={() => show("inspector")}
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
    </>
  );

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

      {narrow ? (
        <div className="topbar__menu" ref={menu}>
          <button
            type="button"
            className="btn btn--ghost btn--icon"
            onClick={() => setMenuOpen((v) => !v)}
            aria-expanded={menuOpen}
            aria-label={t("topbar.more")}
            title={t("topbar.more")}
          >
            ☰
          </button>
          {menuOpen && <div className="topbar__sheet">{controls}</div>}
        </div>
      ) : (
        controls
      )}

      <button
        type="button"
        className={`btn btn--ghost btn--icon${focus ? " is-on" : ""}`}
        onClick={toggleFocus}
        aria-pressed={focus}
        title={t(focus ? "topbar.focus.off" : "topbar.focus.on")}
        aria-label={t(focus ? "topbar.focus.off" : "topbar.focus.on")}
      >
        {focus ? "⤢" : "⤡"}
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
