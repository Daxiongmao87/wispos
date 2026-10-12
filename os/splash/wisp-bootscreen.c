/* wisp-bootscreen: the WispOS boot screen as a Wayland overlay, from the compositor's first moment until the first window appears (docs/WISPOS-SPLASH-DESIGN.md, "the Wayland phase").
 *
 * The framebuffer splash (wisp-splash, in the initramfs) is hidden the moment sway takes the display, and nothing used to fill the minutes until Steam's or the setup screen's window:
 * the panel was black. This draws THE SAME SCREEN (os/splash/scene.h is shared, so they cannot drift apart) as a layer-shell overlay: the badge, "WispOS", the round-dot spinner, the
 * current stage, a key-pill hint, and on SELECT the live boot log (journalctl) or, after a failure, the red "!" screen. It takes status lines from its own FIFO like the splash does
 * (wisp-splash-say writes to both), and it ENDS the moment any window is announced by the compositor's foreign-toplevel protocol: the Steam client, the setup screen, anything.
 *
 * It must never be able to cover the real UI for good, so every way out is covered: a window appearing, `done`/`quit` on the FIFO, SIGTERM, the layer surface being closed, the
 * compositor going away, a ceiling (--max-seconds, 20 minutes by default), and a compositor without the foreign-toplevel protocol (then 90 seconds, because nothing says when to stop).
 * `wisp.splash=off` on the kernel command line turns it off like it turns off the splash. It draws at the output's native pixel size (buffer scale = the output scale: sway runs the
 * panel at scale 2 in Game mode) and sits centred if the output is larger than 720x480.
 *
 *   wisp-bootscreen [--fifo /run/wisp-bootscreen.fifo] [--splash-fifo /dev/wisp-splash.fifo] [--input-dir /dev/input] [--no-input] [--toggle-key 314] [--status TEXT] [--detail TEXT]
 *                   [--title TEXT] [--hint-key TEXT] [--hint-label TEXT] [--log-file FILE] [--cmdline /proc/cmdline] [--wait SECONDS] [--max-seconds N] [--slow-after N] [--fps N]
 *                   [--keep-on-window] [--dev]
 * Environment: WAYLAND_DISPLAY and XDG_RUNTIME_DIR as for any client; NOTIFY_SOCKET (systemd Type=notify: READY=1 once the first frame is on the screen).
 * Test seams: --log-file (a text file used instead of journalctl), --keep-on-window (do not end on a window: the tests take screenshots), --splash-fifo, --cmdline. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/timerfd.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>

#include "wlr-foreign-toplevel-management-unstable-v1-client.h"
#include "wlr-layer-shell-unstable-v1-client.h"
#include "scene.h"
#include "evinput.h"

typedef struct { struct wl_buffer *b; uint32_t *px; size_t size; int w, h; bool busy, stale; } Buf;

static struct {
    struct wl_display *dpy;
    struct wl_registry *reg;
    struct wl_compositor *comp;
    struct wl_shm *shm;
    struct zwlr_layer_shell_v1 *lshell;
    struct zwlr_foreign_toplevel_manager_v1 *ftm;
    struct wl_output *out;
    struct wl_surface *surf;
    struct zwlr_layer_surface_v1 *ls;
    struct wl_callback *frame;
    int lw, lh, scale;                   /* the layer surface's logical size; the output's scale (1 until it says) */
    bool configured, frame_pending, closed, running, first_frame, rescale;
    Buf buf[2];
    int ox, oy;                          /* where the 720x480 scene sits in the buffer */
    int ntop, spin;
    struct screen scr;
    enum { M_SPLASH, M_LOG } mode;
    char loglines[60][MAXLINE];
    int nlog;
    pid_t logpid;
    int logfd;
    char logbuf[2 * MAXLINE];
    size_t loglen;
    const char *logfile, *splash_fifo;
    int fps;
} S = { .scale = 1, .running = true, .logfd = -1, .logpid = -1 };

static void note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void note(const char *fmt, ...)
{
    va_list ap; va_start(ap, fmt);
    fputs("wisp-bootscreen: ", stderr); vfprintf(stderr, fmt, ap); fputc('\n', stderr);
    va_end(ap);
}

static volatile sig_atomic_t stop_flag;
static void on_signal(int sig) { (void)sig; stop_flag = 1; }

/* ------------------------------------------------------------------------- buffers */
static void buf_release(void *d, struct wl_buffer *b) { (void)b; ((Buf *)d)->busy = false; }
static const struct wl_buffer_listener buf_listener = { buf_release };

static void buf_free(Buf *b)
{
    if (b->b) wl_buffer_destroy(b->b);
    if (b->px) munmap(b->px, b->size);
    memset(b, 0, sizeof *b);
}

static bool buf_alloc(Buf *b, int w, int h)
{
    size_t size = (size_t)w * (size_t)h * 4;
    int fd = memfd_create("wisp-bootscreen", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, (off_t)size) != 0) { if (fd >= 0) close(fd); return false; }
    void *px = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (px == MAP_FAILED) { close(fd); return false; }
    struct wl_shm_pool *pool = wl_shm_create_pool(S.shm, fd, (int32_t)size);
    b->b = wl_shm_pool_create_buffer(pool, 0, w, h, w * 4, WL_SHM_FORMAT_XRGB8888);
    wl_shm_pool_destroy(pool);
    close(fd);
    b->px = px; b->size = size; b->w = w; b->h = h; b->busy = false; b->stale = true;
    wl_buffer_add_listener(b->b, &buf_listener, b);
    return true;
}

static void canvas_of(Buf *b, struct canvas *c)
{
    c->w = b->w < WISP_UI_W ? b->w : WISP_UI_W;
    c->h = b->h < WISP_UI_H ? b->h : WISP_UI_H;
    c->stride = b->w;
    c->px = b->px + (size_t)S.oy * (size_t)b->w + (size_t)S.ox;
}

static void draw_full(Buf *b)
{
    for (size_t i = 0; i < b->size / 4; i++) b->px[i] = WISP_UI_BOOT_BG;            /* the margins of a larger output */
    struct canvas c; canvas_of(b, &c);
    if (S.mode == M_LOG) draw_logview_lines(&c, S.loglines, S.nlog); else draw_screen(&c, &S.scr);
    b->stale = false;
}

#define SPIN_R (WISP_UI_SPIN_ROW_R + 4)
#define SPIN_BOX (SPIN_R + WISP_UI_SPIN_DOT_R + 1)

static void draw_spinner(Buf *b)
{
    struct canvas c; canvas_of(b, &c);
    spinner(&c, c.w / 2, WISP_UI_BOOT_SPIN_CY, SPIN_R, (int)((long)S.spin * WISP_UI_SPIN_SPEED / S.fps) % WISP_UI_SPIN_DOTS);
}

static void mark_stale(void) { S.buf[0].stale = S.buf[1].stale = true; }

static void frame_done(void *d, struct wl_callback *cb, uint32_t t);
static const struct wl_callback_listener frame_listener = { frame_done };
static void notify_ready(void);

static void present(void)
{
    if (!S.configured || S.closed || S.frame_pending) return;
    int pw = S.lw * S.scale, ph = S.lh * S.scale;
    if (pw <= 0 || ph <= 0) return;
    Buf *b = NULL;
    for (int i = 0; i < 2 && !b; i++) if (!S.buf[i].busy) b = &S.buf[i];
    if (!b) return;                                                                    /* both with the compositor: a release and the next tick try again */
    if (b->b && (b->w != pw || b->h != ph)) buf_free(b);
    if (!b->b && !buf_alloc(b, pw, ph)) { note("out of memory for a %dx%d buffer", pw, ph); return; }
    S.ox = pw > WISP_UI_W ? (pw - WISP_UI_W) / 2 : 0;
    S.oy = ph > WISP_UI_H ? (ph - WISP_UI_H) / 2 : 0;
    bool full = b->stale;
    if (full) draw_full(b);
    bool spin = S.mode == M_SPLASH && !S.scr.failed;
    if (spin) draw_spinner(b);
    if (S.rescale) { wl_surface_set_buffer_scale(S.surf, S.scale); S.rescale = false; }
    S.frame = wl_surface_frame(S.surf);
    wl_callback_add_listener(S.frame, &frame_listener, NULL);
    S.frame_pending = true;
    b->busy = true;
    wl_surface_attach(S.surf, b->b, 0, 0);
    if (full) wl_surface_damage_buffer(S.surf, 0, 0, b->w, b->h);
    else if (spin) wl_surface_damage_buffer(S.surf, S.ox + WISP_UI_W / 2 - SPIN_BOX, S.oy + WISP_UI_BOOT_SPIN_CY - SPIN_BOX, 2 * SPIN_BOX, 2 * SPIN_BOX);
    wl_surface_commit(S.surf);
}

static void frame_done(void *d, struct wl_callback *cb, uint32_t t)
{
    (void)d; (void)t;
    wl_callback_destroy(cb);
    if (S.frame == cb) S.frame = NULL;
    S.frame_pending = false;
    if (!S.first_frame) { S.first_frame = true; notify_ready(); }
}

/* ------------------------------------------------------------------------- layer surface, output, windows */
static void ls_configure(void *d, struct zwlr_layer_surface_v1 *ls, uint32_t serial, uint32_t w, uint32_t h)
{
    (void)d;
    zwlr_layer_surface_v1_ack_configure(ls, serial);
    S.lw = w ? (int)w : WISP_UI_W / S.scale;
    S.lh = h ? (int)h : WISP_UI_H / S.scale;
    S.configured = true;
    mark_stale();
    present();
}
static void ls_closed(void *d, struct zwlr_layer_surface_v1 *ls) { (void)d; (void)ls; note("the compositor closed the overlay"); S.closed = true; S.running = false; }
static const struct zwlr_layer_surface_v1_listener ls_listener = { ls_configure, ls_closed };

static void out_geometry(void *d, struct wl_output *o, int32_t x, int32_t y, int32_t pw, int32_t ph, int32_t sp, const char *mk, const char *md, int32_t tr)
{ (void)d; (void)o; (void)x; (void)y; (void)pw; (void)ph; (void)sp; (void)mk; (void)md; (void)tr; }
static void out_mode(void *d, struct wl_output *o, uint32_t f, int32_t w, int32_t h, int32_t r) { (void)d; (void)o; (void)f; (void)w; (void)h; (void)r; }
static void out_done(void *d, struct wl_output *o) { (void)d; (void)o; }
static void out_scale(void *d, struct wl_output *o, int32_t factor)
{
    (void)d; (void)o;
    if (factor < 1 || factor > 4 || factor == S.scale) return;
    S.scale = factor; S.rescale = true; mark_stale();
    if (S.configured) { S.lw = S.lh = 0; }                                             /* the next configure (the compositor sends one when the logical size changes) brings the new size */
}
static const struct wl_output_listener out_listener = { .geometry = out_geometry, .mode = out_mode, .done = out_done, .scale = out_scale };

static void top_title(void *d, struct zwlr_foreign_toplevel_handle_v1 *h, const char *t) { (void)d; (void)h; (void)t; }
static void top_app_id(void *d, struct zwlr_foreign_toplevel_handle_v1 *h, const char *a) { (void)d; (void)h; (void)a; }
static void top_output(void *d, struct zwlr_foreign_toplevel_handle_v1 *h, struct wl_output *o) { (void)d; (void)h; (void)o; }
static void top_state(void *d, struct zwlr_foreign_toplevel_handle_v1 *h, struct wl_array *s) { (void)d; (void)h; (void)s; }
static bool end_on_window = true;
static void top_done(void *d, struct zwlr_foreign_toplevel_handle_v1 *h)
{
    (void)d; (void)h;
    S.ntop++;
    if (end_on_window) { note("a window appeared: the boot screen ends"); S.running = false; }
}
static void top_closed(void *d, struct zwlr_foreign_toplevel_handle_v1 *h) { (void)d; zwlr_foreign_toplevel_handle_v1_destroy(h); }
static void top_parent(void *d, struct zwlr_foreign_toplevel_handle_v1 *h, struct zwlr_foreign_toplevel_handle_v1 *p) { (void)d; (void)h; (void)p; }
static const struct zwlr_foreign_toplevel_handle_v1_listener top_listener = { top_title, top_app_id, top_output, top_output, top_state, top_done, top_closed, top_parent };
static void ftm_toplevel(void *d, struct zwlr_foreign_toplevel_manager_v1 *m, struct zwlr_foreign_toplevel_handle_v1 *h) { (void)d; (void)m; zwlr_foreign_toplevel_handle_v1_add_listener(h, &top_listener, NULL); }
static void ftm_finished(void *d, struct zwlr_foreign_toplevel_manager_v1 *m) { (void)d; (void)m; }
static const struct zwlr_foreign_toplevel_manager_v1_listener ftm_listener = { ftm_toplevel, ftm_finished };

static void reg_global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t ver)
{
    (void)d;
    if (!strcmp(iface, wl_compositor_interface.name)) S.comp = wl_registry_bind(r, name, &wl_compositor_interface, ver < 4 ? ver : 4);
    else if (!strcmp(iface, wl_shm_interface.name)) S.shm = wl_registry_bind(r, name, &wl_shm_interface, 1);
    else if (!strcmp(iface, zwlr_layer_shell_v1_interface.name)) S.lshell = wl_registry_bind(r, name, &zwlr_layer_shell_v1_interface, ver < 4 ? ver : 4);
    else if (!strcmp(iface, zwlr_foreign_toplevel_manager_v1_interface.name)) {
        S.ftm = wl_registry_bind(r, name, &zwlr_foreign_toplevel_manager_v1_interface, ver < 3 ? ver : 3);
        zwlr_foreign_toplevel_manager_v1_add_listener(S.ftm, &ftm_listener, NULL);
    }
    else if (!strcmp(iface, wl_output_interface.name) && !S.out) {
        S.out = wl_registry_bind(r, name, &wl_output_interface, ver < 3 ? ver : 3);
        wl_output_add_listener(S.out, &out_listener, NULL);
    }
}
static void reg_remove(void *d, struct wl_registry *r, uint32_t name) { (void)d; (void)r; (void)name; }
static const struct wl_registry_listener reg_listener = { reg_global, reg_remove };

/* ------------------------------------------------------------------------- systemd and the framebuffer splash */
static void notify_ready(void)
{
    const char *path = getenv("NOTIFY_SOCKET");
    if (path && *path) {
        struct sockaddr_un a; memset(&a, 0, sizeof a); a.sun_family = AF_UNIX;
        size_t n = strlen(path);
        if (n < sizeof a.sun_path) {
            memcpy(a.sun_path, path, n);
            if (a.sun_path[0] == '@') a.sun_path[0] = 0;                               /* an abstract socket */
            int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
            if (fd >= 0) {
                const char *msg = "READY=1\nSTATUS=showing the boot screen";
                (void)sendto(fd, msg, strlen(msg), MSG_NOSIGNAL, (struct sockaddr *)&a, (socklen_t)(offsetof(struct sockaddr_un, sun_path) + n));
                close(fd);
            }
        }
    }
    if (S.splash_fifo) {                                                               /* the framebuffer splash behind the compositor has nothing left to do */
        int fd = open(S.splash_fifo, O_RDWR | O_NONBLOCK | O_CLOEXEC);
        if (fd >= 0) { (void)!write(fd, "ready\n", 6); close(fd); }
    }
}

/* ------------------------------------------------------------------------- the boot log */
static void log_add(const char *line)
{
    if (S.nlog == 60) { memmove(S.loglines, S.loglines + 1, sizeof S.loglines[0] * 59); S.nlog--; }
    snprintf(S.loglines[S.nlog++], MAXLINE, "%s", line);
}

static void log_stop(void)
{
    if (S.logfd >= 0) { close(S.logfd); S.logfd = -1; }
    if (S.logpid > 0) { kill(S.logpid, SIGTERM); waitpid(S.logpid, NULL, 0); S.logpid = -1; }
}

static void log_start(void)
{
    S.nlog = 0; S.loglen = 0;
    if (S.logfile) return;                                                             /* re-read on every tick instead */
    int pfd[2];
    if (pipe2(pfd, O_CLOEXEC | O_NONBLOCK) != 0) return;
    pid_t pid = fork();
    if (pid < 0) { close(pfd[0]); close(pfd[1]); return; }
    if (pid == 0) {
        dup2(pfd[1], 1); dup2(pfd[1], 2);
        execlp("journalctl", "journalctl", "-b", "-f", "-n", "50", "--no-pager", "-o", "short-monotonic", (char *)NULL);
        _exit(127);
    }
    close(pfd[1]);
    S.logfd = pfd[0]; S.logpid = pid;
}

static void log_read_file(void)
{
    FILE *f = fopen(S.logfile, "r");
    S.nlog = 0;
    if (f) { char buf[MAXLINE]; while (fgets(buf, sizeof buf, f)) { buf[strcspn(buf, "\r\n")] = 0; log_add(buf); } fclose(f); }
    mark_stale();
}

static void log_read_pipe(void)
{
    size_t have = S.loglen < sizeof S.logbuf - 1 ? S.loglen : 0;                         /* never more than the buffer holds */
    size_t room = sizeof S.logbuf - 1 - have;
    ssize_t n = read(S.logfd, S.logbuf + have, room);
    if (n <= 0) { if (n == 0 || (errno != EAGAIN && errno != EINTR)) { close(S.logfd); S.logfd = -1; } return; }
    S.loglen = have + (size_t)n; S.logbuf[S.loglen] = 0;
    char *nl;
    while ((nl = memchr(S.logbuf, '\n', S.loglen))) {
        *nl = 0; log_add(S.logbuf);
        size_t used = (size_t)(nl - S.logbuf) + 1; memmove(S.logbuf, S.logbuf + used, S.loglen - used); S.loglen -= used; S.logbuf[S.loglen] = 0;
    }
    if (S.loglen >= MAXLINE) { log_add(S.logbuf); S.loglen = 0; }                       /* a very long line is cut */
    mark_stale();
}

static void toggle_log(void)
{
    if (S.mode == M_SPLASH) { S.mode = M_LOG; log_start(); if (S.logfile) log_read_file(); }
    else { S.mode = M_SPLASH; log_stop(); }
    mark_stale();
}

/* ------------------------------------------------------------------------- main */
int main(int argc, char **argv)
{
    const char *fifopath = "/run/wisp-bootscreen.fifo", *indir = "/dev/input", *cmdline = "/proc/cmdline";
    bool no_input = false;
    int toggle_key = BTN_SELECT, wait_s = 25, max_s = 1200, slow_after = 360;
    S.fps = WISP_UI_SPIN_SPEED; S.splash_fifo = "/dev/wisp-splash.fifo";
    S.scr = (struct screen){ .title = "Starting WispOS", .status = "Starting", .hint_key = "SELECT", .hint_label = "Boot log" };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--no-input")) no_input = true;
        else if (!strcmp(a, "--keep-on-window")) end_on_window = false;
        else if (!strcmp(a, "--dev")) S.scr.dev = true;
        else if (!v) { fprintf(stderr, "wisp-bootscreen: %s needs a value\n", a); return 2; }
        else if (!strcmp(a, "--fifo")) fifopath = argv[++i];
        else if (!strcmp(a, "--splash-fifo")) S.splash_fifo = argv[++i];
        else if (!strcmp(a, "--input-dir")) indir = argv[++i];
        else if (!strcmp(a, "--cmdline")) cmdline = argv[++i];
        else if (!strcmp(a, "--log-file")) S.logfile = argv[++i];
        else if (!strcmp(a, "--status")) snprintf(S.scr.status, sizeof S.scr.status, "%s", argv[++i]);
        else if (!strcmp(a, "--title")) snprintf(S.scr.title, sizeof S.scr.title, "%s", argv[++i]);
        else if (!strcmp(a, "--detail")) snprintf(S.scr.detail, sizeof S.scr.detail, "%s", argv[++i]);
        else if (!strcmp(a, "--hint-key")) snprintf(S.scr.hint_key, sizeof S.scr.hint_key, "%s", argv[++i]);
        else if (!strcmp(a, "--hint-label")) snprintf(S.scr.hint_label, sizeof S.scr.hint_label, "%s", argv[++i]);
        else if (!strcmp(a, "--toggle-key")) toggle_key = atoi(argv[++i]);
        else if (!strcmp(a, "--wait")) wait_s = atoi(argv[++i]);
        else if (!strcmp(a, "--max-seconds")) max_s = atoi(argv[++i]);
        else if (!strcmp(a, "--slow-after")) slow_after = atoi(argv[++i]);
        else if (!strcmp(a, "--fps")) S.fps = atoi(argv[++i]);
        else { fprintf(stderr, "wisp-bootscreen: unknown argument %s\n", a); return 2; }
    }
    if (S.fps < 1 || S.fps > 30) S.fps = WISP_UI_SPIN_SPEED;

    FILE *cf = fopen(cmdline, "r");                                                      /* the same switches as the splash wrapper's */
    if (cf) {
        char line[4096] = { 0 };
        if (fgets(line, sizeof line, cf)) {
            if (strstr(line, "wisp.dev")) S.scr.dev = true;
            if (strstr(line, "wisp.splash=off")) { fclose(cf); note("wisp.splash=off: nothing to show"); notify_ready(); return 0; }
        }
        fclose(cf);
    }

    signal(SIGPIPE, SIG_IGN);
    struct sigaction sa; memset(&sa, 0, sizeof sa); sa.sa_handler = on_signal;
    sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL);

    for (int waited = 0; !(S.dpy = wl_display_connect(NULL)); waited += 250) {            /* sway needs a moment after its unit starts */
        if (waited >= wait_s * 1000) { note("no Wayland display after %d s", wait_s); return 1; }
        struct timespec ts = { 0, 250 * 1000 * 1000 }; nanosleep(&ts, NULL);
        if (stop_flag) return 0;
    }
    S.reg = wl_display_get_registry(S.dpy);
    wl_registry_add_listener(S.reg, &reg_listener, NULL);
    wl_display_roundtrip(S.dpy);
    wl_display_roundtrip(S.dpy);                                                          /* the output's scale arrives after the global is bound */
    if (!S.comp || !S.shm || !S.lshell) { note("the compositor lacks wl_compositor, wl_shm or wlr-layer-shell"); return 1; }
    if (!S.ftm && end_on_window) { note("no foreign-toplevel protocol: nothing says when to stop, so this ends after 90 s"); if (max_s > 90) max_s = 90; }

    S.surf = wl_compositor_create_surface(S.comp);
    S.ls = zwlr_layer_shell_v1_get_layer_surface(S.lshell, S.surf, NULL, ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY, "wisp-bootscreen");
    zwlr_layer_surface_v1_add_listener(S.ls, &ls_listener, NULL);
    zwlr_layer_surface_v1_set_anchor(S.ls, ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP | ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM | ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT | ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT);
    zwlr_layer_surface_v1_set_size(S.ls, 0, 0);
    zwlr_layer_surface_v1_set_exclusive_zone(S.ls, -1);                                   /* over everything, panels included, and moving nothing */
    zwlr_layer_surface_v1_set_keyboard_interactivity(S.ls, ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE);
    struct wl_region *none = wl_compositor_create_region(S.comp); wl_surface_set_input_region(S.surf, none); wl_region_destroy(none);   /* clicks go through */
    if (S.scale != 1) S.rescale = true;
    wl_surface_commit(S.surf);                                                            /* no buffer yet: the compositor answers with the size */

    mkfifo(fifopath, 0600);
    int ffd = open(fifopath, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    int keep_open = open(fifopath, O_WRONLY | O_NONBLOCK | O_CLOEXEC);                    /* a writer of our own: the FIFO never reports EOF */
    (void)keep_open;
    int tfd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
    struct itimerspec its = { { 0, 1000000000L / S.fps }, { 0, 1000000000L / S.fps } };
    timerfd_settime(tfd, 0, &its, NULL);

    time_t t0 = time(NULL), last_scan = 0, fail_at = 0;
    bool failed_shown = false, slow_shown = false;
    char inbuf[512]; size_t inlen = 0;

    while (S.running && !stop_flag) {
        if (!no_input && time(NULL) - last_scan >= 2) { scan_input(indir, toggle_key); last_scan = time(NULL); }
        while (wl_display_prepare_read(S.dpy) != 0) wl_display_dispatch_pending(S.dpy);
        if (wl_display_flush(S.dpy) < 0 && errno != EAGAIN) { wl_display_cancel_read(S.dpy); note("lost the compositor"); break; }
        struct pollfd pf[MAXDEV + 4]; int np = 0, iw = np;
        pf[np].fd = wl_display_get_fd(S.dpy); pf[np].events = POLLIN; np++;
        int ifd = np; pf[np].fd = ffd; pf[np].events = POLLIN; np++;
        int itm = np; pf[np].fd = tfd; pf[np].events = POLLIN; np++;
        int ilg = np; pf[np].fd = S.logfd; pf[np].events = POLLIN; np++;                    /* -1 is ignored by poll */
        int in0 = np; for (int i = 0; i < in_n; i++) { pf[np].fd = in_fd[i]; pf[np].events = POLLIN; np++; }
        int r = poll(pf, (nfds_t)np, 1000);
        if (r > 0 && (pf[iw].revents & POLLIN)) wl_display_read_events(S.dpy); else wl_display_cancel_read(S.dpy);
        if (wl_display_dispatch_pending(S.dpy) < 0) { note("lost the compositor"); break; }
        if (r < 0) continue;

        bool toggle = false, redraw = false;
        if (pf[ifd].revents & POLLIN) {
            ssize_t n = read(ffd, inbuf + inlen, sizeof inbuf - inlen - 1);
            if (n > 0) inlen += (size_t)n;
            char *nl; inbuf[inlen] = 0;
            while ((nl = memchr(inbuf, '\n', inlen))) {
                *nl = 0;
                char *arg = strchr(inbuf, ' '); if (arg) *arg++ = 0;
                if (!strcmp(inbuf, "status") && arg) { snprintf(S.scr.status, sizeof S.scr.status, "%s", arg); redraw = true; }
                else if (!strcmp(inbuf, "detail") && arg) { snprintf(S.scr.detail, sizeof S.scr.detail, "%s", arg); redraw = true; }
                else if (!strcmp(inbuf, "title") && arg) { snprintf(S.scr.title, sizeof S.scr.title, "%s", arg); redraw = true; }
                else if (!strcmp(inbuf, "hint") && arg) {
                    char *label = strchr(arg, ' '); if (label) *label++ = 0;
                    snprintf(S.scr.hint_key, sizeof S.scr.hint_key, "%s", arg); snprintf(S.scr.hint_label, sizeof S.scr.hint_label, "%s", label ? label : ""); redraw = true;
                }
                else if (!strcmp(inbuf, "fail") && arg) { snprintf(S.scr.reason, sizeof S.scr.reason, "%s", arg); S.scr.failed = true; failed_shown = true; fail_at = time(NULL); redraw = true; }
                else if (!strcmp(inbuf, "log") && arg) { if (!strcmp(arg, "toggle") || (!strcmp(arg, "on") && S.mode == M_SPLASH) || (!strcmp(arg, "off") && S.mode == M_LOG)) toggle = true; }
                else if (!strcmp(inbuf, "done") || !strcmp(inbuf, "quit")) S.running = false;
                size_t used = (size_t)(nl - inbuf) + 1; memmove(inbuf, inbuf + used, inlen - used); inlen -= used; inbuf[inlen] = 0;
            }
        }
        if (pf[itm].revents & POLLIN) { uint64_t ticks; if (read(tfd, &ticks, sizeof ticks) > 0) { S.spin += (int)ticks; if (S.logfile && S.mode == M_LOG) log_read_file(); } }
        if (S.logfd >= 0 && (pf[ilg].revents & (POLLIN | POLLHUP))) log_read_pipe();
        for (int i = 0; i < in_n && in0 + i < np; i++) {
            if (!(pf[in0 + i].revents & POLLIN)) continue;
            struct input_event ev;
            while (read(pf[in0 + i].fd, &ev, sizeof ev) == sizeof ev) if (ev.type == EV_KEY && ev.value == 1 && ev.code == toggle_key) toggle = true;
        }
        time_t now = time(NULL);
        if (failed_shown && S.mode == M_SPLASH && now - fail_at >= 2) { toggle = true; failed_shown = false; }          /* a failure never stays hidden */
        if (!slow_shown && slow_after > 0 && !S.scr.failed && now - t0 >= slow_after) {
            slow_shown = true; snprintf(S.scr.status, sizeof S.scr.status, "This is taking longer than usual");
            snprintf(S.scr.detail, sizeof S.scr.detail, "Press %s to see what the system is doing.", S.scr.hint_key); redraw = true;
        }
        if (max_s > 0 && now - t0 >= max_s) { note("%d s without a window: giving up the overlay", max_s); break; }
        if (toggle) toggle_log();
        if (redraw) mark_stale();
        present();
    }

    log_stop();
    unlink(fifopath);                                                                     /* nothing reads it any more: a status line must not pile up in a pipe nobody drains */
    if (S.frame) wl_callback_destroy(S.frame);
    if (S.ls) zwlr_layer_surface_v1_destroy(S.ls);
    if (S.surf) wl_surface_destroy(S.surf);
    buf_free(&S.buf[0]); buf_free(&S.buf[1]);
    if (S.dpy) { wl_display_roundtrip(S.dpy); wl_display_disconnect(S.dpy); }
    return 0;
}
