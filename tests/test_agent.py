"""
The EMV agent terminal — the node whose next command a model decides.

Driven here by a *scripted* provider and a fake card, so the loop is exercised
with no key and no network: what matters is that the agent sends what the model
asks, hands each reply back before the next turn, stops when told, and cannot
run away. The one path that needs a real model (actually reasoning about a card)
is not something a test should buy.

Deliberately not gated on the atrium submodule. The loop uses a fake provider,
and the refusal path is the same refusal whether atrium is absent (no checkout)
or present-but-unconfigured (no key) — a machine with neither still runs this.
"""

from __future__ import annotations

import pytest

from plexus.adapters.atrium import atrium_present
from plexus.nodes.terminal import (
    EmvAgentTerminal,
    TerminalUnavailable,
    agent_pending_reason,
    build,
    pending,
)

PPSE = "00A404000E325041592E5359532E444446303100"
SELECT_VISA = "00A4040007A000000003101000"

MODEL_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ATRIUM_LLM_BASE_URL",
    "ATRIUM_LLM_PROVIDER",
    "ATRIUM_LLM_MODEL",
    "ATRIUM_LLM_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """No model configured unless a test says otherwise."""
    for name in MODEL_ENV:
        monkeypatch.delenv(name, raising=False)


# ── Fakes ─────────────────────────────────────────────────────────────────────


class Call:
    def __init__(self, name, ident="c1", **inp):
        self.id = ident
        self.name = name
        self.input = inp


class Turn:
    def __init__(self, *tool_calls, text=""):
        self.text = text
        self.tool_calls = list(tool_calls)

    @property
    def wants_tools(self):
        return bool(self.tool_calls)


class ScriptedProvider:
    """Returns a fixed turn per `complete()`, and records what it is handed."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.began = None
        self.results = []
        self.completes = 0

    def begin(self, system, first_user):
        self.began = (system, first_user)

    def add_user(self, text):  # pragma: no cover - unused by the agent
        pass

    def add_tool_results(self, results):
        self.results.append(results)

    def complete(self, tools, max_tokens=4096):
        self.completes += 1
        # An empty turn ends the loop, which is the natural "model is done".
        return self.turns.pop(0) if self.turns else Turn()


class LoopingProvider:
    """Always asks to send `n` APDUs a turn, forever — a model that will not stop."""

    def __init__(self, per_turn=1):
        self.per_turn = per_turn
        self.completes = 0

    def begin(self, system, first_user):
        pass

    def add_tool_results(self, results):
        pass

    def complete(self, tools, max_tokens=4096):
        self.completes += 1
        return Turn(
            *[Call("send_apdu", ident="x%d" % i, apdu=PPSE) for i in range(self.per_turn)]
        )


class FakeCard:
    """Records commands; answers from a table, or 6F00 for anything unscripted."""

    def __init__(self, answers=None, on_transmit=None):
        self.sent = []
        self.answers = answers or {}
        self.on_transmit = on_transmit

    def transmit(self, command: bytes) -> bytes:
        self.sent.append(command.hex().upper())
        if self.on_transmit:
            self.on_transmit(self)
        return self.answers.get(command.hex().upper(), b"\x6f\x00")


def never() -> bool:
    return False


# ── The loop ────────────────────────────────────────────────────────────────


def test_the_agent_sends_the_apdus_the_model_asks_for():
    provider = ScriptedProvider([
        Turn(Call("send_apdu", apdu=PPSE)),
        Turn(Call("send_apdu", apdu=SELECT_VISA)),
        Turn(Call("finish", summary="A Visa card.")),
    ])
    card = FakeCard(answers={PPSE: bytes.fromhex("6F1A9000")})
    agent = EmvAgentTerminal({}, provider)

    agent.drive(card, never)

    assert card.sent == [PPSE, SELECT_VISA]
    assert agent.sent == 2
    assert "A Visa card." in agent.summary()
    assert agent.summary()[0] == "EMV agent: 2 commands sent."


def test_the_model_gets_each_reply_before_the_next_command():
    provider = ScriptedProvider([
        Turn(Call("send_apdu", ident="a", apdu=PPSE)),
        Turn(Call("finish")),
    ])
    card = FakeCard(answers={PPSE: bytes.fromhex("6F209000")})
    EmvAgentTerminal({}, provider).drive(card, never)

    # The first (and only) batch of tool results carries the card's real answer,
    # keyed to the call id, so the model's next turn can reason about it.
    assert provider.results, "no tool results were fed back to the model"
    ident, result = provider.results[0][0]
    assert ident == "a"
    assert result["response"] == "6F209000"
    assert result["sw"] == "9000"
    assert result["ok"] is True


def test_finish_ends_the_loop_even_with_turns_left():
    provider = ScriptedProvider([
        Turn(Call("finish", summary="Done early.")),
        Turn(Call("send_apdu", apdu=PPSE)),  # must never be reached
    ])
    card = FakeCard()
    agent = EmvAgentTerminal({}, provider)
    agent.drive(card, never)

    assert card.sent == []
    assert provider.completes == 1
    assert "Done early." in agent.summary()


def test_a_bad_apdu_is_reported_to_the_model_not_raised():
    provider = ScriptedProvider([
        Turn(Call("send_apdu", ident="bad", apdu="nothex")),
        Turn(Call("finish")),
    ])
    card = FakeCard()
    agent = EmvAgentTerminal({}, provider)

    agent.drive(card, never)  # must not raise

    assert card.sent == [], "a bad apdu must not reach the card"
    assert agent.sent == 0
    _, result = provider.results[0][0]
    assert "error" in result


def test_stop_halts_the_agent_between_commands():
    # Stop flips true the moment the first command is sent.
    def flip(card):
        state["stopped"] = True

    state = {"stopped": False}
    card = FakeCard(on_transmit=flip)
    provider = LoopingProvider(per_turn=1)

    EmvAgentTerminal({}, provider).drive(card, lambda: state["stopped"])

    # One command got out before stop was seen; the loop must not send a second.
    assert len(card.sent) == 1


def test_the_turn_ceiling_bounds_a_model_that_never_finishes():
    provider = LoopingProvider(per_turn=1)
    card = FakeCard()
    agent = EmvAgentTerminal({}, provider)

    agent.drive(card, never)

    assert provider.completes == EmvAgentTerminal.MAX_STEPS
    assert agent.sent == EmvAgentTerminal.MAX_STEPS
    assert any("ceiling" in line for line in agent.summary())


def test_the_command_ceiling_bounds_a_single_greedy_turn():
    # One turn asking for far more commands than the ceiling allows.
    provider = LoopingProvider(per_turn=1000)
    card = FakeCard()
    agent = EmvAgentTerminal({}, provider)

    agent.drive(card, never)

    assert agent.sent == EmvAgentTerminal.MAX_COMMANDS
    assert any("ceiling" in line for line in agent.summary())


def test_the_task_and_brute_flag_reach_the_opening():
    provider = ScriptedProvider([Turn()])
    agent = EmvAgentTerminal(
        {"task": "Enumerate every record", "bruteSfi": True}, provider
    )
    agent.drive(FakeCard(), never)

    _system, opening = provider.began
    assert "Enumerate every record" in opening
    assert "SFI" in opening


# ── Availability and refusal ──────────────────────────────────────────────────


def test_the_agent_is_pending_without_a_model():
    reason = agent_pending_reason()
    assert reason is not None
    assert "agent" in pending()
    # Whichever is missing, the message names the fix.
    assert "atrium" in reason or "ANTHROPIC_API_KEY" in reason


def test_binding_the_agent_without_a_model_is_refused():
    # The same refusal whether atrium is absent or present-but-unconfigured.
    with pytest.raises(TerminalUnavailable):
        build("agent", {"provider": "anthropic"})


def test_build_wires_the_agent_to_the_resolved_provider(monkeypatch):
    """
    The success path, with only the one door to a real model stubbed: `build`
    passes the chosen provider name through, hands back an `EmvAgentTerminal`,
    and that terminal drives the provider it was given.
    """
    import plexus.nodes.terminal as terminal

    provider = ScriptedProvider([Turn(Call("finish", summary="wired"))])
    asked = {}

    def fake_resolve(name="", model=""):
        asked["name"] = name
        return provider

    monkeypatch.setattr(terminal._atrium, "resolve_llm_provider", fake_resolve)

    node = build("agent", {"provider": "openai", "task": "Read it"})
    assert isinstance(node, EmvAgentTerminal)
    assert asked["name"] == "openai"

    node.drive(FakeCard(), never)
    assert "wired" in node.summary()


@pytest.mark.skipif(not atrium_present(), reason="needs an atrium checkout to import")
def test_a_configured_model_makes_the_agent_ready(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert agent_pending_reason() is None
    assert "agent" not in pending()
