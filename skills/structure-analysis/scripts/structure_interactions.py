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

"""Run a local PLIP command on one complex and count report sections."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

SECTIONS = (
    "Hydrogen Bonds",
    "Salt Bridges",
    "Hydrophobic Interactions",
    "pi-Stacking",
    "Water Bridges",
)


def pdb_line(serial: int, atom: dict[str, object]) -> str:
    residue = str(atom["residue"])[:3]
    chain = str(atom["chain"])[:1] or "_"
    number = int(atom["residue_number"])
    if not 1 <= serial <= 99999 or not -999 <= number <= 9999:
        raise ValueError("atom serial or residue number does not fit PDB columns")
    element = str(atom["element"])[:2].rjust(2)
    return (
        f"ATOM  {serial:5d}  CA  {residue:>3} {chain}{number:4d}    "
        f"{float(atom['x']):8.3f}{float(atom['y']):8.3f}{float(atom['z']):8.3f}"
        f"  1.00  0.00          {element}"
    )


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


def cif_to_pdb(text: str, destination: Path) -> None:
    tokens = list(cif_tokens(text))
    lines = []
    index = 0
    while index < len(tokens):
        if tokens[index] != "loop_":
            index += 1
            continue
        index += 1
        columns = []
        while index < len(tokens) and tokens[index].startswith("_"):
            columns.append(tokens[index])
            index += 1
        width = len(columns)
        if not any(name.endswith(".Cartn_x") for name in columns):
            continue
        lookup = {name.rsplit(".", 1)[-1]: position for position, name in enumerate(columns)}
        serial = 0
        while index + width <= len(tokens) and tokens[index] not in {"loop_", "stop_"} and not tokens[index].startswith(("data_", "save_", "_")):
            row = tokens[index:index + width]
            index += width

            def cell(*names: str) -> str:
                for name in names:
                    position = lookup.get(name)
                    if position is not None:
                        return row[position]
                return ""

            if cell("group_PDB") not in {"", "ATOM"}:
                continue
            serial += 1
            lines.append(pdb_line(serial, {
                "residue": cell("auth_comp_id", "label_comp_id") or "UNK",
                "chain": cell("auth_asym_id", "label_asym_id") or "_",
                "residue_number": int(float(cell("auth_seq_id", "label_seq_id"))),
                "element": cell("type_symbol") or "C",
                "x": float(cell("Cartn_x")),
                "y": float(cell("Cartn_y")),
                "z": float(cell("Cartn_z")),
            }))
        break
    if not lines:
        raise ValueError("mmCIF has no coordinates to convert")
    destination.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")


def plip_command() -> str | None:
    configured = os.environ.get("PLIP_COMMAND", "")
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("plipcmd.py") or shutil.which("plipcmd")
    if found:
        return found
    default = Path("/usr/local/bin/plipcmd.py")
    return str(default) if default.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("structure", type=Path)
    parser.add_argument("--binder-chains", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    command = plip_command()
    if command is None:
        args.output.write_text(json.dumps({
            "available": False,
            "error": "PLIP command not found; set PLIP_COMMAND or install plipcmd",
        }, indent=2) + "\n", encoding="utf-8")
        return
    with tempfile.TemporaryDirectory(prefix="structure-analysis-") as work:
        root = Path(work)
        pdb = args.structure
        if args.structure.suffix.lower() in {".cif", ".mmcif"}:
            pdb = root / "complex.pdb"
            cif_to_pdb(args.structure.read_text(encoding="utf-8", errors="replace"), pdb)
        report_dir = root / "plip"
        report_dir.mkdir()
        cmd = [command, "-f", str(pdb), "-o", str(report_dir), "-t"]
        chains = [item for item in args.binder_chains.replace(",", " ").split() if item]
        if chains:
            cmd.extend(["--inter", *chains])
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "PLIP failed").strip()
            raise SystemExit(detail[-2000:])
        reports = sorted(report_dir.glob("*report.txt"))
        if not reports:
            raise SystemExit("PLIP exited without a report")
        text = reports[0].read_text(encoding="utf-8", errors="replace")
    report_path = args.output.with_suffix(".txt")
    report_path.write_text(text, encoding="utf-8")
    args.output.write_text(json.dumps({
        "available": True,
        "report": str(report_path),
        "section_mentions": {name: text.count(name) for name in SECTIONS},
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
