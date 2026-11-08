// Offline voice: speech recognition that runs on this device, so the log works without a connection.
// 1. Chrome's own on-device engine (Web Speech `processLocally`) when the browser offers it.
// 2. Otherwise Vosk (Kaldi compiled to WebAssembly) with a small model downloaded once and kept in Cache Storage.
// Audio never leaves the device in either case. The Vosk engine is exposed with the same shape as
// SpeechRecognition (start/stop, onstart/onresult/onerror/onend), so the log code treats both alike.

export const VOSK_SCRIPT = "https://cdn.jsdelivr.net/npm/vosk-browser@0.0.8/dist/vosk.js";
export const MODEL_BASE = "https://ccoreilly.github.io/vosk-browser/models/";
// Hosted small models (size in MB). Keys are BCP 47 prefixes; the most specific match wins.
export const MODELS = {
  en: { file: "vosk-model-small-en-us-0.15.tar.gz", mb: 41, name: "English" },
  "en-IN": { file: "vosk-model-small-en-in-0.4.tar.gz", mb: 38, name: "English (India)" },
  zh: { file: "vosk-model-small-cn-0.3.tar.gz", mb: 33, name: "中文" },
  de: { file: "vosk-model-small-de-0.15.tar.gz", mb: 46, name: "Deutsch" },
  es: { file: "vosk-model-small-es-0.3.tar.gz", mb: 34, name: "Español" },
  fr: { file: "vosk-model-small-fr-pguyot-0.3.tar.gz", mb: 46, name: "Français" },
  it: { file: "vosk-model-small-it-0.4.tar.gz", mb: 34, name: "Italiano" },
  pt: { file: "vosk-model-small-pt-0.3.tar.gz", mb: 32, name: "Português" },
  ru: { file: "vosk-model-small-ru-0.4.tar.gz", mb: 41, name: "Русский" },
  tr: { file: "vosk-model-small-tr-0.3.tar.gz", mb: 37, name: "Türkçe" },
  vi: { file: "vosk-model-small-vn-0.3.tar.gz", mb: 34, name: "Tiếng Việt" },
  ca: { file: "vosk-model-small-ca-0.4.tar.gz", mb: 43, name: "Català" },
  fa: { file: "vosk-model-small-fa-0.4.tar.gz", mb: 49, name: "فارسی" },
};

// The service worker keeps every cache whose name starts with "cairn-voice".
const CACHE = "cairn-voice-v1";
const META_KEY = "cairn.voicelog.offline";
const SCRIPT_KEY = "/__cairn-voice/vosk.js";
const MODEL_KEY = "/__cairn-voice/model.tar.gz";

let modelPromise = null;
let chromeStatusCache = new Map();

export function modelFor(lang) {
  const code = lang || navigator.language || "en-US";
  if (MODELS[code]) return { lang: code, ...MODELS[code] };
  const base = code.split("-")[0].toLowerCase();
  return MODELS[base] ? { lang: base, ...MODELS[base] } : null;
}

export function installed() {
  try {
    return JSON.parse(localStorage.getItem(META_KEY) || "null");
  } catch {
    return null;
  }
}

const hasCaches = () => typeof caches !== "undefined";

// ------------------------------------------------------------------ Chrome's on-device engine

const NativeRecognition = () => window.SpeechRecognition || window.webkitSpeechRecognition || null;

export async function chromeLocalStatus(lang) {
  const Native = NativeRecognition();
  if (!Native || typeof Native.available !== "function") return "unsupported";
  const key = lang || navigator.language || "en-US";
  try {
    const status = await Native.available({ langs: [key], processLocally: true });
    chromeStatusCache.set(key, status);
    return status;
  } catch {
    return "unsupported";
  }
}

export function chromeLocalReady(lang) {
  return chromeStatusCache.get(lang || navigator.language || "en-US") === "available";
}

export async function installChromeLocal(lang) {
  const Native = NativeRecognition();
  if (!Native || typeof Native.install !== "function") return false;
  const key = lang || navigator.language || "en-US";
  try {
    const ok = await Native.install({ langs: [key], processLocally: true });
    if (ok) chromeStatusCache.set(key, "available");
    return !!ok;
  } catch {
    return false;
  }
}

// ------------------------------------------------------------------ Vosk model download / import

async function fetchWithProgress(url, onProgress) {
  const response = await fetch(url, { mode: "cors" });
  if (!response.ok) throw new Error(`Download failed (${response.status})`);
  const total = Number(response.headers.get("content-length")) || 0;
  if (!response.body || !total) {
    const blob = await response.blob();
    onProgress?.(1);
    return blob;
  }
  const reader = response.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress?.(Math.min(1, received / total));
  }
  return new Blob(chunks);
}

async function cacheScript() {
  const cache = await caches.open(CACHE);
  if (await cache.match(SCRIPT_KEY)) return;
  const response = await fetch(VOSK_SCRIPT, { mode: "cors" });
  if (!response.ok) throw new Error(`Couldn't fetch the speech engine (${response.status})`);
  await cache.put(SCRIPT_KEY, new Response(await response.blob(), { headers: { "content-type": "text/javascript" } }));
}

async function storeModel(blob, meta) {
  const cache = await caches.open(CACHE);
  await cache.put(MODEL_KEY, new Response(blob, { headers: { "content-type": "application/gzip" } }));
  forgetModel();
  const saved = { ...meta, bytes: blob.size, at: new Date().toISOString() };
  localStorage.setItem(META_KEY, JSON.stringify(saved));
  return saved;
}

// Downloads the engine and the model for `lang` (onProgress gets 0..1) and keeps both for offline use.
export async function downloadModel(lang, onProgress) {
  if (!hasCaches()) throw new Error("This browser can't store files for offline use.");
  const model = modelFor(lang);
  if (!model) throw new Error("No downloadable offline model for this language. Load a model file instead.");
  await cacheScript();
  const blob = await fetchWithProgress(MODEL_BASE + model.file, onProgress);
  return storeModel(blob, { lang: model.lang, name: model.name, file: model.file, source: "download" });
}

// Uses a Vosk model the user already has (a model folder packed as .tar.gz).
export async function importModel(file) {
  if (!hasCaches()) throw new Error("This browser can't store files for offline use.");
  if (!/\.(tar\.gz|tgz)$/i.test(file.name)) throw new Error("Pick a Vosk model packed as .tar.gz");
  await cacheScript();
  return storeModel(file, { lang: "", name: file.name.replace(/\.(tar\.gz|tgz)$/i, ""), file: file.name, source: "file" });
}

export async function removeModel() {
  forgetModel();
  localStorage.removeItem(META_KEY);
  if (hasCaches()) await caches.delete(CACHE);
}

function forgetModel() {
  const pending = modelPromise;
  modelPromise = null;
  pending?.then((model) => model.terminate?.()).catch(() => {});
}

async function loadScript() {
  if (window.Vosk) return window.Vosk;
  const cache = await caches.open(CACHE);
  let response = await cache.match(SCRIPT_KEY);
  if (!response) {
    await cacheScript();
    response = await cache.match(SCRIPT_KEY);
  }
  const url = URL.createObjectURL(await response.blob());
  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.onload = resolve;
    script.onerror = () => reject(new Error("Couldn't start the offline speech engine"));
    document.head.appendChild(script);
  });
  if (!window.Vosk) throw new Error("Couldn't start the offline speech engine");
  return window.Vosk;
}

export function loadModel() {
  if (!modelPromise) {
    modelPromise = (async () => {
      if (!installed()) throw new Error("Offline voice isn't set up");
      const Vosk = await loadScript();
      const cache = await caches.open(CACHE);
      const response = await cache.match(MODEL_KEY);
      if (!response) throw new Error("The offline model is missing. Set up offline voice again.");
      const url = URL.createObjectURL(await response.blob());
      return Vosk.createModel(url, -1); // log warnings and errors only
    })();
    modelPromise.catch(() => {
      modelPromise = null;
    });
  }
  return modelPromise;
}

// ------------------------------------------------------------------ SpeechRecognition-shaped wrapper

export class LocalRecognition {
  constructor() {
    this.lang = "";
    this.continuous = false;
    this.interimResults = true;
    this.maxAlternatives = 1;
    this.onstart = null;
    this.onresult = null;
    this.onerror = null;
    this.onend = null;
    this.running = false;
    this.parts = null;
    this.local = true;
  }

  start() {
    if (this.running) throw new Error("already started");
    this.running = true;
    this.begin();
  }

  async begin() {
    try {
      const model = await loadModel();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
        video: false,
      });
      if (!this.running) {
        stream.getTracks().forEach((track) => track.stop());
        return this.finish();
      }
      const context = new AudioContext();
      const recognizer = new model.KaldiRecognizer(context.sampleRate);
      recognizer.on("partialresult", (message) => {
        const text = message?.result?.partial;
        if (text && this.interimResults && this.running) this.emit(text, false);
      });
      recognizer.on("result", (message) => {
        const text = message?.result?.text;
        if (!text) return;
        this.emit(text, true);
        if (!this.continuous && this.running) this.stop();
      });
      const source = context.createMediaStreamSource(stream);
      const node = context.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = (event) => {
        try {
          recognizer.acceptWaveform(event.inputBuffer);
        } catch {
          /* engine busy or stopped */
        }
      };
      source.connect(node);
      node.connect(context.destination);
      this.parts = { stream, context, recognizer, source, node };
      this.onstart?.();
    } catch (error) {
      this.running = false;
      const name = error?.name || "";
      const code = name === "NotAllowedError" ? "not-allowed" : name === "NotFoundError" ? "audio-capture" : "local-model";
      this.onerror?.({ error: code, message: String(error?.message || error) });
      this.finish();
    }
  }

  emit(text, isFinal) {
    const result = [{ transcript: text, confidence: 1 }];
    result.isFinal = isFinal;
    this.onresult?.({ resultIndex: 0, results: [result] });
  }

  stop() {
    if (!this.running && !this.parts) return;
    this.running = false;
    const parts = this.parts;
    this.parts = null;
    if (!parts) return;
    try {
      parts.recognizer.retrieveFinalResult();
    } catch {
      /* nothing pending */
    }
    parts.node.onaudioprocess = null;
    try {
      parts.source.disconnect();
      parts.node.disconnect();
    } catch {
      /* already disconnected */
    }
    parts.stream.getTracks().forEach((track) => track.stop());
    setTimeout(() => {
      try {
        parts.recognizer.remove();
      } catch {
        /* gone */
      }
      parts.context.close().catch(() => {});
      this.finish();
    }, 400);
  }

  abort() {
    this.stop();
  }

  finish() {
    this.onend?.();
  }
}
