#!/usr/bin/env python3
import sys
import subprocess
import time

def run_cli(*args):
    cmd = ["./ctrlpi-cli.py"] + list(args)
    print(f"> {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: Command failed with exit code {result.returncode}")
        print(result.stderr or result.stdout)
        sys.exit(1)
    return result.stdout

def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    print(f"Testing against host: {host}")

    # Set context
    run_cli("use", host)
    
    # Run tests using context
    print("\n--- Testing Config ---")
    run_cli("config", "read")
    run_cli("config", "update", "log_days", "0")
    
    print("\n--- Testing GPIO ---")
    run_cli("gpio", "read")
    
    # Test reading and writing to pin 26
    print("\n--- Testing GPIO 26 Write/Read ---")
    run_cli("gpio", "w", "26", "1")
    run_cli("gpio", "r", "26")
    run_cli("gpio", "w", "26", "0")
    run_cli("gpio", "r", "26")
    
    print("\n--- Testing Explicit Host ---")
    run_cli(host, "gpio", "r")
    
    print("\n--- Testing Sensors ---")
    run_cli("sensor", "r")
    
    print("\n--- Testing Logs ---")
    run_cli("logs")
    
    print("\nAll tests ran successfully!")

if __name__ == "__main__":
    main()
