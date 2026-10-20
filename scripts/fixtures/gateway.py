"""Synthetic OpenAI-compatible gateway for the real-runtime smoke test only."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        payload = json.dumps({"data": [{"id": "smoke-model", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if any(m.get("role") == "tool" for m in request.get("messages", [])):
            delta = {"role": "assistant", "content": "Smoke test completed."}
            finish = "stop"
        else:
            functions = [t.get("function", {}) for t in request.get("tools", [])]
            print("available tools:", [f.get("name") for f in functions], flush=True)
            tool = next((f for f in functions if f.get("name") in ("write", "write_file")), None)
            if tool:
                fields = tool.get("parameters", {}).get("properties", {})
                path_key = "file_path" if "file_path" in fields else "path"
                arguments = {
                    path_key: "/workspace/model-result.txt",
                    "content": "created via model tool call\n",
                }
            else:
                tool = next((f for f in functions if f.get("name") in ("bash", "exec_command")), None)
                if tool is None:
                    self.send_error(400, "No supported smoke-test tool")
                    return
                fields = tool.get("parameters", {}).get("properties", {})
                command_key = "command" if "command" in fields else "cmd"
                arguments = {
                    command_key: "printf 'created via model tool call\\n' > /workspace/model-result.txt"
                }
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_smoke",
                        "type": "function",
                        "function": {"name": tool["name"], "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish = "tool_calls"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for d, reason in [(delta, None), ({}, finish)]:
            event = {
                "id": "chatcmpl-smoke",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "smoke-model",
                "choices": [{"index": 0, "delta": d, "finish_reason": reason}],
            }
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


ThreadingHTTPServer(("0.0.0.0", 8000), Gateway).serve_forever()
