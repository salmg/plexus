/**
 * What a graph will reach for, shown before it can be armed.
 *
 * A `.plexus.yaml` is a document people send each other, and it names hardware
 * to claim, addresses to dial and files to read and write. Opening one should
 * not be the same gesture as agreeing to all of that, so this sits between the
 * two: here is what it wants, load it or don't.
 *
 * The runtime does not rely on this. It confines every path to a workspace and
 * refuses graphs that fail its own checks, because a dialog in a browser is
 * not a security boundary — it is an informed-consent step for the person
 * reading it.
 */

import { useEffect, useRef } from "react";

import { nodeType } from "../catalogue";
import { Icon } from "../catalogue/icons";
import { describeIntent } from "../graph/serialize";
import type { PlexusGraph } from "../graph/types";

interface ImportReviewProps {
  graph: PlexusGraph;
  filename: string;
  onLoad: () => void;
  onCancel: () => void;
}

export function ImportReview({ graph, filename, onLoad, onCancel }: ImportReviewProps) {
  const intent = describeIntent(graph);
  const cancel = useRef<HTMLButtonElement | null>(null);

  // Focus lands on Cancel, not Load: the safe half of a decision someone has
  // not read yet. Escape does the same thing.
  useEffect(() => {
    cancel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const counts = new Map<string, number>();
  for (const node of graph.nodes) {
    counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
  }

  const nothing =
    intent.devices.length === 0 &&
    intent.addresses.length === 0 &&
    intent.files.length === 0;

  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Review this graph">
      <div className="sheet__panel">
        <header className="sheet__head">
          <span>Load “{graph.name}”?</span>
          <code>{filename}</code>
        </header>

        <div className="sheet__body">
          <section className="sheet__section">
            <h3 className="panel-section__title">{graph.nodes.length} nodes</h3>
            <ul className="sheet__nodes">
              {[...counts].map(([key, count]) => {
                const type = nodeType(key);
                return (
                  <li key={key}>
                    <Icon name={type?.icon} />
                    <span>{type?.title ?? key}</span>
                    {count > 1 && <em>×{count}</em>}
                  </li>
                );
              })}
            </ul>
          </section>

          {nothing ? (
            <section className="sheet__section">
              <p className="field__help">
                It binds no hardware, dials nothing and names no files.
              </p>
            </section>
          ) : (
            <section className="sheet__section">
              <h3 className="panel-section__title">If you arm it, it will</h3>
              <dl className="kv">
                {intent.devices.length > 0 && (
                  <>
                    <dt>claim</dt>
                    <dd>{intent.devices.join(", ")}</dd>
                  </>
                )}
                {intent.addresses.length > 0 && (
                  <>
                    <dt>dial</dt>
                    <dd>{intent.addresses.join(", ")}</dd>
                  </>
                )}
                {intent.files.length > 0 && (
                  <>
                    <dt>read or write</dt>
                    <dd>{intent.files.join(", ")}</dd>
                  </>
                )}
              </dl>
              <p className="field__help" style={{ marginTop: 8 }}>
                Loading it only draws it. Nothing is claimed or dialled until
                you press Run, and the runtime confines every path to its
                workspace.
              </p>
            </section>
          )}
        </div>

        <footer className="sheet__actions">
          <button ref={cancel} type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn btn--primary" onClick={onLoad}>
            Load
          </button>
        </footer>
      </div>
    </div>
  );
}
