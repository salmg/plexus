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
import type { FieldSpec } from "../catalogue/types";
import { chainLatency } from "../graph/validate";
import type { Problem } from "../graph/validate";
import type { PlexusNode } from "../graph/types";
import { useGraph } from "../store/useGraph";
import { useRuntime } from "../store/useRuntime";

interface InspectorProps {
  problems: Problem[];
}

export function Inspector({ problems }: InspectorProps) {
  const selection = useGraph((s) => s.selection);
  const nodes = useGraph((s) => s.graph.nodes);

  const selected = useMemo(
    () => nodes.filter((n) => selection.includes(n.id)),
    [nodes, selection],
  );

  return (
    <aside className="rail rail--right" aria-label="Inspector">
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
  const removeNodes = useGraph((s) => s.removeNodes);
  const toggleDisabled = useGraph((s) => s.toggleDisabled);
  const select = useGraph((s) => s.select);

  const ids = nodes.map((n) => n.id);
  const allDisabled = nodes.every((n) => n.disabled);
  const mine = problems.filter((p) => p.nodeId && ids.includes(p.nodeId));

  return (
    <>
      <div className="panel-head">
        <span>{nodes.length} nodes</span>
        <button
          type="button"
          className="btn btn--ghost btn--icon"
          onClick={() => select(null)}
          aria-label="Clear the selection"
        >
          ×
        </button>
      </div>

      <div className="rail__scroll">
        <div className="panel-section">
          <p className="inspector__summary">
            Drag any one of them to move the group. Delete removes their wires too.
          </p>
          <ul className="selected-list">
            {nodes.map((n) => (
              <li key={n.id}>
                <button type="button" onClick={() => select(n.id)}>
                  <span>{n.label || nodeType(n.type)?.title || n.type}</span>
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
            {allDisabled ? "Enable all" : "Disable all"}
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={() => removeNodes(ids)}
          >
            Delete {nodes.length}
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
  const graph = useGraph((s) => s.graph);
  const setName = useGraph((s) => s.setName);
  const select = useGraph((s) => s.select);

  const roots = graph.nodes.filter((n) =>
    nodeType(n.type)?.ports.every((p) => p.role === "requester"),
  );

  return (
    <>
      <div className="panel-head">
        <span>Graph</span>
        <span className="badge">{graph.nodes.length} nodes</span>
      </div>

      <div className="rail__scroll">
        <div className="panel-section">
          <label className="field">
            <span className="field__label">Name</span>
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
                  <dt>{r.label || nodeType(r.type)?.title}</dt>
                  <dd>{chainLatency(graph, r.id) * 2} ms out and back</dd>
                </div>
              ))}
            </dl>
          )}

          {!hasSelection && (
            <p className="field__help" style={{ marginTop: 10 }}>
              Select a node to configure it. Estimates above are the sum of each
              node's declared cost, not a measurement.
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
  const type = nodeType(node.type);
  const renameNode = useGraph((s) => s.renameNode);
  const toggleDisabled = useGraph((s) => s.toggleDisabled);
  const removeNode = useGraph((s) => s.removeNode);
  const select = useGraph((s) => s.select);
  const telemetry = useRuntime((s) => s.nodes[node.id]);

  if (!type) {
    return (
      <div className="panel-section">
        <p className="field__error">
          This graph contains a node of type “{node.type}”, which this build does
          not know.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="panel-head">
        <span className="panel-head__titled">
          <Icon name={type.icon} />
          {type.title}
        </span>
        <button
          type="button"
          className="btn btn--ghost btn--icon"
          onClick={() => select(null)}
          aria-label="Close inspector"
        >
          ×
        </button>
      </div>

      <div className="rail__scroll">
        <div className="panel-section">
          <p className="inspector__summary">{type.summary}</p>
          <dl className="kv">
            <dt>id</dt>
            <dd>{node.id}</dd>
            <dt>cost</dt>
            <dd>{type.latencyMs} ms each way</dd>
            {telemetry && telemetry.apdus > 0 && (
              <>
                <dt>seen</dt>
                <dd>
                  {telemetry.apdus.toLocaleString()} apdu
                  {telemetry.meanMs !== null && `, ${telemetry.meanMs.toFixed(1)} ms mean`}
                </dd>
              </>
            )}
          </dl>
        </div>

        <div className="panel-section">
          <label className="field">
            <span className="field__label">Label</span>
            <input
              className="input"
              value={node.label ?? ""}
              placeholder={type.title}
              onChange={(e) => renameNode(node.id, e.target.value)}
            />
          </label>

          {type.fields.map((field) => (
            <Field key={field.key} node={node} field={field} />
          ))}
        </div>

        {type.caveats && type.caveats.length > 0 && (
          <div className="panel-section">
            <h3 className="panel-section__title">Worth knowing</h3>
            <ul className="caveats">
              {type.caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        )}

        {problems.length > 0 && <ProblemList problems={problems} onPick={select} />}

        <div className="panel-section inspector__actions">
          <button type="button" className="btn" onClick={() => toggleDisabled(node.id)}>
            {node.disabled ? "Enable" : "Disable"}
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={() => removeNode(node.id)}
          >
            Delete
          </button>
        </div>
      </div>
    </>
  );
}

/* ── Generated controls ─────────────────────────────────────────────────── */

function Field({ node, field }: { node: PlexusNode; field: FieldSpec }) {
  const updateConfig = useGraph((s) => s.updateConfig);
  const devices = useGraph((s) => s.devices);
  const value = node.config[field.key];

  if (field.showWhen && node.config[field.showWhen.key] !== field.showWhen.equals) {
    return null;
  }

  const set = (v: unknown) => updateConfig(node.id, field.key, v);

  if (field.kind === "bool") {
    return (
      <label className="checkbox">
        <input type="checkbox" checked={value === true} onChange={(e) => set(e.target.checked)} />
        <span>{field.label}</span>
      </label>
    );
  }

  const help = field.help && <p className="field__help">{field.help}</p>;

  if (field.kind === "device") {
    const candidates = devices.filter((d) => d.kind === field.deviceKind);
    return (
      <label className="field">
        <span className="field__label">{field.label}</span>
        <select
          className="select"
          value={typeof value === "string" ? value : ""}
          onChange={(e) => set(e.target.value)}
        >
          <option value="">Choose a device</option>
          {candidates.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
              {d.available ? "" : " — not present"}
            </option>
          ))}
        </select>
        {candidates.length === 0 && (
          <p className="field__help">
            No {field.deviceKind} device is visible to the runtime.
          </p>
        )}
        {help}
      </label>
    );
  }

  if (field.kind === "select") {
    return (
      <label className="field">
        <span className="field__label">{field.label}</span>
        <select
          className="select"
          value={typeof value === "string" ? value : (field.default ?? "")}
          onChange={(e) => set(e.target.value)}
        >
          {field.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
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
        <span className="field__label">{field.label}</span>
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
        <span className="field__label">{field.label}</span>
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
      <span className="field__label">{field.label}</span>
      <input
        className={`input${field.kind === "hex" ? " input--mono" : ""}`}
        value={text}
        placeholder={field.placeholder}
        aria-invalid={badHex || undefined}
        onChange={(e) =>
          set(field.kind === "hex" ? e.target.value.toUpperCase() : e.target.value)
        }
      />
      {badHex && <p className="field__error">Hex digits only.</p>}
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
  if (problems.length === 0) {
    return (
      <div className="panel-section">
        <h3 className="panel-section__title">Checks</h3>
        <p className="field__help">Nothing to fix. This graph is ready to arm.</p>
      </div>
    );
  }

  return (
    <div>
      <div className="panel-head">
        <span>Checks</span>
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
