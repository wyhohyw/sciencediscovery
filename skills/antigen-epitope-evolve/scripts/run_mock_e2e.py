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

"""Run the antigen package through one directed evolve search.

The scorer is the mock NPU program, executed by a local stand-in for the
Runner. No card and no model service are used. The search is the real PUCT
engine from a checkout that has npu_job; point SCIENCE_EVOLVE_SRC at its
services/evolve/src directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import antigen_epitope  # noqa: E402
import mock_npu_evaluator  # noqa: E402


def evolve_src() -> Path:
    raw = os.environ.get("SCIENCE_EVOLVE_SRC", "").strip()
    if raw:
        return Path(raw)
    return Path(r"C:\Users\wyh\Documents\Deerflow\ScienceDiscovery-evolve-e2e\services\evolve\src")


def complex_pdb() -> str:
    def atom(serial, name, residue, chain, number, x, y, z, element):
        atom_name = f" {name:<3}"[:4]
        return (
            f"ATOM  {serial:5d} {atom_name} {residue:>3} {chain}{number:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           {element:>2}"
        )

    lines = [
        atom(1, "CA", "SER", "H", 1, 0, 0, 0, "C"),
        atom(2, "CA", "ALA", "B", 10, 3.0, 0, 0, "C"),
        atom(3, "CA", "GLY", "B", 11, 4.4, 0, 0, "C"),
        "END",
    ]
    return "\n".join(lines) + "\n"


class ExecutingRunner:
    """Accept the evolve shell protocol and run the submitted program."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, str]] = {}
        self.outputs: list[str] = []
        self.errors: list[str] = []
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler(self):
        runner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                execution_id = str(body["executionId"])
                with runner.lock:
                    runner.jobs[execution_id] = {"code": str(body["code"]), "state": "queued"}
                self._json(202, {"id": execution_id, "state": "queued"})

            def do_GET(self):
                execution_id = self.path.split("?", 1)[0].rstrip("/").split("/")[-1]
                with runner.lock:
                    job = runner.jobs.get(execution_id)
                if job is None:
                    self._json(404, {"state": "unknown"})
                    return
                if job["state"] == "queued":
                    stdout, error = runner._run(job["code"])
                    with runner.lock:
                        job["state"] = "completed" if not error else "failed"
                        job["stdout"] = stdout
                        job["error"] = error
                        runner.outputs.append(stdout)
                        if error:
                            runner.errors.append(error)
                with runner.lock:
                    state = job["state"]
                    stdout = job.get("stdout", "")
                if state != "completed":
                    self._json(200, {"state": state, "result": {"stdout": stdout, "stderr": job.get("error", "")}})
                    return
                self._json(200, {"state": "completed", "result": {"stdout": stdout}})

            def _json(self, status, payload):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt, *args):
                del fmt, args

        return Handler

    def _run(self, code: str) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.py"
            path.write_text(code, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        if proc.returncode != 0:
            return proc.stdout, proc.stderr or f"exit {proc.returncode}"
        return proc.stdout, ""

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)


def proposal(start: dict) -> str:
    labels = mock_npu_evaluator.parse_hotspots(start["hotspots"]) or []
    edited = dict(start)
    if len(labels) > 1:
        edited["hotspots"] = "[" + labels[0] + "]"
    else:
        edited["num_designs"] = int(start["num_designs"]) + 1
    return json.dumps(edited, ensure_ascii=False)


def main() -> int:
    src = evolve_src()
    if not (src / "sciencediscovery_evolve" / "puct_engine.py").is_file():
        print(f"evolve source not found at {src}")
        return 2
    sys.path.insert(0, str(src))
    from sciencediscovery_evolve.completion import CompletionUsage
    from sciencediscovery_evolve.engine import RunSpec
    from sciencediscovery_evolve.puct_engine import PuctEngine

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pdb = root / "complex.pdb"
        pdb.write_text(complex_pdb(), encoding="utf-8")
        design = antigen_epitope.build_package(
            pdb, "B", ["H"], root / "inputs", "antibody_pipeline/inputs",
            "1ABC", "literature_sources.json", "mock-e2e", 4.5,
        )
        start = design["evolve_start"]
        evaluator = (HERE / "mock_npu_evaluator.py").read_text(encoding="utf-8")
        reply = proposal(start)
        runner = ExecutingRunner()
        events = []
        try:
            port = runner.server.server_address[1]

            def completion_factory(spec, on_usage, should_stop):
                del spec, on_usage, should_stop

                def complete(prompt, sink=None, on_failure=None):
                    del prompt, on_failure
                    if sink is not None:
                        sink(CompletionUsage(total=1, completion=1, capped=False))
                    return reply

                return complete

            spec = RunSpec(
                search_id="antigen-mock-e2e",
                algorithm="puct",
                expansions=1,
                scorecard_hash="mock",
                scorecard={
                    "criteria": [{
                        "direction": "maximize",
                        "id": "score",
                        "name": "mock score",
                        "measure": {"kind": "npu_job"},
                        "normalize": {"kind": "identity"},
                        "weight": 1,
                    }],
                    "direction": {"editable": list(design["evolve_editable"])},
                    "solvedThreshold": 0.999,
                },
                statement="Raise the mock antibody-design score by editing only the named JSON fields.",
                baseline_code=json.dumps(start, ensure_ascii=False),
                workers=1,
                options={
                    "mode": "serial",
                    "prior_exponent": 0,
                    "npu": {
                        "url": f"http://127.0.0.1:{port}",
                        "token": "token",
                        "execution": {"agentId": "agent", "code": evaluator},
                        "sessionId": "session",
                        "agentId": "agent",
                        "pollSeconds": 0.01,
                    },
                },
            )
            PuctEngine(
                completion_factory=completion_factory,
                store_root=root / "candidates",
            ).run(spec, events.append, lambda: False)
        finally:
            runner.close()

    finished = [event for event in events if event.get("type") == "search_finished"]
    measurements = []
    for output in runner.outputs:
        line = output.strip().splitlines()[-1] if output.strip() else ""
        if line:
            measurements.append(json.loads(line))
    print(json.dumps({
        "status": finished[-1]["status"] if finished else "missing",
        "candidates": finished[-1].get("candidates") if finished else 0,
        "measurements": len(measurements),
        "scores": [item.get("score") for item in measurements],
        "mock": all(item.get("mock") is True and item.get("device") == "npu" for item in measurements),
        "stages": measurements[0]["stages"] if measurements else [],
    }, ensure_ascii=False))
    if not finished or finished[-1]["status"] != "succeeded":
        print("search did not succeed")
        for event in events:
            if event.get("type") in {"log", "search_finished"}:
                print(event)
        for error in runner.errors:
            print(error)
        return 1
    if len(measurements) < 2 or not all(item.get("device") == "npu" and item.get("mock") is True for item in measurements):
        print("the mock program did not score both the start and a later candidate")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
