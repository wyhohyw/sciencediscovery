---
name: antigen-epitope-evolve
description: Turn one named antigen into a one-time literature note, a 4.5 angstrom complex epitope, and an antibody-design input, then record local one-field design rounds. Use when the user wants antigen-antibody PDB evidence, glycoprotein or N-protein chain extraction, epitope hotspots, and a short optimization ledger. Not for launching NPU models or searching the literature again after the first package.
compatibility: Requires Python 3 standard library only. Literature search and the antibody model run stay in their own skills.
metadata:
  version: 1.0.0
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

## One antibody-design run

This skill does not submit `run_npu_job`. When the user asks to design, select `antibody-design` and give it the copied config unchanged. Bring the screening score back as a number the user or that run actually produced. Do not invent a score, a sequence, or a PDB.

## Local rounds

Each round may change one field of the current accepted state:

- L1 `hotspots`: a non-empty subset of `frozen.epitope_residues`.
- L2, one of `num_designs`, `diffuser_t`, `final_step`, `protenix_n_sample`, `protenix_seeds`, `protenix_use_msa`. Keep `diffuser_t` at least 15 and `final_step` between 1 and `diffuser_t`.
- L3 `designed_sequences`: edit sequences that already exist. The first package has an empty list, so L3 is refused until sequences from `antibody-design` are copied into the ledger. Put those sequences on `parent.designed_sequences` and leave every other parent field unchanged. If `antigen_epitope/rounds.json` does not exist yet, create it as `{"parent": <baseline with those sequences>, "seen": {}, "rounds": []}`. Do not use `round` to invent the first sequences.

`literature_id`, `pdb_id`, chains, cutoff, and the full epitope list stay in `frozen`. A round that changes them, changes two fields, or repeats a state without a higher score is recorded and rejected.

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/antigen-epitope-evolve/scripts/antigen_epitope.py" round \
  --design-input antibody_pipeline/inputs/design_input.json \
  --candidate antigen_epitope/candidate.json \
  --score 0.42 \
  --ledger antigen_epitope/rounds.json
```

Write `candidate.json` as a full copy of the current `parent` object with one field changed. Pass the measured score. The script does not score the candidate.

After the requested rounds, write the summary:

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/antigen-epitope-evolve/scripts/antigen_epitope.py" summarize \
  --design-input antibody_pipeline/inputs/design_input.json \
  --ledger antigen_epitope/rounds.json \
  --output antigen_epitope/summary.md
```

Report the PDB, chain, changed fields, and which rounds were accepted. Do not describe a rejected edit as an improved antibody.
