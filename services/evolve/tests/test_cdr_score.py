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

"""The CDR score uses the confidence loss and the 5 A contact gate."""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.science_tags(category="ut", os="linux", arch=("amd64", "arm64"))

ROOT = Path(__file__).resolve().parents[3]
EVALUATOR = ROOT / "skills" / "evolve-design" / "scripts" / "cdr_opendde_evaluator.py"


def load_evaluator():
    spec = importlib.util.spec_from_file_location("cdr_opendde_evaluator", EVALUATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atom(serial: int, chain: str, number: int, x: float) -> str:
    return (
        f"ATOM  {serial:5d}  CA  ALA {chain}{number:4d}    "
        f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C\n"
    )


def structure(binder_contact_at: int) -> str:
    lines = [atom(1, "A", 1, 0.0)]
    for index in range(1, 13):
        lines.append(atom(index + 1, "D", index, 3.0 if index == binder_contact_at else 40.0))
    return "".join(lines) + "END\n"


def confidence() -> tuple[dict, dict]:
    count = 13
    full = {
        "atom_plddt": [80.0] * count,
        "atom_to_token_idx": list(range(count)),
        "token_asym_id": [1] + [2] * 12,
        "token_pair_pae": [[3.1] * count for _ in range(count)],
        "contact_probs": [[0.5] * count for _ in range(count)],
    }
    return {"iptm": 0.7, "plddt": 80.0}, full


def candidate() -> dict:
    return {
        "chain": "D",
        "sequence": "A" * 12,
        "target_chain": "A",
        "target_sequence": "A",
        "cdr_regions": [[10, 12]],
        "target_hotspots": [1],
    }


def test_a_cdr_contact_passes_and_the_loss_omits_esm2():
    module = load_evaluator()
    with tempfile.TemporaryDirectory(prefix="cdr-score-") as directory:
        path = Path(directory) / "complex.pdb"
        path.write_text(structure(10), encoding="utf-8")
        summary, full = confidence()
        result = module.score_fold(candidate(), summary, full, path)
        assert result["gate_passed"] is True
        assert result["cdr_contact_fraction"] == 1.0
        assert result["esm2"] == "omitted"
        assert result["formula"] == "vhh-confidence-contact8-proxy-v2"
        assert abs(result["score"] + result["loss"]) < 1e-9
        assert set(result["components"]) == set(module.WEIGHTS)
        assert result["components"]["plddt"] == pytest.approx(0.2)
        assert result["components"]["i_ptm"] == pytest.approx(0.3)


def test_esm_batch_fills_new_sites_then_a_second_amino_acid():
    module = load_evaluator()
    parent = "ACDE"
    rows = [
        {"Pos": 1, "WT": "A", "W": 3.0, "Y": 2.5},
        {"Pos": 2, "WT": "C", "D": 1.0},
    ]
    batch = module.esm_batch(parent, "D", {1, 2, 3}, rows, 3)
    assert [item[0] for item in batch] == ["WCDE", "ADDE", "YCDE"]
    kept = module.without_folded(batch, {"WCDE", parent})
    assert [item[0] for item in kept] == ["ADDE", "YCDE"]
    skill, fresh = module.cycle_batch(
        parent, "D", {1, 2, 3}, module.ESM_SKILL, 3, rows, [["WWWE"]], True,
        {"WCDE", "ADDE", "YCDE"},
    )
    assert skill == module.MPNN_SKILL
    assert fresh[0][0] == "WWWE"
    assert fresh[0][1] == [["D", 1, "W"], ["D", 2, "W"], ["D", 3, "W"]]


def test_inverse_folding_keeps_unique_cdr_rewrites():
    module = load_evaluator()
    parent = "ACDE"
    rounds = [
        ["WWWE", "AWDE", "ACDE", "WCDF"],
        ["AWDE", "AYDE"],
        ["ADDE"],
        ["AWDY"],
    ]
    batch = module.inverse_folding_batch(parent, "D", {1, 2, 3}, rounds, 3, set())
    assert [item[0] for item in batch] == ["WWWE", "AWDE", "AYDE"]


def test_the_router_draws_one_skill_for_a_cycle():
    module = load_evaluator()
    assert module.design_skill(42, 0, False) == module.ESM_SKILL
    assert module.design_skill(42, 3, True) == module.design_skill(42, 3, True)
    seen = {module.design_skill(42, cycle, True) for cycle in range(30)}
    assert seen == {module.ESM_SKILL, module.MPNN_SKILL}
    assert module.design_skill(42, 0, True, forced=module.MPNN_SKILL) == module.MPNN_SKILL
    assert module.design_skill(42, 0, False, forced=module.MPNN_SKILL) == module.ESM_SKILL


def test_backbone_pdb_keeps_n_ca_c_o():
    module = load_evaluator()
    atoms = [{
        "chain": "D", "name": name, "element": name[0], "sequence_index": 0,
        "residue_number": 1, "x": 1.0, "y": 2.0, "z": 3.0,
    } for name in ("N", "CA", "C", "O", "CB")]
    parsed = module.pdb_atoms(module.backbone_pdb(atoms, {"D"}))
    assert [atom["name"] for atom in parsed] == ["N", "CA", "C", "O"]


def test_a_packed_backbone_round_trips():
    module = load_evaluator()
    plain = "REMARK parent backbone\nATOM      1  CA  ALA D   1       1.000   2.000   3.000  1.00  0.00           C\nEND\n"
    packed = module.pack_backbone(plain)
    assert packed.startswith("gz:")
    assert module.unpack_backbone(packed) == plain
    assert module.unpack_backbone(plain) == plain


def test_a_finite_pll_adds_the_esm2_term_at_weight_one_tenth():
    module = load_evaluator()
    with tempfile.TemporaryDirectory(prefix="cdr-score-") as directory:
        path = Path(directory) / "complex.pdb"
        path.write_text(structure(10), encoding="utf-8")
        summary, full = confidence()
        plain = module.score_fold(candidate(), summary, full, path)
        with_pll = module.score_fold(candidate(), summary, full, path, esm2_pll=-2.0)
        assert with_pll["formula"] == "vhh-confidence-contact8-proxy-esm2-v2"
        assert with_pll["esm2"] == pytest.approx(-2.0)
        assert with_pll["loss"] == pytest.approx(plain["loss"] + 0.2)
        assert with_pll["score"] == pytest.approx(-with_pll["loss"])


def test_a_missing_confidence_field_is_not_replaced():
    module = load_evaluator()
    with tempfile.TemporaryDirectory(prefix="cdr-score-") as directory:
        path = Path(directory) / "complex.pdb"
        path.write_text(structure(10), encoding="utf-8")
        summary, full = confidence()
        del full["contact_probs"]
        with pytest.raises(ValueError, match="contact_probs"):
            module.score_fold(candidate(), summary, full, path)


def test_a_framework_contact_fails_the_gate():
    module = load_evaluator()
    with tempfile.TemporaryDirectory(prefix="cdr-score-") as directory:
        path = Path(directory) / "complex.pdb"
        path.write_text(structure(1), encoding="utf-8")
        summary, full = confidence()
        result = module.score_fold(candidate(), summary, full, path)
        assert result["gate_passed"] is False
        assert result["cdr_contact_fraction"] == 0.0
        assert result["ran"] is True
        assert "5 A gate" in result["error"]
