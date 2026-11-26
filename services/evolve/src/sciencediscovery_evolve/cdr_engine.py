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

"""CDR sequence search: a population of fixed-length binders, not a program tree.

One expansion is one NPU execution. That execution scans the parent with ESM-2,
and when the parent fold left a backbone it also runs SolubleMPNN, then folds
the chosen child and adds the ESM-2 pseudo-log-likelihood to the same loss.
Python still refuses a framework edit. A fold that measured a score keeps that
number on the same events PUCT plots, including when the 5 Å gate refuses it;
the refusal keeps it out of the population.

The population lives in this process. A restarted control plane cannot rebuild
it from the event log, so a CDR run is not resumed.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import events
from .engine import RunSpec
from .events import Emit
from .logging_config import get_logger

log = get_logger("cdr")

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
POINT_SKILL = "cdr-point-mutation"
FULL_SKILL = "cdr-full-redesign"
ESM_SKILL = "esm2-guided-mutation"
MPNN_SKILL = "antibody-inverse-folding"
CHAT_SKILLS = frozenset({POINT_SKILL, FULL_SKILL})
# Match the colleague CRLF2 workflow weights.
DEFAULT_SKILL_WEIGHTS = {
    POINT_SKILL: 0.5,
    FULL_SKILL: 0.1,
    ESM_SKILL: 0.2,
    MPNN_SKILL: 0.2,
}
DEFAULT_UNIFORM_FRACTION = 0.1
DEFAULT_ROUTER_STRATEGY = "agent"
ScoreFn = Callable[[dict[str, Any]], tuple[bool, float | None, str]]
CompleteFn = Callable[[str], str]
MeasureFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class _Member:
    index: int
    sequence: str
    score: float | None
    visits: int = 0
    mutations: list[list[Any]] = field(default_factory=list)
    backbone: str | None = None


class CdrEngine:
    """Select a parent, point-mutate its CDRs, score the child, keep what passes."""

    name = "cdr"
    #: The candidate is a sequence handed to the fixed NPU program. This engine
    #: does not execute model-written Python.
    requires_sandbox = False

    def __init__(
        self,
        *,
        complete: Optional[CompleteFn] = None,
        score: Optional[ScoreFn] = None,
        measure: Optional[MeasureFn] = None,
    ) -> None:
        self._complete = complete
        self._score = score
        self._measure = measure

    def run(self, spec: RunSpec, emit: Emit, should_stop: Callable[[], bool]) -> None:
        try:
            config = _config(spec.baseline_code)
            if self._measure is not None or (self._score is None and self._complete is None):
                measure = self._measure or _npu_measure(spec, should_stop)
                complete = self._complete
                if complete is None:
                    try:
                        complete = _model_complete(spec, should_stop, emit)
                    except _Refusal as refusal:
                        emit(events.log("warn", f"point mutation unavailable: {refusal}"))
                        complete = None
                self._run_measured(spec, emit, should_stop, config, measure, complete)
                return
            complete = self._complete or _model_complete(spec, should_stop, emit)
            score = self._score or _npu_score(spec, should_stop)
        except _Refusal as refusal:
            emit(events.log("error", str(refusal)))
            emit(events.search_finished("failed", None, 0))
            log.warning("run %s refused: %s", spec.search_id, refusal)
            return

        emit(events.search_started(spec.algorithm or self.name, spec.scorecard_hash))
        rng = random.Random(int(spec.options.get("seed", 42)))
        seed_payload = _payload(config, config["sequence"], [])
        seed_ok, seed_score, seed_error = score(seed_payload)
        seed_measured = _measured(seed_ok, seed_score)
        seed_admitted = seed_measured and not seed_error
        seed = _Member(
            index=0, sequence=config["sequence"],
            score=float(seed_score) if seed_admitted else None,
        )
        emit(events.seeded(
            0, float(seed_score) if seed_measured else None,
            code_hash=_digest(seed.sequence),
            code_chars=len(seed.sequence),
        ))
        if seed_measured:
            emit(events.evaluated(0, float(seed_score), {}, gate_score=float(seed_score)))
        if not seed_admitted:
            emit(events.log("warn", seed_error or "the starting sequence was not scored"))

        population = [seed]
        best = 0 if seed_admitted else None
        produced = 0
        node = 0
        status = "succeeded"
        reason = "max_iters"
        while produced < spec.expansions:
            if should_stop():
                status, reason = "stopped", "stopped"
                break
            parent = _select(
                population,
                _temperature(produced, spec.expansions),
                rng,
                float(config.get("parent_fitness_uniform_fraction") or DEFAULT_UNIFORM_FRACTION),
            )
            parent.visits += 1
            emit(events.selected(parent.index, [{"nodeIndex": parent.index, "visits": parent.visits}]))
            try:
                proposals = _proposals(complete, config, parent, _batch(config, spec, produced))
            except _Refusal as refusal:
                node += 1
                produced += 1
                emit(events.expanded(
                    node, parent.index, 1, None, False,
                    change_summary="proposal rejected",
                    error=str(refusal),
                ))
                emit(events.merged(node, False, str(refusal), category="candidate-failed"))
                continue
            if not proposals:
                node += 1
                produced += 1
                emit(events.expanded(
                    node, parent.index, 1, None, False,
                    change_summary="no mutations",
                    error="the model returned no CDR mutations",
                ))
                emit(events.merged(node, False, "no mutations", category="candidate-failed"))
                continue
            for mutations in proposals:
                if produced >= spec.expansions or should_stop():
                    if should_stop():
                        status, reason = "stopped", "stopped"
                    break
                node += 1
                produced += 1
                child, summary, error = _apply(parent.sequence, config, mutations)
                if child is None:
                    emit(events.expanded(
                        node, parent.index, 1, None, False,
                        change_summary=summary, error=error,
                    ))
                    emit(events.merged(node, False, error or "rejected", category="constraint-violated"))
                    continue
                ok, value, why = score(_payload(config, child, mutations))
                admitted = _publish(
                    emit, node, parent.index, summary, _digest(child), len(child), ok, value, why,
                )
                if not admitted:
                    emit(events.merged(node, False, why or "not scored", category="candidate-failed"))
                    continue
                population.append(_Member(index=node, sequence=child, score=value, mutations=mutations))
                emit(events.merged(node, True, "entered the population"))
                current = None if best is None else _score_of(population, best)
                if value is not None and (current is None or value >= current):
                    best = node
        emit(events.search_finished(
            status, best, produced,
            stop_reason=reason, expansions_planned=spec.expansions,
        ))

    def _run_measured(
        self,
        spec: RunSpec,
        emit: Emit,
        should_stop: Callable[[], bool],
        config: dict[str, Any],
        measure: MeasureFn,
        complete: CompleteFn | None = None,
    ) -> None:
        """One cycle draws a skill, then folds the batch that skill produced."""
        emit(events.search_started(spec.algorithm or self.name, spec.scorecard_hash))
        rng = random.Random(int(spec.options.get("seed", 42)))
        seed_payload = _payload(config, config["sequence"], [])
        seed_reading = measure(seed_payload)
        seed_score = seed_reading.get("score")
        seed_ok = bool(seed_reading.get("ok"))
        seed_error = str(seed_reading.get("error") or "")
        seed_measured = _measured(seed_ok, seed_score if isinstance(seed_score, (int, float)) else None)
        seed_admitted = seed_measured and not seed_error
        seed = _Member(
            index=0, sequence=config["sequence"],
            score=float(seed_score) if seed_admitted else None,
            backbone=seed_reading.get("backbone_pdb") if isinstance(seed_reading.get("backbone_pdb"), str) else None,
        )
        emit(events.seeded(
            0, float(seed_score) if seed_measured else None,
            code_hash=_digest(seed.sequence),
            code_chars=len(seed.sequence),
        ))
        if seed_measured:
            emit(events.evaluated(0, float(seed_score), {}, gate_score=float(seed_score)))
        if not seed_admitted:
            emit(events.log("warn", seed_error or "the starting sequence was not scored"))

        population = [seed]
        tried = {config["sequence"]}
        best = 0 if seed_admitted else None
        produced = 0
        node = 0
        cycle = 0
        status = "succeeded"
        reason = "max_iters"
        # Inverse folding rewrites the CDR. The 1–3 cap stays on the chat path.
        accepting = dict(config)
        accepting["mutation_max"] = max(int(config["mutation_max"]), len(config["mutable"]))

        def record(parent: _Member, reading: dict[str, Any], mutations: list, *, budget=accepting) -> None:
            nonlocal node, produced, best
            node += 1
            produced += 1
            emit(events.selected(parent.index, [{"nodeIndex": parent.index, "visits": parent.visits}]))
            if reading.get("empty") or not isinstance(mutations, list):
                emit(events.expanded(
                    node, parent.index, 1, None, False,
                    change_summary="no mutations",
                    error=str(reading.get("error") or "the model returned no CDR mutations"),
                ))
                emit(events.merged(node, False, "no mutations", category="candidate-failed"))
                return
            child, summary, error = _apply(parent.sequence, budget, mutations)
            reported = reading.get("sequence")
            if child is None or (isinstance(reported, str) and reported != child):
                emit(events.expanded(
                    node, parent.index, 1, None, False,
                    change_summary=summary, error=error or "the folded sequence does not match the mutations",
                ))
                emit(events.merged(node, False, error or "rejected", category="constraint-violated"))
                return
            tried.add(child)
            value = reading.get("score")
            why = str(reading.get("error") or "")
            admitted = _publish(
                emit, node, parent.index, summary, _digest(child), len(child),
                bool(reading.get("ok")), value if isinstance(value, (int, float)) else None, why,
            )
            if not admitted:
                emit(events.merged(node, False, why or "not scored", category="candidate-failed"))
                return
            population.append(_Member(
                index=node, sequence=child, score=value, mutations=mutations,
                backbone=reading.get("backbone_pdb") if isinstance(reading.get("backbone_pdb"), str) else None,
            ))
            emit(events.merged(node, True, "entered the population"))
            current = None if best is None else _score_of(population, best)
            if isinstance(value, (int, float)) and (current is None or value >= current):
                best = node

        while produced < spec.expansions:
            if should_stop():
                status, reason = "stopped", "stopped"
                break
            parent = _select(
                population,
                _temperature(produced, spec.expansions),
                rng,
                float(config.get("parent_fitness_uniform_fraction") or DEFAULT_UNIFORM_FRACTION),
            )
            parent.visits += 1
            width = _batch(config, spec, produced)
            skill = _choose_skill(
                complete,
                42 + cycle,
                cycle,
                bool(parent.backbone),
                config.get("skill_weights"),
                chat_ok=complete is not None,
                strategy=str(config.get("router_selection_strategy") or DEFAULT_ROUTER_STRATEGY),
            )
            if skill in CHAT_SKILLS and complete is not None:
                cycle += 1
                budget = dict(config)
                if skill == FULL_SKILL:
                    budget["mutation_min"] = 1
                    budget["mutation_max"] = max(1, len(config["mutable"]))
                try:
                    proposals = (
                        _proposals_full(complete, config, parent, width)
                        if skill == FULL_SKILL
                        else _proposals(complete, config, parent, width)
                    )
                except _Refusal as refusal:
                    record(parent, {"empty": True, "error": str(refusal)}, [], budget=budget)
                    continue
                folded = 0
                for mutations in proposals:
                    if produced >= spec.expansions:
                        break
                    if folded and should_stop():
                        status, reason = "stopped", "stopped"
                        break
                    child, _summary, _error = _apply(parent.sequence, budget, mutations)
                    if child is None or child in tried:
                        continue
                    follow = _payload(config, child, mutations)
                    record(parent, measure(follow), mutations, budget=budget)
                    folded += 1
                if folded == 0:
                    record(parent, {
                        "empty": True,
                        "error": "the model returned no new CDR mutations",
                    }, [], budget=budget)
                continue
            payload = _payload(config, parent.sequence, [])
            payload["propose"] = True
            payload["skill"] = skill if skill in {ESM_SKILL, MPNN_SKILL} else ESM_SKILL
            payload["num_sequences"] = width
            payload["cycle"] = cycle
            payload["proposal_seed"] = 42 + cycle
            payload["excluded_sequences"] = sorted(tried)
            cycle += 1
            if parent.backbone:
                payload["parent_backbone_pdb"] = parent.backbone
            reading = measure(payload)
            pending = _pending_folds(reading)
            if not pending:
                record(parent, reading if reading.get("empty") else {
                    "empty": True,
                    "error": reading.get("error") or "the model returned no CDR mutations",
                }, [])
                continue
            for offset, item in enumerate(pending):
                if produced >= spec.expansions:
                    break
                if offset and should_stop():
                    status, reason = "stopped", "stopped"
                    break
                if offset == 0:
                    record(parent, reading, item["mutations"])
                    continue
                follow = _payload(config, item["sequence"], item["mutations"])
                record(parent, measure(follow), item["mutations"])
        emit(events.search_finished(
            status, best, produced,
            stop_reason=reason, expansions_planned=spec.expansions,
        ))


class _Refusal(Exception):
    pass


def _config(baseline: str) -> dict[str, Any]:
    try:
        parsed = json.loads(baseline)
    except json.JSONDecodeError as error:
        raise _Refusal("the CDR starting point is not JSON") from error
    if not isinstance(parsed, dict):
        raise _Refusal("the CDR starting point is not a JSON object")
    sequence = parsed.get("sequence")
    if not isinstance(sequence, str) or not sequence or any(letter not in AMINO_ACIDS for letter in sequence):
        raise _Refusal("sequence must be a non-empty amino-acid string")
    regions = parsed.get("cdr_regions")
    if not isinstance(regions, list) or not regions:
        raise _Refusal("cdr_regions must list inclusive 1-based ranges")
    mutable: set[int] = set()
    for region in regions:
        if not isinstance(region, list) or len(region) != 2:
            raise _Refusal("each cdr_regions entry is [start, end]")
        start, end = int(region[0]), int(region[1])
        if start < 1 or end > len(sequence) or start > end:
            raise _Refusal("a CDR range falls outside the sequence")
        mutable.update(range(start, end + 1))
    target = parsed.get("target_sequence")
    if not isinstance(target, str) or not target or any(letter not in AMINO_ACIDS for letter in target):
        raise _Refusal("target_sequence must be a non-empty amino-acid string")
    hotspots = parsed.get("target_hotspots") or []
    if (
        not isinstance(hotspots, list)
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 or item > len(target) for item in hotspots)
    ):
        raise _Refusal("target_hotspots must be 1-based positions on the target sequence")
    chain = str(parsed.get("chain") or "D")
    return {
        "sequence": sequence,
        "chain": chain,
        "target_sequence": target,
        "target_chain": str(parsed.get("target_chain") or "A"),
        "target_hotspots": hotspots,
        "cdr_regions": regions,
        "mutable": mutable,
        "candidates_per_cycle": int(parsed.get("candidates_per_cycle") or 8),
        "mutation_min": 1,
        "mutation_max": 3,
        "skill_weights": _skill_weights(parsed.get("skill_weights")),
        "router_selection_strategy": _router_strategy(parsed.get("router_selection_strategy")),
        "parent_fitness_uniform_fraction": _uniform_fraction(
            parsed.get("parent_fitness_uniform_fraction"),
        ),
    }


def _skill_weights(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    weights: dict[str, float] = {}
    for key in (POINT_SKILL, FULL_SKILL, ESM_SKILL, MPNN_SKILL):
        if key not in raw:
            continue
        try:
            weights[key] = float(raw[key])
        except (TypeError, ValueError):
            continue
    return weights or None


def _router_strategy(raw: Any) -> str:
    value = str(raw or DEFAULT_ROUTER_STRATEGY).strip().lower()
    return value if value in {"agent", "weighted"} else DEFAULT_ROUTER_STRATEGY


def _uniform_fraction(raw: Any) -> float:
    try:
        value = float(DEFAULT_UNIFORM_FRACTION if raw is None else raw)
    except (TypeError, ValueError):
        return DEFAULT_UNIFORM_FRACTION
    return min(1.0, max(0.0, value))


def _legal_skills(
    has_backbone: bool,
    weights: dict[str, float] | None,
    *,
    chat_ok: bool,
) -> tuple[list[str], list[float]]:
    table = dict(weights) if isinstance(weights, dict) else dict(DEFAULT_SKILL_WEIGHTS)
    legal: list[str] = []
    chosen: list[float] = []
    for name in (POINT_SKILL, FULL_SKILL, ESM_SKILL, MPNN_SKILL):
        weight = float(table.get(name, 0.0) or 0.0)
        if weight <= 0:
            continue
        if name in CHAT_SKILLS and not chat_ok:
            continue
        if name == MPNN_SKILL and not has_backbone:
            continue
        legal.append(name)
        chosen.append(weight)
    if not legal:
        return [ESM_SKILL], [1.0]
    return legal, chosen


def _draw_skill(
    seed: int,
    cycle: int,
    has_backbone: bool,
    weights: dict[str, float] | None,
    *,
    chat_ok: bool,
) -> str:
    """Weighted draw used by ``weighted`` routing and as the agent fallback."""
    legal, chosen = _legal_skills(has_backbone, weights, chat_ok=chat_ok)
    return random.Random(f"protein-design-router:{seed}:{cycle}").choices(legal, weights=chosen, k=1)[0]


def _agent_pick_skill(
    complete: CompleteFn,
    legal: list[str],
    weights: list[float],
) -> str:
    """Ask the chat model to pick one legal skill, the harness agent route."""
    total = sum(weights) or float(len(legal))
    lines = [
        "Return JSON only. Pick exactly one skill_id from the list.",
        "Compare the priors and choose the skill that best continues the search.",
    ]
    for name, weight in zip(legal, weights):
        lines.append(f"- `{name}`: prior={weight / total:.4f}")
    lines.append('{"skill_id": "<id>"}')
    text = complete("\n".join(lines))
    try:
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start:end + 1] if start >= 0 and end > start else text)
    except json.JSONDecodeError as error:
        raise _Refusal("the model did not return a skill_id") from error
    picked = str(parsed.get("skill_id") or "").strip() if isinstance(parsed, dict) else ""
    if picked not in legal:
        raise _Refusal("the model picked a skill that is not allowed")
    return picked


def _choose_skill(
    complete: CompleteFn | None,
    seed: int,
    cycle: int,
    has_backbone: bool,
    weights: dict[str, float] | None,
    *,
    chat_ok: bool,
    strategy: str,
) -> str:
    legal, chosen = _legal_skills(has_backbone, weights, chat_ok=chat_ok)
    if strategy == "agent" and complete is not None and chat_ok:
        try:
            return _agent_pick_skill(complete, legal, chosen)
        except _Refusal:
            pass
    return random.Random(f"protein-design-router:{seed}:{cycle}").choices(legal, weights=chosen, k=1)[0]


def _pending_folds(reading: dict[str, Any]) -> list[dict[str, Any]]:
    """The sequence this job already folded, then the rest of that same batch."""
    mutations = reading.get("mutations")
    sequence = reading.get("sequence")
    if not isinstance(mutations, list) or not isinstance(sequence, str):
        return []
    pending = [{"sequence": sequence, "mutations": mutations}]
    portfolio = reading.get("portfolio")
    if isinstance(portfolio, list):
        for item in portfolio:
            if (
                isinstance(item, dict)
                and isinstance(item.get("sequence"), str)
                and isinstance(item.get("mutations"), list)
            ):
                pending.append({"sequence": item["sequence"], "mutations": item["mutations"]})
    return pending


def _measured(ok: bool, value: float | None) -> bool:
    return bool(ok) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _publish(
    emit: Emit, node: int, parent: int, summary: str, code_hash: str, code_chars: int,
    ok: bool, value: float | None, why: str,
) -> bool:
    """Record a fold the way the run card reads it, and say whether it may breed.

    A measured score is valid even when the contact gate refuses the sequence.
    The chart and the node list read that score. The population does not.
    """
    measured = _measured(ok, value)
    emit(events.expanded(
        node, parent, 1, float(value) if measured else None, measured,
        change_summary=summary,
        code_hash=code_hash,
        code_chars=code_chars,
        error=None if measured and not why else (why or "not scored"),
    ))
    if measured:
        emit(events.evaluated(node, float(value), {}, gate_score=float(value)))
    return measured and not why


def _batch(config: dict[str, Any], spec: RunSpec, produced: int) -> int:
    """Sequences one generation call asks for.

    The harness default is 8. A requested width below 2 is that default,
    because 1 is only the top ESM-2 substitution and the next cycle repeats it.
    """
    requested = spec.options.get("candidatesPerCycle", config["candidates_per_cycle"])
    try:
        size = int(requested)
    except (TypeError, ValueError):
        size = 8
    if size < 2:
        size = 8
    remaining = spec.expansions - produced
    return max(1, min(size, 16, remaining))


def _payload(config: dict[str, Any], sequence: str, mutations: list[list[Any]]) -> dict[str, Any]:
    return {
        "cdr_regions": config["cdr_regions"],
        "chain": config["chain"],
        "mutations": mutations,
        "sequence": sequence,
        "target_chain": config["target_chain"],
        "target_hotspots": config["target_hotspots"],
        "target_sequence": config["target_sequence"],
    }


def _model_complete(spec: RunSpec, should_stop: Callable[[], bool], emit: Emit) -> CompleteFn:
    if not spec.llm_url or not spec.llm_token:
        raise _Refusal("CDR search needs the run's model proxy")
    from .completion import CompletionUsage, completion_for

    spent = [0]

    def note(usage: CompletionUsage) -> None:
        # The card replaces the token total, the way PUCT's cost event does.
        spent[0] += usage.total
        emit(events.cost(spent[0], 0))

    return completion_for(
        spec.llm_url, spec.llm_token,
        max_tokens=spec.max_tokens_per_call,
        thinking=spec.thinking or None,
        thinking_effort=spec.thinking_effort or None,
        on_usage=note,
        should_stop=should_stop,
    )


def _npu_score(spec: RunSpec, should_stop: Callable[[], bool]) -> ScoreFn:
    from .npu_job import NpuJobError, interpret_npu_job, npu_client_from_spec

    try:
        client = npu_client_from_spec(spec.options, spec.candidate_timeout_seconds)
    except NpuJobError as error:
        raise _Refusal(str(error)) from error
    criteria = list(spec.scorecard.get("criteria") or [])
    metric = str(criteria[0].get("id") or "score") if criteria else "score"

    def score(payload: dict[str, Any]) -> tuple[bool, float | None, str]:
        if should_stop():
            return False, None, "the search was stopped"
        try:
            job, artifact = client.run(json.dumps(payload, sort_keys=True), should_stop)
        except NpuJobError as error:
            return False, None, str(error)
        valid, metrics, error = interpret_npu_job(job, artifact, metric_key=metric)
        # The 5 Å gate decides who may be a parent. The fold's score is still
        # the number the run card plots, the same way PUCT plots a node that
        # ran and then lost.
        if valid and artifact and artifact.get("gate_passed") is False:
            return True, float(metrics["score"]), str(artifact.get("error") or error or "gate")
        if not valid:
            return False, None, error or "not scored"
        return True, float(metrics["score"]), ""

    return score


def _measure_reading(
    artifact: dict[str, Any] | None,
    valid: bool,
    metrics: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    """Keep the folded child and the rest of the batch the job already generated.

    The fold score and the 5 Å gate describe the sequence that just ran. The
    other sequences in ``portfolio`` have not been folded yet. Dropping them
    makes the engine open a new round for every child.
    """
    reading: dict[str, Any] = {
        "mutations": artifact.get("mutations") if artifact else None,
        "sequence": artifact.get("sequence") if artifact else None,
        "backbone_pdb": artifact.get("backbone_pdb") if artifact else None,
    }
    portfolio = artifact.get("portfolio") if artifact else None
    if isinstance(portfolio, list):
        reading["portfolio"] = portfolio
    if valid and artifact and artifact.get("gate_passed") is False:
        reading.update({
            "ok": True,
            "score": float(metrics["score"]),
            "error": str(artifact.get("error") or error or "gate"),
        })
        return reading
    if not valid:
        reading.update({"ok": False, "error": error or "not scored"})
        return reading
    reading.update({"ok": True, "score": float(metrics["score"]), "error": ""})
    return reading


def _npu_measure(spec: RunSpec, should_stop: Callable[[], bool]) -> MeasureFn:
    """One runner execution returns the proposal, the fold, and the backbone."""
    from .npu_job import NpuJobError, interpret_npu_job, npu_client_from_spec

    try:
        client = npu_client_from_spec(spec.options, spec.candidate_timeout_seconds)
    except NpuJobError as error:
        raise _Refusal(str(error)) from error
    criteria = list(spec.scorecard.get("criteria") or [])
    metric = str(criteria[0].get("id") or "score") if criteria else "score"

    def measure(payload: dict[str, Any]) -> dict[str, Any]:
        if should_stop():
            return {"ok": False, "error": "the search was stopped"}
        try:
            job, artifact = client.run(json.dumps(payload, sort_keys=True), should_stop)
        except NpuJobError as error:
            return {"ok": False, "error": str(error)}
        if artifact and artifact.get("proposal") == "empty":
            return {
                "ok": False,
                "empty": True,
                "error": str(artifact.get("error") or "the model returned no CDR mutations"),
            }
        valid, metrics, error = interpret_npu_job(job, artifact, metric_key=metric)
        return _measure_reading(artifact, valid, metrics, error)

    return measure


def _proposals(complete: CompleteFn, config: dict[str, Any], parent: _Member, count: int) -> list[list[list[Any]]]:
    positions = ", ".join(str(pos) for pos in sorted(config["mutable"]))
    how_many = (
        "Exactly one substitution"
        if count == 1
        else f"{count} candidates, each with {config['mutation_min']} to {config['mutation_max']} substitutions"
    )
    prompt = (
        "Return JSON only. Do not name or discuss the protein.\n"
        f"Chain {config['chain']}. {how_many}, "
        "encoded as [chain, 1-based position, new amino acid].\n"
        f"Allowed positions: {positions}.\n"
        f"Sequence:\n{parent.sequence}\n"
        "The new amino acid must differ from the sequence at that position.\n"
        '{"mutations": [[["<chain>", 0, "X"]]]}'
    )
    text = complete(prompt)
    try:
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start:end + 1] if start >= 0 and end > start else text)
    except json.JSONDecodeError as error:
        raise _Refusal("the model did not return JSON") from error
    groups = parsed.get("mutations") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        raise _Refusal("the model JSON has no mutations list")
    proposals: list[list[list[Any]]] = []
    for group in groups[:count]:
        if isinstance(group, list) and group and all(isinstance(item, list) for item in group):
            proposals.append(group)
    return proposals


def _proposals_full(
    complete: CompleteFn, config: dict[str, Any], parent: _Member, count: int,
) -> list[list[list[Any]]]:
    """Ask for a complete CDR assignment, then keep only residues that change."""
    ordered = sorted(config["mutable"])
    positions = ", ".join(str(pos) for pos in ordered)
    prompt = (
        "Return JSON only. Do not name or discuss the protein.\n"
        f"Chain {config['chain']}. {count} full CDR redesign candidates.\n"
        "Each candidate must assign exactly one amino acid to every allowed "
        "position, including unchanged parent residues.\n"
        f"Allowed positions: {positions}.\n"
        f"Sequence:\n{parent.sequence}\n"
        "Candidates must yield different sequences; change at least three "
        "positions when that many exist.\n"
        '{"mutations": [[["<chain>", 0, "X"]]]}'
    )
    text = complete(prompt)
    try:
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start:end + 1] if start >= 0 and end > start else text)
    except json.JSONDecodeError as error:
        raise _Refusal("the model did not return JSON") from error
    groups = parsed.get("mutations") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        raise _Refusal("the model JSON has no mutations list")
    expected = {(config["chain"], pos) for pos in ordered}
    proposals: list[list[list[Any]]] = []
    for group in groups[:count]:
        if not isinstance(group, list) or not group or not all(isinstance(item, list) for item in group):
            continue
        covered: set[tuple[str, int]] = set()
        changed: list[list[Any]] = []
        valid = True
        for item in group:
            if len(item) != 3:
                valid = False
                break
            chain, pos, amino = str(item[0]), int(item[1]), str(item[2])
            key = (chain, pos)
            if key in covered or key not in expected or amino not in AMINO_ACIDS:
                valid = False
                break
            covered.add(key)
            if parent.sequence[pos - 1] != amino:
                changed.append([chain, pos, amino])
        if not valid or covered != expected or not changed:
            continue
        proposals.append(changed)
    if not proposals:
        raise _Refusal("the model returned no complete CDR redesign")
    return proposals


def _apply(
    sequence: str, config: dict[str, Any], mutations: list[list[Any]],
) -> tuple[str | None, str, str]:
    child = sequence
    notes: list[str] = []
    seen: set[int] = set()
    low, high = int(config["mutation_min"]), int(config["mutation_max"])
    if not low <= len(mutations) <= high:
        return None, "mutation count", f"a candidate must contain {low} to {high} CDR substitutions"
    for mutation in mutations:
        if len(mutation) != 3:
            return None, "bad mutation", "a mutation is [chain, position, amino acid]"
        chain, pos, amino = str(mutation[0]), int(mutation[1]), str(mutation[2])
        if chain != config["chain"]:
            return None, f"{chain}{pos}", "the mutation names a different chain"
        if pos not in config["mutable"]:
            return None, f"{chain}{pos}{amino}", "the position is outside the CDRs"
        if pos in seen or amino not in AMINO_ACIDS:
            return None, f"{chain}{pos}{amino}", "the substitution is duplicated or not an amino acid"
        if child[pos - 1] == amino:
            return None, f"{chain}{pos}{amino}", "the substitution does not change the residue"
        seen.add(pos)
        child = child[:pos - 1] + amino + child[pos:]
        notes.append(f"{chain}{pos}{sequence[pos - 1]}{amino}")
    return child, " ".join(notes), ""


def _select(
    population: list[_Member],
    temperature: float,
    rng: random.Random,
    uniform_fraction: float = DEFAULT_UNIFORM_FRACTION,
) -> _Member:
    scored = [member for member in population if member.score is not None]
    pool = scored or population
    if len(pool) == 1:
        return pool[0]
    fitness = [math.exp((member.score or 0.0) / max(temperature, 1e-6)) for member in pool]
    total = sum(fitness) or float(len(pool))
    mix = min(1.0, max(0.0, float(uniform_fraction)))
    uniform = mix / len(pool)
    weights = [(1.0 - mix) * value / total + uniform for value in fitness]
    draw = rng.random() * sum(weights)
    cursor = 0.0
    for member, weight in zip(pool, weights):
        cursor += weight
        if draw <= cursor:
            return member
    return pool[-1]


def _temperature(produced: int, total: int) -> float:
    if total <= 1:
        return 1.0
    return 1.0 + (0.2 - 1.0) * (produced / total)


def _score_of(population: list[_Member], index: int) -> float:
    for member in population:
        if member.index == index and member.score is not None:
            return member.score
    return float("-inf")


def positive_llr_substitutions(
    rows: list[dict[str, Any]], mutable: set[int], chain: str, limit: int,
) -> list[list[list[Any]]]:
    """Rank CDR substitutions by a positive ESM-2 log-likelihood ratio.

    Each row is one 1-based position from a deep mutational scan: ``Pos``,
    ``WT``, and one number per amino acid. A positive ratio is more plausible
    than the parent residue. Positions outside ``mutable`` stay fixed. Each
    returned group is a single substitution, highest ratio first.
    """
    ranked: list[tuple[float, int, str]] = []
    for row in rows:
        position = row.get("Pos")
        wild = row.get("WT")
        if isinstance(position, bool) or not isinstance(position, int) or position not in mutable:
            continue
        if not isinstance(wild, str) or len(wild) != 1:
            continue
        for amino, value in row.items():
            if amino in {"Pos", "WT", "WT_Score"} or amino not in AMINO_ACIDS or amino == wild:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            ratio = float(value)
            if ratio > 0:
                ranked.append((ratio, position, amino))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [[[chain, position, amino]] for _, position, amino in ranked[:max(0, limit)]]


def _digest(sequence: str) -> str:
    return "sha256:" + hashlib.sha256(sequence.encode("utf-8")).hexdigest()
