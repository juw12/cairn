"""The auth Worker relays only GitHub's two device-flow endpoints (rubric_signin.md gate G7).

worker/cairn-auth.js is a standard ES module, so it runs unchanged in Chromium. A fake fetch
records what would be sent to github.com.
"""

from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import launch_browser  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

REQUIRE = os.environ.get("CAIRN_REQUIRE_BROWSER") == "1"
WORKER = Path(__file__).resolve().parents[1] / "worker" / "cairn-auth.js"

HARNESS = """
async ([source, cases]) => {
  const mod = await import("data:text/javascript;base64," + source);
  const env = { GITHUB_CLIENT_ID: "Iv1.public-client-id", ALLOWED_ORIGINS: "https://juw12.github.io, http://127.0.0.1:4777" };
  const results = [];
  for (const c of cases) {
    const sent = [];
    const fakeFetch = async (url, init) => {
      sent.push({ url, body: String(init.body), method: init.method });
      return new Response(JSON.stringify({ ok: true, from: url }), { status: 200 });
    };
    const request = {
      method: c.method,
      url: "https://cairn-auth.example.workers.dev" + c.path,
      headers: new Headers(c.origin ? { Origin: c.origin } : {}),
      json: async () => c.body ?? {},
    };
    const response = await mod.handle(request, c.env === "empty" ? { ALLOWED_ORIGINS: env.ALLOWED_ORIGINS } : env, fakeFetch);
    results.push({
      status: response.status,
      allowOrigin: response.headers.get("Access-Control-Allow-Origin"),
      body: await response.text(),
      sent,
    });
  }
  return results;
}
"""


@unittest.skipIf(sync_playwright is None and not REQUIRE, "playwright not installed")
class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)
        cls.page = cls.browser.new_page()
        cls.page.goto("about:blank")
        cls.source = base64.b64encode(WORKER.read_bytes()).decode("ascii")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def run_cases(self, cases):
        return self.page.evaluate(HARNESS, [self.source, cases])

    def test_rejects_unknown_origins(self):
        [result] = self.run_cases([{"method": "POST", "path": "/device/code", "origin": "https://evil.example"}])
        self.assertEqual(result["status"], 403)
        self.assertIsNone(result["allowOrigin"])
        self.assertEqual(result["sent"], [])

    def test_preflight_for_allowed_origin(self):
        [result] = self.run_cases([{"method": "OPTIONS", "path": "/device/code", "origin": "https://juw12.github.io"}])
        self.assertEqual(result["status"], 204)
        self.assertEqual(result["allowOrigin"], "https://juw12.github.io")

    def test_device_code_forces_client_id_and_scope(self):
        [result] = self.run_cases([
            {
                "method": "POST",
                "path": "/device/code",
                "origin": "http://127.0.0.1:4777",
                "body": {"client_id": "attacker", "scope": "repo delete_repo"},
            }
        ])
        self.assertEqual(result["status"], 200)
        self.assertEqual(len(result["sent"]), 1)
        sent = result["sent"][0]
        self.assertEqual(sent["url"], "https://github.com/login/device/code")
        self.assertIn("client_id=Iv1.public-client-id", sent["body"])
        self.assertIn("scope=public_repo", sent["body"])
        self.assertNotIn("attacker", sent["body"])
        self.assertNotIn("delete_repo", sent["body"])

    def test_access_token_needs_a_device_code(self):
        good, bad = self.run_cases([
            {"method": "POST", "path": "/oauth/access_token", "origin": "https://juw12.github.io", "body": {"device_code": "3584d83530557fdd1f46af8289938c8ef79f9dc5"}},
            {"method": "POST", "path": "/oauth/access_token", "origin": "https://juw12.github.io", "body": {"device_code": "x&client_id=evil"}},
        ])
        self.assertEqual(good["status"], 200)
        self.assertEqual(good["sent"][0]["url"], "https://github.com/login/oauth/access_token")
        self.assertIn("grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code", good["sent"][0]["body"])
        self.assertEqual(bad["status"], 400)
        self.assertEqual(bad["sent"], [])

    def test_only_two_routes_and_post_only(self):
        other, get, unconfigured = self.run_cases([
            {"method": "POST", "path": "/repos/juw12/cairn", "origin": "https://juw12.github.io"},
            {"method": "GET", "path": "/device/code", "origin": "https://juw12.github.io"},
            {"method": "POST", "path": "/device/code", "origin": "https://juw12.github.io", "env": "empty"},
        ])
        self.assertEqual(other["status"], 404)
        self.assertEqual(get["status"], 404)
        self.assertEqual(unconfigured["status"], 500)
        self.assertEqual(other["sent"] + get["sent"] + unconfigured["sent"], [])


if __name__ == "__main__":
    unittest.main()
