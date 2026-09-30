import base64
import http.client
import os
import socket
import tempfile
import threading
import unittest

from hostsfile_dyndns_updater import config, passwords
from hostsfile_dyndns_updater.server import Server

PASSWORD = "correct horse battery staple"


class UnixConn(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(self._path)


class ServerTest(unittest.TestCase):
    query_creds = False
    source_match = False
    log_level = "changes"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.hosts = os.path.join(d, "hosts")
        with open(self.hosts, "w") as fh:
            fh.write("127.0.0.1 localhost\n")
        self.sock = os.path.join(d, "api.sock")
        self.log = os.path.join(d, "requests.log")
        ini = os.path.join(d, "config.ini")
        h = passwords.hash_password(PASSWORD, passwords.MIN_ITERATIONS)
        with open(ini, "w") as fh:
            fh.write(f"""[server]
socket = {self.sock}
socket_group =
hosts_file = {self.hosts}
allow_query_credentials = {self.query_creds}
require_source_match = {self.source_match}
log_file = {self.log}
log_level = {self.log_level}

[host home]
hostname = home.example.org
username = fritz
password_hash = {h}
""")
        self.server = Server(config.load(ini))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def get(self, path, user="fritz", pw=PASSWORD, method="GET", headers=None):
        headers = dict(headers or {})
        if user is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        conn = UnixConn(self.sock)
        conn.request(method, path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode().strip()
        conn.close()
        return resp.status, body

    def hosts_text(self):
        with open(self.hosts) as fh:
            return fh.read()

    def log_lines(self):
        with open(self.log) as fh:
            # drop the timestamp
            return [line.split(" ", 1)[1].rstrip("\n") for line in fh]


class BasicTest(ServerTest):
    def test_update_and_nochg(self):
        self.assertEqual(self.get("/update?ipv4=8.8.8.7&domain=home.example.org"),
                         (200, "good 8.8.8.7"))
        self.assertIn("8.8.8.7\thome.example.org", self.hosts_text())
        self.assertEqual(self.get("/update?ipv4=8.8.8.7")[1], "nochg 8.8.8.7")
        self.assertEqual(self.get("/update?ipv4=8.8.8.8")[1], "good 8.8.8.8")
        self.assertEqual(self.hosts_text().count("home.example.org"), 1)

    def test_dual_stack_and_unspecified_v6(self):
        self.assertEqual(self.get("/update?ipv4=8.8.8.7&ipv6=2606:4700::1")[1],
                         "good 8.8.8.7 2606:4700::1")
        self.assertEqual(self.get("/update?ipv4=8.8.8.9&ipv6=::")[1], "good 8.8.8.9")
        self.assertIn("2606:4700::1\thome.example.org", self.hosts_text())

    def test_auth_required(self):
        before = self.hosts_text()
        for user, pw in ((None, None), ("fritz", "wrong"), ("nobody", PASSWORD)):
            self.assertEqual(self.get("/update?ipv4=8.8.8.7", user, pw), (401, "badauth"))
        self.assertEqual(self.get("/update?ipv4=8.8.8.7&username=fritz&password=" + PASSWORD.replace(" ", "%20"),
                                  user=None)[0], 401)
        self.assertEqual(self.hosts_text(), before)

    def test_bad_input(self):
        for q, status in (("ipv4=999.1.1.1", 400), ("ipv4=127.0.0.1", 400),
                          ("ipv4=192.168.1.1", 400), ("ipv4=2606:4700::1", 400),
                          ("ipv4=1.2.3.4%0a5.6.7.8", 400), ("", 400),
                          ("ipv4=8.8.8.7&domain=evil.example.org", 403)):
            self.assertEqual(self.get("/update?" + q)[0], status, q)
        self.assertEqual(self.get("/other")[0], 404)
        self.assertEqual(self.get("/update?ipv4=8.8.8.7", method="POST")[0], 405)
        self.assertEqual(self.get("/update?ipv4=8.8.8.7", method="HEAD")[0], 405)
        self.assertEqual(self.hosts_text(), "127.0.0.1 localhost\n")


class QueryCredsTest(ServerTest):
    query_creds = True

    def test_query_credentials_when_enabled(self):
        self.assertEqual(
            self.get(f"/update?ipv4=8.8.8.7&username=fritz&password={PASSWORD.replace(chr(32), '%20')}", user=None)[0], 200)


class SourceMatchTest(ServerTest):
    source_match = True

    def test_source_must_match(self):
        self.assertEqual(self.get("/update?ipv4=8.8.8.7",
                                  headers={"X-Real-IP": "8.8.8.99"})[0], 403)
        self.assertEqual(self.get("/update?ipv4=8.8.8.7",
                                  headers={"X-Real-IP": "8.8.8.7"})[0], 200)

    def test_missing_peer_is_rejected(self):
        self.assertEqual(self.get("/update?ipv4=8.8.8.7")[0], 403)


class RequestLogTest(ServerTest):
    def test_changes_level_logs_changes_and_failures_only(self):
        self.get("/update?ipv4=8.8.8.7&domain=home.example.org",
                 headers={"X-Real-IP": "9.9.9.9"})
        self.get("/update?ipv4=8.8.8.7", headers={"X-Real-IP": "9.9.9.9"})  # nochg
        self.get("/update?ipv4=8.8.8.7", pw="wrong", headers={"X-Real-IP": "1.2.3.4"})
        self.get("/update?ipv4=8.8.8.7", user="guess", headers={"X-Real-IP": "1.2.3.4"})
        self.get("/update?ipv4=192.168.1.1", headers={"X-Real-IP": "9.9.9.9"})
        self.get("/update?ipv4=8.8.8.7", method="HEAD", headers={"X-Real-IP": "1.2.3.5"})
        self.assertEqual(self.log_lines(), [
            "OK status=200 result=good client=9.9.9.9 user=fritz host=home.example.org addr=8.8.8.7",
            "FAIL status=401 result=badauth client=1.2.3.4 user=-",
            "FAIL status=401 result=badauth client=1.2.3.4 user=-",  # unknown user not echoed
            "FAIL status=400 result=badip client=9.9.9.9 user=fritz host=home.example.org",
            "FAIL status=405 result=badmethod client=1.2.3.5 user=-",
        ])

    def test_client_field_cannot_be_forged(self):
        self.get("/update?ipv4=8.8.8.7", pw="x", headers={"X-Real-IP": "1.2.3.4 FAIL client=5.6.7.8"})
        self.assertEqual(self.log_lines(), ["FAIL status=401 result=badauth client=- user=-"])

    def test_log_is_reopened_after_rotation(self):
        self.get("/update?ipv4=8.8.8.7", pw="x")
        os.rename(self.log, self.log + ".1")
        self.get("/update?ipv4=8.8.8.8", pw="x")
        self.assertEqual(len(self.log_lines()), 1)


class RequestLogAllTest(ServerTest):
    log_level = "all"

    def test_all_level_logs_unchanged_requests(self):
        self.get("/update?ipv4=8.8.8.7")
        self.get("/update?ipv4=8.8.8.7")
        self.assertEqual([line.split(" ", 3)[:3] for line in self.log_lines()],
                         [["OK", "status=200", "result=good"], ["OK", "status=200", "result=nochg"]])


class UnknownUserTimingTest(unittest.TestCase):
    def test_unknown_user_costs_the_same_iterations(self):
        from unittest import mock

        from hostsfile_dyndns_updater import server
        from hostsfile_dyndns_updater.config import Config, HostEntry

        encoded = passwords.hash_password(PASSWORD, passwords.MIN_ITERATIONS + 1)
        cfg = Config(hosts={"fritz": HostEntry("home", "home.example.org", "fritz", encoded)})
        seen = []
        real = passwords.verify_password
        with mock.patch.object(server.passwords, "verify_password",
                               side_effect=lambda pw, h: seen.append(h) or real(pw, h)):
            for user in ("fritz", "nobody"):
                with self.assertRaises(server.Rejected):
                    server.authenticate(cfg, (user, "wrong"))
        self.assertEqual([passwords.parse_hash(h)[0] for h in seen],
                         [passwords.MIN_ITERATIONS + 1] * 2)


if __name__ == "__main__":
    unittest.main()
