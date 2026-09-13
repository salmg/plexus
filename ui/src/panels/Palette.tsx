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
import { createPortal } from "react-dom";

import { CLASS_ORDER, NODE_TYPES } from "../catalogue";
import { Icon } from "../catalogue/icons";
import type { NodeType } from "../catalogue/types";
import { useT } from "../i18n";
import { classLabel, nodeSummary, nodeTitle } from "../i18n/catalogue";
import { useGraph } from "../store/useGraph";
import { useRuntime } from "../store/useRuntime";
import { PALETTE_DRAG_TYPE } from "../canvas/Canvas";
import { usePlacement } from "../canvas/placement";
import { usePaletteDrag } from "./usePaletteDrag";

export function Palette() {
  const t = useT();
  const addNode = useGraph((s) => s.addNode);
  const nodeCount = useGraph((s) => s.graph.nodes.length);
  const place = usePlacement((s) => s.place);
  const { onPointerDown, consumeClick, ghost } = usePaletteDrag();
  // What the runtime on the other end cannot bind. Empty for the
  // simulator, which invents traffic for anything.
  const unavailable = useRuntime((s) => s.unavailable);
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
          <section
            key={group.cls}
            className={`palette__group palette__group--${group.cls}`}
          >
            <h2 className="panel-section__title">{group.label}</h2>
            {group.items.map((type) => {
              // Marked, not disabled. You can still draw a rig for hardware
              // you have not plugged in yet, or for a machine that is not
              // this one — the file is the document, and it outlives the
              // runtime that happens to be answering right now.
              const needs = unavailable[type.key];
              return (
                <button
                  key={type.key}
                  type="button"
                  className={`palette__item palette__item--${type.nodeClass}${
                    needs ? " palette__item--unavailable" : ""
                  }`}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData(PALETTE_DRAG_TYPE, type.key);
                    e.dataTransfer.effectAllowed = "copy";
                  }}
                  onPointerDown={(e) => onPointerDown(e, type.key)}
                  onClick={() => {
                    // A drag that ended on this item fires a click too; the
                    // node was already placed where it was dropped, so ignore it
                    // rather than adding a second at the centre.
                    if (consumeClick()) return;
                    if (place) place(type.key, "center");
                    else addNode(type.key, placeAt(nodeCount));
                  }}
                  title={
                    needs
                      ? t("palette.unavailable", { needs })
                      : nodeSummary(type)
                  }
                >
                  <Icon name={type.icon} className="palette__item-icon" />
                  <span className="palette__item-body">
                    <span className="palette__item-title">
                      {nodeTitle(type)}
                      {needs && (
                        <i
                          className="palette__item-mark"
                          aria-label={t("palette.unavailable", { needs })}
                        />
                      )}
                    </span>
                    <span className="palette__item-summary">
                      {needs ? t("palette.needs", { needs }) : nodeSummary(type)}
                    </span>
                  </span>
                </button>
              );
            })}
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

      {/* The thing under the finger during a touch drag. In a portal on the
          body so the rail's own scroll and overflow cannot clip it, and
          pointer-transparent so it never eats the release. */}
      {ghost &&
        createPortal(
          (() => {
            const gt = NODE_TYPES.find((n) => n.key === ghost.key);
            if (!gt) return null;
            return (
              <div
                className={`palette__ghost${ghost.valid ? " palette__ghost--valid" : ""}`}
                style={{ left: ghost.x, top: ghost.y }}
                aria-hidden="true"
              >
                <Icon name={gt.icon} className="palette__item-icon" />
                <span className="palette__ghost-title">{nodeTitle(gt)}</span>
              </div>
            );
          })(),
          document.body,
        )}
    </aside>
  );
}
