"""Live sync: while the app is open, what one computer says to another does not wait for the next round.

Every 20 seconds, and every 5 for ten minutes after a message between computers was sent or arrived (an
answer usually follows soon), for the brain the app is serving:

  * a message for another computer was written here and has not left yet: sync now;
  * the remote has something this computer has not fetched (one small question to the remote): sync now;
  * the brain's repository moved, whoever moved it (this loop, the app's own rounds, a sync script of the
    user's, a pull in a terminal): tell the open pages which notes changed, so they show them within seconds.

It only syncs a brain that is set up for it: `git config cairn.sync` is "auto" (the desktop app sets that
while its automatic sync is on) or a command of the user's own. Without it this loop only keeps the pages
current. It never raises: a sync that cannot be done now is tried again on a later round.
"""

from __future__ import annotations

import threading
import time

from . import core
from .brain import SyncError

EVERY_SECONDS = 20
# A conversation is going (a message left or arrived): look this often for this long, so an answer shows
# about as fast as the two syncs it takes to carry it.
TALKING_EVERY_SECONDS = 5
TALKING_FOR_SECONDS = 600
AFTER_A_FAILURE_SECONDS = 300  # a sync that failed (offline, two edits of one note) is not hammered
CLIENT = "livesync"  # the name its changes carry in the feed; no page has it, so every page hears them
HANDOFFS = core.HANDOFF_FOLDER + "/"


class LiveSync:
    def __init__(self, handler_class, every: float = EVERY_SECONDS, log=None):
        self.handler = handler_class
        self.every = every
        self.log = log or (lambda text: None)
        self.stopping = threading.Event()
        self._wake = threading.Event()
        self.thread: threading.Thread | None = None
        self._brain = None
        self._head = ""
        self._not_before = 0.0
        self._talking_until = 0.0

    def start(self) -> "LiveSync":
        self.thread = threading.Thread(target=self._run, name="cairn-livesync", daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopping.set()
        self._wake.set()

    def wake(self) -> None:
        """Do the next round now (a message for another computer was just saved)."""
        self._not_before = 0.0
        self._talking()
        self._wake.set()

    def _talking(self) -> None:
        self._talking_until = time.monotonic() + TALKING_FOR_SECONDS

    def wait_seconds(self) -> float:
        """How long until the next round: short while a conversation is going."""
        return min(self.every, TALKING_EVERY_SECONDS) if time.monotonic() < self._talking_until else self.every

    def _run(self) -> None:
        while not self.stopping.is_set():
            self._wake.wait(self.wait_seconds())
            self._wake.clear()
            if self.stopping.is_set():
                break
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001 - a background nicety must never take the app down
                self.log(f"live sync: {error}")

    def _announce(self, brain, before: str, after: str) -> int:
        paths = brain.changed_between(before, after)
        for path in paths:
            self.handler.changes.record(path, CLIENT)
        if any(path.startswith(HANDOFFS) for path in paths):
            self._talking()
        return len(paths)

    def tick(self) -> str:
        """One round. Returns what it did, for the log and the tests: "", "changed N", "synced", "failed"."""
        brain = self.handler.brain
        if not brain.git_available():
            return ""
        did = ""
        head = brain.head()
        if brain is not self._brain:  # the first round, or the app opened another brain
            self._brain, self._head = brain, head
        elif head != self._head:
            did = f"changed {self._announce(brain, self._head, head)}"
            self._head = head
        if not brain.sync_mode() or time.monotonic() < self._not_before:
            return did
        unsent = brain.handoffs_unsent()
        if unsent:
            self._talking()
        if not (unsent or brain.remote_moved()):
            return did
        try:
            result = brain.sync_now()
        except SyncError as error:
            self._not_before = time.monotonic() + AFTER_A_FAILURE_SECONDS
            self.log(f"live sync skipped: {error}")
            return "failed"
        after = brain.head()
        if after != self._head:
            self._announce(brain, self._head, after)
            self._head = after
        self.log(f"live sync: {result}")
        return "synced"
