"""最小 ComfyUI 假服务端，只为端到端验证 seed-image 的调用路径。仅测试用。"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

STATE: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - 静音
        pass

    def _send(self, code: int, payload, *, raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/octet-stream" if raw else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if self.path == "/api/upload/image":
            name = re.search(rb'filename="([^"]+)"', raw)
            return self._send(200, {"name": name.group(1).decode() if name else "x.png", "subfolder": "", "type": "input"})
        if self.path == "/api/prompt":
            body = json.loads(raw)
            job_id = body["prompt_id"]
            steps = body["prompt"].get("7", {}).get("inputs", {}).get("steps")
            STATE[job_id] = {"polls": 0, "fail": steps == 999, "never": steps == -1}
            return self._send(200, {"prompt_id": job_id, "number": 1, "node_errors": {}})
        if match := re.fullmatch(r"/api/jobs/([^/]+)/cancel", self.path):
            STATE.setdefault(match.group(1), {"polls": 0, "fail": False})["cancelled"] = True
            return self._send(200, {"cancelled": True})
        return self._send(404, {"error": "not found"})

    def do_GET(self):
        if match := re.fullmatch(r"/api/jobs/([^/?]+)", self.path):
            job_id = match.group(1)
            state = STATE.get(job_id)
            if state is None:
                return self._send(404, {"error": "Job not found"})
            state["polls"] += 1
            if state.get("never"):
                return self._send(200, {"id": job_id, "status": "in_progress", "outputs_count": 0})
            if state["polls"] < 2:
                return self._send(200, {"id": job_id, "status": "in_progress", "outputs_count": 0})
            if state["fail"]:
                return self._send(200, {
                    "id": job_id, "status": "failed", "outputs_count": 0,
                    "execution_error": {"node_id": "7", "exception_message": "假的执行错误"},
                })
            return self._send(200, {
                "id": job_id, "status": "completed", "outputs_count": 1,
                "outputs": {"9": {"images": [{"filename": "ComfyUI_00001_.png", "subfolder": "", "type": "output"}]}},
            })
        if self.path.startswith("/api/view"):
            return self._send(200, None, raw=PNG)
        if self.path.startswith("/api/jobs"):
            return self._send(200, {"jobs": [{"id": k, "status": "completed", "outputs_count": 1} for k in STATE],
                                    "pagination": {"offset": 0, "limit": 20, "total": len(STATE), "has_more": False}})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8199), Handler).serve_forever()
