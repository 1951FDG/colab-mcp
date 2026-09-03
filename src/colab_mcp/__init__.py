# Copyright 2026 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import asyncio
import datetime
import logging
import tempfile
import sys
import webbrowser

from fastmcp import FastMCP
from fastmcp.utilities import logging as fastmcp_logger

from colab_mcp.session import ColabSessionProxy, NOT_CONNECTED_MSG


mcp = FastMCP(name="ColabMCP")

# These will be set during main_async() startup
_proxy_client = None
_session_mcp = None
_colab_client = None  # For runtime API (assign/unassign GPU)
_runtime_tool = None  # For executing code on the Colab runtime via Jupyter kernel


# Startup tool registration (invisible tools fix), change_runtime, and _forward_or_stub
# from SebastianGilPinzon/colab-mcp — https://github.com/SebastianGilPinzon/colab-mcp
async def _forward_or_stub(tool_name: str, arguments: dict) -> str:
    """Forward a tool call to the browser if connected, otherwise return stub message."""
    if _proxy_client is not None and _proxy_client.is_connected():
        try:
            result = await _proxy_client.proxy_mcp_client.call_tool(tool_name, arguments)
            # Extract text from result
            if hasattr(result, 'content'):
                return "\n".join(c.text for c in result.content if hasattr(c, 'text'))
            return str(result)
        except Exception as e:
            return f"Error calling {tool_name}: {e}. Try calling open_colab_browser_connection to reconnect."
    return NOT_CONNECTED_MSG


@mcp.tool()
async def open_colab_browser_connection(notebook_url: str = "") -> str:
    """Opens a connection to a Google Colab browser session and unlocks notebook editing tools.

    Args:
        notebook_url: Optional Colab notebook URL to open for this connection. Must be on
            colab.research.google.com (or the alt domain) — non-Colab origins are ignored
            and fall back to the server-launch default. If empty, uses the URL configured
            via the ``-n/--notebook`` server flag (or a scratch notebook).

    Returns whether the connection attempt succeeded.
    """
    if _proxy_client is not None and _proxy_client.is_connected():
        return "Already connected to Colab."

    if _proxy_client is None:
        return "Server not initialized. Please wait and try again."

    if notebook_url:
        _proxy_client.wss.notebook_url = notebook_url
    colab_url = _proxy_client.wss.get_colab_url()

    # Remote connection CLI args (-n/-H/-P/--no-browser) from ZeroPointSix/colab-mcp
    # https://github.com/ZeroPointSix/colab-mcp
    if _session_mcp is not None and _session_mcp.no_browser:
        print(
            f"\nOpen this URL in your browser to connect to Colab:\n  {colab_url}\n",
            file=sys.stderr,
        )
    else:
        webbrowser.open_new(colab_url)

    # Wait for browser to connect
    await _proxy_client.await_proxy_connection()

    if _proxy_client.is_connected():
        tool_names = await _proxy_client.await_tools_ready()
        tools_text = ", ".join(tool_names) if tool_names else "none discovered"
        return f"Connection successful. Available notebook tools: {tools_text}. You can now create, edit, and execute cells in the Colab notebook."
    else:
        return "Connection timed out. Please make sure you have a Colab notebook open in your browser and try again."


@mcp.tool()
async def add_code_cell(code: str = "", cellIndex: int = 0, language: str = "python") -> str:
    """Add a new code cell to the Colab notebook. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("add_code_cell", {"code": code, "cellIndex": cellIndex, "language": language})


@mcp.tool()
async def add_text_cell(content: str = "", cellIndex: int = -1) -> str:
    """Add a new text/markdown cell to the Colab notebook. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("add_text_cell", {"content": content, "cellIndex": cellIndex})


@mcp.tool()
async def execute_cell(cellId: str = "") -> str:
    """Execute a cell in the Colab notebook. Pass cellId (from add_code_cell or get_cells result). Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("run_code_cell", {"cellId": cellId})


@mcp.tool()
async def update_cell(cellId: str = "", content: str = "") -> str:
    """Update the contents of an existing cell in the Colab notebook. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("update_cell", {"cellId": cellId, "content": content})


@mcp.tool()
async def get_cells() -> str:
    """Read all cells (id, type, source, outputs) from the Colab notebook. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("get_cells", {})


@mcp.tool()
async def move_cell(cellId: str = "", cellIndex: int = 0) -> str:
    """Move an existing cell to a new index in the Colab notebook. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("move_cell", {"cellId": cellId, "cellIndex": cellIndex})


@mcp.tool()
async def delete_cell(cellId: str = "") -> str:
    """Delete a cell from the Colab notebook. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("delete_cell", {"cellId": cellId})


@mcp.tool()
async def change_runtime(accelerator: str = "T4") -> str:
    """Change the Colab runtime to use a specific GPU accelerator. Valid values: NONE, T4, L4, A100. Requires OAuth setup (first time opens browser for consent). Configure with --client-oauth-config."""
    if _colab_client is None:
        return "Runtime API not initialized. Start with --client-oauth-config flag pointing to your OAuth client secrets JSON."
    try:
        from colab_mcp.client import Accelerator, Variant
        import uuid

        acc = Accelerator(accelerator)
        variant = Variant.GPU if acc != Accelerator.NONE else Variant.DEFAULT
        notebook_hash = str(uuid.uuid4())

        # Unassign current VM if any
        try:
            assignments = _colab_client.list_assignments()
            for a in assignments:
                _colab_client.unassign(a.endpoint)
        except Exception as e:
            logging.warning("Failed to unassign existing runtime(s): %s", e)

        # Assign new VM
        result = _colab_client.assign(notebook_hash, variant, acc)
        return f"Runtime changed to {accelerator}. Endpoint: {result.endpoint}. Use open_colab_browser_connection to connect to the new runtime."
    except Exception as e:
        return f"Failed to change runtime: {e}"


def init_logger(logdir):
    log_filename = datetime.datetime.now().strftime(
        f"{logdir}/colab-mcp.%Y-%m-%d_%H-%M-%S.log"
    )
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s",
        datefmt="%m/%d/%Y %I:%M:%S %p",
        filename=log_filename,
        level=logging.INFO,
    )
    fastmcp_logger.get_logger("colab-mcp").info("logging to %s" % log_filename)


def parse_args(v):
    parser = argparse.ArgumentParser(
        description="ColabMCP is an MCP server that lets you interact with Colab."
    )
    parser.add_argument(
        "-l",
        "--log",
        help="if set, use this directory as a location for logfiles (if unset, will log to %s/colab-mcp-logs/)"
        % tempfile.gettempdir(),
        action="store",
        default=tempfile.mkdtemp(prefix="colab-mcp-logs-"),
    )
    parser.add_argument(
        "-p",
        "--enable-proxy",
        help="if set, enable the runtime proxy (enabled by default).",
        action="store_true",
        default=True,
    )
    # Remote connection CLI args (-n/-H/-P/--no-browser) from ZeroPointSix/colab-mcp
    # https://github.com/ZeroPointSix/colab-mcp
    parser.add_argument(
        "-n",
        "--notebook",
        help="URL or path of the Colab notebook to open (default: empty scratch notebook).",
        action="store",
        default=None,
    )
    parser.add_argument(
        "-H",
        "--host",
        help="Host address for the WebSocket server to bind to (default: localhost).",
        action="store",
        default="localhost",
    )
    parser.add_argument(
        "-P",
        "--port",
        help="Port for the WebSocket server to bind to (default: 0, random port).",
        action="store",
        default=0,
        type=int,
    )
    parser.add_argument(
        "--no-browser",
        help="Do not auto-open a browser session. Instead, print the connection URL to stderr.",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--client-oauth-config",
        help="Path to OAuth client secrets JSON for Colab API access (enables change_runtime tool).",
        action="store",
        default=None,
    )
    # --enable-runtime from anthony-maio/colab-mcp — https://github.com/anthony-maio/colab-mcp
    parser.add_argument(
        "-r",
        "--enable-runtime",
        help="Enable runtime tools (execute_code) for running code on a Colab Jupyter kernel without a browser. Requires --client-oauth-config.",
        action="store_true",
        default=False,
    )
    return parser.parse_args(v)


async def main_async():
    global _proxy_client, _session_mcp, _colab_client, _runtime_tool
    args = parse_args(sys.argv[1:])
    init_logger(args.log)

    if args.enable_proxy:
        logging.info("enabling session proxy tools")
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            logging.warning(
                f"WebSocket server binding to {args.host}, which exposes it to the network. "
                "Ensure your firewall is configured appropriately."
            )
        _session_mcp = ColabSessionProxy(
            notebook_url=args.notebook,
            host=args.host,
            port=args.port,
            no_browser=args.no_browser,
        )
        await _session_mcp.start_proxy_server()
        _proxy_client = _session_mcp.proxy_client

    if args.client_oauth_config:
        try:
            from colab_mcp.auth import get_credentials
            from colab_mcp.client import ColabClient, Prod
            logging.info("initializing Colab API client with OAuth")
            session = get_credentials(args.client_oauth_config)
            _colab_client = ColabClient(Prod(), session)
            logging.info("Colab API client ready")
        except Exception as e:
            logging.warning(f"Failed to initialize Colab API client: {e}")

    if args.enable_runtime:
        # execute_code tool: run code on a Colab Jupyter kernel without a browser.
        # Ported from anthony-maio/colab-mcp — https://github.com/anthony-maio/colab-mcp
        if not args.client_oauth_config:
            logging.warning(
                "--enable-runtime requires --client-oauth-config; runtime tools disabled."
            )
        else:
            try:
                from colab_mcp.auth import get_credentials
                from colab_mcp import runtime as colab_runtime
                # Pre-fetch credentials so we fail fast if OAuth isn't set up.
                get_credentials(args.client_oauth_config)
                _runtime_tool = colab_runtime.ColabRuntimeTool()
                mcp.mount(_runtime_tool.mcp, prefix="runtime")
                logging.info("enabling runtime tools (runtime_execute_code)")
            except Exception as e:
                logging.warning(f"Failed to initialize runtime tools: {e}")

    try:
        await mcp.run_async()

    finally:
        if args.enable_proxy and _session_mcp:
            await _session_mcp.cleanup()
        if _runtime_tool is not None:
            try:
                _runtime_tool.stop()
            except Exception as e:
                logging.warning(f"runtime cleanup failed: {e}")


def main() -> None:
    asyncio.run(main_async())
