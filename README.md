# ctrlPi API CLI

[![Platform: Cross-Platform](https://img.shields.io/badge/platform-Cross--Platform-006400.svg)](#setup)
[![Version 0.9.30](https://img.shields.io/badge/version-0.9.30-blue.svg)](https://github.com/ctrlpi/ctrlpi-cli/tags)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Auth: Api-Key](https://img.shields.io/badge/auth-Api--Key-orange.svg)](#setup)
[![License: MIT](https://img.shields.io/badge/license-MIT-188038.svg)](LICENSE)
![Status: Beta](https://img.shields.io/badge/status-Beta-red.svg)
[![Build Status](https://github.com/ctrlpi/ctrlpi-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ctrlpi/ctrlpi-cli/actions)

A pure-Python command-line interface for interacting with ctrlPi agents (Raspberry Pi, Pico W, ESP32). Designed to be fast and portable with **zero external dependencies**.

## Setup

No installation required, just Python 3 standard library.

```bash
git clone https://github.com/ctrlpi/ctrlpi-cli.git
cd ctrlpi-cli
chmod +x ctrlpi-cli.py
```

On Windows, run it through Python instead: `python ctrlpi-cli.py <command>`.

## Discovery

Find agents on your local network:
```bash
./ctrlpi-cli.py scan
```
This probes your subnet for ctrlPi agents (port 8314) and automatically saves them to `~/.ctrlpi/agents-config.json`. Once scanned, you can refer to agents by their name instead of typing out their IP address. A saved agent that took a new address is repaired on the next scan.

The table also lists other SSH devices on the network (usually a Pi that has no agent yet), with `-` in the ctrlPi column. Host names come from reverse DNS everywhere, plus Bonjour and SMB names on macOS.

## Usage

Set your target agent context:
```bash
./ctrlpi-cli.py use garage-pi
```

*(You can also optionally pass the host as the first argument to any command to temporarily override the context: `./ctrlpi-cli.py garage-pi gpio read`)*

A host is an agent name, a `.local` name or an IP address, optionally with a port (`10.0.0.5:8314`); an IPv6 address with a port goes in brackets (`[fd00::5]:8314`).

Results print to stdout (JSON, or the scan table), while errors and notes print to stderr, so a result can be piped straight into a tool such as `jq`.

Config files (the default host and the saved agents) live in `~/.ctrlpi/`. To keep them in a different folder, pass `--config <dir>` anywhere on the command line:
```bash
./ctrlpi-cli.py --config ~/lab-ctrlpi use garage-pi
```

## API keys

Every command sends the agent's `Api-Key`. For a saved agent it is the `api_key` stored in `~/.ctrlpi/agents-config.json`; for anything else it is the agents' default key, `your-secret-key`. A scan saves each new agent on the default key, or with an empty `api_key` when the agent already has a key of its own; fill that in by hand.

To use a different key for one command, pass `--api-key`:
```bash
./ctrlpi-cli.py --api-key my-secret garage-pi config read
```

Changing the key or name with `config update` or `config load` also saves the new value in `agents-config.json` (updating the agent's existing entry, even one saved under an older address), so the next command still reaches the agent. An empty key changes nothing, on the agent or here. Keys, names, URLs and scripts are always sent as text, so `webhook_key=007` stays `"007"`.

## Examples

The CLI covers every endpoint of the agent's REST API that needs a key. Below are some core examples, assuming you are in the context of an agent (`./ctrlpi-cli.py use pi4`).

#### 1. Read a GPIO pin
```bash
./ctrlpi-cli.py gpio r 26
```

#### 2. Set an output pin
```bash
./ctrlpi-cli.py gpio w 26 1
```

#### 3. Send a brief pulse (write with duration)
```bash
# Writes a 1, then after 2.5 seconds the pin goes back to its previous state
./ctrlpi-cli.py gpio w 26 1 2.5
```

#### 4. Configure a pin
```bash
./ctrlpi-cli.py gpio config 26 name=relay type=output reversed=true max=3600
```

#### 5. Scan all hardware GPIO pins
```bash
./ctrlpi-cli.py gpio scan
```

#### 6. Configure a watched input (fires a webhook on change)
```bash
./ctrlpi-cli.py gpio config 6 type=input watched=true
```

#### 7. Change the webhook URL
```bash
./ctrlpi-cli.py config update webhook_url=https://example.com/webhook
```

#### 8. Load a full configuration from a file
```bash
./ctrlpi-cli.py config load my-config.json
```

#### 9. Configure a sensor
```bash
./ctrlpi-cli.py sensor config cpu_temp script=cpu-temp.sh
```

#### 10. Read a sensor
```bash
./ctrlpi-cli.py sensor r cpu_temp
```

#### 11. Manage the agent's scripts
The files in the agent's `scripts/` folder, the one sensors run from (agents 0.9.28 or later). Writing and removing need an agent key other than the default `your-secret-key`, and are refused once a `write.lock` file is in the folder.
```bash
./ctrlpi-cli.py script list
./ctrlpi-cli.py script read cpu-temp.sh > cpu-temp.sh   # prints the file's text as is
./ctrlpi-cli.py script write weather.py ./weather.py    # upload a local file (creates or overwrites)
./ctrlpi-cli.py script run weather.py zone1             # {"name", "exit_code", "output"}
./ctrlpi-cli.py script rm weather.py
```

#### 12. View watched pins and webhooks
```bash
./ctrlpi-cli.py gpio watched
```

#### 13. View recent agent logs
```bash
./ctrlpi-cli.py logs
```

#### 14. Restart or upgrade the agent
```bash
./ctrlpi-cli.py restart
# To physically reboot the device instead of just the agent:
./ctrlpi-cli.py restart reboot

./ctrlpi-cli.py upgrade
```

## Testing

The test suite starts its own mock agent (`mock_agent.py`), so no real agent is needed:

```bash
python3 test.py
```

## Related projects

- **[`pi-gpio-api`](https://github.com/ctrlpi/pi-gpio-api)**: Raspberry Pi GPIO REST server that reads inputs and drives outputs (and named sensors) over an HTTP API.
- **[`pico-gpio-api`](https://github.com/ctrlpi/pico-gpio-api)**: Raspberry Pi Pico W port of the GPIO REST server, with the same endpoints, wire format, and auth.
- **[`esp32-gpio-api`](https://github.com/ctrlpi/esp32-gpio-api)**: ESP32 port of the GPIO REST server for MicroPython, with the same endpoints, wire format, and auth.
- **[`mcp-bridge`](https://github.com/ctrlpi/mcp-bridge)**: MCP server that exposes GPIO control as tools for AI agents (e.g. Claude), executing them over the agents' REST API.
- **[`homebridge-ctrlpi`](https://github.com/ctrlpi/homebridge-ctrlpi)**: A Homebridge plugin that brings Raspberry Pi, Pico W, and ESP32 GPIO into Apple Home, by driving one or more GPIO agents over their REST API.
- **`homekit-bridge`** *(coming soon)*: Native Apple HomeKit bridge that exposes GPIO pins and sensors as HomeKit accessories, driven over the agents' REST API.
- **`matter-bridge`** *(coming soon)*: Matter bridge that exposes GPIO pins and sensors to any Matter platform (Apple Home, Google Home, Alexa, Home Assistant), driven over the agents' REST API.
- **[`ctrlpi-lab`](https://github.com/ctrlpi/ctrlpi-lab)**: A local browser dashboard for your ctrlPi setup. Test GPIO and sensor endpoints, watch webhook events, and tail agent logs.
- **[`pico-bay`](https://github.com/ctrlpi/pico-bay)**: Web app and MCP server for managing MicroPython and CircuitPython boards, such as the Raspberry Pi Pico and ESP32, over USB.
- **`pi-bay`** *(coming soon)*: A local web app and MCP server for Raspberry Pi boards over the network. Edit files, open a remote terminal, and manage your Pi remotely.

## License

[MIT](LICENSE)
