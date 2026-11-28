# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
# Licensed under the Apache License, Version 2.0 (the "License");
# you may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
"""CDR4 regressions: cycle transactions, role tools and the actual model wire."""
from __future__ import annotations

import copy
import io
import json
import random
import re
from types import SimpleNamespace

import pytest

from sciencediscovery_evolve import cdr_engine as cdr
from sciencediscovery_evolve.cdr_phases import AnalyzeOutput, CdrPhaseRunner, PhaseError
from sciencediscovery_evolve.cdr_tools import CdrTools
from sciencediscovery_evolve.completion import completion_for
from sciencediscovery_evolve.engine import RunSpec

pytestmark = pytest.mark.science_tags(category="ut", os="linux", arch=("amd64", "arm64"))


def baseline(**overrides):
    return json.dumps({"sequence": "ACDE", "target_sequence": "GG", "chain": "D", "target_chain": "A",
        "cdr_regions": [[1, 3]], "candidates_per_cycle": 2, "cycles": 2,
        "skill_weights": {cdr.POINT_SKILL: 1}, "router_selection_strategy": "weighted", **overrides})


def spec(**overrides):
    return RunSpec(**{"search_id": "cdr-v4", "algorithm": "cdr", "expansions": 2,
        "scorecard_hash": "card", "baseline_code": baseline(), **overrides})


def text_model(prompt):
    if prompt.startswith("Return plain text"):
        return "evidence bounded note"
    return json.dumps({"mutations": [[["D", 2, "W"]], [["D", 3, "F"]]]})


def reading(payload, **extra):
    return {"ok": True, "score": -1.0, "sequence": payload["sequence"],
            "mutations": payload.get("mutations", []), "error": "", **extra}


def test_CDR4_004_batch_reconsiders_old_survivors_after_a_better_candidate(monkeypatch):
    sequence = "A" * 40 + "G"
    snapshots = []
    original = cdr._retain

    def retain(members, config, reference):
        result = original(members, config, reference)
        snapshots.append((set(m.index for m in members), result[0]))
        return result

    monkeypatch.setattr(cdr, "_retain", retain)

    def complete(prompt):
        if prompt.startswith("Return plain text"):
            return "note"
        return json.dumps({"mutations": [[["D", 1, "C"]], [["D", 1, "C"], ["D", 2, "D"]]]})

    def measure(payload):
        return reading(payload, score=float(sum(a != b for a, b in zip(sequence, payload["sequence"]))))

    out = []
    cdr.CdrEngine(complete=complete, measure=measure).run(spec(baseline_code=baseline(
        sequence=sequence, cdr_regions=[[1, 40]], cycles=1)), out.append, lambda: False)
    assert ({0, 1, 2}, {0, 2}) in snapshots
    assert [e["accepted"] for e in out if e["type"] == "merged"] == [False, True]


def test_CDR4_004_reference_is_the_best_first_admitted_not_the_failed_seed(monkeypatch):
    references = []
    original = cdr._retain

    def retain(members, config, reference):
        references.append(reference)
        return original(members, config, reference)

    monkeypatch.setattr(cdr, "_retain", retain)
    def measure(payload):
        return reading(payload, score=-0.5 if payload["sequence"] == "ACFE" else -1,
                       error="gate" if payload["sequence"] == "ACDE" else "")
    cdr.CdrEngine(complete=text_model, measure=measure).run(spec(), lambda e: None, lambda: False)
    assert references[0] == ["ACF"]


def test_CDR4_005_preserves_configuration_and_zero_uniform_fraction():
    values = {"population_size": 3, "constrained_min_cdr_distance": 0.2,
        "constrained_max_position_reuse_fraction": 0.4, "constrained_max_mutation_reuse_fraction": 0.2,
        "quality_check_enabled": False, "quality_check_threshold": 0.9, "cycle_retry_limit": 4,
        "parent_fitness_uniform_fraction": 0, "parent_selection_strategy": "uniform", "seed": 701}
    config = cdr._config(baseline(**values))
    assert {k: config[k] for k in values} == values


@pytest.mark.parametrize("key,value", [("population_size", 0), ("cycle_retry_limit", 6),
    ("constrained_min_cdr_distance", float("nan")), ("quality_check_enabled", "false"), ("seed", True)])
def test_CDR4_005_rejects_invalid_configuration(key, value):
    with pytest.raises(cdr._Refusal):
        cdr._config(baseline(**{key: value}))


def test_CDR4_005_stage_overrides_expire_and_do_not_mutate_defaults():
    config = cdr._config(baseline(cycles=3, cycle_schedule=[{"start_cycle": 1, "end_cycle": 1,
        "num_sequences": 3, "population_size": 2, "parent_fitness_temperature": 0.4}]))
    assert cdr._cycle_config(config, 1)["population_size"] == 2
    assert cdr._cycle_config(config, 1)["candidates_per_cycle"] == 3
    assert cdr._cycle_config(config, 2)["population_size"] == 20
    assert config["candidates_per_cycle"] == 2


def test_CDR4_005_generation_and_fold_use_the_run_seed():
    seen = []
    def measure(payload):
        seen.append(payload)
        return reading(payload, **({"sequence": "AWDE", "mutations": [["D", 2, "W"]]} if payload.get("propose") else {}))
    cdr.CdrEngine(measure=measure).run(spec(expansions=1, options={"seed": 701},
        baseline_code=baseline(skill_weights={cdr.ESM_SKILL: 1})), lambda e: None, lambda: False)
    assert seen[0]["seed"] == 701
    assert seen[1]["proposal_seed"] == 701


def test_CDR4_006_quality_failure_retries_without_committing_or_excluding_the_batch():
    folds, quality, out = [], [], []
    def measure(payload):
        folds.append(payload["sequence"])
        return reading(payload, metrics={"iptm": 0.9})
    def complete(prompt):
        if "Assess the supplied antibody candidates" in prompt:
            quality.append(1)
            if len(quality) == 1:
                return '{"results": {}}'
            return json.dumps({"results": {"c0": {"pass_check": True}, "c1": {"pass_check": True}}})
        return text_model(prompt)
    cdr.CdrEngine(complete=complete, measure=measure).run(spec(), out.append, lambda: False)
    assert folds == ["ACDE", "AWDE", "ACFE", "AWDE", "ACFE"]
    assert out[-1]["candidates"] == 2
    assert len([e for e in out if e["type"] == "expanded"]) == 2


def test_CDR4_006_exhausted_cycles_do_not_invent_folded_candidates():
    def complete(prompt):
        return "note" if prompt.startswith("Return plain text") else "not JSON"
    out = []
    cdr.CdrEngine(complete=complete, measure=reading).run(spec(), out.append, lambda: False)
    assert out[-1]["candidates"] == 0
    assert not any(e["type"] == "expanded" for e in out)
    assert len([e for e in out if e["type"] == "log" and "skipped" in e["message"]]) == 2


def tool_call(name, arguments=None, call_id="t1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {})}}


def test_CDR4_001_real_transport_preserves_roles_tools_and_provider_fields(monkeypatch):
    requests, usage = [], []
    responses = iter([
        {"role": "assistant", "content": None, "tool_calls": [tool_call("analyze_evolution_tree")], "response_items": [{"type": "opaque"}]},
        {"role": "assistant", "content": '{"downstream_header":"grounded"}'},
    ])
    def respond(request, **kwargs):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"choices": [{"message": next(responses), "finish_reason": "stop"}],
            "usage": {"total_tokens": 5, "completion_tokens": 2}}).encode())
    monkeypatch.setattr("urllib.request.urlopen", respond)
    complete = completion_for("http://fixture.invalid", "run-token", on_usage=usage.append)
    config = cdr._config(baseline())
    tools = CdrTools(config, lambda p: {"tool_result": {"available": True, "candidate_count": 0}})
    phase = CdrPhaseRunner(complete.chat, tools, config, should_stop=lambda: False, emit=lambda e: None)
    result = phase.run("reflect", "only supplied evidence", AnalyzeOutput)
    assert result["downstream_header"] == "grounded"
    assert requests[0]["messages"][0]["role"] == "system"
    assert "evolutionary-analysis" in requests[0]["messages"][0]["content"]
    assert requests[1]["messages"][2]["response_items"] == [{"type": "opaque"}]
    assert requests[1]["messages"][3]["role"] == "tool"
    assert requests[1]["messages"][3]["tool_call_id"] == "t1"
    assert sum(u.total for u in usage) == 10


def test_CDR4_001_truncated_tools_are_never_executed(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(json.dumps({"choices": [{
        "message": {"tool_calls": [tool_call("analyze_evolution_tree")]}, "finish_reason": "length"}]}).encode()))
    tools = CdrTools(cdr._config(baseline()), lambda p: pytest.fail("truncated tool was executed"))
    phase = CdrPhaseRunner(completion_for("http://fixture.invalid", "run").chat, tools,
        tools.config, should_stop=lambda: False, emit=lambda e: None)
    with pytest.raises(PhaseError, match="truncated"):
        phase.run("reflect", "test", AnalyzeOutput)


def test_CDR4_002_reflection_tool_failure_is_explicit_and_tree_is_called_once():
    calls, snapshots = [], []
    def chat(messages, definitions, **kwargs):
        snapshots.append(copy.deepcopy(messages))
        if len(snapshots) <= 2:
            return {"role": "assistant", "content": None, "tool_calls": [tool_call("analyze_evolution_tree", call_id=str(len(snapshots)))]}
        return {"content": '{"downstream_header":"uncertain"}'}
    def measure(payload):
        calls.append(payload)
        return {"tool_result": {"available": False, "reason": "backend unavailable"}}
    tools = CdrTools(cdr._config(baseline()), measure)
    phase = CdrPhaseRunner(chat, tools, tools.config, should_stop=lambda: False, emit=lambda e: None)
    assert phase.run("reflect", "test", AnalyzeOutput)["downstream_header"] == "uncertain"
    assert len(calls) == 1
    assert "once per reflection" in snapshots[2][-1]["content"]


def test_CDR4_002_stop_prevents_further_tools_and_model_calls():
    phase = CdrPhaseRunner(lambda *a, **kw: pytest.fail("called after stop"), None, {},
        should_stop=lambda: True, emit=lambda e: None)
    with pytest.raises(PhaseError, match="stopped"):
        phase.run("design", "test")


def test_CDR4_003_tools_reject_paths_unknown_ids_and_wrong_phase_candidates():
    tools = CdrTools(cdr._config(baseline()), lambda p: pytest.fail("unbound RPC"))
    tools.current = [{"candidate_id": "node1", "sequence": "AWDE", "structure_id": "a" * 64}]
    with pytest.raises(ValueError):
        tools.execute("analyze_epitope", {"candidate_ids": ["other"]})
    with pytest.raises(ValueError):
        tools.execute("analyze_epitope", {"candidate_ids": ["node1"], "structure_path": "/etc/passwd"})
    with pytest.raises(ValueError):
        tools.execute("check_antibody_developability", {"candidate_ids": ["node1"]})


def test_CDR4_003_complete_structure_and_confidence_outlive_the_fold_directory(tmp_path, monkeypatch):
    from test_cdr_score import candidate, confidence, load_evaluator, structure
    module = load_evaluator()
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "temporary-fold"
    source.mkdir()
    path = source / "complex.pdb"
    text = structure(10)
    path.write_text(text)
    summary, full = confidence()
    identifier = module.save_structure(candidate(), path, summary, full)
    path.unlink()
    source.rmdir()
    directory = module.artifact_directory(identifier)
    assert (directory / "complex.pdb").read_text() == text
    assert json.loads((directory / "full.json").read_text()) == full
    assert json.loads((directory / "manifest.json").read_text())["target_sequence"] == "A"
    with pytest.raises(ValueError):
        module.artifact_directory("../../outside")


def test_CDR4_007_a_failed_pll_never_returns_a_structure_only_score(tmp_path, monkeypatch):
    from test_cdr_score import candidate, confidence, load_evaluator, structure
    module = load_evaluator()
    monkeypatch.chdir(tmp_path)
    checkpoint = tmp_path / "weights.pt"
    checkpoint.touch()
    monkeypatch.setattr(module, "_paths", lambda: {"fold_code": str(tmp_path), "fold_checkpoint": str(checkpoint)})
    monkeypatch.setattr(module, "_child_env", lambda: {})
    def run(command, **kwargs):
        output = module.Path(command[command.index("-o") + 1])
        output.mkdir()
        summary, full = confidence()
        (output / "summary_confidence_sample_0.json").write_text(json.dumps(summary))
        (output / "full_data_sample_0.json").write_text(json.dumps(full))
        # load_atoms accepts this fixture when its loader is supplied below.
        (output / "complex_sample_0.cif").write_text("complete complex fixture")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "load_atoms", lambda p: module.pdb_atoms(structure(10)))
    monkeypatch.setattr(module, "esm_pll", lambda seq: (_ for _ in ()).throw(RuntimeError("ESM unavailable")))
    with pytest.raises(RuntimeError, match="ESM unavailable"):
        module.predict(candidate())
    assert list((tmp_path / ".evolve/cdr-artifacts").glob("*/full.json"))


def test_CDR4_003_remote_adapter_reads_the_retained_complex_after_fold_cleanup(tmp_path, monkeypatch):
    import sys
    from test_cdr_score import candidate, confidence, load_evaluator, structure
    module = load_evaluator()
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "temporary.pdb"
    source.write_text(structure(10))
    summary, full = confidence()
    identifier = module.save_structure(candidate(), source, summary, full)
    source.unlink()
    backend = tmp_path / "harness/opendde_harness/plugin/protein_design/servers/backends"
    backend.mkdir(parents=True)
    (backend / "epitope_analysis.py").write_text(
        'from pathlib import Path\n'
        'def load_structure(path): return path\n'
        'def analyze_epitope(path, antibody_chains, antigen_chains, cdr_regions, hotspots):\n'
        '    assert "ATOM" in Path(path).read_text()\n'
        '    assert antibody_chains == ["D"] and antigen_chains == ["A"]\n'
        '    assert cdr_regions == {"D": {"CDR1": {9,10,11}}}\n'
        '    assert hotspots == [("A", 1)]\n'
        '    return {"fixture_checked": True}\n')
    core = tmp_path / "harness/opendde_harness/plugin/protein_design/core"
    core.mkdir()
    (core / "gate.py").write_text(
        'def sequence_hotspot_residue_ids(model, hotspots, chains):\n'
        '    assert hotspots == {("A",0)} and chains == ["A"]\n'
        '    return {("A",1)}\n')
    monkeypatch.setattr(module, "_model_python", lambda: sys.executable)
    monkeypatch.setattr(module, "_child_env", lambda: {"OPENDDE_HARNESS_DIR": str(tmp_path / "harness")})
    result = module.analyze_tool({**candidate(), "analysis_tool": "analyze_epitope", "arguments": {},
        "candidates": [{"candidate_id": "node1", "structure_id": identifier, "sequence": candidate()["sequence"]}],
        "tool_timeout_seconds": 10})
    assert result["results"]["node1"]["fixture_checked"] is True


def reflection_fixture():
    evidence = {"available": False, "summary": "not measured"}
    return {"performance_summary": {"parent": {"candidate_id": "node0"},
        "improvement_counts": {"iptm": 0, "plddt": 0, "ranking_score": 0}, "observation": "fixture"},
        "structure_and_epitope": {"available": False, "summary": "no structure evidence"},
        "sequence_plausibility": evidence, "developability": evidence,
        "search_trajectory_analysis": {"available": True, "summary": "measured lineage"},
        "evolutionary_tree_analysis": evidence,
        "next_cycle_recommendations": [{"priority": 1, "cdr": "CDR1", "action": "preserve W2",
            "evidence_basis": "node1 measured loss", "confidence": "low"}]}


def test_CDR4_002_structured_reflection_is_used_by_the_next_design():
    designs, calls = [], []
    def chat(messages, definitions, **kwargs):
        system, prompt = messages[0]["content"], messages[1]["content"]
        calls.append(system)
        if "You are an antibody engineering analyst" in system:
            result = {"downstream_header": "verified configured CDRs"}
        elif "You are the Antibody Reflection Agent" in system:
            result = reflection_fixture()
        else:
            designs.append(prompt)
            result = {"mutations": [[["D", 2, "W"]]] if len(designs) == 1 else [[["D", 1, "Y"]]]}
        return {"role": "assistant", "content": json.dumps(result)}
    def complete(_prompt):
        pytest.fail("production phase fell back to a single user string")
    complete.chat = chat
    out = []
    cdr.CdrEngine(complete=complete, measure=reading).run(spec(baseline_code=baseline(
        reflection_interval=1, parent_selection_strategy="greedy")), out.append, lambda: False)
    assert out[-1]["status"] == "succeeded"
    assert out[-1]["candidates"] == 2
    assert "verified configured CDRs" in designs[0]
    assert "preserve W2" in designs[1]
    assert sum("You are the Antibody Reflection Agent" in s for s in calls) == 2


def test_CDR4_006_all_fold_failures_fail_the_run_after_retries():
    def measure(payload):
        return reading(payload) if payload["sequence"] == "ACDE" else {"ok": False, "error": "ESM unavailable"}
    out = []
    cdr.CdrEngine(complete=text_model, measure=measure).run(spec(), out.append, lambda: False)
    assert out[-1]["status"] == "failed"
    assert out[-1]["candidates"] == 0


def test_CDR4_003_tool_sequence_coordinates_are_projected_without_changing_structure_ids():
    from sciencediscovery_evolve.cdr_tools import external_coordinates
    evidence = {"cdr_regions": {"D": {"CDR1": [0, 1, 2]}},
        "contacts": [{"sequence_position": 0, "residue_id": 101, "distance": 4.5}]}
    result = external_coordinates(evidence)
    assert result["cdr_regions"]["D"]["CDR1"] == [1, 2, 3]
    assert result["contacts"][0] == {"sequence_position": 1, "residue_id": 101, "distance": 4.5}


def test_CDR4_003_an_analysis_job_without_artifact_is_unavailable_evidence_not_a_dead_search():
    """Observed live: a protrek analysis job returned no artifact and ended the whole search."""
    tools = CdrTools(cdr._config(baseline()),
                     lambda p: {"error": "analysis job failed returned no artifact: runner detail"})
    result = tools.execute("protrek_sequence_search", {})
    assert result["available"] is False
    assert "returned no artifact" in result["reason"]
    assert tools.runner_lost is False


def test_CDR4_002_phase_token_budget_prevents_tool_execution():
    tools = CdrTools(cdr._config(baseline()), lambda p: pytest.fail("overspent phase executed tool"))
    phase = CdrPhaseRunner(lambda *a, **kw: {"_usage": {"total_tokens": 10},
        "tool_calls": [tool_call("analyze_evolution_tree")]}, tools, {"phase_token_budget": 5},
        should_stop=lambda: False, emit=lambda e: None)
    with pytest.raises(PhaseError, match="token budget"):
        phase.run("reflect", "test", AnalyzeOutput)


def test_CDR4_002_a_prose_answer_is_asked_once_more_before_the_phase_fails():
    """Observed live: analyze answered in prose after a ProTrek result and ended the search."""
    prompts_seen = []
    answers = iter(["ProTrek shows three homologs worth keeping.", '{"downstream_header":"recovered"}'])

    def chat(messages, definitions, **kwargs):
        prompts_seen.append(messages[-1]["content"])
        return {"content": next(answers)}

    tools = CdrTools(cdr._config(baseline()), lambda p: pytest.fail("no tool is needed here"))
    phase = CdrPhaseRunner(chat, tools, tools.config, should_stop=lambda: False, emit=lambda e: None)
    assert phase.run("analyze", "test", AnalyzeOutput)["downstream_header"] == "recovered"
    assert "JSON object the schema describes" in prompts_seen[1]


def test_CDR4_002_a_phase_that_never_answers_in_json_still_fails_with_the_text():
    tools = CdrTools(cdr._config(baseline()), lambda p: pytest.fail("no tool is needed here"))
    phase = CdrPhaseRunner(lambda *a, **kw: {"content": "still prose"}, tools, tools.config,
        should_stop=lambda: False, emit=lambda e: None)
    with pytest.raises(PhaseError, match="still prose"):
        phase.run("analyze", "test", AnalyzeOutput)


def test_CDR4_002_wrong_role_tools_are_not_dispatched():
    tools = CdrTools(cdr._config(baseline()), lambda p: pytest.fail("wrong-role tool dispatched"))
    phase = CdrPhaseRunner(lambda *a, **kw: {"tool_calls": [tool_call("analyze_evolution_tree")]},
        tools, tools.config, should_stop=lambda: False, emit=lambda e: None)
    with pytest.raises(PhaseError, match="not allowed in analyze"):
        phase.run("analyze", "test", AnalyzeOutput)


def test_CDR4_002_a_rejected_tool_call_is_answered_instead_of_ending_the_phase():
    # Every harness tool answers a bad call with its error text. An analyze phase
    # that asked for structures with an empty candidate_ids list used to take the
    # whole search down with the validation error.
    answers = []
    def chat(messages, definitions, **kwargs):
        if messages[-1]["role"] == "tool":
            answers.append(json.loads(messages[-1]["content"]))
            return {"content": '{"downstream_header":"recovered"}'}
        return {"tool_calls": [tool_call("protrek_structure_search", {"candidate_ids": []})]}
    tools = CdrTools(cdr._config(baseline()), lambda p: pytest.fail("a rejected call must not reach the backend"))
    phase = CdrPhaseRunner(chat, tools, tools.config, should_stop=lambda: False, emit=lambda e: None)
    assert phase.run("analyze", "test", AnalyzeOutput)["downstream_header"] == "recovered"
    assert answers[0]["available"] is False
    assert "candidate_ids" in answers[0]["reason"]


def test_CDR4_002_a_tool_loop_stops_at_the_harness_role_limit():
    dispatched, turns = [], []
    tools = CdrTools(cdr._config(baseline()), lambda p: dispatched.append(p) or {"tool_result": {"available": False}})
    def chat(messages, definitions, **kwargs):
        turns.append(definitions)
        return {"tool_calls": [tool_call("protrek_sequence_search", {"entity": "target"}, str(len(turns)))]}
    phase = CdrPhaseRunner(chat, tools, tools.config, should_stop=lambda: False, emit=lambda e: None)
    with pytest.raises(PhaseError, match="tool-turn limit"):
        phase.run("analyze", "test", AnalyzeOutput)
    assert len(dispatched) == 4
    assert turns[-1] == []


def test_CDR4_001_http_round_trip_carries_tool_receipts_and_sums_usage():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    bodies, costs = [], []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            bodies.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            message = ({"role": "assistant", "tool_calls": [tool_call("protrek_sequence_search", {"entity": "target"})]}
                       if len(bodies) == 1 else {"role": "assistant", "content": '{"downstream_header":"verified"}'})
            payload = json.dumps({"choices": [{"message": message}],
                "usage": {"total_tokens": 7, "completion_tokens": 3}}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        complete = completion_for(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "fixture-run", on_usage=costs.append)
        tools = CdrTools(cdr._config(baseline()), lambda p: {"tool_result": {"available": False, "reason": "not configured"}})
        runner = CdrPhaseRunner(complete.chat, tools, tools.config, should_stop=lambda: False, emit=lambda e: None)
        assert runner.run("analyze", "test", AnalyzeOutput)["downstream_header"] == "verified"
        assert bodies[1]["messages"][-1]["role"] == "tool"
        assert json.loads(bodies[1]["messages"][-1]["content"])["available"] is False
        assert sum(cost.total for cost in costs) == 14
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_CDR4_002_production_quality_role_uses_stub_evidence_and_commits_the_batch():
    receipts = []
    def chat(messages, definitions, **kwargs):
        system = messages[0]["content"]
        if "You are an antibody engineering analyst" in system:
            result = {"downstream_header": "CDR1 configured"}
        elif "You are the Antibody Quality Agent" in system:
            if messages[-1]["role"] != "tool":
                return {"tool_calls": [tool_call("check_antibody_developability", {"candidate_ids": ["c0", "c1"]})]}
            receipts.append(json.loads(messages[-1]["content"]))
            result = {"results": {key: {"pass_check": True, "reasoning": "sequence-only check",
                "overall_risk_level": "unknown"} for key in ("c0", "c1")}}
        else:
            result = {"mutations": [[["D", 2, "W"]], [["D", 3, "F"]]]}
        return {"content": json.dumps(result)}
    complete = lambda p: pytest.fail("text-only model path")
    complete.chat = chat
    out = []
    cdr.CdrEngine(complete=complete, measure=lambda p: reading(p, metrics={"iptm": 0.9})).run(
        spec(), out.append, lambda: False)
    assert out[-1]["candidates"] == 2
    assert receipts == [{"summary": {"c0": {"available": False}, "c1": {"available": False}},
                         "results": {"c0": {"available": False}, "c1": {"available": False}}}]


def test_CDR4_003_evolution_tool_receives_chain_local_cdr_history_and_skill_labels():
    histories = []
    run_keys = []
    def chat(messages, definitions, **kwargs):
        system = messages[0]["content"]
        if "You are an antibody engineering analyst" in system:
            result = {"downstream_header": "configured CDR"}
        elif "You are the Antibody Reflection Agent" in system:
            if messages[-1]["role"] != "tool":
                return {"tool_calls": [tool_call("analyze_evolution_tree")]}
            result = reflection_fixture()
        else:
            result = {"mutations": [[["D", 2, "W"]], [["D", 3, "F"]]]}
        return {"content": json.dumps(result)}
    def measure(payload):
        if payload.get("analysis_tool"):
            histories.append(copy.deepcopy(payload["history"]))
            run_keys.append(payload["run_key"])
            return {"tool_result": {"available": True}}
        return reading(payload)
    complete = lambda p: pytest.fail("text-only model path")
    complete.chat = chat
    cdr.CdrEngine(complete=complete, measure=measure).run(spec(baseline_code=baseline(
        reflection_interval=1)), lambda e: None, lambda: False)
    assert len(histories) == 1
    # The evaluator names the analysis directory with this value and accepts only a bare 64-hex digest.
    assert len(run_keys) == 1 and re.fullmatch(r"[0-9a-f]{64}", run_keys[0])
    assert histories[0][0]["chains"] == {"D": "ACDE"}
    child = histories[0][1]
    assert child["chains"] == {"D": "AWDE"}
    assert child["cycle"] == 0 and child["skill_id"] == cdr.POINT_SKILL
    assert child["parent_id"] == "node0"


def test_CDR4_004_design_accepts_the_harness_candidates_shape_the_model_returns():
    """The design contract makes the model answer with candidates[].mutations, not the host's top-level array.

    Before this was accepted, every such answer was refused as 'no mutations list' and the cycle was skipped.
    """
    parent = SimpleNamespace(sequence="ACDEFG", score=-2.0, metrics={}, gate="")
    config = {"chain": "D", "mutable": {2, 3}, "mutation_min": 1, "mutation_max": 3,
              "target_chain": "A", "target_sequence": "MKV", "target_hotspots": [1]}
    host = json.dumps({"mutations": [[["D", 2, "W"]], [["D", 3, "Y"]]]})
    harness = json.dumps({"skill_id": cdr.POINT_SKILL, "selection_reason": "x",
                          "candidates": [{"mutations": [["D", 2, "W"]]}, {"mutations": [["D", 3, "Y"]]}]})
    expected = [[["D", 2, "W"]], [["D", 3, "Y"]]]
    assert cdr._proposals(lambda prompt: host, config, parent, 2) == expected
    assert cdr._proposals(lambda prompt: harness, config, parent, 2) == expected
    with pytest.raises(cdr._Refusal, match="no mutations list"):
        cdr._proposals(lambda prompt: json.dumps({"unrelated": 1}), config, parent, 2)
