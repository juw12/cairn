"""Tell search engines that pages of a public brain's web page are new or changed.

A search engine finds a page by itself in time, through the sitemap and through links. Two doors make it
sooner, and neither needs an account:

- IndexNow, which Bing, Yandex, Naver and Seznam share: the addresses that changed are posted with the
  site's key, and the site shows the same key in a file (site.indexnow_key), which is the proof that the
  site itself asked.
- WebSub, which Google reads feeds through: the hub is told that feed.xml changed and fetches it.

Only addresses of pages that are already public are sent, never note text, and nothing is sent to GitHub's
own API from here. GitHub Pages needs about a minute to put a commit online, so the telling waits until
one changed page reads the way it was published: a hub that fetched the old feed would learn nothing.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import publish, site

INDEXNOW = "https://api.indexnow.org/indexnow"
WAIT_SECONDS = 240
POLL_SECONDS = 10


def _request(url: str, data: bytes | None = None, content_type: str = "", timeout: int = 20) -> tuple[int, bytes]:
    """(status, body) of a plain web request without any sign-in. 0 = no answer."""
    headers = {"User-Agent": "cairn"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=publish.ssl_context()) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError):
        return 0, b""


def is_online(check: dict, fetch=_request) -> bool:
    """True when the page at check["url"] reads exactly as it was published (check["sha"])."""
    mark = "&" if "?" in check["url"] else "?"
    status, body = fetch(f"{check['url']}{mark}v={check['sha'][:12]}")  # a new address each publish: no cached copy
    return status == 200 and publish.git_blob_sha(body.decode("utf-8", errors="replace")) == check["sha"]


def tell(web: dict, fetch=_request) -> dict:
    """Send the changed addresses. Returns {"indexnow": status or None, "hub": status or None}."""
    told = {"indexnow": None, "hub": None}
    base, pages = web["url"], [page for page in web.get("changed", []) if page]
    if pages:
        key = site.indexnow_key(base)
        body = {
            "host": urllib.parse.urlsplit(base).netloc,
            "key": key,
            "keyLocation": f"{base}{key}.txt",
            "urlList": pages[:10000],
        }
        told["indexnow"], _ = fetch(INDEXNOW, json.dumps(body).encode("utf-8"), "application/json; charset=utf-8")
    if web.get("feed"):
        form = urllib.parse.urlencode({"hub.mode": "publish", "hub.url": web["feed"]}).encode("ascii")
        told["hub"], _ = fetch(site.HUB, form, "application/x-www-form-urlencoded")
    return told


def after_publish(web: dict | None, wait: int = WAIT_SECONDS, fetch=_request, sleep=time.sleep) -> dict | None:
    """Wait until the publish is online, then tell the search engines. None when there is nothing to tell.

    `web` is report["site"] of publish.publish_to_github. Returns tell()'s answer plus "online": False when
    the pages did not come online in time (nothing is sent then; the next publish tells them)."""
    if not web or not web.get("live") or not (web.get("changed") or web.get("feed")):
        return None
    check, waited = web.get("check"), 0
    while check and not is_online(check, fetch):
        if waited >= wait:
            return {"online": False, "indexnow": None, "hub": None}
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    return {"online": True, **tell(web, fetch)}


def summary(told: dict | None) -> str:
    """One line for a person: who was told."""
    if not told:
        return ""
    if not told["online"]:
        return "The pages are not online yet, so search engines were not told. The next publish tells them."
    parts = []
    if told["indexnow"] is not None:
        parts.append("Bing and Yandex (IndexNow)" if 200 <= told["indexnow"] < 300 else f"IndexNow refused ({told['indexnow']})")
    if told["hub"] is not None:
        parts.append("Google's feed hub" if 200 <= told["hub"] < 300 else f"the feed hub refused ({told['hub']})")
    return "Told about the changed pages: " + ", ".join(parts) + "." if parts else ""
