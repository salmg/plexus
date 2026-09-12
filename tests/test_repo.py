"""
Does a fresh clone of this repository actually work?

Every test in the rest of the suite passes on the machine that wrote the files, which
is exactly why this module exists. A `*.jsonl` line in .gitignore once left
tests/captures/mastercard-probe.jsonl untracked: three test modules, an example rig,
both first-run guides and the screenshot script referenced a file that was not in the
repository, and the suite was green throughout.

So these assert on `git ls-files`, not on the filesystem. Existence here proves nothing
about what someone else would get.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def tracked() -> set[str]:
    """Every path git would hand a fresh clone, as forward-slashed repo-relative text."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


@pytest.fixture(scope="module")
def tracked_paths() -> set[str]:
    return tracked()


def test_the_test_fixtures_are_tracked(tracked_paths):
    """
    The capture the engine and server suites replay.

    Without this the whole runtime suite fails on a fresh clone with "No capture at …",
    which reads like a broken engine rather than a missing file.
    """
    assert "tests/captures/mastercard-probe.jsonl" in tracked_paths


def test_every_example_rig_is_tracked(tracked_paths):
    for rig in sorted((REPO / "examples").glob("*.yaml")):
        relative = rig.relative_to(REPO).as_posix()
        assert relative in tracked_paths, f"{relative} exists here but is not in the repo"


def test_the_generated_catalogue_is_tracked(tracked_paths):
    """Python reads this file; an untracked one means the runtime cannot start."""
    assert "plexus/schema/catalogue.json" in tracked_paths


#: Paths named inside a file we ship. A reference to something untracked is a broken
#: instruction, whether it is in code, an example or a manual page.
_PATH_PATTERN = re.compile(
    r"(?:tests|examples|schema|docs|plexus|ui)/[A-Za-z0-9_./-]+\.(?:jsonl|yaml|yml|json|png|md)"
)

_SOURCES = (
    "tests/test_runtime.py",
    "tests/test_server.py",
    "tests/test_security.py",
    "examples/probe-a-capture.plexus.yaml",
    "examples/contactless-relay.plexus.yaml",
    "docs/first-run.md",
    "docs/es/first-run.md",
    "docs/install.md",
    "docs/README.md",
    "ui/scripts/screenshots.mjs",
)


def test_nothing_we_ship_points_at_an_untracked_file(tracked_paths):
    """
    Sweep the files most likely to name a fixture and check each reference resolves.

    Deliberately not a full-repo grep: prose mentions paths that are examples rather
    than references ("logs/trace.jsonl", a path someone is told to choose), and a test
    that cannot tell those apart would be turned off within a week.
    """
    missing: list[str] = []

    for source in _SOURCES:
        path = REPO / source
        assert path.is_file(), f"{source} is named by this test but does not exist"
        text = path.read_text(encoding="utf-8")

        for candidate in sorted(set(_PATH_PATTERN.findall(text))):
            # logs/ is runtime output and is ignored on purpose; a docs/ page naming
            # another page is covered by the link test below.
            if candidate.startswith("logs/"):
                continue
            if candidate in tracked_paths:
                continue
            # Only flag it if it actually exists here — otherwise it is prose about a
            # path the reader supplies, not a reference to a file we ship.
            if (REPO / candidate).exists():
                missing.append(f"{source} -> {candidate}")

    assert not missing, (
        "referenced, present on this machine, but not in the repo:\n"
        + "\n".join(missing)
    )


def test_every_screenshot_the_manual_shows_is_tracked(tracked_paths):
    image = re.compile(r"!\[[^\]]*\]\(([^)]+\.png)\)")
    missing: list[str] = []

    for page in sorted((REPO / "docs").rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        for reference in image.findall(text):
            target = (page.parent / reference).resolve()
            try:
                relative = target.relative_to(REPO).as_posix()
            except ValueError:
                missing.append(f"{page.name} -> {reference} (outside the repo)")
                continue
            if relative not in tracked_paths:
                missing.append(f"{page.name} -> {relative}")

    assert not missing, (
        "manual pages showing images that are not in the repo:\n"
        + "\n".join(missing)
    )


def test_every_internal_manual_link_resolves():
    """A dead link in the manual is a dead end for whoever followed it."""
    link = re.compile(r"\[[^\]]+\]\((?!https?://|#)([^)#]+)(?:#[^)]*)?\)")
    broken: list[str] = []

    for page in sorted((REPO / "docs").rglob("*.md")):
        for target in link.findall(page.read_text(encoding="utf-8")):
            if not (page.parent / target).resolve().exists():
                broken.append(f"{page.relative_to(REPO).as_posix()} -> {target}")

    assert not broken, "broken links in the manual:\n" + "\n".join(broken)


def test_the_catalogue_is_inside_the_package_and_not_beside_it():
    """
    The one packaging invariant.

    `pip install .` collects `plexus*` and nothing above it, so anything the
    runtime needs at import time has to live under `plexus/`. The catalogue
    spent a while at the repository root, which works in an editable install
    and in a clone — the only two ways anybody here ever runs it — and left a
    non-editable install with a `plexus` command that could not answer a
    single subcommand.

    Checked against the package, not the repository, so it still holds when
    this file is read from an installed copy.
    """
    from plexus import catalogue as catalogue_module

    package = Path(catalogue_module.__file__).resolve().parent
    assert catalogue_module.CATALOGUE_PATH.exists()
    # Raises if the catalogue is not under the package directory.
    inside = catalogue_module.CATALOGUE_PATH.relative_to(package)
    assert inside.parts[0] == "schema"


def test_pyproject_says_the_catalogue_is_package_data():
    """
    Belt to the braces above. setuptools includes it today by default; saying
    so means a change to that default fails here rather than in someone's
    `pip install`.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.setuptools.package-data]" in text
    assert 'plexus = ["schema/*.json"]' in text


def test_the_manual_names_every_field_the_inspector_shows():
    """
    A control nobody documented is a control nobody can look up.

    The manual had nineteen of them: every device picker, both of the timing
    node's numbers, both of the breakpoint's, the mutation node's playbook and
    its logging switch, and the agent's three. Some of those were on nodes the
    page described in prose and gave no table at all.

    Compared against the catalogue rather than a list kept beside it, because a
    list kept beside it is the thing that went stale.
    """
    from plexus.catalogue import CATALOGUE

    page = (REPO / "docs" / "nodes.md").read_text(encoding="utf-8")
    missing = [
        "%s.%s (%s)" % (node_type.key, field.key, field.label)
        for node_type in CATALOGUE
        for field in node_type.fields
        if field.label not in page
    ]
    assert not missing, "docs/nodes.md never names:\n" + "\n".join(missing)


def test_the_manual_names_every_node():
    """The same, one level up."""
    from plexus.catalogue import CATALOGUE

    page = (REPO / "docs" / "nodes.md").read_text(encoding="utf-8")
    missing = [t.key for t in CATALOGUE if t.title not in page]
    assert not missing, "docs/nodes.md never names: %s" % ", ".join(missing)


def test_every_screenshot_that_is_generated_is_also_shown(tracked_paths):
    """
    The other direction from the test above, and it found two: `02-light-mode`
    and `07-runnable-rig` were generated by `npm run screenshots`, committed,
    and displayed on no page at all. A tracked image nobody shows is a file
    that goes stale without anyone noticing — which is what a screenshot is
    most likely to do.
    """
    pages = list((REPO / "docs").rglob("*.md")) + [REPO / "README.md"]
    shown = "\n".join(p.read_text(encoding="utf-8") for p in pages if p.is_file())

    orphans = [
        name
        for name in sorted(tracked_paths)
        if name.startswith("docs/images/") and name.split("/")[-1] not in shown
    ]
    assert not orphans, "generated, tracked, and shown nowhere:\n" + "\n".join(orphans)
