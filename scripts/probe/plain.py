"""Probe (temporary): a plain menu-bar icon made on the main thread; lives 12 s."""
import os
import threading
import time

import AppKit

app = AppKit.NSApplication.sharedApplication()
item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
item.button().setTitle_("probe")
threading.Thread(target=lambda: (time.sleep(12), os._exit(0)), daemon=True).start()
app.run()
