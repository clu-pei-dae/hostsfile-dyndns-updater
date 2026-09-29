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

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.hosts = os.path.join(d, "hosts")
        with open(self.hosts, "w") as fh:
            fh.write("127.0.0.1 localhost\n")
        self.sock = os.path.join(d, "api.sock")
        ini = os.path.join(d, "config.ini")
        h = passwords.hash_password(PASSWORD, passwords.MIN_ITERATIONS)
        with open(ini, "w") as fh:
            fh.write(f"""[server]
socket = {self.sock}
socket_group =
hosts_file = {self.hosts}
allow_query_credentials = {self.query_creds}
require_source_match = {self.source_match}

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


if __name__ == "__main__":
    unittest.main()
