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

import pytest

from plexus.nodes import card as card_nodes
from plexus.nodes.card import CardUnavailable, PcscCard
from plexus.runtime import discover_devices, node_implementations, node_requirements

#: One reader, shaped exactly like atrium's `Reader.to_dict()`.
_SCR = {
    "index": 0,
    "name": "SCM Microsystems Inc. SCR 3310",
    "kind": "contact",
    "virtual": False,
    "pn532": False,
}
#: atrium's own output side — a slot no card sits in.
_VIRTUAL = {
    "index": 1,
    "name": "Virtual PCD",
    "kind": "virtual",
    "virtual": True,
    "pn532": False,
}


@pytest.fixture
def one_reader(monkeypatch):
    """A machine atrium is present on, with exactly the SCR 3310 attached."""
    monkeypatch.setattr("plexus.adapters.atrium.atrium_present", lambda: True)
    monkeypatch.setattr("plexus.adapters.atrium.pcsc_readers", lambda: [dict(_SCR)])


@pytest.fixture
def no_reader(monkeypatch):
    """atrium present, nothing plugged in."""
    monkeypatch.setattr("plexus.adapters.atrium.atrium_present", lambda: True)
    monkeypatch.setattr("plexus.adapters.atrium.pcsc_readers", lambda: [])


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


def test_the_virtual_reader_is_never_offered_as_a_card_slot(monkeypatch):
    monkeypatch.setattr("plexus.adapters.atrium.atrium_present", lambda: True)
    monkeypatch.setattr(
        "plexus.adapters.atrium.pcsc_readers", lambda: [dict(_VIRTUAL), dict(_SCR)]
    )
    names = [d.name for d in discover_devices()]
    assert names == [_SCR["name"]]  # the virtual one is left out


def test_discovery_is_empty_and_quiet_with_nothing_attached(no_reader):
    assert discover_devices() == []


# ── readiness ──────────────────────────────────────────────────────────────


def test_pcsc_is_ready_when_a_reader_is_attached(one_reader):
    assert card_nodes.pcsc_pending_reason() is None
    assert "pcsc" not in card_nodes.pending()
    assert node_implementations()["pcsc"] == "ready"
    assert "pcsc" not in node_requirements()


def test_pcsc_is_pending_with_its_reason_when_no_reader(no_reader):
    assert card_nodes.pcsc_pending_reason() is not None
    assert "pcsc" in card_nodes.pending()
    assert node_implementations()["pcsc"] == "pending"
    assert "pyscard" in node_requirements()["pcsc"]


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
