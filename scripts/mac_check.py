"""Is the Mac app up? Run with Cairn's own Python after docs/mac-install.sh and `open Cairn.app`.

    python scripts/mac_check.py              # the app is listed as Cairn, has a menu-bar icon, its page answers
    python scripts/mac_check.py --window     # and its window is on the screen
    python scripts/mac_check.py --no-window  # and its window is hidden (started with --hidden, or closed)

Reads what macOS itself says (the list of running apps and of windows), so it needs no permission.
"""
import re
import subprocess
import sys
import time
import urllib.request

import Quartz

BUNDLE_ID = "io.github.juw12.cairn"
PORT = 47777
MENU_BAR_LAYER = 25


def app_entry() -> dict:
    listing = subprocess.run(["lsappinfo", "list"], capture_output=True, text=True).stdout
    for block in re.split(r'(?m)^(?=\s*\d+\) ")', listing):  # one entry starts with: 12) "Name" ASN:...
        if f'bundleID="{BUNDLE_ID}"' not in block:
            continue
        name = re.search(r'"([^"]*)"', block)
        pid = re.search(r"\bpid = (\d+)", block)
        bundle = re.search(r'bundle path="([^"]*)"', block)
        return {
            "name": name.group(1) if name else "",
            "pid": int(pid.group(1)) if pid else 0,
            "bundle": bundle.group(1) if bundle else "",
            "said": block.strip(),
        }
    return {}


def windows(pid: int) -> list[dict]:
    found = []
    for window in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID):
        if window.get("kCGWindowOwnerPID") == pid:
            bounds = window["kCGWindowBounds"]
            found.append({
                "layer": window.get("kCGWindowLayer"),
                "onscreen": bool(window.get("kCGWindowIsOnscreen")),
                "width": int(bounds["Width"]),
                "height": int(bounds["Height"]),
            })
    return found


def page_title() -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5) as reply:
        page = reply.read().decode("utf-8", "replace")
    return page.split("<title>")[1].split("</title>")[0] if "<title>" in page else ""


def look(want_window: bool | None) -> list[str]:
    """What is wrong, as sentences; empty when all is as it should be."""
    entry = app_entry()
    if not entry.get("pid"):
        return ["macOS does not list Cairn as a running app"]
    wrong = []
    if entry["name"] != "Cairn":
        wrong.append(f"macOS calls the app {entry['name']!r}, not Cairn")
    if not entry.get("bundle", "").endswith("Cairn.app"):
        wrong.append(f"it runs from {entry.get('bundle')!r}, not from Cairn.app")
    listed = windows(entry["pid"])
    if not any(w["layer"] == MENU_BAR_LAYER and w["onscreen"] for w in listed):
        wrong.append("no menu-bar icon")
    shown = [w for w in listed if w["layer"] == 0 and w["onscreen"] and w["width"] >= 380 and w["height"] >= 400]
    if want_window is True and not shown:
        wrong.append("the window is not on the screen")
    if want_window is False and shown:
        wrong.append("the window is on the screen, and should be hidden")
    try:
        title = page_title()
        if "Cairn" not in title:
            wrong.append(f"the page on port {PORT} is not Cairn ({title!r})")
    except OSError as error:
        wrong.append(f"the page on port {PORT} does not answer: {error}")
    return wrong


def main(argv: list[str]) -> int:
    want_window = True if "--window" in argv else False if "--no-window" in argv else None
    deadline = time.time() + 90
    wrong = look(want_window)
    while wrong and time.time() < deadline:  # the first start makes a brain and loads the window libraries
        time.sleep(3)
        wrong = look(want_window)
    for line in wrong:
        print("WRONG:", line)
    if wrong:
        print("macOS said about the app:", app_entry().get("said", "nothing"), sep="\n")
    if not wrong:
        state = {True: ", window on the screen", False: ", window hidden", None: ""}[want_window]
        print(f"OK: Cairn runs from Cairn.app, menu-bar icon there, page answers{state}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
