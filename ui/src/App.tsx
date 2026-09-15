import { useEffect, useMemo } from "react";
import { ReactFlowProvider } from "@xyflow/react";

import { Canvas } from "./canvas/Canvas";
import { TopBar } from "./chrome/TopBar";
import { validate } from "./graph/validate";
import { useLocale, useT } from "./i18n";
import { Console } from "./panels/Console";
import { Inspector } from "./panels/Inspector";
import { Palette } from "./panels/Palette";
import { remember, THEME_KEY } from "./store/remember";
import { savedGraph, useGraph } from "./store/useGraph";
import { useLayout, useMeasureLayout } from "./store/useLayout";
import { useRuntime } from "./store/useRuntime";
import { CONTACTLESS_RELAY } from "./examples";

/**
 * How far an arrow key moves a selection.
 *
 * Eighteen, because that is the Background grid the canvas draws, so a nudged
 * node lands on it. Shift gives one pixel, for when it has to be exact.
 */
const GRID_STEP = 18;

export default function App() {
  const theme = useGraph((s) => s.theme);
  const [locale] = useLocale();
  const graph = useGraph((s) => s.graph);
  const devices = useGraph((s) => s.devices);
  const setGraph = useGraph((s) => s.setGraph);
  const init = useRuntime((s) => s.init);

  const paletteOpen = useLayout((s) => s.palette);
  const inspectorOpen = useLayout((s) => s.inspector);
  const consoleOpen = useLayout((s) => s.console);
  const compact = useLayout((s) => s.compact);
  const inspectorOverlay = useLayout((s) => s.inspectorOverlay);
  const closeDrawers = useLayout((s) => s.closeDrawers);
  useMeasureLayout();

  // Escape closes a drawer, because a panel covering the canvas needs a way out
  // that is not hunting for the button that opened it.
  useEffect(() => {
    if (!inspectorOverlay) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeDrawers();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [inspectorOverlay, closeDrawers]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    remember(THEME_KEY, theme);
  }, [theme]);

  // The page's language, which index.html hardcodes as English. A screen
  // reader announces Spanish text with an English voice otherwise — the one
  // part of translating an interface that is not visible to whoever did it.
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  useEffect(() => {
    void init();
    // Reopen the graph from last time if there is one. A first-ever visit has
    // none, and an empty canvas teaches nothing, so it opens on a rig that
    // shows what a chain looks like. Either way, anything the user does
    // replaces it and is itself remembered — see `savedGraph` in the store.
    const restored = savedGraph();
    if (restored) {
      setGraph(restored, { clean: true });
    } else if (useGraph.getState().graph.nodes.length === 0) {
      setGraph(CONTACTLESS_RELAY, { clean: true });
    }
  }, [init, setGraph]);

  // Select-all and nudge, which the store has always been able to do and
  // nothing ever called. Arrow keys move a selection the way dragging does but
  // by a known amount, which is the only way to line two nodes up exactly; and
  // Ctrl/Cmd+A is how you select a whole canvas everywhere else.
  //
  // Ignored while the caret is in a field: the console, the inspector's text
  // boxes and the playbook editor all want these keys for themselves.
  useEffect(() => {
    const typing = (target: EventTarget | null): boolean => {
      // `instanceof Element`, not a cast: a keydown can be targeted at the
      // window or the document, and `closest` is not a method on either.
      if (!(target instanceof Element)) return false;
      const tag = target.tagName;
      return (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        (target as HTMLElement).isContentEditable ||
        target.closest(".console") !== null
      );
    };
    const onKey = (event: KeyboardEvent) => {
      if (typing(event.target)) return;

      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
        event.preventDefault();
        useGraph.getState().selectAll();
        return;
      }

      const step = event.shiftKey ? 1 : GRID_STEP;
      const move: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
      };
      const delta = move[event.key];
      if (!delta) return;

      const { selection, nudge } = useGraph.getState();
      if (selection.length === 0) return;
      event.preventDefault();
      nudge(selection, delta[0], delta[1]);
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // `t` changes identity when the locale does, which is what re-runs the
  // validator: it writes sentences people read, so its output is language.
  const t = useT();
  const unavailable = useRuntime((s) => s.unavailable);
  const problems = useMemo(
    () => validate(graph, devices, unavailable),
    // `t` is not called here, but its identity changes with the locale and the
    // validator writes language. Dropping it leaves the checks in the old one.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
    [graph, devices, unavailable, t],
  );

  // A panel is a column only where there is room for one; everywhere else it
  // is a drawer over the canvas, and the canvas keeps the full width. That
  // matters for more than looks: React Flow lays out against its container, so
  // a canvas that narrows every time a panel opens re-fits the graph under
  // whoever is reading it.
  const paletteDocked = paletteOpen && !compact;
  const inspectorDocked = inspectorOpen && !inspectorOverlay;

  const columns =
    (paletteDocked ? "" : " shell__body--no-palette") +
    (inspectorDocked ? "" : " shell__body--no-inspector") +
    (compact ? " shell__body--compact" : "") +
    (inspectorOverlay ? " shell__body--inspector-over" : "");

  // Something is covering the canvas, so there has to be a way back to it.
  const covered =
    (compact && (paletteOpen || consoleOpen)) || (inspectorOverlay && inspectorOpen);

  return (
    <div className="shell">
      <TopBar problems={problems} />

      <div className={`shell__body${columns}`}>
        {paletteOpen && <Palette />}

        <div className="shell__centre">
          <ReactFlowProvider>
            <Canvas problems={problems} />
          </ReactFlowProvider>
          {consoleOpen && <Console />}
        </div>

        {inspectorOpen && <Inspector problems={problems} />}

        {/* One backdrop for whichever drawer is open — they open one at a
            time. A tap anywhere on the canvas puts the work back. */}
        {covered && (
          <button
            type="button"
            className="scrim"
            onClick={closeDrawers}
            aria-label={t("layout.close")}
          />
        )}
      </div>
    </div>
  );
}
