/* Cairn.app's program on a Mac: runs Cairn inside this process, with Cairn's own Python.
 *
 * macOS ties what an app may show (its menu-bar icon, since macOS 26) to the program inside the .app.
 * A shell script that hands over to Python is a different program from the one in the .app, and macOS 26
 * then shows no menu-bar icon for it. So this small program stays the app: it loads Python's library and
 * runs Cairn.pyw in the same process.
 *
 * It reads three lines from Contents/Resources/cairn-paths.txt, written by docs/mac-install.sh:
 *     1  libpython3.13.dylib of Cairn's own Python
 *     2  the python of Cairn's environment (so Python finds the window libraries, and so
 *        sys.executable is a real python for everything Cairn starts with it)
 *     3  Cairn.pyw
 *
 * Build (both chips, any Mac with Apple's command line tools):
 *     clang -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 -O2 -o cairn-launcher launcher.c
 */
#include <dlfcn.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef int (*python_main)(int, char **);

static void fail(const char *what, const char *detail) {
    fprintf(stderr, "Cairn: %s%s%s\n", what, detail ? ": " : "", detail ? detail : "");
    exit(1);
}

static void read_line(FILE *file, char *into, size_t size, const char *what) {
    if (!fgets(into, (int)size, file)) fail("cairn-paths.txt has no line for", what);
    into[strcspn(into, "\r\n")] = 0;
}

int main(int argc, char **argv) {
    char program[PATH_MAX], real[PATH_MAX], bundle[PATH_MAX], paths[PATH_MAX];
    char library[PATH_MAX], python[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(program);
    if (_NSGetExecutablePath(program, &size) != 0 || !realpath(program, real)) fail("cannot find itself", NULL);

    /* <bundle>/Contents/MacOS/Cairn */
    snprintf(bundle, sizeof(bundle), "%s", real);
    for (int up = 0; up < 3; up++) {
        char *slash = strrchr(bundle, '/');
        if (!slash) fail("is not inside an app", real);
        *slash = 0;
    }
    snprintf(paths, sizeof(paths), "%s/Contents/Resources/cairn-paths.txt", bundle);
    FILE *file = fopen(paths, "r");
    if (!file) fail("cannot read", paths);
    read_line(file, library, sizeof(library), "Python's library");
    read_line(file, python, sizeof(python), "Cairn's python");
    read_line(file, script, sizeof(script), "Cairn.pyw");
    fclose(file);

    setenv("CAIRN_APP_BUNDLE", bundle, 1);
    setenv("__PYVENV_LAUNCHER__", python, 1);   /* Python then takes its environment from this python */
    const char *path = getenv("PATH");
    char longer[8192];
    snprintf(longer, sizeof(longer), "%s:/usr/local/bin:/opt/homebrew/bin", path && *path ? path : "/usr/bin:/bin:/usr/sbin:/sbin");
    setenv("PATH", longer, 1);
    char folder[PATH_MAX];
    snprintf(folder, sizeof(folder), "%s", script);
    if (chdir(dirname(folder)) != 0) fail("cannot open Cairn's folder", script);

    void *handle = dlopen(library, RTLD_NOW | RTLD_GLOBAL);
    if (!handle) fail("cannot load Python", dlerror());
    python_main run = (python_main)dlsym(handle, "Py_BytesMain");
    if (!run) fail("this Python has no Py_BytesMain", library);

    char **args = calloc((size_t)argc + 2, sizeof(char *));
    int count = 0;
    args[count++] = python;
    args[count++] = script;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) == 0) continue;   /* old macOS adds this when Finder starts an app */
        args[count++] = argv[i];
    }
    return run(count, args);
}
