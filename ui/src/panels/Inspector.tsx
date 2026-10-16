/**
 * The inspector.
 *
 * Every control here is generated from the catalogue's field specs — there is
 * no per-node-type form.  Adding a node type to the catalogue gives it a
 * working editor, which is the whole reason the catalogue is data.
 */

import { useMemo } from "react";

import { nodeType } from "../catalogue";
import { Icon } from "../catalogue/icons";
import { tn, useT } from "../i18n";
import {
  fieldHelp,
  fieldLabel,
  fieldPlaceholder,
  nodeCaveats,
  nodeSummary,
  nodeTitle,
  optionLabel,
} from "../i18n/catalogue";
import type { FieldSpec } from "../catalogue/types";
import { chainLatency } from "../graph/validate";
import type { Problem } from "../graph/validate";
import type { PlexusNode } from "../graph/types";
import { useGraph } from "../store/useGraph";
import { useRuntime } from "../store/useRuntime";
import { deviceNotes } from "./deviceNotes";

/** A node's name as shown: its own label, else the translated type title. */
function nodeLabelOf(node: PlexusNode): string {
  const type = nodeType(node.type);
  return node.label || (type ? nodeTitle(type) : node.type);
}

interface InspectorProps {
  problems: Problem[];
}

export function Inspector({ problems }: InspectorProps) {
  const t = useT();
  const selection = useGraph((s) => s.selection);
  const nodes = useGraph((s) => s.graph.nodes);

  const selected = useMemo(
    () => nodes.filter((n) => selection.includes(n.id)),
    [nodes, selection],
  );

  return (
    <aside className="rail rail--right" aria-label={t("inspector.title")}>
      {selected.length === 1 ? (
        <NodeInspector
          node={selected[0]}
          problems={problems.filter((p) => p.nodeId === selected[0].id)}
        />
      ) : selected.length > 1 ? (
        <MultiInspector nodes={selected} problems={problems} />
      ) : (
        <GraphInspector problems={problems} hasSelection={false} />
      )}
    </aside>
  );
}

/* ── Several selected ───────────────────────────────────────────────────── */

function MultiInspector({
  nodes,
  problems,
}: {
  nodes: PlexusNode[];
  problems: Problem[];
}) {
  const t = useT();
  const removeNodes = useGraph((s) => s.removeNodes);
  const toggleDisabled = useGraph((s) => s.toggleDisabled);
  const select = useGraph((s) => s.select);

  const ids = nodes.map((n) => n.id);
  const allDisabled = nodes.every((n) => n.disabled);
  const mine = problems.filter((p) => p.nodeId && ids.includes(p.nodeId));

  return (
    <>
      <div className="panel-head">
        <span>{tn("inspector.nodes", nodes.length)}</span>
        <button
          type="button"
          className="btn btn--ghost btn--icon"
          onClick={() => select(null)}
          aria-label={t("canvas.clear.title")}
        >
          ×
        </button>
      </div>

      <div className="rail__scroll">
        <div className="panel-section">
          <p className="inspector__summary">{t("inspector.multi.hint")}</p>
          <ul className="selected-list">
            {nodes.map((n) => (
              <li key={n.id}>
                <button type="button" onClick={() => select(n.id)}>
                  <span>{n.label || nodeLabelOf(n)}</span>
                  <code>{n.id}</code>
                </button>
              </li>
            ))}
          </ul>
        </div>

        {mine.length > 0 && <ProblemList problems={mine} onPick={select} />}

        <div className="panel-section inspector__actions">
          <button
            type="button"
            className="btn"
            onClick={() => ids.forEach((id) => toggleDisabled(id))}
          >
            {t(allDisabled ? "inspector.enableAll" : "inspector.disableAll")}
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={() => removeNodes(ids)}
          >
            {t("inspector.deleteCount", { count: nodes.length })}
          </button>
        </div>
      </div>
    </>
  );
}

/* ── Nothing selected: the graph itself ─────────────────────────────────── */

function GraphInspector({
  problems,
  hasSelection,
}: {
  problems: Problem[];
  hasSelection: boolean;
}) {
  const t = useT();
  const graph = useGraph((s) => s.graph);
  const setName = useGraph((s) => s.setName);
  const select = useGraph((s) => s.select);

  const roots = graph.nodes.filter((n) =>
    nodeType(n.type)?.ports.every((p) => p.role === "requester"),
  );

  return (
    <>
      <div className="panel-head">
        <span>{t("inspector.graph")}</span>
        <span className="badge">{tn("inspector.nodes", graph.nodes.length)}</span>
      </div>

      <div className="rail__scroll">
        <div className="panel-section">
          <label className="field">
            <span className="field__label">{t("inspector.name")}</span>
            <input
              className="input"
              value={graph.name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          {roots.length > 0 && (
            <dl className="kv">
              {roots.map((r) => (
                <div key={r.id} style={{ display: "contents" }}>
                  <dt>{r.label || nodeLabelOf(r)}</dt>
                  <dd>{t("inspector.roundTrip", { ms: chainLatency(graph, r.id) * 2 })}</dd>
                </div>
              ))}
            </dl>
          )}

          {!hasSelection && (
            <p className="field__help" style={{ marginTop: 10 }}>
              {t("inspector.hint")}
            </p>
          )}
        </div>

        <ProblemList problems={problems} onPick={select} />
      </div>
    </>
  );
}

/* ── A node ─────────────────────────────────────────────────────────────── */

function NodeInspector({ node, problems }: { node: PlexusNode; problems: Problem[] }) {
  const t = useT();
  const type = nodeType(node.type);
  const renameNode = useGraph((s) => s.renameNode);
  const setNodeHost = useGraph((s) => s.setNodeHost);
  const hosts = useGraph((s) => s.graph.hosts);
  const toggleDisabled = useGraph((s) => s.toggleDisabled);
  const removeNode = useGraph((s) => s.removeNode);
  const select = useGraph((s) => s.select);
  const telemetry = useRuntime((s) => s.nodes[node.id]);

  if (!type) {
    return (
      <div className="panel-section">
        <p className="field__error">
          {t("inspector.unknownType", { type: node.type })}
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="panel-head">
        <span className="panel-head__titled">
          <Icon name={type.icon} />
          {nodeTitle(type)}
        </span>
        <button
          type="button"
          className="btn btn--ghost btn--icon"
          onClick={() => select(null)}
          aria-label={t("inspector.close")}
        >
          ×
        </button>
      </div>

      <div className="rail__scroll">
        <div className="panel-section">
          <p className="inspector__summary">{nodeSummary(type)}</p>
          <dl className="kv">
            <dt>{t("inspector.id")}</dt>
            <dd>{node.id}</dd>
            <dt>{t("inspector.cost")}</dt>
            <dd>{t("inspector.cost.value", { ms: type.latencyMs })}</dd>
            {telemetry && telemetry.apdus > 0 && (
              <>
                <dt>{t("inspector.seen")}</dt>
                <dd>
                  {telemetry.meanMs === null
                    ? t("inspector.seen.value", {
                        count: telemetry.apdus.toLocaleString(),
                      })
                    : t("inspector.seen.withMean", {
                        count: telemetry.apdus.toLocaleString(),
                        ms: telemetry.meanMs.toFixed(1),
                      })}
                </dd>
              </>
            )}
          </dl>
        </div>

        <div className="panel-section">
          <label className="field">
            <span className="field__label">{t("inspector.label")}</span>
            <input
              className="input"
              value={node.label ?? ""}
              placeholder={nodeTitle(type)}
              onChange={(e) => renameNode(node.id, e.target.value)}
            />
          </label>

          {/*
            Which machine this node runs on.

            The model has carried `host` from the start, the shipped relay
            example uses it, and `_host_problems` refuses a cross-machine edge
            that carries card traffic with no pairing secret — but `setNodeHost`
            had no caller, so there was no way to set one. The field existed
            everywhere except where a person could reach it.

            Only shown when the graph declares hosts: on a single-machine rig
            it is a control with one empty option.
          */}
          {hosts.length > 0 && (
            <label className="field">
              <span className="field__label">{t("inspector.host")}</span>
              <select
                className="input"
                value={node.host ?? ""}
                onChange={(e) => setNodeHost(node.id, e.target.value)}
              >
                <option value="">{t("inspector.hostNone")}</option>
                {hosts.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.address ? `${h.name} (${h.address})` : h.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {type.fields.map((field) => (
            <Field key={field.key} node={node} field={field} />
          ))}
        </div>

        {type.caveats && type.caveats.length > 0 && (
          <div className="panel-section">
            <h3 className="panel-section__title">{t("inspector.caveats")}</h3>
            <ul className="caveats">
              {nodeCaveats(type).map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          </div>
        )}

        {problems.length > 0 && <ProblemList problems={problems} onPick={select} />}

        <div className="panel-section inspector__actions">
          <button type="button" className="btn" onClick={() => toggleDisabled(node.id)}>
            {t(node.disabled ? "inspector.enable" : "inspector.disable")}
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={() => removeNode(node.id)}
          >
            {t("inspector.delete")}
          </button>
        </div>
      </div>
    </>
  );
}

/* ── Generated controls ─────────────────────────────────────────────────── */

/** A field's catalogue default. Not every kind has one — a device has none. */
function defaultOf(field: FieldSpec): unknown {
  return "default" in field ? field.default : undefined;
}

/**
 * A node's value for a config key, falling back to the catalogue default.
 *
 * The runtime reads config this way — `config.get(key, default)` — so reading
 * the raw object here made the two disagree about a graph that simply had not
 * saved the key.
 */
function configOf(node: PlexusNode, key: string): unknown {
  const spec = nodeType(node.type)?.fields.find((f) => f.key === key);
  return node.config[key] ?? (spec ? defaultOf(spec) : undefined);
}

function Field({ node, field }: { node: PlexusNode; field: FieldSpec }) {
  const t = useT();
  const updateConfig = useGraph((s) => s.updateConfig);
  const devices = useGraph((s) => s.devices);
  const devicesProblems = useGraph((s) => s.devicesProblems);
  const refreshDevices = useRuntime((s) => s.refreshDevices);
  const unavailable = useRuntime((s) => s.unavailable);

  // The catalogue's default when the key is absent, because that is what the
  // runtime does: `config.get("logMutations", True)`. Reading the raw config
  // instead meant a graph saved without the key showed the box unchecked while
  // the run treated it as on — and `showWhen` hid fields whose controlling
  // value was only ever a default.
  const value = node.config[field.key] ?? defaultOf(field);

  if (field.showWhen && configOf(node, field.showWhen.key) !== field.showWhen.equals) {
    return null;
  }

  const set = (v: unknown) => updateConfig(node.id, field.key, v);

  if (field.kind === "bool") {
    return (
      <label className="checkbox">
        <input type="checkbox" checked={value === true} onChange={(e) => set(e.target.checked)} />
        <span>{fieldLabel(node.type, field)}</span>
      </label>
    );
  }

  const helpText = fieldHelp(node.type, field);
  const help = helpText && <p className="field__help">{helpText}</p>;

  if (field.kind === "device") {
    const candidates = devices.filter((d) => d.kind === field.deviceKind);
    const { empty, problem } = deviceNotes(
      candidates,
      devicesProblems[field.deviceKind],
      unavailable[node.type],
    );
    return (
      <div className="field">
        <label>
          <span className="field__label">{fieldLabel(node.type, field)}</span>
          <select
            className="select"
            value={typeof value === "string" ? value : ""}
            onChange={(e) => set(e.target.value)}
          >
            <option value="">{t("field.chooseDevice")}</option>
            {candidates.map((d) => (
              <option key={d.id} value={d.id}>
                {d.available ? d.name : t("field.notPresent", { name: d.name })}
              </option>
            ))}
          </select>
        </label>
        {empty && (
          <p className="field__help">
            {empty.key === "field.notBuilt"
              ? t(empty.key, { kind: field.deviceKind, needs: empty.needs })
              : t(empty.key, { kind: field.deviceKind })}
          </p>
        )}
        {problem && <p className="field__help field__help--problem">{problem}</p>}
        <button type="button" className="btn btn--ghost field__refresh" onClick={refreshDevices}>
          {t("field.lookAgain")}
        </button>
        {help}
      </div>
    );
  }

  if (field.kind === "select") {
    return (
      <label className="field">
        <span className="field__label">{fieldLabel(node.type, field)}</span>
        <select
          className="select"
          value={typeof value === "string" ? value : (field.default ?? "")}
          onChange={(e) => set(e.target.value)}
        >
          {field.options.map((o) => (
            <option key={o.value} value={o.value}>
              {optionLabel(node.type, field.key, o)}
            </option>
          ))}
        </select>
        {help}
      </label>
    );
  }

  if (field.kind === "number") {
    return (
      <label className="field">
        <span className="field__label">{fieldLabel(node.type, field)}</span>
        <input
          className="input"
          type="number"
          value={typeof value === "number" ? value : (field.default ?? "")}
          min={field.min}
          max={field.max}
          step={field.step ?? 1}
          onChange={(e) => {
            const n = Number.parseInt(e.target.value, 10);
            set(Number.isFinite(n) ? n : undefined);
          }}
        />
        {help}
      </label>
    );
  }

  if (field.kind === "yaml") {
    return (
      <label className="field">
        <span className="field__label">{fieldLabel(node.type, field)}</span>
        <textarea
          className="textarea"
          rows={field.rows ?? 12}
          value={typeof value === "string" ? value : (field.default ?? "")}
          spellCheck={false}
          onChange={(e) => set(e.target.value)}
        />
        {help}
      </label>
    );
  }

  // text and hex
  const text = typeof value === "string" ? value : "";
  const badHex =
    field.kind === "hex" && text !== "" && !/^[0-9a-fA-F]*$/.test(text.replace(/\s/g, ""));

  return (
    <label className="field">
      <span className="field__label">{fieldLabel(node.type, field)}</span>
      <input
        className={`input${field.kind === "hex" ? " input--mono" : ""}`}
        value={text}
        placeholder={fieldPlaceholder(node.type, field)}
        aria-invalid={badHex || undefined}
        onChange={(e) =>
          set(field.kind === "hex" ? e.target.value.toUpperCase() : e.target.value)
        }
      />
      {badHex && <p className="field__error">{t("field.hexOnly")}</p>}
      {help}
    </label>
  );
}

function ProblemList({
  problems,
  onPick,
}: {
  problems: Problem[];
  onPick: (id: string | null) => void;
}) {
  const t = useT();
  if (problems.length === 0) {
    return (
      <div className="panel-section">
        <h3 className="panel-section__title">{t("inspector.checks")}</h3>
        <p className="field__help">{t("inspector.checks.none")}</p>
      </div>
    );
  }

  return (
    <div>
      <div className="panel-head">
        <span>{t("inspector.checks")}</span>
        <span className="badge">{problems.length}</span>
      </div>
      {problems.map((p, i) => (
        <button
          key={`${p.code}-${p.nodeId ?? p.edgeId ?? i}`}
          type="button"
          className={`problem problem--${p.level}`}
          onClick={() => p.nodeId && onPick(p.nodeId)}
        >
          <span className="problem__icon">{p.level === "error" ? "✕" : "!"}</span>
          <span>{p.message}</span>
        </button>
      ))}
    </div>
  );
}
