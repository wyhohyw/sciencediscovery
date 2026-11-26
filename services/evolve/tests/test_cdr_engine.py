# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The CDR engine keeps scored sequences and drops a framework edit."""

from __future__ import annotations

import json
import random

import pytest

pytestmark = pytest.mark.science_tags(category="ut", os="linux", arch=("amd64", "arm64"))

from sciencediscovery_evolve.cdr_engine import (
    CdrEngine,
    ESM_SKILL,
    FULL_SKILL,
    POINT_SKILL,
    _Member,
    _draw_skill,
    _measure_reading,
    _select,
    _temperature,
    positive_llr_substitutions,
)
from sciencediscovery_evolve.engine import RunSpec


def baseline() -> str:
    return json.dumps({
        "chain": "D",
        "cdr_regions": [[1, 3]],
        "candidates_per_cycle": 2,
        "sequence": "ACDE",
        "target_chain": "A",
        "target_sequence": "GG",
    })


def spec(**overrides) -> RunSpec:
    values = dict(
        search_id="cdr-1",
        algorithm="cdr",
        expansions=1,
        scorecard_hash="sha256:card",
        scorecard={"criteria": [{"id": "score"}]},
        baseline_code=baseline(),
    )
    values.update(overrides)
    return RunSpec(**values)


def run(complete, score, expansions=1, stop=None):
    events = []
    CdrEngine(complete=complete, score=score).run(
        spec(expansions=expansions),
        events.append,
        stop or (lambda: False),
    )
    return events


def test_a_cdr_substitution_enters_the_population_and_a_framework_edit_does_not():
    def complete(_prompt: str) -> str:
        return json.dumps({"mutations": [[["D", 2, "W"]], [["D", 4, "A"]]]})

    def score(payload):
        assert payload["sequence"] in {"AWDE", "ACDE"}
        return True, 1.5 if payload["sequence"] == "AWDE" else 0.0, ""

    events = run(complete, score, expansions=2)
    expanded = [event for event in events if event["type"] == "expanded"]
    merged = [event for event in events if event["type"] == "merged"]
    assert expanded[0]["valid"] is True
    assert expanded[0]["changeSummary"] == "D2CW"
    assert expanded[1]["valid"] is False
    assert merged[0]["accepted"] is True
    assert merged[1]["accepted"] is False
    finished = events[-1]
    assert finished["type"] == "search_finished"
    assert finished["status"] == "succeeded"
    assert finished["bestNodeIndex"] == expanded[0]["nodeIndex"]


def test_a_failed_gate_is_recorded_and_does_not_become_the_winner():
    def score(payload):
        if payload["sequence"] == "ACDE":
            return True, 0.0, ""
        return False, None, "gate"

    events = run(
        lambda _prompt: json.dumps({"mutations": [[["D", 1, "W"]]]}),
        score,
    )
    expanded = next(event for event in events if event["type"] == "expanded")
    assert expanded["valid"] is False
    assert expanded["score"] is None
    assert events[-1]["bestNodeIndex"] == 0


def test_a_measured_gate_failure_keeps_its_score_off_the_population():
    def score(payload):
        if payload["sequence"] == "ACDE":
            return True, -1.5, "gate"
        return True, -2.2, "gate"

    events = run(
        lambda _prompt: json.dumps({"mutations": [[["D", 1, "W"]]]}),
        score,
    )
    expanded = next(event for event in events if event["type"] == "expanded")
    evaluated = [event for event in events if event["type"] == "evaluated"]
    assert expanded["valid"] is True
    assert expanded["score"] == -2.2
    assert evaluated[-1]["gateScore"] == -2.2
    assert all(event["accepted"] is False for event in events if event["type"] == "merged")
    assert events[-1]["bestNodeIndex"] is None


def test_stop_ends_the_search_before_another_expansion():
    events = run(
        lambda _prompt: json.dumps({"mutations": [[["D", 1, "W"]]]}),
        lambda _payload: (True, 1.0, ""),
        expansions=4,
        stop=lambda: True,
    )
    assert events[-1]["status"] == "stopped"
    assert events[-1]["candidates"] == 0


def test_one_execution_folds_the_proposed_child_and_keeps_the_parent_backbone():
    seen = []

    def measure(payload):
        seen.append(payload)
        if payload.get("propose") is not True:
            return {
                "ok": True, "score": -1.5, "error": "gate",
                "backbone_pdb": "BACK", "sequence": payload["sequence"], "mutations": [],
            }
        return {
            "ok": True, "score": -1.1, "error": "gate",
            "backbone_pdb": "CHILD", "sequence": "AWDE", "mutations": [["D", 2, "W"]],
        }

    starting = json.loads(baseline())
    starting["skill_weights"] = {ESM_SKILL: 1.0}
    starting["router_selection_strategy"] = "weighted"
    events = []
    CdrEngine(measure=measure).run(
        spec(expansions=1, baseline_code=json.dumps(starting)),
        events.append,
        lambda: False,
    )
    assert seen[1]["propose"] is True
    assert seen[1]["skill"] == ESM_SKILL
    assert seen[1]["sequence"] == "ACDE"
    assert seen[1]["parent_backbone_pdb"] == "BACK"
    expanded = [event for event in events if event["type"] == "expanded"]
    assert expanded[0]["changeSummary"] == "D2CW"
    assert expanded[0]["score"] == -1.1
    assert expanded[0]["valid"] is True
    assert [event for event in events if event["type"] == "merged"][0]["accepted"] is False


def test_a_cycle_folds_the_generated_batch_without_proposing_again():
    seen = []

    def measure(payload):
        seen.append(payload)
        if payload.get("propose") is True:
            return {
                "ok": True, "score": -1.1, "error": "gate",
                "backbone_pdb": "CHILD", "sequence": "AWDE", "mutations": [["D", 2, "W"]],
                "portfolio": [{"sequence": "AYDE", "mutations": [["D", 2, "Y"]]}],
            }
        return {
            "ok": True, "score": -1.2, "error": "gate",
            "backbone_pdb": "NEXT", "sequence": payload["sequence"], "mutations": payload["mutations"],
        }

    starting = json.loads(baseline())
    starting["skill_weights"] = {ESM_SKILL: 1.0}
    starting["router_selection_strategy"] = "weighted"
    events = []
    CdrEngine(measure=measure).run(
        spec(expansions=2, baseline_code=json.dumps(starting)),
        events.append,
        lambda: False,
    )
    assert seen[1]["propose"] is True
    assert seen[1]["num_sequences"] == 2
    assert seen[1]["cycle"] == 0
    assert seen[1]["proposal_seed"] == 42
    assert seen[1]["excluded_sequences"] == ["ACDE"]
    assert "propose" not in seen[2] or seen[2]["propose"] is not True
    assert seen[2]["sequence"] == "AYDE"
    expanded = [event for event in events if event["type"] == "expanded"]
    assert [event["changeSummary"] for event in expanded] == ["D2CW", "D2CY"]
    assert len([item for item in seen if item.get("propose") is True]) == 1


def test_a_measured_fold_keeps_the_rest_of_the_batch():
    reading = _measure_reading(
        {
            "device": "npu",
            "ran": True,
            "score": -1.1,
            "gate_passed": False,
            "error": "gate",
            "sequence": "AWDE",
            "mutations": [["D", 2, "W"]],
            "backbone_pdb": "CHILD",
            "portfolio": [{"sequence": "AYDE", "mutations": [["D", 2, "Y"]]}],
        },
        True,
        {"score": -1.1},
        "gate",
    )
    assert reading["ok"] is True
    assert reading["score"] == -1.1
    assert reading["error"] == "gate"
    assert reading["portfolio"] == [{"sequence": "AYDE", "mutations": [["D", 2, "Y"]]}]


def test_a_width_of_one_asks_for_the_harness_batch():
    seen = []

    def measure(payload):
        seen.append(payload)
        if payload.get("propose") is True:
            return {
                "ok": True, "score": -1.1, "error": "gate",
                "sequence": "WCDE", "mutations": [["D", 1, "W"]],
            }
        return {
            "ok": True, "score": -1.5, "error": "gate",
            "backbone_pdb": "BACK", "sequence": payload["sequence"], "mutations": [],
        }

    starting = json.loads(baseline())
    starting["candidates_per_cycle"] = 1
    starting["skill_weights"] = {ESM_SKILL: 1.0}
    starting["router_selection_strategy"] = "weighted"
    CdrEngine(measure=measure).run(
        spec(expansions=3, baseline_code=json.dumps(starting)),
        lambda _event: None,
        lambda: False,
    )
    proposes = [item for item in seen if item.get("propose") is True]
    assert proposes[0]["num_sequences"] == 3


def test_chat_point_mutation_folds_without_an_npu_propose():
    seen = []

    def measure(payload):
        seen.append(payload)
        return {
            "ok": True, "score": -1.0, "error": "gate",
            "backbone_pdb": "BACK", "sequence": payload["sequence"],
            "mutations": payload.get("mutations") or [],
        }

    def complete(_prompt: str) -> str:
        return json.dumps({"mutations": [[["D", 2, "W"]], [["D", 1, "Y"]]]})

    starting = json.loads(baseline())
    starting["skill_weights"] = {POINT_SKILL: 1.0}
    starting["router_selection_strategy"] = "weighted"
    events = []
    CdrEngine(measure=measure, complete=complete).run(
        spec(expansions=2, baseline_code=json.dumps(starting)),
        events.append,
        lambda: False,
    )
    assert all(item.get("propose") is not True for item in seen[1:])
    assert [item["sequence"] for item in seen[1:]] == ["AWDE", "YCDE"]
    expanded = [event for event in events if event["type"] == "expanded"]
    assert [event["changeSummary"] for event in expanded] == ["D2CW", "D1AY"]


def test_full_redesign_keeps_only_changed_cdr_sites():
    seen = []

    def measure(payload):
        seen.append(payload)
        return {
            "ok": True, "score": -0.5, "error": "gate",
            "backbone_pdb": "BACK", "sequence": payload["sequence"],
            "mutations": payload.get("mutations") or [],
        }

    def complete(_prompt: str) -> str:
        return json.dumps({"mutations": [[
            ["D", 1, "A"], ["D", 2, "W"], ["D", 3, "D"],
        ]]})

    starting = json.loads(baseline())
    starting["skill_weights"] = {FULL_SKILL: 1.0}
    starting["router_selection_strategy"] = "weighted"
    events = []
    CdrEngine(measure=measure, complete=complete).run(
        spec(expansions=1, baseline_code=json.dumps(starting)),
        events.append,
        lambda: False,
    )
    assert seen[1]["sequence"] == "AWDE"
    expanded = next(event for event in events if event["type"] == "expanded")
    assert expanded["changeSummary"] == "D2CW"


def test_the_router_keeps_point_mutation_when_chat_is_ready():
    assert _draw_skill(42, 0, False, None, chat_ok=False) == ESM_SKILL
    assert _draw_skill(42, 0, False, {POINT_SKILL: 1.0}, chat_ok=True) == POINT_SKILL
    assert FULL_SKILL in {
        _draw_skill(42, cycle, True, None, chat_ok=True)
        for cycle in range(80)
    }
    seen = {
        _draw_skill(42, cycle, True, None, chat_ok=True)
        for cycle in range(40)
    }
    assert POINT_SKILL in seen
    assert ESM_SKILL in seen


def test_parent_select_mixes_a_uniform_fraction():
    population = [
        _Member(index=0, sequence="A", score=-3.0),
        _Member(index=1, sequence="B", score=-1.0),
    ]
    picks = {
        _select(population, 0.2, random.Random(seed), 0.1).index
        for seed in range(200)
    }
    assert picks == {0, 1}


def test_a_positive_llr_keeps_only_cdr_substitutions():
    rows = [
        {"Pos": 1, "WT": "A", "C": 0.4, "D": -0.2, "W": 1.5},
        {"Pos": 2, "WT": "C", "D": 0.9},
        {"Pos": 4, "WT": "E", "G": 3.0},
    ]
    assert positive_llr_substitutions(rows, {1, 2, 3}, "D", 2) == [
        [["D", 1, "W"]],
        [["D", 2, "D"]],
    ]


def test_temperature_anneals_geometrically_by_cycle():
    assert _temperature(0, 10) == 1.0
    assert abs(_temperature(3, 10) - 0.2 ** (3 / 9)) < 1e-9
    assert abs(_temperature(9, 10) - 0.2) < 1e-9
    assert abs(_temperature(40, 10) - 0.2) < 1e-9


def test_the_agent_route_sees_the_search_state_and_earlier_cycles():
    prompts = []
    proposals = iter([
        [[["D", 2, "W"]], [["D", 1, "Y"]]],
        [[["D", 2, "F"]], [["D", 3, "H"]]],
    ])

    def measure(payload):
        return {
            "ok": True, "score": -1.0, "error": "",
            "backbone_pdb": "BACK", "sequence": payload["sequence"],
            "mutations": payload.get("mutations") or [],
        }

    def complete(prompt: str) -> str:
        if prompt.startswith("Return plain text"):
            return "the loop tip drives contact"
        if "ROUTER-ALLOWED" in prompt:
            prompts.append(prompt)
            return json.dumps({"skill_id": POINT_SKILL, "selection_reason": "refine"})
        return json.dumps({"mutations": next(proposals)})

    events = []
    CdrEngine(measure=measure, complete=complete).run(
        spec(expansions=4, baseline_code=baseline()),
        events.append,
        lambda: False,
    )
    assert len(prompts) == 2
    assert "backend=llm; prior=" in prompts[0]
    assert "Consecutive cycles without global-best improvement: 0" in prompts[0]
    assert "HISTORICAL SKILL OUTCOMES" not in prompts[0]
    assert "Consecutive cycles without global-best improvement: 1" in prompts[1]
    assert "`cdr-point-mutation`: folded 2, admitted 2" in prompts[1]


def test_parent_select_normalises_fitness_before_the_temperature():
    population = [
        _Member(index=0, sequence="A", score=-2.2),
        _Member(index=1, sequence="B", score=-1.7),
    ]
    rng = random.Random(7)
    picks = [_select(population, 1.0, rng, 0.0).index for _ in range(4000)]
    assert 0.69 < picks.count(1) / len(picks) < 0.77


def test_a_child_is_one_level_below_its_parent():
    def measure(payload):
        if payload.get("propose") is True:
            return {
                "ok": True, "score": -1.0, "error": "", "backbone_pdb": "CHILD",
                "sequence": "AWDE", "mutations": [["D", 2, "W"]],
                "portfolio": [{"sequence": "AYDE", "mutations": [["D", 2, "Y"]]}],
            }
        return {
            "ok": True, "score": -1.0, "error": "", "backbone_pdb": "CHILD",
            "sequence": payload["sequence"], "mutations": payload["mutations"],
        }

    starting = json.loads(baseline())
    starting["skill_weights"] = {ESM_SKILL: 1.0}
    starting["router_selection_strategy"] = "weighted"
    events = []
    CdrEngine(measure=measure).run(
        spec(expansions=6, baseline_code=json.dumps(starting)),
        events.append,
        lambda: False,
    )
    depth = {0: 0}
    for event in events:
        if event["type"] == "expanded":
            assert event["depth"] == depth[event["parentIndex"]] + 1
            depth[event["nodeIndex"]] = event["depth"]
    assert max(depth.values()) >= 2


def test_a_runner_that_drops_ends_the_search_without_spending_expansions():
    calls = []

    def measure(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {"ok": True, "score": -1.5, "error": "", "sequence": payload["sequence"]}
        return {
            "ok": False,
            "error": "runner GET /shell-executions/x returned 502: Remote runner is not connected",
            "runner_lost": True,
        }

    starting = json.loads(baseline())
    starting["skill_weights"] = {ESM_SKILL: 1.0}
    starting["router_selection_strategy"] = "weighted"
    events = []
    CdrEngine(measure=measure).run(
        spec(expansions=5, baseline_code=json.dumps(starting)),
        events.append,
        lambda: False,
    )
    assert events[-1]["status"] == "failed"
    assert events[-1]["stopReason"] == "runner_lost"
    assert events[-1]["candidates"] == 0
    assert len(calls) == 2


def test_a_refused_submission_waits_for_the_runner_then_folds(monkeypatch):
    from sciencediscovery_evolve import cdr_engine, npu_job

    monkeypatch.setattr(cdr_engine, "RUNNER_RETRY_WAITS", (0, 0))
    refused = npu_job.NpuJobError(
        'runner POST /shell-executions returned 409: {"error":"the selected runner is not connected"}'
    )
    attempts = []

    class Client:
        def run(self, code, should_stop=None):
            attempts.append(code)
            if len(attempts) < 3:
                raise refused
            return {"state": "succeeded"}, {"sequence": "AWDE", "mutations": [["D", 2, "W"]]}

    monkeypatch.setattr(npu_job, "npu_client_from_spec", lambda options, timeout: Client())
    monkeypatch.setattr(npu_job, "interpret_npu_job", lambda job, artifact, metric_key: (True, {"score": -1.0}, ""))
    reading = cdr_engine._npu_measure(spec(), lambda: False)({"sequence": "ACDE"})
    assert len(attempts) == 3
    assert reading["ok"] is True
    assert "runner_lost" not in reading


def test_a_lost_status_read_is_not_resubmitted(monkeypatch):
    from sciencediscovery_evolve import cdr_engine, npu_job

    monkeypatch.setattr(cdr_engine, "RUNNER_RETRY_WAITS", (0, 0))
    attempts = []

    class Client:
        def run(self, code, should_stop=None):
            attempts.append(code)
            raise npu_job.NpuJobError("runner GET /shell-executions/x returned 502: Remote runner is not connected")

    monkeypatch.setattr(npu_job, "npu_client_from_spec", lambda options, timeout: Client())
    reading = cdr_engine._npu_measure(spec(), lambda: False)({"sequence": "ACDE"})
    assert len(attempts) == 1
    assert reading["runner_lost"] is True


def test_the_design_prompt_carries_the_fold_metrics_the_gate_and_what_was_tried():
    prompts = []

    def measure(payload):
        return {
            "ok": payload["sequence"] != "AWDE",
            "score": -1.0 if payload["sequence"] == "AWDE" else -1.4,
            "error": "cdr_fraction 0.31 below 0.5" if payload["sequence"] == "AWDE" else "",
            "sequence": payload["sequence"],
            "mutations": payload.get("mutations") or [],
            "metrics": {
                "iptm": 0.62, "plddt": 81.4, "cdr_contact_fraction": 0.31,
                "gate_passed": payload["sequence"] != "AWDE",
                "components": {"iptm": 0.9, "pae": 0.4},
            },
        }

    proposals = iter([
        [[["D", 2, "W"]], [["D", 1, "Y"]]],
        [[["D", 3, "F"]], [["D", 2, "H"]]],
    ])

    def complete(prompt: str) -> str:
        if prompt.startswith("Return plain text"):
            return "the loop tip drives contact"
        if "ROUTER-ALLOWED" in prompt:
            return json.dumps({"skill_id": POINT_SKILL, "selection_reason": "refine"})
        prompts.append(prompt)
        return json.dumps({"mutations": next(proposals)})

    events = []
    CdrEngine(measure=measure, complete=complete).run(
        spec(expansions=4, baseline_code=baseline()),
        events.append,
        lambda: False,
    )
    assert "=== OBJECTIVE ===" in prompts[0]
    assert "- iptm: 0.62" in prompts[0]
    assert "- cdr_contact_fraction: 0.31" in prompts[0]
    assert "Largest loss terms: iptm=0.9, pae=0.4" in prompts[0]
    assert "ANTIBODY HEADER / CDR MAP" in prompts[0]
    assert "the loop tip drives contact" in prompts[0]
    assert "ALREADY MEASURED" not in prompts[0]
    assert "D2CW -> score -1.0 gate: cdr_fraction 0.31 below 0.5" in prompts[1]
    assert "REFLECTION ON THE LAST CYCLE" in prompts[1]


def test_a_chat_cycle_after_a_proposed_batch_reflects_on_the_batch():
    """The NPU branch folds a whole batch; the next cycle still has to reflect."""
    skills = iter([ESM_SKILL, POINT_SKILL, POINT_SKILL])
    reflections = []

    def measure(payload):
        if payload.get("propose") is True:
            return {
                "ok": True, "score": -1.2, "error": "", "sequence": "AWDE",
                "mutations": [["D", 2, "W"]],
                "portfolio": [{"sequence": "AYDE", "mutations": [["D", 2, "Y"]]}],
            }
        return {
            "ok": True, "score": -1.1, "error": "",
            "sequence": payload["sequence"], "mutations": payload["mutations"],
        }

    def complete(prompt: str) -> str:
        if prompt.startswith("Return plain text under 80 words"):
            reflections.append(prompt)
            return "the tip helped"
        if prompt.startswith("Return plain text"):
            return "the loop tip drives contact"
        if "ROUTER-ALLOWED" in prompt:
            return json.dumps({"skill_id": next(skills), "selection_reason": "go"})
        return json.dumps({"mutations": [[["D", 3, "F"]], [["D", 1, "Y"]]]})

    events = []
    CdrEngine(measure=measure, complete=complete).run(
        spec(expansions=4, baseline_code=baseline()),
        events.append,
        lambda: False,
    )
    assert events[-1]["status"] == "succeeded"
    assert reflections, "the cycle after the proposed batch never reflected"
    assert "`esm2-guided-mutation`" in reflections[0]
    assert "D2CW -> score -1.2" in reflections[0]


def test_a_measurement_keeps_the_metrics_behind_its_score():
    reading = _measure_reading(
        {"iptm": 0.7, "plddt": 80.0, "gate_passed": True, "chatter": "drop me"},
        True,
        {"score": -1.2},
        "",
    )
    assert reading["metrics"] == {"iptm": 0.7, "plddt": 80.0, "gate_passed": True}


def test_a_starting_point_without_cdr_ranges_fails_before_a_model_call():
    events = []
    called = False

    def complete(_prompt: str) -> str:
        nonlocal called
        called = True
        return "{}"

    CdrEngine(complete=complete, score=lambda _payload: (True, 1.0, "")).run(
        spec(baseline_code=json.dumps({"sequence": "ACDE"})),
        events.append,
        lambda: False,
    )
    assert called is False
    assert events[-1]["status"] == "failed"
