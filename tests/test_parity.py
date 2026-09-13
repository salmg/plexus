"""
The two validators agree, and something checks that they do.

The project's load-bearing claim is that the editor and the runtime apply the
same rules in two languages: `ui/src/graph/validate.ts` and `plexus/graph.py`
share eighteen problem codes, kept in step entirely by hand. Nothing verified
it. A rule added to one side and forgotten on the other would draw clean in the
editor and refuse at `arm`, or block in the editor and run fine — and the first
anyone would know is a graph behaving differently depending on which half of the
project was looking at it.

So: one set of fixture graphs, one file of expected problem codes, and *both*
suites assert against it. `tests/fixtures/validator/expected.json` is the
contract rather than a snapshot either side generates — a generated snapshot
would quietly absorb a drift on whichever side generated it. Change a rule and
you change the contract deliberately, and the other language's suite tells you
if you only changed one of them.

The mirror of this file is `ui/src/graph/parity.test.ts`, which loads the same
YAML with the editor's own reader and runs the editor's own validator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plexus.graph import Graph, validate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "validator"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


def fixture_names() -> list[str]:
    return sorted(f.name[: -len(".plexus.yaml")] for f in FIXTURES.glob("*.plexus.yaml"))


@pytest.mark.parametrize("name", fixture_names())
def test_the_python_validator_matches_the_contract(name: str) -> None:
    graph = Graph.load(FIXTURES / ("%s.plexus.yaml" % name))
    codes = sorted(p.code for p in validate(graph))
    assert codes == EXPECTED[name], (
        "%s: python says %s, the contract says %s" % (name, codes, EXPECTED[name])
    )


def test_every_fixture_is_in_the_contract() -> None:
    """A fixture nobody asserts on is a fixture that proves nothing."""
    assert fixture_names() == sorted(EXPECTED)


def test_the_fixtures_cover_the_rules_worth_covering() -> None:
    """
    Not every code — some need devices or hosts the fixtures do not set up, and
    `unknown-type` is unreachable here because the Python loader refuses an
    unknown type at parse rather than at validate. But a fixture set that had
    quietly stopped covering anything would pass every other test in this file.
    """
    covered = {code for codes in EXPECTED.values() for code in codes}
    for code in ("no-chain", "same-role", "self-edge", "cycle", "port-contended"):
        assert code in covered, "%s is no longer covered by any fixture" % code
    assert len(covered) >= 10
