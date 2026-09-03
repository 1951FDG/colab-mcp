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

import asyncio
from collections.abc import AsyncIterator
import contextlib
from contextlib import AsyncExitStack
import logging
from fastmcp import FastMCP, Client
from fastmcp.client.transports import ClientTransport
from mcp.client.session import ClientSession

from colab_mcp.websocket_server import ColabWebSocketServer

logger = logging.getLogger(__name__)

UI_CONNECTION_TIMEOUT = 60.0  # secs

# From SebastianGilPinzon/colab-mcp — https://github.com/SebastianGilPinzon/colab-mcp
TOOLS_READY_TIMEOUT = 10.0  # secs
TOOLS_READY_POLL_INTERVAL = 0.5  # secs

INJECTED_TOOL_NAME = "open_colab_browser_connection"

NOT_CONNECTED_MSG = (
    "Not connected to a Google Colab browser session. "
    "Please call open_colab_browser_connection first to establish a connection, "
    "then retry this tool."
)


def _make_stub_server() -> FastMCP:
    """Empty FastMCP server used as fallback when no browser is connected.

    The user-facing tool stubs are registered directly on the top-level
    ``mcp`` server in ``__init__.py``; this server is just a placeholder
    target for the stubbed proxy client when the browser is not yet
    connected.
    """
    return FastMCP("colab-notebook-stubs")


class ColabTransport(ClientTransport):
    def __init__(self, wss: ColabWebSocketServer):
        self.wss = wss

    @contextlib.asynccontextmanager
    async def connect_session(self, **session_kwargs) -> AsyncIterator[ClientSession]:
        async with ClientSession(
            self.wss.read_stream, self.wss.write_stream, **session_kwargs
        ) as session:
            yield session

    def __repr__(self) -> str:
        return "<ColabSessionProxyTransport>"


class ColabProxyClient:
    def __init__(self, wss: ColabWebSocketServer):
        self.wss = wss
        self.stubbed_mcp_client = Client(_make_stub_server())
        self.proxy_mcp_client: Client | None = None
        self._exit_stack = AsyncExitStack()
        self._start_task = None

    def is_connected(self):
        return self.wss.connection_live.is_set() and self.proxy_mcp_client is not None

    async def await_proxy_connection(self):
        with contextlib.suppress(asyncio.TimeoutError):
            # wait for the connection to be live and for the proxy client to fully initialize
            connection_tasks = asyncio.gather(
                self.wss.connection_live.wait(), self._start_task
            )
            await asyncio.wait_for(
                connection_tasks,
                timeout=UI_CONNECTION_TIMEOUT,
            )

    # From SebastianGilPinzon/colab-mcp — https://github.com/SebastianGilPinzon/colab-mcp
    async def await_tools_ready(self) -> list[str]:
        """Poll the proxy client until remote tools are available."""
        if not self.is_connected():
            return []
        elapsed = 0.0
        while elapsed < TOOLS_READY_TIMEOUT:
            try:
                tools = await self.proxy_mcp_client.list_tools()
                if tools:
                    return [t.name for t in tools]
            except Exception:
                pass
            await asyncio.sleep(TOOLS_READY_POLL_INTERVAL)
            elapsed += TOOLS_READY_POLL_INTERVAL
        return []

    def client_factory(self):
        if self.is_connected():
            return self.proxy_mcp_client
        # return a client mapped to a stubbed mcp server if there is no session proxy
        return self.stubbed_mcp_client

    async def _start_proxy_client(self):
        # blocks until a websocket connection is made successfully
        self.proxy_mcp_client = await self._exit_stack.enter_async_context(
            Client(ColabTransport(self.wss))
        )

    async def __aenter__(self):
        self._start_task = asyncio.create_task(self._start_proxy_client())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._start_task:
            self._start_task.cancel()
        await self._exit_stack.aclose()


class ColabSessionProxy:
    # Remote connection args (notebook_url, host, port, no_browser) from:
    # ZeroPointSix/colab-mcp — https://github.com/ZeroPointSix/colab-mcp
    def __init__(
        self,
        notebook_url: str | None = None,
        host: str = "localhost",
        port: int = 0,
        no_browser: bool = False,
    ):
        self.notebook_url = notebook_url
        self.host = host
        self.port = port
        self.no_browser = no_browser
        self._exit_stack = AsyncExitStack()
        self.proxy_client: ColabProxyClient | None = None
        self.wss: ColabWebSocketServer | None = None

    async def start_proxy_server(self):
        self.wss = await self._exit_stack.enter_async_context(
            ColabWebSocketServer(
                host=self.host,
                port=self.port,
                notebook_url=self.notebook_url,
            )
        )
        self.proxy_client = await self._exit_stack.enter_async_context(
            ColabProxyClient(self.wss)
        )

    async def cleanup(self):
        await self._exit_stack.aclose()
