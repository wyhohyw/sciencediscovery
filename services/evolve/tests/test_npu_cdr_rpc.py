# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
# Licensed under the Apache License, Version 2.0 (the "License");
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sciencediscovery_evolve.npu_job import NpuJobError, RunnerNpuClient, execution_signature

pytestmark = pytest.mark.science_tags(category="ut", os="linux", arch=("amd64", "arm64"))


@pytest.mark.parametrize("acknowledged", [True, False])
def test_CDR6_003_wire_freezes_capacity_binds_a_slot_and_publishes_before_returning_score(acknowledged):
    seen = []
    structure_id = "a" * 64

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers["authorization"] == "Bearer token"
            seen.append(("GET", self.path, None))
            result = {"workers": 2} if self.path == "/capacity" else {
                "state": "completed", "result": {"stdout": json.dumps({
                    "device": "npu", "score": 1.5, "structure_id": structure_id})}}
            self.reply(200, result)

        def do_POST(self):
            assert self.headers["authorization"] == "Bearer token"
            body = self.rfile.read(int(self.headers["content-length"])).decode()
            assert self.headers["x-science-execution-signature"] == execution_signature(
                "token", self.headers["x-science-execution-timestamp"], body)
            payload = json.loads(body)
            seen.append(("POST", self.path, payload))
            if "/publish?" in self.path:
                assert payload == {"structureId": structure_id}
                self.reply(200, {"published": acknowledged})
            else:
                assert payload["cdrDeviceSlot"] == 1
                self.reply(202, {"state": "queued"})

        def reply(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-length", str(len(raw)))
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = RunnerNpuClient(url=f"http://127.0.0.1:{server.server_port}", token="token",
            execution={"code": "result = _EVOLVE_CANDIDATE"}, session_id="session", agent_id="evolve",
            timeout=5, transport="api")
        assert client.cdr_capacity() == 2
        if acknowledged:
            job, artifact = client.run('{"sequence":"AWDE"}', device_slot=1)
            assert job["status"] == "completed" and artifact["score"] == 1.5
        else:
            with pytest.raises(NpuJobError, match="publication was not confirmed"):
                client.run('{"sequence":"AWDE"}', device_slot=1)
        assert len(seen) == 4 and "/publish?" in seen[-1][1]
        values = {}
        exec(seen[1][2]["code"], values)
        assert values["result"] == {"sequence": "AWDE"}
        assert client.run("{}", lambda: True, device_slot=0)[0]["status"] == "cancelled"
        assert len(seen) == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
