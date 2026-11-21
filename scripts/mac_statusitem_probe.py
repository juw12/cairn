"""Probe: does a plain menu-bar icon of a plain script show up in macOS's window list? (temporary)"""
import os
import threading
import time

import AppKit
import Quartz


def icons():
    shown = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    return [(w.get("kCGWindowOwnerName"), int(w["kCGWindowBounds"]["X"]), int(w["kCGWindowBounds"]["Width"])) for w in shown if w.get("kCGWindowLayer") == 25]


print("before:", icons())
app = AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
item.button().setTitle_("probe")
print("item visible says:", item.isVisible())


def later():
    time.sleep(6)
    print("after:", icons())
    mine = [
        (w.get("kCGWindowLayer"), bool(w.get("kCGWindowIsOnscreen")), int(w["kCGWindowBounds"]["Width"]), int(w["kCGWindowBounds"]["Height"]))
        for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
        if w.get("kCGWindowOwnerPID") == os.getpid()
    ]
    print("own windows:", mine)
    os._exit(0)


threading.Thread(target=later, daemon=True).start()
app.run()
