"""announce.py: after a publish, search engines are told which public pages changed, once those are online."""

import json
import unittest
import urllib.parse

from cairn import announce, publish, site

BASE = "https://me.github.io/brain/"
PAGE = "<h1>new text</h1>\n"


def web(**changes) -> dict:
    report = {
        "url": BASE, "live": True, "note": "",
        "changed": [BASE, BASE + "notes/built-x.html"],
        "feed": BASE + "feed.xml",
        "check": {"url": BASE + "feed.xml", "sha": publish.git_blob_sha(PAGE)},
    }
    report.update(changes)
    return report


class Web:
    """The internet as announce.py sees it: the live page (old for the first `old` reads), IndexNow and the hub."""

    def __init__(self, old=0, indexnow=200, hub=204):
        self.old, self.indexnow, self.hub = old, indexnow, hub
        self.calls = []

    def __call__(self, url, data=None, content_type="", timeout=20):
        self.calls.append((url, data, content_type))
        if url.startswith(BASE):
            self.old -= 1
            return 200, (b"<h1>old text</h1>\n" if self.old >= 0 else PAGE.encode())
        if url == announce.INDEXNOW:
            return self.indexnow, b""
        if url == site.HUB:
            return self.hub, b""
        return 404, b""

    def posts(self):
        return [call for call in self.calls if call[1] is not None]


class AnnounceTests(unittest.TestCase):
    def test_waits_until_the_publish_is_online_then_tells_both(self):
        internet, naps = Web(old=2), []
        told = announce.after_publish(web(), fetch=internet, sleep=naps.append)
        self.assertEqual(told, {"online": True, "indexnow": 200, "hub": 204})
        self.assertEqual(naps, [announce.POLL_SECONDS, announce.POLL_SECONDS])
        reads = [call[0] for call in internet.calls if call[1] is None]
        self.assertTrue(all(url.startswith(BASE + "feed.xml?v=") for url in reads))  # a fresh address, never a cached copy
        self.assertEqual(len(internet.posts()), 2, "nothing is sent before the pages are online")

    def test_what_is_sent_is_public_addresses_and_the_sites_own_key(self):
        internet = Web()
        announce.after_publish(web(), fetch=internet, sleep=lambda seconds: None)
        (url, data, kind), (hub_url, form, form_kind) = internet.posts()
        self.assertEqual((url, kind), (announce.INDEXNOW, "application/json; charset=utf-8"))
        body = json.loads(data)
        key = site.indexnow_key(BASE)
        self.assertEqual(body, {
            "host": "me.github.io", "key": key, "keyLocation": f"{BASE}{key}.txt",
            "urlList": [BASE, BASE + "notes/built-x.html"],
        })
        self.assertEqual((hub_url, form_kind), (site.HUB, "application/x-www-form-urlencoded"))
        self.assertEqual(urllib.parse.parse_qs(form.decode()), {"hub.mode": ["publish"], "hub.url": [BASE + "feed.xml"]})

    def test_gives_up_quietly_when_the_pages_do_not_come_online(self):
        internet = Web(old=10 ** 6)
        told = announce.after_publish(web(), wait=30, fetch=internet, sleep=lambda seconds: None)
        self.assertEqual(told, {"online": False, "indexnow": None, "hub": None})
        self.assertEqual(internet.posts(), [])
        self.assertIn("not online yet", announce.summary(told))

    def test_nothing_is_told_when_nothing_changed_or_the_page_is_not_served(self):
        internet = Web()
        self.assertIsNone(announce.after_publish(None, fetch=internet))
        self.assertIsNone(announce.after_publish({"url": BASE, "live": True, "note": ""}, fetch=internet))
        self.assertIsNone(announce.after_publish(web(live=False), fetch=internet))
        self.assertEqual(internet.calls, [])

    def test_only_the_feed_changed_or_only_pages_changed(self):
        internet = Web()
        told = announce.after_publish(web(feed="", check={"url": BASE, "sha": publish.git_blob_sha(PAGE)}), fetch=internet, sleep=lambda s: None)
        self.assertEqual((told["indexnow"], told["hub"]), (200, None))
        self.assertEqual(announce.summary(told), "Told about the changed pages: Bing and Yandex (IndexNow).")

    def test_a_refusal_is_said_plainly(self):
        internet = Web(indexnow=403, hub=0)
        told = announce.after_publish(web(), fetch=internet, sleep=lambda seconds: None)
        self.assertEqual(announce.summary(told), "Told about the changed pages: IndexNow refused (403), the feed hub refused (0).")


if __name__ == "__main__":
    unittest.main()
