import os
import tempfile
import unittest
from unittest.mock import patch

from common import emails


class EmailPoolTests(unittest.TestCase):
    def test_latest_email_requires_token_and_reserves_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = os.path.join(tmp, "emails.txt")
            used = os.path.join(tmp, "used.txt")
            with open(pool, "w", encoding="utf-8") as f:
                f.write("old@example.com----pw----old-rt----old-client\n")
                f.write("new-no-rt@example.com----pw\n")
                f.write("new@example.com----pw----new-rt----new-client\n")
            with patch.object(emails, "EMAILS_FILE", pool):
                with patch.object(emails, "_used_file", return_value=used):
                    with patch.object(emails, "_error_file", return_value=os.path.join(tmp, "errors.txt")):
                        selected = emails.latest_email("grok", require_token=True)
            self.assertEqual(selected[0], "new@example.com")
            with open(used, encoding="utf-8") as f:
                self.assertIn("new@example.com", f.read())

    def test_latest_email_skips_unusable_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = os.path.join(tmp, "emails.txt")
            used = os.path.join(tmp, "used.txt")
            with open(pool, "w", encoding="utf-8") as f:
                f.write("working@example.com----pw----good-rt----client\n")
                f.write("blocked@example.com----pw----bad-rt----client\n")
            with patch.object(emails, "EMAILS_FILE", pool):
                with patch.object(emails, "_used_file", return_value=used):
                    with patch.object(emails, "_error_file", return_value=os.path.join(tmp, "errors.txt")):
                        with patch("common.mailbox._get_access_token",
                                   side_effect=lambda token, _client: "access" if token == "good-rt" else None):
                            selected = emails.latest_email(
                                "grok", require_token=True, validate_token=True
                            )
            self.assertEqual(selected[0], "working@example.com")


if __name__ == "__main__":
    unittest.main()
