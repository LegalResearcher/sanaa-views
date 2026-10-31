import json
import os
import tempfile
import unittest

from account_rotation import select_account


class AccountRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = self.temp.name
        self.env = {}
        for number in range(1, 10):
            self.env[f"SITE_USERNAME_{number}"] = f"user-{number}"
            self.env[f"SITE_PASSWORD_{number}"] = f"password-{number}"

    def tearDown(self):
        self.temp.cleanup()

    def test_round_robin_persists_and_wraps(self):
        selected = [select_account(self.state_dir, self.env) for _ in range(20)]
        expected = list(range(1, 10)) + list(range(1, 10)) + [1, 2]
        self.assertEqual([entry[0] for entry in selected], expected)
        self.assertEqual([entry[1] for entry in selected], [f"user-{n}" for n in expected])
        self.assertEqual([entry[2] for entry in selected], [f"password-{n}" for n in expected])

        cursor_path = os.path.join(self.state_dir, "account_rotation.json")
        with open(cursor_path, "r", encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"next_account": 3})
        with open(cursor_path, "r", encoding="utf-8") as file:
            saved = file.read()
        self.assertNotIn("user-", saved)
        self.assertNotIn("password-", saved)

    def test_missing_secret_fails_without_advancing(self):
        del self.env["SITE_PASSWORD_2"]
        with open(os.path.join(self.state_dir, "account_rotation.json"), "w", encoding="utf-8") as file:
            json.dump({"next_account": 2}, file)
        with self.assertRaisesRegex(RuntimeError, "SITE_PASSWORD_2"):
            select_account(self.state_dir, self.env)
        with open(os.path.join(self.state_dir, "account_rotation.json"), "r", encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"next_account": 2})


if __name__ == "__main__":
    unittest.main()
