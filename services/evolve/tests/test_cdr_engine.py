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

import pytest

pytestmark = pytest.mark.science_tags(category="ut", os="linux", arch=("amd64", "arm64"))

from sciencediscovery_evolve.cdr_engine import CdrEngine
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


def test_stop_ends_the_search_before_another_expansion():
    events = run(
        lambda _prompt: json.dumps({"mutations": [[["D", 1, "W"]]]}),
        lambda _payload: (True, 1.0, ""),
        expansions=4,
        stop=lambda: True,
    )
    assert events[-1]["status"] == "stopped"
    assert events[-1]["candidates"] == 0


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
