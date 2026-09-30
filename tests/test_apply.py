import json
import os
import subprocess
import sys
import tempfile
import unittest

from hostsfile_dyndns_updater import apply, config, passwords, requestlog

RID = "0123456789abcdef"


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.hosts = os.path.join(d, "hosts")
        with open(self.hosts, "w") as fh:
            fh.write("127.0.0.1 localhost\n")
        self.log = os.path.join(d, "requests.log")
        open(self.log, "w").close()
        self.ini = os.path.join(d, "config.ini")
        h = passwords.hash_password("x" * 20, passwords.MIN_ITERATIONS)
        with open(self.ini, "w") as fh:
            fh.write(f"[server]\nhosts_file = {self.hosts}\nlog_file = {self.log}\n"
                     f"[host home]\nhostname = home.example.org\nusername = fritz\npassword_hash = {h}\n")
        self.cfg = config.load(self.ini)
        requestlog.setup(self.cfg)

    def tearDown(self):
        self.dir.cleanup()

    def send(self, message):
        raw = message if isinstance(message, bytes) else json.dumps(message).encode()
        return apply.handle(self.cfg, raw)

    def hosts_text(self):
        with open(self.hosts) as fh:
            return fh.read()

    def log_lines(self):
        with open(self.log) as fh:
            return [line.split(" ", 1)[1].rstrip("\n") for line in fh]

    def test_applies_and_logs(self):
        msg = {"request": RID, "host": "home.example.org", "addresses": ["8.8.8.8", "2606:4700::1"]}
        self.assertEqual(self.send(msg), {"result": "good", "changed": ["8.8.8.8", "2606:4700::1"]})
        self.assertEqual(self.send(msg), {"result": "nochg", "changed": []})
        self.assertIn("8.8.8.8\thome.example.org", self.hosts_text())
        self.assertEqual(self.log_lines(), [
            f"APPLY result=good host=home.example.org addr=8.8.8.8,2606:4700::1 request={RID}",
            f"APPLY result=nochg host=home.example.org addr=8.8.8.8,2606:4700::1 request={RID}",
        ])

    def test_rejects_everything_not_validated(self):
        base = {"request": RID, "host": "home.example.org", "addresses": ["8.8.8.8"]}
        cases = [
            (b"not json", "badrequest"),
            (b"x" * (apply.MAX_MESSAGE + 1), "badrequest"),
            (["list"], "badrequest"),
            ({**base, "extra": 1}, "badrequest"),
            ({**base, "request": "../../etc"}, "badrequest"),
            ({**base, "host": "evil.example.org"}, "badhost"),
            ({**base, "host": "localhost"}, "badhost"),
            ({**base, "addresses": []}, "badip"),
            ({**base, "addresses": "8.8.8.8"}, "badip"),
            ({**base, "addresses": ["127.0.0.1"]}, "badip"),
            ({**base, "addresses": ["192.168.1.1"]}, "badip"),
            ({**base, "addresses": ["8.8.8.8", "8.8.4.4"]}, "badip"),
            ({**base, "addresses": ["8.8.8.8\n1.1.1.1 evil"]}, "badip"),
            ({**base, "addresses": ["8.8.8.8", "2606:4700::1", "1.1.1.1"]}, "badip"),
        ]
        for message, reason in cases:
            self.assertEqual(self.send(message), {"result": "error", "reason": reason}, message)
        self.assertEqual(self.hosts_text(), "127.0.0.1 localhost\n")
        lines = self.log_lines()
        self.assertEqual(len(lines), len(cases))
        # Unvalidated values never reach the log.
        self.assertTrue(all(line.startswith("APPLY result=error reason=") for line in lines))
        self.assertNotIn("evil", "".join(lines))
        self.assertEqual(lines[5], f"APPLY result=error reason=badhost request={RID}")
        self.assertEqual(lines[4], "APPLY result=error reason=badrequest request=-")


class CommandTest(unittest.TestCase):
    """`hostsfile-dyndns-updater apply` as systemd runs it: request on stdin, reply on stdout."""

    def run_apply(self, ini, message):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
        proc = subprocess.run([sys.executable, "-m", "hostsfile_dyndns_updater", "apply", "-c", ini],
                              input=json.dumps(message).encode() + b"\n", capture_output=True,
                              env=env, check=True, timeout=30)
        return json.loads(proc.stdout)

    def test_command(self):
        with tempfile.TemporaryDirectory() as d:
            hosts = os.path.join(d, "hosts")
            with open(hosts, "w") as fh:
                fh.write("127.0.0.1 localhost\n")
            log = os.path.join(d, "requests.log")
            ini = os.path.join(d, "config.ini")
            h = passwords.hash_password("x" * 20, passwords.MIN_ITERATIONS)
            with open(ini, "w") as fh:
                fh.write(f"[server]\nhosts_file = {hosts}\nlog_file = {log}\n"
                         f"[host home]\nhostname = home.example.org\nusername = u\npassword_hash = {h}\n")
            message = {"request": RID, "host": "home.example.org", "addresses": ["8.8.8.8"]}
            # The helper runs as root and must not create the log with the wrong owner.
            self.assertEqual(self.run_apply(ini, message)["result"], "good")
            self.assertFalse(os.path.exists(log))
            open(log, "w").close()
            self.assertEqual(self.run_apply(ini, message)["result"], "nochg")
            with open(log) as fh:
                self.assertIn(f"APPLY result=nochg host=home.example.org addr=8.8.8.8 request={RID}",
                              fh.read())


if __name__ == "__main__":
    unittest.main()
