#!/usr/bin/env python3

import sys
import os
import json
import socket
import urllib.request
import urllib.error
from urllib.error import URLError
import subprocess
import re

COMMANDS = {"use", "scan", "gpio", "sensor", "config", "logs", "restart", "upgrade"}
DEFAULT_AGENT_KEY = "your-secret-key"

def get_config_path(filename):
    return os.path.expanduser(os.path.join("~", ".ctrlpi", filename))

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def resolve_host(host_str):
    shared_config = load_json(get_config_path("config.json"))
    agents = shared_config.get("agents", [])
    
    host_lower = host_str.lower()
    for agent in agents:
        agent_name = agent.get("name", "")
        if (agent_name and agent_name.lower() == host_lower) or agent.get("ip") == host_str:
            ip = agent.get("ip")
            port = agent.get("port", 8314)
            key = agent.get("api_key", "")
            return f"{ip}:{port}", key or DEFAULT_AGENT_KEY
    
    # Not found, parse as is
    if ":" not in host_str:
        return f"{host_str}:8314", DEFAULT_AGENT_KEY
    return host_str, DEFAULT_AGENT_KEY

def request(host, path, method="GET", data=None, api_key="", retry_local=True):
    url = f"http://{host}{path}"
    headers = {}
    if api_key:
        headers["Api-Key"] = api_key
    
    encoded_data = None
    if data is not None:
        encoded_data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    
    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res_body = response.read().decode("utf-8")
            try:
                return json.loads(res_body)
            except:
                return res_body
    except urllib.error.HTTPError as e:
        res_body = e.read().decode("utf-8")
        print(f"Error {e.code}: {res_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if "nodename nor servname provided" in reason or "Name or service not known" in reason:
            if retry_local and ":" in host:
                host_part, port_part = host.split(":", 1)
                # If it's not an IP and doesn't already end in .local, try appending .local
                if not host_part.endswith(".local") and not host_part.replace(".", "").isdigit():
                    return request(f"{host_part}.local:{port_part}", path, method, data, api_key, retry_local=False)
            
            hostname_only = host.split(':')[0]
            print(f"Error: Could not resolve hostname for '{hostname_only}'.")
            print("Reason: The name was not found in your network DNS, nor in ~/.ctrlpi/config.json.")
            print("Tip: Run 'ctrlpi-cli scan' to discover agents and auto-save them to your config!")
        elif "Connection refused" in reason:
            print(f"Error: Connection refused by '{host}'.")
            print("Reason: The device is online, but the ctrlPi agent (port 8314) is not running on it.")
        elif "timed out" in reason.lower() or "timeout" in reason.lower():
            print(f"Error: Connection timed out to '{host}'.")
            print("Reason: The device is turned off, or the IP is unreachable on your network.")
        else:
            print(f"Connection error to {host}: {reason}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

import concurrent.futures

def _dns_sd(args, seconds):
    try:
        out = subprocess.run(["dns-sd", *args], capture_output=True, text=True, timeout=seconds).stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
    except Exception:
        return ""
    return out.decode(errors="ignore") if isinstance(out, bytes) else out

BONJOUR_SERVICES = ["_afpovertcp._tcp", "_smb._tcp", "_ssh._tcp", "_workstation._tcp"]

def bonjour_names():
    def browse(svc):
        found = []
        for line in _dns_sd(["-B", svc, "local"], 3).splitlines():
            idx = line.find(svc + ".")
            if " Add " in line and idx != -1:
                inst = line[idx + len(svc) + 1:].strip()
                if inst: found.append((inst, svc))
        return found
    
    instances = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for found in ex.map(browse, BONJOUR_SERVICES):
            instances.update(found)
            
    def to_host(item):
        inst, svc = item
        out = _dns_sd(["-L", inst, svc, "local"], 2)
        m = re.search(r"reached at ([A-Za-z0-9._-]+\.local)\.?:", out)
        return (m.group(1), inst) if m else None
        
    host_name = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for r in ex.map(to_host, instances):
            if r: host_name.setdefault(r[0], r[1])
            
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
                names[r[0]] = re.sub(r"\s*\[[0-9a-fA-F:]+\]\s*$", "", r[1])
    return names

def smb_name(ip):
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
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        pass
    return bonjour.get(ip) or smb_name(ip) or "-"

def check_agent(ip, bonjour):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.5)
    try:
        result = sock.connect_ex((ip, 8314))
        if result != 0:
            return None
    finally:
        sock.close()
        
    hostname = resolve_network_name(ip, bonjour)
    name = "0"
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://{ip}:8314/hello", timeout=1.5) as r:
            if r.status == 200:
                data = json.loads(r.read().decode("utf-8"))
                name = data.get('name', '0')
    except Exception:
        pass
        
    return (ip, hostname, name)

def do_scan():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '192.168.1.1'
    finally:
        s.close()
    
    prefix = '.'.join(local_ip.split('.')[:3]) + '.'
    print(f"Scanning {prefix}0/24 on port 8314... (batch: 48, timeout: 1.5s)")
    
    # Pre-resolve Bonjour names
    bonjour = bonjour_names()
    
    print(f"\n{'IP ADDRESS':<17} {'HOST NAME':<20} {'ctrlPi'}")
    print("-" * 55)
    
    shared_config_path = get_config_path("config.json")
    shared_config_path = get_config_path("config.json")
    shared_config = load_json(shared_config_path)
    agents = shared_config.get("agents", [])
    
    # Build a lookup for existing agents by IP so we can update them in place
    existing_agents = {a.get("ip"): a for a in agents if a.get("ip")}
    
    needs_save = False
    found_any = False
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
        futures = [pool.submit(check_agent, f"{prefix}{i}", bonjour) for i in range(1, 255)]
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                ip, hostname, name = result
                found_any = True
                
                if len(hostname) > 19:
                    hostname = hostname[:16] + "..."
                    
                print(f"{ip:<17} {hostname:<20} {name}")
                
                if ip in existing_agents:
                    # If it already exists, update the name if it's valid and has changed
                    if name and name != "0" and name != "unknown":
                        if existing_agents[ip].get("name") != name:
                            existing_agents[ip]["name"] = name
                            needs_save = True
                else:
                    new_agent = {
                        "name": name,
                        "ip": ip,
                        "port": 8314,
                        "api_key": DEFAULT_AGENT_KEY
                    }
                    agents.append(new_agent)
                    existing_agents[ip] = new_agent
                    needs_save = True
    
    if not found_any:
        print("No agents found.")
        
    if needs_save:
        shared_config["agents"] = agents
        save_json(shared_config_path, shared_config)
        print("\n(Added newly found agents to ~/.ctrlpi/config.json)")

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: ctrlpi-cli [host] <command> [args...]")
        print("\nCommands:")
        print("  use <host>              Set the default target agent")
        print("  scan                    Find ctrlPi agents on the local network")
        print("  gpio <action> [args...] Read, write, or configure GPIO pins")
        print("  sensor <action> [args.] Read or configure sensors")
        print("  config <action> [args.] Read, update, or load global agent configuration")
        print("  logs                    View recent agent logs")
        print("  restart [reboot]        Restart the agent (or reboot the device)")
        print("  upgrade                 Upgrade the agent")
        sys.exit(1 if len(sys.argv) < 2 else 0)
    
    arg1 = sys.argv[1]
    
    cli_config_path = get_config_path("cli-config.json")
    
    if arg1 in COMMANDS:
        command = arg1
        args = sys.argv[2:]
        if command == "scan":
            do_scan()
            return
        
        # Load host from config (unless the command is 'use')
        if command != "use":
            cli_config = load_json(cli_config_path)
            raw_host = cli_config.get("host")
            if not raw_host:
                print("No host provided and no default host set. Use 'ctrlpi-cli use <host>' first.")
                sys.exit(1)
        else:
            raw_host = None
    else:
        raw_host = arg1
        if len(sys.argv) < 3:
            print("Missing command.")
            sys.exit(1)
        command = sys.argv[2]
        args = sys.argv[3:]
        
        if command not in COMMANDS:
            print(f"Unknown command: {command}")
            sys.exit(1)
            
    if command == "use":
        if not args:
            print("Usage: ctrlpi-cli use <host>")
            sys.exit(1)
        cli_config = load_json(cli_config_path)
        cli_config["host"] = args[0]
        save_json(cli_config_path, cli_config)
        print(f"Default host set to {args[0]}")
        return

    host, api_key = resolve_host(raw_host)
    
    if command == "gpio":
        if not args:
            print("Usage: ctrlpi-cli gpio <r|read|w|write|c|config|s|scan|wa|watched> [args...]")
            sys.exit(1)
        action = args[0]
        if action in ("r", "read"):
            if len(args) > 1:
                res = request(host, f"/gpio/read/{args[1]}", api_key=api_key)
            else:
                res = request(host, "/gpio/read", api_key=api_key)
            print(json.dumps(res, indent=2))
        elif action in ("s", "scan"):
            res = request(host, "/gpio/scan", api_key=api_key)
            print(json.dumps(res, indent=2))
        elif action in ("w", "write"):
            if len(args) < 3:
                print("Usage: ctrlpi-cli gpio w <id> <value> [duration]")
                sys.exit(1)
            try:
                val = int(args[2])
            except ValueError:
                val = args[2]
            
            data = {"value": val}
            if len(args) > 3:
                data["duration"] = float(args[3])
                
            res = request(host, f"/gpio/write/{args[1]}", method="POST", data=data, api_key=api_key)
            print(json.dumps(res, indent=2))
        elif action in ("c", "config"):
            if len(args) < 2:
                print("Usage: ctrlpi-cli gpio c <id> [key=value ...]")
                sys.exit(1)
            pin = args[1]
            data = {}
            for kv in args[2:]:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if v.lower() in ("true", "yes", "on"): v = True
                    elif v.lower() in ("false", "no", "off"): v = False
                    else:
                        try:
                            if "." in v: v = float(v)
                            else: v = int(v)
                        except ValueError:
                            pass
                    data[k] = v
            res = request(host, f"/gpio/config/{pin}", method="POST", data=data, api_key=api_key)
            print(json.dumps(res, indent=2))
        elif action in ("wa", "watched"):
            res = request(host, "/gpio/watched", api_key=api_key)
            print(json.dumps(res, indent=2))
    elif command == "sensor":
        if not args:
            print("Usage: ctrlpi-cli sensor <r|read|c|config> [args...]")
            sys.exit(1)
        action = args[0]
        if action in ("r", "read"):
            if len(args) > 1:
                res = request(host, f"/sensor/read/{args[1]}", api_key=api_key)
            else:
                res = request(host, "/sensor/read", api_key=api_key)
            print(json.dumps(res, indent=2))
        elif action in ("c", "config"):
            if len(args) < 2:
                print("Usage: ctrlpi-cli sensor config <name> [key=value ...]")
                sys.exit(1)
            sensor_name = args[1]
            data = {}
            for kv in args[2:]:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if v.lower() in ("true", "yes", "on"): v = True
                    elif v.lower() in ("false", "no", "off"): v = False
                    data[k] = v
            res = request(host, f"/sensor/config/{sensor_name}", method="POST", data=data, api_key=api_key)
            print(json.dumps(res, indent=2))
    elif command == "config":
        if not args:
            print("Usage: ctrlpi-cli config <r|read|u|update|l|load> [args...]")
            sys.exit(1)
        action = args[0]
        if action in ("r", "read"):
            res = request(host, "/config/read", api_key=api_key)
            print(json.dumps(res, indent=2))
        elif action in ("u", "update"):
            if len(args) < 2:
                print("Usage: ctrlpi-cli config update [key=value ...]")
                sys.exit(1)
            data = {}
            for kv in args[1:]:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if v.lower() in ("true", "yes", "on"): v = True
                    elif v.lower() in ("false", "no", "off"): v = False
                    else:
                        try:
                            if "." in v: v = float(v)
                            else: v = int(v)
                        except ValueError:
                            pass
                    data[k] = v
            res = request(host, "/config/update", method="POST", data=data, api_key=api_key)
            print(json.dumps(res, indent=2))
        elif action in ("l", "load"):
            if len(args) < 2:
                print("Usage: ctrlpi-cli config load <filename>")
                sys.exit(1)
            filename = args[1]
            if not os.path.exists(filename):
                print(f"File not found: {filename}")
                sys.exit(1)
            with open(filename, "r", encoding="utf-8") as f:
                try:
                    config_data = json.load(f)
                except json.JSONDecodeError as e:
                    print(f"Invalid JSON in {filename}: {e}")
                    sys.exit(1)
            res = request(host, "/config/load", method="POST", data=config_data, api_key=api_key)
            print(json.dumps(res, indent=2))
    elif command == "logs":
        res = request(host, "/logs", api_key=api_key)
        if isinstance(res, dict) and "logs" in res:
            for log in res["logs"]:
                print(log)
        else:
            print(json.dumps(res, indent=2))
    elif command == "restart":
        reboot = len(args) > 0 and args[0].lower() in ("reboot", "true")
        res = request(host, "/restart", method="POST", data={"reboot": reboot} if reboot else {}, api_key=api_key)
        print(json.dumps(res, indent=2))
    elif command == "upgrade":
        res = request(host, "/upgrade", method="POST", api_key=api_key)
        print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
