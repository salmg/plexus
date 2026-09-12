/**
 * Emit plexus/schema/catalogue.json from the TypeScript catalogue.
 *
 * The catalogue describes ports, config fields and latency costs, and both ends
 * need it: the editor to draw and validate, the runtime to build and check the
 * same graph.  Writing it twice guarantees it drifts, so it is authored once in
 * TypeScript and exported here.  Run `npm run export:catalogue` after changing
 * the catalogue; CI fails if the checked-in file is stale.
 *
 * Node strips the TypeScript types on import, so this runs with plain `node` —
 * but only from **22.18** (or 23.6), where that stopped needing a flag. The
 * rest of the editor's tooling is happy on 20.19+; this one script is not,
 * and `docs/install.md` says so rather than leaving it to be discovered.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { NODE_TYPES } from "../src/catalogue/index.ts";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../../plexus/schema/catalogue.json");

const document = {
  $comment:
    "Generated from ui/src/catalogue/index.ts by npm run export:catalogue. Do not edit.",
  version: 1,
  nodeTypes: NODE_TYPES,
};

mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, `${JSON.stringify(document, null, 2)}\n`, "utf8");

console.log(`wrote ${out} — ${NODE_TYPES.length} node types`);
