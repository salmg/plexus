"""
The PC/SC card node and reader discovery, with no reader attached.

A real reader is a fact about the machine the suite runs on, and CI has none.
So the seam these tests exercise is the adapter — `plexus.adapters.atrium`,
the one place atrium is reached into — which is mocked to say what a reader
would say. That is enough to prove the two things this feature adds: a reader
atrium reports turns into a device the editor can offer, and the node a graph
names for it binds atrium's local transport rather than the old placeholder.

What is left for a machine with a reader in it is the transport itself, and
that is atrium's, already tested there.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys

import pytest
from conftest import needs_atrium

from plexus.adapters.atrium import ReaderDiscovery, pcsc_discovery, require_atrium
from plexus.nodes import card as card_nodes
from plexus.nodes.card import CardUnavailable, PcscCard
from plexus.runtime import (
    discover_devices,
    discover_hardware,
    node_implementations,
    node_requirements,
)

#: One reader, shaped exactly like atrium's `Reader.to_dict()`.
_SCR = {
    "index": 0,
    "name": "SCM Microsystems Inc. SCR 3310",
    "kind": "contact",
    "virtual": False,
    "pn532": False,
}


#: What the adapter says on a machine with nothing plugged in.
_NOTHING = ReaderDiscovery(
    problem="No PC/SC readers found. Check that pcscd is running.",
    needs="a reader attached to this machine, with pcscd running",
)


@pytest.fixture
def one_reader(monkeypatch):
    """A machine atrium is present on, with exactly the SCR 3310 attached."""
    monkeypatch.setattr(
        "plexus.adapters.atrium.pcsc_discovery",
        lambda: ReaderDiscovery(readers=[dict(_SCR)]),
    )


@pytest.fixture
def no_reader(monkeypatch):
    """atrium present, nothing plugged in."""
    monkeypatch.setattr("plexus.adapters.atrium.pcsc_discovery", lambda: _NOTHING)


# ── discovery ────────────────────────────────────────────────────────────────


def test_a_reader_becomes_a_device_the_editor_can_offer(one_reader):
    devices = discover_devices()
    assert len(devices) == 1
    device = devices[0]
    # The fields are exactly the `devices` frame the editor's Inspector reads,
    # and its filter is `kind === deviceKind`, which for the pcsc field is
    # "pcsc". A different kind here means the reader is discovered and then
    # never shown.
    assert device.kind == "pcsc"
    assert device.available is True
    assert device.name == _SCR["name"]
    assert device.detail == "contact"
    assert set(dataclasses.asdict(device)) == {
        "id", "kind", "name", "host", "available", "detail"
    }


def test_the_device_id_round_trips_back_to_the_reader(one_reader):
    # The id a graph stores has to name the reader well enough for the node to
    # find it again at bind. It carries the pc/sc name, which atrium resolves.
    device = discover_devices()[0]
    assert card_nodes._reader_spec(device.id) == _SCR["name"]


def test_discovery_is_empty_and_says_why_with_nothing_attached(no_reader):
    assert discover_devices() == []
    assert discover_hardware()[1]["pcsc"] == _NOTHING.problem


# ── readiness ──────────────────────────────────────────────────────────────


def test_pcsc_is_ready_when_a_reader_is_attached(one_reader):
    assert card_nodes.pcsc_pending_reason() is None
    assert "pcsc" not in card_nodes.pending()
    assert node_implementations()["pcsc"] == "ready"
    assert "pcsc" not in node_requirements()


def test_pcsc_is_pending_with_its_reason_when_no_reader(no_reader):
    assert card_nodes.pcsc_pending_reason() == _NOTHING.needs
    assert "pcsc" in card_nodes.pending()
    assert node_implementations()["pcsc"] == "pending"
    # The specific piece missing, not "pyscard and a reader" whatever the case:
    # that sent people to check a reader pcsc_scan could already see.
    assert node_requirements()["pcsc"] == _NOTHING.needs


# ── why there is no reader ───────────────────────────────────────────────────
#
# Each case below once looked exactly like a machine with nothing plugged in.
# The one that prompted this: an SCR 3310 listed by pcsc_scan on Ubuntu, and
# an empty picker in Plexus, because pyscard was not in the venv running it.


@pytest.fixture
def pyscard_present(monkeypatch):
    """`import smartcard` would succeed, whether or not pyscard is really here."""
    real = importlib.util.find_spec
    monkeypatch.setattr(
        "importlib.util.find_spec",
        lambda name, *a, **k: object() if name == "smartcard" else real(name, *a, **k),
    )


def _atrium_readers():
    """atrium's `core.readers`, imported the way the adapter imports it."""
    require_atrium()
    import core.readers

    return core.readers


def _probe_answers(monkeypatch, readers, problem=""):
    """atrium's probe, saying what it would say on some machine."""
    core_readers = _atrium_readers()

    monkeypatch.setattr(core_readers, "probe", lambda: (readers, problem))


def test_a_missing_atrium_says_how_to_fetch_it(monkeypatch):
    monkeypatch.setattr("plexus.adapters.atrium.atrium_present", lambda: False)
    found = pcsc_discovery()
    assert found.readers == []
    assert "git submodule update --init vendor/atrium" in found.problem
    assert "ZIP" in found.problem
    assert "submodule" in found.needs


def test_a_missing_pyscard_names_the_python_and_the_packages(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        "importlib.util.find_spec",
        lambda name, *a, **k: None if name == "smartcard" else real(name, *a, **k),
    )
    found = pcsc_discovery()
    assert found.readers == []
    # Which Python, because "installed" into another one is the usual cause.
    assert sys.executable in found.problem
    assert 'pip install -e ".[hardware]"' in found.problem
    assert "libpcsclite-dev" in found.problem and "swig" in found.problem
    assert found.needs.startswith("pyscard in this Python")


@needs_atrium
def test_pcscd_refusing_carries_atriums_own_words(monkeypatch, pyscard_present):
    said = "Could not list readers (Service not available.) — is pcscd running?"
    _probe_answers(monkeypatch, [], said)
    found = pcsc_discovery()
    assert found.readers == []
    assert found.problem == said
    assert "pcscd" in found.needs


@needs_atrium
def test_a_probe_that_raises_is_an_answer_not_a_crash(monkeypatch, pyscard_present):
    def boom():
        raise RuntimeError("SCardEstablishContext failed")

    monkeypatch.setattr(_atrium_readers(), "probe", boom)
    found = pcsc_discovery()
    assert found.readers == []
    assert "SCardEstablishContext failed" in found.problem


@needs_atrium
def test_only_the_virtual_reader_is_said_to_be_the_wrong_kind(
    monkeypatch, pyscard_present
):
    Reader, classify = _atrium_readers().Reader, _atrium_readers().classify
    name = "Virtual PCD 00 00"
    _probe_answers(monkeypatch, [Reader(index=0, name=name, kind=classify(name))])
    found = pcsc_discovery()
    assert found.readers == []
    assert name in found.problem
    assert "not a slot a card sits in" in found.problem


@needs_atrium
def test_the_reported_ubuntu_machine_offers_the_scr3310_alone(
    monkeypatch, pyscard_present
):
    # The exact list atrium's probe printed there: vpcd's two virtual readers
    # beside the real one. Classified by atrium, not by this test.
    Reader, classify = _atrium_readers().Reader, _atrium_readers().classify

    names = [
        "Virtual PCD 00 00",
        "Virtual PCD 00 01",
        "SCM Microsystems Inc. SCR 3310 [CCID Interface] 00 00",
    ]
    readers = [Reader(index=i, name=n, kind=classify(n)) for i, n in enumerate(names)]
    _probe_answers(monkeypatch, readers)
    found = pcsc_discovery()
    assert found.problem == "" and found.needs == ""
    assert [r["name"] for r in found.readers] == [names[2]]
    assert found.readers[0]["kind"] == "contact"

    devices, problems = discover_hardware()
    assert "pcsc" not in problems
    assert [d.id for d in devices] == ["pcsc:" + names[2]]
    assert card_nodes.pcsc_pending_reason() is None


def test_the_cli_prints_the_reason_beside_the_dot(no_reader, capsys):
    from plexus.cli import main

    assert main(["nodes"]) == 0
    out = capsys.readouterr().out
    assert "pcsc: " + _NOTHING.problem in out


# ── the node ─────────────────────────────────────────────────────────────────


def test_reader_spec_strips_the_prefix_and_defaults_to_none():
    assert card_nodes._reader_spec("") is None
    assert card_nodes._reader_spec("   ") is None
    assert card_nodes._reader_spec("pcsc:ACS ACR122U 00") == "ACS ACR122U 00"
    # A bare name with no prefix is passed through, so a hand-written graph works.
    assert card_nodes._reader_spec("Gemalto") == "Gemalto"


def test_build_pcsc_binds_the_local_transport(monkeypatch):
    opened = {}

    class FakeTransport:
        def __init__(self):
            self.connected = False

        def connect(self):
            self.connected = True

        def get_atr(self):
            return b"\x3b\x00"

        def transmit(self, apdu):
            return b"\x90\x00"

        def disconnect(self):
            self.connected = False

    def fake_open(reader):
        opened["reader"] = reader
        return FakeTransport()

    monkeypatch.setattr("plexus.adapters.atrium.open_local_card", fake_open)

    node = card_nodes.build("pcsc", {"device": "pcsc:SCM Microsystems Inc. SCR 3310"})
    assert isinstance(node, PcscCard)
    # The prefix is stripped before atrium sees it.
    assert opened["reader"] == "SCM Microsystems Inc. SCR 3310"

    node.connect()
    assert node.get_atr() == b"\x3b\x00"
    assert node.transmit(b"\x00\xa4\x04\x00") == b"\x90\x00"
    node.disconnect()


def test_a_missing_reader_is_refused_at_connect_with_a_reason(monkeypatch):
    from plexus.adapters.atrium import AtriumUnavailable

    class Failing:
        def connect(self):
            raise AtriumUnavailable("No reader matching 'ghost'.")

    monkeypatch.setattr(
        "plexus.adapters.atrium.open_local_card", lambda reader: Failing()
    )
    node = card_nodes.build("pcsc", {"device": "pcsc:ghost"})
    with pytest.raises(CardUnavailable) as caught:
        node.connect()
    assert "ghost" in str(caught.value)
