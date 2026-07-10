# tests/test_v07_loop.py
import json
from types import SimpleNamespace as NS

from agent.factcheck.context import PipelineContext
from agent.factcheck.loop import LoopStats, revise_in_loop, run_loop
from agent.factcheck.loop_tools import ToolRuntime

_DRAFT_INPUT = {
    "hypotheses": ["h1"], "target_hypothesis": "h1", "action": "verify",
    "central_claim": "c", "headline_finding": "h", "justification": "j",
    "primary_sources": [], "load_bearing_facts": [],
    "evidence_refs": [], "verdict_derivation": "d",
    "confidence": "low", "verdict_leaning": "insufficient",
}


class FakeClient:
    """Scripted responses; each entry is a list of content blocks."""
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        blocks = self.scripted.pop(0)
        return NS(content=blocks, stop_reason="tool_use" if any(
            getattr(b, "type", "") == "tool_use" for b in blocks) else "end_turn")


def _tool_use(name, input_, id_="t1"):
    return NS(type="tool_use", name=name, input=input_, id=id_)


def test_loop_fetch_then_finalize(monkeypatch):
    rt = ToolRuntime()
    monkeypatch.setattr(rt, "fetch_page", lambda url, origin="fetch": f"fetched {url}")
    client = FakeClient([
        [_tool_use("fetch_page", {"url": "https://a.test"})],
        [_tool_use("finalize", _DRAFT_INPUT, id_="t2")],
    ])
    draft, rt2, stats, msgs = run_loop("post", client=client, ctx=PipelineContext(),
                                       runtime=rt, model="m")
    assert draft is not None and draft.action == "verify"
    assert stats.finalized and stats.turns == 2
    # the fetch tool_result went back into the conversation
    assert any(m["role"] == "user" and isinstance(m["content"], list)
               and m["content"][0].get("type") == "tool_result" for m in msgs)


def test_loop_invalid_finalize_retries():
    bad = {"action": "verify"}          # missing required fields
    client = FakeClient([
        [_tool_use("finalize", bad)],
        [_tool_use("finalize", _DRAFT_INPUT, id_="t2")],
    ])
    draft, _, stats, _ = run_loop("post", client=client, ctx=PipelineContext(), model="m")
    assert draft is not None and stats.turns == 2


def test_loop_turn_cap_forces_finalize_nudge():
    # Model never finalizes; loop must stop at cap + 1 forced turn, unfinalized.
    client = FakeClient([[NS(type="text", text="thinking...")] for _ in range(10)])
    draft, _, stats, _ = run_loop("post", client=client, ctx=PipelineContext(),
                                  model="m", max_turns=3)
    assert draft is None
    assert stats.hit_turn_cap is True
    assert stats.turns <= 4              # cap + one forced-finalize attempt


def _assert_alternating(msgs):
    roles = [m["role"] for m in msgs]
    for a, b in zip(roles, roles[1:]):
        assert a != b, f"adjacent same-role messages: {roles}"


def _fetch_finalize_client():
    return FakeClient([
        [_tool_use("fetch_page", {"url": "https://a.test"})],
        [_tool_use("finalize", _DRAFT_INPUT, id_="t2")],
    ])


def test_no_adjacent_same_role_messages(monkeypatch):
    # Scenario 1: fetch → finalize.
    rt = ToolRuntime()
    monkeypatch.setattr(rt, "fetch_page", lambda url, origin="fetch": f"fetched {url}")
    _, _, _, msgs = run_loop("post", client=_fetch_finalize_client(),
                             ctx=PipelineContext(), runtime=rt, model="m")
    _assert_alternating(msgs)
    # Scenario 2: model never finalizes (turn cap + forced nudge merges into
    # the trailing continue-nudge user message instead of stacking).
    client = FakeClient([[NS(type="text", text="thinking...")] for _ in range(10)])
    _, _, _, msgs = run_loop("post", client=client, ctx=PipelineContext(),
                             model="m", max_turns=3)
    _assert_alternating(msgs)


def test_finalize_closes_transcript(monkeypatch):
    rt = ToolRuntime()
    monkeypatch.setattr(rt, "fetch_page", lambda url, origin="fetch": f"fetched {url}")
    draft, _, _, msgs = run_loop("post", client=_fetch_finalize_client(),
                                 ctx=PipelineContext(), runtime=rt, model="m")
    assert draft is not None
    last = msgs[-1]
    assert last["role"] == "user"
    assert any(isinstance(b, dict) and b.get("type") == "tool_result"
               and b.get("tool_use_id") == "t2" for b in last["content"])


def test_revise_in_loop_valid_transcript(monkeypatch):
    rt = ToolRuntime()
    monkeypatch.setattr(rt, "fetch_page", lambda url, origin="fetch": f"fetched {url}")
    draft, _, _, msgs = run_loop("post", client=_fetch_finalize_client(),
                                 ctx=PipelineContext(), runtime=rt, model="m")
    assert draft is not None
    client2 = FakeClient([[_tool_use("finalize", _DRAFT_INPUT, id_="t3")]])
    draft2, stats2 = revise_in_loop(msgs, "fix X", client=client2, runtime=rt,
                                    model="m")
    assert draft2 is not None
    _assert_alternating(msgs)
