#!/usr/bin/env python3
# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Prepare an antibody-design input from one antigen-antibody complex.

Literature search stays outside this script. For a complex, an antigen residue
is an epitope residue when any of its heavy atoms is within the cutoff of any
heavy atom on the named antibody chains. The default cutoff is 4.5 angstroms.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
from pathlib import Path
from typing import Any


DEFAULT_CUTOFF = 4.5
HOTSPOT_WARNING = 40
LEVELS = {
    "hotspots": "L1",
    "num_designs": "L2",
    "diffuser_t": "L2",
    "final_step": "L2",
    "protenix_n_sample": "L2",
    "protenix_seeds": "L2",
    "protenix_use_msa": "L2",
    "designed_sequences": "L3",
}
GLYCAN_RESIDUES = {
    "A2G", "AMU", "ARA", "BGC", "BMA", "FUC", "FUL", "GAL", "GLA", "GLC",
    "GMH", "MAN", "MMA", "NAG", "NDG", "NGA", "RAM", "RIB", "SIA", "SLB", "XYL",
}
SOLVENT_RESIDUES = {
    "ACT", "BME", "CA", "CD", "CL", "CO", "CU", "DMS", "DOD", "EDO", "EOH",
    "FE", "FMT", "GOL", "H2O", "HEP", "HOH", "IMD", "IPA", "K", "MES", "MG",
    "MN", "MOH", "NA", "NI", "NO3", "PEG", "PG4", "PO4", "SO4", "SOL", "TIP",
    "TRS", "WAT", "ZN",
}
HOTSPOT_LABEL = re.compile(r"^[A-Za-z][0-9]+$")


class ScriptError(Exception):
    """A stop the caller can read from the JSON result."""


def parse_pdb(text: str) -> tuple[list[dict[str, Any]], int]:
    """Return atoms from the first model and a count of malformed atom lines."""
    atoms: list[dict[str, Any]] = []
    malformed = 0
    seen_model = False
    for raw in text.splitlines():
        if raw.startswith("MODEL"):
            if seen_model:
                break
            seen_model = True
            continue
        if raw.startswith("ENDMDL"):
            break
        if not raw.startswith(("ATOM  ", "HETATM")):
            continue
        atom = parse_atom(raw)
        if atom is None:
            malformed += 1
            continue
        if atom["altloc"] not in {"", "A"}:
            continue
        atoms.append(atom)
    return atoms, malformed


def parse_atom(line: str) -> dict[str, Any] | None:
    if len(line) < 54:
        return None
    try:
        number = int(line[22:26])
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
    except ValueError:
        return None
    element = line[76:78].strip().upper() if len(line) >= 78 else ""
    return {
        "record": line[:6].strip(),
        "name": line[12:16].strip(),
        "altloc": line[16].strip(),
        "residue": line[17:20].strip(),
        "chain": line[21].strip() or "_",
        "number": number,
        "insertion": line[26].strip(),
        "x": x,
        "y": y,
        "z": z,
        "element": element,
    }


def is_hydrogen(atom: dict[str, Any]) -> bool:
    element = str(atom["element"])
    if element in {"H", "D"}:
        return True
    if element:
        return False
    name = str(atom["name"]).upper()
    if not name:
        return False
    if name[0] == "H":
        return True
    return name[0].isdigit() and "H" in name[:2]


def distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    return math.sqrt(
        (left["x"] - right["x"]) ** 2
        + (left["y"] - right["y"]) ** 2
        + (left["z"] - right["z"]) ** 2
    )


def residue_key(atom: dict[str, Any]) -> tuple[str, int, str, str]:
    return (str(atom["chain"]), int(atom["number"]), str(atom["insertion"]), str(atom["residue"]))


def heavy_atoms(atoms: list[dict[str, Any]], chains: set[str], record: str) -> list[dict[str, Any]]:
    return [
        atom for atom in atoms
        if atom["record"] == record and atom["chain"] in chains and not is_hydrogen(atom)
    ]


def find_contacts(
    atoms: list[dict[str, Any]],
    antigen_chain: str,
    antibody_chains: list[str],
    cutoff: float,
    malformed: int = 0,
) -> dict[str, Any]:
    if not 0 < cutoff <= 25:
        raise ScriptError("距离阈值必须大于 0 且不超过 25 Å")
    antigen = heavy_atoms(atoms, {antigen_chain}, "ATOM")
    present_antibody = [chain for chain in antibody_chains if heavy_atoms(atoms, {chain}, "ATOM")]
    antibody = heavy_atoms(atoms, set(present_antibody), "ATOM")
    result: dict[str, Any] = {
        "antigen_chain": antigen_chain,
        "antibody_chains": antibody_chains,
        "cutoff_angstrom": cutoff,
        "malformed_atom_records": malformed,
        "epitope_residues": [],
        "hotspots": "",
        "unrepresentable_hotspots": [],
        "glycan_contacts": [],
        "ignored_solvent_contacts": 0,
        "other_hetatm_contacts": [],
        "warnings": [],
        "error": "",
    }
    missing = [chain for chain in antibody_chains if chain not in present_antibody]
    if missing and present_antibody:
        result["warnings"].append("这些抗体链没有重原子：" + ",".join(missing))
    if not antigen:
        result["error"] = f"抗原链 {antigen_chain} 没有重原子"
        return result
    if not antibody:
        result["error"] = "没有抗体链上的重原子，不能按复合物距离计算表位"
        return result

    grouped: dict[tuple[str, int, str, str], list[dict[str, Any]]] = {}
    for atom in antigen:
        grouped.setdefault(residue_key(atom), []).append(atom)
    residues = []
    for key, group in grouped.items():
        nearest = min(distance(atom, other) for atom in group for other in antibody)
        if nearest <= cutoff:
            chain, number, insertion, residue = key
            has_ca = any(atom["name"] == "CA" for atom in group)
            label = f"{chain}{number}"
            representable = bool(has_ca and not insertion and HOTSPOT_LABEL.fullmatch(label))
            residues.append({
                "chain": chain,
                "number": number,
                "insertion": insertion,
                "residue": residue,
                "nearest_angstrom": round(nearest, 3),
                "has_ca": has_ca,
                "hotspot": label if representable else "",
            })
    residues.sort(key=lambda item: (item["chain"], item["number"], item["insertion"]))
    result["epitope_residues"] = residues
    representable = [item["hotspot"] for item in residues if item["hotspot"]]
    for item in residues:
        if item["hotspot"]:
            continue
        reason = "没有 CA" if not item["has_ca"] else "残基编号不能写成热点"
        result["unrepresentable_hotspots"].append({
            "chain": item["chain"],
            "number": item["number"],
            "insertion": item["insertion"],
            "residue": item["residue"],
            "reason": reason,
        })
    result["hotspots"] = format_hotspots(representable)

    for atom in atoms:
        if atom["record"] != "HETATM" or is_hydrogen(atom):
            continue
        nearest = min(distance(atom, other) for other in antibody)
        if nearest > cutoff:
            continue
        entry = {
            "chain": atom["chain"],
            "number": atom["number"],
            "insertion": atom["insertion"],
            "residue": atom["residue"],
            "nearest_angstrom": round(nearest, 3),
        }
        if atom["residue"] in SOLVENT_RESIDUES:
            result["ignored_solvent_contacts"] += 1
        elif atom["residue"] in GLYCAN_RESIDUES and atom["chain"] == antigen_chain:
            _keep_closer(result["glycan_contacts"], entry)
        elif atom["chain"] == antigen_chain:
            _keep_closer(result["other_hetatm_contacts"], entry)

    if not residues:
        result["error"] = f"{cutoff:g} Å 内没有抗原–抗体接触残基"
    elif not representable:
        result["error"] = "接触残基都不能写成抗体设计热点"
    elif len(representable) > HOTSPOT_WARNING:
        result["warnings"].append(f"热点有 {len(representable)} 个，超过 {HOTSPOT_WARNING}，先核对是不是把相邻晶胞链算进来了")
    return result


def _keep_closer(items: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    identity = ("chain", "number", "insertion", "residue")
    for item in items:
        if all(item[key] == entry[key] for key in identity):
            if entry["nearest_angstrom"] < item["nearest_angstrom"]:
                item["nearest_angstrom"] = entry["nearest_angstrom"]
            return
    items.append(entry)


def format_hotspots(labels: list[str]) -> str:
    unique = sorted(set(labels), key=_hotspot_sort_key)
    if not unique:
        return ""
    return "[" + ",".join(unique) + "]"


def _hotspot_sort_key(label: str) -> tuple[str, int]:
    return (label[0], int(label[1:]))


def parse_hotspots(value: Any) -> list[str]:
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    labels = []
    for item in re.split(r"[,;\s]+", text):
        if not item:
            continue
        if not HOTSPOT_LABEL.fullmatch(item):
            raise ScriptError(f"热点格式不能交给现有抗体设计：{item}")
        labels.append(item)
    if len(labels) != len(set(labels)):
        raise ScriptError("热点里有重复残基")
    return labels


def format_pdb(atoms: list[dict[str, Any]], chains: list[str], title: str) -> str:
    selected = [atom for atom in atoms if atom["record"] == "ATOM" and atom["chain"] in set(chains)]
    if not selected:
        raise ScriptError("这些链没有可写出的蛋白原子：" + ",".join(chains))
    lines = [f"REMARK antigen-epitope-evolve {title}"]
    for serial, atom in enumerate(selected, 1):
        name = atom["name"]
        atom_name = name if len(name) == 4 else f" {name:<3}"[:4]
        element = atom["element"] or name[:1].upper()
        lines.append(
            f"ATOM  {serial:5d} {atom_name}{atom['altloc'] or ' '}{atom['residue']:>3} {atom['chain']}"
            f"{atom['number']:4d}{atom['insertion'] or ' '}   "
            f"{atom['x']:8.3f}{atom['y']:8.3f}{atom['z']:8.3f}"
            f"  1.00  0.00          {element:>2}"
        )
    lines.append("END")
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_package(
    pdb_path: Path,
    antigen_chain: str,
    antibody_chains: list[str],
    output_dir: Path,
    workspace_input_dir: str,
    pdb_id: str,
    literature_id: str,
    run_name: str,
    cutoff: float,
) -> dict[str, Any]:
    atoms, malformed = parse_pdb(pdb_path.read_text(encoding="utf-8", errors="replace"))
    contacts = find_contacts(atoms, antigen_chain, antibody_chains, cutoff, malformed)
    contacts["pdb"] = str(pdb_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "epitope.json", contacts)
    if contacts["error"]:
        raise ScriptError(contacts["error"])
    target_name = "target_antigen.pdb"
    framework_name = "antibody_framework.pdb"
    (output_dir / target_name).write_text(
        format_pdb(atoms, [antigen_chain], f"antigen chain {antigen_chain}"),
        encoding="utf-8",
    )
    (output_dir / framework_name).write_text(
        format_pdb(atoms, antibody_chains, "antibody chains " + ",".join(antibody_chains)),
        encoding="utf-8",
    )
    prefix = workspace_input_dir.strip().strip("/")
    target_pdb = f"{prefix}/{target_name}"
    framework_pdb = f"{prefix}/{framework_name}"
    baseline = {
        "hotspots": contacts["hotspots"],
        "num_designs": 1,
        "diffuser_t": 200,
        "final_step": 160,
        "protenix_n_sample": 1,
        "protenix_seeds": "42",
        "protenix_use_msa": False,
        "designed_sequences": [],
    }
    design = {
        "frozen": {
            "literature_id": literature_id,
            "pdb_id": pdb_id,
            "antigen_chain": antigen_chain,
            "antibody_chains": antibody_chains,
            "cutoff_angstrom": cutoff,
            "epitope_residues": [item["hotspot"] for item in contacts["epitope_residues"] if item["hotspot"]],
            "target_pdb": target_pdb,
            "framework_pdb": framework_pdb,
        },
        "editable": {
            "L1": ["hotspots"],
            "L2": ["num_designs", "diffuser_t", "final_step", "protenix_n_sample", "protenix_seeds", "protenix_use_msa"],
            "L3": ["designed_sequences"],
        },
        "baseline": baseline,
        "warnings": contacts["warnings"],
        "glycan_contacts": contacts["glycan_contacts"],
        "unrepresentable_hotspots": contacts["unrepresentable_hotspots"],
    }
    config = {
        "workspace": "antibody_pipeline",
        "target_pdb": target_pdb,
        "framework_pdb": framework_pdb,
        "hotspots": baseline["hotspots"],
        "num_designs": baseline["num_designs"],
        "run_name": run_name,
        "npus": "0",
        "workers_per_npu": 1,
        "final_step": baseline["final_step"],
        "diffuser_t": baseline["diffuser_t"],
        "protenix_use_msa": baseline["protenix_use_msa"],
        "protenix_n_sample": baseline["protenix_n_sample"],
        "protenix_seeds": baseline["protenix_seeds"],
        "force": False,
    }
    design["config"] = config
    write_json(output_dir / "design_input.json", design)
    write_json(output_dir / "config.json", config)
    return design


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ScriptError("候选必须是 JSON 对象")
    normalized = dict(state)
    if "hotspots" in normalized:
        normalized["hotspots"] = format_hotspots(parse_hotspots(normalized["hotspots"]))
    return normalized


def _parent_state(design: dict[str, Any], ledger: dict[str, Any] | None) -> dict[str, Any]:
    if ledger and isinstance(ledger.get("parent"), dict):
        return normalize_state(ledger["parent"])
    baseline = design.get("baseline")
    if not isinstance(baseline, dict):
        raise ScriptError("设计输入里没有基线")
    return normalize_state(baseline)


def _validate_change(parent: dict[str, Any], candidate: dict[str, Any], field: str, epitope: set[str]) -> None:
    old = parent[field]
    new = candidate[field]
    if field == "hotspots":
        labels = parse_hotspots(new)
        if not labels:
            raise ScriptError("热点不能为空")
        outside = [label for label in labels if label not in epitope]
        if outside:
            raise ScriptError("热点不在已计算的表位内：" + ",".join(outside))
        return
    if field == "designed_sequences":
        if not isinstance(old, list) or not old:
            raise ScriptError("尚无已设计序列，不能改序列")
        if not isinstance(new, list) or len(new) != len(old) or not all(isinstance(item, str) and item for item in new):
            raise ScriptError("只能修改已有序列，不能增减条数")
        return
    if field in {"num_designs", "diffuser_t", "final_step", "protenix_n_sample"}:
        if isinstance(new, bool) or not isinstance(new, int):
            raise ScriptError(f"{field} 必须是整数")
    if field == "num_designs" and new < 1:
        raise ScriptError("num_designs 必须是正整数")
    if field == "protenix_n_sample" and new < 1:
        raise ScriptError("protenix_n_sample 必须是正整数")
    diffuser_t = candidate["diffuser_t"] if field == "diffuser_t" else parent["diffuser_t"]
    final_step = candidate["final_step"] if field == "final_step" else parent["final_step"]
    if field in {"diffuser_t", "final_step"}:
        if not isinstance(diffuser_t, int) or isinstance(diffuser_t, bool) or diffuser_t < 15:
            raise ScriptError("diffuser_t 不能小于 15")
        if not isinstance(final_step, int) or isinstance(final_step, bool) or not 1 <= final_step <= diffuser_t:
            raise ScriptError("final_step 必须在 1 和 diffuser_t 之间")
    if field == "protenix_seeds" and (not isinstance(new, str) or not new.strip()):
        raise ScriptError("protenix_seeds 必须是非空字符串")
    if field == "protenix_use_msa" and not isinstance(new, bool):
        raise ScriptError("protenix_use_msa 必须是布尔值")


def decide_round(
    design: dict[str, Any],
    candidate: dict[str, Any],
    score: Any,
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parent = _parent_state(design, ledger)
    epitope = set(design.get("frozen", {}).get("epitope_residues") or [])
    try:
        proposed = normalize_state(candidate)
        extra = sorted(set(proposed) - set(parent))
        missing = sorted(set(parent) - set(proposed))
        if extra:
            raise ScriptError(f"未知字段：{extra[0]}")
        if missing:
            raise ScriptError(f"固定字段被删除：{missing[0]}")
        changed = [key for key in parent if proposed[key] != parent[key]]
        if not changed:
            raise ScriptError("没有改动")
        if len(changed) != 1:
            raise ScriptError("一轮只能改一个字段：" + ",".join(changed))
        field = changed[0]
        if field not in LEVELS:
            raise ScriptError(f"字段不在可编辑范围内：{field}")
        _validate_change(parent, proposed, field, epitope)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            raise ScriptError("合法改动必须带有限分数，脚本不编造分数")
        measured = float(score)
        seen = dict((ledger or {}).get("seen") or {})
        key = json.dumps(proposed, ensure_ascii=False, sort_keys=True)
        previous = seen.get(key)
        if previous is not None and measured <= float(previous):
            raise ScriptError("已经见过，而且没有更好")
        seen[key] = measured
        decision = {
            "accepted": True,
            "level": LEVELS[field],
            "field": field,
            "old": parent[field],
            "new": proposed[field],
            "score": measured,
            "reason": "记下这一次",
        }
        parent_out = copy.deepcopy(proposed)
    except ScriptError as error:
        seen = dict((ledger or {}).get("seen") or {})
        decision = {
            "accepted": False,
            "level": "",
            "field": "",
            "old": "",
            "new": "",
            "score": None,
            "reason": str(error),
        }
        parent_out = parent
    rounds = list((ledger or {}).get("rounds") or [])
    decision["index"] = len(rounds) + 1
    rounds.append(decision)
    return {"parent": parent_out, "seen": seen, "rounds": rounds, "decision": decision}


def render_summary(design: dict[str, Any], ledger: dict[str, Any]) -> str:
    frozen = design.get("frozen") or {}
    lines = [
        "# 抗原表位试跑总结",
        "",
        f"- PDB：{frozen.get('pdb_id', '')}",
        f"- 抗原链：{frozen.get('antigen_chain', '')}",
        f"- 抗体链：{', '.join(frozen.get('antibody_chains') or [])}",
        f"- 距离阈值：{frozen.get('cutoff_angstrom', '')} Å",
        f"- 文献：{frozen.get('literature_id', '')}",
        f"- 表位残基：{', '.join(frozen.get('epitope_residues') or [])}",
        "",
        "## 轮次",
        "",
    ]
    rounds = ledger.get("rounds") or []
    if not rounds:
        lines.append("还没有轮次。")
    for item in rounds:
        outcome = "接受" if item.get("accepted") else "拒绝"
        score = "—" if item.get("score") is None else f"{item['score']:g}"
        field = item.get("field") or "—"
        lines.append(
            f"{item.get('index')}. {field}：`{item.get('old', '')}` → `{item.get('new', '')}`，"
            f"分数 {score}，{outcome}。{item.get('reason', '')}"
        )
    parent = ledger.get("parent") or design.get("baseline") or {}
    lines.extend(["", "## 当前采用", ""])
    for key, value in parent.items():
        lines.append(f"- {key}：`{json.dumps(value, ensure_ascii=False)}`")
    warnings = design.get("warnings") or []
    if warnings:
        lines.extend(["", "## 警告", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def _split_chains(text: str) -> list[str]:
    chains = [item.strip() for item in text.split(",") if item.strip()]
    if not chains:
        raise ScriptError("至少要给出一条链")
    return chains


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build epitope contacts and a local antibody-design round ledger.")
    commands = parser.add_subparsers(dest="command", required=True)

    contacts = commands.add_parser("contacts")
    contacts.add_argument("--pdb", type=Path, required=True)
    contacts.add_argument("--antigen-chain", required=True)
    contacts.add_argument("--antibody-chains", required=True)
    contacts.add_argument("--cutoff", type=float, default=DEFAULT_CUTOFF)
    contacts.add_argument("--output", type=Path, required=True)

    package = commands.add_parser("package")
    package.add_argument("--pdb", type=Path, required=True)
    package.add_argument("--antigen-chain", required=True)
    package.add_argument("--antibody-chains", required=True)
    package.add_argument("--output-dir", type=Path, required=True)
    package.add_argument("--workspace-input-dir", default="antibody_pipeline/inputs")
    package.add_argument("--pdb-id", required=True)
    package.add_argument("--literature-id", required=True)
    package.add_argument("--run-name", default="antigen-epitope")
    package.add_argument("--cutoff", type=float, default=DEFAULT_CUTOFF)

    round_cmd = commands.add_parser("round")
    round_cmd.add_argument("--design-input", type=Path, required=True)
    round_cmd.add_argument("--candidate", type=Path, required=True)
    round_cmd.add_argument("--score", type=float, required=True)
    round_cmd.add_argument("--ledger", type=Path, required=True)

    summary = commands.add_parser("summarize")
    summary.add_argument("--design-input", type=Path, required=True)
    summary.add_argument("--ledger", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "contacts":
            atoms, malformed = parse_pdb(args.pdb.read_text(encoding="utf-8", errors="replace"))
            result = find_contacts(atoms, args.antigen_chain, _split_chains(args.antibody_chains), args.cutoff, malformed)
            result["pdb"] = str(args.pdb)
            write_json(args.output, result)
            if result["error"]:
                raise ScriptError(result["error"])
        elif args.command == "package":
            build_package(
                args.pdb,
                args.antigen_chain,
                _split_chains(args.antibody_chains),
                args.output_dir,
                args.workspace_input_dir,
                args.pdb_id,
                args.literature_id,
                args.run_name,
                args.cutoff,
            )
        elif args.command == "round":
            design = json.loads(args.design_input.read_text(encoding="utf-8"))
            candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
            ledger = json.loads(args.ledger.read_text(encoding="utf-8")) if args.ledger.is_file() else None
            updated = decide_round(design, candidate, args.score, ledger)
            decision = updated.pop("decision")
            write_json(args.ledger, updated)
            print(json.dumps(decision, ensure_ascii=False))
        elif args.command == "summarize":
            design = json.loads(args.design_input.read_text(encoding="utf-8"))
            ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
            args.output.write_text(render_summary(design, ledger), encoding="utf-8")
    except (OSError, json.JSONDecodeError, ScriptError) as error:
        print(str(error))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
