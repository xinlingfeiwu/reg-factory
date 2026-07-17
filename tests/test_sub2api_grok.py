import unittest
from unittest.mock import patch

from common import uploaders


class Sub2ApiGrokTests(unittest.TestCase):
    def test_imports_sso_into_grok_group(self):
        responses = [
            {"access_token": "admin-token"},
            [
                {"id": 4, "name": "grok", "platform": "openai"},
                {"id": 9, "name": "grok", "platform": "grok"},
            ],
            {"created": [{"email": "new@example.com"}], "failed": []},
        ]
        with patch.object(uploaders, "_sub2api_request", side_effect=responses) as request:
            ok, message = uploaders.upload_sub2api_grok(
                "https://sub.example.com",
                "admin@example.com",
                "secret",
                "grok",
                "sso-token",
                account_email="new@example.com",
                proxy_id=12,
            )

        self.assertTrue(ok)
        self.assertIn("new@example.com", message)
        import_call = request.call_args_list[2]
        self.assertEqual(import_call.args[1], "/api/v1/admin/grok/sso-to-oauth")
        self.assertEqual(import_call.kwargs["body"]["sso_tokens"], ["sso-token"])
        self.assertEqual(import_call.kwargs["body"]["group_ids"], [9])
        self.assertEqual(import_call.kwargs["body"]["name"], "new@example.com")
        self.assertEqual(import_call.kwargs["body"]["proxy_id"], 12)
        self.assertEqual(import_call.kwargs["retries"], 1)

    def test_rejects_same_name_openai_group(self):
        responses = [
            {"access_token": "admin-token"},
            [{"id": 4, "name": "grok", "platform": "openai"}],
        ]
        with patch.object(uploaders, "_sub2api_request", side_effect=responses) as request:
            ok, message = uploaders.upload_sub2api_grok(
                "https://sub.example.com",
                "admin@example.com",
                "secret",
                "grok",
                "sso-token",
            )

        self.assertFalse(ok)
        self.assertIn("grok 分组", message)
        self.assertEqual(request.call_count, 2)

    def test_reports_conversion_failure(self):
        responses = [
            {"access_token": "admin-token"},
            [{"id": 9, "name": "grok", "platform": "grok"}],
            {"created": [], "failed": [{"index": 1, "error": "device flow denied"}]},
        ]
        with patch.object(uploaders, "_sub2api_request", side_effect=responses):
            ok, message = uploaders.upload_sub2api_grok(
                "https://sub.example.com",
                "admin@example.com",
                "secret",
                "grok",
                "sso-token",
            )

        self.assertFalse(ok)
        self.assertIn("device flow denied", message)

    def test_falls_back_to_local_oauth_when_remote_conversion_fails(self):
        responses = [
            {"access_token": "admin-token"},
            [{"id": 9, "name": "grok", "platform": "grok"}],
            {"created": [], "failed": [{"error": "xAI OAuth HTTP 403"}]},
            {"items": []},
            {"id": 99, "platform": "grok", "type": "oauth"},
        ]
        credentials = {
            "access_token": "access",
            "refresh_token": "refresh",
            "email": "new@example.com",
        }
        with patch.object(uploaders, "_sub2api_request", side_effect=responses) as request:
            with patch(
                "common.grok_oauth.convert_grok_sso_local",
                return_value=(credentials, "new@example.com"),
            ) as convert:
                ok, message = uploaders.upload_sub2api_grok(
                    "https://sub.example.com",
                    "admin@example.com",
                    "secret",
                    "grok",
                    "sso-token",
                    account_email="new@example.com",
                    local_proxy="http://127.0.0.1:7897",
                )

        self.assertTrue(ok)
        self.assertIn("本机 OAuth 回退", message)
        convert.assert_called_once()
        create_call = request.call_args_list[4]
        self.assertEqual(create_call.args[1], "/api/v1/admin/accounts")
        self.assertEqual(create_call.kwargs["body"]["platform"], "grok")
        self.assertEqual(create_call.kwargs["body"]["group_ids"], [9])
        self.assertEqual(create_call.kwargs["body"]["credentials"], credentials)
        self.assertFalse(create_call.kwargs["use_env_proxy"])


if __name__ == "__main__":
    unittest.main()
