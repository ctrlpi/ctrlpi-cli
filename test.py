#!/usr/bin/env python3
import sys
import os
import subprocess
import time
import unittest
import json

class TestCtrlPiCli(unittest.TestCase):
    def __str__(self):
        return self._testMethodName

    @classmethod
    def setUpClass(cls):
        # Determine paths
        cls.cli_path = os.path.join(os.path.dirname(__file__), "ctrlpi-cli.py")
        cls.mock_agent_path = os.path.join(os.path.dirname(__file__), "mock_agent.py")
        
        # Mock agent configuration
        cls.port = 12314
        cls.host = f"127.0.0.1:{cls.port}"
        
        # 1. Spawn mock agent
        cls.agent_proc = subprocess.Popen(
            [sys.executable, cls.mock_agent_path, "--port", str(cls.port), "--name", "cli-test-agent"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Wait for agent to start up
        time.sleep(0.5)

        # 2. Configure CLI to use the mock agent
        cls.run_cli("use", cls.host)

    @classmethod
    def tearDownClass(cls):
        # Clean up mock agent
        if hasattr(cls, 'agent_proc') and cls.agent_proc:
            cls.agent_proc.terminate()
            cls.agent_proc.wait()

    @classmethod
    def run_cli(cls, *args):
        """Helper to run the CLI and return its stdout as a string"""
        cmd = [sys.executable, cls.cli_path] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Command failed: {' '.join(cmd)}\nStderr: {result.stderr}\nStdout: {result.stdout}")
        return result.stdout.strip()

    def test_config_read(self):
        out = self.run_cli("config", "read")
        data = json.loads(out)
        self.assertIn("api_key", data)
        
    def test_config_update(self):
        out = self.run_cli("config", "update", "log_days=0")
        data = json.loads(out)
        self.assertEqual(data.get("log_days"), 0)

    def test_gpio_read_all(self):
        out = self.run_cli("gpio", "read")
        data = json.loads(out)
        self.assertIsInstance(data, dict)

    def test_gpio_write_and_read_pin(self):
        # Write 1 to GPIO 26
        out_w1 = self.run_cli("gpio", "w", "26", "1")
        data_w1 = json.loads(out_w1)
        self.assertEqual(data_w1.get("value"), 1)

        # Read GPIO 26
        out_r1 = self.run_cli("gpio", "r", "26")
        data_r1 = json.loads(out_r1)
        self.assertEqual(data_r1.get("value"), 1)

        # Write 0 to GPIO 26
        out_w0 = self.run_cli("gpio", "w", "26", "0")
        data_w0 = json.loads(out_w0)
        self.assertEqual(data_w0.get("value"), 0)

    def test_explicit_host_override(self):
        # Pass the host explicitly as the first argument
        out = self.run_cli(self.host, "gpio", "scan")
        data = json.loads(out)
        self.assertIsInstance(data, list)
        if len(data) > 0:
            self.assertIn("gpio", data[0])

    def test_sensors(self):
        out = self.run_cli("sensor", "r")
        data = json.loads(out)
        self.assertIsInstance(data, dict)

    def test_logs(self):
        out = self.run_cli("logs")
        self.assertIn("log line 1", out.lower())

    def test_gpio_config(self):
        out = self.run_cli("gpio", "config", "26", "name=relay", "type=output", "reversed=true")
        data = json.loads(out)
        self.assertEqual(data.get("status"), "configured")

    def test_gpio_pulse(self):
        # Write value 1 with a duration of 2.5 seconds
        out = self.run_cli("gpio", "w", "26", "1", "2.5")
        data = json.loads(out)
        self.assertEqual(data.get("status"), "written")
        self.assertEqual(data.get("value"), 1)

    def test_sensor_config_and_read(self):
        # Configure specific sensor
        out_conf = self.run_cli("sensor", "config", "test_sensor", "script=value")
        data_conf = json.loads(out_conf)
        self.assertEqual(data_conf.get("status"), "configured")

        # Read specific sensor (mock agent returns 42 for scripts containing "value")
        out_read = self.run_cli("sensor", "r", "test_sensor")
        data_read = json.loads(out_read)
        self.assertEqual(data_read.get("test_sensor"), 42)

    def test_gpio_watched(self):
        out = self.run_cli("gpio", "watched")
        data = json.loads(out)
        self.assertIn("watched", data)
        self.assertIsInstance(data["watched"], list)

    def test_system_commands(self):
        # Restart
        out_restart = self.run_cli("restart")
        data_restart = json.loads(out_restart)
        self.assertEqual(data_restart.get("status"), "restarting")

        # Restart Reboot
        out_reboot = self.run_cli("restart", "reboot")
        data_reboot = json.loads(out_reboot)
        self.assertEqual(data_reboot.get("status"), "restarting")

        # Upgrade
        out_upgrade = self.run_cli("upgrade")
        data_upgrade = json.loads(out_upgrade)
        self.assertEqual(data_upgrade.get("status"), "upgrading")

    def test_config_load(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".json") as f:
            json.dump({"gpios": {"26": {"name": "temp", "type": "output"}}}, f)
            temp_name = f.name
        
        try:
            out = self.run_cli("config", "load", temp_name)
            data = json.loads(out)
            self.assertEqual(data.get("status"), "loaded")
        finally:
            os.remove(temp_name)

if __name__ == "__main__":
    unittest.main(verbosity=2)
