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

"""Stand-in for the antibody NPU pipeline.

Evolve prepends ``_EVOLVE_CANDIDATE = {...}`` and runs this file. The last
stdout line is the measurement. ``device`` is ``npu`` so the existing npu_job
loop can accept it. Nothing here touches a card or the real model pipeline.
"""

import argparse
import json
import re
import sys
from typing import Any, Optional


HOTSPOT_LABEL = re.compile(r"^[A-Za-z][0-9]+$")
STAGES = ("rfdiffusion", "proteinmpnn", "protenix", "screening")


def parse_hotspots(value: Any) -> Optional[list[str]]:
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    labels = []
    for item in re.split(r"[,;\s]+", text):
        if not item:
            continue
        if not HOTSPOT_LABEL.fullmatch(item):
            return None
        labels.append(item)
    if len(labels) != len(set(labels)):
        return None
    return labels


def score_candidate(candidate: dict[str, Any]) -> float:
    """A stand-in score in ``[0, 0.95]``. A broken hotspot list scores lower."""
    epitope = candidate.get("epitope_residues")
    labels = parse_hotspots(candidate.get("hotspots"))
    if not isinstance(epitope, list) or not epitope or labels is None or not labels:
        return 0.2
    if any(label not in set(epitope) for label in labels):
        return 0.25
    fraction = len(labels) / len(epitope)
    coverage = max(0.0, 1 - abs(fraction - 0.5) * 2)
    diffuser = candidate.get("diffuser_t")
    final = candidate.get("final_step")
    schedule = 0.0
    if isinstance(diffuser, int) and not isinstance(diffuser, bool) and diffuser >= 15:
        if isinstance(final, int) and not isinstance(final, bool) and 1 <= final <= diffuser:
            schedule = 1.0
    designs = candidate.get("num_designs")
    design_term = 1.0 if isinstance(designs, int) and not isinstance(designs, bool) and designs >= 1 else 0.0
    score = 0.15 + 0.35 * coverage + 0.25 * schedule + 0.15 * design_term
    return round(min(score, 0.95), 4)


def measurement(candidate: dict[str, Any]) -> dict[str, Any]:
    labels = parse_hotspots(candidate.get("hotspots")) or []
    token = labels[0] if labels else "none"
    return {
        "device": "npu",
        "mock": True,
        "score": score_candidate(candidate),
        "stages": list(STAGES),
        "designs": [
            {"id": "mock-0", "sequence": f"MOCK{token}QVQL"},
            {"id": "mock-1", "sequence": f"MOCK{token}EVQL"},
        ],
    }


def emit(candidate: dict[str, Any]) -> None:
    print(json.dumps(measurement(candidate), ensure_ascii=False, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score one candidate JSON as a mock NPU job.")
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    loaded = json.loads(args.candidate)
    if not isinstance(loaded, dict):
        print("candidate is not a JSON object", file=sys.stderr)
        return 2
    emit(loaded)
    return 0


if isinstance(globals().get("_EVOLVE_CANDIDATE"), dict):
    emit(_EVOLVE_CANDIDATE)
elif __name__ == "__main__":
    raise SystemExit(main())
