import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from common import mailbox


class MailboxTests(unittest.TestCase):
    def test_link_reader_skips_old_fractional_graph_timestamp(self):
        messages = [
            {
                "subject": "Claude magic link",
                "from": "login@anthropic.com",
                "body": "https://claude.ai/magic-link#old",
                "received": "2026-07-17T10:00:00.1234567Z",
            },
            {
                "subject": "Claude magic link",
                "from": "login@anthropic.com",
                "body": "https://claude.ai/magic-link#fresh",
                "received": "2026-07-17T11:00:00.7654321Z",
            },
        ]
        cutoff = datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc).timestamp()
        with patch.object(mailbox, "_get_access_token", return_value="access"):
            with patch.object(mailbox, "fetch_messages", return_value=messages):
                link = mailbox.get_link_by_token(
                    "user@example.com",
                    "refresh-token",
                    link_regex=r"https://claude\.ai/magic-link#[a-z]+",
                    sender_contains=("anthropic",),
                    subject_contains=(),
                    must_contain="claude.ai/magic-link",
                    max_wait=1,
                    received_after=cutoff,
                )
        self.assertEqual(link, "https://claude.ai/magic-link#fresh")


if __name__ == "__main__":
    unittest.main()
