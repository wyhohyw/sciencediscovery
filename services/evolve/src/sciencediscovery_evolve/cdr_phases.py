# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
# Licensed under the Apache License, Version 2.0 (the "License");
# you may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
"""Bounded CDR role sessions using the existing run-scoped model transport.

Role text, skills and reflection schema originate from the pinned Harness;
see CDR_HARNESS_NOTICE.md. This module owns no population or chat-session state.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import cdr_harness_prompts as prompts
from .cdr_harness_reflection import ReflectOutput


class PhaseError(RuntimeError):
    """A phase did not return a usable structured answer."""


class AnalyzeOutput(BaseModel):
    downstream_header: str = Field(min_length=1)
    report: str = ""
    evidence_provenance: list[str] = Field(default_factory=list)


class QualityResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    expressivity: str = "Unknown"
    immunogenicity: str = "Unknown"
    aggregation: str = "Unknown"
    solubility: str = "Unknown"
    specificity: str = "Unknown"
    liability: str = "Unknown"
    pass_check: StrictBool
    reasoning: str
    overall_risk_level: str


class QualityOutput(BaseModel):
    results: dict[str, QualityResult]


class ParentDecision(BaseModel):
    selected_parent_name: str
    selection_mode: Literal["exploit", "explore"]
    confidence: Literal["low", "medium", "high"]
    rationale: str


class ParentOutput(BaseModel):
    parent_selection: ParentDecision


@dataclass(frozen=True)
class Profile:
    system: str
    skills: tuple[str, ...]
    allowed: tuple[str, ...]
    turns: int
    errors_fatal: bool = True


PROFILES = {
    "analyze": Profile(prompts.ANALYZE_SYSTEM_PROMPT,
        ("epitope-analysis", "structure-analysis"),
        ("protrek_sequence_search", "protrek_structure_search"), 4),
    "reflect": Profile(prompts.REFLECT_SYSTEM_PROMPT,
        ("evolutionary-analysis", "epitope-analysis", "structure-analysis"),
        ("analyze_current_cycle_structure", "analyze_evolution_tree", "analyze_epitope"), 6, False),
    "quality": Profile(prompts.QUALITY_CHECK_SYSTEM_PROMPT,
        ("developability-filter",), ("check_antibody_developability",), 3),
    "design": Profile(prompts.DESIGN_SYSTEM_PROMPT,
        ("cdr-point-mutation", "cdr-full-redesign"), (), 6),
    "parent_selection": Profile(prompts.PARENT_SELECTION_SYSTEM_PROMPT, (), (), 1),
}


def json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    value = json.loads(text[start:end + 1] if start >= 0 and end > start else text)
    if not isinstance(value, dict):
        raise PhaseError("the phase must return a JSON object")
    return value


class CdrPhaseRunner:
    def __init__(self, chat: Callable, tools: Any, config: dict, *,
                 should_stop: Callable[[], bool], emit: Callable[[dict], None]) -> None:
        self.chat, self.tools, self.config = chat, tools, config
        self.should_stop, self.emit = should_stop, emit
        self.skills = json.loads(files(__package__).joinpath("cdr_skills.json").read_text(encoding="utf-8"))
        self.analysis = "none"
        self.context: dict[str, Any] = {}

    def run(self, role: str, prompt: str, schema: type[BaseModel] | None = None) -> dict:
        if self.should_stop():
            raise PhaseError("the search was stopped")
        profile = PROFILES[role]
        instructions = [profile.system, "Use only tools registered for this phase."]
        instructions.append("Tool adapter: select structures by candidate_ids from the verified catalog. "
            "Do not send structure paths or candidate databases. Registered tool schemas are authoritative; "
            "the listed skills are already loaded inline, so no use_skill call is needed.")
        for name in profile.skills:
            instructions.append(f"<skill id={json.dumps(name)}>\n{self.skills[name]}\n</skill>")
        if schema is not None:
            instructions.append("Return one JSON object matching this schema:\n" + json.dumps(schema.model_json_schema()))
        if role == "design":
            instructions.append("ScienceDiscovery adapter: the exact JSON shape in the host request is authoritative. "
                "For a mutations request return its mutations array using the supplied 1-based positions. "
                "For routing return skill_id; for parent selection return parent_id. "
                "Generation backends are dispatched by the search engine after routing.")
        messages: list[dict] = [{"role": "system", "content": "\n\n".join(instructions)},
                                {"role": "user", "content": prompt}]
        definitions = self.tools.definitions(profile.allowed)
        remaining = int(self.config.get("phase_token_budget", 65536))
        deadline = time.monotonic() + float(self.config.get("phase_timeout_seconds", 300))
        tree_calls = 0
        calls_used = 0
        for turn in range(profile.turns + 1):
            if self.should_stop():
                raise PhaseError("the search was stopped")
            if remaining <= 0 or time.monotonic() >= deadline:
                raise PhaseError(f"{role} phase budget exhausted")
            # At the tool-round limit one final synthesis turn is still allowed.
            message = self.chat(messages, definitions if turn < profile.turns else [],
                max_tokens=min(24576, remaining), timeout=max(0.01, deadline - time.monotonic()))
            usage = message.get("_usage") or {}
            usage = usage if isinstance(usage, dict) else {}
            total = usage.get("total_tokens")
            content = message.get("content") or ""
            # A provider omitting usage must not make the stage's local ceiling unbounded.
            remaining -= int(total) if isinstance(total, (int, float)) and not isinstance(total, bool) and 0 <= total <= 2**53 - 1 else max(1, len(json.dumps(message, default=str)) // 4)
            if remaining < 0:
                raise PhaseError(f"{role} phase token budget exhausted")
            if message.get("_error"):
                raise PhaseError(str(message["_error"]))
            assistant = {k: v for k, v in message.items() if not k.startswith("_")}
            assistant["role"] = "assistant"
            calls = assistant.get("tool_calls") or []
            if not calls:
                result = json_object(str(content))
                if schema is not None:
                    result = schema.model_validate(result).model_dump(mode="json")
                self.emit({"type": "log", "level": "info", "message": f"{role} phase completed ({calls_used} tool calls)"})
                return result
            if turn >= profile.turns or not isinstance(calls, list):
                raise PhaseError(f"{role} exceeded its tool-turn limit")
            if calls_used + len(calls) > profile.turns * 4:
                raise PhaseError(f"{role} exceeded its tool-call limit")
            ids = [call.get("id") for call in calls if isinstance(call, dict)]
            if len(ids) != len(calls) or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                raise PhaseError("invalid tool call IDs")
            messages.append(assistant)
            for call in calls:
                if self.should_stop() or time.monotonic() >= deadline:
                    raise PhaseError("phase stopped before executing tools")
                function = call.get("function") or {}
                name = function.get("name")
                try:
                    if name not in profile.allowed:
                        raise PhaseError(f"tool is not allowed in {role}: {name}")
                    if name == "analyze_evolution_tree":
                        tree_calls += 1
                        if tree_calls > 1:
                            raise PhaseError("analyze_evolution_tree may be called once per reflection")
                    args = json.loads(function.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise PhaseError("tool arguments must be an object")
                    result = self.tools.execute(name, args, timeout=max(0.01, deadline - time.monotonic()))
                except Exception as error:
                    if profile.errors_fatal:
                        raise PhaseError(f"{name}: {error}") from error
                    result = {"available": False, "reason": str(error)}
                calls_used += 1
                self.emit({"type": "log", "level": "info", "message": f"{role} tool {name}: " +
                    ("unavailable" if isinstance(result, dict) and result.get("available") is False else "completed")})
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
        raise PhaseError(f"{role} did not complete")

    def analyze(self, config: dict) -> str:
        chain, target = config["chain"], config["target_chain"]
        mutable = config["mutable"]
        prompt = prompts.ANALYZE_REPORT_PROMPT.format(
            target_name=config.get("target_name", "configured target"),
            target_sequence=json.dumps({target: config["target_sequence"]}),
            target_length=len(config["target_sequence"]), hotspots=config["target_hotspots"],
            binder_name="node0", binder_sequence={chain: config["sequence"]},
            binder_length=len(config["sequence"]),
            binder_fixed_residues={chain: [p for p in range(1, len(config["sequence"]) + 1) if p not in mutable]},
            binder_cdr_regions={chain: config["cdr_regions"]})
        prompt += '\nReturn the marked header as JSON field "downstream_header", with report and evidence_provenance.'
        result = self.run("analyze", prompt, AnalyzeOutput)
        self.analysis = result["downstream_header"]
        return self.analysis

    def reflect(self, cycle: int, skill: str, results: list[dict]) -> str:
        context = self.context
        best = context.get("best") or {}
        prompt = prompts.REFLECT_ANALYSIS_PROMPT.format(
            target_name=self.config.get("target_name", "configured target"),
            target_sequence=self.config["target_sequence"], target_length=len(self.config["target_sequence"]),
            hotspots=self.config["target_hotspots"], phase_analyze_summary=self.analysis,
            candidates_json_path="current-run history (available through analyze_evolution_tree)",
            objective_key="loss", minimize=True, binder_chain_ids=[self.config["chain"]],
            target_chain_ids=[self.config["target_chain"]], quality_check_summary=json.dumps(context.get("quality", {})),
            trajectory_summary=json.dumps(self.tools.history_summary()), cycle_num=cycle,
            parent_name=best.get("candidate_id", "none"), parent_backend="opendde",
            parent_sequence=best.get("sequence", ""), parent_iptm=best.get("metrics", {}).get("iptm"),
            parent_plddt=best.get("metrics", {}).get("plddt"), parent_ranking_score=best.get("objective"),
            parent_loglikelihood=best.get("metrics", {}).get("esm2"), parent_metric_context=json.dumps(best.get("metrics", {})),
            fold_results_table=json.dumps(results), structure_path_catalog=json.dumps(self.tools.catalog()),
            epitope_analysis="Call analyze_epitope for current-run candidate IDs; no contacts may be inferred.")
        result = self.run("reflect", prompt, ReflectOutput)
        return ReflectOutput.model_validate(result).to_design_directives()

    def quality(self, prompt: str) -> str:
        return json.dumps(self.run("quality", prompt, QualityOutput))

    def design(self, prompt: str) -> str:
        return json.dumps(self.run("design", prompt))

    def parent_selection(self, population: list[dict]) -> str:
        return str(self.run("parent_selection", "Select a current population parent:\n" +
                           json.dumps(population), ParentOutput)["parent_selection"]["selected_parent_name"])

    def __call__(self, prompt: str) -> str:
        return self.design(prompt)
