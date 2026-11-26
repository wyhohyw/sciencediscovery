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
import json
import math
import os
import random
import subprocess
import sys
import tempfile
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
            env=os.environ.copy(),
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


def predict(candidate: dict) -> dict:
    code = configured("OPENDDE_CODE_DIR", "/data1/gs_agent/opendde/OpenDDE")
    checkpoint = configured(
        "OPENDDE_CHECKPOINT",
        "/data1/gs_agent/opendde/weights/checkpoint/opendde_abag.pt",
    )
    python = configured("OPENDDE_PYTHON", "/data1/gs_agent/opendde/venv_pd/bin/python") or sys.executable
    if not code or not checkpoint:
        raise RuntimeError("OPENDDE_CODE_DIR and OPENDDE_CHECKPOINT are required")
    with tempfile.TemporaryDirectory(prefix="cdr-opendde-") as work:
        root = Path(work)
        request = root / "input.json"
        request.write_text(json.dumps(input_document(candidate)), encoding="utf-8")
        output = root / "out"
        env = os.environ.copy()
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
            "--sample", "1", "--seeds", "42",
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
        pll = None
        esm_error = ""
        try:
            pll = esm_pll(str(candidate["sequence"]))
        except Exception as error:
            esm_error = str(error).splitlines()[-1][:300]
        scored = score_fold(candidate, summary, full, structures[0], esm2_pll=pll)
        scored["backbone_pdb"] = backbone
        if esm_error:
            scored["esm2_error"] = esm_error
        return scored


def main() -> None:
    candidate = globals().get("_EVOLVE_CANDIDATE")
    if not isinstance(candidate, dict):
        emit({"device": "npu", "error": "the candidate is not a JSON object", "ran": False})
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
