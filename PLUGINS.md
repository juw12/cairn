# Writing a Cairn plugin

A Cairn plugin is **one JavaScript file**. It can add commands to the palette (<kbd>Ctrl</kbd>+<kbd>K</kbd>),
pages to the sidebar, renderers for fenced code blocks, and react to what you do. Plugins run in the
web app and in the desktop app alike.

> **Obsidian plugins don't run in Cairn.** They are written against Obsidian's own API (and often
> Electron/Node), which Cairn doesn't have. Cairn has a small API of its own, below. Porting a simple
> Obsidian plugin is usually a short job: the ideas map one to one (commands, views, code-block
> processors, events, settings).

## The smallest plugin

```js
export const manifest = {
  id: "hello-world",          // lowercase letters, numbers and dashes
  name: "Hello world",
  version: "1.0.0",
  author: "you",
  description: "Says hello.",
  minApi: 1,                  // optional: the plugin API version you need
};

export default function activate(cairn) {
  cairn.addCommand({ id: "hi", name: "Say hello", run: () => cairn.toast("Hello!") });
  return () => {};            // optional: clean-up when the plugin is turned off
}
```

`export default { onload(cairn) {…}, onunload() {…} }` works too. Everything a plugin registers
through `cairn` is removed automatically when it is turned off, so most plugins need no clean-up.

## Sharing it

* **On GitHub:** put the file at the root of a repo as `main.js`. Anyone can install it from
  **Plugins → Install from GitHub or a link** by typing `owner/repo`.
* **Anywhere else:** any `https://` link to the `.js` file works.
* **In the plugin list:** open a pull request adding an entry to
  [`docs/plugins/index.json`](docs/plugins/index.json) (`id`, `name`, `author`, `version`,
  `description`, and `repo` or `main`).

## Safety model

* Community plugins are **off** until you turn them on, and every install from outside the built-in
  list asks for confirmation.
* A plugin runs with the same access as Cairn itself: your notes and, when you're signed in, your
  GitHub session. Only install plugins you trust.
* Plugin **code** is stored **on the device** (in the browser or desktop app), never in the brain. A shared
  brain can't push code onto a teammate's machine.
* The **list** of plugins can travel with the brain: *Save list to this brain* on the Plugins page writes
  `brain/cairn/plugins.md`, a note whose front matter names where each plugin comes from
  (`plugins: [plugins/calendar.js, you/word-goal]`). Other devices show the list and offer to install it,
  with the same confirmation as any install. Once the note exists, installing or removing a plugin keeps it
  up to date.
* A plugin that throws while running (a command, view, code block, event or settings handler) is flagged on
  its row with the error and a **Restart** button; the rest of Cairn keeps working.

## The API

| Call | What it does |
|---|---|
| `cairn.notes()` | Every note: `{ path, title, type, status, tags, body, meta, created, mtime }` (copies). |
| `cairn.note(path)`, `cairn.activeNote()` | One note, or the one open in the editor. |
| `cairn.resolve(link, fromPath)` | Resolve a `[[link]]` the way Cairn does. |
| `cairn.files()` | Every file path in the brain (notes and canvases). |
| `cairn.createNote({ title, type, body, tags, properties })` | Create a note; returns its path. |
| `cairn.updateNote(path, { body, title, tags, status, properties })` | Change a note (only what you pass). |
| `cairn.writeFile(path, text)` | Write a `.md` or `.canvas` file in the brain. |
| `cairn.insertIntoActiveNote(text)` | Insert at the caret of the open note. |
| `cairn.openJournal(date)` | Open (or start) the journal page for `YYYY-MM-DD`. |
| `cairn.openNote(path, { newTab })`, `cairn.go(view)`, `cairn.openView(id)` | Navigate. |
| `cairn.addCommand({ id, name, icon, run })` | A palette command. |
| `cairn.addView({ id, name, icon, render(el) })` | A sidebar page; `render` fills `el`. |
| `cairn.registerCodeBlock(lang, (source, el, ctx) => …)` | Render ` ```lang ` blocks; `ctx.notePath` is the note. |
| `cairn.on(event, fn)` | Events: `note-open` `{ path }`, `notes-changed`, `view-change` `{ view }`. |
| `cairn.addStyle(css)` | Add CSS (removed when the plugin is turned off). Use Cairn's CSS variables (`--ink`, `--line`, `--accent`…). |
| `cairn.settings.get(key, fallback)`, `cairn.settings.set(key, value)` | Small per-plugin storage on this device. With no fallback, a key declared in `manifest.settings` returns its default. |
| `cairn.settings.onChange(key?, fn)` | Called with `(value, key)` when a setting changes (from the settings page or `set`). |
| `cairn.addSettings(fields)` | Add settings fields at run time (same shape as `manifest.settings`). |
| `cairn.toast(msg)`, `cairn.ask({ title, text, value })`, `cairn.pick(items, placeholder)` | Talk to the person. |
| `cairn.renderMarkdown(text, fromPath)`, `cairn.escape(text)`, `cairn.icon(name)` | Helpers. |
| `cairn.today()`, `cairn.project()`, `cairn.readOnly()`, `cairn.version` | Context. |

Write calls throw when the brain is read-only (someone else's brain on GitHub).

### A settings page for free

Describe your options in the manifest and Cairn draws the settings page (the gear on your plugin's row),
stores the values on the device and offers *Reset to defaults*:

```js
export const manifest = {
  id: "word-goal", name: "Word goal", version: "1.0.0",
  settings: [
    { key: "goal", label: "Daily goal", type: "number", default: 500, min: 50, step: 50, description: "Words per day." },
    { key: "unit", label: "Count", type: "select", default: "words", options: ["words", "characters"] },
    { key: "celebrate", label: "Confetti when I hit it", type: "toggle", default: true },
  ],
};

export default function activate(cairn) {
  const goal = () => cairn.settings.get("goal"); // 500 until the person changes it
  cairn.settings.onChange("goal", (value) => cairn.toast(`New goal: ${value}`));
}
```

Field types: `toggle`, `text`, `textarea`, `number` (`min`, `max`, `step`) and `select` (`options` as strings or
`{ value, label }`). The bundled *Writing stats* plugin is a working example.

## Examples

The four built-in plugins are short and readable:
[note queries](docs/plugins/note-queries.js) (a code-block renderer, like Dataview),
[calendar](docs/plugins/calendar.js) (a view),
[writing stats](docs/plugins/writing-stats.js) (a view and a command) and
[recent files](docs/plugins/recent-files.js) (events and settings).
