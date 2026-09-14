"""
What someone gets when they install this rather than clone it.

Everything here was measured broken first. A `pip install` produced a `plexus`
that could read a graph and not run one, because atrium was looked for at one
path that only exists in a clone; and using the editor meant a second process,
a Node toolchain and a URL with `?runtime=` in it to paste.

Deliberately not gated on the atrium submodule — this is the file that has to
keep working on a machine that has neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from plexus.adapters.atrium import ATRIUM_VARIABLE, atrium_candidates
from plexus.api.server import create_app, editor_is_bundled

REPO = Path(__file__).resolve().parent.parent


# ── Finding atrium ──────────────────────────────────────────────────────────


def test_an_explicit_atrium_path_is_tried_first(monkeypatch, tmp_path):
    monkeypatch.setenv(ATRIUM_VARIABLE, str(tmp_path))
    assert atrium_candidates()[0] == tmp_path.resolve()


def test_a_clone_is_found_without_being_told(monkeypatch):
    """The submodule, relative to the package. Today's common case."""
    monkeypatch.delenv(ATRIUM_VARIABLE, raising=False)
    assert (REPO / "vendor" / "atrium").resolve() in atrium_candidates()


def test_the_search_has_no_duplicates(monkeypatch):
    """`cwd` and the package root are the same directory inside a clone, and a
    message that lists one path twice reads like a bug."""
    monkeypatch.delenv(ATRIUM_VARIABLE, raising=False)
    monkeypatch.chdir(REPO)
    found = atrium_candidates()
    assert len(found) == len(set(found))


def test_a_missing_atrium_names_every_place_it_looked(monkeypatch, tmp_path):
    """
    The old message named one directory inside site-packages that nobody would
    ever create, and told you to run `git submodule update` in a tree that is
    not a git repository.

    The candidate list is replaced rather than the filesystem arranged: on a
    developer's machine one of the real fallbacks is a clone that exists, and a
    test that skips itself there is not a test.
    """
    from plexus.adapters import atrium as adapter

    nowhere = [tmp_path / "one", tmp_path / "two"]
    monkeypatch.setattr(adapter, "atrium_candidates", lambda: nowhere)
    adapter.require_atrium.cache_clear()
    try:
        with pytest.raises(adapter.AtriumUnavailable) as refused:
            adapter.require_atrium()
    finally:
        adapter.require_atrium.cache_clear()

    message = str(refused.value)
    for candidate in nowhere:
        assert str(candidate) in message
    assert ATRIUM_VARIABLE in message
    assert "git submodule update" in message


# ── One origin ──────────────────────────────────────────────────────────────


def test_the_runtime_serves_the_editor_when_it_was_built_in():
    """
    `plexus up` is one command because these share an origin: no CORS, no mixed
    content, no allow-list, and no URL to paste. The editor asks
    `/api/health` — same origin, so the browser lets it read the answer — and
    connects to `/ws` beside it.
    """
    with TestClient(create_app()) as client:
        # The runtime's own routes answer whether or not a bundle is present:
        # the editor is mounted at "/" and a mount at "/" must not shadow them.
        assert client.get("/api/health").status_code == 200
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "version": 1})
            assert socket.receive_json()["type"] in ("devices", "state", "batch")

        page = client.get("/")
        if editor_is_bundled():
            assert page.status_code == 200
            assert "<title>Plexus</title>" in page.text
        else:
            assert page.status_code == 404


def test_an_editor_built_after_the_app_exists_is_still_served(monkeypatch, tmp_path):
    """
    Measured: `plexus up` built the editor and then 404ed on it.

    `app = create_app()` runs when the module is imported, and `plexus up`
    imports the module to find out where the bundle goes — so the app was
    always built one moment before the bundle existed. Deciding the mount at
    that moment decides it wrong.
    """
    from plexus.api import server

    web = tmp_path / "web"
    monkeypatch.setattr(server, "WEB_ROOT", web)
    app = server.create_app()  # nothing built yet, exactly as `up` finds it

    web.mkdir()
    (web / "index.html").write_text("<title>Plexus</title>", encoding="utf-8")

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "<title>Plexus</title>" in page.text


def test_a_runtime_with_no_editor_404s_rather_than_500s(monkeypatch, tmp_path):
    """
    Measured on a clean checkout, and nowhere else.

    `check_dir=False` does not make the directory optional — it moves
    starlette's check from mount time to the first request, where it raises
    `RuntimeError` instead. So an install with no editor built into it answered
    `/` with a 500. Every machine that has ever run `npm run build:bundle` has
    the directory and never sees it; CI, on a fresh clone, saw it immediately.
    """
    from plexus.api import server

    monkeypatch.setattr(server, "WEB_ROOT", tmp_path / "never-built")
    with TestClient(server.create_app()) as client:
        assert client.get("/").status_code == 404
        assert client.get("/assets/index.js").status_code == 404
        # And the runtime's own routes still answer, mount or no mount.
        assert client.get("/api/health").status_code == 200


def test_up_without_a_bundle_says_what_to_run(monkeypatch, capsys):
    """
    A wheel built without the editor still installs, and `--no-build` reaches
    the same place deliberately. Either way the command has to say what to do
    instead, rather than serve a blank page.

    `build=False` is not incidental: with it left on, this test runs a vite
    build as a side effect. It did, once.
    """
    from plexus import cli
    from plexus.api import server

    monkeypatch.setattr(server, "editor_is_bundled", lambda: False)

    assert cli._up("127.0.0.1", 8900, open_browser=False, build=False) == 2

    said = capsys.readouterr().err
    assert "npm run build:bundle" in said, "it must say how to get the bundle"
    assert "?runtime=" in said, "and offer the two-process way meanwhile"
    assert "plexus serve" in said


def test_up_does_not_tell_a_wheel_to_cd_into_a_directory_it_lacks(
    monkeypatch, capsys
):
    """
    `cd ui && npm install` is good advice inside a clone and nonsense to
    somebody who installed a wheel: there is no ui/ there to cd into.
    """
    from plexus import cli
    from plexus.api import server

    monkeypatch.setattr(server, "editor_is_bundled", lambda: False)
    monkeypatch.setattr(cli, "_editor_source", lambda: None)

    assert cli._up("127.0.0.1", 8900, open_browser=False, build=False) == 2

    said = capsys.readouterr().err
    assert "cd ui" not in said
    assert "git clone" in said, "it has to say where a ui/ comes from"


def test_the_editor_is_built_into_the_package_that_serves_it(monkeypatch, tmp_path):
    """
    The npm script writes to `../plexus/web`, which is the right place in a
    clone and the wrong one under a non-editable install: the package doing the
    serving is in site-packages, and a build beside the source would look like
    it worked and change nothing. So the destination is passed in.
    """
    from plexus import cli

    ran = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: "npm-here")
    monkeypatch.setattr(cli, "_editor_source", lambda: tmp_path)
    (tmp_path / "node_modules").mkdir()

    class Finished:
        returncode = 0

    monkeypatch.setattr(
        cli.subprocess, "run", lambda step, **kw: ran.append(step) or Finished()
    )

    assert cli._build_editor(tmp_path / "elsewhere" / "web") is True
    assert len(ran) == 1, "node_modules is there; it must not install again"
    assert ran[0][-2:] == ["--outDir", str(tmp_path / "elsewhere" / "web")]


def test_building_the_editor_needs_both_a_source_and_npm(monkeypatch, tmp_path):
    """Neither is present in a wheel on a machine with no Node."""
    from plexus import cli

    monkeypatch.setattr(cli, "_editor_source", lambda: None)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "npm-here")
    assert cli._build_editor(tmp_path) is False

    monkeypatch.setattr(cli, "_editor_source", lambda: tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli._build_editor(tmp_path) is False
