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

One expansion is one point-mutated sequence. The model proposes substitutions
inside the configured CDR ranges; Python applies them and refuses a framework
edit. Scoring is a single NPU job per sequence (the fixed program folds that
sequence and prints ``score``, higher meaning better). A candidate that fails
the job or its gate is recorded and does not enter the population.

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
ScoreFn = Callable[[dict[str, Any]], tuple[bool, float | None, str]]
CompleteFn = Callable[[str], str]


@dataclass
class _Member:
    index: int
    sequence: str
    score: float | None
    visits: int = 0
    mutations: list[list[Any]] = field(default_factory=list)


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
    ) -> None:
        self._complete = complete
        self._score = score

    def run(self, spec: RunSpec, emit: Emit, should_stop: Callable[[], bool]) -> None:
        try:
            config = _config(spec.baseline_code)
            complete = self._complete or _model_complete(spec, should_stop)
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
        seed = _Member(index=0, sequence=config["sequence"], score=seed_score if seed_ok else None)
        emit(events.seeded(
            0, seed.score,
            code_hash=_digest(seed.sequence),
            code_chars=len(seed.sequence),
        ))
        if not seed_ok:
            emit(events.log("warn", seed_error or "the starting sequence was not scored"))

        population = [seed]
        best = 0 if seed_ok else None
        produced = 0
        node = 0
        status = "succeeded"
        reason = "max_iters"
        while produced < spec.expansions:
            if should_stop():
                status, reason = "stopped", "stopped"
                break
            parent = _select(population, _temperature(produced, spec.expansions), rng)
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
                emit(events.expanded(
                    node, parent.index, 1, value if ok else None, ok,
                    change_summary=summary,
                    code_hash=_digest(child),
                    code_chars=len(child),
                    error=None if ok else why,
                ))
                if not ok:
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
        "candidates_per_cycle": int(parsed.get("candidates_per_cycle") or 4),
        "mutation_min": 1,
        "mutation_max": 3,
    }


def _batch(config: dict[str, Any], spec: RunSpec, produced: int) -> int:
    requested = spec.options.get("candidatesPerCycle", config["candidates_per_cycle"])
    try:
        size = int(requested)
    except (TypeError, ValueError):
        size = 4
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


def _model_complete(spec: RunSpec, should_stop: Callable[[], bool]) -> CompleteFn:
    if not spec.llm_url or not spec.llm_token:
        raise _Refusal("CDR search needs the run's model proxy")
    from .completion import completion_for

    return completion_for(
        spec.llm_url, spec.llm_token,
        max_tokens=spec.max_tokens_per_call,
        thinking=spec.thinking or None,
        thinking_effort=spec.thinking_effort or None,
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
        if artifact and artifact.get("gate_passed") is False:
            return False, None, str(artifact.get("error") or error or "gate")
        if not valid:
            return False, None, error or "not scored"
        return True, float(metrics["score"]), ""

    return score


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


def _apply(
    sequence: str, config: dict[str, Any], mutations: list[list[Any]],
) -> tuple[str | None, str, str]:
    child = sequence
    notes: list[str] = []
    seen: set[int] = set()
    if not config["mutation_min"] <= len(mutations) <= config["mutation_max"]:
        return None, "mutation count", "a candidate must contain 1 to 3 CDR substitutions"
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


def _select(population: list[_Member], temperature: float, rng: random.Random) -> _Member:
    scored = [member for member in population if member.score is not None]
    pool = scored or population
    if len(pool) == 1:
        return pool[0]
    weights = [math.exp((member.score or 0.0) / temperature) for member in pool]
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


def _digest(sequence: str) -> str:
    return "sha256:" + hashlib.sha256(sequence.encode("utf-8")).hexdigest()
