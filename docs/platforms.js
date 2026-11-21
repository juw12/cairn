// Versions: can this project be had on Windows, on a Mac, on an iPhone and on Android, and where is each one?
//
// GitHub answers with a list of release files. A person asks "can I get it on my computer and my phone?", so
// the Look tab of a repo, and of an app's folder inside a monorepo, opens with four lines, each read from what
// is really published:
//   Windows, Mac, Android   a file in a release (the newest release that has one), else a download the README
//                           links: .exe / .msi / an install script, .dmg / .pkg, .apk, or a file named for it
//   iPhone                  the project's own web app kept on the home screen: the repo holds a web manifest
//                           that opens as its own app (display: standalone) and its page has an apple-touch-icon
// A version that an older release still carries while newer releases dropped it is shown as left behind.
//
// One look costs one more GitHub call (the releases), kept for five minutes, and for a repo with a web manifest
// two small files. Nothing is written.

import * as gh from "./github.js";
import { escapeHtml as esc } from "./md.js";
import { icon, relativeTime } from "./ui.js";

export const PLATFORMS = ["Windows", "Mac", "iPhone", "Android"];
const BUILD = {
  Windows: /\.(exe|msi|msix|cmd|bat|ps1)$|(^|[-_.])win(dows)?(32|64)?([-_.]|$)/i,
  Mac: /\.(dmg|pkg|command)$|(^|[-_.])(mac|macos|osx|darwin)([-_.]|$)/i,
  Android: /\.(apk|aab)$/i,
};
// Checksums, update feeds and pictures sit next to builds and carry their names; they are not builds.
const NOT_A_BUILD = /\.(sha256|sig|asc|txt|ya?ml|json|png|jpe?g|gif|svg|md|html?|blockmap)$/i;
const MANIFEST_RE = /^(manifest\.webmanifest|site\.webmanifest|manifest\.json)$/i;
const LIVE_HOST_RE = /^https:\/\/[^/]+\.(github\.io|pages\.dev|vercel\.app|netlify\.app|web\.app)(\/|$)/i;
const PAGES = 2; // release pages asked for at most: 200 releases cover a monorepo with a tag per app and platform
const STALE_MS = 5 * 60 * 1000;
const SHOWN = 3; // files listed for one platform (an installer and a portable build, two chips)

// ============================================================ pure helpers (tested)

// Which platform a file is a build for, or "" (a checksum, a picture, a Linux build, an iPhone .ipa nobody can install).
export function platformOf(name) {
  const file = String(name || "");
  if (!file || NOT_A_BUILD.test(file)) return "";
  return Object.keys(BUILD).find((platform) => BUILD[platform].test(file)) || "";
}

// The part of a tag that stays the same from release to release: v1.3.0 -> "", glow-mac -> "glow-mac".
export function seriesOf(tag) {
  return String(tag || "").replace(/[-_]?v?\d[\d.\-]*$/, "").toLowerCase();
}

// GitHub's release list, slimmed: [{ tag, at, assets: [{ name, url, size }] }], newest first, drafts left out.
export function slimReleases(list) {
  return (Array.isArray(list) ? list : [])
    .filter((release) => release && !release.draft)
    .map((release) => ({
      tag: String(release.tag_name || ""),
      at: Date.parse(release.published_at || release.created_at || "") || 0,
      assets: (release.assets || []).map((asset) => ({ name: String(asset.name || ""), url: String(asset.browser_download_url || ""), size: asset.size || 0 })),
    }));
}

// The releases that belong to one folder of a monorepo: the tags that start with the folder's name, or with its
// first word (apps/airiz-motor owns airiz-desktop-v1.2 and airiz-mobile-v1.0). The repo itself owns them all.
export function ownReleases(releases, folder) {
  const name = String(folder || "").split("/").pop().toLowerCase();
  if (!name) return releases;
  const stems = [...new Set([name, name.split("-")[0]])].filter(Boolean);
  return releases.filter((release) => stems.some((stem) => release.tag.toLowerCase() === stem || release.tag.toLowerCase().startsWith(`${stem}-`)));
}

// The newest release with a build for `platform`: { tag, at, assets, behind }. `behind` names a newer release of
// the same series that has downloads but none for this platform, so this version was left behind.
export function buildsFor(releases, platform) {
  for (let i = 0; i < releases.length; i++) {
    const assets = releases[i].assets.filter((asset) => platformOf(asset.name) === platform);
    if (!assets.length) continue;
    const newer = releases.slice(0, i).find((other) => seriesOf(other.tag) === seriesOf(releases[i].tag) && other.assets.some((asset) => platformOf(asset.name)));
    return { tag: releases[i].tag, at: releases[i].at, assets, behind: newer ? newer.tag : "" };
  }
  return null;
}

// Every link in a README (as rendered HTML) that is a direct download for a platform, or a live web address.
export function readmeLinks(html) {
  const found = { Windows: [], Mac: [], Android: [], live: [] };
  for (const match of String(html || "").matchAll(/href="(https?:\/\/[^"]+)"/g)) {
    const href = match[1].replace(/&amp;/g, "&");
    let name = "";
    try {
      name = decodeURIComponent(new URL(href).pathname.split("/").pop() || "");
    } catch {
      continue;
    }
    const platform = platformOf(name);
    if (platform && !found[platform].some((link) => link.url === href)) found[platform].push({ name, url: href, size: 0 });
    else if (!platform && LIVE_HOST_RE.test(href) && !found.live.includes(href)) found.live.push(href);
  }
  return found;
}

// The web manifest that belongs to a folder: in it or one folder below (docs/, web/, public/), the shallowest
// first. A monorepo's root, and its apps/ folder, therefore do not borrow the manifest of one of the apps.
export function manifestOf(index, folder) {
  const found = [];
  const walk = (path, depth) => {
    const here = index?.dirs.get(path);
    if (!here) return;
    for (const file of here.files) if (MANIFEST_RE.test(file.name)) found.push({ path: file.path, depth, json: /\.json$/i.test(file.name) });
    if (depth < 1) for (const name of here.dirs) walk(path ? `${path}/${name}` : name, depth + 1);
  };
  walk(folder || "", 0);
  found.sort((a, b) => a.depth - b.depth || Number(a.json) - Number(b.json) || a.path.localeCompare(b.path));
  return found[0]?.path || "";
}

// What the web app's two files say: { standalone, icon }.
export function webAppFacts(manifestText, pageHtml) {
  let display = "";
  try {
    display = String(JSON.parse(manifestText || "{}").display || "");
  } catch {
    display = "";
  }
  const html = String(pageHtml || "");
  const links = html.match(/<link\b[^>]*>/gi) || [];
  return {
    standalone: display === "standalone" || display === "fullscreen" || /<meta\b[^>]*mobile-web-app-capable[^>]*content\s*=\s*["']?yes/i.test(html),
    icon: links.some((tag) => /rel\s*=\s*["']?apple-touch-icon/i.test(tag) && /href\s*=/i.test(tag)),
  };
}

// The four lines. `web` is null (no manifest in the repo), or { standalone, icon, live } once its files are read.
//   -> [{ platform, state: "there" | "behind" | "missing" | "unknown", files: [{ name, url, size }], tag, at, note }]
export function fourVersions({ releases, folder = "", readmeHtml = "", web = null, live = "", releasesKnown = true }) {
  const mine = ownReleases(releases || [], folder);
  const links = readmeLinks(readmeHtml);
  return PLATFORMS.map((platform) => {
    if (platform === "iPhone") {
      const name = folder.split("/").pop().toLowerCase();
      const address = live || (name && links.live.find((href) => href.toLowerCase().includes(`/${name}`))) || links.live[0] || "";
      if (!web) return { platform, state: "missing", files: [], tag: "", at: 0, note: "no web app to keep on the home screen" };
      if (web.loading) return { platform, state: "unknown", files: [], tag: "", at: 0, note: "reading the web app…" };
      const lacks = [web.standalone ? "" : "a manifest that opens as its own app (display: standalone)", web.icon ? "" : "an apple-touch-icon"].filter(Boolean);
      if (lacks.length) return { platform, state: "missing", files: [], tag: "", at: 0, note: `the web app lacks ${lacks.join(" and ")}` };
      return { platform, state: "there", files: address ? [{ name: "Open the web app", url: address, size: 0 }] : [], tag: "", at: 0, note: "in Safari: Share, then Add to Home Screen" };
    }
    const built = buildsFor(mine, platform);
    if (built) {
      return { platform, state: built.behind ? "behind" : "there", files: built.assets, tag: built.tag, at: built.at, note: built.behind ? `left behind: the newest release is ${built.behind}` : "" };
    }
    if (links[platform].length) return { platform, state: "there", files: links[platform], tag: "", at: 0, note: "linked in the README" };
    return { platform, state: releasesKnown ? "missing" : "unknown", files: [], tag: "", at: 0, note: releasesKnown ? "not published" : "the releases could not be read" };
  });
}

// ============================================================ loading

const repos = new Map(); // "owner/name" in lower case -> { state, releases, at, web: Map(folder -> facts) }

function factsFor(entry) {
  const key = entry.full.toLowerCase();
  if (!repos.has(key)) repos.set(key, { state: "", releases: [], at: 0, web: new Map() });
  return repos.get(key);
}

async function loadReleases(entry, facts, redraw) {
  facts.state = facts.state === "done" ? "done" : "loading";
  facts.at = Date.now();
  try {
    const all = [];
    for (let page = 1; page <= PAGES; page++) {
      const list = await (await gh.apiFresh(`/repos/${entry.full}/releases?per_page=100&page=${page}`)).json();
      all.push(...(Array.isArray(list) ? list : []));
      if (!Array.isArray(list) || list.length < 100) break;
    }
    facts.releases = slimReleases(all);
    facts.state = "done";
  } catch {
    facts.state = facts.state === "done" ? "done" : "failed";
  }
  redraw();
}

async function fileText(entry, path) {
  if (!entry.isPrivate) return (await gh.rawText(entry.owner, entry.name, entry.ref, path)) || "";
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  return (await gh.apiFresh(`/repos/${entry.full}/contents/${encoded}?ref=${encodeURIComponent(entry.ref)}`, { accept: "application/vnd.github.raw" })).text();
}

function webApp(entry, facts, folder, redraw) {
  const manifest = manifestOf(entry.index, folder);
  if (!manifest) return null;
  const key = `${entry.ref}:${manifest}`;
  if (!facts.web.has(key)) {
    const web = { loading: true, standalone: false, icon: false };
    facts.web.set(key, web);
    const page = `${manifest.includes("/") ? `${manifest.slice(0, manifest.lastIndexOf("/"))}/` : ""}index.html`;
    Promise.all([fileText(entry, manifest).catch(() => ""), fileText(entry, page).catch(() => "")]).then(([manifestText, pageHtml]) => {
      Object.assign(web, webAppFacts(manifestText, pageHtml), { loading: false });
      redraw();
    });
  }
  return facts.web.get(key);
}

// ============================================================ drawing

const megabytes = (bytes) => (bytes >= 1048576 ? `${Math.round(bytes / 1048576)} MB` : bytes >= 1024 ? `${Math.round(bytes / 1024)} KB` : "");

function line(version) {
  const files = version.files.slice(0, SHOWN).map((file) => `<a href="${esc(file.url)}" target="_blank" rel="noopener noreferrer">${esc(file.name)}</a>${megabytes(file.size) ? ` <span class="muted">${megabytes(file.size)}</span>` : ""}`);
  const more = version.files.length > SHOWN ? ` <span class="muted">and ${version.files.length - SHOWN} more</span>` : "";
  const when = [version.tag, version.at ? relativeTime(version.at) : "", version.note].filter(Boolean).join(" · ");
  const mark = version.state === "there" ? icon("check", "sm") : version.state === "behind" ? icon("history", "sm") : "";
  return `<li class="repo-version ${esc(version.state)}" data-version="${esc(version.platform)}">
    <strong>${mark}${esc(version.platform)}</strong>
    <span class="repo-version-files">${files.join(`<span aria-hidden="true"> · </span>`)}${more}</span>
    <span class="muted">${esc(when)}</span>
  </li>`;
}

// The Versions block of the Look tab. `readme` is the README's document as repo.js loads it ({ state, html }).
// Returns "" for a folder that has no version of its own (src/, docs/): only the repo and its apps get the block.
export function versionsSection(entry, folder, readme, redraw) {
  if (!entry?.index || !entry.meta) return "";
  const facts = factsFor(entry);
  if (facts.state !== "loading" && Date.now() - facts.at > STALE_MS) loadReleases(entry, facts, redraw);
  if (!facts.state || (facts.state === "loading" && !facts.releases.length)) {
    return folder ? "" : `<section class="repo-versions" aria-label="Versions"><h2>Versions</h2><p class="muted">Looking for the Windows, Mac, iPhone and Android versions…</p></section>`;
  }
  // A repo whose releases belong to several apps (a tag series per app) has no versions of its own.
  const apps = new Set(facts.releases.filter((release) => release.assets.some((asset) => platformOf(asset.name))).map((release) => seriesOf(release.tag).split("-")[0]));
  if (!folder && apps.size > 2) {
    return `<section class="repo-versions none" aria-label="Versions"><h2>Versions</h2><p class="muted">The releases here belong to ${apps.size} apps. Open an app's folder to see its Windows, Mac, iPhone and Android versions.</p></section>`;
  }
  const meta = entry.meta;
  const live = folder ? "" : String(meta.homepage || "").trim() || (meta.has_pages ? `https://${entry.owner}.github.io/${entry.name}/` : "");
  const web = webApp(entry, facts, folder, redraw);
  // Until the README and the web app's two files are read, "nothing published" would be said too early.
  const settling = Boolean(web?.loading) || readme?.state === "loading";
  const versions = fourVersions({
    releases: facts.releases,
    folder,
    readmeHtml: readme?.state === "done" ? readme.html : "",
    web,
    live: /^https?:\/\//.test(live) ? live : "",
    releasesKnown: facts.state === "done",
  });
  const there = versions.filter((version) => version.state === "there" || version.state === "behind").length;
  if (folder && !there) return "";
  if (!there && settling) return `<section class="repo-versions" aria-label="Versions"><h2>Versions</h2><p class="muted">Looking for the Windows, Mac, iPhone and Android versions…</p></section>`;
  const count = `<span class="muted">${there} of ${PLATFORMS.length}</span>`;
  if (!there && facts.state === "done") {
    return `<section class="repo-versions none" aria-label="Versions"><h2>Versions ${count}</h2><p class="muted">No Windows, Mac, iPhone or Android version is published.</p></section>`;
  }
  return `<section class="repo-versions" aria-label="Versions"><h2>Versions ${count}</h2><ul>${versions.map(line).join("")}</ul></section>`;
}
