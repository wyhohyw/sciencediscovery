# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
# Licensed under the Apache License, Version 2.0 (the "License");
from __future__ import annotations

import json
import threading
import time

import pytest

from sciencediscovery_evolve import cdr_engine as cdr
from sciencediscovery_evolve import npu_job
from sciencediscovery_evolve.cdr_folds import fold_batch
from sciencediscovery_evolve.engine import RunSpec

pytestmark = pytest.mark.science_tags(category="ut", os="linux", arch=("amd64", "arm64"))


def payloads(count=4):
    return [{"sequence": str(i), "mutations": [["D", 1, "W"]]} for i in range(count)]


def test_CDR6_001_workers_overlap_with_unique_slots_and_preserve_input_order():
    barrier, second_done = threading.Barrier(2), threading.Event()
    slots, completed = set(), []
    def measure(payload):
        slots.add(payload["_device_slot"])
        barrier.wait(timeout=3)
        if payload["sequence"] == "0":
            assert second_done.wait(timeout=3)
        completed.append(payload["sequence"])
        second_done.set()
        return {"ok": True, "score": float(payload["sequence"])}
    result = fold_batch(payloads(2), measure, 2, lambda: False)
    assert result.error is None
    assert result.attempts == 2 and slots == {0, 1}
    assert completed == ["1", "0"]
    assert [reading["sequence"] for _, reading in result.results] == ["0", "1"]


@pytest.mark.parametrize("lost", [True, False])
@pytest.mark.parametrize("fatal_index", ["0", "1"])
def test_CDR6_004_runner_loss_or_exception_cancels_peers_and_leaves_queued_folds_unstarted(lost, fatal_index):
    barrier = threading.Barrier(2)
    def measure(payload):
        barrier.wait(timeout=3)
        if payload["sequence"] == fatal_index:
            if lost:
                return {"runner_lost": True, "ok": False}
            raise RuntimeError("fold failed")
        deadline = time.monotonic() + 3
        while not payload["_batch_stop"]():
            assert time.monotonic() < deadline
            time.sleep(0.005)
        return {"ok": False, "error": "cancelled"}
    result = fold_batch(payloads(6), measure, 2, lambda: False)
    assert result.attempts == 2
    if lost:
        assert any(value.get("runner_lost") for _, value in result.results)
    else:
        assert str(result.error) == "fold failed"


def test_CDR6_004_stop_does_not_dispatch_any_folds():
    result = fold_batch(payloads(), lambda _: pytest.fail("dispatched after stop"), 2, lambda: True)
    assert result.attempts == 0 and not result.results


def baseline(**extra):
    return json.dumps({"sequence": "ACDE", "target_sequence": "GG", "chain": "D", "target_chain": "A",
        "cdr_regions": [[1, 3]], "candidates_per_cycle": 2, "cycles": 1,
        "quality_check_enabled": False, "skill_weights": {cdr.POINT_SKILL: 1},
        "router_selection_strategy": "weighted", **extra})


def spec(**extra):
    return RunSpec(**{"search_id": "cdr-parallel", "algorithm": "cdr", "expansions": 2,
        "scorecard_hash": "card", "workers": 2, "baseline_code": baseline(), **extra})


def text_model(prompt):
    if prompt.startswith("Return plain text"):
        return "evidence note"
    return json.dumps({"mutations": [[["D", 2, "W"]], [["D", 3, "F"]]]})


def test_CDR6_004_parallel_commit_matches_serial_despite_out_of_order_folds():
    barrier = threading.Barrier(2)
    def run(workers):
        out = []
        def measure(payload):
            if workers == 2 and payload["sequence"] != "ACDE":
                barrier.wait(timeout=3)
            return {"ok": True, "score": -1.0 if payload["sequence"] == "AWDE" else -2.0,
                    "sequence": payload["sequence"], "mutations": payload.get("mutations", [])}
        cdr.CdrEngine(complete=text_model, measure=measure).run(spec(workers=workers), out.append, lambda: False)
        return [event for event in out if event["type"] != "log"]
    assert run(2) == run(1)


def test_CDR6_001_capacity_one_uses_serial_path_even_when_more_workers_requested():
    seen = []
    def measure(payload):
        assert "_device_slot" not in payload
        seen.append(payload["sequence"])
        return {"ok": True, "score": -1.0, "sequence": payload["sequence"]}
    measure.parallel_limit = 1
    out = []
    cdr.CdrEngine(complete=text_model, measure=measure).run(spec(), out.append, lambda: False)
    assert seen == ["ACDE", "AWDE", "ACFE"]
    assert out[-1]["candidates"] == 2


def test_CDR6_005_backend_generates_then_folds_the_whole_batch_in_parallel():
    barrier = threading.Barrier(2)
    generated, folded = [], []
    def measure(payload):
        if payload.get("propose"):
            assert payload["generation_only"] is True
            generated.append(payload)
            return {"generated": True, "portfolio": [
                {"sequence": "AWDE", "mutations": [["D", 2, "W"]]},
                {"sequence": "ACFE", "mutations": [["D", 3, "F"]]}]}
        if payload["sequence"] != "ACDE":
            barrier.wait(timeout=3)
            folded.append(payload["sequence"])
        return {"ok": True, "score": -1.0, "sequence": payload["sequence"], "mutations": payload.get("mutations", [])}
    out = []
    cdr.CdrEngine(measure=measure).run(spec(baseline_code=baseline(skill_weights={cdr.ESM_SKILL: 1})), out.append, lambda: False)
    assert len(generated) == 1 and set(folded) == {"AWDE", "ACFE"}
    assert out[-1]["status"] == "succeeded" and out[-1]["candidates"] == 2


def test_CDR6_002_measure_strips_internal_controls_and_forwards_only_the_slot(monkeypatch):
    seen = []
    class Client:
        transport = "api"
        def cdr_capacity(self): return 2
        def run(self, code, should_stop, **kwargs):
            seen.append((json.loads(code), kwargs))
            return {"status": "completed"}, {"device": "npu", "ran": True, "score": 1.0}
    monkeypatch.setattr(npu_job, "npu_client_from_spec", lambda *args: Client())
    measure = cdr._npu_measure(spec(), lambda: False)
    assert measure.parallel_limit == 2
    assert measure({"sequence": "AWDE", "_device_slot": 1, "_batch_stop": lambda: False})["ok"]
    assert seen == [({"sequence": "AWDE"}, {"device_slot": 1})]


def test_CDR6_005_measure_generation_is_not_a_fold_score(monkeypatch):
    class Client:
        def run(self, *args):
            return {"status": "completed"}, {"device": "npu", "proposal": "generated", "ran": False,
                "portfolio": [{"sequence": "AWDE", "mutations": [["D", 2, "W"]]}]}
    monkeypatch.setattr(npu_job, "npu_client_from_spec", lambda *args: Client())
    result = cdr._npu_measure(spec(), lambda: False)({"propose": True})
    assert result["generated"] is True and "score" not in result
    assert cdr._pending_folds(result) == [{"sequence": "AWDE", "mutations": [["D", 2, "W"]]}]


def test_CDR6_004_parallel_quality_retry_commits_once_and_models_stay_on_the_main_thread():
    barrier, main_thread = threading.Barrier(2), threading.get_ident()
    folded, quality, out = [], [], []
    def measure(payload):
        if payload["sequence"] != "ACDE":
            assert threading.get_ident() != main_thread
            barrier.wait(timeout=3)
            folded.append(payload["sequence"])
        return {"ok": True, "score": -1.0, "sequence": payload["sequence"],
                "mutations": payload.get("mutations", []), "metrics": {"iptm": 0.9}}
    def complete(prompt):
        assert threading.get_ident() == main_thread
        if "Assess the supplied antibody candidates" in prompt:
            assert not any(event["type"] == "expanded" for event in out)
            quality.append(True)
            if len(quality) == 1:
                return '{"results": {}}'
            return json.dumps({"results": {key: {"pass_check": True} for key in ["c0", "c1"]}})
        return text_model(prompt)
    cdr.CdrEngine(complete=complete, measure=measure).run(spec(
        baseline_code=baseline(quality_check_enabled=True)), out.append, lambda: False)
    assert len(quality) == 2 and folded.count("AWDE") == folded.count("ACFE") == 2
    assert out[-1]["candidates"] == 2 and out[-1]["status"] == "succeeded"
    assert len([event for event in out if event["type"] == "expanded"]) == 2


def test_CDR6_004_stop_during_parallel_folds_never_commits_or_dispatches_the_rest():
    stop, barrier = threading.Event(), threading.Barrier(2)
    started, out = [], []
    def complete(prompt):
        if prompt.startswith("Return plain text"):
            return "analysis"
        assert not stop.is_set(), "model called after stop"
        return json.dumps({"mutations": [[["D", 1, "W"]], [["D", 2, "W"]],
                                        [["D", 3, "F"]], [["D", 1, "F"]]]})
    def measure(payload):
        if payload["sequence"] != "ACDE":
            started.append(payload["sequence"])
            barrier.wait(timeout=3)
            stop.set()
        return {"ok": True, "score": -1.0, "sequence": payload["sequence"]}
    cdr.CdrEngine(complete=complete, measure=measure).run(spec(expansions=4,
        baseline_code=baseline(candidates_per_cycle=4)), out.append, stop.is_set)
    assert len(started) == 2
    assert out[-1]["status"] == "stopped" and out[-1]["candidates"] == 0
    assert not any(event["type"] == "expanded" for event in out)


def test_CDR6_004_runner_loss_wins_over_a_cancelled_peers_exception_without_retry():
    barrier = threading.Barrier(2)
    started, out = [], []
    def measure(payload):
        if payload["sequence"] == "ACDE":
            return {"ok": True, "score": -1.0, "sequence": "ACDE"}
        started.append(payload["sequence"])
        barrier.wait(timeout=3)
        if payload["sequence"] == "AWDE":
            return {"runner_lost": True, "ok": False, "error": "runner absent"}
        deadline = time.monotonic() + 3
        while not payload["_batch_stop"]():
            assert time.monotonic() < deadline
            time.sleep(0.005)
        raise RuntimeError("peer cancellation transport failed")
    cdr.CdrEngine(complete=text_model, measure=measure).run(spec(), out.append, lambda: False)
    assert len(started) == 2 and out[-1]["status"] == "failed"
    assert out[-1]["stopReason"] == "runner_lost" and out[-1]["candidates"] == 0


def test_CDR6_003_publication_transport_failure_never_replays_an_already_completed_fold(monkeypatch):
    calls = []
    class Client:
        transport = "api"
        def cdr_capacity(self): return 2
        def run(self, *args, **kwargs):
            calls.append(True)
            raise npu_job.NpuJobError("runner POST /shell-executions/evolve-one/publish?sessionId=session failed: reset")
    monkeypatch.setattr(npu_job, "npu_client_from_spec", lambda *args: Client())
    monkeypatch.setattr(cdr, "_pause", lambda *args: pytest.fail("publication failure replayed a physical fold"))
    result = cdr._npu_measure(spec(), lambda: False)({"sequence": "AWDE"})
    assert result["runner_lost"] is True and len(calls) == 1


def test_CDR6_004_unknown_execution_stops_dispatch_instead_of_becoming_an_ordinary_bad_score(monkeypatch):
    class Client:
        def run(self, *args): return {"status": "unknown"}, None
    monkeypatch.setattr(npu_job, "npu_client_from_spec", lambda *args: Client())
    result = cdr._npu_measure(spec(), lambda: False)({"sequence": "AWDE"})
    assert result["runner_lost"] is True and "score" not in result


def test_CDR6_004_user_stop_wins_over_a_peers_exception_even_with_no_cycle_retries():
    stop, barrier, out = threading.Event(), threading.Barrier(2), []
    def measure(payload):
        if payload["sequence"] == "ACDE":
            return {"ok": True, "score": -1.0, "sequence": "ACDE"}
        barrier.wait(timeout=3)
        stop.set()
        raise RuntimeError("cancelled fold transport")
    cdr.CdrEngine(complete=text_model, measure=measure).run(spec(
        baseline_code=baseline(cycle_retry_limit=0, skip_failed_cycles=False)), out.append, stop.is_set)
    assert out[-1]["status"] == "stopped" and out[-1]["candidates"] == 0
