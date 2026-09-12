/**
 * Graphs the editor opens with, so a first run has something to read.
 *
 * These are the topologies atrium already supports, expressed in the node
 * model: the same palette, four different rigs.
 */

import type { PlexusGraph } from "./graph/types";

export const CONTACTLESS_RELAY: PlexusGraph = {
  version: 1,
  name: "contactless relay to a phone",
  hosts: [
    { id: "workbench", name: "workbench", note: "the machine running Plexus" },
    { id: "phone", name: "android phone", address: "192.168.1.24" },
  ],
  nodes: [
    {
      id: "acr122u-target-1",
      type: "acr122u.target",
      host: "workbench",
      position: { x: 40, y: 160 },
      config: { device: "acr122u:001", splitResponses: false },
    },
    {
      id: "trace-1",
      type: "trace",
      host: "workbench",
      position: { x: 300, y: 160 },
      config: { detail: "tlv", path: "logs/trace.jsonl" },
    },
    {
      id: "mutation-1",
      type: "mutation",
      label: "CVM bypass",
      host: "workbench",
      position: { x: 560, y: 160 },
      config: {
        logMutations: true,
        playbook: `# COMBO-A — CVM bypass via contactless amount threshold
enabled: true
log_mutations: true

pdol_mutations:
  - tag: "9F02"
    value: "000000000001"
    enabled: true
    comment: "Amount: 1 cent — triggers No-CVM amount threshold rule"
  - tag: "5F2A"
    value: "0978"
    enabled: true
    comment: "Currency: EUR — synchronise with amount"

response_mutations:
  - tag: "8E"
    mode: replace
    value: "00000000000000001F03"
    enabled: true
    comment: "CVM List: No-CVM first"

afl_mutations: []
dol_mutations: []
`,
      },
    },
    {
      id: "nfcgate-1",
      type: "nfcgate",
      host: "phone",
      position: { x: 820, y: 160 },
      config: { host: "192.168.1.24", port: 5566, session: 1, cafile: "" },
    },
  ],
  edges: [
    {
      id: "acr122u-target-1:card->trace-1:terminal",
      from: { node: "acr122u-target-1", port: "card" },
      to: { node: "trace-1", port: "terminal" },
    },
    {
      id: "trace-1:card->mutation-1:terminal",
      from: { node: "trace-1", port: "card" },
      to: { node: "mutation-1", port: "terminal" },
    },
    {
      id: "mutation-1:card->nfcgate-1:terminal",
      from: { node: "mutation-1", port: "card" },
      to: { node: "nfcgate-1", port: "terminal" },
    },
  ],
};
