"""Shared context references preserve full instructions across existing entrypoints."""
from copy import deepcopy
import hashlib
import json

import pytest

from loopx.control_plane.quota.cli_projection import _reference_explore_writeback_guidance
from loopx.control_plane.quota.turn_envelope import _turn_envelope_result, build_turn_envelope
from test_replan_host_context_projection import _quota_payload


def packet():
    source = _quota_payload()
    guidance = "Keep original requirements, lease ownership, negative evidence and stop conditions. " * 20
    source["interaction_contract"]["agent_channel"]["work_context"] = {"complete": True, "sources": [{
        "kind": "explore_turn_context", "source": "turn_start_capability_hook",
        "hook_id": "explore.turn_context", "capability_id": "explore",
        "content": {"graph": {"result_writeback_guidance": guidance,
            "writeback_results": [{"status": "refuted", "evidence": "fixture:negative"}]}}}]}
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = {
        "identity": {"turn_instance_id": "original-turn"}, "ordered_steps": [
            {"kind": "validation"}, {"kind": "durable_writeback", "optional_attachments": [
                {"capability_id": "explore", "guidance": guidance, "required": False}]}]}
    return source, guidance


def graph(source):
    return source["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["graph"]


def attachment(source):
    return source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["optional_attachments"][0]

@pytest.mark.parametrize("captured", [False, True])
def test_shared_guidance_reaches_existing_readers_without_changing_settlement(captured):
    source, guidance = packet()
    if captured:
        source["replan_read_path"] = "/synthetic/decision.json"
    original = deepcopy(source)
    shared = _reference_explore_writeback_guidance(source)
    assert source == original
    assert _reference_explore_writeback_guidance(shared) == shared
    assert shared["interaction_contract"]["cli_channel"] == source["interaction_contract"]["cli_channel"]
    assert graph(shared)["writeback_results"] == graph(source)["writeback_results"]
    assert "result_writeback_guidance" not in graph(shared)
    envelope = build_turn_envelope(shared)
    assert envelope["schema_version"] == "loopx_turn_envelope_v0"
    assert envelope["work_context"]["sources"][0]["content"]["graph"] == graph(source)
    assert envelope["action_signature"]["matches"] is True
    assert graph(source)["result_writeback_guidance"] == guidance
    if captured:
        projected = _turn_envelope_result("replan_cli_view", payload=shared)
        assert graph(projected) == graph(source)
        digest = "sha256:" + hashlib.sha256(json.dumps(shared, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        writer = _turn_envelope_result("replan_view", payload=shared, source_hash=digest, stage="writeback")
        assert writer["writeback"]["settlement_plan"] == source["interaction_contract"]["cli_channel"]["settlement_plan"]


@pytest.mark.parametrize("mutation", ["different", "wrong-step", "wrong-capability", "missing-owner", "short", "wrong-source"])
def test_nonidentical_or_unavailable_guidance_stays_inline(mutation):
    source, _ = packet()
    if mutation == "different":
        attachment(source)["guidance"] += " Extra stop."
    elif mutation == "wrong-step":
        source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["kind"] = "validation"
    elif mutation == "wrong-capability":
        attachment(source)["capability_id"] = "another"
    elif mutation == "missing-owner":
        attachment(source).pop("guidance")
    elif mutation == "short":
        attachment(source)["guidance"] = graph(source)["result_writeback_guidance"] = "Keep evidence."
    else:
        source["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["kind"] = "other_context"
    assert graph(_reference_explore_writeback_guidance(source)) == graph(source)


@pytest.mark.parametrize("mutation", ["dangling", "wrong-step", "wrong-capability", "chain", "both", "malformed"])
def test_invalid_reference_is_rejected_before_relocation(mutation):
    source, _ = packet()
    source["replan_read_path"] = "/synthetic/decision.json"
    shared = _reference_explore_writeback_guidance(source)
    if mutation == "dangling":
        graph(shared)["result_writeback_guidance_ref"] = graph(shared)["result_writeback_guidance_ref"].replace("[1]", "[99]")
    elif mutation == "wrong-step":
        shared["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["kind"] = "validation"
    elif mutation == "wrong-capability":
        attachment(shared)["capability_id"] = "another"
    elif mutation == "chain":
        attachment(shared)["guidance_ref"] = "$.unknown"
    elif mutation == "both":
        graph(shared)["result_writeback_guidance"] = "Conflicting instruction."
    else:
        graph(shared)["result_writeback_guidance_ref"] = {"path": "$.unknown"}
    for read in (lambda: _reference_explore_writeback_guidance(shared),
                 lambda: build_turn_envelope(shared),
                 lambda: _turn_envelope_result("replan_cli_view", payload=shared)):
        with pytest.raises(RuntimeError, match="Explore guidance reference"):
            read()


@pytest.mark.parametrize("body", ["🙂" * 90, "🙂" * 140, "完整要求\n" * 100])
def test_title_threshold_preserves_unicode_character_semantics(body):
    from loopx.control_plane.quota.cli_projection import _deduplicate_agent_lane_next_action
    path = "$.interaction_contract.agent_channel.work_context.sources[0].content.todo.text"
    lane = {"todo_id": "task-one", "text": "Bounded label", "title": body}
    source = {"selected_todo": {"todo_id": "task-one"}, "agent_lane_next_action": lane,
        "interaction_contract": {"agent_channel": {"work_context": {
            "complete": True, "selected_todo_text_verified": True, "sources": [{
                "source": "selected_todo", "content": {"ok": True, "matched": True,
                    "todo": {"todo_id": "task-one", "status": "open", "text": body}}}]}}}}
    original = deepcopy(source)
    shared = _deduplicate_agent_lane_next_action(lane, payload=source)
    if len(json.dumps(body, ensure_ascii=False)) > len(path) + 8:
        assert shared == {"todo_id": "task-one", "text": "Bounded label", "title_ref": path}
    else:
        assert shared == lane
    assert source == original


def test_distinct_guidance_owners_restore_each_original_source():
    source, first = packet()
    context = source["interaction_contract"]["agent_channel"]["work_context"]
    second = "Retain this separate capability evidence and its original stop conditions. " * 20
    another = deepcopy(context["sources"][0])
    another["content"]["graph"]["result_writeback_guidance"] = second
    context["sources"].append(another)
    steps = source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
    steps[1]["optional_attachments"].append({"capability_id": "explore", "guidance": second})
    original = deepcopy(source)
    shared = _reference_explore_writeback_guidance(source)
    assert source == original
    graphs = [item["content"]["graph"] for item in shared["interaction_contract"]["agent_channel"]["work_context"]["sources"]]
    assert graphs[0]["result_writeback_guidance_ref"].endswith("optional_attachments[0].guidance")
    assert graphs[1]["result_writeback_guidance_ref"].endswith("optional_attachments[1].guidance")
    restored = build_turn_envelope(shared)["work_context"]["sources"]
    assert [item["content"]["graph"]["result_writeback_guidance"] for item in restored] == [first, second]
