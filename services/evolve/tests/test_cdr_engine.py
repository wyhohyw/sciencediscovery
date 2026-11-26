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
