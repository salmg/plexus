/**
 * The palette.
 *
 * Grouped by what a node does to a conversation rather than by what it is made
 * of, because that is the order you build a chain in: something that asks, then
 * what you want to do to the traffic, then something that answers.
 *
 * The filter matches the translated title and summary as well as the catalogue
 * key, so searching in the language on screen works and the English key still
 * finds a node whose title you only know in English.
 */

import { useMemo, useState } from "react";

import { CLASS_ORDER, NODE_TYPES } from "../catalogue";
import { Icon } from "../catalogue/icons";
import type { NodeType } from "../catalogue/types";
import { useT } from "../i18n";
import { classLabel, nodeSummary, nodeTitle } from "../i18n/catalogue";
import { useGraph } from "../store/useGraph";
import { PALETTE_DRAG_TYPE } from "../canvas/Canvas";

export function Palette() {
  const t = useT();
  const addNode = useGraph((s) => s.addNode);
  const nodeCount = useGraph((s) => s.graph.nodes.length);
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matches = (type: NodeType) =>
      !needle ||
      nodeTitle(type).toLowerCase().includes(needle) ||
      nodeSummary(type).toLowerCase().includes(needle) ||
      type.title.toLowerCase().includes(needle) ||
      type.key.toLowerCase().includes(needle);

    return CLASS_ORDER.map((cls) => ({
      cls,
      label: classLabel(cls),
      items: NODE_TYPES.filter((type) => type.nodeClass === cls && matches(type)),
    })).filter((group) => group.items.length > 0);
    // `t` changes identity with the locale, which is what re-filters and
    // re-labels these when the language does.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [query, t]);

  // Clicking drops into a lane so a click-built chain does not stack up.
  const placeAt = (index: number) => ({
    x: 80 + index * 240,
    y: 140 + (index % 2) * 30,
  });

  return (
    <aside className="rail" aria-label={t("palette.title")}>
      <div className="panel-head">
        <span>{t("palette.title")}</span>
      </div>

      <div className="palette__search">
        <input
          className="input"
          type="search"
          value={query}
          placeholder={t("palette.filter")}
          onChange={(e) => setQuery(e.target.value)}
          aria-label={t("palette.filter")}
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
                title={nodeSummary(type)}
              >
                <Icon name={type.icon} className="palette__item-icon" />
                <span className="palette__item-body">
                  <span className="palette__item-title">{nodeTitle(type)}</span>
                  <span className="palette__item-summary">{nodeSummary(type)}</span>
                </span>
              </button>
            ))}
          </section>
        ))}

        {groups.length === 0 && (
          <p className="empty">{t("palette.empty", { query })}</p>
        )}
      </div>

      {/* The port colours carry the whole model, and nothing else on screen
          says what they mean. */}
      <footer className="legend">
        <span className="legend__row" title={t("legend.cardPort.title")}>
          <i className="legend__port legend__port--card" />
          {t("legend.cardPort")}
        </span>
        <span className="legend__row" title={t("legend.terminalPort.title")}>
          <i className="legend__port legend__port--terminal" />
          {t("legend.terminalPort")}
        </span>
      </footer>
    </aside>
  );
}
