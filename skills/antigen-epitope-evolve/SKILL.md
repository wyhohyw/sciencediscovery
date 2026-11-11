---
name: antigen-epitope-evolve
description: Turn one named antigen into a one-time literature note, a 4.5 angstrom complex epitope, and an antibody-design input, then record local one-field design rounds. Use when the user wants antigen-antibody PDB evidence, glycoprotein or N-protein chain extraction, epitope hotspots, and a short optimization ledger. Not for launching NPU models or searching the literature again after the first package.
compatibility: Requires Python 3 standard library only. Literature search and the antibody model run stay in their own skills.
metadata:
  version: 1.1.0
---

# Antigen epitope preparation and local rounds

Prepare the inputs for `antibody-design`. Do not launch that pipeline from this skill, and do not infer an epitope for an antigen that has no antibody chain in the chosen structure.

## Once, before any round

1. Ask for the antigen name and whether the chain of interest is the glycoprotein or the N protein. Optional: a PDB ID the user already trusts, a design count, and a run name.
2. Select `literature-searcher` and search once. The topic is antigen-antibody complexes for that antigen, plus antigen-only and antibody-only PDB entries. Save the deduplicated source package at `antigen_epitope/literature_sources.json`.
3. Select `report-writer` and write `antigen_epitope/literature_review.md` from that source package only. Name the complex PDB, the antigen chain, and the antibody chains, and quote the coverage gaps. Do not run another literature search to fill those gaps.
4. Stop if the package does not identify one antigen-antibody complex that contains the requested glycoprotein or N-protein chain. Say what is missing. Do not switch to a surface prediction on an antigen-only structure.

## Structure package

Run the bundled script on the local complex PDB. Do not copy the script or search the disk for another copy.

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/antigen-epitope-evolve/scripts/antigen_epitope.py" package \
  --pdb antigen_epitope/complex.pdb \
  --antigen-chain B \
  --antibody-chains H,L \
  --output-dir antibody_pipeline/inputs \
  --workspace-input-dir antibody_pipeline/inputs \
  --pdb-id 1ABC \
  --literature-id antigen_epitope/literature_sources.json \
  --run-name trial-antigen \
  --cutoff 4.5
```

Replace the chain IDs, PDB ID, and run name with the values from the review. The cutoff stays 4.5 unless the user sets a different one before this command. Later rounds must not change it.

A residue is an epitope residue when one of its non-hydrogen atoms is within the cutoff of a non-hydrogen atom on an antibody chain. The script keeps the first model and blank or A alternate locations. Water and other solvents are ignored. Glycans within the cutoff are listed in `epitope.json` and are not hotspots. Hotspots use the original antigen chain and residue numbers, for example `[B45,B46]`. Residues with insertion codes are reported separately and are left out of `hotspots`.

Read `antibody_pipeline/inputs/design_input.json`. If the command exits non-zero, report `epitope.json` field `error` and stop. Copy `antibody_pipeline/inputs/config.json` to `antibody_pipeline/config.json` only when handing the package to `antibody-design`.

## Iteration

The package writes `antibody_pipeline/inputs/evolve_start.json`. Pass that object to the existing evolve search. Do not run the local `round` ledger, and do not re-ask the evolve-design checkpoints. The editable range is already fixed.

Read the whole mock program at `$SCIENCEDISCOVERY_SKILLS_DIR/antigen-epitope-evolve/scripts/mock_npu_evaluator.py` and pass that text as `evaluatorSource`. `hotspots` stays first in `editableFields` because the search probe damages the first field. Leave `designed_sequences` out until a real design run has produced sequences; then add only that field in a later run.

```text
create_evolve_run(
  mode="npu_job",
  algorithm="puct",
  statement="Raise the antibody-design score by editing only the named JSON fields.",
  howScored="Mock NPU score from the candidate JSON. Higher is better. Hotspots must stay inside epitope_residues.",
  startingPointText=<contents of evolve_start.json>,
  editableFields=["hotspots", "num_designs", "diffuser_t", "final_step", "protenix_n_sample", "protenix_seeds", "protenix_use_msa"],
  evaluatorSource=<contents of mock_npu_evaluator.py>,
  expansions=4,
  workers=1,
  thinking="disabled"
)
```

Call it once and end the turn. The mock prints `device` `npu` and `mock` true. It walks the stage names RFdiffusion, ProteinMPNN, Protenix, and screening, then returns a deterministic score and placeholder sequences. It does not submit a model job. If `create_evolve_run` has no `npu_job` mode, stop and say this build cannot restrict an evolve run to JSON fields.

For a real NPU test, keep the same `startingPointText` and `editableFields`. Replace `evaluatorSource` with a program that runs `antibody-design` and prints one JSON line whose `device` is `npu` and whose `score` is the measured screening score. Do not describe mock sequences as designed antibodies.
