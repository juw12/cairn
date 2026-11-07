// Maths and diagrams: $inline$, $$display$$ (KaTeX) and ```mermaid code blocks (Mermaid).
// Each library is fetched only when a note on screen needs it, pinned to an exact version with
// a Subresource Integrity hash (a tampered file is refused). Without a connection the note simply
// keeps showing the source, so nothing breaks offline.

const SOURCES = {
  katex: {
    js: { url: "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js", integrity: "sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg" },
    css: { url: "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css", integrity: "sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+" },
  },
  mermaid: {
    js: { url: "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js", integrity: "sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I" },
  },
};

const MATH = "pre.math:not([data-rendered]), .math-inline:not([data-rendered])";
const DIAGRAMS = "pre > code.language-mermaid:not([data-rendered])";
const loading = new Map();
let diagramCount = 0;

function sources() {
  return window.CAIRN_RICH_SOURCES || SOURCES;  // tests point these at local stand-ins
}

function attach(tag, attributes) {
  return new Promise((resolve, reject) => {
    const el = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (value != null) el.setAttribute(name, value);
    }
    const timer = setTimeout(() => reject(new Error("timed out")), 20000);
    el.onload = () => {
      clearTimeout(timer);
      resolve();
    };
    el.onerror = () => {
      clearTimeout(timer);
      el.remove();
      reject(new Error("could not load"));
    };
    document.head.append(el);
  });
}

function load(name) {
  if (!loading.has(name)) {
    const source = sources()[name];
    const steps = [];
    if (source.css) {
      steps.push(attach("link", { rel: "stylesheet", href: source.css.url, integrity: source.css.integrity, crossorigin: "anonymous" }));
    }
    steps.push(attach("script", { src: source.js.url, integrity: source.js.integrity, crossorigin: "anonymous" }));
    const promise = Promise.all(steps).then(() => window[name]);
    promise.catch(() => loading.delete(name));  // try again next time a note needs it
    loading.set(name, promise);
  }
  return loading.get(name);
}

function markUnrendered(elements, reason) {
  for (const el of elements) {
    el.dataset.rendered = "source";
    el.title = `Shown as source: ${reason}`;
  }
}

async function renderMath(elements) {
  let katex;
  try {
    katex = await load("katex");
  } catch (error) {
    return markUnrendered(elements, "the maths renderer could not be loaded (offline?)");
  }
  for (const el of elements) {
    if (el.dataset.rendered || !el.isConnected) continue;
    const display = el.matches("pre");
    const tex = el.textContent;
    const html = katex.renderToString(tex, { displayMode: display, throwOnError: false, trust: false, strict: "ignore", output: "htmlAndMathml" });
    const out = document.createElement(display ? "div" : "span");
    out.className = display ? "math-block" : "math-inline rendered";
    out.dataset.rendered = "1";
    out.dataset.tex = tex;
    out.innerHTML = html;
    el.replaceWith(out);
  }
}

async function renderDiagrams(codes) {
  let mermaid;
  try {
    mermaid = await load("mermaid");
  } catch (error) {
    return markUnrendered(codes, "the diagram renderer could not be loaded (offline?)");
  }
  const dark = document.documentElement.dataset.theme === "dark";
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral", fontFamily: "inherit" });
  for (const code of codes) {
    if (code.dataset.rendered || !code.isConnected) continue;
    code.dataset.rendered = "busy";
    const pre = code.parentElement;
    try {
      diagramCount += 1;
      const { svg } = await mermaid.render(`cairn-diagram-${diagramCount}`, code.textContent);
      const out = document.createElement("div");
      out.className = "mermaid-diagram";
      out.dataset.rendered = "1";
      out.innerHTML = svg;
      pre.replaceWith(out);
    } catch (error) {
      code.dataset.rendered = "source";
      pre.title = `Diagram has an error: ${String(error?.message || error).split("\n")[0]}`;
      document.querySelectorAll(`#dcairn-diagram-${diagramCount}`).forEach((leftover) => leftover.remove());
    }
  }
}

export async function enhance(root = document) {
  const maths = [...root.querySelectorAll(MATH)];
  const diagrams = [...root.querySelectorAll(DIAGRAMS)];
  const jobs = [];
  if (maths.length) jobs.push(renderMath(maths));
  if (diagrams.length) jobs.push(renderDiagrams(diagrams));
  await Promise.all(jobs);
}

export function initRichContent() {
  // Automated test browsers keep the source unless a test opts in (no network in tests).
  if (navigator.webdriver && !window.CAIRN_RICH_SOURCES) return;
  let queued = false;
  const run = () => {
    queued = false;
    enhance().catch(() => {});
  };
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(run);
  });
  observer.observe(document.body, { childList: true, subtree: true });
  run();
}
