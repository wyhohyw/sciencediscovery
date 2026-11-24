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

"""Count distance contacts between antibody and antigen atoms.

CDR ranges are 1-based positions in chain-local residue order. Hotspots use
the residue numbers written in the structure.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def pdb_atom(line: str) -> dict[str, object] | None:
    if not line.startswith("ATOM  ") or len(line) < 54:
        return None
    altloc = line[16].strip()
    if altloc not in {"", "A"}:
        return None
    element = line[76:78].strip().upper() if len(line) >= 78 else ""
    if not element:
        element = line[12:16].strip()[:1].upper()
    try:
        return {
            "chain": line[21].strip() or "_",
            "residue": line[17:20].strip(),
            "residue_number": int(line[22:26]),
            "insertion": line[26].strip(),
            "element": element,
            "x": float(line[30:38]),
            "y": float(line[38:46]),
            "z": float(line[46:54]),
        }
    except ValueError:
        return None


def cif_tokens(text: str):
    index = 0
    size = len(text)
    while index < size:
        char = text[index]
        if char in " \t\r\n":
            index += 1
            continue
        if char == "#":
            while index < size and text[index] != "\n":
                index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            start = index
            while index < size and text[index] != quote:
                index += 1
            yield text[start:index]
            index = min(index + 1, size)
            continue
        start = index
        while index < size and text[index] not in " \t\r\n":
            index += 1
        yield text[start:index]


def cif_atoms(text: str) -> list[dict[str, object]]:
    tokens = list(cif_tokens(text))
    atoms: list[dict[str, object]] = []
    index = 0
    while index < len(tokens):
        if tokens[index] != "loop_":
            index += 1
            continue
        index += 1
        columns: list[str] = []
        while index < len(tokens) and tokens[index].startswith("_"):
            columns.append(tokens[index])
            index += 1
        width = len(columns)
        if width == 0 or not any(name.endswith(".Cartn_x") for name in columns):
            while index < len(tokens) and tokens[index] not in {"loop_", "stop_"} and not tokens[index].startswith(("_", "data_", "save_")):
                index += 1
            continue
        rows: list[list[str]] = []
        while index + width <= len(tokens) and not tokens[index].startswith("_") and tokens[index] not in {"loop_", "stop_"}:
            if tokens[index].startswith("data_") or tokens[index].startswith("save_"):
                break
            rows.append(tokens[index:index + width])
            index += width
        lookup = {name.rsplit(".", 1)[-1]: position for position, name in enumerate(columns)}

        def cell(row: list[str], *names: str) -> str:
            for name in names:
                position = lookup.get(name)
                if position is not None:
                    return row[position]
            return ""

        for row in rows:
            if cell(row, "group_PDB") not in {"", "ATOM"}:
                continue
            try:
                atoms.append({
                    "chain": cell(row, "auth_asym_id", "label_asym_id") or "_",
                    "residue": cell(row, "auth_comp_id", "label_comp_id"),
                    "residue_number": int(float(cell(row, "auth_seq_id", "label_seq_id"))),
                    "insertion": cell(row, "pdbx_PDB_ins_code").replace("?", "").replace(".", ""),
                    "element": cell(row, "type_symbol").upper(),
                    "x": float(cell(row, "Cartn_x")),
                    "y": float(cell(row, "Cartn_y")),
                    "z": float(cell(row, "Cartn_z")),
                })
            except ValueError:
                continue
        return atoms
    raise ValueError("mmCIF has no atom_site loop with coordinates")


def load_atoms(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".cif", ".mmcif"}:
        atoms = cif_atoms(text)
    else:
        atoms = []
        for line in text.splitlines():
            if line.startswith("ENDMDL"):
                break
            atom = pdb_atom(line)
            if atom:
                atoms.append(atom)
    numbered: list[dict[str, object]] = []
    seen: dict[tuple[object, ...], int] = {}
    for atom in atoms:
        if atom["element"] in {"H", "D"} or atom["residue"] in {"HOH", "WAT"}:
            continue
        key = (atom["chain"], atom["residue_number"], atom["insertion"])
        if key not in seen:
            seen[key] = 1 + sum(1 for chain, _number, _insertion in seen if chain == atom["chain"])
        atom["sequence_index"] = seen[key]
        numbered.append(atom)
    return numbered


def cdr_index(payload: dict) -> dict[tuple[str, int], str]:
    found: dict[tuple[str, int], str] = {}
    for chain, ranges in payload.items():
        if not isinstance(ranges, list):
            raise ValueError(f"CDR ranges for chain {chain} must be a list")
        for ordinal, bounds in enumerate(ranges, start=1):
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise ValueError("each CDR range is [start, end]")
            start, end = int(bounds[0]), int(bounds[1])
            if start < 1 or end < start:
                raise ValueError(f"invalid CDR range on chain {chain}")
            name = f"CDR{ordinal}_{chain}"
            for position in range(start, end + 1):
                found[(str(chain), position)] = name
    if not found:
        raise ValueError("cdr json must name at least one range")
    return found


def hotspots(text: str) -> list[tuple[str, int]]:
    found = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        chain, number = item.split(":", 1)
        found.append((chain.strip(), int(number)))
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("structure", type=Path)
    parser.add_argument("--antibody-chains", required=True)
    parser.add_argument("--antigen-chains", required=True)
    parser.add_argument("--cdr-json", type=Path, required=True)
    parser.add_argument("--hotspots", default="")
    parser.add_argument("--cutoff", type=float, default=4.5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.cutoff <= 25:
        parser.error("--cutoff must be greater than 0 and at most 25")
    antibody = {item.strip() for item in args.antibody_chains.split(",") if item.strip()}
    antigen = {item.strip() for item in args.antigen_chains.split(",") if item.strip()}
    atoms = load_atoms(args.structure)
    antibody_atoms = [atom for atom in atoms if atom["chain"] in antibody]
    antigen_atoms = [atom for atom in atoms if atom["chain"] in antigen]
    if not antibody_atoms or not antigen_atoms:
        raise SystemExit("antibody or antigen chains have no heavy atoms")
    regions = cdr_index(json.loads(args.cdr_json.read_text(encoding="utf-8")))
    residues: dict[tuple[object, object], dict[str, object]] = {}
    contributions: dict[str, dict[str, object]] = {}
    for name in regions.values():
        contributions.setdefault(name, {"residues": set(), "contacts": 0})
    total = 0
    limit = args.cutoff * args.cutoff
    for left in antibody_atoms:
        for right in antigen_atoms:
            distance = (
                (float(left["x"]) - float(right["x"])) ** 2
                + (float(left["y"]) - float(right["y"])) ** 2
                + (float(left["z"]) - float(right["z"])) ** 2
            )
            if distance > limit:
                continue
            total += 1
            key = (right["chain"], right["residue_number"])
            row = residues.setdefault(key, {
                "chain": right["chain"],
                "residue_id": right["residue_number"],
                "residue_name": right["residue"],
                "contacts": 0,
            })
            row["contacts"] = int(row["contacts"]) + 1
            name = regions.get((str(left["chain"]), int(left["sequence_index"])))
            if name:
                bucket = contributions[name]
                bucket["contacts"] = int(bucket["contacts"]) + 1
                bucket["residues"].add(key)
    points = hotspots(args.hotspots)
    epitope = {(row["chain"], row["residue_id"]) for row in residues.values()}
    missed = [f"{chain}:{number}" for chain, number in points if (chain, number) not in epitope]
    result = {
        "structure": args.structure.name,
        "antibody_chains": sorted(antibody),
        "antigen_chains": sorted(antigen),
        "cutoff": args.cutoff,
        "epitope_residues": sorted(residues.values(), key=lambda row: (-int(row["contacts"]), str(row["chain"]), int(row["residue_id"]))),
        "epitope_size": len(residues),
        "total_contacts": total,
        "hotspot_analysis": {
            "provided_hotspots": len(points),
            "contacted_hotspots": len(points) - len(missed),
            "hotspot_coverage": None if not points else round((len(points) - len(missed)) / len(points), 3),
            "missed_hotspots": missed,
        },
        "cdr_contributions": {
            name: {"residues_contacted": len(bucket["residues"]), "total_contacts": bucket["contacts"]}
            for name, bucket in sorted(contributions.items())
        },
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
