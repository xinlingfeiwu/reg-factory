import unittest

import register_grok


class GrokBrowserTests(unittest.TestCase):
    def test_stealth_avoids_global_runtime_monkeypatches(self):
        script = register_grok.GROK_STEALTH_JS
        self.assertNotIn("Object.defineProperty =", script)
        self.assertNotIn("Error.prepareStackTrace", script)
        self.assertNotIn("HTMLIFrameElement.prototype", script)

    def test_browser_starts_at_xai_signup(self):
        self.assertEqual(
            register_grok.GROK_SIGNUP_URL,
            "https://accounts.x.ai/sign-up?redirect=grok-com&return_to=%2F",
        )


if __name__ == "__main__":
    unittest.main()
