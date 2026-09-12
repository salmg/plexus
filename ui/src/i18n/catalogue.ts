/**
 * Reading the catalogue in the current language.
 *
 * Every one of these falls back to the English already in the catalogue, so a
 * new language can be added a node at a time and an untranslated node reads in
 * English inside an otherwise translated interface. That is the honest outcome
 * — better than a blank label or a raw key.
 *
 * The key shapes are derived from the catalogue's own names rather than
 * written out, so adding a node type gives it predictable keys with no second
 * list to maintain:
 *
 *     node.<nodeKey>.title
 *     node.<nodeKey>.summary
 *     node.<nodeKey>.caveat.<index>
 *     field.<nodeKey>.<fieldKey>.label | .help | .placeholder
 *     option.<nodeKey>.<fieldKey>.<optionValue>
 *     port.<portId>
 */

import type { FieldSpec, NodeType, SelectOption } from "../catalogue/types";
import type { PortSpec } from "../graph/types";
import { catalogueText, t } from "./index";

export function nodeTitle(type: NodeType): string {
  return catalogueText(`node.${type.key}.title`, type.title);
}

export function nodeSummary(type: NodeType): string {
  return catalogueText(`node.${type.key}.summary`, type.summary);
}

export function nodeCaveats(type: NodeType): string[] {
  return (type.caveats ?? []).map((caveat, i) =>
    catalogueText(`node.${type.key}.caveat.${i}`, caveat),
  );
}

export function fieldLabel(nodeKey: string, field: FieldSpec): string {
  return catalogueText(`field.${nodeKey}.${field.key}.label`, field.label);
}

export function fieldHelp(nodeKey: string, field: FieldSpec): string | undefined {
  if (!field.help) return undefined;
  return catalogueText(`field.${nodeKey}.${field.key}.help`, field.help);
}

export function fieldPlaceholder(nodeKey: string, field: FieldSpec): string | undefined {
  const placeholder = "placeholder" in field ? field.placeholder : undefined;
  if (!placeholder) return undefined;
  return catalogueText(`field.${nodeKey}.${field.key}.placeholder`, placeholder);
}

export function optionLabel(
  nodeKey: string,
  fieldKey: string,
  option: SelectOption,
): string {
  return catalogueText(`option.${nodeKey}.${fieldKey}.${option.value}`, option.label);
}

/**
 * A port's label.
 *
 * These are short and appear on the node card itself, where they are the only
 * thing naming the model, so they are translated even though they double as
 * identifiers in the saved file. The file keeps the English id; only what is
 * drawn changes.
 */
export function portLabel(port: PortSpec): string {
  return catalogueText(`port.${port.id}`, port.label);
}

export function classLabel(nodeClass: string): string {
  return t(`palette.class.${nodeClass}`);
}
