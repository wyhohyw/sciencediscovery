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

"""Parent-child search lineage, plus one FoldMason guide tree when requested."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def candidates(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise SystemExit("input must be a non-empty candidate list or {candidates: [...]}")
    return rows


def lineages(rows: list[dict]) -> dict:
    by_id = {}
    children: dict[str | None, list[str]] = {}
    for row in rows:
        identity = str(row.get("candidate_id") or "")
        if not identity or identity in by_id:
            raise SystemExit("each candidate needs a unique candidate_id")
        parent = row.get("parent_id")
        parent = None if parent in {None, ""} else str(parent)
        by_id[identity] = {
            "parent_id": parent,
            "score": row.get("score"),
            "mutations": list(row.get("mutations") or []),
        }
        children.setdefault(parent, []).append(identity)
    missing = sorted(parent for parent in children if parent is not None and parent not in by_id)
    if missing:
        raise SystemExit(f"unknown parent_id: {', '.join(missing)}")
    paths = []

    def walk(identity: str, path: list[str]) -> None:
        if identity in path:
            raise SystemExit(f"lineage cycle at {identity}")
        nxt = path + [identity]
        kids = children.get(identity, [])
        if not kids:
            paths.append(nxt)
            return
        for child in kids:
            walk(child, nxt)

    for identity in children.get(None, []):
        walk(identity, [])
    recurrence: dict[str, dict[str, list[str]]] = {}
    for identity, row in by_id.items():
        for mutation in row["mutations"]:
            bucket = recurrence.setdefault(str(mutation), {"candidates": [], "parents": []})
            bucket["candidates"].append(identity)
            parent = row["parent_id"]
            if parent and parent not in bucket["parents"]:
                bucket["parents"].append(parent)
    return {
        "lineages": paths,
        "candidates": by_id,
        "mutation_recurrence": recurrence,
        "note": "lineage is the recorded parent-child search, not natural phylogeny",
    }


def extract_chain(source: Path, chain: str, destination: Path) -> None:
    kept = []
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("ATOM  ") and len(line) > 21 and line[21] == chain:
            kept.append(line)
        if line.startswith("ENDMDL"):
            break
    if not kept:
        raise ValueError(f"{source.name} has no ATOM records for chain {chain}")
    destination.write_text("\n".join(kept) + "\nEND\n", encoding="utf-8")


def foldmason(rows: list[dict], structures: Path, chain: str, output: Path) -> dict:
    executable = shutil.which("foldmason")
    if executable is None:
        return {"status": "not_run", "reason": "foldmason_not_installed"}
    work = output.parent / f"{output.stem}-foldmason"
    inputs = work / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    written = []
    for row in rows:
        identity = str(row["candidate_id"])
        source = structures / f"{identity}.pdb"
        if not source.is_file():
            continue
        destination = inputs / f"{identity}.pdb"
        extract_chain(source, chain, destination)
        written.append(destination)
    if len(written) < 2:
        return {"status": "not_run", "reason": "fewer than two pdb structures for the chain"}
    prefix = work / "alignment"
    command = [
        executable, "easy-msa", *(str(path) for path in written), str(prefix), str(work / "tmp"),
        "--threads", str(max(1, min(8, os.cpu_count() or 1))), "--input-format", "1",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
    tree = prefix.with_suffix(".nw")
    if completed.returncode != 0 or not tree.is_file():
        detail = (completed.stderr or completed.stdout or "foldmason failed").strip()
        return {"status": "failed", "reason": detail[-1000:]}
    return {
        "status": "ran",
        "chain": chain,
        "tree": str(tree),
        "note": "structural-similarity guide tree, not evolutionary time",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--structures", type=Path)
    parser.add_argument("--chain", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = candidates(args.candidates)
    result = lineages(rows)
    if args.structures:
        if not args.chain:
            raise SystemExit("--chain is required with --structures")
        result["foldmason"] = foldmason(rows, args.structures, args.chain, args.output)
    else:
        result["foldmason"] = {"status": "not_run", "reason": "no structures directory"}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
