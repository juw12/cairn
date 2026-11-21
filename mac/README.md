# The Mac app's program

`cairn-launcher` is the program inside `Cairn.app`. [`docs/mac-install.sh`](../docs/mac-install.sh) copies it
there. It loads Cairn's own Python and runs `Cairn.pyw` in the same process, so macOS sees one app called Cairn:
its Dock icon, its menu-bar icon, its microphone question.

Why not a shell script that starts Python: on macOS 26 an app whose program hands over to another program gets
no menu-bar icon (measured on GitHub's macOS 26 runner: a plain icon shows from a bare script and from this
program, and does not show from a script inside an app, signed or not).

`launcher.c` is its whole source. To build it again, on any Mac with Apple's command line tools:

```
clang -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 -O2 -Wall -o mac/cairn-launcher mac/launcher.c
codesign --force --sign - mac/cairn-launcher
```

The `Mac app` workflow installs Cairn with the file that is checked in, on Apple silicon and on Intel, and
checks the menu-bar icon, the window and the page.
