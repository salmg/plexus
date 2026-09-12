/**
 * The palette.
 *
 * Grouped by what a node does to a conversation rather than by what it is made
 * of, because that is the order you build a chain in: something that asks, then
 * what you want to do to the traffic, then something that answers.
 */

import { useMemo, useState } from "react";

import { CLASS_LABEL, CLASS_ORDER, NODE_TYPES } from "../catalogue";
import { Icon } from "../catalogue/icons";
import type { NodeType } from "../catalogue/types";
import { useGraph } from "../store/useGraph";
import { PALETTE_DRAG_TYPE } from "../canvas/Canvas";

export function Palette() {
  const addNode = useGraph((s) => s.addNode);
  const nodeCount = useGraph((s) => s.graph.nodes.length);
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matches = (t: NodeType) =>
      !needle ||
      t.title.toLowerCase().includes(needle) ||
      t.key.toLowerCase().includes(needle) ||
      t.summary.toLowerCase().includes(needle);

    return CLASS_ORDER.map((cls) => ({
      cls,
      label: CLASS_LABEL[cls],
      items: NODE_TYPES.filter((t) => t.nodeClass === cls && matches(t)),
    })).filter((g) => g.items.length > 0);
  }, [query]);

  // Clicking drops into a lane so a click-built chain does not stack up.
  const placeAt = (index: number) => ({
    x: 80 + index * 240,
    y: 140 + (index % 2) * 30,
  });

  return (
    <aside className="rail" aria-label="Node palette">
      <div className="panel-head">
        <span>Palette</span>
      </div>

      <div className="palette__search">
        <input
          className="input"
          type="search"
          value={query}
          placeholder="Filter nodes"
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Filter nodes"
        />
      </div>

      <div className="rail__scroll">
        {groups.map((group) => (
          <section key={group.cls} className="palette__group">
            <h2 className="panel-section__title">{group.label}</h2>
            {group.items.map((type) => (
              <button
                key={type.key}
                type="button"
                className={`palette__item palette__item--${type.nodeClass}`}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(PALETTE_DRAG_TYPE, type.key);
                  e.dataTransfer.effectAllowed = "copy";
                }}
                onClick={() => addNode(type.key, placeAt(nodeCount))}
                title={type.summary}
              >
                <Icon name={type.icon} className="palette__item-icon" />
                <span className="palette__item-body">
                  <span className="palette__item-title">{type.title}</span>
                  <span className="palette__item-summary">{type.summary}</span>
                </span>
              </button>
            ))}
          </section>
        ))}

        {groups.length === 0 && <p className="empty">No node matches “{query}”.</p>}
      </div>

      {/* The port colours carry the whole model, and nothing else on screen
          says what they mean. */}
      <footer className="legend">
        <span className="legend__row" title="A requester: it needs a card on the other end">
          <i className="legend__port legend__port--card" />
          card port — wants a card
        </span>
        <span className="legend__row" title="A responder: it behaves as a card would">
          <i className="legend__port legend__port--terminal" />
          terminal port — is a card
        </span>
      </footer>
    </aside>
  );
}
