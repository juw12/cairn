// Live sync between open pages: an edit on your phone shows up on the PC window (and the other way
// round) within a few seconds. Polls the server's change feed (GET api/changes); a page is never told
// about its own edits. A note you are typing in is left alone until you click out of it.

import { loadAll, state } from "./state.js";
import { render } from "./app.js";
import { renderDrawer } from "./drawer.js";
import { toast } from "./ui.js";

const POLL_MS = 2500;

let timer = null;
let busy = false;
let revision = 0;
let epoch = "";
const waiting = new Set(); // changed while you were typing in that note
let waitingAll = false;

function typingInOpenNote() {
  const active = document.activeElement;
  const drawer = document.getElementById("drawer-layer");
  if (!state.open || !active || !drawer || !drawer.contains(active)) return false;
  return active.isContentEditable || /^(INPUT|TEXTAREA)$/.test(active.tagName);
}

async function apply(paths, everything) {
  const touchesOpen = everything || paths.includes(state.open);
  if (touchesOpen && typingInOpenNote()) {
    paths.forEach((path) => waiting.add(path));
    waitingAll = waitingAll || everything;
    return false;
  }
  waiting.clear();
  waitingAll = false;
  await loadAll();
  render();
  if (state.open && touchesOpen) renderDrawer();
  return true;
}

async function poll() {
  if (busy || document.hidden) return;
  busy = true;
  try {
    const result = await state.store.changes(revision, epoch);
    const first = !epoch;
    revision = result.revision;
    epoch = result.epoch;
    if (first) return;
    const paths = [...new Set([...result.paths, ...waiting])];
    const everything = Boolean(result.reset) || waitingAll;
    if (!paths.length && !everything) return;
    const fresh = result.paths.length;
    const applied = await apply(paths, everything);
    if (applied && fresh) toast(fresh === 1 ? "Updated from your other device." : `${fresh} notes updated from your other device.`);
  } catch {
    // Offline or the PC is asleep: try again on the next tick.
  } finally {
    busy = false;
  }
}

export function startLiveSync() {
  if (timer || typeof state.store?.changes !== "function") return;
  timer = setInterval(poll, window.CAIRN_TEST_LIVE_MS || POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
  document.addEventListener("focusout", () => {
    if (waiting.size || waitingAll) setTimeout(poll, 50);
  });
  poll();
}
