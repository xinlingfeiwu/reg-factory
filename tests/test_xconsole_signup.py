import unittest

from xconsole_client.client import XConsoleAuthClient


class SignupResponseTests(unittest.TestCase):
    def test_unknown_non_error_rsc_shape_continues_to_sso_extraction(self):
        self.assertTrue(
            XConsoleAuthClient._signup_response_looks_ok(
                '2:["new-rsc-shape",{"status":"pending"}]',
                ["next-auth.csrf-token=value; Path=/"],
                {},
            )
        )

    def test_structured_error_still_fails(self):
        self.assertFalse(
            XConsoleAuthClient._signup_response_looks_ok(
                '0:E{"message":"turnstile_failed"}',
                [],
                {},
            )
        )


if __name__ == "__main__":
    unittest.main()
