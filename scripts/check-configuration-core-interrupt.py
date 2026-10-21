# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
# Licensed under the Apache License, Version 2.0 (the "License");
"""Check the pinned core interrupt patch against a temporary copy of its real handler.

Run with the installed core interpreter. This never modifies an installation or
starts a WorkSwarm instance. It verifies the actual core commit method and failure
boundary; it is not a substitute for a full adapter/core restart integration test.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace


def check(source_root: Path, patch: Path) -> None:
    from openjiuwen.core.foundation.llm import AssistantMessage
    from openjiuwen.core.session.agent import Session
    from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler as OriginalHandler
    from openjiuwen.core.single_agent.interrupt.state import ToolInterruptionState, INTERRUPTION_KEY

    relative = Path("openjiuwen/core/single_agent/interrupt/handler.py")
    with tempfile.TemporaryDirectory(prefix="configuration-core-contract-") as temporary:
        target = Path(temporary)
        (target / relative).parent.mkdir(parents=True)
        shutil.copyfile(source_root / relative, target / relative)
        # No unsafe paths, no git checkout, no write to the installed dependency.
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=target, check=True, capture_output=True)
        subprocess.run(["git", "apply", str(patch)], cwd=target, check=True, capture_output=True)
        name = "_configuration_core_handler_contract"
        spec = importlib.util.spec_from_file_location(name, target / relative)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)

            async def exercise(handler_type, fail_commit=False):
                order = []
                saved = {}

                class State:
                    def update_global(self, value):
                        order.append("state")
                        saved.update(value)

                class Checkpointer:
                    async def post_agent_execute(self, inner):
                        order.append("durable_commit")
                        assert INTERRUPTION_KEY in saved
                        if fail_commit:
                            raise OSError("injected checkpoint write failure")

                class Inner:
                    def state(self):
                        return State()

                    def checkpointer(self):
                        return Checkpointer()

                async def save_contexts(session):
                    order.append("context")

                session = object.__new__(Session)
                session._inner = Inner()
                handler = handler_type(SimpleNamespace(context_engine=SimpleNamespace(save_contexts=save_contexts)))
                state = ToolInterruptionState(ai_message=AssistantMessage(content=""), iteration=1)
                invoke = SimpleNamespace(result=None)
                try:
                    result = await handler.commit_interrupt(state, None, session, invoke)
                    order.append("observable_result")
                    assert result["result_type"] == "interrupt"
                except OSError:
                    assert fail_commit and invoke.result is None
                    return order
                assert not fail_commit
                return order

            original = asyncio.run(exercise(OriginalHandler))
            assert original == ["context", "state", "observable_result"], original
            patched = asyncio.run(exercise(module.ToolInterruptHandler))
            assert patched == ["context", "state", "durable_commit", "observable_result"], patched
            failure = asyncio.run(exercise(module.ToolInterruptHandler, fail_commit=True))
            assert failure == ["context", "state", "durable_commit"], failure
            print("PASS: pinned source patch applies; real core commits before returning the interrupt; checkpoint failure exposes no result")
        finally:
            sys.modules.pop(name, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path, help="Directory containing the installed openjiuwen package")
    parser.add_argument("--patch", required=True, type=Path)
    arguments = parser.parse_args()
    check(arguments.source_root.resolve(), arguments.patch.resolve())
