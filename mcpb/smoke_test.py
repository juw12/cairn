"""Smoke test an MCPB bundle: unpack it, start the server exactly as manifest.json says, list its tools.

    python mcpb/smoke_test.py <bundle.mcpb> [--expect-tools N]

The bundle must serve exactly the tools the code beside this script defines (cairn/mcp.py). That list is the
one source: a tool added or removed in the code needs no number changed anywhere. `--expect-tools N` is a
floor (N tools or more), kept so a caller that still passes a number written long ago does not stop a publish
that is fine; a bundle with fewer tools than N still fails.

Standard library only. user_config values are filled from their defaults, or a fresh temp folder for
directory settings, so the test needs no setup.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile


def substitute(value, mapping):
    return re.sub(r"\$\{([^}]+)\}", lambda m: mapping.get(m.group(1), m.group(0)), value)


def tools_of_this_checkout():
    """The names of the tools cairn/mcp.py beside this script defines, or None when this script was copied
    somewhere without the code (then only the floor is checked)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isfile(os.path.join(root, "cairn", "mcp.py")):
        return None
    sys.path.insert(0, root)
    try:
        from cairn import mcp
    except Exception as error:  # noqa: BLE001 - say why and go on with the floor
        print(f"could not read the tools from {root}: {error}", file=sys.stderr)
        return None
    finally:
        sys.path.remove(root)
    return [tool["name"] for tool in mcp.TOOLS]


def main(argv, expected=None):
    bundle = argv[0]
    floor = int(argv[argv.index("--expect-tools") + 1]) if "--expect-tools" in argv else None
    expected = tools_of_this_checkout() if expected is None else expected
    with tempfile.TemporaryDirectory() as unpack_dir, tempfile.TemporaryDirectory() as work_dir:
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(unpack_dir)
        manifest = json.load(open(os.path.join(unpack_dir, "manifest.json"), encoding="utf-8"))
        mapping = {"__dirname": unpack_dir, "HOME": os.path.expanduser("~")}
        for key, spec in manifest.get("user_config", {}).items():
            default = spec.get("default")
            if spec.get("type") == "directory" or default is None:
                default = work_dir
            mapping["user_config." + key] = str(default)
        config = manifest["server"]["mcp_config"]
        command = [sys.executable if config["command"] in ("python", "python3") else config["command"]]
        command += [substitute(arg, mapping) for arg in config.get("args", [])]
        env = dict(os.environ)
        env.update({k: substitute(v, mapping) for k, v in config.get("env", {}).items()})
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        stdin = "".join(json.dumps(r) + "\n" for r in requests)
        result = subprocess.run(command, input=stdin, capture_output=True, text=True, env=env, cwd=work_dir, timeout=60)
        replies = {}
        for line in result.stdout.splitlines():
            if line.strip().startswith("{"):
                message = json.loads(line)
                if "id" in message:
                    replies[message["id"]] = message
        if 1 not in replies or 2 not in replies:
            print("server did not answer initialize/tools/list", result.stderr[-2000:], file=sys.stderr)
            return 1
        server = replies[1]["result"]["serverInfo"]
        tools = [tool["name"] for tool in replies[2]["result"]["tools"]]
        print(f"{server['name']} {server.get('version')}: {len(tools)} tools: {', '.join(tools)}")
        if server.get("version") and server["version"] != manifest["version"]:
            print(f"version mismatch: server {server['version']} != manifest {manifest['version']}", file=sys.stderr)
            return 1
        if expected is not None and sorted(tools) != sorted(expected):
            missing = sorted(set(expected) - set(tools))
            extra = sorted(set(tools) - set(expected))
            print(
                "the bundle does not serve the tools of this code (cairn/mcp.py): "
                f"missing {', '.join(missing) or 'none'}; not in the code {', '.join(extra) or 'none'}",
                file=sys.stderr,
            )
            return 1
        if floor is not None and len(tools) < floor:
            print(f"expected {floor} tools or more, the bundle has {len(tools)}: {', '.join(sorted(tools))}", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
