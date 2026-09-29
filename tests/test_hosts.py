import os
import tempfile
import unittest

from hostsfile_dyndns_updater import hosts

BASE = "127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost\n"


class UpdateContentTest(unittest.TestCase):
    def test_appends_new_entry(self):
        out = hosts.update_content(BASE, "home.example.org", "203.0.113.7")
        self.assertEqual(out, BASE + f"203.0.113.7\thome.example.org  {hosts.MARKER}\n")

    def test_updates_existing_entry_in_place(self):
        src = BASE + "198.51.100.1\thome.example.org  # mine\n10.0.0.1 other\n"
        out = hosts.update_content(src, "HOME.example.org", "203.0.113.7")
        self.assertIn("203.0.113.7\tHOME.example.org  # mine\n10.0.0.1 other\n", out)
        self.assertNotIn("198.51.100.1", out)

    def test_families_are_independent(self):
        src = hosts.update_content(BASE, "h.example.org", "203.0.113.7")
        out = hosts.update_content(src, "h.example.org", "2001:db8::1")
        self.assertIn("203.0.113.7\th.example.org", out)
        self.assertIn("2001:db8::1\th.example.org", out)

    def test_shared_line_keeps_other_names(self):
        src = "198.51.100.1 a.example.org h.example.org\n"
        out = hosts.update_content(src, "h.example.org", "203.0.113.7")
        self.assertIn("198.51.100.1 a.example.org\n", out)
        self.assertIn("203.0.113.7\th.example.org", out)

    def test_duplicates_collapse_and_idempotent(self):
        src = "1.1.1.1 h.example.org\n2.2.2.2 h.example.org\n"
        out = hosts.update_content(src, "h.example.org", "203.0.113.7")
        self.assertEqual(out.count("h.example.org"), 1)
        self.assertEqual(hosts.update_content(out, "h.example.org", "203.0.113.7"), out)

    def test_hostname_validation(self):
        for bad in ("", "a b", "a\nb", "-a.example", "a..b", "a_b", "x" * 300):
            self.assertFalse(hosts.valid_hostname(bad), bad)
        self.assertTrue(hosts.valid_hostname("home.example.org"))


class UpdateFileTest(unittest.TestCase):
    def test_file_roundtrip_keeps_mode(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hosts")
            with open(path, "w") as fh:
                fh.write(BASE)
            os.chmod(path, 0o640)
            self.assertTrue(hosts.update_hosts_file(path, "h.example.org", "203.0.113.7"))
            self.assertFalse(hosts.update_hosts_file(path, "h.example.org", "203.0.113.7"))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)
            self.assertEqual(os.listdir(d), ["hosts"])


if __name__ == "__main__":
    unittest.main()
