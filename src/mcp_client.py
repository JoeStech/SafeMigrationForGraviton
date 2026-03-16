"""Lightweight MCP (Model Context Protocol) stdio client.

Spawns the Arm MCP server as a local subprocess and communicates via
JSON-RPC over stdin/stdout.  Designed to run inside a container-image
Lambda where the MCP server code is baked into the image.
"""

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# Where the MCP server lives inside the Lambda container image
MCP_SERVER_DIR = os.environ.get("MCP_SERVER_DIR", "/opt/arm-mcp")
MCP_VENV_PYTHON = os.environ.get("MCP_VENV_PYTHON", "/opt/arm-mcp/.venv/bin/python")
MCP_SERVER_SCRIPT = os.environ.get("MCP_SERVER_SCRIPT", "/opt/arm-mcp/server.py")

_request_id = 0


def _next_id():
    global _request_id
    _request_id += 1
    return _request_id


class McpSubprocessClient:
    """Communicate with an MCP server over subprocess stdin/stdout."""

    def __init__(self, timeout=90):
        self.timeout = timeout
        self._proc = None

    def start(self):
        """Spawn the MCP server subprocess."""
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        env["DISABLE_MLFLOW_INTEGRATION"] = "TRUE"
        # LD_PRELOAD must be set before the subprocess starts so the shim
        # is loaded by the dynamic linker before any C library calls happen.
        shim = "/app/cpu_shim.so"
        if os.path.exists(shim):
            existing = env.get("LD_PRELOAD", "")
            env["LD_PRELOAD"] = f"{shim}:{existing}" if existing else shim
        self._proc = subprocess.Popen(
            [MCP_VENV_PYTHON, "-u", MCP_SERVER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=MCP_SERVER_DIR,
            env=env,
        )
        self._initialize()

    def _send(self, msg):
        data = json.dumps(msg).encode("utf-8") + b"\n"
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    def _recv(self):
        """Read one JSON-RPC response line from stdout."""
        line = self._proc.stdout.readline()
        if not line:
            stderr = self._proc.stderr.read().decode("utf-8", errors="replace")
            raise ConnectionError(f"MCP server closed stdout. stderr: {stderr[:3000]}")
        return json.loads(line.decode("utf-8"))

    def _request(self, method, params=None):
        msg = {"jsonrpc": "2.0", "id": _next_id(), "method": method}
        if params:
            msg["params"] = params
        self._send(msg)
        # Read responses, skipping notifications (no "id" field)
        while True:
            resp = self._recv()
            if "id" in resp:
                if "error" in resp:
                    raise RuntimeError(f"MCP error: {resp['error']}")
                return resp.get("result")

    def _initialize(self):
        result = self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "safemigration-analyze", "version": "1.0.0"},
        })
        # Send initialized notification
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self):
        result = self._request("tools/list")
        return result.get("tools", [])

    def call_tool(self, name, arguments=None):
        return self._request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

    def close(self):
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                if self._proc:
                    self._proc.kill()
            self._proc = None


def get_mcp_client():
    """Create, start, and return an MCP subprocess client."""
    client = McpSubprocessClient()
    client.start()
    return client


def mcp_tools_to_bedrock_tools(mcp_tools):
    """Convert MCP tool definitions to Bedrock Converse toolSpec format.

    Bedrock rejects schemas with 'additionalProperties', so we strip it.
    """
    bedrock_tools = []
    for tool in mcp_tools:
        schema = tool.get("inputSchema", {"type": "object", "properties": {}})
        # Bedrock Converse doesn't support additionalProperties in tool schemas
        schema = {k: v for k, v in schema.items() if k != "additionalProperties"}
        bedrock_tools.append({
            "toolSpec": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "inputSchema": {"json": schema},
            }
        })
    return bedrock_tools
