#!/usr/bin/env python3
import sys
import os
import subprocess
import time
import unittest
import json
import shutil
import tempfile

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

        # Throwaway config folder, passed to every CLI run with --config, so the
        # tests never touch the real ~/.ctrlpi
        cls.config_dir = tempfile.mkdtemp(prefix="ctrlpi-cli-test-")
        
        # 1. Spawn mock agent
        cls.agent_proc = subprocess.Popen(
            [sys.executable, cls.mock_agent_path, "--port", str(cls.port), "--name", "cli-test-agent"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Wait for agent to start up
        time.sleep(0.5)

        # An agent that exited (usually port in use, e.g. an orphan from an aborted run)
        # would leave the tests talking to whatever else holds the port
        if cls.agent_proc.poll() is not None:
            shutil.rmtree(cls.config_dir, ignore_errors=True)
            raise RuntimeError(f"mock agent failed to start on port {cls.port} (port already in use?)")

        # 2. Configure CLI to use the mock agent
        cls.run_cli("use", cls.host)

    @classmethod
    def tearDownClass(cls):
        # Clean up mock agent
        if hasattr(cls, 'agent_proc') and cls.agent_proc:
            cls.agent_proc.terminate()
            cls.agent_proc.wait()
        shutil.rmtree(cls.config_dir, ignore_errors=True)

    @classmethod
    def run_cli(cls, *args):
        """Helper to run the CLI and return its stdout as a string"""
        cmd = [sys.executable, cls.cli_path, "--config", cls.config_dir] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Command failed: {' '.join(cmd)}\nStderr: {result.stderr}\nStdout: {result.stdout}")
        return result.stdout.strip()

    @classmethod
    def run_cli_raw(cls, *args):
        """Run the CLI and return the finished process, whatever its exit code"""
        cmd = [sys.executable, cls.cli_path, "--config", cls.config_dir] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def load_cli(self, config_dir=None):
        """Load ctrlpi-cli.py in-process, pointed at a config folder"""
        import importlib.util
        spec = importlib.util.spec_from_file_location('ctrlpicli', self.cli_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.CONFIG_DIR = config_dir or self.config_dir
        return mod

    def saved_agent(self, name):
        with open(os.path.join(self.config_dir, "agents-config.json"), encoding="utf-8") as f:
            return next((a for a in json.load(f).get("agents", []) if a.get("name") == name), None)

    def test_config_read(self):
        out = self.run_cli("config", "read")
        data = json.loads(out)
        self.assertIn("agent", data)
        self.assertNotIn("api_key", data)  # the agents never show their own key
        
    def test_config_update(self):
        out = self.run_cli("config", "update", "log_days=0")
        data = json.loads(out)
        self.assertEqual(data.get("log_days"), 0)

    def test_config_dir_flag(self):
        # `use` (run in setUpClass) saved the default host in the --config folder
        with open(os.path.join(self.config_dir, "cli-config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f).get("host"), self.host)
        # --config is accepted anywhere on the command line
        out = self.run_cli("config", "read", "--config", self.config_dir)
        self.assertIn("agent", json.loads(out))

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

    def test_name_resolution(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ctrlpicli', self.cli_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.CONFIG_DIR = self.config_dir

        # 1. is_ip_address test
        self.assertTrue(mod.is_ip_address("127.0.0.1"))
        self.assertTrue(mod.is_ip_address("192.168.1.50"))
        self.assertTrue(mod.is_ip_address("::1"))
        self.assertFalse(mod.is_ip_address("pi4"))
        self.assertFalse(mod.is_ip_address("pi4.local"))

        # 2. Test candidate matching against mock agents
        # Save a dummy agent in config with .local
        cfg_path = mod.get_shared_agents_path()
        orig_cfg = mod.load_json(cfg_path)
        try:
            test_cfg = dict(orig_cfg)
            test_cfg["agents"] = [
                {"name": "mytestagent.local", "ip": "10.0.0.99", "port": 8314, "api_key": "key-local"},
                {"name": "bareagent", "ip": "10.0.0.100", "port": 8314, "api_key": "key-bare"}
            ]
            mod.save_json(cfg_path, test_cfg)

            # Query without .local should match mytestagent.local
            host, key, _ = mod.resolve_host("mytestagent")
            self.assertEqual(host, "10.0.0.99:8314")
            self.assertEqual(key, "key-local")

            # Query with .local should match bareagent
            host, key, _ = mod.resolve_host("bareagent.local")
            self.assertEqual(host, "10.0.0.100:8314")
            self.assertEqual(key, "key-bare")
        finally:
            mod.save_json(cfg_path, orig_cfg)

        # 3. DNS resolution before sending: localhost
        host, _, _ = mod.resolve_host("localhost")
        self.assertEqual(host, "localhost:8314")

        # 4. Local IP detection (normal and simulated offline)
        local_ip = mod.get_local_ip()
        self.assertTrue(mod.is_ip_address(local_ip))
        self.assertFalse(local_ip.startswith("127."))

    def test_reconcile_moved_agent(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ctrlpicli', self.cli_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.CONFIG_DIR = self.config_dir

        # Saved on a dead port; the mock agent answers under the same name elsewhere.
        agents = [{"name": "cli-test-agent", "ip": "127.0.0.1", "port": 9, "api_key": "secret"}]
        found = [{"name": "cli-test-agent", "ip": "127.0.0.1", "port": self.port}]

        changed = mod.reconcile_agents(agents, found)
        self.assertTrue(changed)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["port"], self.port)
        self.assertEqual(agents[0]["api_key"], "your-secret-key")
        self.assertIn("updated_at", agents[0])

        # Nothing accepts a key at the new address: the entry is left where it was.
        stuck = [{"name": "ghost", "ip": "127.0.0.1", "port": 9, "api_key": "secret"}]
        self.assertFalse(mod.reconcile_agents(stuck, [{"name": "ghost", "ip": "127.0.0.1", "port": 10}]))
        self.assertEqual(stuck[0]["port"], 9)

        # Test search_and_add_agents adds fresh agents without duplicate
        changed_add = mod.search_and_add_agents(agents, found)
        self.assertFalse(changed_add)
        self.assertEqual(len(agents), 1)

    def test_reconcile_homebridge_config(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ctrlpicli', self.cli_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.CONFIG_DIR = self.config_dir

        base = [
            {"name": "pico1", "ip": "192.168.1.10", "updated_at": "2026-10-10 10:00:00"},
            {"name": "pico2", "ip": "192.168.1.20", "updated_at": "2026-10-10 12:00:00"}
        ]
        overlay = [
            # Newer updated_at -> should update IP
            {"name": "pico1", "ip": "192.168.1.15", "updated_at": "2026-10-11 10:00:00"},
            # Older updated_at -> should NOT override
            {"name": "pico2", "ip": "192.168.1.25", "updated_at": "2026-10-09 10:00:00"},
            # New agent -> should be added
            {"name": "pico3", "ip": "192.168.1.30", "updated_at": "2026-10-11 10:00:00"}
        ]
        reconciled = mod.reconcile_agent_list(base, overlay)
        self.assertEqual(len(reconciled), 3)
        p1 = next(a for a in reconciled if a["name"] == "pico1")
        p2 = next(a for a in reconciled if a["name"] == "pico2")
        p3 = next(a for a in reconciled if a["name"] == "pico3")
        self.assertEqual(p1["ip"], "192.168.1.15")
        self.assertEqual(p2["ip"], "192.168.1.20")
        self.assertEqual(p3["ip"], "192.168.1.30")

    def test_fields_stay_text(self):
        # Keys, names and URLs are never turned into numbers; other numbers still are.
        out = self.run_cli("config", "update", "webhook_key=007", "log_days=0")
        data = json.loads(out)
        self.assertEqual(data.get("webhook_key"), "007")
        self.assertEqual(data.get("log_days"), 0)

    def test_key_change_is_saved(self):
        # The agent is not saved yet: the new key is filed under its /hello name.
        self.run_cli("config", "update", "api_key=4242")
        try:
            saved = self.saved_agent("cli-test-agent")
            self.assertEqual(saved.get("api_key"), "4242")
            self.assertIn("key_set", saved)
            # The next command uses the saved key, so it is not refused.
            self.assertIn("agent", json.loads(self.run_cli("config", "read")))
        finally:
            self.run_cli("config", "update", "api_key=your-secret-key")
        self.assertEqual(self.saved_agent("cli-test-agent").get("api_key"), "your-secret-key")

    def test_config_load_numeric_key(self):
        # A key written as a number in the file is sent and saved as text.
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".json") as f:
            json.dump({"api_key": 12345}, f)
            temp_name = f.name
        try:
            self.run_cli("config", "load", temp_name)
            self.assertEqual(self.saved_agent("cli-test-agent").get("api_key"), "12345")
            self.assertIn("agent", json.loads(self.run_cli("config", "read")))
        finally:
            with open(temp_name, "w") as f:
                json.dump({"api_key": "your-secret-key"}, f)
            self.run_cli("config", "load", temp_name)
            os.remove(temp_name)

    def test_api_key_flag(self):
        r = self.run_cli_raw("--api-key", "wrong-key", "config", "read")
        self.assertEqual(r.returncode, 1)
        self.assertIn("403", r.stderr)

    def test_usage_errors(self):
        # Mistakes stop with a message and exit 1, never silently or with a traceback.
        for args, text in ((["gpio", "foo"], "Usage"),
                           (["gpio", "w", "26", "1", "abc"], "Duration must be a number"),
                           (["gpio", "config", "26", "name"], "Expected key=value"),
                           (["sensor", "bogus"], "Usage"),
                           (["config", "bogus"], "Usage")):
            r = self.run_cli_raw(*args)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(text, r.stderr, args)  # errors go to stderr
            self.assertEqual(r.stdout, "", args)
            self.assertNotIn("Traceback", r.stderr, args)

    def test_old_config_file_ignored(self):
        # Agents in the old ~/.ctrlpi/config.json are not read.
        old_dir = tempfile.mkdtemp(prefix="ctrlpi-cli-old-")
        try:
            with open(os.path.join(old_dir, "config.json"), "w") as f:
                json.dump({"agents": [{"name": "old-only", "ip": "10.0.0.5"}]}, f)
            agents, _ = self.load_cli(old_dir).load_shared_agents()
            self.assertEqual(agents, [])
        finally:
            shutil.rmtree(old_dir, ignore_errors=True)

    def test_scan_add_and_duplicates(self):
        mod = self.load_cli()
        # An agent that refuses the default key is added without one.
        agents = []
        self.assertTrue(mod.search_and_add_agents(agents, [{"name": "keyed", "ip": "127.0.0.1", "port": 9}]))
        self.assertEqual(agents[0]["api_key"], "")
        # Two addresses claiming one saved name: nothing is moved.
        saved = [{"name": "twin", "ip": "127.0.0.1", "port": 9, "api_key": "secret"}]
        found = [{"name": "twin", "ip": "127.0.0.1", "port": 10}, {"name": "twin", "ip": "127.0.0.1", "port": 11}]
        self.assertFalse(mod.reconcile_agents(saved, found))
        self.assertEqual(saved[0]["port"], 9)

    def test_result_stays_valid_json(self):
        # The "(Saved ...)" note goes to stderr, so stdout is only the agent's JSON.
        r = self.run_cli_raw("config", "update", "api_key=k-json")
        try:
            self.assertEqual(r.returncode, 0)
            json.loads(r.stdout)
            self.assertIn("(Saved", r.stderr)
        finally:
            self.run_cli("config", "update", "api_key=your-secret-key")

    def test_key_change_updates_an_older_entry(self):
        # Saved under an older address: the entry is updated, not filed a second time.
        path = os.path.join(self.config_dir, "agents-config.json")
        with open(path, encoding="utf-8") as f:
            original = f.read()
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"agents": [{"name": "cli-test-agent", "ip": "10.9.9.9", "port": 8314,
                                   "api_key": "your-secret-key"}]}, f)
        try:
            self.run_cli("config", "update", "api_key=k-move")
            with open(path, encoding="utf-8") as f:
                entries = [a for a in json.load(f)["agents"] if a["name"] == "cli-test-agent"]
            self.assertEqual(len(entries), 1)
            self.assertEqual((entries[0]["ip"], entries[0]["port"]), ("127.0.0.1", self.port))
            self.assertEqual(entries[0]["api_key"], "k-move")
        finally:
            self.run_cli("config", "update", "api_key=your-secret-key")
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)

    def test_empty_key_not_saved(self):
        # An empty key changes nothing on an agent, so the saved one is kept.
        mod = self.load_cli()
        before = self.saved_agent("cli-test-agent")
        mod.remember_agent(self.host, "cli-test-agent", None, "")
        self.assertEqual(self.saved_agent("cli-test-agent"), before)

    def test_ipv6_and_missing_address(self):
        mod = self.load_cli()
        self.assertEqual(mod.split_host("[fd00::5]:9999"), ("fd00::5", "9999"))
        self.assertEqual(mod.split_host("fd00::5"), ("fd00::5", None))
        self.assertEqual(mod.resolve_host("[fd00::5]:9999")[0], "[fd00::5]:9999")
        self.assertEqual(mod.resolve_host("::1")[0], "[::1]:8314")
        # A saved agent whose address was cleared stops with a message.
        empty_dir = tempfile.mkdtemp(prefix="ctrlpi-cli-noip-")
        try:
            with open(os.path.join(empty_dir, "agents-config.json"), "w") as f:
                json.dump({"agents": [{"name": "ghost", "ip": ""}]}, f)
            with self.assertRaises(SystemExit):
                self.load_cli(empty_dir).resolve_host("ghost")
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

if __name__ == "__main__":
    unittest.main(verbosity=2)
