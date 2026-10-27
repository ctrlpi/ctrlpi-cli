#!/usr/bin/env python3
# ctrlpi-cli - a command-line client for ctrlPi GPIO agents (Raspberry Pi, Pico W, ESP32).
# Each command is one REST call to an agent. `scan` finds agents on the local /24 and
# saves them, so later commands can name an agent instead of typing its address.
# Standard library only, so it runs anywhere Python 3 does.

import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

VERSION = "0.9.30"  # CLI version; keep in sync with the README badge
# The first word after the optional host; any other first word is taken as the host.
COMMANDS = {"use", "scan", "gpio", "sensor", "script", "config", "logs", "restart", "upgrade"}
DEFAULT_AGENT_KEY = "your-secret-key"  # the key every agent ships with
DEFAULT_PORT = 8314
SHARED_AGENTS_FILENAME = "agents-config.json"
# A Homebridge host keeps its own agent list here; it is read, never written.
HOMEBRIDGE_AGENTS_FILE = "/var/lib/homebridge/ctrlpi-agents-config.json"

# Fields that are always text, however they look: a key such as "007" must reach the
# agent as "007", not as the number 7.
STRING_FIELDS = {
    "name", "api_key", "webhook_url", "webhook", "webhook_key", "script", "type", "pullup",
    "matter", "matter_key", "homekit", "homekit_key", "homebridge", "homebridge_key",
}

# Config files live in ~/.ctrlpi/, or in the folder given by --config <dir>. The tests
# pass --config so they never touch the real one.
CONFIG_DIR = os.path.expanduser(os.path.join("~", ".ctrlpi"))

# Results go to stdout (JSON, the scan table); errors and notes go to stderr, so a
# result can be piped (e.g. into jq) without anything else mixed in.
# Colour only on a terminal, so piped or redirected output stays plain text.
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
ERR_COLOR = sys.stderr.isatty() and not os.environ.get("NO_COLOR")


def color(text, code, enabled=None):
    # ANSI colour codes: 31 red, 32 green, 34 blue.
    on = USE_COLOR if enabled is None else enabled
    return f"\033[{code}m{text}\033[0m" if on else text


def note(msg):
    """A message for the user that is not the command's result."""
    print(msg, file=sys.stderr)


def warn(msg):
    """A security warning, in red on a terminal."""
    note(color(msg, 31, ERR_COLOR))


def fail(msg):
    """Print a usage or input error and exit 1."""
    note(msg)
    sys.exit(1)


def now_stamp():
    """UTC "YYYY-MM-DD HH:MM:SS", the stamp format every ctrlPi config file uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --- Config files --------------------------------------------------------------------

def get_config_path(filename):
    return os.path.join(CONFIG_DIR, filename)


def pop_flag(argv, flag):
    """Remove `<flag> <value>` from argv (anywhere in it) and return the value, or None."""
    if flag not in argv:
        return None
    i = argv.index(flag)
    if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
        fail(f"Usage: ctrlpi-cli {flag} <value> ...")
    value = argv[i + 1]
    del argv[i:i + 2]
    return value


def get_shared_agents_path():
    return get_config_path(SHARED_AGENTS_FILENAME)


def load_json(path):
    # A missing or unreadable file reads as empty, so a first run needs no setup.
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_json(path, data):
    # Creates the config folder on the first write.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def reconcile_agent_list(base_agents, overlay_agents):
    """Merge `overlay_agents` into `base_agents` by name: a new name is added, a known one
    is replaced only when the overlay's `updated_at` is newer."""
    base_list = list(base_agents) if isinstance(base_agents, list) else []
    if not isinstance(overlay_agents, list):
        return base_list
    for overlay in overlay_agents:
        if not overlay or not isinstance(overlay, dict) or not overlay.get("name"):
            continue
        name = overlay["name"]
        match_idx = -1
        for idx, item in enumerate(base_list):
            if isinstance(item, dict) and item.get("name") == name:
                match_idx = idx
                break
        if match_idx == -1:
            base_list.append(dict(overlay))
        else:
            existing = base_list[match_idx]
            overlay_date = overlay.get("updated_at") or ""
            existing_date = existing.get("updated_at") or ""
            if overlay_date > existing_date:
                updated = dict(existing)
                updated.update(overlay)
                base_list[match_idx] = updated
    return base_list


def load_shared_agents():
    """The shared agent list, plus a Homebridge host's own list (read-only), whichever
    entry is newer winning. Returns (agents, path of the shared file)."""
    path = get_shared_agents_path()
    cfg = load_json(path)
    agents = cfg.get("agents", []) if isinstance(cfg.get("agents"), list) else []
    if os.path.exists(HOMEBRIDGE_AGENTS_FILE):
        try:
            hb = load_json(HOMEBRIDGE_AGENTS_FILE)
            if isinstance(hb.get("agents"), list):
                agents = reconcile_agent_list(agents, hb["agents"])
        except Exception:
            pass
    return agents, path


def save_shared_agents(agents):
    # Only the `agents` key is written; anything else other projects keep there stays.
    path = get_shared_agents_path()
    cfg = load_json(path)
    cfg["agents"] = agents
    save_json(path, cfg)


def agent_key(agent):
    # An agent's stored key, always as text (a hand-edited file may hold a number).
    # `api-key` is the spelling some hand-written files use.
    key = agent.get("api_key", agent.get("api-key"))
    return "" if key is None else str(key)


# --- Talking to agents ---------------------------------------------------------------

def probe_hello(ip, port=DEFAULT_PORT):
    """One unauthenticated /hello. Returns its JSON object, or None."""
    try:
        req = urllib.request.Request(f"http://{join_host(ip, port)}/hello")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            if r.status == 200:
                data = json.loads(r.read().decode("utf-8"))
                return data if isinstance(data, dict) else None
    except Exception:
        pass
    return None


def check_agent_accepts_key(ip, port, api_key):
    """True when /config/read answers 200 with this key."""
    try:
        req = urllib.request.Request(f"http://{join_host(ip, port)}/config/read", headers={"Api-Key": str(api_key)})
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def is_ip_address(host):
    # IPv4 or IPv6, with or without the brackets around IPv6.
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def can_resolve_dns(hostname):
    # True if DNS (or mDNS, for a .local name) knows the name; the host is not contacted.
    try:
        socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return True
    except (socket.gaierror, socket.herror, OSError):
        return False


def split_host(host_str):
    """(host, port or None) from `host`, `host:port`, `[v6]:port`, `[v6]` or a bare v6."""
    if host_str.startswith("["):
        end = host_str.find("]")
        if end == -1:
            fail(f"Bad address: {host_str}")
        rest = host_str[end + 1:]
        return host_str[1:end], (rest[1:] if rest.startswith(":") else None) or None
    if host_str.count(":") > 1:
        return host_str, None  # a bare IPv6 address has no port
    if ":" in host_str:
        host, port = host_str.split(":", 1)
        return host, port or None
    return host_str, None


def join_host(host, port):
    # IPv6 needs brackets in a URL: [fd00::5]:8314.
    return f"[{host}]:{port}" if ":" in str(host) else f"{host}:{port}"


def resolve_host(host_str):
    """Turn what the user typed (IP, name, or `.local` host, optionally with :port) into
    (host:port, api_key, saved agent name or None). A saved agent supplies its own key;
    anything else gets the default key."""
    agents, _ = load_shared_agents()

    # host:port, or a bare host on the default agent port.
    host_part, port_part = split_host(host_str)
    has_custom_port = port_part is not None
    port_part = port_part or str(DEFAULT_PORT)

    host_lower = host_part.lower()

    # An IP address: use the saved agent at that address (and port, if one was given).
    if is_ip_address(host_part):
        for agent in agents:
            if agent.get("ip") == host_part and (
                    not has_custom_port or str(agent.get("port", DEFAULT_PORT)) == port_part):
                port = port_part if has_custom_port else agent.get("port", DEFAULT_PORT)
                return join_host(host_part, port), agent_key(agent) or DEFAULT_AGENT_KEY, agent.get("name")
        return join_host(host_part, port_part), DEFAULT_AGENT_KEY, None

    # A name: match saved agents case-insensitively, with and without `.local`.
    if not host_lower.endswith(".local"):
        candidates = [host_lower, f"{host_lower}.local"]
    else:
        candidates = [host_lower, host_lower[:-6]]

    for cand in candidates:
        for agent in agents:
            agent_name = agent.get("name", "")
            if agent_name and agent_name.lower() == cand:
                if not agent.get("ip"):
                    fail(f"'{agent_name}' has no address on file. Run 'ctrlpi-cli scan' to find it.")
                port = port_part if has_custom_port else agent.get("port", DEFAULT_PORT)
                return join_host(agent["ip"], port), agent_key(agent) or DEFAULT_AGENT_KEY, agent_name

    # Not saved: let DNS (or mDNS through `.local`) find it.
    if can_resolve_dns(host_part):
        return join_host(host_part, port_part), DEFAULT_AGENT_KEY, None
    if not host_lower.endswith(".local") and can_resolve_dns(f"{host_part}.local"):
        return join_host(f"{host_part}.local", port_part), DEFAULT_AGENT_KEY, None

    return join_host(host_part, port_part), DEFAULT_AGENT_KEY, None


def request(host, path, method="GET", data=None, api_key="", retry_local=True, timeout=5):
    """One REST call. Returns the parsed JSON (or text); on any error prints why and
    exits 1, naming the likely cause (unknown name, agent not running, device off)."""
    url = f"http://{host}{path}"
    headers = {}
    if api_key:
        headers["Api-Key"] = str(api_key)

    encoded_data = None
    if data is not None:
        encoded_data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    try:
        # 5 s covers a busy Pico; the agents answer /upgrade and /restart before acting.
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_body = response.read().decode("utf-8")
            try:
                return json.loads(res_body)
            except ValueError:
                return res_body
    except urllib.error.HTTPError as e:
        # The agent answered with an error status: show its own message.
        res_body = e.read().decode("utf-8")
        note(f"Error {e.code}: {res_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        # The agent never answered: work out why from the socket error.
        reason = str(e.reason)
        is_dns_error = isinstance(e.reason, socket.gaierror) or any(
            msg in reason for msg in [
                "nodename nor servname provided",
                "Name or service not known",
                "getaddrinfo failed",
                "Name does not resolve",
                "Host not found"
            ]
        )
        if is_dns_error:
            # One retry with `.local` appended, for a bare name only mDNS knows.
            host_part, port_part = split_host(host)
            if retry_local and not host_part.endswith(".local") and not is_ip_address(host_part):
                return request(join_host(f"{host_part}.local", port_part or DEFAULT_PORT), path, method, data,
                               api_key, retry_local=False, timeout=timeout)

            hostname_only = host_part
            note(f"Error: Could not resolve hostname for '{hostname_only}'.")
            note(f"Reason: The name was not found in your network DNS, nor in {get_shared_agents_path()}.")
            note("Tip: Run 'ctrlpi-cli scan' to discover agents and auto-save them to your config!")
        elif "Connection refused" in reason:
            note(f"Error: Connection refused by '{host}'.")
            note("Reason: The device is online, but the ctrlPi agent is not running on it.")
        elif "timed out" in reason.lower() or "timeout" in reason.lower():
            note(f"Error: Connection timed out to '{host}'.")
            note("Reason: The device is turned off, or the IP is unreachable on your network.")
        else:
            note(f"Connection error to {host}: {reason}")
        sys.exit(1)
    except Exception as e:
        note(f"Error: {e}")
        sys.exit(1)


def remember_agent(host, saved_name, new_name=None, new_key=None):
    """Record a name or key the agent just accepted in agents-config.json, so the next
    command still reaches it. An agent not on file at this address is matched by its
    /hello name, so one saved under an older address is updated rather than filed twice."""
    # An empty key or name changes nothing on the agent, so nothing is saved either.
    new_name = str(new_name) if new_name not in (None, "") else None
    new_key = str(new_key) if new_key not in (None, "") else None
    if new_name is None and new_key is None:
        return
    agents, path = load_shared_agents()
    ip, port = split_host(host)
    port = int(port or DEFAULT_PORT)

    def at_address(a):
        return a.get("ip") == ip and int(a.get("port", DEFAULT_PORT)) == port

    def named(name):
        return [a for a in agents if str(a.get("name", "")).lower() == str(name).lower()]

    # The saved entry this command used: the one at this address when a name is filed twice.
    entry = None
    if saved_name:
        same = named(saved_name)
        entry = next((a for a in same if at_address(a)), same[0] if same else None)
    if entry is None:
        # Not saved at this address (typed as an IP or .local name): use its own name.
        name = (probe_hello(ip, port) or {}).get("name") or new_name
        if not name:
            note(f"Note: could not record the change for {host} in {path}; "
                 "pass --api-key with the new key next time.")
            return
        same = named(name)
        entry = same[0] if same else None
        if entry is None:
            entry = {"name": name}
            agents.append(entry)
        entry["ip"], entry["port"] = ip, port
    if new_name:
        entry["name"] = new_name
    if new_key is not None:
        entry["api_key"] = new_key
        entry.pop("api-key", None)
        entry["key_set"] = now_stamp()  # when this key was set
    entry["updated_at"] = now_stamp()
    save_shared_agents(agents)
    note(f"(Saved '{entry['name']}' in {path})")


# --- Scan ------------------------------------------------------------------------------

def _dns_sd(args, seconds):
    # Runs macOS `dns-sd` for a fixed time and returns what it printed ("" elsewhere).
    try:
        out = subprocess.run(["dns-sd", *args], capture_output=True, text=True, timeout=seconds).stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
    except Exception:
        return ""
    return out.decode(errors="ignore") if isinstance(out, bytes) else out


BONJOUR_SERVICES = ["_afpovertcp._tcp", "_smb._tcp", "_ssh._tcp", "_workstation._tcp"]


def bonjour_names():
    """{ip: Bonjour name} for devices advertising common services (macOS only). Browses
    each service, resolves every instance to its .local host, then that host to an IPv4
    address, all in parallel."""
    # `dns-sd -B` lists instances and never exits by itself, so it runs for a fixed time.
    def browse(svc):
        found = []
        for line in _dns_sd(["-B", svc, "local"], 3).splitlines():
            idx = line.find(svc + ".")
            if " Add " in line and idx != -1:
                inst = line[idx + len(svc) + 1:].strip()
                if inst:
                    found.append((inst, svc))
        return found

    instances = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for found in ex.map(browse, BONJOUR_SERVICES):
            instances.update(found)

    # `dns-sd -L` resolves one instance to the .local host it runs on.
    def to_host(item):
        inst, svc = item
        out = _dns_sd(["-L", inst, svc, "local"], 2)
        m = re.search(r"reached at ([A-Za-z0-9._-]+\.local)\.?:", out)
        return (m.group(1), inst) if m else None

    host_name = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for r in ex.map(to_host, instances):
            if r:
                host_name.setdefault(r[0], r[1])

    # `dns-sd -G v4` resolves a .local host to its IPv4 address.
    def to_ip(host):
        for line in _dns_sd(["-G", "v4", host], 2).splitlines():
            m = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", line)
            if "Add" in line and m and m.group(1) != "0.0.0.0":
                return m.group(1), host_name[host]
        return None

    names = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for r in ex.map(to_ip, list(host_name)):
            if r:
                # Drop the " [aa:bb:...]" MAC suffix some services add to the name.
                names[r[0]] = re.sub(r"\s*\[[0-9a-fA-F:]+\]\s*$", "", r[1])
    return names


def smb_name(ip):
    # The NetBIOS name from macOS `smbutil`, or None.
    try:
        out = subprocess.run(["smbutil", "status", "-a", ip], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "0x00" and parts[2] == "UNIQUE":
            return parts[0]
    return None


def resolve_network_name(ip, bonjour):
    # Reverse DNS first, then Bonjour and SMB (both macOS only), else "-".
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        pass
    return bonjour.get(ip) or smb_name(ip) or "-"


def check_agent(ip, bonjour):
    """Probe one address. Returns (ip, hostname, agent name): the agent name when /hello
    answers, None for a plain SSH device, and (ip, None, None) for nothing at all."""
    name = "0"
    is_ctrlpi = False
    try:
        with urllib.request.urlopen(f"http://{ip}:{DEFAULT_PORT}/hello", timeout=1.5) as r:
            if r.status == 200:
                data = json.loads(r.read().decode("utf-8"))
                name = data.get('name', '0')
                is_ctrlpi = True
    except Exception:
        pass

    # Not an agent: still list it if SSH answers, since that is usually a Pi to set up.
    if not is_ctrlpi:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.5)
                if s.connect_ex((ip, 22)) != 0:
                    return (ip, None, None)
        except Exception:
            return (ip, None, None)

    hostname = resolve_network_name(ip, bonjour)
    return (ip, hostname, name if is_ctrlpi else None)


def get_local_ip():
    """This machine's LAN IPv4 address, or None. Works offline too: the UDP trick needs
    a default route, so the interfaces are tried after it."""
    # 1. A UDP "connect" picks the outgoing interface without sending anything.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith('127.'):
                return ip
    except Exception:
        pass

    # 2. The addresses this host's name resolves to.
    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        for a in addrs:
            if not a.startswith('127.') and not a.startswith('169.254.'):
                return a
    except Exception:
        pass

    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = res[4][0]
            if not ip.startswith('127.') and not ip.startswith('169.254.'):
                return ip
    except Exception:
        pass

    # 3. The interface list from `ip` (Linux) or `ifconfig` (macOS, BSD).
    for cmd in [['ip', '-4', 'addr'], ['ifconfig']]:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout
            for match in re.finditer(r'\binet\s+(\d+\.\d+\.\d+\.\d+)', out):
                ip = match.group(1)
                if not ip.startswith('127.') and not ip.startswith('169.254.'):
                    return ip
        except Exception:
            pass

    return None


def reconcile_agents(agents, found_agents):
    """Repair the address of saved agents that moved (a new DHCP lease). Adds nothing.
    Returns True if anything changed."""
    if not found_agents or not agents:
        return False

    changed = False
    for a in agents:
        agent_name = a.get("name")
        agent_ip = a.get("ip")
        port = a.get("port", DEFAULT_PORT)

        # Exact name match only: guessing at a similar name is how the wrong device
        # gets adopted.
        claimants = [f for f in found_agents if f.get("name") == agent_name]
        if not claimants:
            continue  # offline, or renamed
        if (len(claimants) == 1 and claimants[0].get("ip") == agent_ip
                and claimants[0].get("port", DEFAULT_PORT) == port):
            continue  # still where we left it

        # Is the saved agent still answering at its stored address, under its own name?
        at_old = probe_hello(agent_ip, port) if agent_ip else None
        alive_at_old = bool(at_old and at_old.get("name") == agent_name)

        if len(claimants) > 1:
            warn(f"[SECURITY] '{agent_name}' is claimed by {len(claimants)} addresses "
                 f"({', '.join(c['ip'] for c in claimants)}) - possible cyber attack; not moving it.")
            continue

        moved = claimants[0]
        moved_port = moved.get("port", DEFAULT_PORT)
        if alive_at_old:
            warn(f"[SECURITY] '{agent_name}' answers at both {agent_ip}:{port} and "
                 f"{moved['ip']}:{moved_port} - possible cyber attack; not moving it.")
            continue

        # The old address is dead, so this is the agent that moved. Offer the public
        # default key first; its own key goes only to an address that took its name.
        own_key = agent_key(a)
        if check_agent_accepts_key(moved["ip"], moved_port, DEFAULT_AGENT_KEY):
            key = DEFAULT_AGENT_KEY
        elif own_key and own_key != DEFAULT_AGENT_KEY and check_agent_accepts_key(moved["ip"], moved_port, own_key):
            key = own_key
        else:
            note(f"'{agent_name}' looks like it moved to {moved['ip']}:{moved_port}, but neither the "
                  f"default key nor its own was accepted - leaving {agent_ip}:{port} on file.")
            continue

        a["ip"] = moved["ip"]
        a["port"] = moved_port
        if key != own_key:
            a.pop("key_set", None)  # the stamp belonged to the key it no longer has
        a.pop("api-key", None)
        a["api_key"] = key
        a["updated_at"] = now_stamp()
        changed = True

    return changed


def search_and_add_agents(agents, found_agents):
    """Add found agents that are not on file yet. One still on the default key is saved
    with it; one that refuses it is saved without a key, for the user to fill in."""
    known = {(a.get("ip"), a.get("port", DEFAULT_PORT)) for a in agents if a.get("ip")}
    known_names = {a.get("name") for a in agents if a.get("name")}

    # New means a new address and a new name; a known name at a new address is a move,
    # which reconcile_agents handles.
    fresh = [f for f in found_agents if (f.get("ip"), f.get("port", DEFAULT_PORT)) not in known]
    to_add = [f for f in fresh if f.get("name") and f["name"] not in known_names and f["name"] not in ("0", "unknown")]

    if not to_add:
        return False

    for f in to_add:
        port = f.get("port", DEFAULT_PORT)
        key = DEFAULT_AGENT_KEY if check_agent_accepts_key(f["ip"], port, DEFAULT_AGENT_KEY) else ""
        if not key:
            note(f"'{f['name']}' has its own key: set its api_key in {get_shared_agents_path()}, "
                  "or pass --api-key.")
        known_names.add(f["name"])
        agents.append({
            "name": f["name"],
            "ip": f["ip"],
            "port": port,
            "api_key": key,
            "updated_at": now_stamp(),
        })
    return True


def do_scan():
    """Probe every address on the local /24, list agents and SSH devices, then repair
    moved agents and save new ones in agents-config.json."""
    local_ip = get_local_ip()
    if not local_ip:
        fail("Error: could not find this computer's LAN address. Is it connected to a network?")
    prefix = '.'.join(local_ip.split('.')[:3]) + '.'
    note(f"Scanning {prefix}0/24 on port {DEFAULT_PORT}... (batch: 48, timeout: 1.5s)")

    # Bonjour names are gathered once up front; each probe then just looks its IP up.
    bonjour = bonjour_names()

    print(f"\n{'IP ADDRESS':<17} {'HOST NAME':<20} {'ctrlPi'}")
    print("-" * 55)

    agents, _ = load_shared_agents()

    found_any = False
    found_agents = []

    # 48 probes at a time: a Pico serves one connection at a time and drops probes when
    # the whole /24 arrives at once.
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
        futures = [pool.submit(check_agent, f"{prefix}{i}", bonjour) for i in range(1, 255)]

        # Rows print as answers arrive, so their order varies from scan to scan.
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                ip, hostname, name = result
                if hostname is None:
                    continue

                found_any = True

                # Long names are cut to keep the table aligned.
                if len(hostname) > 19:
                    hostname = hostname[:16] + "..."

                if hostname == "-":
                    host_disp = color("-".ljust(20), 31)
                else:
                    host_disp = color(hostname.ljust(20), 32)

                # Only rows with an agent name are agents; the rest are SSH devices.
                if not name or name == "0" or name == "unknown":
                    name_disp = color("-", 31)
                else:
                    name_disp = color(name, 34)
                    found_agents.append({"ip": ip, "port": DEFAULT_PORT, "name": name})

                print(f"{ip:<17} {host_disp} {name_disp}")

    if not found_any:
        print("No agents found.")
        return

    # Repair first: an agent that only moved must not be added a second time.
    changed = False
    if agents:
        if reconcile_agents(agents, found_agents):
            changed = True
    if search_and_add_agents(agents, found_agents):
        changed = True

    if changed:
        save_shared_agents(agents)
        note(f"\n(Updated agents in {get_shared_agents_path()})")


# --- Commands --------------------------------------------------------------------------

def parse_kv(pairs):
    """`key=value` arguments to a dict. Booleans and numbers are converted, except for
    STRING_FIELDS, which always stay text."""
    data = {}
    for kv in pairs:
        if "=" not in kv:
            fail(f"Expected key=value, got '{kv}'.")
        k, v = kv.split("=", 1)
        if k not in STRING_FIELDS:
            if v.lower() in ("true", "yes", "on"):
                v = True
            elif v.lower() in ("false", "no", "off"):
                v = False
            else:
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    pass
        data[k] = v
    return data


def show(res):
    # Every answer is printed as indented JSON.
    print(json.dumps(res, indent=2))


def print_help():
    # Kept in step with the README.
    print("Usage: ctrlpi-cli [--config <dir>] [--api-key <key>] [host] <command> [args...]")
    print("\nCommands:")
    print("  use <host>                Set the default target agent")
    print("  scan                      Find ctrlPi agents on the local network")
    print("  gpio <action> [args...]   Read, write, or configure GPIO pins")
    print("  sensor <action> [args...] Read or configure sensors")
    print("  script <action> [args...] List, read, write, remove or run the agent's scripts")
    print("  config <action> [args...] Read, update, or load the agent's configuration")
    print("  logs                      View recent agent logs")
    print("  restart [reboot]          Restart the agent (or reboot the device)")
    print("  upgrade                   Upgrade the agent")
    print("\nOptions:")
    print("  --config <dir>            Keep config files in <dir> instead of ~/.ctrlpi")
    print("  --api-key <key>           Use this key instead of the saved one")


def main():
    global CONFIG_DIR
    config_dir = pop_flag(sys.argv, "--config")
    if config_dir:
        CONFIG_DIR = os.path.abspath(os.path.expanduser(config_dir))
    # --api-key replaces the saved key for this one command.
    key_override = pop_flag(sys.argv, "--api-key")
    # No arguments is a usage error (exit 1); an explicit --help is not (exit 0).
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print_help()
        sys.exit(1 if len(sys.argv) < 2 else 0)

    arg1 = sys.argv[1]
    cli_config_path = get_config_path("cli-config.json")

    # The host is optional: without one, commands go to the default set by `use`.
    if arg1 in COMMANDS:
        command = arg1
        args = sys.argv[2:]
        if command == "scan":
            do_scan()
            return

        if command != "use":
            cli_config = load_json(cli_config_path)
            raw_host = cli_config.get("host")
            if not raw_host:
                fail("No host provided and no default host set. Use 'ctrlpi-cli use <host>' first.")
        else:
            raw_host = None
    else:
        raw_host = arg1
        if len(sys.argv) < 3:
            fail("Missing command.")
        command = sys.argv[2]
        args = sys.argv[3:]

        if command not in COMMANDS:
            fail(f"Unknown command: {command}")

    # `use` only stores what was typed; it is resolved on each later command, so a saved
    # agent that moves is still found by name.
    if command == "use":
        if not args:
            fail("Usage: ctrlpi-cli use <host>")
        cli_config = load_json(cli_config_path)
        cli_config["host"] = args[0]
        save_json(cli_config_path, cli_config)
        print(f"Default host set to {args[0]}")
        return

    host, api_key, saved_name = resolve_host(raw_host)
    if key_override is not None:
        api_key = key_override
    action = args[0] if args else None

    # Each command takes a short or long action name, e.g. `r` or `read`.
    if command == "gpio":
        usage = "Usage: ctrlpi-cli gpio <r|read|w|write|c|config|s|scan|wa|watched> [args...]"
        if action in ("r", "read"):
            show(request(host, f"/gpio/read/{args[1]}" if len(args) > 1 else "/gpio/read", api_key=api_key))
        elif action in ("s", "scan"):
            show(request(host, "/gpio/scan", api_key=api_key))
        elif action in ("w", "write"):
            if len(args) < 3:
                fail("Usage: ctrlpi-cli gpio w <id> <value> [duration]")
            try:
                val = int(args[2])
            except ValueError:
                val = args[2]  # on, off, toggle
            data = {"value": val}
            if len(args) > 3:
                try:
                    data["duration"] = float(args[3])
                except ValueError:
                    fail(f"Duration must be a number of seconds, not '{args[3]}'.")
            show(request(host, f"/gpio/write/{args[1]}", method="POST", data=data, api_key=api_key))
        elif action in ("c", "config"):
            if len(args) < 2:
                fail("Usage: ctrlpi-cli gpio c <id> [key=value ...]")
            show(request(host, f"/gpio/config/{args[1]}", method="POST", data=parse_kv(args[2:]), api_key=api_key))
        elif action in ("wa", "watched"):
            show(request(host, "/gpio/watched", api_key=api_key))
        else:
            fail(usage)
    elif command == "sensor":
        if action in ("r", "read"):
            show(request(host, f"/sensor/read/{args[1]}" if len(args) > 1 else "/sensor/read", api_key=api_key))
        elif action in ("c", "config"):
            if len(args) < 2:
                fail("Usage: ctrlpi-cli sensor config <name> [key=value ...]")
            show(request(host, f"/sensor/config/{args[1]}", method="POST", data=parse_kv(args[2:]), api_key=api_key))
        else:
            fail("Usage: ctrlpi-cli sensor <r|read|c|config> [args...]")
    elif command == "script":
        # The name is URL-encoded, so a path in it reaches the agent as one (refused) name.
        name = urllib.parse.quote(args[1], safe="") if len(args) > 1 else None
        if action in ("l", "list"):
            show(request(host, "/script/list", api_key=api_key))
        elif action in ("r", "read") and name:
            # The file's text as is, so `script read x.py > x.py` saves a copy.
            res = request(host, f"/script/read/{name}", api_key=api_key)
            if isinstance(res, dict) and "content" in res:
                sys.stdout.write(res["content"])
            else:
                show(res)
        elif action in ("w", "write") and name:
            if len(args) < 3:
                fail("Usage: ctrlpi-cli script write <name> <file>")
            if not os.path.isfile(args[2]):
                fail(f"File not found: {args[2]}")
            with open(args[2], "r", encoding="utf-8") as f:
                content = f.read()
            show(request(host, f"/script/write/{name}", method="POST", data={"content": content}, api_key=api_key))
        elif action in ("rm", "remove") and name:
            show(request(host, f"/script/write/{name}", method="POST", data={"remove": True}, api_key=api_key))
        elif action == "run" and name:
            # A Pi gives a script 15 s before killing it, so wait longer than that.
            show(request(host, f"/script/run/{name}", method="POST", data={"args": args[2:]},
                         api_key=api_key, timeout=20))
        else:
            fail("Usage: ctrlpi-cli script <l|list|r|read|w|write|rm|remove|run> [name] [file|args...]")
    elif command == "config":
        if action in ("r", "read"):
            show(request(host, "/config/read", api_key=api_key))
        elif action in ("u", "update"):
            if len(args) < 2:
                fail("Usage: ctrlpi-cli config update [key=value ...]")
            data = parse_kv(args[1:])
            # A new key or name is saved locally too, or the next command would be refused.
            show(request(host, "/config/update", method="POST", data=data, api_key=api_key))
            remember_agent(host, saved_name, data.get("name"), data.get("api_key"))
        elif action in ("l", "load"):
            if len(args) < 2:
                fail("Usage: ctrlpi-cli config load <filename>")
            filename = args[1]
            if not os.path.exists(filename):
                fail(f"File not found: {filename}")
            with open(filename, "r", encoding="utf-8") as f:
                try:
                    config_data = json.load(f)
                except json.JSONDecodeError as e:
                    fail(f"Invalid JSON in {filename}: {e}")
            if not isinstance(config_data, dict):
                fail(f"{filename} must hold a JSON object.")
            # A key or name is always text, even when the file wrote it as a number.
            for k in ("api_key", "name"):
                if k in config_data and config_data[k] is not None:
                    config_data[k] = str(config_data[k])
            show(request(host, "/config/load", method="POST", data=config_data, api_key=api_key))
            remember_agent(host, saved_name, config_data.get("name"), config_data.get("api_key"))
        else:
            fail("Usage: ctrlpi-cli config <r|read|u|update|l|load> [args...]")
    elif command == "logs":
        # Log lines print as plain text rather than a JSON array.
        res = request(host, "/logs", api_key=api_key)
        if isinstance(res, dict) and "logs" in res:
            for log in res["logs"]:
                print(log)
        else:
            show(res)
    elif command == "restart":
        # `restart reboot` reboots the whole device; a plain restart sends an empty body.
        reboot = len(args) > 0 and args[0].lower() in ("reboot", "true")
        show(request(host, "/restart", method="POST", data={"reboot": reboot} if reboot else {}, api_key=api_key))
    elif command == "upgrade":
        # The agent downloads the latest release, then restarts on it.
        show(request(host, "/upgrade", method="POST", api_key=api_key))


if __name__ == "__main__":
    main()
