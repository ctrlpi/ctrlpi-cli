# ctrlPi API CLI

[![Platform: Cross-Platform](https://img.shields.io/badge/platform-Cross--Platform-006400.svg)](#setup)
[![Version 0.9.24](https://img.shields.io/badge/version-0.9.24-blue.svg)](https://github.com/ctrlpi/ctrlpi-cli/tags)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Auth: Api-Key](https://img.shields.io/badge/auth-Api--Key-orange.svg)](#setup)
[![License: MIT](https://img.shields.io/badge/license-MIT-188038.svg)](LICENSE)
![Status: Beta](https://img.shields.io/badge/status-Beta-red.svg)
[![Build Status](https://github.com/ctrlpi/ctrlpi-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ctrlpi/ctrlpi-cli/actions)

A pure-Python command-line interface for interacting with ctrlPi agents (Raspberry Pi, Pico W). Designed to be fast and portable with **zero external dependencies**.

## Setup

No installation required, just Python 3 standard library.

```bash
git clone https://github.com/ctrlpi/ctrlpi-cli.git
cd ctrlpi-cli
chmod +x ctrlpi-cli.py
```

## Discovery

Find agents on your local network:
```bash
./ctrlpi-cli.py scan
```
This probes your subnet for ctrlPi agents (port 8314) and automatically saves them to `~/.ctrlpi/config.json`. Once scanned, you can refer to agents by their name instead of typing out their IP address.

## Usage

Set your target agent context:
```bash
./ctrlpi-cli.py use garage-pi
```

*(You can also optionally pass the host as the first argument to any command to temporarily override the context: `./ctrlpi-cli.py garage-pi gpio read`)*

## Examples

The CLI mirrors all operations available in the REST API. Below are some core examples, assuming you are in the context of an agent (`./ctrlpi-cli.py use pi4`).

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
# Writes a 1, then waits 2.5 seconds, then writes a 0
./ctrlpi-cli.py gpio w 26 1 2.5
```

#### 4. Configure a pin
```bash
./ctrlpi-cli.py gpio config 26 name=relay type=output reversed=true max=3600
```

#### 5. Scan all BCM GPIO hardware pins (Pi only)
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

#### 11. View watched pins and webhooks
```bash
./ctrlpi-cli.py gpio watched
```

#### 12. View recent agent logs
```bash
./ctrlpi-cli.py logs
```

#### 13. Restart or upgrade the agent
```bash
./ctrlpi-cli.py restart
# To physically reboot the device instead of just the agent:
./ctrlpi-cli.py restart reboot

./ctrlpi-cli.py upgrade
```

## Testing

You can verify the CLI against a running agent using the included test script:

```bash
./test.py <agent-name>
```

## License

[MIT](http://localhost:8181/LICENSE)
