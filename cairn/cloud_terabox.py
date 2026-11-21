"""Put files in a TeraBox account through TeraBox's own web page. Run as a script by cairn.cloud_backup.

    cloud_terabox.py window                          open the sign-in window and leave it open
    cloud_terabox.py check                           is the profile signed in?
    cloud_terabox.py upload-folder LOCAL PARENT      LOCAL goes up as PARENT/<name of LOCAL>, which must not exist yet
    cloud_terabox.py remove PARENT NAME...           those rows of PARENT go to TeraBox's recycle bin
    cloud_terabox.py list FOLDER                     names and sizes TeraBox holds there

TeraBox has no upload tool for a personal account, and its terms allow only its "publicly supported
interfaces". Its web page is one, so this steers a Chrome that has a profile of its own, signed in once by
the person (CAIRN_HOME/cloud-backup/chrome). It reads no cookie and calls no TeraBox address itself: it
presses what a person would press, gives the page's own upload field the files, and reads the list the page
is sent. What is removed goes to TeraBox's recycle bin, which keeps it 10 days.

Nothing is ever uploaded under a name that is there already: TeraBox removes in the background, and a file
that arrives while its old self is still going is kept under another name. So a new copy gets a new folder
name, and the old folder is removed only after the new one has been checked.

It depends on TeraBox's page staying as it is. When the page changes, this says what it could not find and
changes nothing. Needs the `playwright` package (cairn cloud setup puts it in a private environment) and
Google Chrome. Not imported by Cairn itself: Cairn keeps no dependency.

The last line printed is one JSON object: {"ok": true, ...} or {"ok": false, "error": "..."}.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("CAIRN_TERABOX_PORT") or 9333)
START = "https://www.terabox.com/main?category=all"
ROW = "tr.wp-s-pan-table__body-row"


class Stop(Exception):
    """Something is not as expected; the message says what. Nothing more is changed."""


def home() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn") / "cloud-backup"


def profile() -> Path:
    return Path(os.environ.get("CAIRN_TERABOX_PROFILE") or home() / "chrome")


def say(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def chrome() -> str:
    places = [
        os.environ.get("CAIRN_CHROME", ""),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        shutil.which("google-chrome") or "", shutil.which("google-chrome-stable") or "", shutil.which("chromium") or "",
    ]
    found = next((place for place in places if place and Path(place).is_file()), "")
    if not found:
        raise Stop("Google Chrome is not on this computer.")
    return found


# ---------------------------------------------------------------- the window


def answering() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=5).read()
        return True
    except OSError:
        return False


def start_window(hidden: bool) -> bool:
    """Start Chrome on the profile. False when a window on that profile is already open (the person's)."""
    if answering():
        return False
    profile().mkdir(parents=True, exist_ok=True)
    flags = [
        f"--user-data-dir={profile()}", f"--remote-debugging-port={PORT}", "--remote-debugging-address=127.0.0.1",
        "--no-first-run", "--no-default-browser-check", "--window-size=1300,900",
        # keep working when the window is covered or the screen is locked
        "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
    ]
    if hidden:
        # No window: the work must not take the screen or the keyboard from the person. Signing in is done in a
        # window (the `window` command); a profile that is signed in works the same without one.
        flags += ["--headless=new"]
    subprocess.Popen([chrome(), *flags, START], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(80):
        if answering():
            time.sleep(5)
            return True
        time.sleep(0.5)
    stop_window(True)
    raise Stop("Chrome did not start.")


def stop_window(started: bool) -> None:
    """Close the Chrome this run started (found by its profile folder). One the person opened is left open."""
    if not started:
        return
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*"
                  + str(profile()).replace("'", "''") + "*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True)
    else:
        subprocess.run(["pkill", "-f", f"user-data-dir={profile()}"], capture_output=True)
    time.sleep(3)
    trim_profile()


# What Chrome keeps to be quick next time, and fetches again when it is gone. Hundreds of MB after a few runs.
# The sign-in is not in these.
CACHES = ("Default/Cache", "Default/Code Cache", "Default/GPUCache", "Default/DawnGraphiteCache", "Default/DawnWebGPUCache",
          "Default/Service Worker/CacheStorage", "Default/Service Worker/ScriptCache", "GrShaderCache", "ShaderCache",
          "GraphiteDawnCache", "Crashpad", "component_crx_cache", "extensions_crx_cache", "optimization_guide_model_store",
          "OptGuideOnDeviceModel", "Safe Browsing", "screen_ai", "WasmTtsEngine", "hyphen-data", "ZxcvbnData",
          "OnDeviceHeadSuggestModel", "BrowserMetrics", "segmentation_platform", "SODA", "SODALanguagePacks")


def trim_profile() -> None:
    for name in CACHES:
        shutil.rmtree(profile() / name, ignore_errors=True)


def the_page(playwright):
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", timeout=180000)
    context = browser.contexts[0]
    pages = [page for page in context.pages if "terabox" in page.url]
    page = pages[-1] if pages else context.new_page()
    page.set_default_timeout(60000)
    return page


def go(page, remote: str) -> None:
    base = page.url.split("/main")[0] if "/main" in page.url else "https://www.terabox.com"
    try:
        page.goto(f"{base}/main?category=all&path=" + urllib.parse.quote(remote, safe=""), wait_until="commit", timeout=120000)
    except Exception:  # a slow page still arrives
        pass
    page.wait_for_timeout(7000)


def signed_in(page) -> bool:
    go(page, "/")
    return "/main" in page.url and page.locator("input[type=password]").count() == 0 \
        and page.get_by_text("New Folder", exact=True).count() > 0


# ---------------------------------------------------------------- what the page shows


def load_all(page) -> None:
    """A long list comes in parts as it is scrolled."""
    for _ in range(8):
        page.evaluate("""() => { for (const e of document.querySelectorAll('*')) {
            if (e.scrollHeight > e.clientHeight + 200 && /auto|scroll/.test(getComputedStyle(e).overflowY)) e.scrollTop = e.scrollHeight; } }""")
        page.wait_for_timeout(1200)


def names_shown(page) -> list[str]:
    return page.evaluate(f"""() => [...document.querySelectorAll('{ROW} a[title]')].map(a => a.getAttribute('title'))""")


def listing(page, remote: str) -> dict[str, int]:
    """Name -> size in bytes. Sizes come from the list TeraBox sends its own page; a name the page shows
    beyond that list has size -1 (there, size not known)."""
    found: dict[str, int] = {}

    def keep(response) -> None:
        if response.url.split("?")[0].endswith("/api/list"):
            try:
                for item in response.json().get("list", []):
                    if str(item.get("path", "")).rsplit("/", 1)[0] == remote.rstrip("/"):
                        found[item["server_filename"]] = int(item.get("size", 0))
            except Exception:
                pass

    page.on("response", keep)
    try:
        go(page, remote)
        load_all(page)
    finally:
        page.remove_listener("response", keep)
    for name in names_shown(page):
        found.setdefault(name, -1)
    return found


# ---------------------------------------------------------------- changing what is there


def ensure_folder(page, remote: str) -> None:
    """Make every folder of the path that is not there yet."""
    parent = "/"
    for name in [part for part in remote.split("/") if part]:
        go(page, parent)
        load_all(page)
        if name not in names_shown(page):
            page.get_by_text("New Folder", exact=True).first.click()
            page.wait_for_timeout(2000)
            box = page.locator("input.u-input__inner:visible:not([placeholder])").last  # the name field of the new row
            box.fill(name)
            box.press("Enter")
            page.wait_for_timeout(4000)
            go(page, parent)
            load_all(page)
            if name not in names_shown(page):
                raise Stop(f"The folder {name} could not be made in {parent} on TeraBox.")
        parent = parent.rstrip("/") + "/" + name


def to_recycle_bin(page, name: str) -> None:
    """Move the one row called `name`, in the folder that is open, to TeraBox's recycle bin."""
    row = page.locator(ROW).filter(has=page.locator(f'a[title="{name}"]'))
    if row.count() != 1:
        raise Stop(f"Expected one row called {name}, found {row.count()}. Nothing was removed.")
    checked = page.evaluate(f"""() => document.querySelectorAll('{ROW} label.u-checkbox.is-checked').length""")
    if checked:
        raise Stop("Other rows are ticked on the page. Nothing was removed.")
    row.scroll_into_view_if_needed()
    row.hover()
    page.wait_for_timeout(800)
    row.locator("label.u-checkbox").first.click()
    page.wait_for_timeout(1200)
    row.locator("button[title=Delete]").click()
    page.wait_for_timeout(2500)
    if not page.evaluate("() => document.body.innerText.includes('move 1 item(s) to the recycle bin')"):
        raise Stop("TeraBox did not ask about moving exactly one item to the recycle bin. Nothing was removed.")
    spot = page.evaluate("""() => { const b = [...document.querySelectorAll('.delete-box-btn.delete-btn')].find(e => e.offsetParent);
        if (!b) return null; const r = b.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }""")
    if not spot:
        raise Stop("The button that confirms the move was not found. Nothing was removed.")
    page.mouse.click(spot[0], spot[1])
    page.wait_for_timeout(5000)


def upload(page, paths: list[Path], folder: bool, minutes: int = 180) -> None:
    """Give the page's own upload field a folder or files, in the folder that is open, and wait for its count."""
    button = page.locator("button:has-text('Upload')").first
    box = button.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)  # the menu makes the file fields
    page.wait_for_timeout(1500)
    fields = page.locator("input[type=file]")
    wanted = [index for index in range(fields.count()) if bool(fields.nth(index).evaluate("e => e.webkitdirectory")) == folder]
    if not wanted:
        raise Stop("TeraBox's page has no upload field of that kind any more.")
    fields.nth(wanted[0]).set_input_files(str(paths[0]) if folder else [str(path) for path in paths])
    deadline = time.time() + minutes * 60
    last = ""
    while time.time() < deadline:
        page.wait_for_timeout(8000)
        text = page.locator("[class*=uploader]").first.inner_text().replace("\n", " | ")
        head = re.search(r"(Uploading|Upload Complete)[（(](\d+)/(\d+)[）)]", text)
        if head and head.group(0) != last:
            last = head.group(0)
            say(f"{head.group(1).lower()} {head.group(2)} of {head.group(3)}")
        if head and head.group(1) == "Upload Complete" and head.group(2) == head.group(3):
            return
    raise Stop(f"The upload did not finish in {minutes} minutes.")


def renamed_twin(name: str, size: int, remote: dict[str, int]) -> str:
    """TeraBox now and then puts the time in the name of a file it has just taken (seen with small files in a
    new, empty folder): HOW-TO-RESTORE.txt arrives as HOW-TO-RESTORE_20261122_004953.txt. Same bytes, other name."""
    stem, dot, ending = name.rpartition(".")
    pattern = re.compile(re.escape(stem if dot else name) + r"_\d{8}_\d{6}" + (re.escape("." + ending) if dot else "") + "$")
    return next((other for other, other_size in remote.items() if pattern.match(other) and other_size in (size, -1)), "")


def not_right(local: dict[str, int], remote: dict[str, int]) -> list[str]:
    """Files that are missing on TeraBox, or there with another size. One that is there whole under the name
    TeraBox gave it (see renamed_twin) is there."""
    wrong = []
    for name, size in local.items():
        if remote.get(name) in (size, -1) and name in remote:
            continue
        if not renamed_twin(name, size, remote):
            wrong.append(name)
    return sorted(wrong)


def upload_folder(page, local: Path, parent: str) -> dict:
    files = {path.name: path.stat().st_size for path in local.iterdir() if path.is_file()}
    ensure_folder(page, parent)
    go(page, parent)
    load_all(page)
    if local.name in names_shown(page):
        raise Stop(f"{parent.rstrip('/')}/{local.name} is on TeraBox already. Nothing was uploaded.")
    upload(page, [local], folder=True)
    there = listing(page, f"{parent.rstrip('/')}/{local.name}")
    wrong = not_right(files, there)
    renamed = {name: twin for name in files if name not in there and (twin := renamed_twin(name, files[name], there))}
    return {"ok": not wrong, "files": len(files), "wrong": wrong, "renamed": renamed}


def remove(page, parent: str, names: list[str]) -> dict:
    removed = []
    for name in names:
        go(page, parent)
        load_all(page)
        if name in names_shown(page):
            to_recycle_bin(page, name)
            removed.append(name)
    return {"ok": True, "removed": removed}


def main(argv: list[str]) -> dict:
    command = argv[0] if argv else ""
    if command == "window":
        return {"ok": True, "opened": start_window(hidden=False)}
    if command not in ("check", "upload-folder", "remove", "list"):
        raise Stop(__doc__)
    from playwright.sync_api import sync_playwright

    started = start_window(hidden=True)
    try:
        with sync_playwright() as playwright:
            page = the_page(playwright)
            if not signed_in(page):
                if command == "check":
                    return {"ok": True, "signedIn": False}
                raise Stop("TeraBox asks to sign in. Run `cairn cloud window`, sign in there, close the window and try again.")
            if command == "check":
                return {"ok": True, "signedIn": True}
            if command == "list":
                return {"ok": True, "files": listing(page, argv[1])}
            if command == "upload-folder":
                return upload_folder(page, Path(argv[1]), argv[2])
            return remove(page, argv[1], argv[2:])
    finally:
        stop_window(started)


if __name__ == "__main__":
    try:
        result = main(sys.argv[1:])
    except Stop as error:
        result = {"ok": False, "error": str(error)}
    except Exception as error:  # the page changed under us, Chrome went away: say it, change nothing more
        result = {"ok": False, "error": f"{type(error).__name__}: {str(error).splitlines()[0][:300]}"}
    print(json.dumps(result), flush=True)
    sys.exit(0 if result.get("ok") else 1)
