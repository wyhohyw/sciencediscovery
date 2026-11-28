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

"""Score one CDR candidate by running OpenDDE once and exiting.

Evolve binds ``_EVOLVE_CANDIDATE`` before this file runs. The last stdout line
is the measurement. ``score`` is the negation of the VHH confidence loss, so
the search can maximise it. The structural terms match
``vhh-confidence-contact8-proxy-esm2-v2``. A one-shot fold still omits ESM-2
until a pseudo-log-likelihood is passed in; then its weight is 0.1 and the
formula id becomes the esm2 version. The term is never replaced by another
model's ranking score.

Set ``OPENDDE_CODE_DIR``, ``OPENDDE_CHECKPOINT`` and ``OPENDDE_PYTHON``. ESM-2
and SolubleMPNN run as child processes of this same job under that Python,
because the job interpreter does not have those packages. Each child exits
before the next one starts, so the card is free for the fold. The fold stays
its own child and imports rdkit before OpenDDE.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWY")
WEIGHTS = {
    "plddt": 1.0,
    "i_plddt": 1.0,
    "pae": 0.1,
    "i_pae": 0.5,
    "i_ptm": 1.0,
    "con": 0.1,
    "i_con": 0.1,
    "rg": 0.1,
    "dgram_cce": 0.01,
}
CONTACT_PROBABILITY_CUTOFF = 8.0
GATE_CUTOFF = 5.0
CDR_FRACTION_THRESHOLD = 0.5
FORMULA = "vhh-confidence-contact8-proxy-v2"
ESM2_FORMULA = "vhh-confidence-contact8-proxy-esm2-v2"
ESM2_WEIGHT = 0.1


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def amino_acid(sequence: str) -> bool:
    return bool(sequence) and all(letter in AMINO_ACIDS for letter in sequence)


def _mean(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        raise ValueError("cannot average an empty confidence selection")
    return sum(finite) / len(finite)


def _unit(value: float) -> float:
    number = float(value)
    return number / 100 if number > 1 else number


def _clip(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _matrix(value: object, count: int, name: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != count or any(not isinstance(row, list) or len(row) != count for row in value):
        raise ValueError(f"{name} must be a {count} by {count} matrix")
    return [[float(item) for item in row] for row in value]


def _ordered_unique(values: list[int]) -> list[int]:
    found: list[int] = []
    for value in values:
        if value not in found:
            found.append(value)
    return found


def token_plddt(full: dict, count: int) -> list[float]:
    atoms = [_unit(item) for item in full["atom_plddt"]]
    assigned = [int(item) for item in full["atom_to_token_idx"]]
    if len(atoms) != len(assigned):
        raise ValueError("atom_plddt and atom_to_token_idx must have equal length")
    totals = [0.0] * count
    counts = [0] * count
    for value, token in zip(atoms, assigned):
        if not 0 <= token < count:
            raise ValueError("atom_to_token_idx leaves the token range")
        totals[token] += value
        counts[token] += 1
    if any(count == 0 for count in counts):
        raise ValueError("every protein token must have atom-level pLDDT")
    return [totals[index] / counts[index] for index in range(count)]


def chain_tokens(full: dict, sequences: dict[str, str]) -> dict[str, list[int]]:
    asym = [int(item) for item in full["token_asym_id"]]
    if len(asym) != sum(len(sequence) for sequence in sequences.values()):
        raise ValueError("confidence tokens do not match the sequences")
    groups = _ordered_unique(asym)
    if len(groups) != len(sequences):
        raise ValueError("confidence chain ids do not match the sequences")
    tokens = {chain: [index for index, value in enumerate(asym) if value == group] for chain, group in zip(sequences, groups)}
    for chain, indices in tokens.items():
        if len(indices) != len(sequences[chain]):
            raise ValueError(f"confidence chain {chain} does not match its sequence")
    return tokens


def top_contact_loss(probs: list[list[float]], rows: list[int], cols: list[int], top_k: int = 10) -> float:
    if not rows or not cols:
        raise ValueError("contact loss requires both sides")
    width = min(max(1, top_k), len(cols))
    per_row = []
    for row in rows:
        losses = sorted(-math.log(_clip(probs[row][col], 1e-8, 1.0)) for col in cols)
        per_row.append(sum(losses[:width]) / width)
    return _mean(per_row)


def binder_contact_loss(probs: list[list[float]], indices: list[int]) -> float:
    per_residue = []
    for place, token in enumerate(indices):
        partners = [other for other_place, other in enumerate(indices) if abs(place - other_place) >= 9]
        if not partners:
            continue
        losses = sorted(-math.log(_clip(probs[token][partner], 1e-8, 1.0)) for partner in partners)
        chosen = losses[:2]
        per_residue.append(sum(chosen) / len(chosen))
    return _mean(per_residue)


def submatrix_mean(pae: list[list[float]], rows: list[int], cols: list[int]) -> float:
    values = [((pae[row][col] + pae[col][row]) / 2) / 31 for row in rows for col in cols]
    return _mean(values)


def radius_penalty(points: list[tuple[float, float, float]]) -> float:
    count = len(points)
    center = tuple(sum(point[axis] for point in points) / count for axis in range(3))
    mean_square = sum(sum((point[axis] - center[axis]) ** 2 for axis in range(3)) for point in points) / count
    radius = math.sqrt(mean_square + 1e-8)
    delta = radius - 2.38 * count ** 0.365
    return delta if delta > 0 else math.expm1(delta)


def cdr_positions(ranges: object, length: int) -> list[int]:
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("cdr_regions must list inclusive 1-based ranges")
    found: set[int] = set()
    for region in ranges:
        if not isinstance(region, list) or len(region) != 2:
            raise ValueError("each cdr_regions entry is [start, end]")
        start, end = int(region[0]), int(region[1])
        if start < 1 or end > length or start > end:
            raise ValueError("a CDR range falls outside the sequence")
        found.update(range(start - 1, end))
    return sorted(found)


def pdb_atom(line: str) -> dict | None:
    if not line.startswith("ATOM  ") or len(line) < 54:
        return None
    if line[16].strip() not in {"", "A"}:
        return None
    element = line[76:78].strip().upper() if len(line) >= 78 else ""
    if not element:
        element = line[12:16].strip()[:1].upper()
    try:
        return {
            "name": line[12:16].strip(),
            "chain": line[21].strip() or "_",
            "residue_number": int(line[22:26]),
            "element": element,
            "x": float(line[30:38]),
            "y": float(line[38:46]),
            "z": float(line[46:54]),
        }
    except ValueError:
        return None


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


def cif_atoms(text: str) -> list[dict]:
    tokens = list(cif_tokens(text))
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
        if width == 0 or not any(name.endswith(".Cartn_x") for name in columns):
            while index < len(tokens) and tokens[index] not in {"loop_", "stop_"} and not tokens[index].startswith(("_", "data_", "save_")):
                index += 1
            continue
        lookup = {name.rsplit(".", 1)[-1]: place for place, name in enumerate(columns)}
        atoms = []
        while index + width <= len(tokens) and tokens[index] not in {"loop_", "stop_"} and not tokens[index].startswith(("_", "data_", "save_")):
            row = tokens[index:index + width]
            index += width

            def cell(*names: str) -> str:
                for name in names:
                    place = lookup.get(name)
                    if place is not None:
                        return row[place]
                return ""

            if cell("group_PDB") not in {"", "ATOM"}:
                continue
            try:
                atoms.append({
                    "name": cell("auth_atom_id", "label_atom_id"),
                    "chain": cell("auth_asym_id", "label_asym_id") or "_",
                    "residue_number": int(float(cell("auth_seq_id", "label_seq_id"))),
                    "element": cell("type_symbol").upper(),
                    "x": float(cell("Cartn_x")),
                    "y": float(cell("Cartn_y")),
                    "z": float(cell("Cartn_z")),
                })
            except ValueError:
                continue
        return atoms
    raise ValueError("mmCIF has no atom coordinates")


def pdb_atoms(text: str) -> list[dict]:
    found = []
    for line in text.splitlines():
        if line.startswith("ENDMDL"):
            break
        atom = pdb_atom(line)
        if atom:
            found.append(atom)
    return found


def load_atoms(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    atoms = cif_atoms(text) if path.suffix.lower() in {".cif", ".mmcif"} else pdb_atoms(text)
    heavy = [atom for atom in atoms if atom["element"] not in {"H", "D"}]
    residues: dict[tuple[str, int], int] = {}
    next_index: dict[str, int] = {}
    for atom in heavy:
        if atom["name"] != "CA":
            continue
        key = (str(atom["chain"]), int(atom["residue_number"]))
        if key in residues:
            continue
        residues[key] = next_index.get(key[0], 0)
        next_index[key[0]] = residues[key] + 1
    for atom in heavy:
        atom["sequence_index"] = residues.get((str(atom["chain"]), int(atom["residue_number"])))
    return heavy


def ca_points(atoms: list[dict], chain: str, length: int) -> list[tuple[float, float, float]]:
    points = []
    seen: set[int] = set()
    for atom in atoms:
        if atom["chain"] != chain or atom["name"] != "CA" or atom["sequence_index"] in seen or atom["sequence_index"] is None:
            continue
        seen.add(int(atom["sequence_index"]))
        points.append((int(atom["sequence_index"]), (float(atom["x"]), float(atom["y"]), float(atom["z"]))))
    points.sort()
    if [index for index, _point in points] != list(range(length)):
        raise ValueError(f"chain {chain} does not have one CA for every residue")
    return [point for _index, point in points]


def contact_gate(atoms: list[dict], binder: str, target: str, cdr: set[int], cdr3: set[int], hotspots: set[int]) -> dict:
    binder_atoms = [atom for atom in atoms if atom["chain"] == binder and atom["sequence_index"] is not None]
    target_atoms = [atom for atom in atoms if atom["chain"] == target and atom["sequence_index"] is not None]
    limit = GATE_CUTOFF * GATE_CUTOFF
    interface: set[int] = set()
    cdr_interface: set[int] = set()
    cdr3_hotspot_pairs: set[tuple[int, int]] = set()
    for left in binder_atoms:
        for right in target_atoms:
            distance = (
                (left["x"] - right["x"]) ** 2
                + (left["y"] - right["y"]) ** 2
                + (left["z"] - right["z"]) ** 2
            )
            if distance > limit:
                continue
            position = int(left["sequence_index"])
            interface.add(position)
            if position in cdr:
                cdr_interface.add(position)
            if position in cdr3 and int(right["sequence_index"]) in hotspots:
                cdr3_hotspot_pairs.add((position, int(right["sequence_index"])))
    fraction = len(cdr_interface) / len(interface) if interface else 0.0
    fraction_passed = bool(interface) and fraction > CDR_FRACTION_THRESHOLD
    cdr3_passed = not hotspots or bool(cdr3_hotspot_pairs)
    return {
        "cdr_contact_fraction": round(fraction, 4),
        "framework_contact_fraction": round(1 - fraction, 4) if interface else 0.0,
        "cdr_contact_fraction_gate_passed": fraction_passed,
        "cdr3_gate_passed": cdr3_passed,
        "gate_passed": fraction_passed and cdr3_passed,
    }


def structure_loss(
    full: dict,
    summary: dict,
    atoms: list[dict],
    sequences: dict[str, str],
    binder: str,
    cdr: list[int],
    hotspots: set[int],
) -> dict[str, float]:
    tokens = chain_tokens(full, sequences)
    count = sum(len(sequence) for sequence in sequences.values())
    plddt = token_plddt(full, count)
    pae = _matrix(full.get("token_pair_pae"), count, "token_pair_pae")
    contacts = _matrix(full.get("contact_probs"), count, "contact_probs")
    binder_tokens = tokens[binder]
    target_name = next(chain for chain in sequences if chain != binder)
    target_tokens = tokens[target_name]
    if any(position < 0 or position >= len(target_tokens) for position in hotspots):
        raise ValueError("a target hotspot falls outside the target sequence")
    objective = [target_tokens[position] for position in sorted(hotspots)] or target_tokens
    framework = [position for position in range(len(sequences[binder])) if position not in set(cdr)]
    if not cdr or not framework or not target_tokens:
        raise ValueError("loss scoring requires a target, CDR residues, and framework residues")
    cdr_tokens = [binder_tokens[position] for position in cdr]
    framework_tokens = [binder_tokens[position] for position in framework]
    cdr_contact = top_contact_loss(
        contacts,
        objective if hotspots else cdr_tokens,
        cdr_tokens if hotspots else target_tokens,
    )
    framework_contact = top_contact_loss(contacts, target_tokens, framework_tokens)
    paratope = cdr_contact * (cdr_contact / max(framework_contact - 1.0, 1e-8))
    points = []
    for chain in [binder]:
        points.extend(ca_points(atoms, chain, len(sequences[chain])))
    flat = {binder_tokens[position]: ca_points(atoms, binder, len(sequences[binder]))[position] for position in framework}
    dgram = []
    for offset, token_i in enumerate(framework_tokens):
        for token_j in framework_tokens[offset + 1:]:
            probability = _clip(contacts[token_i][token_j], 1e-8, 1 - 1e-8)
            actual = math.dist(flat[token_i], flat[token_j]) < CONTACT_PROBABILITY_CUTOFF
            dgram.append(-(actual * math.log(probability) + (1 - actual) * math.log(1 - probability)))
    return {
        "plddt": 1 - _mean([plddt[token] for token in binder_tokens]),
        "i_plddt": 1 - _mean([plddt[token] for token in cdr_tokens]),
        "pae": submatrix_mean(pae, binder_tokens, list(range(count))),
        "i_pae": submatrix_mean(pae, cdr_tokens, objective),
        "i_ptm": 1 - _unit(summary["iptm"]),
        "con": binder_contact_loss(contacts, binder_tokens),
        "i_con": paratope,
        "rg": radius_penalty(points),
        "dgram_cce": _mean(dgram) if dgram else 0.0,
    }


def backbone_pdb(atoms: list[dict], chains: set[str]) -> str:
    """A PDB with only backbone atoms, small enough to hand to the next round.

    SolubleMPNN redesigns the binder. The target chain is left out so the next
    job does not push the candidate past the runner's argument-length limit.
    """
    lines = ["REMARK parent backbone"]
    serial = 1
    for atom in atoms:
        if str(atom.get("chain")) not in chains or atom.get("name") not in {"N", "CA", "C", "O"}:
            continue
        if atom.get("sequence_index") is None:
            continue
        name = str(atom["name"])
        padded = f" {name:<3}" if len(name) < 4 else name[:4]
        element = str(atom.get("element") or name[:1])[:2]
        lines.append(
            f"ATOM  {serial:5d} {padded} ALA {atom['chain']}{int(atom['residue_number']):4d}    "
            f"{float(atom['x']):8.3f}{float(atom['y']):8.3f}{float(atom['z']):8.3f}"
            f"  1.00  0.00           {element:>2}"
        )
        serial += 1
    lines.append("END")
    return "\n".join(lines) + "\n"


def pack_backbone(pdb: str) -> str:
    """Compress a backbone so the next job can carry it in the candidate JSON."""
    if not pdb:
        return pdb
    if pdb.startswith("gz:"):
        return pdb
    packed = base64.b64encode(gzip.compress(pdb.encode("utf-8"), compresslevel=9)).decode("ascii")
    return "gz:" + packed


def unpack_backbone(value: str) -> str:
    """Accept a plain PDB or the compressed form from ``pack_backbone``."""
    if not isinstance(value, str) or not value.startswith("gz:"):
        return value
    return gzip.decompress(base64.b64decode(value[3:].encode("ascii"))).decode("utf-8")


ESM_SKILL = "esm2-guided-mutation"
MPNN_SKILL = "antibody-inverse-folding"
# The harness default for one design call. A width of 1 is the single top
# substitution, which is the same child on every later call.
HARNESS_BATCH = 8


def sequence_mutations(
    parent: str, child: str, chain: str, mutable: set[int],
) -> list[list] | None:
    """CDR substitutions that turn ``parent`` into ``child``, or None.

    Inverse folding may rewrite every designable CDR residue. A framework
    change is not a proposal. The 1–3 budget belongs to the chat
    point-mutation skill, which the engine runs outside this evaluator.
    """
    if len(parent) != len(child) or parent == child:
        return None
    mutations = []
    for index, (left, right) in enumerate(zip(parent, child), start=1):
        if left == right:
            continue
        if index not in mutable or right not in AMINO_ACIDS:
            return None
        mutations.append([chain, index, right])
    if not mutations:
        return None
    return mutations


def design_skill(
    seed: int,
    cycle: int,
    has_backbone: bool,
    forced: str | None = None,
) -> str:
    """Pick the NPU generation skill for this propose job.

    The engine draws among point mutation, ESM-2, and SolubleMPNN. Point
    mutation never reaches this evaluator. When the engine already chose
    ESM-2 or SolubleMPNN, ``forced`` is that name. Without a backbone,
    SolubleMPNN falls back to ESM-2. A missing force keeps the equal-weight
    ESM/MPNN draw used by older payloads.
    """
    if forced == MPNN_SKILL and has_backbone:
        return MPNN_SKILL
    if forced == ESM_SKILL or (forced == MPNN_SKILL and not has_backbone):
        return ESM_SKILL
    legal = [ESM_SKILL]
    weights = [1.0]
    if has_backbone:
        legal.append(MPNN_SKILL)
        weights.append(1.0)
    return random.Random(f"protein-design-router:{seed}:{cycle}").choices(legal, weights=weights, k=1)[0]


def esm_batch(
    parent: str,
    chain: str,
    mutable: set[int],
    dms_rows: list,
    limit: int,
) -> list[tuple[str, list[list], str]]:
    """One ESM-2 call's diverse substitutions, matching ``_generate_esm2_guided``.

    Positive and zero log-likelihood ratios are kept. The list is sorted by
    ratio, then filled up to ``limit`` with a new CDR site first and a second
    amino acid at a used site only after that. History is not an index into
    this list: the same parent yields the same batch.
    """
    if limit < 1:
        return []
    ranked: list[tuple[float, str, str, list[list], str, int]] = []
    for row in dms_rows:
        position = row.get("Pos") if isinstance(row, dict) else None
        wild = row.get("WT") if isinstance(row, dict) else None
        if isinstance(position, bool) or not isinstance(position, int) or position not in mutable:
            continue
        if not isinstance(wild, str) or len(wild) != 1:
            continue
        if position < 1 or position > len(parent) or parent[position - 1] != wild:
            continue
        for amino, value in row.items():
            if not isinstance(amino, str) or amino not in AMINO_ACIDS or amino == wild:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            ratio = float(value)
            if ratio < 0:
                continue
            child = parent[:position - 1] + amino + parent[position:]
            ranked.append((
                ratio,
                f"esm2_{chain}{position}{wild}{amino}",
                child,
                [[chain, position, amino]],
                f"{chain}{position}{wild}{amino}",
                position,
            ))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected: list[tuple[str, list[list], str]] = []
    used_sites: set[int] = set()
    chosen: set[str] = set()
    for require_new_site in (True, False):
        for _ratio, identity, child, mutations, summary, position in ranked:
            if identity in chosen or (require_new_site and position in used_sites):
                continue
            chosen.add(identity)
            used_sites.add(position)
            selected.append((child, mutations, summary))
            if len(selected) >= limit:
                return selected
    return selected


def inverse_folding_batch(
    parent: str,
    chain: str,
    mutable: set[int],
    rounds: list[list[str]],
    limit: int,
    blocked: set[str] | None = None,
) -> list[tuple[str, list[list], str]]:
    """Unique SolubleMPNN sequences from at most three sampling rounds.

    A round is one ``design`` call. Duplicates and sequences already folded
    are dropped. The next round uses the next seed, which the caller passes
    by sampling again. A framework edit is dropped. CDR edits are not capped
    at three. The function does not walk an ESM-2 ranking to fill a shortfall.
    """
    if limit < 1:
        return []
    seen = set(blocked or ())
    seen.add(parent)
    selected: list[tuple[str, list[list], str]] = []
    for sequences in rounds[:3]:
        if len(selected) >= limit:
            break
        if not isinstance(sequences, list):
            continue
        for sequence in sequences:
            if not isinstance(sequence, str) or sequence in seen:
                continue
            mutations = sequence_mutations(parent, sequence, chain, mutable)
            if not mutations:
                continue
            seen.add(sequence)
            notes = [f"{item[0]}{item[1]}{parent[item[1] - 1]}{item[2]}" for item in mutations]
            selected.append((sequence, mutations, " ".join(notes)))
            if len(selected) >= limit:
                break
    return selected


def without_folded(
    batch: list[tuple[str, list[list], str]],
    blocked: set[str],
) -> list[tuple[str, list[list], str]]:
    """Drop sequences already folded. Do not replace them from further down a list."""
    return [item for item in batch if item[0] not in blocked]


def cycle_batch(
    parent: str,
    chain: str,
    mutable: set[int],
    skill: str,
    limit: int,
    esm_rows: list,
    mpnn_rounds: list[list[str]],
    has_backbone: bool,
    blocked: set[str] | None = None,
) -> tuple[str, list[tuple[str, list[list], str]]]:
    """The drawn skill's batch, or the other legal skill when that batch is already folded.

    ESM-2 is not asked for the next ranked substitution to fill a gap. A short
    or empty inverse-folding sample stays short. The other skill runs only
    when the drawn skill produced nothing new.
    """
    used = set(blocked or ())
    used.add(parent)

    def materialize(name: str) -> list[tuple[str, list[list], str]]:
        if name == MPNN_SKILL:
            if not has_backbone:
                return []
            return inverse_folding_batch(parent, chain, mutable, mpnn_rounds, limit, used)
        return without_folded(esm_batch(parent, chain, mutable, esm_rows, limit), used)

    fresh = materialize(skill)
    if fresh:
        return skill, fresh
    # An empty source means that model has not been run yet. Leave the skill
    # unchanged so the caller can run it, then ask again. Do not fill the gap
    # from the next ESM-2 rank.
    other = ""
    if skill == ESM_SKILL and has_backbone and mpnn_rounds:
        other = MPNN_SKILL
    elif skill == MPNN_SKILL and esm_rows:
        other = ESM_SKILL
    if not other:
        return skill, []
    return other, materialize(other)


def score_fold(
    candidate: dict, summary: dict, full: dict, structure: Path,
    esm2_pll: float | None = None,
) -> dict:
    binder = str(candidate.get("chain") or "D")
    target = str(candidate.get("target_chain") or "A")
    sequence = str(candidate["sequence"])
    target_sequence = str(candidate.get("target_sequence") or "")
    if not amino_acid(target_sequence):
        raise ValueError("target_sequence is required")
    sequences = {target: target_sequence, binder: sequence}
    cdr = cdr_positions(candidate.get("cdr_regions"), len(sequence))
    regions = candidate["cdr_regions"]
    last = regions[-1]
    cdr3 = set(range(int(last[0]) - 1, int(last[1])))
    raw_hotspots = candidate.get("target_hotspots") or []
    if isinstance(raw_hotspots, dict):
        raw_hotspots = raw_hotspots.get(target, [])
    hotspot_positions = {int(item) - 1 for item in raw_hotspots} if isinstance(raw_hotspots, list) else set()
    atoms = load_atoms(structure)
    gate = contact_gate(atoms, binder, target, set(cdr), cdr3, hotspot_positions)
    components = structure_loss(full, summary, atoms, sequences, binder, cdr, hotspot_positions)
    loss = sum(WEIGHTS[name] * components[name] for name in WEIGHTS)
    esm2: float | str = "omitted"
    formula = FORMULA
    if esm2_pll is not None:
        pll = float(esm2_pll)
        if not math.isfinite(pll):
            raise ValueError("esm2 pll must be finite")
        # The harness subtracts weight * pll. A typical pll is negative, so
        # this penalizes a less natural sequence. Weight 0.1 matches
        # vhh-confidence-contact8-proxy-esm2-v2.
        loss += -ESM2_WEIGHT * pll
        esm2 = pll
        formula = ESM2_FORMULA
    reason = ""
    if not gate["gate_passed"]:
        reason = (
            f"CDR contact fraction {gate['cdr_contact_fraction']} "
            f"or CDR3 hotspot contacts failed the {GATE_CUTOFF:g} A gate"
        )
    return {
        "components": {name: round(components[name], 6) for name in WEIGHTS},
        "device": "npu",
        "esm2": round(esm2, 6) if isinstance(esm2, float) else esm2,
        "formula": formula,
        "gate_passed": gate["gate_passed"],
        "cdr_contact_fraction": gate["cdr_contact_fraction"],
        "framework_contact_fraction": gate["framework_contact_fraction"],
        "iptm": summary.get("iptm"),
        "loss": loss,
        "plddt": summary.get("plddt"),
        "ran": True,
        "score": -loss,
        **({"error": reason} if reason else {}),
    }


def input_document(candidate: dict) -> list[dict]:
    return [{
        "modelSeeds": [42],
        "name": "cdr",
        "sequences": [
            {"proteinChain": {"count": 1, "id": [str(candidate.get("target_chain") or "A")], "sequence": candidate["target_sequence"]}},
            {"proteinChain": {"count": 1, "id": [str(candidate.get("chain") or "D")], "sequence": candidate["sequence"]}},
        ],
    }]


def stage(name: str) -> None:
    print(f"EVOLVE_STAGE {name}", flush=True)


def _device() -> str:
    return os.environ.get("ESM_DEVICE") or os.environ.get("OPENDDE_DEVICE") or "npu"


# The NPU job interpreter does not have the harness. This program runs under
# OPENDDE_PYTHON, prints one JSON object, and exits so the card is released.
_MODEL_WORKER = r'''
import json
import os
import sys
import tempfile
from pathlib import Path


def plain(value):
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes)):
        try:
            return item()
        except Exception:
            return value
    return value


def install_ligand(ligand):
    root = tempfile.mkdtemp(prefix="cdr-ligand-")
    external = Path(root) / "external"
    package = external / "ligandmpnn"
    external.mkdir()
    (external / "__init__.py").write_text("")
    package.symlink_to(ligand)
    return root


def main():
    request = json.loads(sys.stdin.read())
    harness = os.environ.get("OPENDDE_HARNESS_DIR", "/data1/gs_agent/opendde/OpenDDE-Harness")
    ligand = os.environ.get("LIGANDMPNN_DIR", "/data1/gs_agent/opendde/LigandMPNN")
    sys.path.insert(0, harness)
    device = request.get("device") or "npu"
    model = os.environ.get("ESM_MODEL_NAME", "/data1/gs_agent/opendde/weights/esm2")
    operation = request["op"]
    if operation == "scan":
        from opendde_harness.plugin.protein_design.servers.backends.esm_backend import ESMClient
        client = ESMClient(model_name=model, offline=True, device=device)
        rows = client.calculate_deep_mutation_scan(request["sequence"], scoring_mode="llr")
        print(json.dumps({"rows": plain(list(rows))}), flush=True)
        return
    if operation == "pll":
        from opendde_harness.plugin.protein_design.servers.backends.esm_backend import ESMClient
        client = ESMClient(model_name=model, offline=True, device=device)
        print(json.dumps({"pll": float(client.calculate_pseudo_log_likelihood(request["sequence"]))}), flush=True)
        return
    if operation == "soluble":
        import shutil
        ligand_root = install_ligand(ligand)
        try:
            sys.path.insert(0, ligand_root)
            from opendde_harness.plugin.protein_design.servers.backends.soluble_mpnn import SolubleMPNNClient
            weights = os.environ.get(
                "SOLUBLE_MPNN_WEIGHTS",
                "/data1/gs_agent/opendde/weights/soluble_mpnn/solublempnn_v_48_020.pt",
            )
            anchor = request.get("anchor")
            forced = None
            if isinstance(anchor, list) and len(anchor) == 3:
                forced = {int(anchor[1]) - 1: str(anchor[2])}
            raw_seed = request.get("seed", 42)
            seed = raw_seed if isinstance(raw_seed, int) and not isinstance(raw_seed, bool) and raw_seed >= 0 else 42
            raw_count = request.get("num_sequences", 1)
            count = raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 1 else 1
            client = SolubleMPNNClient(weights_path=weights, device=device, seed=seed)
            with tempfile.TemporaryDirectory(prefix="cdr-mpnn-") as work:
                pdb_path = Path(work) / "parent.pdb"
                pdb_path.write_text(request["backbone"], encoding="utf-8")
                designs = client.design(
                    pdb_path=str(pdb_path),
                    chain=request["chain"],
                    designable_positions=sorted(int(position) - 1 for position in request["mutable"]),
                    expected_length=len(request["sequence"]),
                    temperature=0.1,
                    num_sequences=count,
                    seed=seed,
                    forced_residues=forced,
                )
            sequences = [str(item["sequence"]) for item in designs if isinstance(item, dict) and item.get("sequence")]
            print(json.dumps({"sequences": sequences}), flush=True)
        finally:
            shutil.rmtree(ligand_root, ignore_errors=True)
        return
    raise SystemExit("unknown model operation")


if __name__ == "__main__":
    main()
'''


def _model_python() -> str:
    return configured("OPENDDE_PYTHON", "/data1/gs_agent/opendde/venv_pd/bin/python") or sys.executable


def _model_call(op: str, payload: dict) -> dict:
    """One child under the OpenDDE Python. It exits before the caller continues."""
    python = _model_python()
    if not python:
        raise RuntimeError("OPENDDE_PYTHON is required for ESM-2 and SolubleMPNN")
    with tempfile.TemporaryDirectory(prefix="cdr-model-") as work:
        script = Path(work) / "worker.py"
        script.write_text(_MODEL_WORKER, encoding="utf-8")
        completed = subprocess.run(
            [python, str(script)],
            input=json.dumps({"op": op, "device": _device(), **payload}),
            capture_output=True,
            text=True,
            env=_child_env(),
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "model worker failed").strip()
        raise RuntimeError(detail[-2000:])
    line = next((item for item in reversed(completed.stdout.splitlines()) if item.strip()), "")
    try:
        loaded = json.loads(line)
    except json.JSONDecodeError as error:
        raise RuntimeError(line[-500:] or "model worker returned no JSON") from error
    if not isinstance(loaded, dict):
        raise RuntimeError("model worker returned a non-object")
    return loaded


def esm_scan(sequence: str) -> list:
    """Deep mutational scan in its own process, so the model is gone before the fold."""
    stage("esm2-scan")
    rows = _model_call("scan", {"sequence": sequence}).get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("ESM-2 scan returned no rows")
    return rows


def esm_pll(sequence: str) -> float:
    stage("esm2-pll")
    value = float(_model_call("pll", {"sequence": sequence})["pll"])
    if not math.isfinite(value):
        raise ValueError("esm2 pll must be finite")
    return value


def soluble_sequences(
    backbone_pdb: str,
    chain: str,
    sequence: str,
    mutable: set[int],
    anchor: list | None,
    seed: int = 42,
    num_sequences: int = 1,
) -> list[str]:
    """One SolubleMPNN sampling round. Framework positions stay fixed."""
    stage("soluble-mpnn")
    sequences = _model_call("soluble", {
        "backbone": backbone_pdb,
        "chain": chain,
        "sequence": sequence,
        "mutable": sorted(mutable),
        "anchor": anchor,
        "seed": seed,
        "num_sequences": num_sequences,
    }).get("sequences")
    if not isinstance(sequences, list):
        raise RuntimeError("SolubleMPNN returned no sequences")
    return [str(item) for item in sequences]


def configured(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return default if os.path.exists(default) else ""


# Environment probe. It runs once per search, before any candidate, and reports
# each component by name. A missing component is installed when there is a safe
# source for it; otherwise the report says why. Installs go under DEPS_DIR, a
# sibling of the colleague's OpenDDE tree, never inside it.
DEPS_DIR = os.environ.get("SD_DEPS_DIR", "/data1/gs_agent/sciencediscovery-deps")
PROBE_CHECKS = (
    "npu", "rdkit", "biotite", "fold_code", "fold_checkpoint", "esm_module",
    "esm_weights", "harness_modules", "soluble_weights", "ligand_code",
)
# pip packages, pinned to the versions in the colleague's working venv.
PIP_REQUIREMENTS = {
    "esm_module": "fair-esm==2.0.0",
    "rdkit": "rdkit==2025.9.3",
    "biotite": "biotite==1.4.0",
}
# Weights, pinned to the revisions the colleague's run used.
# The repo moved to biohub/ on the mirror; the pinned revision serves byte-identical files.
ESM_REPO = "biohub/esm2_t33_650M_UR50D"
ESM_REVISION = "08e4846e537177426273712802403f7ba8261b6c"
ESM_FILES = {
    "config.json": 0,
    "model.safetensors": 2609506392,
    "special_tokens_map.json": 0,
    "tokenizer_config.json": 0,
    "vocab.txt": 0,
}
CHECKPOINT_URL = (
    "https://hf-mirror.com/aurekaresearch/OpenDDE/resolve/"
    "eddd563ce96571f784012edd8f045181c8f8627d/opendde_abag.pt"
)
CHECKPOINT_SHA256 = "5cf37441ddef2a2f148b81dd4a218ad274f996fecaf17dec901ab6cf1351713d"
CHECKPOINT_SIZE = 2625271509
SOLUBLE_URL = "https://files.ipd.uw.edu/pub/ligandmpnn/solublempnn_v_48_020.pt"
# Code is reproduced, not cloned as-is: the colleague's three trees carry local changes, so a
# clone is pinned to the upstream commit and the colleague's `git diff --binary HEAD` is applied.
# `tree` is the first 20 hex digits of sha256 over the diff of the reproduced tree; it must
# equal the colleague's, or the install fails. The patches are also kept in the repo under
# skills/evolve-design/patches/ for review.
CODE_SOURCES = {
    "fold_code": {
        "name": "OpenDDE",
        "url": "https://github.com/aurekaresearch/OpenDDE.git",
        "commit": "9d0d9c975178074c1ce722d2d6eefac8b7057062",
        "tree": "b9cbbd909abd02550fb1",
        "dir": "code/OpenDDE",
    },
    "harness_modules": {
        "name": "OpenDDE-Harness",
        "url": "https://github.com/aurekaresearch/OpenDDE-Harness.git",
        "commit": "fdab4f0ade76db41a300f1bab4c931540dc7a557",
        "tree": "cb3d02982a7f01f67ea5",
        "dir": "code/OpenDDE-Harness",
    },
    "ligand_code": {
        "name": "LigandMPNN",
        "url": "https://github.com/dauparas/LigandMPNN.git",
        "commit": "26ec57ac976ade5379920dbd43c7f97a91cf82de",
        "tree": "b8c71102c3fdd3cfb8a5",
        "dir": "code/LigandMPNN",
    },
}
CODE_PATCHES = {
    "OpenDDE": (
        "ZGlmZiAtLWdpdCBhL29wZW5kZGUvY29uZmlnL3NjaGVtYS5weSBiL29wZW5kZGUvY29uZmlnL3NjaGVtYS5weQppbmRleCBmZmJlNTE2Li42NjcyZjU2IDEwMDY0NAotLS0gYS9vcGVuZGRlL2NvbmZpZy9zY2hlbWEucHkKKysrIGIvb3BlbmRkZS9jb25maWcvc2NoZW1hLnB5CkBAIC0xOCwxMiArMTgsMTMgQEAgZnJvbSBweWRhbnRpYyBpbXBvcnQgQmFzZU1vZGVsLCBDb25maWdEaWN0CiAKIEluZmVyZW5jZUR0eXBlID0gTGl0ZXJhbFsiYmYxNiIsICJmcDMyIl0KIElORkVSRU5DRV9EVFlQRV9DSE9JQ0VTOiB0dXBsZVtJbmZlcmVuY2VEdHlwZSwgLi4uXSA9ICgiYmYxNiIsICJmcDMyIikKLUluZmVyZW5jZURldmljZSA9IExpdGVyYWxbImF1dG8iLCAiY3B1IiwgImN1ZGEiLCAibXBzIl0KK0luZmVyZW5jZURldmljZSA9IExpdGVyYWxbImF1dG8iLCAiY3B1IiwgImN1ZGEiLCAibXBzIiwgIm5wdSJdCiBJTkZFUkVOQ0VfREVWSUNFX0NIT0lDRVM6IHR1cGxlW0luZmVyZW5jZURldmljZSwgLi4uXSA9ICgKICAgICAiYXV0byIsCiAgICAgImNwdSIsCiAgICAgImN1ZGEiLAogICAgICJtcHMiLAorICAgICJucHUiLAogKQogVHJpYW5nbGVLZXJuZWwgPSBMaXRlcmFsWyJhdXRvIiwgImN1ZXF1aXZhcmlhbmNlIiwgInRvcmNoIl0KIApkaWZmIC0tZ2l0IGEvb3BlbmRkZS9tb2RlbC9zYW1wbGVfY29uZmlkZW5jZS5weSBiL29wZW5kZGUvbW9kZWwvc2FtcGxlX2NvbmZpZGVuY2UucHkKaW5kZXggZGZmYzI0Ni4uOTM1N2UxNCAxMDA2NDQKLS0tIGEvb3BlbmRkZS9tb2RlbC9zYW1wbGVfY29uZmlkZW5jZS5weQorKysgYi9vcGVuZGRlL21vZGVsL3NhbXBsZV9jb25maWRlbmNlLnB5CkBAIC03MzMsNyArNzMzLDEyIEBAIGRlZiBjYWxjdWxhdGVfY2hhaW5fYmFzZWRfcGxkZHQoCiAgICAgICAgIGF0b21fbWFzayA9IHRva2VuX21hc2tbYXRvbV90b190b2tlbl9pZHhdCiAgICAgICAgIHN1Yl9wbGRkdCA9IGF0b21fcGxkZHRbOiwgYXRvbV9tYXNrXQogICAgICAgICAjIE1QUyBoYXMgbm8gZmxvYXQ2NDsgZmFsbCBiYWNrIHRvIGZsb2F0MzIgYWNjdW11bGF0aW9uIHRoZXJlLgotICAgICAgICBhY2N1bV9kdHlwZSA9IHRvcmNoLmZsb2F0MzIgaWYgc3ViX3BsZGR0LmRldmljZS50eXBlID09ICJtcHMiIGVsc2UgdG9yY2guZmxvYXQ2NAorICAgICAgICAjIEFzY2VuZCBOUFUgbGlrZXdpc2UgaGFzIG5vIG5hdGl2ZSBmbG9hdDY0IHN1cHBvcnQuCisgICAgICAgIGFjY3VtX2R0eXBlID0gKAorICAgICAgICAgICAgdG9yY2guZmxvYXQzMgorICAgICAgICAgICAgaWYgc3ViX3BsZGR0LmRldmljZS50eXBlIGluICgibXBzIiwgIm5wdSIpCisgICAgICAgICAgICBlbHNlIHRvcmNoLmZsb2F0NjQKKyAgICAgICAgKQogICAgICAgICByZXR1cm4gc3ViX3BsZGR0LnRvKGFjY3VtX2R0eXBlKS5tZWFuKC0xKS50byhhdG9tX3BsZGR0LmR0eXBlKQogCiAgICAgYmF0Y2hfc2hhcGUgPSBhdG9tX3BsZGR0LnNoYXBlWzotMV0KZGlmZiAtLWdpdCBhL29wZW5kZGUvdXRpbHMvZW52aXJvbm1lbnQucHkgYi9vcGVuZGRlL3V0aWxzL2Vudmlyb25tZW50LnB5CmluZGV4IDg3Njc5MzEuLmY2YzdmOTggMTAwNjQ0Ci0tLSBhL29wZW5kZGUvdXRpbHMvZW52aXJvbm1lbnQucHkKKysrIGIvb3BlbmRkZS91dGlscy9lbnZpcm9ubWVudC5weQpAQCAtMjI0LDExICsyMjQsMTYgQEAgZGVmIHNlbGVjdF90b3JjaF9kZXZpY2UoCiAKICAgICBjdWRhX2F2YWlsYWJsZSA9IHRvcmNoLmN1ZGEuaXNfYXZhaWxhYmxlKCkKICAgICBfLCBtcHNfYXZhaWxhYmxlID0gX3Byb2JlX21wcyh0b3JjaCkKKyAgICBucHVfYXZhaWxhYmxlID0gKAorICAgICAgICBib29sKHRvcmNoLm5wdS5pc19hdmFpbGFibGUoKSkgaWYgaGFzYXR0cih0b3JjaCwgIm5wdSIpIGVsc2UgRmFsc2UKKyAgICApCiAgICAgaWYgcmVxdWVzdGVkX2RldmljZSA9PSAiYXV0byI6CiAgICAgICAgIGlmIGN1ZGFfYXZhaWxhYmxlOgogICAgICAgICAgICAgcmVxdWVzdGVkX2RldmljZSA9ICJjdWRhIgogICAgICAgICBlbGlmIG1wc19hdmFpbGFibGU6CiAgICAgICAgICAgICByZXF1ZXN0ZWRfZGV2aWNlID0gIm1wcyIKKyAgICAgICAgZWxpZiBucHVfYXZhaWxhYmxlOgorICAgICAgICAgICAgcmVxdWVzdGVkX2RldmljZSA9ICJucHUiCiAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICByZXF1ZXN0ZWRfZGV2aWNlID0gImNwdSIKIApAQCAtMjU2LDYgKzI2MSwyMSBAQCBkZWYgc2VsZWN0X3RvcmNoX2RldmljZSgKICAgICAgICAgICAgICkKICAgICAgICAgcmV0dXJuIHRvcmNoLmRldmljZShmImN1ZGE6e2xvY2FsX3Jhbmt9IikKIAorICAgIGlmIHJlcXVlc3RlZF9kZXZpY2UgPT0gIm5wdSI6CisgICAgICAgIGlmIG5vdCBucHVfYXZhaWxhYmxlOgorICAgICAgICAgICAgcmFpc2UgUnVudGltZUVycm9yKAorICAgICAgICAgICAgICAgICJOUFUgd2FzIHJlcXVlc3RlZCwgYnV0IHRvcmNoLm5wdS5pc19hdmFpbGFibGUoKSBpcyBmYWxzZS4gIgorICAgICAgICAgICAgICAgICJJbnN0YWxsIHRvcmNoX25wdSwgc291cmNlIHRoZSBDQU5OIHNldF9lbnYuc2ggc2NyaXB0LCBvciB1c2UgIgorICAgICAgICAgICAgICAgICInLS1kZXZpY2UgY3B1Jy4iCisgICAgICAgICAgICApCisgICAgICAgIGRldmljZV9jb3VudCA9IHRvcmNoLm5wdS5kZXZpY2VfY291bnQoKQorICAgICAgICBpZiBsb2NhbF9yYW5rIDwgMCBvciBsb2NhbF9yYW5rID49IGRldmljZV9jb3VudDoKKyAgICAgICAgICAgIHJhaXNlIFJ1bnRpbWVFcnJvcigKKyAgICAgICAgICAgICAgICBmIk5QVSBkZXZpY2UgaW5kZXgge2xvY2FsX3Jhbmt9IGlzIHVuYXZhaWxhYmxlOyBkZXRlY3RlZCAiCisgICAgICAgICAgICAgICAgZiJ7ZGV2aWNlX2NvdW50fSBOUFUgZGV2aWNlKHMpLiIKKyAgICAgICAgICAgICkKKyAgICAgICAgcmV0dXJuIHRvcmNoLmRldmljZShmIm5wdTp7bG9jYWxfcmFua30iKQorCiAgICAgcmV0dXJuIHRvcmNoLmRldmljZSgiY3B1IikKIAogCmRpZmYgLS1naXQgYS9vcGVuZGRlL3V0aWxzL3RvcmNoX3V0aWxzLnB5IGIvb3BlbmRkZS91dGlscy90b3JjaF91dGlscy5weQppbmRleCBkOTE4YzZmLi42Y2ZhNzE3IDEwMDY0NAotLS0gYS9vcGVuZGRlL3V0aWxzL3RvcmNoX3V0aWxzLnB5CisrKyBiL29wZW5kZGUvdXRpbHMvdG9yY2hfdXRpbHMucHkKQEAgLTE3LDE4ICsxNywzMiBAQCBkZWYgX21wc19hdXRvY2FzdF9hdmFpbGFibGUoKSAtPiBib29sOgogICAgICAgICByZXR1cm4gRmFsc2UKIAogCitkZWYgX25wdV9hdXRvY2FzdF9hdmFpbGFibGUoKSAtPiBib29sOgorICAgICIiIldoZXRoZXIgYW4gTlBVIChBc2NlbmQgdG9yY2hfbnB1KSBhdXRvY2FzdCBzdGF0ZSBleGlzdHMgdG8gY2xlYXIuIiIiCisgICAgdHJ5OgorICAgICAgICByZXR1cm4gYm9vbChoYXNhdHRyKHRvcmNoLCAibnB1IikgYW5kIHRvcmNoLm5wdS5pc19hdmFpbGFibGUoKSkKKyAgICBleGNlcHQgRXhjZXB0aW9uOgorICAgICAgICByZXR1cm4gRmFsc2UKKworCiBAY29udGV4dG1hbmFnZXIKIGRlZiBkaXNhYmxlZF9hdXRvY2FzdCgpOgogICAgICIiIkZvcmNlIEZQMzIgaW5zaWRlIHRoZSBibG9jayBvbiBldmVyeSBhY2NlbGVyYXRvciB0aGF0IGNhbiBhdXRvY2FzdC4KIAogICAgIFJlZ2lvbnMgdGhhdCBtdXN0IHN0YXkgRlAzMiBoaXN0b3JpY2FsbHkgZ3VhcmRlZCBvbmx5IENVREEgYXV0b2Nhc3QuIFRoZQogICAgIEFwcGxlIE1QUyBiYWNrZW5kIGtlZXBzIGEgc2VwYXJhdGUgYXV0b2Nhc3Qgc3RhdGUsIHNvIGEgQ1VEQS1vbmx5IGd1YXJkCi0gICAgc2lsZW50bHkgbGV0cyBCRjE2IHRocm91Z2ggdGhlcmUuCisgICAgc2lsZW50bHkgbGV0cyBCRjE2IHRocm91Z2ggdGhlcmUuICBBc2NlbmQgTlBVICh0b3JjaF9ucHUpIGxpa2V3aXNlIGtlZXBzIGEKKyAgICBzZXBhcmF0ZSBhdXRvY2FzdCBzdGF0ZS4KICAgICAiIiIKICAgICB3aXRoIEV4aXRTdGFjaygpIGFzIHN0YWNrOgogICAgICAgICBzdGFjay5lbnRlcl9jb250ZXh0KHRvcmNoLmFtcC5hdXRvY2FzdCgiY3VkYSIsIGVuYWJsZWQ9RmFsc2UpKQogICAgICAgICBpZiBfbXBzX2F1dG9jYXN0X2F2YWlsYWJsZSgpOgogICAgICAgICAgICAgc3RhY2suZW50ZXJfY29udGV4dCh0b3JjaC5hbXAuYXV0b2Nhc3QoIm1wcyIsIGVuYWJsZWQ9RmFsc2UpKQorICAgICAgICBpZiBfbnB1X2F1dG9jYXN0X2F2YWlsYWJsZSgpOgorICAgICAgICAgICAgdHJ5OgorICAgICAgICAgICAgICAgIHN0YWNrLmVudGVyX2NvbnRleHQodG9yY2guYW1wLmF1dG9jYXN0KCJucHUiLCBlbmFibGVkPUZhbHNlKSkKKyAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CisgICAgICAgICAgICAgICAgcGFzcwogICAgICAgICB5aWVsZAogCiAKZGlmZiAtLWdpdCBhL3J1bm5lci9pbmZlcmVuY2UucHkgYi9ydW5uZXIvaW5mZXJlbmNlLnB5CmluZGV4IDhhMTYyOTEuLjc1MGRhMmQgMTAwNjQ0Ci0tLSBhL3J1bm5lci9pbmZlcmVuY2UucHkKKysrIGIvcnVubmVyL2luZmVyZW5jZS5weQpAQCAtMTYsNiArMTYsMTUgQEAgZnJvbSBvcy5wYXRoIGltcG9ydCBleGlzdHMgYXMgb3BleGlzdHMKIGZyb20gb3MucGF0aCBpbXBvcnQgam9pbiBhcyBvcGpvaW4KIGZyb20gdHlwaW5nIGltcG9ydCBBbnksIGNhc3QKIAorIyBJbXBvcnQgUkRLaXQgYmVmb3JlIHRvcmNoLiBPbiBBc2NlbmQgTlBVIGhvc3RzIGBpbXBvcnQgdG9yY2hgIGF1dG8tbG9hZHMKKyMgdG9yY2hfbnB1IChDQU5OIGVudiksIGFuZCByZGtpdCdzIGJ1bmRsZWQgbGliUkRLaXQqLnNvIGNyYXNoIHdpdGggU0lHU0VHViBpbgorIyB0aGVpciBzdGF0aWMgaW5pdGlhbGl6ZXJzIHdoZW4gZGxvcGVuZWQgYWZ0ZXIgdG9yY2hfbnB1IChvYnNlcnZlZCBvbiA5MTBDLAorIyBkZXRlcm1pbmlzdGljKS4gUHJlbG9hZGluZyByZGtpdCBmaXJzdCBpcyBhIG5vLW9wIG9uIG90aGVyIHBsYXRmb3Jtcy4KK3RyeTogICMgc29mdCBmYWlsdXJlOiByZGtpdCBsb2FkcyBhZ2FpbiBsYXRlciB2aWEgb3BlbmRkZS5kYXRhLmNvcmUuY2NkIGFueXdheQorICAgIGZyb20gcmRraXQuQ2hlbSBpbXBvcnQgQWxsQ2hlbSBhcyBfYWxsY2hlbV9wcmVsb2FkICAjIG5vcWE6IEY0MDEKK2V4Y2VwdCBFeGNlcHRpb246CisgICAgcGFzcworCiBpbXBvcnQgdG9yY2gKIGltcG9ydCB0b3JjaC5kaXN0cmlidXRlZCBhcyBkaXN0CiAKQEAgLTEzODgsNyArMTM5NywxMCBAQCBjbGFzcyBJbmZlcmVuY2VSdW5uZXIob2JqZWN0KToKICAgICAgICAgICAgICJiZjE2IjogdG9yY2guYmZsb2F0MTYsCiAgICAgICAgIH1bc2VsZi5jb25maWdzLmR0eXBlXQogCi0gICAgICAgIGlmIGV2YWxfcHJlY2lzaW9uID09IHRvcmNoLmZsb2F0MzIgb3Igc2VsZi5kZXZpY2UudHlwZSBub3QgaW4geyJjdWRhIiwgIm1wcyJ9OgorICAgICAgICBpZiAoCisgICAgICAgICAgICBldmFsX3ByZWNpc2lvbiA9PSB0b3JjaC5mbG9hdDMyCisgICAgICAgICAgICBvciBzZWxmLmRldmljZS50eXBlIG5vdCBpbiB7ImN1ZGEiLCAibXBzIiwgIm5wdSJ9CisgICAgICAgICk6CiAgICAgICAgICAgICBlbmFibGVfYW1wID0gbnVsbGNvbnRleHQoKQogICAgICAgICBlbHNlOgogICAgICAgICAgICAgZW5hYmxlX2FtcCA9IHRvcmNoLmF1dG9jYXN0KAo="
    ),
    "OpenDDE-Harness": (
        "ZGlmZiAtLWdpdCBhL29wZW5kZGVfaGFybmVzcy9jbGkvY29tcHV0ZV9lbnZpcm9ubWVudC5weSBiL29wZW5kZGVfaGFybmVzcy9jbGkvY29tcHV0ZV9lbnZpcm9ubWVudC5weQppbmRleCA0ZGRkZTY2Li5hYzhkY2ZhIDEwMDY0NAotLS0gYS9vcGVuZGRlX2hhcm5lc3MvY2xpL2NvbXB1dGVfZW52aXJvbm1lbnQucHkKKysrIGIvb3BlbmRkZV9oYXJuZXNzL2NsaS9jb21wdXRlX2Vudmlyb25tZW50LnB5CkBAIC0zMywyNiArMzMsNDcgQEAgZGVmIGNvbXB1dGVfaW1hZ2UoKSAtPiBzdHI6CiAKIAogZGVmIGNoZWNrX2xvY2FsX3BsYXRmb3JtKCkgLT4gTm9uZToKLSAgICBpZiBwbGF0Zm9ybS5zeXN0ZW0oKSAhPSAiTGludXgiIG9yIHBsYXRmb3JtLm1hY2hpbmUoKS5sb3dlcigpICE9ICJ4ODZfNjQiOgotICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJMb2NhbCBjb21wdXRlIGlzIHN1cHBvcnRlZCBvbiBMaW51eCB4ODYtNjQgb25seS4gVXNlIGEgY29uZmlndXJlZCBMaW51eCBjb21wdXRlIHNlcnZpY2UuIikKKyAgICBpZiBwbGF0Zm9ybS5zeXN0ZW0oKSAhPSAiTGludXgiIG9yIHBsYXRmb3JtLm1hY2hpbmUoKS5sb3dlcigpIG5vdCBpbiAoIng4Nl82NCIsICJhYXJjaDY0Iik6CisgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkxvY2FsIGNvbXB1dGUgaXMgc3VwcG9ydGVkIG9uIExpbnV4IHg4Ni02NC9hYXJjaDY0IG9ubHkuIFVzZSBhIGNvbmZpZ3VyZWQgTGludXggY29tcHV0ZSBzZXJ2aWNlLiIpCiAKIAogZGVmIHJlc29sdmVfZGV2aWNlKHJlcXVlc3RlZDogc3RyIHwgTm9uZSA9IE5vbmUpIC0+IHN0cjoKICAgICBkZXZpY2UgPSBzdHIocmVxdWVzdGVkIG9yIG9zLmVudmlyb24uZ2V0KCJPUEVORERFX0hBUk5FU1NfQ09NUFVURV9ERVZJQ0UiLCAiYXV0byIpKS5zdHJpcCgpLmxvd2VyKCkKICAgICBpZiBkZXZpY2UgPT0gImNwdSI6CiAgICAgICAgIHJldHVybiBkZXZpY2UKLSAgICBpZiBkZXZpY2UgIT0gImF1dG8iIGFuZCBub3QgcmUuZnVsbG1hdGNoKHIiY3VkYSg/OjpcZCspPyIsIGRldmljZSk6Ci0gICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkNvbXB1dGUgZGV2aWNlIG11c3QgYmUgYXV0bywgY3B1LCBjdWRhLCBvciBjdWRhOk47IHRoaXMgcnVudGltZSBzdXBwb3J0cyBMaW51eCBDUFUvQ1VEQS4iKQorICAgIGlmIGRldmljZSAhPSAiYXV0byIgYW5kIG5vdCByZS5mdWxsbWF0Y2gociIoPzpjdWRhfG5wdSkoPzo6XGQrKT8iLCBkZXZpY2UpOgorICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJDb21wdXRlIGRldmljZSBtdXN0IGJlIGF1dG8sIGNwdSwgY3VkYSwgY3VkYTpOLCBucHUsIG9yIG5wdTpOOyBBc2NlbmQgTlBVIGlzIHN1cHBvcnRlZC4iKQogICAgIGltcG9ydCB0b3JjaAogCisgICAgZGVmIF9ucHVfY291bnQoKSAtPiBpbnQ6CisgICAgICAgIHRyeToKKyAgICAgICAgICAgIGlmIGhhc2F0dHIodG9yY2gsICJucHUiKSBhbmQgdG9yY2gubnB1LmlzX2F2YWlsYWJsZSgpOgorICAgICAgICAgICAgICAgIHJldHVybiBpbnQodG9yY2gubnB1LmRldmljZV9jb3VudCgpKQorICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgorICAgICAgICAgICAgcGFzcworICAgICAgICByZXR1cm4gMAorCiAgICAgaWYgZGV2aWNlID09ICJhdXRvIjoKLSAgICAgICAgcmV0dXJuICJjdWRhIiBpZiB0b3JjaC5jdWRhLmlzX2F2YWlsYWJsZSgpIGVsc2UgImNwdSIKLSAgICBpZiBub3QgdG9yY2guY3VkYS5pc19hdmFpbGFibGUoKToKKyAgICAgICAgaWYgdG9yY2guY3VkYS5pc19hdmFpbGFibGUoKToKKyAgICAgICAgICAgIHJldHVybiAiY3VkYSIKKyAgICAgICAgaWYgX25wdV9jb3VudCgpID4gMDoKKyAgICAgICAgICAgIHJldHVybiAibnB1IgorICAgICAgICByZXR1cm4gImNwdSIKKyAgICBpZiBkZXZpY2Uuc3RhcnRzd2l0aCgiY3VkYSIpOgorICAgICAgICBpZiBub3QgdG9yY2guY3VkYS5pc19hdmFpbGFibGUoKToKKyAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoCisgICAgICAgICAgICAgICAgIkNVREEgd2FzIHJlcXVlc3RlZCBidXQgaXMgdW5hdmFpbGFibGUgaW4gdGhlIGNvbXB1dGUgZW52aXJvbm1lbnQuIFNlbGVjdCBDUFUgb3IgZml4IEdQVSBhY2Nlc3MuIgorICAgICAgICAgICAgKQorICAgICAgICBpZiAiOiIgaW4gZGV2aWNlIGFuZCBpbnQoZGV2aWNlLnNwbGl0KCI6IiwgMSlbMV0pID49IHRvcmNoLmN1ZGEuZGV2aWNlX2NvdW50KCk6CisgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKGYiQ1VEQSBkZXZpY2UgaXMgdW5hdmFpbGFibGU6IHtkZXZpY2V9IikKKyAgICAgICAgcmV0dXJuIGRldmljZQorICAgIG5wdV9jb3VudCA9IF9ucHVfY291bnQoKQorICAgIGlmIG5wdV9jb3VudCA9PSAwOgogICAgICAgICByYWlzZSBWYWx1ZUVycm9yKAotICAgICAgICAgICAgIkNVREEgd2FzIHJlcXVlc3RlZCBidXQgaXMgdW5hdmFpbGFibGUgaW4gdGhlIGNvbXB1dGUgZW52aXJvbm1lbnQuIFNlbGVjdCBDUFUgb3IgZml4IEdQVSBhY2Nlc3MuIgorICAgICAgICAgICAgIk5QVSB3YXMgcmVxdWVzdGVkIGJ1dCB0b3JjaC5ucHUgcmVwb3J0cyBubyBkZXZpY2VzLiBTb3VyY2UgdGhlIENBTk4gc2V0X2Vudi5zaCBzY3JpcHQgb3Igc2VsZWN0IENQVS4iCiAgICAgICAgICkKLSAgICBpZiAiOiIgaW4gZGV2aWNlIGFuZCBpbnQoZGV2aWNlLnNwbGl0KCI6IiwgMSlbMV0pID49IHRvcmNoLmN1ZGEuZGV2aWNlX2NvdW50KCk6Ci0gICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoZiJDVURBIGRldmljZSBpcyB1bmF2YWlsYWJsZToge2RldmljZX0iKQorICAgIGlmICI6IiBpbiBkZXZpY2UgYW5kIGludChkZXZpY2Uuc3BsaXQoIjoiLCAxKVsxXSkgPj0gbnB1X2NvdW50OgorICAgICAgICByYWlzZSBWYWx1ZUVycm9yKGYiTlBVIGRldmljZSBpcyB1bmF2YWlsYWJsZToge2RldmljZX0iKQogICAgIHJldHVybiBkZXZpY2UKIAogCmRpZmYgLS1naXQgYS9vcGVuZGRlX2hhcm5lc3MvY29uZmlnL3VwZGF0ZV9wcm92aWRlcnMucHkgYi9vcGVuZGRlX2hhcm5lc3MvY29uZmlnL3VwZGF0ZV9wcm92aWRlcnMucHkKaW5kZXggMWNjZmM0Mi4uN2VjYjAzYyAxMDA2NDQKLS0tIGEvb3BlbmRkZV9oYXJuZXNzL2NvbmZpZy91cGRhdGVfcHJvdmlkZXJzLnB5CisrKyBiL29wZW5kZGVfaGFybmVzcy9jb25maWcvdXBkYXRlX3Byb3ZpZGVycy5weQpAQCAtNjkwLDYgKzY5MCwxNyBAQCBhc3luYyBkZWYgX2NoZWNrX3Rocm91Z2hfdGhlX3NlcnZpY2UocHJvdmlkZXI6IHN0ciwgY29uZmlnOiBBbnksIHN0YXJ0OiBmbG9hdCwgdAogCiAgICAgY29udGV4dCA9IHsibWVzc2FnZXMiOiBbeyJyb2xlIjogInVzZXIiLCAiY29udGVudCI6IF9URVNUX1BST01QVCwgInRpbWVzdGFtcCI6IDB9XX0KICAgICBvcHRpb25zID0geyJtYXhUb2tlbnMiOiBfVEVTVF9NQVhfVE9LRU5TfQorICAgICMgQSBtb2RlbCB0aGF0IGFsd2F5cyB0aGlua3MgKGdsbS01LjMgYmVoaW5kIHRoZSB2b2xjZSBnYXRld2F5IHJlZnVzZXMgYQorICAgICMgcmVxdWVzdCB3aXRoIHRoaW5raW5nIG9mZikgbmVlZHMgdGhlIHByb2JlIHRvIGNhcnJ5IHRoZSByb3cncyBkZWNsYXJlZAorICAgICMgbGV2ZWwgdGhlIHdheSBhIHR1cm4ncyBlZmZvcnRfZm9yIHdvdWxkLCBhbmQgaGVhZHJvb20gZm9yIHRoZSB0aGlua2luZworICAgICMgYnVkZ2V0IHRvIGZpdCB1bmRlciBtYXhUb2tlbnMuCisgICAgZnJvbSBvcGVuZGRlX2hhcm5lc3MucHJvdmlkZXJzIGltcG9ydCBtb2RlbF9pZAorCisgICAgcm93ID0gbW9kZWxfaWQucm93X2Zvcihjb25maWcucHJvdmlkZXJzLCBtb2RlbF9pZC5qb2luKHByb3ZpZGVyLCBtb2RlbCkpCisgICAgZWZmb3J0ID0gZ2V0YXR0cihyb3csICJyZWFzb25pbmdfZWZmb3J0IiwgTm9uZSkKKyAgICBpZiBlZmZvcnQgaW4geyJtaW5pbWFsIiwgImxvdyIsICJtZWRpdW0iLCAiaGlnaCIsICJ4aGlnaCIsICJtYXgifToKKyAgICAgICAgb3B0aW9uc1sicmVhc29uaW5nIl0gPSBlZmZvcnQKKyAgICAgICAgb3B0aW9uc1sibWF4VG9rZW5zIl0gPSBtYXgoX1RFU1RfTUFYX1RPS0VOUywgNTEyKQogICAgIGV2ZW50cyA9IHNlcnZpY2Uuc3RyZWFtKHByb3ZpZGVyLCBtb2RlbCwgY29udGV4dCwgb3B0aW9ucykKICAgICB0cnk6CiAgICAgICAgIGFzeW5jIHdpdGggYXN5bmNpby50aW1lb3V0KHRpbWVvdXRfcyk6CmRpZmYgLS1naXQgYS9vcGVuZGRlX2hhcm5lc3MvcGx1Z2luL3Byb3RlaW5fZGVzaWduL3NlcnZlcnMvYXBpLnB5IGIvb3BlbmRkZV9oYXJuZXNzL3BsdWdpbi9wcm90ZWluX2Rlc2lnbi9zZXJ2ZXJzL2FwaS5weQppbmRleCA5YTIxNmIyLi4zZWY1MjVlIDEwMDY0NAotLS0gYS9vcGVuZGRlX2hhcm5lc3MvcGx1Z2luL3Byb3RlaW5fZGVzaWduL3NlcnZlcnMvYXBpLnB5CisrKyBiL29wZW5kZGVfaGFybmVzcy9wbHVnaW4vcHJvdGVpbl9kZXNpZ24vc2VydmVycy9hcGkucHkKQEAgLTczLDkgKzczLDEzIEBAIGRlZiB2aXNpYmxlX2N1ZGFfZGV2aWNlcygpIC0+IGxpc3RbaW50XToKICAgICBleGNlcHQgSW1wb3J0RXJyb3I6CiAgICAgICAgIHJldHVybiBbXQogICAgIHRyeToKLSAgICAgICAgaWYgbm90IHRvcmNoLmN1ZGEuaXNfYXZhaWxhYmxlKCk6Ci0gICAgICAgICAgICByZXR1cm4gW10KLSAgICAgICAgcmV0dXJuIGxpc3QocmFuZ2UodG9yY2guY3VkYS5kZXZpY2VfY291bnQoKSkpCisgICAgICAgIGlmIHRvcmNoLmN1ZGEuaXNfYXZhaWxhYmxlKCk6CisgICAgICAgICAgICByZXR1cm4gbGlzdChyYW5nZSh0b3JjaC5jdWRhLmRldmljZV9jb3VudCgpKSkKKyAgICAgICAgIyBBc2NlbmQgTlBVIGhvc3Q6IGV4cG9zZSBkaWUgMCBmb3IgdGhlIGxlYXNlIHRhYmxlOyBkZXZpY2Ugc3RyaW5ncworICAgICAgICAjIGJlY29tZSBucHU6TiBpbiB3aXRoX2xlYXNlZF9kZXZpY2VzIG9uIHRoaXMgcnVudGltZS4KKyAgICAgICAgaWYgaGFzYXR0cih0b3JjaCwgIm5wdSIpIGFuZCB0b3JjaC5ucHUuaXNfYXZhaWxhYmxlKCk6CisgICAgICAgICAgICByZXR1cm4gWzBdCisgICAgICAgIHJldHVybiBbXQogICAgIGV4Y2VwdCAoQXNzZXJ0aW9uRXJyb3IsIFJ1bnRpbWVFcnJvcik6CiAgICAgICAgIHJldHVybiBbXQogCkBAIC05MCw3ICs5NCwxMiBAQCBkZWYgd2l0aF9sZWFzZWRfZGV2aWNlcyhvcGVyYXRpb246IHN0ciwgcGF5bG9hZDogZGljdFtzdHIsIEFueV0sIGRldmljZXM6IFNlcXVlbgogICAgIGlmIG5vdCBkZXZpY2VzOgogICAgICAgICByZXR1cm4gcGF5bG9hZAogICAgIHBheWxvYWQgPSBkaWN0KHBheWxvYWQpCi0gICAgcHJpbWFyeSA9IGYiY3VkYTp7ZGV2aWNlc1swXX0iCisgICAgdHJ5OgorICAgICAgICBpbXBvcnQgdG9yY2gKKyAgICAgICAgX25wdSA9IGhhc2F0dHIodG9yY2gsICJucHUiKSBhbmQgdG9yY2gubnB1LmlzX2F2YWlsYWJsZSgpCisgICAgZXhjZXB0IEV4Y2VwdGlvbjoKKyAgICAgICAgX25wdSA9IEZhbHNlCisgICAgcHJpbWFyeSA9IGYibnB1OntkZXZpY2VzWzBdfSIgaWYgX25wdSBlbHNlIGYiY3VkYTp7ZGV2aWNlc1swXX0iCiAgICAgaWYgb3BlcmF0aW9uID09ICJmb2xkIjoKICAgICAgICAgb3B0aW9ucyA9IGRpY3QocGF5bG9hZC5nZXQoIm9wdGlvbnMiKSBvciB7fSkKICAgICAgICAgb3B0aW9uc1siZ3B1cyJdID0gIiwiLmpvaW4oc3RyKGluZGV4KSBmb3IgaW5kZXggaW4gZGV2aWNlcykKZGlmZiAtLWdpdCBhL29wZW5kZGVfaGFybmVzcy9wbHVnaW4vcHJvdGVpbl9kZXNpZ24vc2VydmVycy9iYWNrZW5kcy9zb2x1YmxlX21wbm4ucHkgYi9vcGVuZGRlX2hhcm5lc3MvcGx1Z2luL3Byb3RlaW5fZGVzaWduL3NlcnZlcnMvYmFja2VuZHMvc29sdWJsZV9tcG5uLnB5CmluZGV4IDU3ZWQ4OWYuLmE1YTJlOTMgMTAwNjQ0Ci0tLSBhL29wZW5kZGVfaGFybmVzcy9wbHVnaW4vcHJvdGVpbl9kZXNpZ24vc2VydmVycy9iYWNrZW5kcy9zb2x1YmxlX21wbm4ucHkKKysrIGIvb3BlbmRkZV9oYXJuZXNzL3BsdWdpbi9wcm90ZWluX2Rlc2lnbi9zZXJ2ZXJzL2JhY2tlbmRzL3NvbHVibGVfbXBubi5weQpAQCAtMjQxLDE0ICsyNDEsMjQgQEAgY2xhc3MgU29sdWJsZU1QTk5DbGllbnQ6CiAgICAgICAgICAgICBpZiBzZWxmLmRldmljZS50eXBlID09ICJjdWRhIgogICAgICAgICAgICAgZWxzZSBbXQogICAgICAgICApCisgICAgICAgIG5wdV9kZXZpY2VzID0gKAorICAgICAgICAgICAgW3NlbGYuZGV2aWNlLmluZGV4IGlmIHNlbGYuZGV2aWNlLmluZGV4IGlzIG5vdCBOb25lIGVsc2UgaW50KHRvcmNoLm5wdS5jdXJyZW50X2RldmljZSgpKV0KKyAgICAgICAgICAgIGlmIHNlbGYuZGV2aWNlLnR5cGUgPT0gIm5wdSIgYW5kIGhhc2F0dHIodG9yY2gsICJucHUiKQorICAgICAgICAgICAgZWxzZSBbXQorICAgICAgICApCiAgICAgICAgICMgU2FtcGxpbmcgYW5kIGJhY2tib25lIG5vaXNlIHVzZSB1cHN0cmVhbSBnbG9iYWwgUk5HczsgaXNvbGF0ZSBjb25jdXJyZW50IHJlcXVlc3RzLgotICAgICAgICB3aXRoIF9TQU1QTElOR19MT0NLLCB0b3JjaC5yYW5kb20uZm9ya19ybmcoZGV2aWNlcz1jdWRhX2RldmljZXMpLCB0b3JjaC5pbmZlcmVuY2VfbW9kZSgpOgorICAgICAgICB3aXRoIF9TQU1QTElOR19MT0NLLCB0b3JjaC5yYW5kb20uZm9ya19ybmcoZGV2aWNlcz1jdWRhX2RldmljZXMgKyBucHVfZGV2aWNlcyksIHRvcmNoLmluZmVyZW5jZV9tb2RlKCk6CiAgICAgICAgICAgICBzYW1wbGluZ19zZWVkID0gc2VlZCBpZiBzZWVkIGlzIG5vdCBOb25lIGVsc2Ugc2VsZi5zZWVkCiAgICAgICAgICAgICBpZiBzYW1wbGluZ19zZWVkIGlzIE5vbmU6CiAgICAgICAgICAgICAgICAgc2FtcGxpbmdfc2VlZCA9IHNlY3JldHMucmFuZGJpdHMoMzIpCiAgICAgICAgICAgICB0b3JjaC5yYW5kb20uZGVmYXVsdF9nZW5lcmF0b3IubWFudWFsX3NlZWQoc2FtcGxpbmdfc2VlZCkKICAgICAgICAgICAgIGZvciBkZXZpY2VfaW5kZXggaW4gY3VkYV9kZXZpY2VzOgogICAgICAgICAgICAgICAgIHRvcmNoLmN1ZGEuZGVmYXVsdF9nZW5lcmF0b3JzW2RldmljZV9pbmRleF0ubWFudWFsX3NlZWQoc2FtcGxpbmdfc2VlZCkKKyAgICAgICAgICAgIGlmIG5wdV9kZXZpY2VzOgorICAgICAgICAgICAgICAgIHRyeToKKyAgICAgICAgICAgICAgICAgICAgdG9yY2gubnB1Lm1hbnVhbF9zZWVkKHNhbXBsaW5nX3NlZWQpCisgICAgICAgICAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKKyAgICAgICAgICAgICAgICAgICAgcGFzcwogICAgICAgICAgICAgZmVhdHVyZXNbInJhbmRuIl0gPSB0b3JjaC5yYW5kbigobnVtX3NlcXVlbmNlcywgbGVuZ3RoKSwgZGV2aWNlPXNlbGYuZGV2aWNlKQogICAgICAgICAgICAgcmVzdWx0ID0gc2VsZi5tb2RlbC5zYW1wbGUoZmVhdHVyZXMpCiAgICAgICAgICAgICBtYXNrID0gZmVhdHVyZXNbIm1hc2siXSAqIGZlYXR1cmVzWyJjaGFpbl9tYXNrIl0KZGlmZiAtLWdpdCBhL3B5cHJvamVjdC50b21sIGIvcHlwcm9qZWN0LnRvbWwKaW5kZXggNjJiMzJkNC4uODQ0Yzc3NiAxMDA2NDQKLS0tIGEvcHlwcm9qZWN0LnRvbWwKKysrIGIvcHlwcm9qZWN0LnRvbWwKQEAgLTMsNyArMyw3IEBAIG5hbWUgPSAib3BlbmRkZS1oYXJuZXNzIgogdmVyc2lvbiA9ICIwLjAuNSIKIHJlYWRtZSA9ICJSRUFETUUubWQiCiBkZXNjcmlwdGlvbiA9ICJPcGVuRERFIEhhcm5lc3MgLSBhbnRpYm9keSBkZXNpZ24gaGFybmVzczogYWdlbnQgcnVudGltZSwgcHJvdGVpbi1kZXNpZ24gcGx1Z2luLCBhbmQgY29tcHV0ZSBzZXJ2aWNlIgotcmVxdWlyZXMtcHl0aG9uID0gIj49My4xMiIKK3JlcXVpcmVzLXB5dGhvbiA9ICI+PTMuMTEiCiBsaWNlbnNlID0gICJBcGFjaGUtMi4wIgogbGljZW5zZS1maWxlcyA9IFsiTElDRU5TRSIsICJMSUNFTlNFUy8qLnR4dCJdCiBhdXRob3JzID0gWwo="
    ),
    "LigandMPNN": (
        "ZGlmZiAtLWdpdCBhL29wZW5mb2xkL2RhdGEvdGVtcGxhdGVzLnB5IGIvb3BlbmZvbGQvZGF0YS90ZW1wbGF0ZXMucHkKaW5kZXggMjYwZjM2ZS4uZmRiYTE5YSAxMDA2NDQKLS0tIGEvb3BlbmZvbGQvZGF0YS90ZW1wbGF0ZXMucHkKKysrIGIvb3BlbmZvbGQvZGF0YS90ZW1wbGF0ZXMucHkKQEAgLTg1LDggKzg1LDggQEAgVEVNUExBVEVfRkVBVFVSRVMgPSB7CiAgICAgInRlbXBsYXRlX2FhdHlwZSI6IG5wLmludDY0LAogICAgICJ0ZW1wbGF0ZV9hbGxfYXRvbV9tYXNrIjogbnAuZmxvYXQzMiwKICAgICAidGVtcGxhdGVfYWxsX2F0b21fcG9zaXRpb25zIjogbnAuZmxvYXQzMiwKLSAgICAidGVtcGxhdGVfZG9tYWluX25hbWVzIjogbnAub2JqZWN0LAotICAgICJ0ZW1wbGF0ZV9zZXF1ZW5jZSI6IG5wLm9iamVjdCwKKyAgICAidGVtcGxhdGVfZG9tYWluX25hbWVzIjogb2JqZWN0LAorICAgICJ0ZW1wbGF0ZV9zZXF1ZW5jZSI6IG9iamVjdCwKICAgICAidGVtcGxhdGVfc3VtX3Byb2JzIjogbnAuZmxvYXQzMiwKIH0KIApkaWZmIC0tZ2l0IGEvb3BlbmZvbGQvbnAvcmVsYXgvdXRpbHMucHkgYi9vcGVuZm9sZC9ucC9yZWxheC91dGlscy5weQppbmRleCBmNDUzZDE2Li4wZjFkZDg1IDEwMDY0NAotLS0gYS9vcGVuZm9sZC9ucC9yZWxheC91dGlscy5weQorKysgYi9vcGVuZm9sZC9ucC9yZWxheC91dGlscy5weQpAQCAtNzksNyArNzksNyBAQCBkZWYgYXNzZXJ0X2VxdWFsX25vbnRlcm1pbmFsX2F0b21fdHlwZXMoCiAgICAgIiIiQ2hlY2tzIHRoYXQgcHJlLSBhbmQgcG9zdC1taW5pbWl6ZWQgcHJvdGVpbnMgaGF2ZSBzYW1lIGF0b20gc2V0LiIiIgogICAgICMgSWdub3JlIGFueSB0ZXJtaW5hbCBPWFQgYXRvbXMgd2hpY2ggbWF5IGhhdmUgYmVlbiBhZGRlZCBieSBtaW5pbWl6YXRpb24uCiAgICAgb3h0ID0gcmVzaWR1ZV9jb25zdGFudHMuYXRvbV9vcmRlclsiT1hUIl0KLSAgICBub19veHRfbWFzayA9IG5wLm9uZXMoc2hhcGU9YXRvbV9tYXNrLnNoYXBlLCBkdHlwZT1ucC5ib29sKQorICAgIG5vX294dF9tYXNrID0gbnAub25lcyhzaGFwZT1hdG9tX21hc2suc2hhcGUsIGR0eXBlPWJvb2wpCiAgICAgbm9fb3h0X21hc2tbLi4uLCBveHRdID0gRmFsc2UKICAgICBucC50ZXN0aW5nLmFzc2VydF9hbG1vc3RfZXF1YWwoCiAgICAgICAgIHJlZl9hdG9tX21hc2tbbm9fb3h0X21hc2tdLCBhdG9tX21hc2tbbm9fb3h0X21hc2tdCmRpZmYgLS1naXQgYS9vcGVuZm9sZC9ucC9yZXNpZHVlX2NvbnN0YW50cy5weSBiL29wZW5mb2xkL25wL3Jlc2lkdWVfY29uc3RhbnRzLnB5CmluZGV4IDI4NTZlMDAuLmJhMWEwMmYgMTAwNjQ0Ci0tLSBhL29wZW5mb2xkL25wL3Jlc2lkdWVfY29uc3RhbnRzLnB5CisrKyBiL29wZW5mb2xkL25wL3Jlc2lkdWVfY29uc3RhbnRzLnB5CkBAIC0xMTIxLDEwICsxMTIxLDEwIEBAIGRlZiBfbWFrZV9yaWdpZF90cmFuc2Zvcm1hdGlvbl80eDQoZXgsIGV5LCB0cmFuc2xhdGlvbik6CiAjIGFuZCBhbiBhcnJheSB3aXRoIChyZXN0eXBlLCBhdG9tdHlwZSwgY29vcmQpIGZvciB0aGUgYXRvbSBwb3NpdGlvbnMKICMgYW5kIGNvbXB1dGUgYWZmaW5lIHRyYW5zZm9ybWF0aW9uIG1hdHJpY2VzICg0LDQpIGZyb20gb25lIHJpZ2lkIGdyb3VwIHRvIHRoZQogIyBwcmV2aW91cyBncm91cAotcmVzdHlwZV9hdG9tMzdfdG9fcmlnaWRfZ3JvdXAgPSBucC56ZXJvcyhbMjEsIDM3XSwgZHR5cGU9bnAuaW50KQorcmVzdHlwZV9hdG9tMzdfdG9fcmlnaWRfZ3JvdXAgPSBucC56ZXJvcyhbMjEsIDM3XSwgZHR5cGU9aW50KQogcmVzdHlwZV9hdG9tMzdfbWFzayA9IG5wLnplcm9zKFsyMSwgMzddLCBkdHlwZT1ucC5mbG9hdDMyKQogcmVzdHlwZV9hdG9tMzdfcmlnaWRfZ3JvdXBfcG9zaXRpb25zID0gbnAuemVyb3MoWzIxLCAzNywgM10sIGR0eXBlPW5wLmZsb2F0MzIpCi1yZXN0eXBlX2F0b20xNF90b19yaWdpZF9ncm91cCA9IG5wLnplcm9zKFsyMSwgMTRdLCBkdHlwZT1ucC5pbnQpCityZXN0eXBlX2F0b20xNF90b19yaWdpZF9ncm91cCA9IG5wLnplcm9zKFsyMSwgMTRdLCBkdHlwZT1pbnQpCiByZXN0eXBlX2F0b20xNF9tYXNrID0gbnAuemVyb3MoWzIxLCAxNF0sIGR0eXBlPW5wLmZsb2F0MzIpCiByZXN0eXBlX2F0b20xNF9yaWdpZF9ncm91cF9wb3NpdGlvbnMgPSBucC56ZXJvcyhbMjEsIDE0LCAzXSwgZHR5cGU9bnAuZmxvYXQzMikKIHJlc3R5cGVfcmlnaWRfZ3JvdXBfZGVmYXVsdF9mcmFtZSA9IG5wLnplcm9zKFsyMSwgOCwgNCwgNF0sIGR0eXBlPW5wLmZsb2F0MzIpCkBAIC0xMjgwLDcgKzEyODAsNyBAQCBkZWYgbWFrZV9hdG9tMTRfZGlzdHNfYm91bmRzKAogCiByZXN0eXBlX2F0b20xNF9hbWJpZ3VvdXNfYXRvbXMgPSBucC56ZXJvcygoMjEsIDE0KSwgZHR5cGU9bnAuZmxvYXQzMikKIHJlc3R5cGVfYXRvbTE0X2FtYmlndW91c19hdG9tc19zd2FwX2lkeCA9IG5wLnRpbGUoCi0gICAgbnAuYXJhbmdlKDE0LCBkdHlwZT1ucC5pbnQpLCAoMjEsIDEpCisgICAgbnAuYXJhbmdlKDE0LCBkdHlwZT1pbnQpLCAoMjEsIDEpCiApCiAKIAo="
    ),
}
# Only the NPU driver stays outside automatic install: it is system-level.
CODE_REASON = {
    "npu": "the NPU driver and CANN toolkit are system-level; they are not installed by a search",
}
INSTALLABLE = set(PIP_REQUIREMENTS) | {"esm_weights", "soluble_weights", "fold_checkpoint"} | set(CODE_SOURCES)

_PROBE_CHILD = r'''
import json, os, sys
paths = json.loads(sys.stdin.read())
checks = {}


def check(name, probe):
    try:
        checks[name] = {"ok": True, "detail": str(probe() or "")}
    except Exception as error:
        lines = str(error).strip().splitlines() or [type(error).__name__]
        checks[name] = {"ok": False, "detail": lines[-1][:300]}


def present(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"nothing at {path}")
    return path


def npu():
    import rdkit.Chem  # the fold imports rdkit before torch_npu on 910C
    import torch
    import torch_npu  # noqa: F401
    count = torch.npu.device_count()
    if count < 1:
        raise RuntimeError("torch_npu sees no NPU device")
    return f"{count} device(s)"


def rdkit_module():
    import rdkit.Chem  # noqa: F401
    return "rdkit.Chem"


def biotite_module():
    import biotite
    return biotite.__file__


def esm_module():
    import esm
    return esm.__file__


def esm_weights():
    for name in ("config.json", "model.safetensors"):
        present(os.path.join(paths["esm"], name))
    return paths["esm"]


def harness_modules():
    sys.path.insert(0, paths["harness"])
    import opendde_harness.plugin.protein_design.servers.backends.esm_backend  # noqa: F401
    import opendde_harness.plugin.protein_design.servers.backends.soluble_mpnn  # noqa: F401
    return paths["harness"]


check("npu", npu)
check("rdkit", rdkit_module)
check("biotite", biotite_module)
check("fold_code", lambda: present(paths["fold_code"]))
check("fold_checkpoint", lambda: present(paths["fold_checkpoint"]))
check("esm_module", esm_module)
check("esm_weights", esm_weights)
check("harness_modules", harness_modules)
check("soluble_weights", lambda: present(paths["soluble_weights"]))
check("ligand_code", lambda: present(os.path.join(paths["ligand"], "model_utils.py")))
print(json.dumps(checks), flush=True)
'''


def _location(name: str, default: str, marker: str, deps_rel: str) -> str:
    """Where one component is: the variable, else the colleague's copy, else the sibling install."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if os.path.exists(os.path.join(default, marker) if marker else default):
        return default
    return os.path.join(DEPS_DIR, deps_rel)


def _paths() -> dict:
    return {
        "fold_code": _location("OPENDDE_CODE_DIR", "/data1/gs_agent/opendde/OpenDDE", "", "code/OpenDDE"),
        "fold_checkpoint": _location(
            "OPENDDE_CHECKPOINT",
            "/data1/gs_agent/opendde/weights/checkpoint/opendde_abag.pt",
            "",
            "weights/checkpoint/opendde_abag.pt",
        ),
        "harness": _location(
            "OPENDDE_HARNESS_DIR", "/data1/gs_agent/opendde/OpenDDE-Harness", "opendde_harness", "code/OpenDDE-Harness",
        ),
        "esm": _location("ESM_MODEL_NAME", "/data1/gs_agent/opendde/weights/esm2", "model.safetensors", "weights/esm2"),
        "soluble_weights": _location(
            "SOLUBLE_MPNN_WEIGHTS",
            "/data1/gs_agent/opendde/weights/soluble_mpnn/solublempnn_v_48_020.pt",
            "",
            "weights/soluble_mpnn/solublempnn_v_48_020.pt",
        ),
        "ligand": _location("LIGANDMPNN_DIR", "/data1/gs_agent/opendde/LigandMPNN", "model_utils.py", "code/LigandMPNN"),
    }


def _child_env() -> dict:
    """The environment for every OpenDDE child: resolved locations, and DEPS_DIR/pylib on PYTHONPATH."""
    env = os.environ.copy()
    if env.get("OPENDDE_PROTREK_ENDPOINT"):
        env["PROTREK_ENDPOINT"] = env["OPENDDE_PROTREK_ENDPOINT"]
    paths = _paths()
    env["ESM_MODEL_NAME"] = paths["esm"]
    env["SOLUBLE_MPNN_WEIGHTS"] = paths["soluble_weights"]
    env["LIGANDMPNN_DIR"] = paths["ligand"]
    env["OPENDDE_HARNESS_DIR"] = paths["harness"]
    # The harness plip_runner looks for PLIP_COMMAND, plipcmd.py or plipcmd on PATH; the model venv only
    # ships the `plip` entry script (same main as plip.plipcmd), so point at it unless configured.
    if not env.get("PLIP_COMMAND"):
        plip = os.path.join(os.path.dirname(_model_python()), "plip")
        if os.path.isfile(plip):
            env["PLIP_COMMAND"] = plip
    # Every weight this job loads is a local path. The Hub calls gradio_client and
    # transformers make anyway only add latency, and a sandbox that denies them
    # marks the whole execution failed even when the tool result is complete.
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        env.setdefault(name, "1")
    pylib = os.path.join(DEPS_DIR, "pylib")
    if os.path.isdir(pylib):
        env["PYTHONPATH"] = pylib + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _probe_checks(python: str) -> dict:
    completed = subprocess.run(
        [python, "-c", _PROBE_CHILD],
        input=json.dumps(_paths()),
        capture_output=True,
        text=True,
        env=_child_env(),
        timeout=900,
    )
    line = next((item for item in reversed(completed.stdout.splitlines()) if item.strip()), "")
    try:
        loaded = json.loads(line)
    except json.JSONDecodeError:
        tail = (completed.stderr or completed.stdout or "the probe printed nothing").strip().splitlines()
        reason = (tail[-1] if tail else "no output")[:300]
        return {name: {"ok": False, "detail": f"probe did not finish: {reason}"} for name in PROBE_CHECKS}
    return loaded if isinstance(loaded, dict) else {}


def _open(url: str):
    """Open a URL, waiting out the mirror's rate limit (429) and server errors; other errors fail at once."""
    for attempt in range(4):
        try:
            # The mirror refuses the default Python-urllib agent on some paths; curl's agent is served.
            request = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
            return urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except urllib.error.URLError:
            if attempt == 3:
                raise
        time.sleep(30 * (attempt + 1))
    raise RuntimeError("unreachable")


def _download(url: str, dest: str, *, sha256: str = "", size: int = 0, magic: bytes = b"") -> None:
    """Fetch one file to a .part name, check its size, digest, or header, then move it into place."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    partial = dest + ".part"
    digest = hashlib.sha256()
    received = 0
    head = b""
    with _open(url) as response, open(partial, "wb") as out:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            if not head:
                head = block[:4]
            out.write(block)
            digest.update(block)
            received += len(block)
    problem = ""
    if size and received != size:
        problem = f"expected {size} bytes, got {received}"
    elif sha256 and digest.hexdigest() != sha256:
        problem = "the sha256 does not match the pinned digest"
    elif magic and not head.startswith(magic):
        problem = "the file is not the expected format"
    if problem:
        os.remove(partial)
        raise RuntimeError(f"{os.path.basename(dest)}: {problem}")
    os.replace(partial, dest)


def _pip_install(python: str, requirement: str) -> str:
    """Install one pinned package with --no-deps into DEPS_DIR/pylib. The dependencies are already in the venv."""
    target = os.path.join(DEPS_DIR, "pylib")
    os.makedirs(target, exist_ok=True)
    completed = subprocess.run(
        [python, "-m", "pip", "install", "--no-deps", "--target", target, requirement],
        capture_output=True,
        text=True,
        timeout=900,
    )
    # pip ends a failure with a "[notice]" line about upgrading pip; the reason is the ERROR line.
    lines = [
        line.strip()
        for line in (completed.stderr + "\n" + completed.stdout).splitlines()
        if line.strip() and not line.lstrip().startswith("[notice]")
    ]
    if completed.returncode != 0:
        errors = [line for line in lines if "ERROR" in line]
        raise RuntimeError((errors or lines or ["pip failed"])[-1])
    return f"installed {requirement}"


def _git(args: list) -> None:
    completed = subprocess.run(["git", *args], capture_output=True, text=True, timeout=900)
    if completed.returncode != 0:
        lines = (completed.stderr or completed.stdout or "git failed").strip().splitlines()
        raise RuntimeError((lines or ["git failed"])[-1][:300])


def _tree_digest(path: str) -> str:
    diff = subprocess.run(["git", "-C", path, "diff", "--binary", "HEAD"], capture_output=True, timeout=900).stdout
    return hashlib.sha256(diff).hexdigest()[:20]


def _fetch_code(name: str) -> str:
    """Clone the pinned upstream commit, apply the colleague's local changes, and check the result matches."""
    source = CODE_SOURCES[name]
    dest = os.path.join(DEPS_DIR, source["dir"])
    if os.path.isdir(os.path.join(dest, ".git")) and _tree_digest(dest) == source["tree"]:
        return dest
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    _git(["clone", "-q", source["url"], dest])
    _git(["-C", dest, "checkout", "-q", source["commit"]])
    patch_dir = os.path.join(DEPS_DIR, "patches")
    os.makedirs(patch_dir, exist_ok=True)
    patch = os.path.join(patch_dir, source["name"] + ".patch")
    Path(patch).write_bytes(base64.b64decode(CODE_PATCHES[source["name"]]))
    _git(["-C", dest, "apply", "--binary", patch])
    digest = _tree_digest(dest)
    if digest != source["tree"]:
        raise RuntimeError(f"{source['name']}: the reproduced tree differs from the colleague's (digest {digest})")
    return dest


def _fetch_esm_weights() -> str:
    target = os.path.join(DEPS_DIR, "weights", "esm2")
    for name, size in ESM_FILES.items():
        dest = os.path.join(target, name)
        if os.path.exists(dest) and (not size or os.path.getsize(dest) == size):
            continue
        _download(f"https://hf-mirror.com/{ESM_REPO}/resolve/{ESM_REVISION}/{name}", dest, size=size)
    return target


def _fetch_soluble_weights() -> str:
    dest = os.path.join(DEPS_DIR, "weights", "soluble_mpnn", "solublempnn_v_48_020.pt")
    _download(SOLUBLE_URL, dest, magic=b"PK")
    return dest


def _fetch_checkpoint() -> str:
    dest = os.path.join(DEPS_DIR, "weights", "checkpoint", "opendde_abag.pt")
    _download(CHECKPOINT_URL, dest, sha256=CHECKPOINT_SHA256, size=CHECKPOINT_SIZE, magic=b"PK")
    return dest


def _install_component(name: str, python: str) -> dict:
    """Try to make one missing component present. Never raises: the report carries the reason."""
    try:
        if name in PIP_REQUIREMENTS:
            detail = _pip_install(python, PIP_REQUIREMENTS[name])
        elif name == "esm_weights":
            detail = _fetch_esm_weights()
        elif name == "soluble_weights":
            detail = _fetch_soluble_weights()
        elif name == "fold_checkpoint":
            detail = _fetch_checkpoint()
        elif name in CODE_SOURCES:
            detail = _fetch_code(name)
        else:
            return {"component": name, "ok": False, "detail": CODE_REASON.get(name, "no automatic source")}
        return {"component": name, "ok": True, "detail": detail}
    except Exception as error:
        lines = str(error).strip().splitlines() or [type(error).__name__]
        return {"component": name, "ok": False, "detail": lines[-1][:300]}


def environment_report() -> dict:
    """What this host can run, component by component. Missing parts are installed or explained."""
    try:
        python = _model_python()
        checks = _probe_checks(python)
        installs = []
        for name in PROBE_CHECKS:
            if (checks.get(name) or {}).get("ok") or name not in INSTALLABLE:
                continue
            installs.append(_install_component(name, python))
        if any(item["ok"] for item in installs):
            checks = _probe_checks(python)
        for name in PROBE_CHECKS:
            entry = checks.get(name) or {}
            if not entry.get("ok") and name in CODE_REASON:
                detail = entry.get("detail") or "not reported by the probe"
                checks[name] = {"ok": False, "detail": f"{detail}; not installed automatically: {CODE_REASON[name]}"}
        return {"ran": True, "device": "npu", "checks": checks, "installs": installs}
    except Exception as error:
        message = (str(error).strip().splitlines() or [type(error).__name__])[-1]
        return {"ran": True, "device": "npu", "checks": {}, "error": message[:300]}


def artifact_directory(identifier: str) -> Path:
    """Opaque, content-addressed artifacts inside this runner's session workspace."""
    if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("invalid CDR structure ID")
    workspace = Path.cwd().resolve()
    root = (workspace / ".evolve" / "cdr-artifacts").resolve()
    if not root.is_relative_to(workspace):
        raise ValueError("CDR artifact directory is outside the session workspace")
    directory = root / identifier
    if directory.resolve().parent != root:
        raise ValueError("CDR structure is outside the artifact directory")
    return directory


def save_structure(candidate: dict, structure: Path, summary: dict, full: dict) -> str:
    """Retain the complete complex and original confidence before temporary cleanup."""
    content = structure.read_bytes()
    manifest = {key: candidate.get(key) for key in (
        "sequence", "target_sequence", "chain", "target_chain", "cdr_regions", "target_hotspots",
    )}
    identifier = hashlib.sha256(content + json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    directory = artifact_directory(identifier)
    directory.mkdir(parents=True, exist_ok=True)
    for filename, value in (("manifest.json", manifest), ("summary.json", summary), ("full.json", full)):
        target = directory / filename
        if target.is_symlink():
            raise ValueError("CDR artifact must not be a symbolic link")
        target.write_text(json.dumps(value), encoding="utf-8")
    destination = directory / ("complex.cif" if structure.suffix.lower() == ".cif" else "complex.pdb")
    if destination.is_symlink():
        raise ValueError("CDR artifact must not be a symbolic link")
    destination.write_bytes(content)
    return identifier


_ANALYSIS_WORKER = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["OPENDDE_HARNESS_DIR"])

def run(request):
    name = request["analysis_tool"]
    config = request
    candidates = request.get("candidates") or []
    if name == "analyze_evolution_tree":
        from opendde_harness.plugin.protein_design.servers.backends.evolution_analysis import analyze_search_history
        from opendde_harness.plugin.protein_design.servers.backends.foldmason import run_structural_tree
        structural = run_structural_tree(request["history"], task_output=Path(request["analysis_root"]),
            cycle=int(request.get("cycle", 0)), binder_chain_ids=[config["chain"]],
            cdr_regions={config["chain"]: sorted({p for a,b in config["cdr_regions"] for p in range(a-1,b)})},
            reflection_interval=int(request.get("reflection_interval", 20)), current_parent_id=None, minimize=True)
        trees = {chain: v["tree_path"] for chain,v in (structural.get("chains") or {}).items() if v.get("tree_path")}
        alignments = {chain: v["alignment_path"] for chain,v in (structural.get("chains") or {}).items() if v.get("alignment_path")}
        result = analyze_search_history(request["history"], metric="objective", minimize=True,
            tree_paths=trees, alignment_paths=alignments)
        result["structural_analysis"] = structural
        return {"available": True, "result": result}
    if name.startswith("protrek_"):
        from opendde_harness.plugin.protein_design.core.external import protrek_endpoint
        if protrek_endpoint() is None:
            return {"available": False, "reason": "ProTrek endpoint is not configured"}
        from opendde_harness.plugin.protein_design.servers.backends.protrek import GRADIO_AVAILABLE, search_protrek_sequence, search_protrek_structure
        if not GRADIO_AVAILABLE:
            return {"available": False, "reason": "ProTrek client is missing from the model environment"}
        if name == "protrek_sequence_search":
            key = "sequence" if request["arguments"].get("entity") == "binder" else "target_sequence"
            return {"available": True, "result": search_protrek_sequence(request[key])}
        return {"available": True, "results": {c["candidate_id"]: search_protrek_structure(c["structure_path"], chain=config["chain"]) for c in candidates}}
    if name == "analyze_current_cycle_structure":
        from opendde_harness.plugin.protein_design.servers.backends.structure_analysis import run_plip_structure_analysis
        return run_plip_structure_analysis([c["structure_path"] for c in candidates], 0,
            [c["candidate_id"] for c in candidates], [config["chain"]], [config["target_chain"]],
            request["output_dir"], timeout=min(60, max(1, int(request["tool_timeout_seconds"] / 3))))
    if name == "analyze_epitope":
        from opendde_harness.plugin.protein_design.core.gate import sequence_hotspot_residue_ids
        from opendde_harness.plugin.protein_design.servers.backends.epitope_analysis import analyze_epitope, load_structure
        groups = {"CDR" + str(i + 1): set(range(int(a) - 1, int(b))) for i, (a, b) in enumerate(config["cdr_regions"])}
        return {"available": True, "results": {c["candidate_id"]: analyze_epitope(
            c["structure_path"], [config["chain"]], [config["target_chain"]],
            {config["chain"]: groups}, hotspots=sorted(sequence_hotspot_residue_ids(
                load_structure(c["structure_path"]), {(config["target_chain"], int(p)-1) for p in config["target_hotspots"]},
                [config["target_chain"]]))
        ) for c in candidates}}
    raise ValueError("unknown CDR analysis tool")

try:
    print(json.dumps(run(json.loads(sys.stdin.read())), default=lambda x: x.item() if hasattr(x, "item") else str(x)), flush=True)
except Exception as error:
    print(json.dumps({"available": False, "reason": str(error)[-1500:]}), flush=True)
'''


def analyze_tool(request: dict) -> dict:
    allowed = {"protrek_sequence_search", "protrek_structure_search", "analyze_epitope",
               "analyze_current_cycle_structure", "analyze_evolution_tree"}
    if request.get("analysis_tool") not in allowed:
        raise ValueError("unknown CDR analysis tool")
    history_tool = request["analysis_tool"] == "analyze_evolution_tree"
    selected = request.get("history") if history_tool else request.get("candidates")
    selected = selected or []
    if not isinstance(selected, list) or (not history_tool and len(selected) > 3):
        raise ValueError("analysis accepts at most three candidates")
    materialized = []
    for candidate in selected:
        if history_tool and not candidate.get("structure_id"):
            materialized.append(candidate)
            continue
        directory = artifact_directory(str(candidate.get("structure_id") or ""))
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or manifest_path.resolve().parent != directory.resolve():
            raise ValueError("invalid CDR artifact manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if candidate.get("sequence") != manifest.get("sequence"):
            raise ValueError("structure does not belong to the selected candidate")
        structure = directory / "complex.cif"
        if not structure.is_file():
            structure = directory / "complex.pdb"
        if structure.resolve().parent != directory.resolve() or structure.is_symlink():
            raise ValueError("invalid CDR structure artifact")
        materialized.append({**candidate, "structure_path": str(structure)})
    timeout = min(120.0, max(0.01, float(request.get("tool_timeout_seconds", 120))))
    with tempfile.TemporaryDirectory(prefix="cdr-analysis-") as work:
        script = Path(work) / "analysis.py"
        script.write_text(_ANALYSIS_WORKER, encoding="utf-8")
        try:
            payload = {**request, "output_dir": work}
            if history_tool:
                payload["history"] = materialized
                analysis_root = artifact_directory(str(request.get("run_key") or ""))
                analysis_root.mkdir(parents=True, exist_ok=True)
                payload["analysis_root"] = str(analysis_root)
            else:
                payload["candidates"] = materialized
            completed = subprocess.run([_model_python(), str(script)],
                input=json.dumps(payload),
                capture_output=True, text=True, env=_child_env(), timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"available": False, "reason": "analysis exceeded its bounded runtime"}
    if completed.returncode != 0:
        return {"available": False, "reason": (completed.stderr or completed.stdout)[-1500:]}
    lines = completed.stdout.splitlines()
    result = json.loads(next((line for line in reversed(lines) if line.strip()), "{}"))
    if not isinstance(result, dict):
        raise ValueError("analysis returned no object")
    return result


def predict(candidate: dict) -> dict:
    paths = _paths()
    code = paths["fold_code"]
    checkpoint = paths["fold_checkpoint"]
    python = configured("OPENDDE_PYTHON", "/data1/gs_agent/opendde/venv_pd/bin/python") or sys.executable
    if not os.path.exists(code) or not os.path.exists(checkpoint):
        raise RuntimeError("OPENDDE_CODE_DIR and OPENDDE_CHECKPOINT are required")
    with tempfile.TemporaryDirectory(prefix="cdr-opendde-") as work:
        root = Path(work)
        request = root / "input.json"
        request.write_text(json.dumps(input_document(candidate)), encoding="utf-8")
        output = root / "out"
        env = _child_env()
        env["PYTHONPATH"] = code + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.setdefault("LAYERNORM_TYPE", "torch")
        # common/components.cif sits beside the checkpoint directory. Without
        # this, OpenDDE tries to download it and the offline NPU host fails.
        env.setdefault("OPENDDE_ROOT_DIR", str(Path(checkpoint).resolve().parent.parent))
        # `python -m runner.cli` never calls the Click group. rdkit has to be
        # imported before torch_npu; the predict module imports torch first and
        # then segfaults on 910C.
        command = [
            python, "-c",
            "import rdkit.Chem; from runner.cli import opendde_cli; opendde_cli()",
            "pred",
            "-i", str(request), "-o", str(output),
            "--cycle", os.environ.get("OPENDDE_CYCLE", "10"),
            "--step", os.environ.get("OPENDDE_STEP", "200"),
            "--sample", "1", "--seeds", str(int(candidate.get("seed", 42))),
            "--device", os.environ.get("OPENDDE_DEVICE", "npu"),
            "--load_checkpoint_path", checkpoint,
            "--use_msa", "false",
            "--trimul_kernel", "torch", "--triatt_kernel", "torch",
        ]
        completed = subprocess.run(command, cwd=code, env=env, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "OpenDDE failed").strip()
            raise RuntimeError(detail[-2000:])
        summary_paths = sorted(output.glob("**/*summary_confidence_sample_0.json"))
        full_paths = sorted(output.glob("**/*full_data_sample_0.json"))
        structures = sorted(output.glob("**/*_sample_0.cif"))
        if not summary_paths or not full_paths or not structures:
            raise RuntimeError("OpenDDE finished without summary, full confidence, and structure")
        summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
        full = json.loads(full_paths[0].read_text(encoding="utf-8"))
        if not isinstance(summary, dict) or not isinstance(full, dict):
            raise RuntimeError("OpenDDE confidence output is not an object")
        atoms = load_atoms(structures[0])
        # Only the binder chain is needed for SolubleMPNN. Keeping the target
        # here made CRLF2-sized complexes exceed the runner argument limit.
        backbone = pack_backbone(backbone_pdb(atoms, {str(candidate.get("chain") or "D")}))
        structure_id = save_structure(candidate, structures[0], summary, full)
        # Missing PLL changes the optimization objective. Refuse the candidate;
        # never mix structure-only and ESM2-weighted losses in one population.
        pll = esm_pll(str(candidate["sequence"]))
        scored = score_fold(candidate, summary, full, structures[0], esm2_pll=pll)
        scored["backbone_pdb"] = backbone
        scored["structure_id"] = structure_id
        return scored


def main() -> None:
    candidate = globals().get("_EVOLVE_CANDIDATE")
    if not isinstance(candidate, dict):
        emit({"device": "npu", "error": "the candidate is not a JSON object", "ran": False})
        return
    if candidate.get("probe") is True:
        emit(environment_report())
        return
    if candidate.get("analysis_tool"):
        try:
            emit({"tool_result": analyze_tool(candidate)})
        except Exception as error:
            emit({"tool_result": {"available": False, "reason": str(error)[-1500:]}})
        return
    sequence = candidate.get("sequence")
    if not isinstance(sequence, str) or not amino_acid(sequence):
        emit({"device": "npu", "error": "sequence is not amino acids", "ran": False})
        return
    try:
        folding = candidate
        mpnn_error = ""
        portfolio: list[dict] = []
        skill_used = ""
        if candidate.get("propose") is True:
            chain = str(candidate.get("chain") or "D")
            mutable = set(position + 1 for position in cdr_positions(candidate.get("cdr_regions"), len(sequence)))
            raw_limit = candidate.get("num_sequences", HARNESS_BATCH)
            limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) and raw_limit >= 1 else HARNESS_BATCH
            raw_seed = candidate.get("proposal_seed", 42)
            proposal_seed = raw_seed if isinstance(raw_seed, int) and not isinstance(raw_seed, bool) and raw_seed >= 0 else 42
            raw_cycle = candidate.get("cycle", 0)
            cycle = raw_cycle if isinstance(raw_cycle, int) and not isinstance(raw_cycle, bool) and raw_cycle >= 0 else 0
            blocked = {
                item for item in candidate.get("excluded_sequences") or []
                if isinstance(item, str)
            }
            blocked.add(sequence)
            raw_backbone = candidate.get("parent_backbone_pdb")
            backbone = unpack_backbone(raw_backbone) if isinstance(raw_backbone, str) else ""
            has_backbone = isinstance(backbone, str) and bool(backbone.strip()) and bool(mutable)
            forced = candidate.get("skill") if isinstance(candidate.get("skill"), str) else None
            skill = design_skill(proposal_seed, cycle, has_backbone, forced=forced)
            rows: list = []
            rounds: list[list[str]] = []

            def sample_mpnn() -> None:
                nonlocal mpnn_error
                if not has_backbone:
                    return
                try:
                    for attempt in range(3):
                        shortfall = limit - len(inverse_folding_batch(
                            sequence, chain, mutable, rounds, limit, blocked,
                        ))
                        if shortfall <= 0:
                            break
                        got = soluble_sequences(
                            backbone, chain, sequence, mutable, None,
                            proposal_seed + attempt, shortfall,
                        )
                        rounds.append([str(item) for item in got])
                except Exception as error:
                    mpnn_error = str(error).splitlines()[-1][:300]

            if skill == MPNN_SKILL:
                sample_mpnn()
            else:
                rows = esm_scan(sequence)
            skill_used, batch = cycle_batch(
                sequence, chain, mutable, skill, limit, rows, rounds, has_backbone, blocked,
            )
            if not batch and skill_used == ESM_SKILL and has_backbone and not rounds:
                sample_mpnn()
                skill_used, batch = cycle_batch(
                    sequence, chain, mutable, skill, limit, rows, rounds, has_backbone, blocked,
                )
            elif not batch and skill_used == MPNN_SKILL and not rows:
                try:
                    rows = esm_scan(sequence)
                except Exception as error:
                    mpnn_error = mpnn_error or str(error).splitlines()[-1][:300]
                skill_used, batch = cycle_batch(
                    sequence, chain, mutable, skill, limit, rows, rounds, has_backbone, blocked,
                )
            if not batch:
                emit({
                    "device": "npu",
                    "error": "the model returned no CDR mutations",
                    "proposal": "empty",
                    "ran": False,
                    "skill": skill_used,
                })
                return
            child, mutations, _summary = batch[0]
            if candidate.get("generation_only") is True:
                emit({"device": "npu", "proposal": "generated", "ran": False, "skill": skill_used,
                      "portfolio": [{"sequence": item[0], "mutations": item[1]} for item in batch]})
                return
            folding = dict(candidate)
            folding["sequence"] = child
            folding["mutations"] = mutations
            portfolio = [{"sequence": item[0], "mutations": item[1]} for item in batch[1:]]
        stage("fold")
        scored = predict(folding)
        scored["sequence"] = folding["sequence"]
        scored["mutations"] = folding.get("mutations") or []
        if portfolio:
            scored["portfolio"] = portfolio
        if skill_used:
            scored["skill"] = skill_used
        if mpnn_error:
            scored["soluble_error"] = mpnn_error
        emit(scored)
    except Exception as error:
        emit({"device": "npu", "error": str(error).splitlines()[-1][:500], "ran": False})


if __name__ == "__main__":
    main()
