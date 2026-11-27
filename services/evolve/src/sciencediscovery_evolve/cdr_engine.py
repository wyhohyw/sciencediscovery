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

import difflib
import hashlib
import json
import math
import random
import time
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any, Callable, Optional

from . import events
from .capabilities import ESM_SKILL, MPNN_SKILL, assess
from .engine import RunSpec
from .events import Emit
from .logging_config import get_logger

log = get_logger("cdr")

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
POINT_SKILL = "cdr-point-mutation"
FULL_SKILL = "cdr-full-redesign"
CHAT_SKILLS = frozenset({POINT_SKILL, FULL_SKILL})
SKILL_BACKENDS = {
    POINT_SKILL: "llm",
    FULL_SKILL: "llm",
    MPNN_SKILL: "inverse_folding",
    ESM_SKILL: "esm2",
}
# Match the colleague CRLF2 workflow weights.
DEFAULT_SKILL_WEIGHTS = {
    POINT_SKILL: 0.5,
    FULL_SKILL: 0.1,
    ESM_SKILL: 0.2,
    MPNN_SKILL: 0.2,
}
DEFAULT_UNIFORM_FRACTION = 0.1
DEFAULT_ROUTER_STRATEGY = "agent"
#: Retries of a cycle whose design phase produced nothing, as ``cycle_retry_limit``.
CYCLE_RETRY_LIMIT = 2
#: Constrained-elite population, the harness ``ConstrainedElitePopulation`` defaults.
POPULATION_SIZE = 20
MIN_CDR_DISTANCE = 0.05
POSITION_REUSE_FRACTION = 0.75
MUTATION_REUSE_FRACTION = 0.30
#: Folds at or above this ipTM go to the quality check, as ``quality_check_threshold``.
QUALITY_THRESHOLD = 0.7
#: Harness contract default for ``reflection_interval``. ds10 sets 1.
DEFAULT_REFLECTION_INTERVAL = 20
RUNNER_RETRY_WAITS = (30, 60, 120)
#: Fold evidence the evaluator already returns, carried into the design prompt.
REPORTED_METRICS = (
    "iptm", "plddt", "cdr_contact_fraction", "framework_contact_fraction",
    "gate_passed", "esm2", "components",
)
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
    depth: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    gate: str = ""
    summary: str = "starting sequence"


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
                if self._measure is None:
                    config = _preflight(config, complete, spec, should_stop, emit)
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
                    node, parent.index, parent.depth + 1, None, False,
                    change_summary="proposal rejected",
                    error=str(refusal),
                ))
                emit(events.merged(node, False, str(refusal), category="candidate-failed"))
                continue
            if not proposals:
                node += 1
                produced += 1
                emit(events.expanded(
                    node, parent.index, parent.depth + 1, None, False,
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
                        node, parent.index, parent.depth + 1, None, False,
                        change_summary=summary, error=error,
                    ))
                    emit(events.merged(node, False, error or "rejected", category="constraint-violated"))
                    continue
                ok, value, why = score(_payload(config, child, mutations))
                admitted = _publish(
                    emit, node, parent.index, parent.depth + 1, summary, _digest(child), len(child),
                    ok, value, why,
                )
                if not admitted:
                    emit(events.merged(node, False, why or "not scored", category="candidate-failed"))
                    continue
                population.append(_Member(
                    index=node, sequence=child, score=value, mutations=mutations, depth=parent.depth + 1,
                ))
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
        if seed_reading.get("runner_lost"):
            emit(events.log("error", str(seed_reading.get("error") or "runner lost")))
            emit(events.search_finished(
                "failed", None, 0, stop_reason="runner_lost", expansions_planned=spec.expansions,
            ))
            return
        seed_score = seed_reading.get("score")
        seed_ok = bool(seed_reading.get("ok"))
        seed_error = str(seed_reading.get("error") or "")
        seed_measured = _measured(seed_ok, seed_score if isinstance(seed_score, (int, float)) else None)
        seed_admitted = seed_measured and not seed_error
        seed = _Member(
            index=0, sequence=config["sequence"],
            score=float(seed_score) if seed_measured else None,
            backbone=seed_reading.get("backbone_pdb") if isinstance(seed_reading.get("backbone_pdb"), str) else None,
            metrics=seed_reading.get("metrics") or {},
            gate=seed_error,
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

        # Only sequences that pass the gate are population members. A seed that
        # fails the gate is the first working parent instead, as in the harness.
        population: list[_Member] = [seed] if seed_admitted else []
        seed_regions = _cdr_slices(config["sequence"], config["cdr_regions"])
        working: _Member | None = seed if seed_measured and not seed_admitted else None
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
        lost = False
        outcomes: list[dict[str, Any]] = []
        tries: list[dict[str, Any]] = []
        reflection = "none"
        folded_cycle: tuple[int, str, int] | None = None
        attempt = 0
        analysis = _analyze(complete, config) if complete is not None else "none"
        streak = 0
        seen_best: float | None = None
        cycles = max(1, -(-spec.expansions // _batch(config, spec, 0)))

        def record(
            parent: _Member, reading: dict[str, Any], mutations: list, *,
            budget=accepting, verdict: bool | None = None,
        ) -> None:
            nonlocal node, produced, best, lost, status, reason, working
            if reading.get("runner_lost"):
                # The runner never ran this candidate, so it does not spend an expansion.
                lost = True
                status, reason = "failed", "runner_lost"
                emit(events.log("error", str(reading.get("error") or "the runner was lost")))
                return
            node += 1
            produced += 1
            emit(events.selected(parent.index, [{"nodeIndex": parent.index, "visits": parent.visits}]))
            if reading.get("empty") or not isinstance(mutations, list):
                emit(events.expanded(
                    node, parent.index, parent.depth + 1, None, False,
                    change_summary="no mutations",
                    error=str(reading.get("error") or "the model returned no CDR mutations"),
                ))
                emit(events.merged(node, False, "no mutations", category="candidate-failed"))
                return
            child, summary, error = _apply(parent.sequence, budget, mutations)
            reported = reading.get("sequence")
            if child is None or (isinstance(reported, str) and reported != child):
                emit(events.expanded(
                    node, parent.index, parent.depth + 1, None, False,
                    change_summary=summary, error=error or "the folded sequence does not match the mutations",
                ))
                emit(events.merged(node, False, error or "rejected", category="constraint-violated"))
                return
            tried.add(child)
            value = reading.get("score")
            why = str(reading.get("error") or "")
            admitted = _publish(
                emit, node, parent.index, parent.depth + 1, summary, _digest(child), len(child),
                bool(reading.get("ok")), value if isinstance(value, (int, float)) else None, why,
            )
            if outcomes and isinstance(value, (int, float)):
                outcomes[-1]["scores"].append(round(float(value), 4))
            tries.append({
                "node": node, "parent": parent.index, "summary": summary,
                "score": round(float(value), 4) if isinstance(value, (int, float)) else None,
                "gate": why, "metrics": reading.get("metrics") or {},
            })
            candidate = _Member(
                index=node, sequence=child, score=value, mutations=mutations,
                backbone=reading.get("backbone_pdb") if isinstance(reading.get("backbone_pdb"), str) else None,
                depth=parent.depth + 1,
                metrics=reading.get("metrics") or {},
                gate=why,
                summary=summary,
            )
            if not admitted and _measured(bool(reading.get("ok")), value if isinstance(value, (int, float)) else None):
                # Measured but refused by the contact gate: it may still be the working parent.
                working = _prefer_working(candidate, working)
            if admitted and verdict is False:
                admitted = False
                why = "quality check did not pass"
            if not admitted:
                emit(events.merged(node, False, why or "not scored", category="candidate-failed"))
                return
            if outcomes:
                outcomes[-1]["admitted"] += 1
            population.append(candidate)
            kept, actions = _retain(population, config, seed_regions)
            population[:] = [member for member in population if member.index in kept]
            if node in kept:
                emit(events.merged(node, True, "entered the population"))
            else:
                emit(events.merged(node, False, actions.get(node, "not retained"), category="candidate-failed"))
            # The best of the survivors, as the harness reads it after each update.
            best = max(population, key=lambda member: member.score).index if population else None

        while produced < spec.expansions and not lost:
            if should_stop():
                status, reason = "stopped", "stopped"
                break
            top = max((member.score for member in population if member.score is not None), default=None)
            # A retried cycle never committed, so it does not count as a cycle
            # without improvement either.
            if attempt == 0:
                if outcomes:
                    streak = 0 if top is not None and (seen_best is None or top > seen_best) else streak + 1
                seen_best = top
            if folded_cycle is not None:
                # As the harness: reflect only when (cycle + 1) % reflection_interval == 0.
                # Other cycles keep the latest reflection, which the harness also keeps.
                if complete is not None and (folded_cycle[0] + 1) % config["reflection_interval"] == 0:
                    reflection = _reflect(complete, folded_cycle[0], folded_cycle[1], tries[folded_cycle[2]:])
                folded_cycle = None
            if population:
                parent = _select(
                    population,
                    _temperature(cycle, cycles),
                    rng,
                    float(config.get("parent_fitness_uniform_fraction") or DEFAULT_UNIFORM_FRACTION),
                )
            else:
                # Nothing has passed the gate yet. The harness then breeds from the working parent.
                parent = working if working is not None else seed
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
                state={
                    "parent": {"score": parent.score, "sequence": parent.sequence},
                    "best": top,
                    "streak": streak,
                    "outcomes": outcomes,
                },
            )
            outcomes.append({"cycle": cycle, "skill": skill, "scores": [], "admitted": 0})
            design_state = {
                "analysis": analysis,
                "best": top,
                "streak": streak,
                "reflection": reflection,
                "tries": tries,
                "working": None if working is None else {
                    "sequence": working.sequence, "score": working.score,
                    "metrics": working.metrics, "gate": working.gate,
                },
                "population": [
                    {
                        "node": member.index, "score": member.score,
                        "depth": member.depth, "summary": member.summary,
                    }
                    for member in sorted(
                        (item for item in population if item.score is not None),
                        key=lambda item: -item.score,
                    )
                ],
            }
            folded_cycle = (cycle, skill, len(tries))
            emit(events.log("info", f"cycle {cycle} drew {skill} on node {parent.index}"))
            if skill in CHAT_SKILLS and complete is not None:
                budget = dict(config)
                if skill == FULL_SKILL:
                    budget["mutation_min"] = 1
                    budget["mutation_max"] = max(1, len(config["mutable"]))
                refused = ""
                try:
                    proposals = (
                        _proposals_full(complete, config, parent, width, design_state)
                        if skill == FULL_SKILL
                        else _proposals(complete, config, parent, width, design_state)
                    )
                except _Refusal as refusal:
                    proposals, refused = [], str(refusal)
                # Fold the whole batch first, then one quality check over it, as the harness
                # cycle does. Only then are the folds recorded, in proposal order.
                batch: list[tuple[list, dict[str, Any]]] = []
                children: set[str] = set()
                for mutations in proposals:
                    if produced + len(batch) >= spec.expansions or lost:
                        break
                    if batch and should_stop():
                        status, reason = "stopped", "stopped"
                        break
                    child, _summary, _error = _apply(parent.sequence, budget, mutations)
                    if child is None or child in tried or child in children:
                        continue
                    children.add(child)
                    reading = measure(_payload(config, child, mutations))
                    reading.setdefault("sequence", child)
                    batch.append((mutations, reading))
                    if reading.get("runner_lost"):
                        break
                verdicts, quality_error = _quality_verdicts(
                    complete, analysis, [reading for _m, reading in batch], exempt=skill == FULL_SKILL,
                )
                if quality_error:
                    emit(events.log("warn", f"cycle {cycle} quality check failed: {quality_error}"))
                for index, (mutations, reading) in enumerate(batch):
                    record(parent, reading, mutations, budget=budget, verdict=verdicts[index])
                folded = len(batch)
                if folded == 0 and not lost:
                    why = refused or "the model returned no new CDR mutations"
                    if attempt < CYCLE_RETRY_LIMIT:
                        # Nothing was folded, so the cycle has not committed. The harness
                        # retries it from parent selection instead of charging the budget.
                        attempt += 1
                        folded_cycle = None
                        outcomes.pop()
                        emit(events.log("warn", f"cycle {cycle} retry {attempt}: {why}"))
                        continue
                    record(parent, {"empty": True, "error": why}, [], budget=budget)
                cycle += 1
                attempt = 0
                continue
            attempt = 0
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
            if reading.get("runner_lost"):
                record(parent, reading, [])
                continue
            pending = _pending_folds(reading)
            if not pending:
                record(parent, reading if reading.get("empty") else {
                    "empty": True,
                    "error": reading.get("error") or "the model returned no CDR mutations",
                }, [])
                continue
            # The job already folded the first sequence; the rest of its batch folds here.
            reading.setdefault("sequence", pending[0]["sequence"])
            batch = [(pending[0]["mutations"], reading)]
            for item in pending[1:]:
                if produced + len(batch) >= spec.expansions or lost:
                    break
                if should_stop():
                    status, reason = "stopped", "stopped"
                    break
                measured = measure(_payload(config, item["sequence"], item["mutations"]))
                measured.setdefault("sequence", item["sequence"])
                batch.append((item["mutations"], measured))
                if measured.get("runner_lost"):
                    break
            verdicts, quality_error = _quality_verdicts(
                complete, analysis, [item for _m, item in batch], exempt=False,
            )
            if quality_error:
                emit(events.log("warn", f"cycle {cycle} quality check failed: {quality_error}"))
            for index, (mutations, item) in enumerate(batch):
                record(parent, item, mutations, verdict=verdicts[index])
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
        "reflection_interval": _reflection_interval(parsed.get("reflection_interval")),
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


def _reflection_interval(raw: Any) -> int:
    """``reflection_interval`` as the harness contract reads it: an integer of at least 1.

    The harness default is 20. The CRLF2 ds10 config sets 1, so each cycle reflects.
    """
    if raw is None or isinstance(raw, bool):
        return DEFAULT_REFLECTION_INTERVAL
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REFLECTION_INTERVAL
    return max(1, value)


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
    state: dict[str, Any] | None = None,
) -> str:
    """Ask the chat model to pick one legal skill, the harness agent route.

    The harness design agent compares the routes against the parent, the search
    state and what earlier cycles did, so the prompt carries the same evidence.
    """
    state = state or {}
    total = sum(weights) or float(len(legal))
    streak = int(state.get("streak") or 0)
    lines = [
        "You are the Design Agent for an antibody CDR search. Return one JSON object only.",
        "",
        "SKILL SELECTION",
        "- Choose exactly one Router-allowed primary skill. Compare available routes against current evidence,",
        "  bottlenecks, and search radius, not list order or a single metric.",
        "",
        "=== ROUTER-ALLOWED PRIMARY SKILLS ===",
        "The Router checked runtime legality. Compare all listed skills and select exactly one.",
    ]
    for name, weight in zip(legal, weights):
        lines.append(f"- `{name}`: backend={SKILL_BACKENDS[name]}; prior={weight / total:.4f}")
    lines.append("- Return the chosen ID once as top-level `skill_id`; do not compose skills.")
    parent = state.get("parent")
    if isinstance(parent, dict):
        lines.extend([
            "",
            "=== PARENT ===",
            f"- Score (higher is better, score = -loss): {parent.get('score')}",
            f"- Sequence: {parent.get('sequence')}",
        ])
    lines.extend([
        "",
        "=== SEARCH STATE ===",
        f"- Best score so far: {state.get('best')}",
        f"- Consecutive cycles without global-best improvement: {streak}",
        "- " + (
            "Use the gate evidence and historical skill outcomes to choose a legal basin-changing proposal."
            if streak else "Continue refining the improved lineage."
        ),
    ])
    outcomes = state.get("outcomes") or []
    if outcomes:
        lines.extend(["", "=== HISTORICAL SKILL OUTCOMES ==="])
        for item in outcomes[-6:]:
            lines.append(
                f"- cycle {item['cycle']} `{item['skill']}`: folded {len(item['scores'])}, "
                f"admitted {item['admitted']}, best score {max(item['scores']) if item['scores'] else 'none'}"
            )
    lines.extend(["", '{"skill_id": "<id>", "selection_reason": "<short comparison>"}'])
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
    state: dict[str, Any] | None = None,
) -> str:
    legal, chosen = _legal_skills(has_backbone, weights, chat_ok=chat_ok)
    if strategy == "agent" and complete is not None and chat_ok:
        try:
            return _agent_pick_skill(complete, legal, chosen, state)
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
    emit: Emit, node: int, parent: int, depth: int, summary: str, code_hash: str, code_chars: int,
    ok: bool, value: float | None, why: str,
) -> bool:
    """Record a fold the way the run card reads it, and say whether it may breed.

    A measured score is valid even when the contact gate refuses the sequence.
    The chart and the node list read that score. The population does not.
    """
    measured = _measured(ok, value)
    emit(events.expanded(
        node, parent, depth, float(value) if measured else None, measured,
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
        # The design prompt needs the evidence behind the score, the way the
        # harness hands the agent its metric context and gate feedback.
        "metrics": {name: artifact.get(name) for name in REPORTED_METRICS if artifact and name in artifact},
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


def _runner_absent(error: Exception) -> bool:
    text = str(error)
    return text.startswith("runner ") and ("not connected" in text or "failed:" in text)


def _pause(seconds: float, should_stop: Callable[[], bool]) -> bool:
    for _ in range(int(seconds)):
        if should_stop():
            return False
        time.sleep(1)
    return True


def _npu_probe(spec: RunSpec, should_stop: Callable[[], bool]) -> dict[str, Any]:
    """One NPU execution that reports which components this host has. It folds nothing."""
    from .npu_job import NpuJobError, npu_client_from_spec

    try:
        client = npu_client_from_spec(spec.options, spec.candidate_timeout_seconds)
    except NpuJobError as error:
        raise _Refusal(str(error)) from error
    try:
        _job, artifact = client.run(json.dumps({"probe": True}), should_stop)
    except NpuJobError as error:
        return {"error": str(error)}
    if not isinstance(artifact, dict):
        return {"error": "the NPU probe returned no report"}
    return artifact


def _apply_preflight(
    config: dict[str, Any], report: dict[str, Any], chat_ok: bool, emit: Emit,
) -> dict[str, Any]:
    """Switch off each skill the probe could not satisfy, and refuse if nothing can run."""
    result = assess(report)
    for note in result.notes:
        emit(events.log("info", note))
    if not result.fold_ok:
        reason = result.fold_reason or str(report.get("error") or "unknown")
        raise _Refusal(f"this NPU host cannot fold a candidate: {reason}")
    weights = dict(config.get("skill_weights") or DEFAULT_SKILL_WEIGHTS)
    for skill, reason in result.unavailable.items():
        weights[skill] = 0.0
        emit(events.log("warn", f"skill {skill} is off for this run: {reason}"))
    runnable = [
        skill for skill in (POINT_SKILL, FULL_SKILL, ESM_SKILL, MPNN_SKILL)
        if float(weights.get(skill, 0.0) or 0.0) > 0 and (skill not in CHAT_SKILLS or chat_ok)
    ]
    if not runnable:
        detail = "; ".join(f"{skill}: {reason}" for skill, reason in result.unavailable.items())
        raise _Refusal(f"no skill can run on this host ({detail or 'no skill weight is set'})")
    return {**config, "skill_weights": weights}


def _preflight(
    config: dict[str, Any], complete: Optional[CompleteFn], spec: RunSpec,
    should_stop: Callable[[], bool], emit: Emit,
) -> dict[str, Any]:
    report = _npu_probe(spec, should_stop)
    return _apply_preflight(config, report, complete is not None, emit)


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
        code = json.dumps(payload, sort_keys=True)
        for wait in (*RUNNER_RETRY_WAITS, None):
            try:
                job, artifact = client.run(code, should_stop)
                break
            except NpuJobError as error:
                if not _runner_absent(error):
                    return {"ok": False, "error": str(error)}
                # A refused submission never reached the runner, so trying again cannot
                # put a second fold on the card. A lost status read might have run it.
                if wait is None or "POST /shell-executions" not in str(error):
                    return {"ok": False, "error": str(error), "runner_lost": True}
                if not _pause(wait, should_stop):
                    return {"ok": False, "error": "the search was stopped"}
        if artifact and artifact.get("proposal") == "empty":
            return {
                "ok": False,
                "empty": True,
                "error": str(artifact.get("error") or "the model returned no CDR mutations"),
            }
        valid, metrics, error = interpret_npu_job(job, artifact, metric_key=metric)
        return _measure_reading(artifact, valid, metrics, error)

    return measure


def _evidence(
    config: dict[str, Any], parent: _Member, state: dict[str, Any] | None,
) -> list[str]:
    """The context blocks the harness design prompt carries around a proposal.

    Scoring evidence is the difference between a blind edit and a directed one:
    the fold already measured ipTM, pLDDT and the contact fractions, and the
    gate already said why it refused a sequence.
    """
    state = state or {}
    lines = [
        "=== OBJECTIVE ===",
        "- Minimize the OpenDDE fold loss; the reported score is -loss, so a higher score is better.",
        "- Loss combines pLDDT, interface pLDDT, PAE, interface PAE, ipTM, contact and radius terms,",
        "  minus 0.1 x the ESM-2 pseudo-log-likelihood.",
        "- A sequence only enters the population when the 5 A contact gate passes:",
        "  the CDR share of interface contacts must exceed 0.5.",
    ]
    # The harness design context names the target; without it the model cannot tell what the CDRs bind.
    lines.extend([
        "",
        "=== TARGET ===",
        f"- Chain {config['target_chain']} sequence: {config['target_sequence']}",
        f"- Hotspots (1-based): {config['target_hotspots'] or 'none given'}",
    ])
    if state.get("analysis"):
        lines.extend(["", "=== ANTIBODY HEADER / CDR MAP ===", str(state["analysis"])])
    lines.extend(["", "=== PARENT ===", f"- Score: {parent.score}"])
    if parent.metrics:
        for name in ("iptm", "plddt", "cdr_contact_fraction", "framework_contact_fraction", "esm2"):
            if parent.metrics.get(name) is not None:
                lines.append(f"- {name}: {parent.metrics[name]}")
        components = parent.metrics.get("components")
        if isinstance(components, dict):
            worst = sorted(components.items(), key=lambda item: -float(item[1] or 0.0))[:4]
            lines.append("- Largest loss terms: " + ", ".join(f"{k}={v}" for k, v in worst))
    if parent.gate:
        lines.append(f"- Gate feedback: {parent.gate}")
    working = state.get("working")
    if isinstance(working, dict) and working.get("sequence") != parent.sequence:
        lines.extend(["", "=== WORKING PARENT (best sequence measured so far that the gate refused) ==="])
        working_metrics = working.get("metrics") or {}
        lines.append(f"- Score: {working.get('score')}")
        for name in ("iptm", "cdr_contact_fraction"):
            if working_metrics.get(name) is not None:
                lines.append(f"- {name}: {working_metrics[name]}")
        if working.get("gate"):
            lines.append(f"- Gate feedback: {working['gate']}")
    population = state.get("population") or []
    if population:
        lines.extend(["", "=== POPULATION ==="])
        for item in population[:8]:
            lines.append(
                f"- node {item['node']} score {item['score']} depth {item['depth']} {item['summary']}"
            )
    tries = state.get("tries") or []
    if tries:
        lines.extend(["", "=== ALREADY MEASURED (do not repeat these edits) ==="])
        for item in tries[-14:]:
            gate = f" gate: {item['gate']}" if item.get("gate") else ""
            lines.append(f"- {item['summary'] or 'no change'} -> score {item['score']}{gate}")
    lines.extend([
        "",
        "=== SEARCH STATE ===",
        f"- Best score so far: {state.get('best')}",
        f"- Consecutive cycles without global-best improvement: {int(state.get('streak') or 0)}",
        "- " + (
            "Use the gate evidence and the measured history to choose a basin-changing proposal."
            if state.get("streak") else "Continue refining the improved lineage."
        ),
    ])
    if state.get("reflection") and state["reflection"] != "none":
        lines.extend(["", "=== REFLECTION (latest reflected cycle) ===", str(state["reflection"])])
    return lines


def _analyze(complete: CompleteFn, config: dict[str, Any]) -> str:
    """One opening read of the CDR map, the harness analyze phase."""
    groups = "; ".join(f"{start}-{end}" for start, end in config["cdr_regions"])
    prompt = "\n".join([
        "Return plain text under 120 words. Do not name or discuss the protein.",
        "Describe the designable loops of this binder and the CDR-localized strategy for later edits.",
        "Rules, as the harness analyze agent:",
        "- Use 1-based sequence positions and inclusive ranges, verified against the supplied sequence.",
        "- The CDR ranges are the annotation. Do not infer CDRs from anything else.",
        "- Never invent residue-residue contacts, interaction geometry, or affinity.",
        "- Mark sequence-derived hypotheses as hypotheses. Only state observations the evidence supports.",
        "- Do not explain standard concepts. Be concise.",
        f"Chain {config['chain']} sequence:\n{config['sequence']}",
        f"Designable CDR ranges (1-based, last range is CDR3): {groups}.",
        f"Target chain {config['target_chain']} sequence:\n{config['target_sequence']}",
        f"Target hotspots (1-based): {config['target_hotspots'] or 'none given'}.",
    ])
    try:
        return complete(prompt).strip()[:900] or "none"
    except _Refusal:
        return "none"


def _reflect(complete: CompleteFn, cycle: int, skill: str, results: list[dict[str, Any]]) -> str:
    """Summarise the cycle that just folded, the harness reflect phase."""
    if not results:
        return "none"
    lines = [
        "Return plain text under 80 words. Do not name or discuss the protein.",
        f"Cycle {cycle} used skill `{skill}`. These edits were folded:",
    ]
    for item in results:
        gate = f" gate: {item['gate']}" if item.get("gate") else ""
        lines.append(f"- {item['summary'] or 'no change'} -> score {item['score']}{gate}")
    lines.extend([
        "Use only the evidence above. Higher score is better; a score of None was not measured.",
        "Never invent contacts or measurements. Mark anything the evidence does not show as unknown.",
        "Say which positions or substitutions helped, which hurt, and what the next cycle should try.",
    ])
    try:
        return complete("\n".join(lines)).strip()[:600] or "none"
    except _Refusal:
        return "none"


def _proposals(
    complete: CompleteFn, config: dict[str, Any], parent: _Member, count: int,
    state: dict[str, Any] | None = None,
) -> list[list[list[Any]]]:
    positions = ", ".join(str(pos) for pos in sorted(config["mutable"]))
    how_many = (
        "Exactly one substitution"
        if count == 1
        else f"{count} candidates, each with {config['mutation_min']} to {config['mutation_max']} substitutions"
    )
    prompt = "\n".join([
        "Return JSON only. Do not name or discuss the protein.",
        f"Chain {config['chain']}. {how_many}, encoded as [chain, 1-based position, new amino acid].",
        f"Allowed positions: {positions}.",
        f"Sequence:\n{parent.sequence}",
        "The new amino acid must differ from the sequence at that position.",
        "Candidates must yield distinct sequences, not merely different rationale.",
        "Use the parent sequence given here; do not replace it. Copy the chain ID exactly.",
        "",
        *_evidence(config, parent, state),
        "",
        '{"mutations": [[["<chain>", 0, "X"]]]}',
    ])
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
    state: dict[str, Any] | None = None,
) -> list[list[list[Any]]]:
    """Ask for a complete CDR assignment, then keep only residues that change."""
    ordered = sorted(config["mutable"])
    positions = ", ".join(str(pos) for pos in ordered)
    prompt = "\n".join([
        "Return JSON only. Do not name or discuss the protein.",
        f"Chain {config['chain']}. {count} full CDR redesign candidates.",
        "Each candidate must assign exactly one amino acid to every allowed "
        "position, including unchanged parent residues.",
        f"Allowed positions: {positions}.",
        f"Sequence:\n{parent.sequence}",
        "Candidates must yield different sequences; change at least three "
        "positions when that many exist.",
        "",
        *_evidence(config, parent, state),
        "",
        '{"mutations": [[["<chain>", 0, "X"]]]}',
    ])
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


def _cdr_slices(sequence: str, regions: list) -> list[str]:
    return [sequence[int(start) - 1:int(end)] for start, end in regions]


def _edit_distance(left: str, right: str) -> float:
    """Normalised edit distance, as the harness ``_normalized_edit_distance``."""
    if left == right:
        return 0.0
    normalizer = max(len(left), len(right))
    if normalizer == 0:
        return 0.0
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) / normalizer
    previous = list(range(len(right) + 1))
    for left_index, left_residue in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_residue in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_residue != right_residue),
            ))
        previous = current
    return previous[-1] / normalizer


def _cdr_distance(left: list[str], right: list[str]) -> float:
    """Weighted CDR distance. Three CDRs on one chain weigh 0.25, 0.25, 0.50 as the harness does."""
    pairs = list(zip_longest(left, right, fillvalue=""))
    if not pairs:
        return 0.0
    weights = [0.25, 0.25, 0.50] if len(pairs) == 3 else [1.0 / len(pairs)] * len(pairs)
    return sum(weight * _edit_distance(a, b) for weight, (a, b) in zip(weights, pairs))


def _mutation_tokens(reference: list[str], regions: list[str]) -> tuple[set[str], set[str]]:
    """Positions and substitutions that differ from the starting CDRs, as the harness tokens them."""
    positions: set[str] = set()
    mutations: set[str] = set()
    for index, (left, right) in enumerate(zip_longest(reference, regions, fillvalue="")):
        prefix = f"CDR{index + 1}"
        matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                position = f"{prefix}:{i1 + offset + 1}"
                positions.add(position)
                mutations.add(f"{position}:{left[i1 + offset]}>{right[j1 + offset]}")
            for left_index in range(i1 + paired, i2):
                position = f"{prefix}:{left_index + 1}"
                positions.add(position)
                mutations.add(f"{position}:{left[left_index]}>-")
            for insertion, right_index in enumerate(range(j1 + paired, j2), start=1):
                position = f"{prefix}:ins{i2 + 1}.{insertion}"
                positions.add(position)
                mutations.add(f"{position}:->{right[right_index]}")
    return positions, mutations


def _retain(
    members: list[_Member], config: dict[str, Any], reference: list[str],
) -> tuple[set[int], dict[int, str]]:
    """Which members the population keeps: best first, then the harness constraints.

    The order is the harness ``ConstrainedElitePopulation.update``: duplicates and
    capacity first, then the minimum CDR distance to every kept member, then the
    per-position and per-mutation reuse caps counted against the starting CDRs.
    """
    cap = max(1, int(config.get("population_size") or POPULATION_SIZE))
    min_distance = float(config.get("constrained_min_cdr_distance", MIN_CDR_DISTANCE))
    position_cap = max(1, math.ceil(cap * float(
        config.get("constrained_max_position_reuse_fraction", POSITION_REUSE_FRACTION))))
    mutation_cap = max(1, math.ceil(cap * float(
        config.get("constrained_max_mutation_reuse_fraction", MUTATION_REUSE_FRACTION))))
    kept: set[int] = set()
    actions: dict[int, str] = {}
    kept_regions: list[list[str]] = []
    positions_used: dict[str, int] = {}
    mutations_used: dict[str, int] = {}
    seen: set[str] = set()
    for member in sorted(members, key=lambda item: item.score, reverse=True):
        if member.sequence in seen:
            actions[member.index] = "duplicate_not_retained"
            continue
        seen.add(member.sequence)
        if len(kept_regions) >= cap:
            actions[member.index] = "capacity_not_retained"
            continue
        regions = _cdr_slices(member.sequence, config["cdr_regions"])
        nearest = min((_cdr_distance(regions, other) for other in kept_regions), default=math.inf)
        if nearest < min_distance:
            actions[member.index] = "cdr_distance_not_retained"
            continue
        positions, mutations = _mutation_tokens(reference, regions)
        if any(positions_used.get(token, 0) >= position_cap for token in positions):
            actions[member.index] = "position_reuse_not_retained"
            continue
        if any(mutations_used.get(token, 0) >= mutation_cap for token in mutations):
            actions[member.index] = "mutation_reuse_not_retained"
            continue
        kept.add(member.index)
        kept_regions.append(regions)
        actions[member.index] = "retained_constrained_elite"
        for token in positions:
            positions_used[token] = positions_used.get(token, 0) + 1
        for token in mutations:
            mutations_used[token] = mutations_used.get(token, 0) + 1
    return kept, actions


def _prefer_working(candidate: _Member, incumbent: _Member | None) -> _Member:
    """The harness ``WorkingParentTracker``: higher CDR contact fraction first, then score."""
    if incumbent is None:
        return candidate

    def contact(member: _Member) -> float:
        value = (member.metrics or {}).get("cdr_contact_fraction")
        return float(value) if isinstance(value, (int, float)) else 0.0

    def score(member: _Member) -> float:
        return float(member.score) if isinstance(member.score, (int, float)) else -math.inf

    if contact(candidate) != contact(incumbent):
        return candidate if contact(candidate) > contact(incumbent) else incumbent
    return candidate if score(candidate) > score(incumbent) else incumbent


#: The harness ``QUALITY_CHECK_SYSTEM_PROMPT``, verbatim. The completion call has no system
#: role yet, so it leads the quality prompt as its first block.
QUALITY_SYSTEM_PROMPT = """You are the Antibody Quality Agent.

Assess only the candidates and current-run evidence supplied by the workflow.
Treat objective measurements as authoritative, preserve candidate IDs exactly,
and never invent measurements, residue annotations, or experimental facts.
Missing evidence is uncertainty. Return the configured structured output with
one result for every supplied candidate and no additional candidates. Keep all
reasoning concise and use normal English spacing."""

#: The harness ``QUALITY_CHECK_BATCH_PROMPT`` layout, with the structured output
#: replaced by a JSON shape this engine parses itself.
QUALITY_PROMPT = "\n".join([
    "Assess the supplied antibody candidates.",
    "",
    "Return JSON only, shaped as {\"results\": {\"<candidate id>\": {\"pass_check\": true, \"reasoning\": \"<short>\"}}}.",
    "Include every supplied candidate ID exactly once. Do not alter or recompute the objective evidence.",
    "",
    "<antibody_identification>",
    "{analysis}",
    "</antibody_identification>",
    "",
    "<objective_developability_evidence>",
    "```json",
    "{objective}",
    "```",
    "</objective_developability_evidence>",
    "",
    "<candidates>",
    "{candidates}",
    "</candidates>",
])


def _context_json(value: Any) -> str:
    """The harness ``context_json``: compact, sorted keys, non-ASCII kept."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _developability_objective(keys: list[str]) -> dict[str, Any]:
    """The objective developability result the harness gets for antibody candidates.

    The open-source harness backend (``servers/backends/developability_filter.py``) is a
    stub that returns ``{"available": False}`` per candidate, and the harness wraps it as
    ``{"summary": ..., "results": ...}``. The colleague's CRLF2 run logged
    ``available:false``, so the quality agent judges from sequence alone. A real
    developability backend would add evidence the harness never had.
    """
    results = {key: {"available": False} for key in keys}
    return {"summary": results, "results": results}


def _quality_verdicts(
    complete: CompleteFn | None,
    analysis: str,
    readings: list[dict[str, Any]],
    *,
    exempt: bool,
) -> tuple[list[bool | None], str]:
    """One batch quality check over the folds that passed the gate with ipTM at the threshold.

    Returns a verdict per reading: ``None`` when the check does not apply, otherwise
    whether the model let the candidate into the population. A candidate is admitted
    only when its ``pass_check`` is true. If the check itself fails (model error or
    no results object), nothing from that batch is admitted. The harness instead
    retries the cycle, which would fold the batch again; that difference is kept
    and documented.
    """
    verdicts: list[bool | None] = [None] * len(readings)
    if complete is None or exempt:
        return verdicts, ""
    eligible: dict[str, int] = {}
    summaries: list[dict[str, Any]] = []
    for index, reading in enumerate(readings):
        metrics = reading.get("metrics") or {}
        iptm = metrics.get("iptm")
        if not reading.get("ok") or reading.get("error"):
            continue
        if not isinstance(iptm, (int, float)) or float(iptm) < QUALITY_THRESHOLD:
            continue
        key = f"c{index}"
        eligible[key] = index
        summaries.append({
            "candidate_id": key,
            "sequence": reading.get("sequence"),
            "score": reading.get("score"),
            "metrics": {name: metrics.get(name) for name in REPORTED_METRICS if name in metrics},
        })
    if not eligible:
        return verdicts, ""
    objective = _developability_objective(list(eligible))
    prompt = "\n\n".join([
        QUALITY_SYSTEM_PROMPT,
        QUALITY_PROMPT.replace("{analysis}", analysis)
        .replace("{objective}", _context_json(objective))
        .replace("{candidates}", _context_json(summaries)),
    ])
    try:
        text = complete(prompt)
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start:end + 1] if start >= 0 and end > start else text)
        results = parsed.get("results") if isinstance(parsed, dict) else None
        if not isinstance(results, dict):
            raise ValueError("the quality check returned no results object")
    except Exception as error:  # the model call or its JSON failed; nothing from this batch is admitted
        for index in eligible.values():
            verdicts[index] = False
        return verdicts, str(error)
    for key, index in eligible.items():
        item = results.get(key)
        passed = isinstance(item, dict) and item.get("pass_check") is True
        verdicts[index] = passed
    return verdicts, ""


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
    finite = [member.score for member in pool if member.score is not None and math.isfinite(member.score)]
    if not finite or max(finite) == min(finite):
        softmax = [1.0] * len(pool)
    else:
        lower, upper = min(finite), max(finite)
        softmax = [
            math.exp(max(-700.0, ((member.score - lower) / (upper - lower) - 1.0) / max(temperature, 1e-6)))
            if member.score is not None else 0.0
            for member in pool
        ]
    total = sum(softmax)
    mix = min(1.0, max(0.0, float(uniform_fraction)))
    uniform = mix / len(pool)
    weights = [(1.0 - mix) * value / total + uniform for value in softmax]
    draw = rng.random() * sum(weights)
    cursor = 0.0
    for member, weight in zip(pool, weights):
        cursor += weight
        if draw <= cursor:
            return member
    return pool[-1]


def _temperature(cycle: int, cycles: int) -> float:
    """Anneal 1.0 to 0.2 geometrically over the cycles, as ``ParentSampler.temperature`` does."""
    progress = min(1.0, max(0.0, cycle / max(1, cycles - 1)))
    return 1.0 * (0.2 / 1.0) ** progress


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
