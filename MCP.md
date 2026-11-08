# Set up WolvWealth in your AI app (MCP)

WolvWealth ships an [MCP](https://modelcontextprotocol.io) server, so an AI app on this machine (Claude Desktop, Claude Code, Cursor, and anything else that speaks MCP) can check your price data, read your portfolios, run optimizations, and import holdings as tools, with no copy-pasting of API docs.

**The easy way:** paste this whole file into your AI assistant and say *"set this up for me"*. It is written so the assistant can do every step. If the assistant can run commands on this machine (Claude Code, Cursor's agent), it will; if not (Claude Desktop), it will tell you exactly what to run and what to paste where.

**The manual way:** run `wolvwealth mcp --config`, paste the JSON it prints into your app's MCP configuration (it also prints where that is), restart the app.

---

## Instructions for the assistant

You are setting up WolvWealth's MCP server for the user. Work through the steps in order. Run commands yourself when you have a shell on this machine; otherwise give the user the exact command and ask for the output. Never guess a file path that a command can print for you.

### 1. Confirm WolvWealth is installed

Run `wolvwealth --help`. If the command is not found, try `python3 -m wolvwealth --help`. If neither works, WolvWealth is not installed: on Linux, macOS, or Windows via WSL run

```bash
curl -fsSL https://raw.githubusercontent.com/rramboer/WolvWealth/main/install.sh | bash
```

which installs into the user's global Python (3.10+ required) and downloads the price history. Open a new terminal afterwards so `wolvwealth` is on the PATH.

### 2. Confirm the price history exists

Run `wolvwealth refresh-data`. If prices are already cached it finishes in seconds and reports the date they end; if not, it downloads 2006-to-today prices for about 2,600 tickers (roughly 40 MB, several minutes). The MCP server deliberately never starts this download on its own, so it must have happened once before the tools are useful. Tell the user what it is doing and why it takes a while.

### 3. Get the configuration

Run

```bash
wolvwealth mcp --config
```

It prints a JSON object like

```json
{
  "mcpServers": {
    "wolvwealth": {
      "command": "/usr/bin/python3",
      "args": ["-m", "wolvwealth", "mcp"]
    }
  }
}
```

Use that output exactly. The `command` is the absolute path of the Python interpreter the package is installed in; that is what makes it work from a desktop app that does not see the user's shell PATH. Do not replace it with a bare `wolvwealth` or `python3`.

### 4. Put it where the app reads it

Ask the user which app they use if it is not obvious, then:

| App | Where the configuration goes |
|---|---|
| **Claude Desktop** | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`. Windows: `%APPDATA%\Claude\claude_desktop_config.json`. Linux: `~/.config/Claude/claude_desktop_config.json`. Create the file if it does not exist. Afterwards the user must **fully quit** Claude Desktop (not just close the window) and reopen it. |
| **Claude Code** | One command, substituting the interpreter path from step 3: `claude mcp add wolvwealth -- /usr/bin/python3 -m wolvwealth mcp`. Add `--scope user` to make it available in every project. Alternatively put the JSON in a `.mcp.json` at a project root. |
| **Cursor** | `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` in a project. Same JSON. Reload the window. |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json`. Same JSON. |
| **Anything else** | The `mcpServers` shape above is the convention almost every MCP client uses; look for "MCP servers" in the app's settings, and choose "stdio" / "command" if asked for a transport type. |

**Merging rule:** if the file already exists, add the `"wolvwealth"` entry *inside* the existing `"mcpServers"` object. Never remove the user's other servers, and make sure the result is valid JSON (no trailing commas). Show the user the final file contents.

**Windows with WolvWealth inside WSL:** Claude Desktop runs on Windows and cannot launch a Linux path directly. Use

```json
{ "mcpServers": { "wolvwealth": { "command": "wsl.exe", "args": ["-e", "/usr/bin/python3", "-m", "wolvwealth", "mcp"] } } }
```

with the interpreter path from step 3 in place of `/usr/bin/python3`.

### 5. Verify

After the app restarts, call the `market_status` tool (or ask the user to ask the assistant in that app to). A working setup answers with `as_of` (the date of the newest cached price), `age_days`, `tbill_rate`, and the universe aliases. If the answer is *"No price history on this machine yet"*, go back to step 2.

### 6. Tell the user what they have

Four tools, all reading and writing the same local SQLite database and price cache as the browser app and the `wolvwealth` command:

| Tool | What it does |
|---|---|
| `market_status` | How fresh the prices are, the current T-bill yield as a risk-free-rate reference, and an optional symbol check |
| `list_portfolios` | Every workspace and portfolio: cash, holdings, risk-free rate |
| `optimize` | The maximum-Sharpe allocation for a stored portfolio or inline holdings, with the trades to reach it (sells first) and the metrics |
| `import_holdings` | Load holdings into a portfolio from a ticker map or a brokerage CSV export; can set cash and the risk-free rate too |

And the three things to understand before acting on a result:

- **Downloaded prices are checked, and exclusions are reported.** A ticker whose downloaded history moves implausibly is quarantined (it cannot be bought), and `market_status` lists it under `quarantined` with the reason. A held position in a quarantined ticker does not fail the run: it is kept at its current size, valued at its last believable close, and reported under `unoptimized` with reason `quarantined`. In a scheduled or unattended run, check both fields before acting on trades.
- **Splits and dividends are repaired, and the repair is reported.** A split or dividend rescales the entire price history Yahoo serves, so a refresh re-downloads that ticker's history whole. `market_status` notes it under `readjusted` for 30 days — a holding whose every cached number changed since the last run changed basis, not meaning. If the re-download fails or comes back truncated, the ticker is quarantined (with the reason) rather than kept with a wrong or shortened history.
- **A symbol Yahoo throttled does not break the run.** Individual tickers routinely miss a day. Their last close is carried forward for up to 10 trading days so one missing quote cannot fail an optimization, and `market_status` and the optimize response list every carried price under `stale_prices` with its date. Past that window a held position is kept at its current size and reported under `unoptimized` with reason `no_current_price` rather than the request being refused — check that field before treating a plan as complete.
- **Prices are a local cache, not live quotes.** Every result carries `as_of`. When it is stale, `market_status` says so and the fix is `wolvwealth refresh-data` in a terminal; the running server picks up the new prices without a restart.
- **By default `optimize` may sell any holding.** `constraints.max_turnover` caps how much of the portfolio's value is sold.
- **Recent listings are left out.** A ticker with fewer than a year of prices (`min_history_days`, default 252) is skipped by universe aliases and refused by name, because a few weeks of returns extrapolated to a year would dominate the allocation. A held one is kept at its current size and reported under `unoptimized` in the result, never sold on the optimizer's say-so. `market_status` lists such tickers under `short_history`; pass a lower `min_history_days` to `optimize` only knowingly.
- **Nothing is executed.** WolvWealth produces target allocations and trade lists. Placing orders is the user's decision, or a separate broker tool's job.

---

## Troubleshooting

- **"spawn wolvwealth ENOENT", "command not found", or the server never connects.** The configuration names a bare `wolvwealth` or `python3` that the app cannot find. Use the absolute interpreter path from `wolvwealth mcp --config`.
- **The server connects, then disconnects at once, or the log mentions an "unexpected token".** Something wrote text to stdout, which MCP uses for messages. WolvWealth routes all of its own output to stderr, so this points at the interpreter or a startup hook printing something. Check by running `wolvwealth mcp` in a terminal, typing `{"jsonrpc":"2.0","id":1,"method":"ping"}` and pressing Enter: the only output must be `{"jsonrpc":"2.0","id":1,"result":{}}`. Ctrl-D ends the session.
- **The first tool call is slow.** Loading the price cache takes a few seconds, and after a price refresh the optimizer's statistics are recomputed once (about a minute). Later calls are fast; repeated identical requests are served from memory.
- **Everything is slow or the app complains about a timeout at startup.** The server answers the handshake before touching any data, so a startup timeout means the interpreter itself is slow to start (an antivirus scanning site-packages, a network home directory). Starting it once by hand usually warms it up.
- **Results say the risk-free rate was assumed to be 0%.** No rate is stored on that portfolio. Set it with `import_holdings` (`risk_free_rate`: a fraction or `"tbill"`) or on the portfolio card in the browser app.
- **Where are the logs?** The server's diagnostics go to its stderr, which the app captures: Claude Desktop writes them to `~/Library/Logs/Claude/mcp*.log` on macOS and `%APPDATA%\Claude\logs` on Windows; Claude Code shows them under `/mcp`.

## What the server is, technically

A stdio MCP server: the app launches `python -m wolvwealth mcp` as a child process and exchanges newline-delimited JSON-RPC with it over stdin/stdout. No port is opened and nothing listens on the network, so only the app that started the process can talk to it. It offers the `tools` capability only, speaks protocol revisions 2024-11-05 through 2025-11-25, needs no extra Python packages, and runs whether or not `wolvwealth serve` is up. The tool handlers call the same service functions as the JSON API ([API.md](API.md) documents the fields in detail; the tool schemas mirror them one for one).
