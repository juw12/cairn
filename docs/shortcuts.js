// The keyboard-shortcuts sheet (press ? anywhere, or "Keyboard shortcuts" in the palette).

import { escapeHtml as esc } from "./md.js";
import { $ } from "./ui.js";

const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
const MOD = IS_MAC ? "⌘" : "Ctrl";
const ALT = IS_MAC ? "⌥" : "Alt";

// Each row: [keys, what it does]. "Mod" and "Alt" are swapped for the Mac symbols.
export const SHORTCUTS = [
  {
    title: "Everywhere",
    rows: [
      ["Mod+K", "Command palette: every action and note"],
      ["/", "Search notes"],
      ["C", "Jump to quick capture"],
      ["?", "This sheet"],
      ["Esc", "Close the note, dialog or menu"],
    ],
  },
  {
    title: "Tabs",
    rows: [
      ["Mod+click", "Open a link in a new tab"],
      ["Middle-click", "Open in a new tab, or close a tab"],
      ["Mod+Alt+→ / ←", "Next or previous tab"],
      ["Mod+Alt+W", "Close this tab"],
      ["Mod+Alt+T", "Reopen the last closed tab"],
    ],
  },
  {
    title: "Split view",
    rows: [
      ["Mod+Alt+\\", "Open or close a note beside this one"],
      ["Alt+click", "Open a link or tab in the side pane"],
      ["Drag a tab", "Drop it on the right half to split"],
    ],
  },
  {
    title: "Writing",
    rows: [
      ["Mod+B / Mod+I", "Bold or italic"],
      ["[[", "Link to a note (↑ ↓ to pick, Enter to insert)"],
      ["Enter", "Continue a list; on an empty item, end it"],
      ["Tab", "Indent"],
    ],
  },
  {
    title: "Canvas",
    rows: [
      ["Double-click", "Add a card"],
      ["Shift+drag", "Select several cards"],
      ["Mod+A", "Select all"],
      ["Mod+C / X / V", "Copy, cut, paste cards"],
      ["Mod+D", "Duplicate the selection"],
      ["Mod+Z / Mod+Shift+Z", "Undo / redo (Mod+Y redoes too)"],
      ["Delete", "Delete the selection"],
      ["Enter", "Edit the focused card"],
      ["Arrows", "Nudge cards (Shift for bigger steps), or pan"],
      ["+ / −", "Zoom in or out (Mod+scroll too)"],
      ["Shift+1", "Fit everything on screen"],
      ["Esc", "Clear the selection"],
    ],
  },
];

function keys(combo) {
  return combo
    .split(" / ")
    .map((alt) => (alt === "+" ? [alt] : alt.split("+")))
    .map((parts) => parts.map((k) => `<kbd>${esc(k === "Mod" ? MOD : k === "Alt" ? ALT : k)}</kbd>`).join("<span class=\"sc-plus\">+</span>"))
    .join("<span class=\"sc-or\">/</span>");
}

export function showShortcuts() {
  if ($(".modal.shortcuts")) return;
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.innerHTML = `
    <div class="modal shortcuts" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" tabindex="-1">
      <div class="shortcuts-head">
        <h2>Keyboard shortcuts</h2>
        <button type="button" class="btn ghost sm" data-close aria-label="Close">Close</button>
      </div>
      <p>Press <kbd>?</kbd> any time to see this again.${IS_MAC ? "" : " On a Mac, Ctrl is ⌘ and Alt is ⌥."}</p>
      <div class="shortcuts-grid">
        ${SHORTCUTS.map((group) => `
          <section>
            <h3>${esc(group.title)}</h3>
            <dl>${group.rows.map(([combo, what]) => `<div><dt>${keys(combo)}</dt><dd>${esc(what.replaceAll("Mod+", `${MOD}+`))}</dd></div>`).join("")}</dl>
          </section>`).join("")}
      </div>
    </div>`;
  // Listen on the document (capture) so Esc or ? closes the sheet wherever focus is.
  const onKey = (event) => {
    if (!wrap.isConnected) return;
    if (event.key === "Escape" || (event.key === "?" && !event.target.closest?.("input, textarea, [contenteditable]"))) {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  };
  const close = () => {
    wrap.remove();
    document.removeEventListener("keydown", onKey, true);
  };
  wrap.addEventListener("click", (event) => {
    if (event.target === wrap || event.target.closest("[data-close]")) close();
  });
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(wrap);
  $(".modal.shortcuts", wrap)?.focus();
}
