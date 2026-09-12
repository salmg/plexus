import { useEffect, useMemo, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";

import { Canvas } from "./canvas/Canvas";
import { TopBar } from "./chrome/TopBar";
import { validate } from "./graph/validate";
import { useT } from "./i18n";
import { Console } from "./panels/Console";
import { Inspector } from "./panels/Inspector";
import { Palette } from "./panels/Palette";
import { useGraph } from "./store/useGraph";
import { useRuntime } from "./store/useRuntime";
import { CONTACTLESS_RELAY } from "./examples";

/**
 * How far an arrow key moves a selection.
 *
 * Eighteen, because that is the Background grid the canvas draws, so a nudged
 * node lands on it. Shift gives one pixel, for when it has to be exact.
 */
const GRID_STEP = 18;

const CONSOLE_KEY = "plexus.console-open";

/*
 * Storage that is allowed to be unavailable.
 *
 * A private window, a browser with site data blocked, or a page opened from
 * `file://` all make `localStorage` *throw* rather than return nothing — and
 * this one is read in a `useState` initialiser, so the throw takes the whole
 * app down before it paints. Remembering which panels were open is not worth
 * that.
 */
function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Not being able to remember a panel is not worth telling anyone about.
  }
}

/**
 * Below this the three columns leave the canvas too little to be worth
 * drawing on, so the inspector folds away.  It stays reachable from the top
 * bar — hiding it is a default, not a decision taken for the user.
 */
const INSPECTOR_MIN_WIDTH = 1100;

export default function App() {
  const theme = useGraph((s) => s.theme);
  const graph = useGraph((s) => s.graph);
  const devices = useGraph((s) => s.devices);
  const setGraph = useGraph((s) => s.setGraph);
  const init = useRuntime((s) => s.init);

  const [consoleOpen, setConsoleOpen] = useState(
    () => remembered(CONSOLE_KEY) !== "0",
  );
  const [inspectorOpen, setInspectorOpen] = useState(
    () => window.innerWidth >= INSPECTOR_MIN_WIDTH,
  );

  // Follow the window only while the user has not taken over: once they toggle
  // the inspector themselves, resizing stops second-guessing them.
  const [inspectorPinned, setInspectorPinned] = useState(false);

  useEffect(() => {
    if (inspectorPinned) return;
    const onResize = () =>
      setInspectorOpen(window.innerWidth >= INSPECTOR_MIN_WIDTH);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [inspectorPinned]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    remember(CONSOLE_KEY, consoleOpen ? "1" : "0");
  }, [consoleOpen]);

  useEffect(() => {
    void init();
    // An empty canvas teaches nothing, so open on a rig that shows what a
    // chain looks like.  Anything the user does replaces it.
    if (useGraph.getState().graph.nodes.length === 0) {
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
  // `t` is not called here, but its identity changes with the locale and the
  // validator writes language. Dropping it leaves the checks in the old one.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  const problems = useMemo(() => validate(graph, devices), [graph, devices, t]);

  return (
    <div className="shell">
      <TopBar
        problems={problems}
        consoleOpen={consoleOpen}
        onToggleConsole={() => setConsoleOpen((v) => !v)}
        inspectorOpen={inspectorOpen}
        onToggleInspector={() => {
          setInspectorPinned(true);
          setInspectorOpen((v) => !v);
        }}
      />

      <div
        className={`shell__body${inspectorOpen ? "" : " shell__body--no-inspector"}`}
      >
        <Palette />

        <div
          className="shell__centre"
        >
          <ReactFlowProvider>
            <Canvas problems={problems} />
          </ReactFlowProvider>
          {consoleOpen && <Console />}
        </div>

        {inspectorOpen && <Inspector problems={problems} />}
      </div>
    </div>
  );
}
