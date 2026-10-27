# CLAUDE.md - ctrlpi-cli

This file provides guidance to Claude Code when working with this repository.

## The README is locked

`README.md` is done. Do not edit it without the user's explicit confirmation, even for a small wording or formatting fix, and even to keep it in sync with another project's README.

## What This Is

A pure Python, zero-dependency command-line interface (`ctrlpi-cli.py`) for interacting with ctrlPi REST agents.

## Architecture

- **ctrlpi-cli.py**: The main CLI script. Handles parsing, HTTP requests (via `urllib`), and DNS resolution.
- **test.py**: Test suite. Starts `mock_agent.py` on port 12314 and calls `ctrlpi-cli.py` through `subprocess`, plus a few in-process checks of its functions. No real agent is needed.
- **mock_agent.py**: A mock agent shared with other projects' tests; every copy is identical, so change them together.

## Tests

`python test.py` (no dependencies; CI runs it on Ubuntu). It starts `mock_agent.py` on `127.0.0.1:12314` and runs
`ctrlpi-cli.py` against it through `subprocess`, plus a few in-process checks of its functions, all with a temporary
`--config` folder. `mock_agent.py` answers the agents' REST API from memory; ctrlpi-lab, mcp-bridge, homekit-bridge
and matter-bridge keep identical copies, so change them all together.

## Config folder (`--config`)

Config files (`cli-config.json` with the default host, `agents-config.json` with the shared agent
list) live in `~/.ctrlpi/`, or in the folder given by `--config <dir>`, the same flag ctrlpi-lab and
mcp-bridge take. `pop_flag()` strips it (and `--api-key`) from `sys.argv` (it may appear anywhere) and sets the
module-level `CONFIG_DIR` that `get_config_path()` reads. `test.py` passes `--config <tempdir>` to
every CLI run and sets `mod.CONFIG_DIR` on in-process loads, so the tests never touch the real
`~/.ctrlpi`. Keep it that way: a new test that writes config must go through that temp folder.

## Network Discovery (`scan`)

- The `scan` command probes the /24 in parallel (48 at a time) and names hosts by reverse DNS, plus Bonjour/mDNS and SMB on macOS.
- Moved agents are repaired first (`reconcile_agents()`: only when a key works at the new address; duplicate claims print a `[SECURITY]` warning), then new ones are added (`search_and_add_agents()`: default key if accepted, else an empty `api_key`).

## Keys

A saved agent's `api_key` comes from `agents-config.json`, anything else gets the default `your-secret-key`; `--api-key` overrides both for one command. `remember_agent()` saves a key or name the agent just accepted through `config update`/`config load`: it updates the entry at the address used, or the one with the agent's `/hello` name (never adding a duplicate), and ignores an empty key or name, which the agents ignore too. `parse_kv()` never converts `STRING_FIELDS` (keys, names, URLs, scripts) to numbers, and keys are always sent as text.

## Scripts

`script <list|read|write|rm|run>` drives the agents' `/script/*` routes. The name is URL-encoded (`urllib.parse.quote(..., safe="")`), so a path in it reaches the agent as one name, which it refuses. `read` prints the file's text as is (not JSON), so it can be redirected to a file; `write <name> <file>` uploads a local file; `run` waits 20 s (`request(timeout=...)`), past the Pi's 15 s script limit. `test_scripts` covers the default-key `403`, then the full cycle on a changed key.

## Output

Results (JSON, the scan table) go to stdout; errors, notes and `[SECURITY]` warnings go to stderr through `note()`/`warn()`/`fail()`, so a result can be piped into `jq`.

## Name Resolution
The CLI accepts raw IP addresses (IPv6 in brackets with a port, `[fd00::5]:8314`; `split_host()`/`join_host()`), `.local` hostnames, or exact agent names. A saved agent with no address on file stops with a message to run `scan`.
If a name is passed (e.g. `./ctrlpi-cli.py use pi4`), it looks up the name in `~/.ctrlpi/agents-config.json` case-insensitively. If missing, it automatically appends `.local` and retries standard DNS natively on the network, gracefully falling back on failure.
