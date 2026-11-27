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

"""The NPU probe decides which skills a CDR search may draw, and says why not."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.science_tags(category="ut", os="linux", arch=("amd64", "arm64"))

from sciencediscovery_evolve.capabilities import ESM_SKILL, MPNN_SKILL, assess
from sciencediscovery_evolve.cdr_engine import (
    DEFAULT_SKILL_WEIGHTS,
    FULL_SKILL,
    POINT_SKILL,
    _apply_preflight,
    _Refusal,
)


def _ok(detail: str = "present") -> dict:
    return {"ok": True, "detail": detail}


def _all_ok() -> dict:
    return {
        "ran": True,
        "device": "npu",
        "checks": {
            "npu": _ok("8 device(s)"),
            "rdkit": _ok(),
            "biotite": _ok(),
            "fold_code": _ok(),
            "fold_checkpoint": _ok(),
            "esm_module": _ok("/env/esm/__init__.py"),
            "esm_weights": _ok(),
            "harness_modules": _ok(),
            "soluble_weights": _ok(),
            "ligand_code": _ok(),
        },
        "installs": [],
    }


def _with(report: dict, name: str, ok: bool, detail: str = "") -> dict:
    report["checks"][name] = {"ok": ok, "detail": detail}
    return report


def _collector() -> tuple[list, callable]:
    seen: list = []
    return seen, seen.append


def test_a_complete_host_offers_every_skill():
    result = assess(_all_ok())

    assert result.fold_ok is True
    assert result.unavailable == {}
    assert result.notes == ()


def test_missing_esm_weights_refuse_the_shared_objective_with_the_reason():
    report = _with(_all_ok(), "esm_weights", False, "nothing at /weights/esm2/config.json")

    result = assess(report)

    assert result.fold_ok is False
    assert set(result.unavailable) == {ESM_SKILL}
    assert "esm_weights: nothing at /weights/esm2/config.json" in result.unavailable[ESM_SKILL]


def test_missing_soluble_weights_turn_off_only_the_inverse_folding_skill():
    report = _with(_all_ok(), "soluble_weights", False, "nothing at solublempnn.pt")

    result = assess(report)

    assert set(result.unavailable) == {MPNN_SKILL}


def test_a_missing_fold_checkpoint_refuses_the_whole_search_with_its_reason():
    report = _with(_all_ok(), "fold_checkpoint", False, "nothing at opendde_abag.pt")

    result = assess(report)

    assert result.fold_ok is False
    assert "fold_checkpoint: nothing at opendde_abag.pt" in result.fold_reason


def test_a_probe_that_never_reported_counts_as_nothing_available():
    result = assess(None)

    assert result.fold_ok is False
    assert "no report" in result.fold_reason
    assert set(result.unavailable) == {ESM_SKILL, MPNN_SKILL}


def test_a_probe_error_is_carried_as_the_fold_reason():
    result = assess({"ran": True, "checks": {}, "error": "runner refused the probe"})

    assert result.fold_ok is False
    assert result.fold_reason == "runner refused the probe"


def test_an_auto_install_is_reported_as_a_note_with_its_outcome():
    report = _all_ok()
    report["installs"] = [
        {"component": "esm_module", "ok": False, "detail": "ERROR: no matching distribution"},
        {"component": "esm_weights", "ok": True, "detail": "/data1/gs_agent/sciencediscovery-deps/weights/esm2"},
    ]

    result = assess(report)

    assert result.notes == (
        "auto-install esm_module: failed: ERROR: no matching distribution",
        "auto-install esm_weights: ok",
    )


def test_a_missing_rdkit_or_biotite_refuses_the_fold():
    report = _with(_all_ok(), "rdkit", False, "No module named 'rdkit'")

    result = assess(report)

    assert result.fold_ok is False
    assert "rdkit: No module named 'rdkit'" in result.fold_reason


def test_preflight_switches_off_an_unavailable_skill_and_logs_why():
    seen, emit = _collector()
    report = _with(_all_ok(), "soluble_weights", False, "nothing at solublempnn.pt")

    config = _apply_preflight({"skill_weights": None}, report, True, emit)

    assert config["skill_weights"][MPNN_SKILL] == 0.0
    assert config["skill_weights"][ESM_SKILL] == DEFAULT_SKILL_WEIGHTS[ESM_SKILL]
    assert any("skill antibody-inverse-folding is off" in str(event) for event in seen)


def test_preflight_refuses_when_the_fold_cannot_run():
    report = _with(_all_ok(), "npu", False, "torch_npu sees no NPU device")

    with pytest.raises(_Refusal, match="cannot fold a candidate: npu: torch_npu sees no NPU device"):
        _apply_preflight({"skill_weights": None}, report, True, _collector()[1])


def test_preflight_refuses_when_no_skill_can_run_without_the_llm():
    report = _with(_all_ok(), "esm_weights", False, "no esm")
    report = _with(report, "soluble_weights", False, "no soluble")

    with pytest.raises(_Refusal, match="cannot fold a candidate"):
        _apply_preflight({"skill_weights": None}, report, False, _collector()[1])


def test_the_llm_cannot_bypass_a_missing_required_esm_objective():
    report = _with(_all_ok(), "esm_weights", False, "no esm")
    report = _with(report, "soluble_weights", False, "no soluble")

    with pytest.raises(_Refusal, match="cannot fold a candidate: esm_weights"):
        _apply_preflight({"skill_weights": None}, report, True, _collector()[1])
