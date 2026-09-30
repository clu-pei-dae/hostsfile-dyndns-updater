import base64
import http.client
import os
import re
import socket
import socketserver
import tempfile
import threading
import unittest

from hostsfile_dyndns_updater import apply, config, passwords
from hostsfile_dyndns_updater.server import Server, systemd_listen_fd

PASSWORD = "correct horse battery staple"


class UnixConn(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(self._path)


class FakeApplySocket(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Stands in for hostsfile-dyndns-updater-apply.socket (Accept=yes): one
    helper run per connection, connection as its stdin/stdout."""

    daemon_threads = True

    def __init__(self, path, cfg):
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                apply.serve_stream(cfg, self.rfile, self.wfile)

        super().__init__(path, Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()
        os.unlink(self.server_address)


class ServerTest(unittest.TestCase):
    query_creds = False
    source_match = False
    log_level = "changes"
    split = True  # API and root helper as separate parts, as in production

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.hosts = os.path.join(d, "hosts")
        with open(self.hosts, "w") as fh:
            fh.write("127.0.0.1 localhost\n")
        self.sock = os.path.join(d, "api.sock")
        self.apply_sock = os.path.join(d, "apply.sock")
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
apply_socket = {self.apply_sock if self.split else ""}

[host home]
hostname = home.example.org
username = fritz
password_hash = {h}
""")
        self.cfg = config.load(ini)
        self.helper = FakeApplySocket(self.apply_sock, self.cfg) if self.split else None
        self.server = Server(self.cfg)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        if self.helper and os.path.exists(self.apply_sock):
            self.helper.stop()
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

    def log_lines_without_ids(self):
        """Log lines with request ids replaced by ID; asserts ids pair up APPLY and OK/ERROR."""
        lines = self.log_lines()
        ids = [m.group(1) for m in (re.search(r" request=([0-9a-f]{16})", line) for line in lines) if m]
        for rid in set(ids):
            self.assertEqual(ids.count(rid), 2, f"request {rid} not logged by both parts")
        return [re.sub(r"request=[0-9a-f]{16}", "request=ID", line) for line in lines]


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
        self.assertEqual(self.log_lines_without_ids(), [
            "APPLY result=good host=home.example.org addr=8.8.8.7 request=ID",
            "OK status=200 result=good client=9.9.9.9 user=fritz host=home.example.org addr=8.8.8.7 request=ID",
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
                         [["APPLY", "result=good", "host=home.example.org"],
                          ["OK", "status=200", "result=good"], ["OK", "status=200", "result=nochg"]])


class SplitTest(ServerTest):
    def test_unchanged_address_does_not_start_the_helper(self):
        self.assertEqual(self.get("/update?ipv4=8.8.8.7")[0], 200)
        self.helper.stop()
        self.assertEqual(self.get("/update?ipv4=8.8.8.7"), (200, "nochg 8.8.8.7"))

    def test_unreachable_helper_is_a_server_error(self):
        self.helper.stop()
        self.assertEqual(self.get("/update?ipv4=8.8.8.7", headers={"X-Real-IP": "9.9.9.9"}),
                         (500, "911"))
        self.assertEqual(self.hosts_text(), "127.0.0.1 localhost\n")
        line = self.log_lines()[-1]
        self.assertRegex(line, r"^ERROR status=500 result=911 client=9\.9\.9\.9 user=fritz "
                               r"host=home\.example\.org addr=8\.8\.8\.7 request=[0-9a-f]{16} "
                               r"detail=apply-unavailable$")

    def test_failed_write_is_logged_by_both_parts(self):
        from unittest import mock
        with mock.patch.object(apply, "update_hosts_file", side_effect=PermissionError("denied")):
            self.assertEqual(self.get("/update?ipv4=8.8.8.7")[0], 500)
        self.assertEqual(self.log_lines_without_ids(), [
            "APPLY result=error host=home.example.org addr=8.8.8.7 reason=writefailed request=ID",
            "ERROR status=500 result=911 client=- user=fritz host=home.example.org addr=8.8.8.7 "
            "request=ID detail=apply-writefailed",
        ])


class RaceTest(ServerTest):
    def test_helper_nochg_is_still_paired_in_the_log(self):
        # Another request applied the address between the API's check and the helper.
        from unittest import mock

        from hostsfile_dyndns_updater import server
        self.get("/update?ipv4=8.8.8.7")
        with mock.patch.object(server, "pending_addresses", return_value=["8.8.8.7"]):
            self.assertEqual(self.get("/update?ipv4=8.8.8.7"), (200, "nochg 8.8.8.7"))
        self.assertEqual([line.split(" ", 3)[:3] for line in self.log_lines_without_ids()], [
            ["APPLY", "result=good", "host=home.example.org"],
            ["OK", "status=200", "result=good"],
            ["APPLY", "result=nochg", "host=home.example.org"],
            ["OK", "status=200", "result=nochg"],
        ])


class InProcessTest(ServerTest):
    split = False  # apply_socket empty: the API process writes the file itself

    def test_update_without_helper(self):
        self.assertEqual(self.get("/update?ipv4=8.8.8.7"), (200, "good 8.8.8.7"))
        self.assertIn("8.8.8.7\thome.example.org", self.hosts_text())
        self.assertEqual([line.split(" ", 1)[0] for line in self.log_lines_without_ids()],
                         ["APPLY", "OK"])


class SocketActivationTest(ServerTest):
    def setUp(self):
        super().setUp()
        # Replace the self-bound server by one using an inherited, pre-bound socket.
        self.server.shutdown()
        self.server.server_close()
        os.unlink(self.sock)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.sock)
        listener.listen()
        self.server = Server(self.cfg, listen_fd=listener.detach())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def test_serves_on_inherited_socket(self):
        self.assertEqual(self.get("/update?ipv4=8.8.8.7"), (200, "good 8.8.8.7"))

    def test_listen_fd_only_for_this_process(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"LISTEN_PID": "1", "LISTEN_FDS": "1"}):
            self.assertIsNone(systemd_listen_fd())
        with mock.patch.dict(os.environ, {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1"}):
            self.assertEqual(systemd_listen_fd(), 3)
            self.assertNotIn("LISTEN_FDS", os.environ)


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
