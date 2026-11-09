// Browser backend for the online demo (GitHub Pages).
//
// The local app talks to server.py. On the demo site there is no server, so this module
// answers the same /api/... calls inside the browser: it loads Python (Pyodide, bundled in
// ./pyodide/) and runs the real server.py Studio class and the skill's own scripts
// (panel.py, van_westendorp.py, unit_economics.py, compile.py). Every number is computed by
// the same code as the local app. Files live in browser memory and are gone on reload.
//
// It only activates when the page is not served by server.py (any host other than localhost).

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
// Inside Cairn there is no Founder Studio server, so <html data-backend="browser"> forces this mode.
const isDemo = !LOCAL_HOSTS.has(location.hostname)
  || new URLSearchParams(location.search).has("browser")
  || document.documentElement.dataset.backend === "browser";

const PY_SETUP = `
import contextlib, io, json, runpy, sys, traceback
from pathlib import Path

sys.path.insert(0, "/app")
import server

def run_tool_in_process(self, script, *args, stdin=None):
    """Same contract as Studio.run_tool, but runs the script in this interpreter (no subprocess in a browser)."""
    if not self.tools:
        raise RuntimeError("skill tools not found")
    argv = [str(self.tools / script), *[str(a) for a in args]]
    out, err = io.StringIO(), io.StringIO()
    saved_argv, saved_stdin = sys.argv, sys.stdin
    sys.argv = argv
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runpy.run_path(argv[0], run_name="__main__")
    except SystemExit as stop:
        if isinstance(stop.code, int):
            code = stop.code
        elif stop.code is not None:
            err.write(str(stop.code))
            code = 1
    except Exception:
        err.write(traceback.format_exc())
        code = 1
    finally:
        sys.argv, sys.stdin = saved_argv, saved_stdin
    return code, out.getvalue(), err.getvalue()

server.Studio.run_tool = run_tool_in_process
STUDIO = server.Studio("/app/projects", "/app/tools")

def handle(method, path, query_json, body_json):
    query = json.loads(query_json)
    try:
        if method == "GET" and path == "/api/projects":
            data = STUDIO.list_projects()
            data["root"] = "browser memory (online demo)"
        elif method == "GET" and path == "/api/project":
            data = STUDIO.get_project(query.get("name", ""), query.get("variant"))
        elif method == "POST" and path == "/api/llm":
            raise RuntimeError("AI buyers need the local app (python server.py): the online demo has no server to call a model from, and it never asks for an API key.")
        elif method == "POST":
            routes = {
                "/api/deal": STUDIO.deal,
                "/api/answers": STUDIO.save_answers,
                "/api/numbers": STUDIO.numbers,
                "/api/save": STUDIO.save_file,
                "/api/verdict": STUDIO.verdict,
            }
            if path not in routes:
                return 404, json.dumps({"error": "not found"})
            data = routes[path](json.loads(body_json or "{}"))
        else:
            return 404, json.dumps({"error": "not found"})
        return 200, json.dumps(data, ensure_ascii=False)
    except (ValueError, KeyError) as error:
        return 400, json.dumps({"error": str(error)})
    except FileNotFoundError as error:
        return 404, json.dumps({"error": str(error)})
    except Exception as error:
        return 500, json.dumps({"error": str(error)})
`;

let enginePromise = null;

function setStatus(text) {
  let node = document.getElementById("demo-engine-status");
  if (!node) {
    node = document.createElement("div");
    node.id = "demo-engine-status";
    node.setAttribute("role", "status");
    node.className = "demo-engine-status";
    document.body.append(node);
  }
  node.textContent = text;
  node.hidden = !text;
}

async function startEngine() {
  setStatus("Loading the Python engine (one time, about 14 MB)...");
  // Cairn reuses the Python runtime already published with Founder Studio (same site), not a second 14 MB copy.
  const base = document.documentElement.dataset.pyodide || new URL("../pyodide/", import.meta.url).href;
  const { loadPyodide } = await import(new URL("pyodide.mjs", base).href);
  const pyodide = await loadPyodide({ indexURL: base });
  const bundle = await (await realFetch(new URL("../bundle.json", import.meta.url).href)).json();
  for (const [path, content] of Object.entries(bundle.files)) {
    const full = `/app/${path}`;
    pyodide.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
    pyodide.FS.writeFile(full, content, { encoding: "utf8" });
  }
  await pyodide.runPythonAsync(PY_SETUP);
  setStatus("");
  return pyodide.globals.get("handle");
}

const realFetch = window.fetch.bind(window);

async function demoFetch(input, init = {}) {
  const url = new URL(typeof input === "string" ? input : input.url, location.href);
  const marker = url.pathname.indexOf("/api/");
  if (marker < 0) return realFetch(input, init);
  const path = url.pathname.slice(marker);
  enginePromise = enginePromise || startEngine();
  let handle;
  try {
    handle = await enginePromise;
  } catch (error) {
    enginePromise = null;
    setStatus("");
    return new Response(JSON.stringify({ error: `Could not start the in-browser engine: ${error.message}` }), { status: 500 });
  }
  const method = (init.method || "GET").toUpperCase();
  const query = JSON.stringify(Object.fromEntries(url.searchParams));
  const result = handle(method, path, query, typeof init.body === "string" ? init.body : "");
  const [status, text] = result.toJs();
  result.destroy();
  return new Response(text, { status, headers: { "content-type": "application/json" } });
}

if (isDemo) {
  window.fetch = demoFetch;
  document.documentElement.classList.add("online-demo");
}

export { isDemo };
