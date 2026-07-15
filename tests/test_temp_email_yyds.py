import unittest

from common import temp_email


class FakeResponse:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text
        self.content = b"x" if data is not None or text else b""

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


class YydsMailTests(unittest.TestCase):
    def test_normalizes_marketing_and_pasted_endpoint_urls(self):
        self.assertEqual(
            temp_email._norm_yyds_base("vip.215.im/v1/accounts"),
            "https://maliapi.215.im",
        )
        self.assertEqual(
            temp_email._norm_yyds_base("https://maliapi.215.im/v1"),
            "https://maliapi.215.im",
        )

    def test_create_uses_normalized_api_root(self):
        sess = FakeSession([
            FakeResponse(data={"data": {"id": "box-1", "address": "a@example.com", "token": "mail-token"}}),
        ])

        mailbox = temp_email._yyds_create(
            None, "example.com", None, "AC-test", "https://vip.215.im/v1/accounts", sess,
        )

        self.assertEqual(mailbox["id"], "box-1")
        self.assertEqual(sess.calls[0][1], "https://maliapi.215.im/v1/accounts")

    def test_fetch_prefers_mailbox_token_and_public_messages_route(self):
        sess = FakeSession([
            FakeResponse(data={"data": {"messages": []}}),
        ])

        messages = temp_email._yyds_fetch(
            "box-1", "a@example.com", "mail-token", "AC-test", None, sess,
        )

        self.assertEqual(messages, [])
        _, url, kwargs = sess.calls[0]
        self.assertEqual(url, "https://maliapi.215.im/v1/messages")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer mail-token"})

    def test_fetch_falls_back_to_api_key_after_token_404(self):
        sess = FakeSession([
            FakeResponse(status_code=404, data={"error": "not found"}),
            FakeResponse(data={"data": {"messages": []}}),
        ])

        temp_email._yyds_fetch(
            "box-1", "a@example.com", "mail-token", "AC-test", None, sess,
        )

        self.assertEqual(len(sess.calls), 2)
        self.assertEqual(sess.calls[1][2]["headers"], {"X-API-Key": "AC-test"})

    def test_fetch_reports_404_after_all_routes_fail(self):
        sess = FakeSession([
            FakeResponse(status_code=404, data={"error": "not found"}, text="not found"),
            FakeResponse(status_code=404, data={"error": "not found"}, text="not found"),
            FakeResponse(status_code=404, data={"error": "not found"}, text="not found"),
        ])

        with self.assertRaisesRegex(RuntimeError, "YYDS fetch 404"):
            temp_email._yyds_fetch(
                "box-1", "a@example.com", "mail-token", "AC-test", None, sess,
            )


if __name__ == "__main__":
    unittest.main()
