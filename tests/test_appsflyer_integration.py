import unittest
from unittest.mock import Mock

from appsflyer_integration.client import AppsFlyerClient
from appsflyer_integration.normalize import normalize_attribution


class AppsFlyerClientTests(unittest.TestCase):
    def test_token_is_not_exposed_by_headers_repr(self):
        client = AppsFlyerClient("secret-token")
        self.assertNotIn("secret-token", repr(client))

    def test_list_apps_uses_bearer_and_pagination(self):
        first = Mock(ok=True, status_code=200)
        first.json.return_value = {"data": [{"id": "a"}], "links": {"next": "https://hq1.appsflyer.com/api/mng/apps?offset=1"}}
        second = Mock(ok=True, status_code=200)
        second.json.return_value = {"data": [{"id": "b"}], "links": {}}
        session = Mock()
        session.get.side_effect = [first, second]
        result = AppsFlyerClient("secret-token", session=session).list_apps()
        self.assertEqual([item["id"] for item in result], ["a", "b"])
        self.assertEqual(session.get.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer secret-token")

    def test_normalization_does_not_claim_material_effect(self):
        result = normalize_attribution({"Campaign (c)": "C1", "Adset": "S1", "Ad": "A1"}, "app")
        self.assertEqual(result["available_granularity"], "ad")
        self.assertFalse(result["material_effect_confirmed"])
        self.assertEqual(len(result["attribution_key"]), 24)


if __name__ == "__main__":
    unittest.main()
