# CLAUDE.md - ctrlpi-cli

This file provides guidance to Claude Code when working with this repository.

**`README.md` is done.** Do not edit it without the user's explicit confirmation first.

## What This Is

A pure Python, zero-dependency command-line interface (`ctrlpi-cli.py`) for interacting with ctrlPi REST agents.

## Architecture

- **ctrlpi-cli.py**: The main CLI script. Handles parsing, HTTP requests (via `urllib`), and DNS resolution.
- **test.py**: Integration test script that uses `subprocess` to call `ctrlpi-cli.py` against a specified host.

## Network Discovery (`scan`)

- The `scan` command is highly optimized using parallel threading and macOS Bonjour/mDNS resolution (similar to `search.py` in `dev-api-tester`).
- Newly discovered agents are aggressively synced to `~/.ctrlpi/config.json`.

## Name Resolution
The CLI accepts raw IP addresses, `.local` hostnames, or exact agent names.
If a name is passed (e.g. `./ctrlpi-cli.py use pi4`), it looks up the name in `~/.ctrlpi/config.json` case-insensitively. If missing, it automatically appends `.local` and retries standard DNS natively on the network, gracefully falling back on failure.
