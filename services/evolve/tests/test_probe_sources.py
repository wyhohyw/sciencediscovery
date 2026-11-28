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

"""The probe script and the capability module must agree, and the embedded patches must match the repo."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.science_tags(category="ut", os="linux", arch=("amd64", "arm64"))

from sciencediscovery_evolve.capabilities import FOLD_CHECKS, SKILL_CHECKS

ROOT = Path(__file__).resolve().parents[3]
EVALUATOR = ROOT / "skills" / "evolve-design" / "scripts" / "cdr_opendde_evaluator.py"
PATCHES = ROOT / "skills" / "evolve-design" / "patches"


def _evaluator():
    spec = importlib.util.spec_from_file_location("cdr_opendde_evaluator_under_test", EVALUATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plip_command_defaults_to_the_model_venv_entry_script(tmp_path, monkeypatch):
    # The harness plip_runner only finds PLIP via PLIP_COMMAND / plipcmd on PATH; the venv ships `plip`.
    evaluator = _evaluator()
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("", encoding="utf-8")
    (venv_bin / "plip").write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENDDE_PYTHON", str(venv_bin / "python"))
    monkeypatch.delenv("PLIP_COMMAND", raising=False)
    assert evaluator._child_env()["PLIP_COMMAND"] == str(venv_bin / "plip")

    monkeypatch.setenv("PLIP_COMMAND", "/custom/plip")
    assert evaluator._child_env()["PLIP_COMMAND"] == "/custom/plip"


def test_every_check_the_capabilities_read_is_reported_by_the_probe():
    evaluator = _evaluator()
    needed = set(FOLD_CHECKS) | {name for names in SKILL_CHECKS.values() for name in names}

    assert needed <= set(evaluator.PROBE_CHECKS)


def test_each_code_source_has_its_patch_embedded_byte_for_byte_from_the_repo():
    evaluator = _evaluator()

    for source in evaluator.CODE_SOURCES.values():
        embedded = base64.b64decode(evaluator.CODE_PATCHES[source["name"]])
        on_disk = (PATCHES / f"{source['name']}.patch").read_bytes()
        assert embedded == on_disk, source["name"]


def test_every_failing_component_has_either_an_installer_or_a_reason():
    evaluator = _evaluator()
    installers = set(evaluator.PIP_REQUIREMENTS) | {"esm_weights", "soluble_weights", "fold_checkpoint"}
    installers |= set(evaluator.CODE_SOURCES)

    for name in evaluator.PROBE_CHECKS:
        assert name in installers or name in evaluator.CODE_REASON, name
