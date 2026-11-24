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

"""Scan antibody sequences for motifs. This is not a measured pI or TAP score."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWY")
HYDROPHOBIC = set("FILMVWY")
MOTIFS = ("NG", "NS", "DG", "DP")


def ranges(value: object) -> list[tuple[int, int]]:
    if not isinstance(value, list):
        raise ValueError("cdr_regions must be a list of [start, end]")
    parsed = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("each CDR range is [start, end]")
        start, end = int(item[0]), int(item[1])
        if start < 1 or end < start:
            raise ValueError("a CDR range is empty or starts below 1")
        parsed.append((start, end))
    return parsed


def in_cdr(position: int, intervals: list[tuple[int, int]]) -> bool:
    return any(start <= position <= end for start, end in intervals)


def scan_chain(chain: dict) -> dict:
    sequence = str(chain.get("sequence") or "")
    if not sequence or any(letter not in AMINO_ACIDS for letter in sequence):
        raise ValueError(f"chain {chain.get('id')} is not an amino-acid sequence")
    intervals = ranges(chain.get("cdr_regions") or [])
    if intervals and intervals[-1][1] > len(sequence):
        raise ValueError(f"chain {chain.get('id')} has a CDR past the sequence")
    hits = []
    for index, letter in enumerate(sequence, start=1):
        if letter == "C":
            hits.append({"kind": "cysteine", "position": index, "in_cdr": in_cdr(index, intervals)})
        if index + 2 <= len(sequence) and letter == "N" and sequence[index] != "P" and sequence[index + 1] in "ST":
            hits.append({"kind": "glycosylation", "position": index, "motif": sequence[index - 1:index + 2], "in_cdr": in_cdr(index, intervals)})
    for start, end in intervals:
        segment = sequence[start - 1:end]
        for motif in MOTIFS:
            offset = segment.find(motif)
            while offset >= 0:
                hits.append({"kind": "motif", "position": start + offset, "motif": motif, "in_cdr": True})
                offset = segment.find(motif, offset + 1)
        for offset, letter in enumerate(segment):
            if letter in "MW":
                hits.append({"kind": "oxidation-context", "position": start + offset, "residue": letter, "in_cdr": True})
        run = 0
        for offset, letter in enumerate(segment):
            run = run + 1 if letter in HYDROPHOBIC else 0
            if run == 4:
                hits.append({"kind": "hydrophobic-run", "position": start + offset - 3, "length": 4, "in_cdr": True})
    return {
        "id": str(chain.get("id") or ""),
        "length": len(sequence),
        "charges": {
            "negative": sum(sequence.count(letter) for letter in "DE"),
            "positive": sum(sequence.count(letter) for letter in "KR"),
            "histidine": sequence.count("H"),
        },
        "cysteine_count": sequence.count("C"),
        "hits": hits,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    rows = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise SystemExit("candidates json must contain a non-empty candidates list")
    result = []
    for row in rows:
        chains = row.get("chains") if isinstance(row, dict) else None
        if not isinstance(chains, list) or not chains:
            raise SystemExit(f"candidate {row!r} has no chains")
        result.append({
            "name": str(row.get("name") or ""),
            "chains": [scan_chain(chain) for chain in chains],
            "limits": "sequence motifs and residue counts only; not pI, TAP, SoluProt, or BioPhi",
        })
    args.output.write_text(json.dumps({"candidates": result}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
