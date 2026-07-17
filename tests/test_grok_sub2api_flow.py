import argparse
import unittest

import register_three_platforms


class GrokSub2ApiFlowTests(unittest.TestCase):
    def test_three_platform_command_forwards_sub2api_options(self):
        args = argparse.Namespace(
            timeout=600,
            node="auto",
            grok_sub2api=True,
            grok_sub2api_group="grok-prod",
        )

        command = register_three_platforms.build_command(
            "grok",
            args,
            ("mail@example.com", "password", "token", "client-id"),
        )

        self.assertIn("--sub2api", command)
        self.assertEqual(command[-2:], ["--sub2api-group", "grok-prod"])


if __name__ == "__main__":
    unittest.main()
