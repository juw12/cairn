"""Double-click (or Start menu) launcher for the Cairn desktop app. See cairn/desktop.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cairn.desktop import main  # noqa: E402

sys.exit(main())
