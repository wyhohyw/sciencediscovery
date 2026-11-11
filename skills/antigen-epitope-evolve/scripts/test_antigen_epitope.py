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

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import antigen_epitope  # noqa: E402
import mock_npu_evaluator  # noqa: E402


def atom(serial, name, residue, chain, number, x, y, z, element, alt="", insertion="", record="ATOM"):
    atom_name = name if len(name) == 4 else f" {name:<3}"[:4]
    return (
        f"{record:<6}{serial:5d} {atom_name}{alt or ' '}{residue:>3} {chain}"
        f"{number:4d}{insertion or ' '}   "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {element:>2}"
    )


def complex_pdb() -> str:
    lines = [
        atom(1, "CA", "SER", "H", 1, 0, 0, 0, "C"),
        atom(2, "CB", "SER", "H", 1, 0, 1.5, 0, "C"),
        atom(3, "CA", "ALA", "L", 1, 30, 0, 0, "C"),
        atom(4, "CA", "ALA", "B", 10, 3.0, 0, 0, "C"),
        atom(5, "CB", "ALA", "B", 10, 3.0, 1.5, 0, "C"),
        atom(6, "CA", "GLY", "B", 11, 4.4, 0, 0, "C"),
        atom(7, "CA", "VAL", "B", 12, 10.0, 0, 0, "C"),
        atom(8, "CB", "VAL", "B", 12, 1.0, 0, 0, "C", alt="B"),
        atom(9, "CA", "LEU", "B", 13, 20.0, 0, 0, "C"),
        atom(10, "H", "LEU", "B", 13, 1.0, 0, 0, "H"),
        atom(11, "CA", "THR", "B", 14, 2.0, 0, 0, "C", insertion="A"),
        atom(12, "C1", "NAG", "B", 201, 1.0, 0, 0, "C", record="HETATM"),
        atom(13, "O", "HOH", "B", 301, 0.5, 0, 0, "O", record="HETATM"),
        atom(14, "CA", "PHE", "B", 15, 4.6, 0, 0, "C"),
        "ENDMDL",
        atom(15, "CA", "TYR", "B", 99, 0, 0, 0, "C"),
    ]
    return "\n".join(lines) + "\n"


class ContactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.atoms, self.malformed = antigen_epitope.parse_pdb(complex_pdb())

    def test_heavy_atom_cutoff_excludes_hydrogen_solvent_glycan_and_later_model(self) -> None:
        result = antigen_epitope.find_contacts(self.atoms, "B", ["H", "L"], 4.5, self.malformed)
        self.assertEqual(result["error"], "")
        self.assertEqual([item["number"] for item in result["epitope_residues"]], [10, 11, 14])
        self.assertEqual(result["hotspots"], "[B10,B11]")
        self.assertEqual(result["unrepresentable_hotspots"][0]["insertion"], "A")
        self.assertEqual(result["glycan_contacts"][0]["residue"], "NAG")
        self.assertGreater(result["ignored_solvent_contacts"], 0)
        self.assertNotIn(99, [item["number"] for item in result["epitope_residues"]])
        self.assertNotIn(12, [item["number"] for item in result["epitope_residues"]])
        self.assertNotIn(13, [item["number"] for item in result["epitope_residues"]])

    def test_tighter_cutoff_drops_the_boundary_residue(self) -> None:
        result = antigen_epitope.find_contacts(self.atoms, "B", ["H"], 4.0, self.malformed)
        self.assertEqual([item["number"] for item in result["epitope_residues"] if item["hotspot"]], [10])

    def test_missing_antibody_chain_stops(self) -> None:
        result = antigen_epitope.find_contacts(self.atoms, "B", ["Z"], 4.5, self.malformed)
        self.assertIn("没有抗体链", result["error"])


class MockNpuTests(unittest.TestCase):
    def test_evolve_binding_prints_one_npu_measurement(self) -> None:
        source = Path(mock_npu_evaluator.__file__).read_text(encoding="utf-8")
        candidate = {
            "epitope_residues": ["B10", "B11"],
            "hotspots": "[B10,B11]",
            "num_designs": 1,
            "diffuser_t": 200,
            "final_step": 160,
        }
        binding = "_EVOLVE_CANDIDATE = " + json.dumps(candidate)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exec(binding + "\n" + source, {"__name__": "evolve"})  # noqa: S102
        line = stdout.getvalue().strip().splitlines()[-1]
        payload = json.loads(line)
        self.assertEqual(payload["device"], "npu")
        self.assertTrue(payload["mock"])
        self.assertEqual(payload["stages"], ["rfdiffusion", "proteinmpnn", "protenix", "screening"])
        self.assertGreater(payload["score"], 0.2)


class PackageAndRoundTests(unittest.TestCase):
    def test_package_writes_pipeline_config_and_round_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdb = root / "complex.pdb"
            pdb.write_text(complex_pdb(), encoding="utf-8")
            design = antigen_epitope.build_package(
                pdb, "B", ["H", "L"], root / "inputs", "antibody_pipeline/inputs",
                "1ABC", "literature_sources.json", "trial-antigen", 4.5,
            )
            self.assertEqual(design["frozen"]["epitope_residues"], ["B10", "B11"])
            self.assertEqual(design["config"]["target_pdb"], "antibody_pipeline/inputs/target_antigen.pdb")
            self.assertEqual(design["config"]["hotspots"], "[B10,B11]")
            self.assertEqual(design["evolve_start"]["epitope_residues"], ["B10", "B11"])
            self.assertEqual(design["evolve_editable"][0], "hotspots")
            start = json.loads((root / "inputs" / "evolve_start.json").read_text(encoding="utf-8"))
            self.assertEqual(start["hotspots"], "[B10,B11]")
            self.assertLess(mock_npu_evaluator.score_candidate({**start, "hotspots": start["hotspots"] + "-damaged"}),
                            mock_npu_evaluator.score_candidate(start))
            self.assertIn("ATOM", (root / "inputs" / "antibody_framework.pdb").read_text(encoding="utf-8"))
            self.assertNotIn("NAG", (root / "inputs" / "target_antigen.pdb").read_text(encoding="utf-8"))

            candidate = dict(design["baseline"])
            candidate["hotspots"] = "[B11,B10]"
            unchanged = antigen_epitope.decide_round(design, candidate, 0.2)
            self.assertFalse(unchanged["decision"]["accepted"])
            self.assertIn("没有改动", unchanged["decision"]["reason"])

            candidate["hotspots"] = "[B10]"
            accepted = antigen_epitope.decide_round(design, candidate, 0.4)
            self.assertTrue(accepted["decision"]["accepted"])
            self.assertEqual(accepted["decision"]["level"], "L1")
            self.assertEqual(accepted["parent"]["hotspots"], "[B10]")

            both = dict(accepted["parent"])
            both["num_designs"] = 2
            both["diffuser_t"] = 20
            rejected = antigen_epitope.decide_round(design, both, 0.9, accepted)
            self.assertIn("一轮只能改一个字段", rejected["decision"]["reason"])
            self.assertIsNone(rejected["decision"]["score"])

            outside = dict(accepted["parent"])
            outside["hotspots"] = "[B99]"
            rejected = antigen_epitope.decide_round(design, outside, 0.9, accepted)
            self.assertIn("不在已计算的表位内", rejected["decision"]["reason"])

            sequences = dict(accepted["parent"])
            sequences["designed_sequences"] = ["AAAA"]
            rejected = antigen_epitope.decide_round(design, sequences, 0.9, accepted)
            self.assertIn("尚无已设计序列", rejected["decision"]["reason"])

            short = dict(accepted["parent"])
            short["diffuser_t"] = 10
            rejected = antigen_epitope.decide_round(design, short, 0.9, accepted)
            self.assertIn("不能小于 15", rejected["decision"]["reason"])

            better_parent = dict(accepted["parent"])
            better_parent["num_designs"] = 2
            better = antigen_epitope.decide_round(design, better_parent, 0.5, accepted)
            self.assertTrue(better["decision"]["accepted"])
            self.assertEqual(better["decision"]["field"], "num_designs")

            back = dict(better["parent"])
            back["num_designs"] = 1
            again = antigen_epitope.decide_round(design, back, 0.4, better)
            self.assertIn("已经见过", again["decision"]["reason"])

            designed = dict(better["parent"])
            designed["designed_sequences"] = ["QVQL", "EVQL"]
            seeded = {"parent": designed, "seen": better["seen"], "rounds": better["rounds"]}
            edited = dict(designed)
            edited["designed_sequences"] = ["QVQL", "EVQL"]
            same_sequences = antigen_epitope.decide_round(design, edited, 0.6, seeded)
            self.assertIn("没有改动", same_sequences["decision"]["reason"])
            edited["designed_sequences"] = ["QVQM"]
            changed_count = antigen_epitope.decide_round(design, edited, 0.6, seeded)
            self.assertIn("不能增减条数", changed_count["decision"]["reason"])
            edited["designed_sequences"] = ["QVQM", "EVQL"]
            changed = antigen_epitope.decide_round(design, edited, 0.7, seeded)
            self.assertTrue(changed["decision"]["accepted"])
            self.assertEqual(changed["decision"]["level"], "L3")

            summary = antigen_epitope.render_summary(design, changed)
            self.assertIn("1ABC", summary)
            self.assertIn("num_designs", summary)
            self.assertIn("当前采用", summary)


if __name__ == "__main__":
    unittest.main()
