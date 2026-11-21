"""Probe (temporary): pystray's own icon, run the way pystray documents; lives 12 s."""
import os
import sys
import threading
import time

import pystray
from PIL import Image

image = Image.open(sys.argv[1])
icon = pystray.Icon("Probe", image, "Probe", pystray.Menu(pystray.MenuItem("Hello", lambda *_: None)))
threading.Thread(target=lambda: (time.sleep(12), os._exit(0)), daemon=True).start()
icon.run()
