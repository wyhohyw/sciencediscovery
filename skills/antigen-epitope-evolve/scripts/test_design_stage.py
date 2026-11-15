import json
import tempfile
import unittest
from pathlib import Path

from real_npu_evaluator import format_fasta, sequences_from_structure, stage_design


PDB = """\
ATOM      1  N   GLY H   1
ATOM      2  CA  GLY H   1
ATOM      3  CA  ALA H   2
ATOM      4  CA  MET L   1
END
"""

CIF = """\
data_test
loop_
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
CA GLY H 1
CA ALA H 2
CA MET L 1
"""


class DesignStageTest(unittest.TestCase):
    def test_pdb_and_cif_sequences(self):
        self.assertEqual(sequences_from_structure(PDB), [("H", "GA"), ("L", "M")])
        self.assertEqual(sequences_from_structure(CIF), [("H", "GA"), ("L", "M")])
        self.assertEqual(format_fasta([("H", "GA")]), ">H\nGA\n")

    def test_stage_writes_sequence_structure_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            cif = run / "model.cif"
            cif.write_text(CIF, encoding="utf-8")
            fasta = run / "design_0.fasta"
            fasta.write_text(">H\nQVQL\n", encoding="utf-8")
            dest = root / "out"
            staged = stage_design(
                str(run),
                {"protenix_model_cif": str(cif), "design": "design_0", "screen_score": "0.250"},
                "abc123",
                dest_root=str(dest),
            )
            self.assertTrue(staged)
            index = json.loads((dest / "abc123" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["sequence"], "antigen_epitope/results/abc123/sequence.fasta")
            self.assertEqual(index["structure"], "antigen_epitope/results/abc123/structure.cif")
            self.assertEqual(index["score"], "0.250")
            self.assertIn("QVQL", (dest / "abc123" / "sequence.fasta").read_text(encoding="utf-8"))
            self.assertTrue((dest / "abc123" / "structure.cif").is_file())


if __name__ == "__main__":
    unittest.main()
