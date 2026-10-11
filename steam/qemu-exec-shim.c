/*
 * TEST-ONLY user-space stand-in for binfmt_misc, for running Steam under
 * qemu-aarch64-static without registering any kernel handler (HOST-SAFETY).
 * Loaded into the emulated process with LD_PRELOAD. When Steam executes an
 * AArch64 binary (steamwebhelper, steamsysinfo, `sh`, ...), the call is
 * redirected to `qemu-aarch64-static -cpu cortex-a53 -L <sysroot> <binary>`.
 * Environment: EMU_QEMU (qemu path), EMU_SYSROOT (ROCKNIX userland copy).
 * Never installed on the handheld; the emulated device runs these natively.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char *qemu_path(void)   { const char *p = getenv("EMU_QEMU"); return p ? p : "/usr/bin/qemu-aarch64-static"; }
static const char *sysroot_path(void) { return getenv("EMU_SYSROOT"); }

static int is_arm_elf(const char *path)
{
    unsigned char h[20];
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    ssize_t n;
    if (fd < 0) return 0;
    n = read(fd, h, sizeof h);
    close(fd);
    return n == (ssize_t)sizeof h && memcmp(h, ELFMAG, SELFMAG) == 0 && h[EI_CLASS] == ELFCLASS64 &&
           (h[18] | (h[19] << 8)) == EM_AARCH64;
}

/* Resolve a command the way execvp does (guest PATH view). */
static int resolve(const char *file, char *out, size_t size)
{
    const char *path = getenv("PATH");
    if (strchr(file, '/')) { snprintf(out, size, "%s", file); return access(out, X_OK) == 0; }
    if (!path) path = "/usr/bin:/bin";
    char *copy = strdup(path), *save = NULL;
    for (char *dir = strtok_r(copy, ":", &save); dir; dir = strtok_r(NULL, ":", &save)) {
        snprintf(out, size, "%s/%s", *dir ? dir : ".", file);
        if (access(out, X_OK) == 0) { free(copy); return 1; }
    }
    free(copy);
    return 0;
}

/* Host-visible path of a guest path: QEMU maps absolute guest paths under the
 * sysroot when they exist there, so the new qemu process needs the real path. */
static void host_path(const char *guest, char *out, size_t size)
{
    const char *root = sysroot_path();
    char probe[4096];
    if (root && guest[0] == '/') {
        snprintf(probe, sizeof probe, "%s%s", root, guest);
        if (access(probe, F_OK) == 0) { snprintf(out, size, "%s", probe); return; }
    }
    snprintf(out, size, "%s", guest);
}

static char **wrap_argv(const char *host, char *const argv[])
{
    size_t argc = 0;
    while (argv && argv[argc]) ++argc;
    char **n = calloc(argc + 12, sizeof *n);
    size_t i = 0;
    n[i++] = (char *)qemu_path();
    n[i++] = "-cpu"; n[i++] = "cortex-a53";
    if (sysroot_path()) { n[i++] = "-L"; n[i++] = (char *)sysroot_path(); }
    n[i++] = "-0"; n[i++] = (char *)(argc ? argv[0] : host);
    n[i++] = (char *)host;
    for (size_t k = 1; k < argc; ++k) n[i++] = argv[k];
    n[i] = NULL;
    return n;
}

static int want_wrap(const char *file, int search, char *host, size_t size)
{
    char found[4096];
    if (!(search ? resolve(file, found, sizeof found) : (snprintf(found, sizeof found, "%s", file), access(found, X_OK) == 0)))
        return 0;
    if (!is_arm_elf(found)) return 0;
    host_path(found, host, size);
    return 1;
}

int execve(const char *path, char *const argv[], char *const envp[])
{
    static int (*real)(const char *, char *const[], char *const[]);
    char host[4096];
    if (!real) real = dlsym(RTLD_NEXT, "execve");
    if (want_wrap(path, 0, host, sizeof host)) return real(qemu_path(), wrap_argv(host, argv), envp);
    return real(path, argv, envp);
}
int execv(const char *path, char *const argv[]) { extern char **environ; return execve(path, argv, environ); }
int execvpe(const char *file, char *const argv[], char *const envp[])
{
    static int (*real)(const char *, char *const[], char *const[]);
    char host[4096];
    if (!real) real = dlsym(RTLD_NEXT, "execvpe");
    if (want_wrap(file, 1, host, sizeof host)) return execve(qemu_path(), wrap_argv(host, argv), envp);
    return real(file, argv, envp);
}
int execvp(const char *file, char *const argv[]) { extern char **environ; return execvpe(file, argv, environ); }

int posix_spawn(pid_t *pid, const char *path, const posix_spawn_file_actions_t *fa,
                const posix_spawnattr_t *attr, char *const argv[], char *const envp[])
{
    static int (*real)(pid_t *, const char *, const posix_spawn_file_actions_t *, const posix_spawnattr_t *, char *const[], char *const[]);
    char host[4096];
    if (!real) real = dlsym(RTLD_NEXT, "posix_spawn");
    if (want_wrap(path, 0, host, sizeof host)) return real(pid, qemu_path(), fa, attr, wrap_argv(host, argv), envp);
    return real(pid, path, fa, attr, argv, envp);
}
int posix_spawnp(pid_t *pid, const char *file, const posix_spawn_file_actions_t *fa,
                 const posix_spawnattr_t *attr, char *const argv[], char *const envp[])
{
    static int (*real)(pid_t *, const char *, const posix_spawn_file_actions_t *, const posix_spawnattr_t *, char *const[], char *const[]);
    char host[4096];
    if (!real) real = dlsym(RTLD_NEXT, "posix_spawnp");
    if (want_wrap(file, 1, host, sizeof host)) return real(pid, qemu_path(), fa, attr, wrap_argv(host, argv), envp);
    return real(pid, file, fa, attr, argv, envp);
}

/* system()/popen(): run the command line with the device's own (ARM) bash. */
static char *shell_command(const char *command)
{
    const char *root = sysroot_path();
    size_t cap = strlen(command) * 4 + 1024;
    char *out = malloc(cap), *w = out;
    w += snprintf(w, cap, "exec '%s' -cpu cortex-a53 %s%s%s '%s%s' -c '", qemu_path(),
                  root ? "-L '" : "", root ? root : "", root ? "'" : "", root ? root : "", "/usr/bin/bash");
    for (const char *p = command; *p; ++p) { if (*p == '\'') { memcpy(w, "'\\''", 4); w += 4; } else *w++ = *p; }
    *w++ = '\''; *w = 0;
    return out;
}
int system(const char *command)
{
    static int (*real)(const char *);
    if (!real) real = dlsym(RTLD_NEXT, "system");
    if (!command || !sysroot_path()) return real(command);
    char *wrapped = shell_command(command);
    int status = real(wrapped);
    free(wrapped);
    return status;
}
FILE *popen(const char *command, const char *mode)
{
    static FILE *(*real)(const char *, const char *);
    if (!real) real = dlsym(RTLD_NEXT, "popen");
    if (!sysroot_path()) return real(command, mode);
    char *wrapped = shell_command(command);
    FILE *f = real(wrapped, mode);
    free(wrapped);
    return f;
}
