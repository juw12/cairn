"""Windows Start-menu shortcut that keeps Cairn's icon when the window is pinned to the taskbar.

Cairn runs as ``pythonw.exe Cairn.pyw`` and tags its window with the AppUserModelID ``juw12.Cairn``
so it gets its own taskbar button instead of being grouped with every other Python app. Windows
only knows which icon (and relaunch command) belongs to that ID if a Start-menu shortcut carries the
same ID in its property store. Without it a pinned Cairn shows a blank icon and can't be relaunched.

``stamp_shortcut`` writes the shortcut (target, arguments, icon) and sets
``System.AppUserModel.ID`` on it. It uses PowerShell plus a few lines of C# COM interop, so it needs
nothing beyond what ships with Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

AUMID = "juw12.Cairn"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_CSHARP = r"""
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class CairnShortcut {
    [StructLayout(LayoutKind.Sequential)]
    public struct PropertyKey {
        public Guid FormatId;
        public uint PropertyId;
    }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    public struct PropVariant {
        [FieldOffset(0)] public ushort ValueType;
        [FieldOffset(8)] public IntPtr Pointer;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }

    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellLinkW {
        void GetPath(IntPtr file, int size, IntPtr data, uint flags);
        void GetIDList(out IntPtr list);
        void SetIDList(IntPtr list);
        void GetDescription(IntPtr name, int size);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetWorkingDirectory(IntPtr dir, int size);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string dir);
        void GetArguments(IntPtr args, int size);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string args);
        void GetHotkey(out short hotkey);
        void SetHotkey(short hotkey);
        void GetShowCmd(out int show);
        void SetShowCmd(int show);
        void GetIconLocation(IntPtr path, int size, out int index);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
        void Resolve(IntPtr hwnd, uint flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string file);
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLink { }

    public static void Write(string lnk, string target, string args, string icon, string workdir, string appId) {
        var link = (IShellLinkW)new ShellLink();
        link.SetPath(target);
        link.SetArguments(args);
        link.SetWorkingDirectory(workdir);
        link.SetIconLocation(icon, 0);
        link.SetDescription("Cairn - your project's second brain");

        var store = (IPropertyStore)link;
        var key = new PropertyKey { FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 5 };
        var value = new PropVariant { ValueType = 31, Pointer = Marshal.StringToCoTaskMemUni(appId) };
        try {
            store.SetValue(ref key, ref value);
            store.Commit();
        } finally {
            Marshal.FreeCoTaskMem(value.Pointer);
        }
        ((IPersistFile)link).Save(lnk, true);
    }
}
"""


def start_menu_shortcut() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Cairn.lnk"


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def stamp_shortcut(lnk: Path, target: str, args: str, icon: Path, workdir: Path, app_id: str = AUMID) -> None:
    """Create or rewrite ``lnk`` so it launches Cairn with its icon and carries ``app_id``."""
    if sys.platform != "win32":
        return
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f"Add-Type -TypeDefinition @'\n{_CSHARP}\n'@\n"
        f"[CairnShortcut]::Write({_ps_quote(str(lnk))}, {_ps_quote(target)}, {_ps_quote(args)}, "
        f"{_ps_quote(str(icon))}, {_ps_quote(str(workdir))}, {_ps_quote(app_id)})"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip()[-400:] or "could not write the shortcut")


def read_app_id(lnk: Path) -> str | None:
    """The AppUserModelID stored in a shortcut, or None (used by the tests and the self-check)."""
    if sys.platform != "win32" or not lnk.exists():
        return None
    script = (
        "$shell = New-Object -ComObject Shell.Application; "
        f"$item = $shell.Namespace({_ps_quote(str(lnk.parent))}).ParseName({_ps_quote(lnk.name)}); "
        "$item.ExtendedProperty('System.AppUserModel.ID')"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW,
    )
    value = result.stdout.strip()
    return value or None
