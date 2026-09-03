# colab-mcp (cdundes-sb fork)

An MCP server for bridging your local AI agent to a Google Colab session running in the browser.

This fork merges two community improvements into the official repo:
- **Invisible tools fix** — all tools appear at startup, no `notifications/tools/list_changed` required
- **Remote connection support** — specify a notebook URL, fixed port, and headless (`--no-browser`) mode
- **`change_runtime` tool** — programmatically assign GPU accelerators via OAuth

---

## Credits

This fork would not exist without the work of:

| Contributor | Fork | Contribution |
|---|---|---|
| **Google Colab team** | [googlecolab/colab-mcp](https://github.com/googlecolab/colab-mcp) | Original project, core WebSocket/proxy architecture, Apache 2.0 license |
| **guanshangshui** | [ZeroPointSix/colab-mcp](https://github.com/ZeroPointSix/colab-mcp) | CLI args (`-n/-H/-P/--no-browser`), `get_colab_url()`, SSH tunnel / remote connection support, stdout→stderr fix |
| **Sebastian Gil Pinzon** | [SebastianGilPinzon/colab-mcp](https://github.com/SebastianGilPinzon/colab-mcp) | Invisible tools fix (startup registration), `change_runtime` tool, OAuth token caching (`auth.py`), Colab API client (`client.py`), `await_tools_ready()`, Windows port fix |
| **Anthony Maio** | [anthony-maio/colab-mcp](https://github.com/anthony-maio/colab-mcp) | `execute_code` tool / `runtime.py` (run Python on a Colab Jupyter kernel without a browser), `--enable-runtime` flag, test suite (`auth_test.py`, `client_test.py`, `runtime_test.py`) |
| **Claude Code** | [Anthropic](https://claude.ai/code) | Assembled and merged this fork |

---

## Why this fork?

The official `googlecolab/colab-mcp` has two well-known limitations:

1. **Invisible tools** ([#54](https://github.com/googlecolab/colab-mcp/discussions/54), [#67](https://github.com/googlecolab/colab-mcp/discussions/67)) — Only `open_colab_browser_connection` appears at startup. The notebook tools (`add_code_cell`, `execute_cell`, etc.) are hidden until a browser connects, because the server relies on `notifications/tools/list_changed` which many clients don't support (some Claude Code versions, OpenAI Codex, Kiro IDE).

2. **No programmatic GPU control** — Google [removed](https://github.com/googlecolab/colab-mcp/discussions/41) `--enable-runtime` entirely.

This fork fixes both, combining the approaches from ZeroPointSix and SebastianGilPinzon.

## Changes on top of the source forks

Beyond merging the two upstreams, this fork adds:

- **Security: notebook URL origin validation** — `get_colab_url()` now only accepts paths or URLs whose origin is `colab.research.google.com` / `colab.google.com`. Anything else logs a warning and falls back to the scratch notebook. Without this, passing `--notebook https://attacker.example/page` would put the proxy auth token in that page's URL fragment, where client-side JS could exfiltrate it and drive the local WebSocket.
- **Reliability**: dropped three tool stubs (`get_cells`, `delete_cell`, `move_cell`) whose parameter signatures were guessed and never tested against the browser-side tools — they can be reintroduced once verified.
- **Cleanup**: removed the now-unused `ColabProxyMiddleware` (~70 lines of dead code from the pre-startup-registration design); fixed unassign-failure swallowing in `change_runtime`; tightened `None` checks around startup globals.
- **Architecture note**: anthony-maio's `runtime.py` / `execute_code` is gated behind `--enable-runtime` (rather than auto-enabled with OAuth) so the user can opt out of mounting the Jupyter kernel client even when `change_runtime` is configured.

---

## Available Tools

| Tool | Requires browser connection | Requires OAuth | Description |
|------|-----------------------------|----------------|-------------|
| `open_colab_browser_connection` | — | — | Connect to a Colab notebook in your browser |
| `add_code_cell` | Yes | — | Add a code cell to the notebook |
| `add_text_cell` | Yes | — | Add a markdown/text cell |
| `execute_cell` | Yes | — | Run a cell by ID or index |
| `update_cell` | Yes | — | Edit an existing cell's contents |
| `change_runtime` | — | Yes | Assign GPU: `T4`, `L4`, `A100`, or `NONE` |
| `runtime_execute_code` | — | Yes (and `--enable-runtime`) | Run Python on the Colab Jupyter kernel directly — no browser required |

All tools are registered at startup and visible immediately to any MCP client.

---

## Setup

### 1. Install `uv`

```bash
pip install uv
```

### 2. Configure your MCP client

Add to your `mcp.json` (or equivalent config):

```json
{
  "mcpServers": {
    "colab-mcp": {
      "command": "uvx",
      "args": ["git+https://github.com/cdundes-sb/colab-mcp"],
      "timeout": 30
    }
  }
}
```

### 3. (Optional) Enable `change_runtime` with OAuth

To use `change_runtime` for programmatic GPU assignment:

1. Create an OAuth client ID in [Google Cloud Console](https://console.cloud.google.com/) (Desktop app type)
2. Download the client secrets JSON
3. Add `--client-oauth-config /path/to/client_secrets.json` to your args

```json
{
  "mcpServers": {
    "colab-mcp": {
      "command": "uvx",
      "args": [
        "git+https://github.com/cdundes-sb/colab-mcp",
        "--client-oauth-config", "/path/to/client_secrets.json"
      ],
      "timeout": 30
    }
  }
}
```

The first run opens a browser for OAuth consent. The token is cached at `~/.colab-mcp-auth-token.json` for all future runs.

---

## CLI Reference

```
colab-mcp [OPTIONS]

Options:
  -l, --log DIR               Directory for log files
  -p, --enable-proxy          Enable runtime proxy (default: on)
  -n, --notebook URL          Colab notebook URL or path to open
                              (default: empty scratch notebook)
  -H, --host HOST             WebSocket server bind address
                              (default: localhost)
  -P, --port PORT             WebSocket server port
                              (default: 0, random)
  --no-browser                Print connection URL to stderr instead of
                              opening a browser (useful for SSH/remote)
  --client-oauth-config PATH  Path to OAuth client secrets JSON
                              (enables change_runtime tool)
  -r, --enable-runtime        Mount runtime tools (runtime_execute_code) for
                              running code on a Colab Jupyter kernel directly,
                              without a browser. Requires --client-oauth-config.
```

### Remote / SSH usage

Run with `--no-browser` and a fixed port, then forward that port over SSH:

```bash
# On the remote machine
colab-mcp --no-browser -P 8765

# On your local machine
ssh -L 8765:localhost:8765 user@remote
# Then open the printed URL in your local browser
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
