/**
 * The two validators agree, and something checks that they do.
 *
 * The mirror of `tests/test_parity.py`. Same fixture graphs, same contract
 * file — but loaded with the editor's own `fromYaml` and judged by the editor's
 * own `validate`, so what is compared is the whole path a `.plexus.yaml` takes
 * on this side, not just the rule table.
 *
 * `tests/fixtures/validator/expected.json` is the contract, not a snapshot
 * either side generates. Add a rule to one language and forget the other, and
 * one of these two suites goes red.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { fromYaml } from "../src/graph/serialize";
import { validate } from "../src/graph/validate";

// `import.meta.url`, not `__dirname`: this file is ESM, and the one that
// exists at runtime under vitest is not the one TypeScript believes in.
const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(HERE, "..", "..", "tests", "fixtures", "validator");
const SUFFIX = ".plexus.yaml";

const expected: Record<string, string[]> = JSON.parse(
  readFileSync(join(FIXTURES, "expected.json"), "utf8"),
);

const names = readdirSync(FIXTURES)
  .filter((f: string) => f.endsWith(SUFFIX))
  .map((f: string) => f.slice(0, -SUFFIX.length))
  .sort();

describe("the editor's validator matches the contract", () => {
  it("finds the fixtures", () => {
    expect(names.length).toBeGreaterThan(0);
    expect(names).toEqual(Object.keys(expected).sort());
  });

  for (const name of names) {
    it(name, () => {
      const graph = fromYaml(readFileSync(join(FIXTURES, name + SUFFIX), "utf8"));
      // No devices and no runtime: the same two arguments `plexus check` uses,
      // so device-presence and node-availability differences cannot muddy the
      // comparison. Those two are the checks the two sides legitimately differ
      // on — only the editor is told what a remote runtime can bind.
      const codes = validate(graph, [])
        .map((p) => p.code)
        .sort();
      expect(codes).toEqual(expected[name]);
    });
  }
});
