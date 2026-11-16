"""Score one evolve candidate by running the antibody pipeline on the selected runner.

Evolve prepends ``_EVOLVE_CANDIDATE``. The last stdout line is the measurement.
A real test keeps MSA on, one to eight designs, and one to four Protenix
samples. Diffusion stays in the short 15 to 30 range. The seed stays ``42``.
"""

import csv
import glob
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)


# Furthest marker wins. These are lines the pipeline actually prints, not a clock.
_STAGE_MARKERS = (
    ("rfdiffusion", ("Step 1/2",)),
    ("proteinmpnn", ("] proteinmpnn", "Step 2/2")),
    ("protenix", ("] protenix", "[protenix]")),
    ("screen", ("] screen_protenix",)),
)


def furthest_stage(text):
    """The latest pipeline stage named in ``text``, or None."""
    found = None
    for name, markers in _STAGE_MARKERS:
        if any(marker in text for marker in markers):
            found = name
    return found


def _announce(stage, seen):
    if not stage or (seen and seen[-1] == stage):
        return
    seen.append(stage)
    print(f"EVOLVE_STAGE {stage}", flush=True)


def resolve_input(value):
    """A candidate path, the same relative path under /workspace, or its file name there."""
    if not isinstance(value, str) or not value.strip():
        return ""
    path = value.strip().replace("\\", "/")
    candidates = [path]
    if not path.startswith("/"):
        candidates.append("/workspace/" + path.lstrip("/"))
    name = os.path.basename(path)
    if name:
        candidates.append("/workspace/" + name)
    for item in candidates:
        if os.path.isfile(item):
            return item
    return ""


_AA = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}


def _letter(name):
    return _AA.get(str(name).upper(), "X")


def sequences_from_pdb(text):
    seen = {}
    order = []
    for line in text.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 26:
            continue
        if line[12:16].strip() != "CA":
            continue
        chain = line[21].strip() or "?"
        seq = line[22:26].strip()
        key = (chain, seq)
        if key in seen:
            continue
        seen[key] = _letter(line[17:20])
        order.append(key)
    return _group(seen, order)


def sequences_from_mmcif(text):
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("_atom_site."):
            index += 1
            continue
        columns = []
        while index < len(lines) and lines[index].startswith("_atom_site."):
            columns.append(lines[index][len("_atom_site."):].strip())
            index += 1
        rows = []
        while index < len(lines):
            line = lines[index]
            if not line.strip() or line.startswith(("loop_", "data_", "#", "_")):
                break
            rows.append(line.split())
            index += 1
        parsed = _mmcif_rows(columns, rows)
        if parsed:
            return parsed
    return []


def _column(columns, *names):
    for name in names:
        if name in columns:
            return columns.index(name)
    return None


def _mmcif_rows(columns, rows):
    atom_i = _column(columns, "label_atom_id", "auth_atom_id")
    comp_i = _column(columns, "label_comp_id", "auth_comp_id")
    chain_i = _column(columns, "label_asym_id", "auth_asym_id")
    seq_i = _column(columns, "label_seq_id", "auth_seq_id")
    if None in (atom_i, comp_i, chain_i, seq_i):
        return []
    width = max(atom_i, comp_i, chain_i, seq_i)
    seen = {}
    order = []
    for row in rows:
        if len(row) <= width or row[atom_i] != "CA":
            continue
        key = (row[chain_i], row[seq_i])
        if key in seen:
            continue
        seen[key] = _letter(row[comp_i])
        order.append(key)
    return _group(seen, order)


def _group(seen, order):
    chains = []
    grouped = {}
    for chain, seq in order:
        if chain not in grouped:
            chains.append(chain)
            grouped[chain] = []
        try:
            number = int(seq)
        except ValueError:
            number = 0
        grouped[chain].append((number, seen[(chain, seq)]))
    return [(chain, "".join(letter for _, letter in sorted(grouped[chain]))) for chain in chains]


def sequences_from_structure(text):
    if "_atom_site." in text:
        parsed = sequences_from_mmcif(text)
        if parsed:
            return parsed
    return sequences_from_pdb(text)


def format_fasta(pairs):
    lines = []
    for chain, sequence in pairs:
        lines.append(">" + chain)
        for start in range(0, len(sequence), 80):
            lines.append(sequence[start:start + 80])
    return "\n".join(lines) + ("\n" if lines else "")


def _fasta_pairs(path):
    pairs = []
    header = ""
    chunks = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header or chunks:
                    pairs.append((header or "sequence", "".join(chunks)))
                header = line[1:].split()[0] or "sequence"
                chunks = []
            else:
                chunks.append(line)
    if header or chunks:
        pairs.append((header or "sequence", "".join(chunks)))
    return [(name, sequence) for name, sequence in pairs if sequence]


def sequences_near(root, design):
    found = []
    for dirpath, _, names in os.walk(root):
        for name in names:
            lower = name.lower()
            if not lower.endswith((".fa", ".fasta")):
                continue
            path = os.path.join(dirpath, name)
            if design and design not in path.replace("\\", "/"):
                continue
            found.append(path)
    if not found:
        return []
    path = min(found, key=os.path.getsize)
    return _fasta_pairs(path)


def stage_design(root, row, digest, dest_root=None):
    """Copy the measured structure and sequence where the session can publish them."""
    source = str(row.get("protenix_model_cif") or "")
    if not source or not os.path.isfile(source):
        return ""
    if dest_root is None:
        if not os.path.isdir("/workspace"):
            return ""
        dest_root = "/workspace/antigen_epitope/results"
    dest = os.path.join(dest_root, digest)
    os.makedirs(dest, exist_ok=True)
    ext = os.path.splitext(source)[1].lower()
    structure_name = "structure.pdb" if ext == ".pdb" else "structure.cif"
    shutil.copyfile(source, os.path.join(dest, structure_name))
    design = str(row.get("design") or "")
    pairs = sequences_near(root, design) or sequences_near(root, "")
    if not pairs:
        with open(source, encoding="utf-8", errors="replace") as handle:
            pairs = sequences_from_structure(handle.read())
    rel = f"antigen_epitope/results/{digest}"
    index = {"structure": f"{rel}/{structure_name}"}
    if pairs:
        with open(os.path.join(dest, "sequence.fasta"), "w", encoding="utf-8") as handle:
            handle.write(format_fasta(pairs))
        index["sequence"] = f"{rel}/sequence.fasta"
    score = row.get("screen_score")
    if score not in (None, ""):
        index["score"] = score
    with open(os.path.join(dest, "index.json"), "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False)
        handle.write("\n")
    return dest


def main():
    candidate = globals().get("_EVOLVE_CANDIDATE")
    if not isinstance(candidate, dict):
        emit({"device": "npu", "score": 0.0, "error": "missing candidate", "ran": False})
        return 0
    epitope = [str(item) for item in candidate.get("epitope_residues") or []]
    raw_hotspots = candidate.get("hotspots")
    if isinstance(raw_hotspots, list):
        labels = [str(item) for item in raw_hotspots]
        hotspots = "[" + ",".join(labels) + "]"
    else:
        hotspots = str(raw_hotspots or "")
        labels = [item for item in hotspots.strip("[]").split(",") if item]
    reason = ""
    if not labels or any(item not in set(epitope) for item in labels):
        reason = "hotspots must be a non-empty subset of the epitope"
    try:
        designs = int(candidate["num_designs"])
        diffuser = int(candidate["diffuser_t"])
        final = int(candidate["final_step"])
        samples = int(candidate["protenix_n_sample"])
    except (KeyError, TypeError, ValueError):
        reason = reason or "design schedule is not integers"
        designs = diffuser = final = samples = 0
    use_msa = candidate.get("protenix_use_msa")
    if not reason and not 1 <= designs <= 8:
        reason = "num_designs must be from 1 to 8"
    if not reason and not 1 <= samples <= 4:
        reason = "protenix_n_sample must be from 1 to 4"
    if not reason and not isinstance(use_msa, bool):
        reason = "protenix_use_msa must be a boolean"
    if not reason and str(candidate.get("protenix_seeds") or "42") != "42":
        reason = "protenix_seeds stays 42"
    if not reason and not 15 <= diffuser <= 30:
        reason = "diffuser_t must be from 15 to 30"
    if not reason and not 1 <= final <= diffuser:
        reason = "final_step must be from 1 to diffuser_t"
    if reason:
        # A real screen score can land near 0.05. This candidate never ran, so
        # its score stays at 0 and the probe can still tell the two apart.
        emit({"device": "npu", "score": 0.0, "error": reason, "ran": False})
        return 0

    digest = hashlib.sha256(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    target = resolve_input(candidate.get("target_pdb"))
    framework = resolve_input(candidate.get("framework_pdb"))
    if not target or not framework:
        emit({
            "device": "npu",
            "score": 0.0,
            "error": "target_pdb and framework_pdb must be files the runner can read",
            "ran": False,
        })
        return 0
    root = os.path.join("/workspace/antigen-runs" if os.path.isdir("/workspace") else "/tmp/antigen-runs", digest)
    env = os.environ.copy()
    env["ANTIBODY_PIPELINE_HOME"] = "/home/lujiale/antibody_pipeline"
    env["ANTIBODY_REQUIRE_SCIENCEAGENT_ENV"] = "0"
    env["PYTHONPYCACHEPREFIX"] = "/tmp/pycache"
    env.pop("PYTHONHOME", None)
    command = r"""
set -u
unset PYTHONPATH
set +u
source /usr/local/Ascend/ascend-toolkit/set_env.sh >/dev/null 2>&1 || true
export PYTHONPATH="/home/lujiale/antibody_pipeline/venv/lib/python3.12/site-packages:/home/lujiale/antibody_pipeline/python_user/lib/python3.12/site-packages${PYTHONPATH:+:$PYTHONPATH}"
set -u
mkdir -p "$ROOT"
set +e
/bin/bash /home/lujiale/antibody_pipeline/protenix_helpers/run_full_antibody_pipeline.sh \
  --run-dir "$ROOT" \
  --num-designs "$DESIGNS" \
  --python /home/miniconda3/envs/mindspore_py12/bin/python \
  --npus 0 --workers-per-npu 1 \
  --target-pdb "$TARGET" \
  --framework-pdb "$FRAMEWORK" \
  --ckpt /home/lujiale/antibody_pipeline/rf_ckpt/RFdiffusion_Ab.ckpt \
  --hotspots "$HOTSPOTS" \
  --design-loops "${LOOPS}" \
  --final-step "$FINAL" --diffuser-t "$DIFF" \
  --protenix-dir /home/lujiale/antibody_pipeline/models/mindscience/MindSPONGE/applications/protenix \
  --protenix-ckpt /home/lujiale/antibody_pipeline/models/mindscience/MindSPONGE/applications/protenix/release_data/checkpoint/ms_model_v0.5.0.ckpt \
  --protenix-use-msa "$MSA" --protenix-n-sample "$SAMPLES" --protenix-seeds "42" \
  --cann-set-env /usr/local/Ascend/ascend-toolkit/set_env.sh
exit $?
"""
    env["ROOT"] = root
    env["HOTSPOTS"] = hotspots
    env["FINAL"] = str(final)
    env["DIFF"] = str(diffuser)
    env["TARGET"] = target
    env["FRAMEWORK"] = framework
    env["LOOPS"] = str(candidate.get("design_loops") or "[H1:8,H2:6,H3:16]")
    env["DESIGNS"] = str(designs)
    env["SAMPLES"] = str(samples)
    env["MSA"] = "true" if use_msa else "false"
    shell = "/bin/bash" if os.path.exists("/bin/bash") else "/usr/bin/bash"
    print("EVOLVE_STAGE starting", flush=True)
    returncode, stdout = _run_pipeline(shell, command, env, root)
    rows = []
    for path in glob.glob(root + "/05_screening/protenix_screening_summary.csv"):
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    row = rows[0] if rows else {}
    if not row.get("screen_score"):
        detail = stdout.strip().replace("\n", " ")[-400:]
        emit({
            "device": "npu",
            "score": 0.0,
            "error": detail or "screening summary missing",
            "pipeline_status": returncode,
            "root": root,
            "ran": False,
        })
        return 0
    try:
        stage_design(root, row, digest)
    except OSError:
        pass
    emit({
        "device": "npu",
        "score": float(row["screen_score"]),
        "decision": row.get("decision", ""),
        "iptm": row.get("iptm", ""),
        "hotspot_prob": row.get("hotspot_prob", ""),
        "pipeline_status": returncode,
        "root": root,
        "ran": True,
    })
    return 0


def _run_pipeline(shell, command, env, root):
    """Run the pipeline and print each new stage while it is still going."""
    proc = subprocess.Popen(
        [shell, "-c", command],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    seen = []
    parts = []
    lock = threading.Lock()

    def announce(stage):
        with lock:
            _announce(stage, seen)

    def read_stdout():
        stream = proc.stdout
        if stream is None:
            return
        for line in stream:
            parts.append(line)
            announce(furthest_stage(line))

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    after = os.path.join(root, "logs", "02_after_rfdiffusion.log")
    while proc.poll() is None:
        if os.path.isfile(after):
            try:
                with open(after, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                text = ""
            announce(furthest_stage(text))
        time.sleep(1)
    reader.join(timeout=5)
    return proc.returncode if proc.returncode is not None else 1, "".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
