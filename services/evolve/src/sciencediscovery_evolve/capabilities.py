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

"""What the NPU host can run, decided before a CDR search starts.

The probe runs as one NPU execution and reports each component by name. This
module turns that report into the skills the router may draw, and says why a
skill is off. It installs and downloads nothing; the probe does the one
whitelisted pip install, into a directory beside the OpenDDE tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

ESM_SKILL = "esm2-guided-mutation"
MPNN_SKILL = "antibody-inverse-folding"

#: Every candidate is folded, so these checks gate every skill, LLM-proposed ones included.
FOLD_CHECKS = ("npu", "rdkit", "biotite", "fold_code", "fold_checkpoint")
#: Checks each NPU-proposed skill needs on top of the fold.
SKILL_CHECKS = {
    ESM_SKILL: ("esm_module", "esm_weights", "harness_modules"),
    MPNN_SKILL: ("harness_modules", "soluble_weights", "ligand_code"),
}


@dataclass(frozen=True)
class Assessment:
    """What the probe found. ``unavailable`` maps a skill to the reason it is off."""

    fold_ok: bool
    fold_reason: str
    unavailable: dict[str, str]
    notes: tuple[str, ...]


def _check_ok(checks: Mapping[str, Any], name: str) -> bool:
    entry = checks.get(name)
    return isinstance(entry, Mapping) and entry.get("ok") is True


def _problems(checks: Mapping[str, Any], names: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for name in names:
        if _check_ok(checks, name):
            continue
        entry = checks.get(name)
        detail = entry.get("detail") if isinstance(entry, Mapping) else None
        found.append(f"{name}: {detail or 'not reported by the NPU probe'}")
    return found


def assess(report: Mapping[str, Any] | None) -> Assessment:
    """Turn one probe report into fold and skill availability.

    A report that is missing or unreadable counts as nothing available, with the
    reason it came back with, so a broken probe refuses the search rather than
    letting an unchecked skill run.
    """
    if not isinstance(report, Mapping) or not isinstance(report.get("checks"), Mapping) or not report["checks"]:
        error = str((report or {}).get("error") or "the NPU probe returned no report")
        return Assessment(False, error, {name: error for name in SKILL_CHECKS}, ())
    checks: Mapping[str, Any] = report["checks"]
    notes = tuple(
        f"auto-install {item.get('component')}: "
        + ("ok" if item.get("ok") is True else f"failed: {item.get('detail') or 'no output'}")
        for item in report.get("installs") or []
        if isinstance(item, Mapping)
    )
    fold = _problems(checks, FOLD_CHECKS)
    unavailable: dict[str, str] = {}
    for skill, names in SKILL_CHECKS.items():
        found = _problems(checks, names)
        if found:
            unavailable[skill] = "; ".join(found)
    return Assessment(not fold, "; ".join(fold), unavailable, notes)
