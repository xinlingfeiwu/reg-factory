import argparse
import inspect
import os
import unittest
from unittest.mock import patch

import register_chatgpt
import register_three_platforms
import run_full_flow
import oauth_codex


class ChatGPTFlowTests(unittest.TestCase):
    def test_browser_profile_uses_configured_clash_proxy(self):
        with patch.dict(
            os.environ,
            {"CLASH_PROXY": "http://proxy-user:proxy-pass@127.0.0.1:7897"},
        ):
            fields = register_chatgpt.clash_browser_proxy_fields()

        self.assertEqual(fields["proxyType"], "http")
        self.assertEqual(fields["host"], "127.0.0.1")
        self.assertEqual(fields["port"], "7897")
        self.assertEqual(fields["proxyUserName"], "proxy-user")
        self.assertEqual(fields["proxyPassword"], "proxy-pass")

    def test_region_rejection_is_parsed_from_auth_response(self):
        error = register_chatgpt._openai_error_from_text(
            '{"error":{"code":"unsupported_country_region_territory",'
            '"message":"Country, region, or territory not supported"}}',
            status=403,
            url="/api/accounts/create",
        )

        self.assertEqual(error["code"], "unsupported_country_region_territory")
        self.assertEqual(error["status"], 403)

    def test_blank_codex_numeric_env_uses_default(self):
        with patch.dict(os.environ, {"CODEX_SMS_TIMEOUT": ""}):
            self.assertEqual(register_chatgpt._env_int("CODEX_SMS_TIMEOUT", 150), 150)

    def test_oauth_can_continue_when_cookie_less_probe_is_blocked(self):
        with patch.object(register_chatgpt, "CF_NODES", ["node-a"]):
            with patch.object(register_chatgpt, "_activate_cf_node", return_value="node-a"):
                with patch.object(
                    register_chatgpt,
                    "_probe_chatgpt_node",
                    return_value=(False, "JP", 403),
                ):
                    selected = register_chatgpt.select_chatgpt_node(
                        "auto", allow_blocked=True
                    )
        self.assertEqual(selected, "node-a")

    def test_three_platform_command_pins_chatgpt_node(self):
        args = argparse.Namespace(
            timeout=600,
            node="level1-test-node",
            keep_on_fail=False,
            import_c2a=False,
            codex=False,
        )
        command = register_three_platforms.build_command(
            "chatgpt",
            args,
            ("mail@example.com", "password", "refresh-token", "client-id"),
        )

        node_index = command.index("--node")
        self.assertEqual(command[node_index + 1], "level1-test-node")

    def test_platform_failure_returns_nonzero(self):
        results = [("chatgpt", False, 1, "chatgpt.log")]
        self.assertEqual(register_three_platforms.results_exit_code(results), 1)
        self.assertEqual(
            register_three_platforms.results_exit_code(
                [("chatgpt", True, 0, "chatgpt.log")]
            ),
            0,
        )

    def test_full_flow_redacts_credentials(self):
        rendered = run_full_flow.redact_command(
            ["python", "child.py", "--password", "mail-pass", "--token", "graph-token"]
        )
        self.assertNotIn("mail-pass", rendered)
        self.assertNotIn("graph-token", rendered)
        self.assertEqual(rendered.count("***"), 2)

    def test_standalone_oauth_propagates_failure_exit_code(self):
        source = inspect.getsource(oauth_codex.main)
        self.assertNotIn("sys.exit(", source)
        module_source = inspect.getsource(oauth_codex)
        self.assertIn("sys.exit(asyncio.run(main()))", module_source)


if __name__ == "__main__":
    unittest.main()
