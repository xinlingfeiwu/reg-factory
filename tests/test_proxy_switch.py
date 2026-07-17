import unittest
from unittest.mock import patch

from common import proxy_switch


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class FakeCurlRequests:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get(self, *_args, **_kwargs):
        return next(self.responses)


class ProxySwitchTests(unittest.TestCase):
    def test_concrete_nodes_excludes_subscription_metadata(self):
        with patch.object(proxy_switch, "list_nodes", return_value=[
            "套餐：LV4套餐",
            "剩余：100 GB",
            "重置：10天后",
            "官网：https://example.com",
            "level4-日本01",
        ]):
            self.assertEqual(proxy_switch.concrete_nodes(), ["level4-日本01"])

    def test_required_markers_reject_incomplete_page(self):
        fake = FakeCurlRequests([
            FakeResponse(200, "<html>generic page</html>"),
            FakeResponse(200, '<script src="/_next/static/chunks/a.js"></script>self.__next_f.push'),
        ])
        with patch.object(proxy_switch, "concrete_nodes", return_value=["node1", "node2"]):
            with patch.object(proxy_switch, "set_node"):
                with patch("curl_cffi.requests.get", side_effect=fake.get):
                    with patch("time.sleep"):
                        with patch("random.shuffle", side_effect=lambda items: None):
                            node = proxy_switch.find_working_node(
                                test_url="https://accounts.x.ai/sign-up",
                                required_markers=("/_next/static/chunks/", "self.__next_f.push"),
                                verbose=False,
                            )
        self.assertEqual(node, "node2")


if __name__ == "__main__":
    unittest.main()
