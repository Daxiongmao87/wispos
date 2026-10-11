/* nm.h: the network side of wisp-setup, with no SDL, so it can be unit-tested on any machine (os/test_setup.py).
 *
 *  - parsing of `nmcli -t` (terse) output, whose fields are separated by ':' with literal ':' and '\' escaped by '\'
 *  - mapping nmcli's error text to a short reason a person can act on
 *  - the internet check: Steam's client manifest must come back 200 and start with "linuxarm64"; a captive portal
 *    that answers every URL with 200 and an HTML page does not pass
 *  - the line protocol of the Steam installer (steam-install): phase / progress / error / done
 *  - running a program without a shell (so an SSID or password can never be interpreted as shell syntax)
 */
#ifndef WISP_NM_H
#define WISP_NM_H

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

typedef struct { char ssid[64]; int signal; int secured; int active; } Net;

/* Split one terse line into at most `max` fields (each at most 95 characters), unescaping "\:" and "\\". Returns the count. */
static int nm_split_terse(const char *line, char fields[][96], int max) {
    int n = 0; size_t k = 0;
    if (max < 1) return 0;
    fields[0][0] = 0;
    for (const char *p = line; *p && *p != '\n' && *p != '\r'; p++) {
        if (*p == '\\' && (p[1] == ':' || p[1] == '\\')) { p++; if (k < 95) fields[n][k++] = *p; fields[n][k] = 0; continue; }
        if (*p == ':') { if (n + 1 >= max) break; n++; k = 0; fields[n][0] = 0; continue; }
        if (k < 95) { fields[n][k++] = *p; fields[n][k] = 0; }
    }
    return n + 1;
}

static int nm_cmp_signal(const void *a, const void *b) { return ((const Net *)b)->signal - ((const Net *)a)->signal; }

/* Parse `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list` output. Hidden networks (empty SSID) are left out,
 * the same SSID seen on several access points keeps its strongest signal, and the result is sorted strongest first. */
static int nm_parse_list(const char *text, Net *out, int max) {
    int n = 0; char line[512];
    for (const char *p = text; *p; ) {
        size_t len = strcspn(p, "\n"); snprintf(line, sizeof line, "%.*s", (int)(len < sizeof line - 1 ? len : sizeof line - 1), p);
        p += len; if (*p == '\n') p++;
        char f[4][96]; if (nm_split_terse(line, f, 4) < 4) continue;
        if (!f[1][0]) continue;
        int sig = atoi(f[2]), sec = f[3][0] && strcmp(f[3], "--") != 0, act = f[0][0] == '*';
        int idx = -1; for (int i = 0; i < n; i++) if (!strcmp(out[i].ssid, f[1])) { idx = i; break; }
        if (idx < 0) { if (n >= max) continue; idx = n++; snprintf(out[idx].ssid, sizeof out[idx].ssid, "%.63s", f[1]); out[idx].signal = sig; out[idx].secured = sec; out[idx].active = act; }
        else { if (sig > out[idx].signal) out[idx].signal = sig; out[idx].active |= act; }
    }
    qsort(out, (size_t)n, sizeof *out, nm_cmp_signal);
    return n;
}

/* A short, actionable reason from nmcli's error output. */
static const char *nm_failure_reason(const char *out) {
    if (strstr(out, "Secrets were required") || strstr(out, "supplicant") || strstr(out, "authentication") || strstr(out, "Wrong password") || strstr(out, "(7)"))
        return "Wrong password.";
    if (strstr(out, "No network with SSID")) return "The network isn't in range any more.";
    if (strstr(out, "imeout") || strstr(out, "timed out")) return "The network didn't answer in time.";
    if (strstr(out, "No suitable device") || strstr(out, "device is not ready") || strstr(out, "unavailable")) return "The Wi-Fi adapter isn't ready.";
    return "Couldn't join the network.";
}

/* Steam's client manifest: HTTP 200 and a body starting with "linuxarm64" (optionally after whitespace or a BOM). */
static int nm_steam_reply_ok(int http, const char *body) {
    if (http != 200 || !body) return 0;
    while (*body && (isspace((unsigned char)*body) || (unsigned char)*body >= 0xEF)) body++;
    return strncmp(body, "\"linuxarm64\"", 12) == 0;
}

/* Run argv (no shell), capture stdout+stderr into out (NUL-terminated, truncated to cap-1), kill after timeout_s seconds.
 * Returns the exit status, or -1 if it could not run, or -2 on timeout. */
static int nm_run(char *const argv[], char *out, size_t cap, int timeout_s) {
    int fd[2]; if (cap) out[0] = 0;
    if (pipe(fd) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(fd[0]); close(fd[1]); return -1; }
    if (pid == 0) {
        dup2(fd[1], 1); dup2(fd[1], 2); close(fd[0]); close(fd[1]);
        int nul = open("/dev/null", 0); if (nul >= 0) { dup2(nul, 0); close(nul); }
        execvp(argv[0], argv); _exit(127);
    }
    close(fd[1]);
    size_t used = 0; time_t start = time(NULL); int timed_out = 0;
    for (;;) {
        fd_set s; FD_ZERO(&s); FD_SET(fd[0], &s); struct timeval tv = {1, 0};
        int r = select(fd[0] + 1, &s, NULL, NULL, &tv);
        if (r > 0) { char buf[512]; ssize_t k = read(fd[0], buf, sizeof buf); if (k <= 0) break; for (ssize_t i = 0; i < k && used + 1 < cap; i++) out[used++] = buf[i]; }
        if (timeout_s > 0 && time(NULL) - start >= timeout_s) { kill(pid, SIGKILL); timed_out = 1; break; }
    }
    close(fd[0]);
    int st = 0; waitpid(pid, &st, 0);
    if (cap) out[used < cap ? used : cap - 1] = 0;
    if (timed_out) return -2;
    return WIFEXITED(st) ? WEXITSTATUS(st) : -1;
}

/* One line of steam-install's stdout: "phase NAME", "progress DONE TOTAL [COMPONENT]", "error TEXT", "done". */
typedef enum { INST_NONE, INST_PHASE, INST_PROGRESS, INST_ERROR, INST_DONE } InstKind;
typedef struct { InstKind kind; char text[160]; long long done, total; } InstEvent;

static InstKind nm_parse_install_line(const char *line, InstEvent *ev) {
    ev->kind = INST_NONE; ev->text[0] = 0; ev->done = ev->total = 0;
    if (!strncmp(line, "phase ", 6)) { snprintf(ev->text, sizeof ev->text, "%.*s", 150, line + 6); ev->kind = INST_PHASE; }
    else if (!strncmp(line, "progress ", 9)) {
        char *end; long long d = strtoll(line + 9, &end, 10); if (end == line + 9) return INST_NONE;
        char *end2; long long t = strtoll(end, &end2, 10); if (end2 == end || d < 0 || t <= 0 || d > t) return INST_NONE;
        ev->done = d; ev->total = t; ev->kind = INST_PROGRESS;
    }
    else if (!strncmp(line, "error ", 6)) { snprintf(ev->text, sizeof ev->text, "%.*s", 150, line + 6); ev->kind = INST_ERROR; }
    else if (!strcmp(line, "done")) ev->kind = INST_DONE;
    for (char *c = ev->text + strlen(ev->text); c > ev->text && (c[-1] == '\n' || c[-1] == '\r' || c[-1] == ' '); ) *--c = 0;
    return ev->kind;
}

/* Start argv without a shell with stdout and stderr merged into the returned pipe (read end); -1 on failure. */
static int nm_spawn(char *const argv[], pid_t *pid_out) {
    int fd[2]; if (pipe(fd) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(fd[0]); close(fd[1]); return -1; }
    if (pid == 0) {
        dup2(fd[1], 1); dup2(fd[1], 2); close(fd[0]); close(fd[1]);
        int nul = open("/dev/null", 0); if (nul >= 0) { dup2(nul, 0); close(nul); }
        execvp(argv[0], argv); _exit(127);
    }
    close(fd[1]); *pid_out = pid; return fd[0];
}

#endif
