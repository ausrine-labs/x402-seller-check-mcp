#!/usr/bin/env python3
"""Tests for seller_check_mcp.py, with the Atlas files mocked: no network."""

import json
import unittest
from unittest import mock

import seller_check_mcp as m

BOARD = {
    "ranked_total": 647, "categories": ["crypto & markets", "ai models"],
    "window": {"from": "2026-10-14", "to": "2026-10-20", "days": 7},
    "sellers": [
        {"host": "api.botpay.network", "rank": 1, "category": "crypto & markets", "category_rank": 1,
         "price": "$0.1", "customers": 321, "returning": 320, "independent": 1, "payments": 2562, "usdc": 256.02,
         "top_payer_share_pct": 0.3, "one_payer_most": False, "robot_payers": 1, "fake_cost_usd": 7.05,
         "reason": "320 returning customers of 321", "sells": "Mint an NFT.",
         "page": "https://atlas.infoharmoni.com/s/api.botpay.network/"},
        {"host": "llm.example.com", "rank": 2, "category": "ai models", "category_rank": 1, "price": "$0.01",
         "customers": 40, "returning": 9, "independent": 30, "top_payer_share_pct": 62.0, "one_payer_most": True},
    ],
}
PROFILES = {
    "types": {"closed loop": {"means": "Buyers come back often, but almost none pay any other seller.",
                              "priorities": ["Show who the buyers are."]},
              "regulars": {"means": "Independent buyers who come back.", "priorities": []}},
    "sellers": [{"host": "api.botpay.network", "type": "closed loop", "why": "321 paying customers this week"}],
}


def fake_fetch(url):
    return {m.BOARD_URL: BOARD, m.PROFILES_URL: PROFILES}[url]


def call(name, args=None):
    return m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": name, "arguments": args or {}}})["result"]


@mock.patch.object(m, "fetch_json", side_effect=fake_fetch)
class Tools(unittest.TestCase):

    def test_check_listed_seller(self, _):
        r = call("check_seller", {"host": "https://API.botpay.network/path"})
        self.assertFalse(r["isError"])
        t = r["content"][0]["text"]
        for want in ("Customers: 321", "returning (paid on 2+ days): 320", "Rank 1 of 647", "closed loop",
                     "Cost to fake this row: about $7.05", "A wallet is not a person"):
            self.assertIn(want, t)

    def test_one_payer_most_is_said(self, _):
        t = call("check_seller", {"host": "llm.example.com"})["content"][0]["text"]
        self.assertIn("one payer sent over half", t)

    def test_unlisted_seller_is_not_judged(self, _):
        t = call("check_seller", {"host": "unknown.example.org"})["content"][0]["text"]
        self.assertIn("not in the free top 20", t)
        self.assertIn("not proof of anything", t)
        self.assertIn("never pays", t)

    def test_bad_host_is_an_error(self, _):
        r = call("check_seller", {"host": "not a host; rm -rf"})
        self.assertTrue(r["isError"])

    def test_top_sellers_and_category(self, _):
        t = call("top_sellers")["content"][0]["text"]
        self.assertIn("1. api.botpay.network", t)
        self.assertIn("free 20 of 647", t)
        t = call("top_sellers", {"category": "AI models"})["content"][0]["text"]
        self.assertIn("llm.example.com", t)
        self.assertNotIn("botpay", t)
        t = call("top_sellers", {"category": "nothing"})["content"][0]["text"]
        self.assertIn("Categories: crypto & markets, ai models", t)

    def test_seller_types(self, _):
        t = call("seller_types")["content"][0]["text"]
        self.assertIn("closed loop: Buyers come back", t)
        self.assertIn("watch: Show who the buyers are.", t)

    def test_never_says_verified(self, _):
        texts = [call("check_seller", {"host": "api.botpay.network"}), call("top_sellers"), call("seller_types")]
        for r in texts:
            self.assertNotIn("verified", r["content"][0]["text"].lower())
        for _, spec in m.TOOLS.values():
            self.assertNotIn("verified", spec["description"].lower())


class Protocol(unittest.TestCase):

    def test_initialize_and_list(self):
        r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]
        self.assertEqual(r["serverInfo"]["name"], "x402-seller-check")
        names = [t["name"] for t in m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(names, ["check_seller", "top_sellers", "seller_types"])

    def test_notification_gets_no_reply(self):
        self.assertIsNone(m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_non_object_params_are_refused(self):
        for params in ([], "bad", {"name": "top_sellers", "arguments": [1]}):
            r = m.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": params})
            self.assertEqual(r["error"]["code"], -32602)

    def test_null_id_still_gets_a_reply(self):
        r = m.handle({"jsonrpc": "2.0", "id": None, "method": "ping"})
        self.assertEqual(r, {"jsonrpc": "2.0", "id": None, "result": {}})

    def test_unknown_method_and_tool(self):
        self.assertEqual(m.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"], -32601)
        self.assertEqual(call_raw("nope")["error"]["code"], -32602)

    def test_network_failure_is_an_error_not_a_guess(self):
        with mock.patch.object(m, "fetch_json", side_effect=OSError("down")):
            r = call("check_seller", {"host": "api.botpay.network"})
        self.assertTrue(r["isError"])
        self.assertIn("Could not read the Atlas", r["content"][0]["text"])

    def test_redirect_is_refused(self):
        h = m.NoRedirect()
        req = m.urllib.request.Request("https://atlas.infoharmoni.com/board/board.json")
        with self.assertRaises(m.urllib.error.HTTPError):
            h.redirect_request(req, None, 301, "Moved", {}, "https://elsewhere.example/")

    def test_cache(self):
        m._cache.clear()
        body = json.dumps({"ok": 1}).encode()
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        with mock.patch.object(m._opener, "open", return_value=resp) as op:
            self.assertEqual(m.fetch_json("https://x.example/a.json"), {"ok": 1})
            self.assertEqual(m.fetch_json("https://x.example/a.json"), {"ok": 1})
        self.assertEqual(op.call_count, 1)
        m._cache.clear()


def call_raw(name):
    return m.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name}})


if __name__ == "__main__":
    unittest.main()
