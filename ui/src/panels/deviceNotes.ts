/**
 * What an empty device picker says, decided apart from the markup so it can be
 * tested without a DOM.
 *
 * "No pcsc device is visible to the runtime" used to be the whole answer. On a
 * machine whose reader `pcsc_scan` listed, it sent people to check the reader
 * when the missing piece was pyscard in the Python running Plexus. The runtime
 * knows which piece is missing and now says so; this decides where it shows.
 */

import type { Device, DeviceKind } from "../graph/types";

export interface DeviceNotes {
  /** Nothing to pick: the line under the picker. */
  empty: null | { key: "field.noDevices" } | { key: "field.notBuilt"; needs: string };
  /** The runtime's own reason there is no reader, in its words. */
  problem: string;
}

export function deviceNotes(
  kind: DeviceKind,
  candidates: Device[],
  devicesProblem: string,
  /** What this node type is waiting on, from the runtime's `implementations`. */
  needs: string | undefined,
): DeviceNotes {
  if (candidates.length > 0) return { empty: null, problem: "" };

  // PC/SC is the one kind discovered, so the runtime's reason is about it.
  if (kind === "pcsc") {
    return { empty: { key: "field.noDevices" }, problem: devicesProblem };
  }

  // The other kinds have pickers before they have discovery: SimTrace2 and
  // the ACR122U nodes say what they need rather than "none visible", which
  // read as a reader the runtime had failed to find.
  return {
    empty: needs ? { key: "field.notBuilt", needs } : { key: "field.noDevices" },
    problem: "",
  };
}
