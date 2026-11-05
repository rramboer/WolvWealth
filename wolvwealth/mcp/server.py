"""The MCP methods (initialize, tools/list, tools/call) over the JSON-RPC transport, plus the client config.

Protocol reference: https://modelcontextprotocol.io/specification. Only the
tools capability is offered, so clients asking for resources or prompts get
a clean "method not found", which the protocol tells them to expect for
capabilities a server did not advertise.
"""

import contextlib
import importlib.metadata
import json
import os
import pathlib
import sys

from wolvwealth.config import REPO_ROOT
from wolvwealth.domain.errors import DomainError
from wolvwealth.mcp import protocol, tools
from wolvwealth.services.container import Services

#: Protocol revisions this server speaks. Nothing in tools/list or tools/call
#: differs between them for a tools-only server. A client's requested
#: revision is echoed when known; otherwise the newest here is offered.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

#: Handed to the client at the handshake; the model sees it with the tool list.
INSTRUCTIONS = (
    "WolvWealth is a portfolio optimizer running on this machine. Typical flow: market_status (how fresh are the "
    "prices, is the T-bill rate known), list_portfolios (what the user has stored), then optimize (target allocation "
    "and the trades to reach it, sells first). Prices are a local cache, not live quotes: every result carries as_of, "
    "the date it was priced at; mention it when presenting trades. By default optimize may sell any holding; pass "
    "constraints.max_turnover to limit that. WolvWealth never places orders. Errors name the field at fault and a "
    "remedy, so fix the request and retry."
)


def version_string() -> str:
    """Return the installed package version, for serverInfo."""
    try:
        return importlib.metadata.version("wolvwealth")
    except importlib.metadata.PackageNotFoundError:
        return "0"


class McpServer:
    """Method handlers bound to one Services object."""

    def __init__(self, services: Services, log=None) -> None:
        """Remember the services; ``log`` receives diagnostics (never stdout in stdio mode)."""
        self.services = services
        self.log = log or (lambda message: print(message, file=sys.stderr))

    def methods(self) -> dict:
        """Return the method-name-to-handler map for ``protocol.dispatch``."""
        return {
            "initialize": self.initialize,
            "notifications/initialized": lambda _params: None,
            "notifications/cancelled": lambda _params: None,
            "ping": lambda _params: {},
            "tools/list": self.list_tools,
            "tools/call": self.call_tool,
        }

    @staticmethod
    def initialize(params: dict) -> dict:
        """Agree on a protocol revision and say what this server offers."""
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "wolvwealth",
                "title": "WolvWealth portfolio optimizer",
                "version": version_string(),
            },
            "instructions": INSTRUCTIONS,
        }

    @staticmethod
    def list_tools(_params: dict) -> dict:
        """Every tool with its schema and annotations."""
        return {"tools": [tool.describe() for tool in tools.TOOLS]}

    def call_tool(self, params: dict) -> dict:
        """Run one tool. Domain errors are tool results (``isError``), so the model reads and acts on them."""
        name = params.get("name") if isinstance(params, dict) else None
        tool = tools.BY_NAME.get(name)
        if tool is None:
            raise protocol.RpcError(protocol.INVALID_PARAMS, f"Unknown tool: {name!r}. Call tools/list.")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise protocol.RpcError(protocol.INVALID_PARAMS, "arguments must be an object.")
        try:
            result = tool.handler(self.services, arguments)
        except DomainError as error:
            text = error.message
            if error.details:
                text += "\n" + json.dumps(error.details, indent=2)
            return {"content": [{"type": "text", "text": text}], "isError": True}
        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            "structuredContent": result,
            "isError": False,
        }


def run(services: Services, stdin=None, stdout=None, log=None) -> int:
    """Serve MCP over stdio until the client closes the pipe.

    stdout belongs to the protocol while this runs: one stray ``print``
    (a refresh message, a library's progress line) would corrupt the stream
    and end the session with a baffling client-side error. So Python-level
    stdout is redirected to stderr for the duration and the protocol writes
    to the original binary stream directly.
    """
    protocol_out = stdout if stdout is not None else sys.stdout.buffer
    protocol_in = stdin if stdin is not None else sys.stdin.buffer
    server = McpServer(services, log)
    with contextlib.redirect_stdout(sys.stderr):
        protocol.serve(server.methods(), protocol_in, protocol_out, server.log)
    return 0


def client_config() -> dict:
    """Return the ``mcpServers`` entry an AI app needs, with no reliance on PATH.

    The interpreter that runs this command is the one the package is
    installed in, so ``python -m wolvwealth mcp`` by absolute path works from
    any app regardless of its environment. Desktop apps launched from a dock
    do not see a shell's PATH, which is how a bare ``wolvwealth`` fails.
    """
    return {"mcpServers": {"wolvwealth": {"command": sys.executable, "args": ["-m", "wolvwealth", "mcp"]}}}


def claude_desktop_config_path() -> pathlib.Path:
    """Where Claude Desktop reads its MCP configuration on this platform."""
    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home())) / "Claude" / "claude_desktop_config.json"
    return pathlib.Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def config_guidance() -> str:
    """Where to put the config and how to verify it, for a person reading a terminal."""
    python = sys.executable
    return (
        "Paste the JSON above into your AI app's MCP configuration (merge it into an existing mcpServers block):\n"
        f"  Claude Desktop:  {claude_desktop_config_path()}  (then fully quit and reopen the app)\n"
        f"  Claude Code:     claude mcp add wolvwealth -- {python} -m wolvwealth mcp\n"
        "  Cursor:          ~/.cursor/mcp.json, same JSON\n"
        "Then ask the assistant to call market_status. Step-by-step instructions written for an AI assistant to\n"
        f"follow on your behalf: {REPO_ROOT / 'MCP.md'}"
    )
