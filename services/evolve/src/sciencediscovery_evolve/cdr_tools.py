# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
# Licensed under the Apache License, Version 2.0 (the "License");
# you may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
"""CDR tools bound to current-run candidates and the existing runner grant."""
from __future__ import annotations

import json
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class CandidateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ids: list[str] = Field(min_length=1, max_length=3)


class SequenceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: Literal["binder", "target"] = "target"


class QualityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ids: list[str] = Field(min_length=1, max_length=10000)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


TOOL_ARGS = {
    "protrek_sequence_search": SequenceArgs,
    "protrek_structure_search": CandidateArgs,
    "analyze_current_cycle_structure": CandidateArgs,
    "analyze_epitope": CandidateArgs,
    "analyze_evolution_tree": EmptyArgs,
    "check_antibody_developability": QualityArgs,
}

DESCRIPTIONS = {
    "protrek_sequence_search": "Search sequence neighbours for the configured binder or target. Unavailable external services are reported explicitly.",
    "protrek_structure_search": "Search structure neighbours of verified current-run candidate IDs. Never provide file paths.",
    "analyze_current_cycle_structure": "Run bounded PLIP on up to three current-cycle candidate complexes, preserving chain and residue IDs.",
    "analyze_epitope": "Analyze CDR contacts and configured hotspot coverage for up to three current-cycle candidates.",
    "analyze_evolution_tree": "Analyze the actual current-run candidate history and parent links; call at most once per reflection.",
    "check_antibody_developability": "Return objective developability evidence for supplied candidate IDs; public Harness backend is unavailable.",
}


def external_coordinates(value: Any) -> Any:
    """Match Harness tool projection: sequence indices are 1-based; structure IDs stay unchanged."""
    if isinstance(value, list):
        return [external_coordinates(item) for item in value]
    if not isinstance(value, dict):
        return value
    def positions(item):
        if isinstance(item, dict):
            return {key: positions(part) for key, part in item.items()}
        if isinstance(item, list):
            return [int(p) + 1 for p in item]
        return item
    result = {}
    for key, item in value.items():
        if key == "sequence_position" and item is not None:
            result[key] = int(item) + 1
        elif key in {"cdr_regions", "mutable_positions", "fixed_residues"} and isinstance(item, dict):
            result[key] = positions(item)
        else:
            result[key] = external_coordinates(item)
    return result


class CdrTools:
    def __init__(self, config: dict, measure: Callable[[dict], dict]) -> None:
        self.config, self.measure = config, measure
        self.history: list[dict] = []
        self.current: list[dict] = []
        self.quality_candidates: list[dict] = []
        self.runner_lost = False
        self.cycle = 0
        self.run_key = ""

    def definitions(self, allowed: tuple[str, ...]) -> list[dict]:
        return [{"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name],
                 "parameters": TOOL_ARGS[name].model_json_schema()}} for name in allowed]

    def catalog(self) -> list[dict]:
        return [{"candidate_id": item["candidate_id"], "structure_available": bool(item.get("structure_id"))}
                for item in self.current]

    def history_summary(self) -> dict:
        scored = [item for item in self.history if isinstance(item.get("objective"), (int, float))]
        return {"candidate_count": len(self.history), "scored_count": len(scored),
                "best_loss": min((item["objective"] for item in scored), default=None),
                "recent": [{k: item.get(k) for k in ("candidate_id", "parent_id", "objective", "mutations", "gate", "quality_passed", "retained")}
                           for item in self.history[-14:]]}

    def execute(self, name: str, arguments: dict, *, timeout: float = 120) -> dict:
        if name not in TOOL_ARGS:
            raise ValueError("unknown CDR tool")
        args = TOOL_ARGS[name].model_validate(arguments).model_dump()
        selected: list[dict] = []
        if "candidate_ids" in args:
            candidates = self.quality_candidates if name == "check_antibody_developability" else self.current
            by_id = {item["candidate_id"]: item for item in candidates}
            for candidate_id in args["candidate_ids"]:
                if candidate_id not in by_id:
                    raise ValueError(f"candidate is not available to this phase: {candidate_id}")
                selected.append(by_id[candidate_id])
        if name == "check_antibody_developability":
            result = {item["candidate_id"]: {"available": False} for item in selected}
            return {"summary": result, "results": result}
        if name != "protrek_sequence_search" and name != "analyze_evolution_tree":
            missing = [item["candidate_id"] for item in selected if not item.get("structure_id")]
            if missing:
                return {"available": False, "reason": "complete structure unavailable", "candidate_ids": missing}
        payload = {
            "analysis_tool": name, "arguments": args, "candidates": selected,
            "history": self.history if name == "analyze_evolution_tree" else [],
            "chain": self.config["chain"], "target_chain": self.config["target_chain"],
            "sequence": self.config["sequence"], "target_sequence": self.config["target_sequence"],
            "cdr_regions": self.config["cdr_regions"], "target_hotspots": self.config["target_hotspots"],
            "tool_timeout_seconds": min(timeout, 120),
            "cycle": self.cycle, "reflection_interval": self.config["reflection_interval"],
            "run_key": self.run_key,
        }
        result = self.measure(payload)
        if result.get("runner_lost"):
            self.runner_lost = True
            raise RuntimeError(str(result.get("error") or "runner lost during analysis"))
        value = result.get("tool_result")
        if not isinstance(value, dict):
            raise RuntimeError(str(result.get("error") or "analysis returned no tool result"))
        # Raw confidence matrices remain on the runner; only bounded analytical evidence is sent to the model.
        if len(json.dumps(value, default=str)) > 100000:
            raise RuntimeError("analysis result exceeded its evidence limit")
        return external_coordinates(value)
