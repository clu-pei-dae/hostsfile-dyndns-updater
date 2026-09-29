import os
import tempfile
import unittest

from hostsfile_dyndns_updater import config, passwords


class ConfigTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.ini")
            with open(p, "w") as fh:
                fh.write(text)
            return config.load(p)

    def test_rejects_bad_configs(self):
        h = passwords.hash_password("x" * 20, passwords.MIN_ITERATIONS)
        for text in ("[server]\n",
                     "[host a]\nhostname=bad name\nusername=u\npassword_hash=%s\n" % h,
                     "[host a]\nhostname=a.example\nusername=u\npassword_hash=plain\n",
                     "[host a]\nhostname=a.example\nusername=u\n"):
            with self.assertRaises(config.ConfigError, msg=text):
                self.load(text)

    def test_password_roundtrip(self):
        h = passwords.hash_password("s3cret-s3cret-s3cret", passwords.MIN_ITERATIONS)
        self.assertTrue(passwords.verify_password("s3cret-s3cret-s3cret", h))
        self.assertFalse(passwords.verify_password("nope", h))


if __name__ == "__main__":
    unittest.main()
