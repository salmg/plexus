/**
 * What a device picker says under it, decided apart from the markup so it can
 * be tested without a DOM.
 *
 * "No pcsc device is visible to the runtime" used to be the whole answer. On a
 * machine whose reader `pcsc_scan` listed, it sent people to check the reader
 * when the missing piece was pyscard in the Python running Plexus. The runtime
 * knows which piece is missing and says so, per device kind; this decides
 * where it shows.
 */

import type { Device } from "../graph/types";

export interface DeviceNotes {
  /** Nothing to pick: the line under the picker. */
  empty: null | { key: "field.noDevices" } | { key: "field.notBuilt"; needs: string };
  /** The runtime's own reason this kind is not ready, in its words. */
  problem: string;
}

export function deviceNotes(
  candidates: Device[],
  /** The runtime's reason for this device kind, when it gave one. */
  problem: string | undefined,
  /** What this node type is waiting on, from the runtime's `implementations`. */
  needs: string | undefined,
): DeviceNotes {
  // Shown with candidates too: a SimTrace2 board can be listed while the
  // daemon it needs is not running, and that is the thing to say.
  const reason = problem ?? "";
  if (candidates.length > 0) return { empty: null, problem: reason };
  if (reason) return { empty: { key: "field.noDevices" }, problem: reason };

  // A kind the runtime does not look for at all, on a node it cannot bind:
  // the ACR122U nodes say what they need rather than "none visible", which
  // read as a device the runtime had failed to find.
  return {
    empty: needs ? { key: "field.notBuilt", needs } : { key: "field.noDevices" },
    problem: "",
  };
}
