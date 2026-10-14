/* wisp-ui-reclaim: keep the Steam UI from squeezing a game (operator-locked plan, docs/WISPOS-NOTES.md "Steam UI memory: decisions", item 1).
 *
 * The Steam UI is a Chromium (the steamwebhelper processes, about 90% of the Steam stack's memory) and it grows over a session. When a game owns the screen the UI is hidden, so its
 * cold pages should go to zram BEFORE the game's pages are touched. The kernel does that on its own once the UI is a cgroup with a memory.high ceiling: above the ceiling the
 * cgroup's pages are reclaimed (to swap, here compressed RAM) instead of the whole system's. Measured: with the web helpers paged out, Quake III's frame rate stopped falling into
 * its slow mode (24 fps in 2 of 4 runs, none in 4; variance 2.8 -> 0.4 fps).
 *
 * What it does, every POLL milliseconds (a /proc scan, no threads, about 1 MB resident):
 *   - every process whose comm is `steamwebhelper` is put into the cgroup <root>/wisp-ui (new helpers appear when Steam starts and when the UI is recycled);
 *   - memory.high of that cgroup is HIGH_GAME while a game is running (a process whose comm is `reaper`: Steam starts one per game) and HIGH_IDLE otherwise.
 * On SIGTERM it lifts the ceiling to `max` and exits, so stopping the service never leaves the UI throttled. It never kills, freezes or signals anything, and it touches no game process: a game is never in the cgroup. It writes only cgroup control files (RAM), never the card.
 * Why a cgroup file and not a unit's MemoryHigh=: the image runs systemd in hybrid mode, where the memory controller exists only on the unified v2 hierarchy mounted at
 * /sys/fs/cgroup/unified; processes may be in a v1 cgroup for systemd and a v2 cgroup for memory at the same time.
 *
 *   wisp-ui-reclaim [--root DIR] [--proc DIR] [--high-idle BYTES] [--high-game BYTES] [--poll MS] [--freeze] [--follow-focus] [--swaymsg PATH] [--once]
 * --follow-focus (dev, 2026-10-13, operator idea "a soft freeze"): the ceiling follows which window is in front exactly as with --freeze (the game ceiling only while a game runs AND the
 * Steam UI is not in front, the idle ceiling when the UI is shown), but nothing is frozen: the UI stays alive, to be kept quiet by priority instead (docs/WISPOS-NOTES.md "soft freeze").
 * In that mode a game that is still LAUNCHING (a `reaper` exists but no window other than the Steam UI has taken focus yet) counts as "game in front": the launch is the most memory-hungry moment.
 * Defaults (the one place they live): /sys/fs/cgroup/unified, /proc, 450 MiB idle, 96 MiB in a game, 200 ms. The idle ceiling was 300 MiB until the first cold start of Steam's UI on a
 * freshly flashed card (2026-10-12): the UI's working set sits at about 300 MiB, so every allocation was throttled, zram filled to its 450 MiB RAM cap, the load average reached 47 and the
 * UI took over 30 minutes to appear; with the ceiling lifted it came up in about 10, and with 450 MiB on a fresh start the load peaked at 7, memory pressure was 0 within five minutes and
 * zram held 115 MB. The ceilings are INITIAL values: a 2026-10-11 device test (docs/WISPOS-NOTES.md) held
 * the UI at 300 MB resident with the rest in zram and the UI up, and a simulated game start took it from 245 MB to 76 MB in 3 s (+75-90 MB for the game); the numbers get tuned on the device.
 * The 200 ms poll matters: pages are charged to the cgroup that was current when they were ALLOCATED, so a helper must be moved while it is young (a 16 hour old helper moved in later
 * is charged 0: its resident pages stay uncharged and the ceiling cannot reach them). So this must be running BEFORE Steam starts (wisp-ui-reclaim.service is ordered before wisp-steam). --once does a single pass and exits (the tests use it); an unusable cgroup root exits 1 with the reason.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define UI_COMM "steamwebhelper"
#define GAME_COMM "reaper"
#define CGROUP_NAME "wisp-ui"
#define MAX_PIDS 4096

static const char *root = "/sys/fs/cgroup/unified", *procdir = "/proc";
static volatile sig_atomic_t stop;
static void on_signal(int sig) { (void)sig; stop = 1; }
static bool freeze_opt;
static bool follow_opt;                                           /* --follow-focus: the ceiling follows which window is in front, like --freeze, but nothing is ever frozen */
static unsigned long long high_idle = 450ULL << 20, high_game = 96ULL << 20;

static bool write_file(const char *path, const char *text, int flags) {
    int fd = open(path, O_WRONLY | O_CREAT | flags, 0644);               /* O_CREAT: the control files exist on a real cgroupfs; a test directory needs them made */
    if (fd < 0) return false;
    ssize_t n = write(fd, text, strlen(text)); int saved = errno;
    close(fd); errno = saved; return n == (ssize_t)strlen(text);
}

static bool comm_is(const char *pid, const char *want) {
    char path[PATH_MAX], comm[64] = "";
    snprintf(path, sizeof path, "%s/%s/comm", procdir, pid);
    FILE *f = fopen(path, "r"); if (!f) return false;
    if (fgets(comm, sizeof comm, f)) comm[strcspn(comm, "\n")] = 0;
    fclose(f); return !strcmp(comm, want);
}

/* One pass over /proc: the helper pids (up to MAX_PIDS) and whether a game process exists. */
static int scan(int *ui, bool *game) {
    DIR *d = opendir(procdir); if (!d) return -1;
    int n = 0; *game = false;
    for (struct dirent *e; (e = readdir(d));) {
        if (!isdigit((unsigned char)e->d_name[0])) continue;
        if (n < MAX_PIDS && comm_is(e->d_name, UI_COMM)) ui[n++] = atoi(e->d_name);
        else if (!*game && comm_is(e->d_name, GAME_COMM)) *game = true;
    }
    closedir(d); return n;
}

static bool in_group(const char *procs_path, int pid) {
    FILE *f = fopen(procs_path, "r"); if (!f) return false;
    int p; bool found = false;
    while (fscanf(f, "%d", &p) == 1) if (p == pid) { found = true; break; }
    fclose(f); return found;
}

/* ---- sway window events: is the Steam UI the window in front? ---- */
static const char *swaymsg_path = "swaymsg";
static pid_t sway_pid = -1; static int sway_fd = -1; static time_t sway_retry; static bool ui_focused = true;       /* true = visible: the fail-safe value */
static bool game_seen;                                                                        /* a window that is not the Steam UI has taken focus since the game (a `reaper`) appeared */
static char evbuf[16384]; static size_t evlen;

/* The string value after KEY ("key": "value", spaces optional), at most cap-1 characters. */
static bool json_str(const char *line, const char *key, char *out, size_t cap) {
    const char *p = strstr(line, key); if (!p) return false;
    p += strlen(key); while (*p == ' ' || *p == ':') p++;
    if (*p != '"') return false;
    size_t n = 0; for (p++; *p && *p != '"' && n + 1 < cap; p++) out[n++] = *p;
    out[n] = 0; return true;
}

static void on_event(const char *line) {
    char change[32] = "", cls[64] = "";
    if (!json_str(line, "\"change\"", change, sizeof change) || strcmp(change, "focus")) return;
    const char *wp = strstr(line, "\"window_properties\"");                          /* a Wayland window has none: its class is "" and it is not the UI */
    if (wp) json_str(wp, "\"class\"", cls, sizeof cls);
    bool now = !strcmp(cls, "steam");
    if (now != ui_focused) fprintf(stderr, "wisp-ui-reclaim: focus -> %s (%s)\n", cls[0] ? cls : "(no class)", now ? "the Steam UI is in front" : "the Steam UI is hidden");
    ui_focused = now;
    if (!now) game_seen = true;
}

static void sway_stop(void) {
    if (sway_fd >= 0) { close(sway_fd); sway_fd = -1; }
    if (sway_pid > 0) { kill(sway_pid, SIGTERM); waitpid(sway_pid, NULL, 0); sway_pid = -1; }
    evlen = 0; ui_focused = true; game_seen = false;                                  /* no subscription: assume visible */
}

static void sway_start(void) {
    int p[2]; if (pipe2(p, O_CLOEXEC) < 0) return;
    pid_t pid = fork();
    if (pid < 0) { close(p[0]); close(p[1]); return; }
    if (pid == 0) {
        dup2(p[1], 1); int dn = open("/dev/null", O_WRONLY); if (dn >= 0) dup2(dn, 2);
        execlp(swaymsg_path, swaymsg_path, "-t", "subscribe", "-m", "[\"window\"]", (char *)NULL); _exit(127);
    }
    close(p[1]); fcntl(p[0], F_SETFL, O_NONBLOCK); sway_fd = p[0]; sway_pid = pid; evlen = 0;
}

static void sway_read(void) {
    char c[4096]; ssize_t n;
    while ((n = read(sway_fd, c, sizeof c)) > 0) {
        for (ssize_t i = 0; i < n; i++) {
            if (c[i] == '\n') { evbuf[evlen] = 0; if (evlen) on_event(evbuf); evlen = 0; }
            else if (evlen + 1 < sizeof evbuf) evbuf[evlen++] = c[i];
        }
    }
    if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) {                       /* swaymsg ended (sway restarted or is not there): visible again, try later */
        sway_stop(); sway_retry = time(NULL) + 2;
        fprintf(stderr, "wisp-ui-reclaim: the sway window-event stream ended; treating the UI as visible until it is back\n");
    }
}

static bool setup_cgroup(char *dir, size_t cap) {
    char path[PATH_MAX]; struct stat st;
    if (stat(root, &st) != 0 || !S_ISDIR(st.st_mode)) { fprintf(stderr, "wisp-ui-reclaim: no cgroup v2 hierarchy at %s\n", root); return false; }
    snprintf(path, sizeof path, "%s/cgroup.subtree_control", root);
    if (!write_file(path, "+memory", 0) && errno != EBUSY) { fprintf(stderr, "wisp-ui-reclaim: cannot enable the memory controller (%s): %s\n", path, strerror(errno)); return false; }
    snprintf(dir, cap, "%s/%s", root, CGROUP_NAME);
    if (mkdir(dir, 0755) != 0 && errno != EEXIST) { fprintf(stderr, "wisp-ui-reclaim: cannot create %s: %s\n", dir, strerror(errno)); return false; }
    return true;
}

int main(int argc, char **argv) {
    int poll_ms = 200; bool once = false;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) root = argv[++i];
        else if (!strcmp(argv[i], "--proc") && i + 1 < argc) procdir = argv[++i];
        else if (!strcmp(argv[i], "--high-idle") && i + 1 < argc) high_idle = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--high-game") && i + 1 < argc) high_game = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--poll") && i + 1 < argc) poll_ms = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--freeze")) freeze_opt = true;
        else if (!strcmp(argv[i], "--follow-focus")) follow_opt = true;
        else if (!strcmp(argv[i], "--swaymsg") && i + 1 < argc) swaymsg_path = argv[++i];
        else if (!strcmp(argv[i], "--once")) once = true;
        else { fprintf(stderr, "usage: %s [--root DIR] [--proc DIR] [--high-idle BYTES] [--high-game BYTES] [--poll MS] [--freeze] [--follow-focus] [--swaymsg PATH] [--once]\n", argv[0]); return 2; }
    }
    if (high_game == 0 || high_idle == 0 || high_game > high_idle || poll_ms < 100) { fprintf(stderr, "wisp-ui-reclaim: need 0 < high-game <= high-idle and poll >= 100 ms\n"); return 2; }
    setvbuf(stderr, NULL, _IOLBF, 0);
    char dir[PATH_MAX], procs[PATH_MAX + 32], high[PATH_MAX + 32], freeze[PATH_MAX + 32];      /* room for the file names after the directory */
    if (!setup_cgroup(dir, sizeof dir)) return 1;
    snprintf(procs, sizeof procs, "%s/cgroup.procs", dir); snprintf(high, sizeof high, "%s/memory.high", dir); snprintf(freeze, sizeof freeze, "%s/cgroup.freeze", dir);
    unsigned long long current = 0; int frozen = -1; static int ui[MAX_PIDS];                /* frozen: -1 = not known yet (the first pass always writes it) */
    struct sigaction sa = {.sa_handler = on_signal}; sigemptyset(&sa.sa_mask); sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL);      /* no SA_RESTART: the sleep ends at once */
    while (!stop) {
        bool game; int n = scan(ui, &game);
        if (n < 0) { fprintf(stderr, "wisp-ui-reclaim: cannot read %s: %s\n", procdir, strerror(errno)); return 1; }
        int moved = 0;
        for (int i = 0; i < n; i++) {
            char pid[16]; snprintf(pid, sizeof pid, "%d\n", ui[i]);
            if (!in_group(procs, ui[i]) && write_file(procs, pid, O_APPEND)) moved++;      /* a helper that exited meanwhile is simply not moved */
        }
        if (moved) fprintf(stderr, "wisp-ui-reclaim: %d web helper process(es) now in %s\n", moved, CGROUP_NAME);
        if (!game) game_seen = false;
        /* without --freeze or --follow-focus the old rule: a game means hidden; with either, only while the UI is not in front. --follow-focus (not --freeze) also counts a game that is still
         * LAUNCHING as hidden: its window has not taken focus yet, and that is the most memory-hungry moment (prefix, Wine, FEX), measured 2026-10-13: a UI at its big ceiling during the launch
         * thrashed the machine (memory pressure 24%, 1 fps, no window in 15 minutes). */
        bool hidden = game && (!(freeze_opt || follow_opt) || !ui_focused || (follow_opt && !freeze_opt && sway_fd >= 0 && !game_seen));      /* only with a live subscription: a lost one means visible, never squeezed blind */
        unsigned long long want = hidden ? high_game : high_idle; int want_frozen = freeze_opt && hidden;
        if (freeze_opt && !want_frozen && frozen != 0) {                            /* thaw BEFORE the ceiling goes back up: the UI must be able to run while it pages back in */
            if (write_file(freeze, "0\n", O_TRUNC)) { frozen = 0; fprintf(stderr, "wisp-ui-reclaim: %s: thawed\n", CGROUP_NAME); }
            else fprintf(stderr, "wisp-ui-reclaim: cannot thaw %s: %s\n", freeze, strerror(errno));
        }
        if (want != current) {
            char val[32]; snprintf(val, sizeof val, "%llu\n", want);
            if (write_file(high, val, O_TRUNC)) { fprintf(stderr, "wisp-ui-reclaim: %s: memory.high %llu MiB (%s)\n", CGROUP_NAME, want >> 20, hidden ? "a game is running" : (game ? "a game is running, the Steam UI is in front" : "no game")); current = want; }
            else fprintf(stderr, "wisp-ui-reclaim: cannot set %s: %s\n", high, strerror(errno));
        }
        if (want_frozen && frozen != 1 && current == want) {                          /* freeze AFTER the ceiling is down: the pages go to zram first, then nothing touches them */
            if (write_file(freeze, "1\n", O_TRUNC)) { frozen = 1; fprintf(stderr, "wisp-ui-reclaim: %s: frozen (a game is running)\n", CGROUP_NAME); }
            else fprintf(stderr, "wisp-ui-reclaim: cannot freeze %s: %s\n", freeze, strerror(errno));
        }
        if (once) return 0;
        if ((freeze_opt || follow_opt) && sway_fd < 0 && time(NULL) >= sway_retry) { sway_start(); sway_retry = time(NULL) + 2; }
        if (sway_fd >= 0) { struct pollfd pf = {.fd = sway_fd, .events = POLLIN}; if (poll(&pf, 1, poll_ms) > 0) sway_read(); }
        else { struct timespec ts = {poll_ms / 1000, (poll_ms % 1000) * 1000000L}; nanosleep(&ts, NULL); }
    }
    sway_stop();
    /* Stopped (the service stops, or Steam's session ends): never leave the UI frozen or throttled at a game's ceiling. */
    if (freeze_opt && write_file(freeze, "0\n", O_TRUNC)) fprintf(stderr, "wisp-ui-reclaim: stopped; %s: thawed\n", CGROUP_NAME);
    if (write_file(high, "max\n", O_TRUNC)) fprintf(stderr, "wisp-ui-reclaim: stopped; %s: memory.high max\n", CGROUP_NAME);
    return 0;
}
