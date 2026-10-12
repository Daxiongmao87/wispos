/* wisp-splash: the WispOS boot screen. Static C, libc + libm, no SDL: it runs before Wayland exists.
 *
 * It is built from the setup screen's components (docs/WISPOS-UI-LANGUAGE.md; reference os/setup/wisp-setup.c) in the boot-time composition: a BLACK ground and one
 * centred column (operator, 2026-10-11): the badge, "WispOS", the round-dot spinner, the current stage in Body below it, a Small dim detail line, and the key-pill
 * hint at the bottom. Colours, sizes and positions come from os/ui/wisp-ui.h; typeface and badge from the generated wisp-ui-assets.h (os/ui/gen_assets.py, rendered
 * from the files the setup screen loads). A failure is the setup's failure treatment: a red "!" disc, the text, the reason in Small dim.
 *
 * Commands arrive as lines on a FIFO:  status TEXT | detail TEXT | title TEXT | hint KEY LABEL | fail REASON | log on|off|toggle | ready | quit
 * The boot log is the real console: the splash puts the virtual terminal in KD_GRAPHICS (fbcon stops drawing but keeps filling its buffer) and switches it
 * back to KD_TEXT when the toggle key is pressed; pressing it again returns to the splash. A `fail` stops the spinner and opens the log by itself after 2 s.
 * Test seams (host tests, no device): --fb FILE --geom WxH (a regular file as framebuffer), --no-vt, --no-input, --log-file FILE (a text view drawn by the
 * program itself, used instead of the console), --frames N (exit after N frames), --fps N.
 *
 *   wisp-splash [--fb /dev/fb0] [--geom WxH] [--tty /dev/tty0] [--no-vt] [--input-dir /dev/input] [--no-input] [--toggle-key 314] [--fifo /run/wisp-splash.fifo]
 *               [--status TEXT] [--title TEXT] [--detail TEXT] [--hint-key TEXT] [--hint-label TEXT] [--dev] [--log-file FILE]
 *               [--reveal-after SECONDS] [--frames N] [--frame0 N] [--fps N] [--daemon]
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <linux/kd.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "wisp-ui.h"
#include "wisp-ui-assets.h"

#include "scene.h"

/* ------------------------------------------------------------------------- output device */
static volatile sig_atomic_t stop_flag;
static void on_signal(int sig) { (void)sig; stop_flag = 1; }

static int open_fb(const char *path, const char *geom, struct canvas *c, size_t *mapsize)
{
    int fd = open(path, O_RDWR);
    if (fd < 0) { perror(path); return -1; }
    struct stat st;
    if (fstat(fd, &st) < 0) { perror("fstat"); return -1; }
    int w = 0, h = 0, stride_bytes = 0;
    if (S_ISCHR(st.st_mode)) {
        struct fb_var_screeninfo v; struct fb_fix_screeninfo f;
        if (ioctl(fd, FBIOGET_VSCREENINFO, &v) < 0 || ioctl(fd, FBIOGET_FSCREENINFO, &f) < 0) { perror("fb ioctl"); return -1; }
        if (v.bits_per_pixel != 32) { fprintf(stderr, "wisp-splash: need a 32 bpp framebuffer, got %u\n", v.bits_per_pixel); return -1; }
        w = v.xres; h = v.yres; stride_bytes = f.line_length;
    } else {
        if (!geom || sscanf(geom, "%dx%d", &w, &h) != 2 || w < 64 || h < 64) { fprintf(stderr, "wisp-splash: a regular file needs --geom WxH\n"); return -1; }
        stride_bytes = w * 4;
        if (st.st_size < (off_t)stride_bytes * h && ftruncate(fd, (off_t)stride_bytes * h) < 0) { perror("ftruncate"); return -1; }
    }
    *mapsize = (size_t)stride_bytes * h;
    void *m = mmap(NULL, *mapsize, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (m == MAP_FAILED) { perror("mmap"); return -1; }
    c->px = m; c->w = w; c->h = h; c->stride = stride_bytes / 4;
    return fd;
}

#include "evinput.h"

/* ------------------------------------------------------------------------- main */
enum mode { SPLASH, LOG };

int main(int argc, char **argv)
{
    const char *fbpath = "/dev/fb0", *geom = NULL, *ttypath = "/dev/tty0", *indir = "/dev/input", *fifopath = "/run/wisp-splash.fifo", *logfile = NULL;
    bool no_vt = false, no_input = false, daemonize = false;
    int toggle_key = BTN_SELECT, max_frames = -1, fps = WISP_UI_SPIN_SPEED, reveal_after = 0, frame0 = 0;
    struct screen s = { .title = "Starting WispOS", .status = "Starting", .hint_key = "SELECT", .hint_label = "Boot log" };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--no-vt")) no_vt = true;
        else if (!strcmp(a, "--no-input")) no_input = true;
        else if (!strcmp(a, "--daemon")) daemonize = true;
        else if (!strcmp(a, "--dev")) s.dev = true;
        else if (!v) { fprintf(stderr, "wisp-splash: %s needs a value\n", a); return 2; }
        else if (!strcmp(a, "--fb")) fbpath = argv[++i];
        else if (!strcmp(a, "--geom")) geom = argv[++i];
        else if (!strcmp(a, "--tty")) ttypath = argv[++i];
        else if (!strcmp(a, "--input-dir")) indir = argv[++i];
        else if (!strcmp(a, "--fifo")) fifopath = argv[++i];
        else if (!strcmp(a, "--log-file")) logfile = argv[++i];
        else if (!strcmp(a, "--status")) snprintf(s.status, sizeof s.status, "%s", argv[++i]);
        else if (!strcmp(a, "--title")) snprintf(s.title, sizeof s.title, "%s", argv[++i]);
        else if (!strcmp(a, "--detail")) snprintf(s.detail, sizeof s.detail, "%s", argv[++i]);
        else if (!strcmp(a, "--hint-key")) snprintf(s.hint_key, sizeof s.hint_key, "%s", argv[++i]);
        else if (!strcmp(a, "--hint-label")) snprintf(s.hint_label, sizeof s.hint_label, "%s", argv[++i]);
        else if (!strcmp(a, "--toggle-key")) toggle_key = atoi(argv[++i]);
        else if (!strcmp(a, "--frames")) max_frames = atoi(argv[++i]);
        else if (!strcmp(a, "--fps")) fps = atoi(argv[++i]);
        else if (!strcmp(a, "--reveal-after")) reveal_after = atoi(argv[++i]);
        else if (!strcmp(a, "--frame0")) frame0 = atoi(argv[++i]);          /* the frame number the spinner starts from (previews and tests) */
        else { fprintf(stderr, "wisp-splash: unknown argument %s\n", a); return 2; }
    }
    if (fps < 1 || fps > 60) fps = WISP_UI_SPIN_SPEED;

    struct canvas c; size_t mapsize;
    int fbfd = open_fb(fbpath, geom, &c, &mapsize);
    if (fbfd < 0) return 1;
    if (c.w < WISP_UI_W || c.h < WISP_UI_H) fprintf(stderr, "wisp-splash: %dx%d is smaller than the %dx%d the layout is made for; drawing clipped\n", c.w, c.h, WISP_UI_W, WISP_UI_H);

    int tty = -1;
    if (!no_vt) { tty = open(ttypath, O_RDWR | O_CLOEXEC); if (tty >= 0) ioctl(tty, KDSETMODE, KD_GRAPHICS); }

    mkfifo(fifopath, 0600);
    int ffd = open(fifopath, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    int keep_open = open(fifopath, O_WRONLY | O_NONBLOCK | O_CLOEXEC);   /* a writer of our own: the FIFO never reports EOF */
    (void)keep_open;

    signal(SIGTERM, on_signal); signal(SIGINT, on_signal);
    if (daemonize) { if (fork() > 0) return 0; setsid(); }

    enum mode mode = SPLASH;
    int frame = 0;
    bool failed_shown = false;
    time_t t0 = time(NULL), last_scan = 0, fail_at = 0;
    char inbuf[512]; size_t inlen = 0;
    draw_screen(&c, &s);
    int cx = c.w / 2, cy = WISP_UI_BOOT_SPIN_CY;

    while (!stop_flag && (max_frames < 0 || frame < max_frames)) {
        if (!no_input && time(NULL) - last_scan >= 2) { scan_input(indir, toggle_key); last_scan = time(NULL); }
        struct pollfd pf[MAXDEV + 1]; int np = 0;
        if (ffd >= 0) { pf[np].fd = ffd; pf[np].events = POLLIN; np++; }
        for (int i = 0; i < in_n; i++) { pf[np].fd = in_fd[i]; pf[np].events = POLLIN; np++; }
        poll(pf, np, 1000 / fps);

        bool toggle = false, panel_dirty = false;
        for (int i = 0; i < np; i++) {
            if (!(pf[i].revents & POLLIN)) continue;
            if (pf[i].fd == ffd) {
                ssize_t n = read(ffd, inbuf + inlen, sizeof inbuf - inlen - 1);
                if (n > 0) inlen += n;
                char *nl;
                inbuf[inlen] = 0;
                while ((nl = memchr(inbuf, '\n', inlen))) {
                    *nl = 0;
                    char *arg = strchr(inbuf, ' '); if (arg) *arg++ = 0;
                    if (!strcmp(inbuf, "status") && arg) { snprintf(s.status, sizeof s.status, "%s", arg); panel_dirty = true; }
                    else if (!strcmp(inbuf, "detail") && arg) { snprintf(s.detail, sizeof s.detail, "%s", arg); panel_dirty = true; }
                    else if (!strcmp(inbuf, "title") && arg) { snprintf(s.title, sizeof s.title, "%s", arg); panel_dirty = true; }
                    else if (!strcmp(inbuf, "hint") && arg) {
                        char *label = strchr(arg, ' '); if (label) *label++ = 0;
                        snprintf(s.hint_key, sizeof s.hint_key, "%s", arg); snprintf(s.hint_label, sizeof s.hint_label, "%s", label ? label : "");
                        panel_dirty = true;
                    }
                    else if (!strcmp(inbuf, "fail") && arg) { snprintf(s.reason, sizeof s.reason, "%s", arg); s.failed = true; failed_shown = true; fail_at = time(NULL); panel_dirty = true; }
                    else if (!strcmp(inbuf, "log") && arg) { if (!strcmp(arg, "toggle") || (!strcmp(arg, "on") && mode == SPLASH) || (!strcmp(arg, "off") && mode == LOG)) toggle = true; }
                    else if (!strcmp(inbuf, "ready") || !strcmp(inbuf, "quit")) stop_flag = 1;
                    size_t used = (size_t)(nl - inbuf) + 1;
                    memmove(inbuf, inbuf + used, inlen - used); inlen -= used; inbuf[inlen] = 0;
                }
            } else {
                struct input_event ev;
                while (read(pf[i].fd, &ev, sizeof ev) == sizeof ev)
                    if (ev.type == EV_KEY && ev.value == 1 && ev.code == toggle_key) toggle = true;
            }
        }
        if (failed_shown && mode == SPLASH && time(NULL) - fail_at >= 2) { toggle = true; failed_shown = false; }   /* a failure never stays hidden */
        if (reveal_after > 0 && mode == SPLASH && time(NULL) - t0 >= reveal_after) { toggle = true; reveal_after = 0; }   /* taking too long: show the log */
        if (toggle) {
            if (mode == SPLASH) {
                mode = LOG;
                if (tty >= 0) { dprintf(tty, "\r\n[ Press %s to go back to the WispOS boot screen ]\r\n", s.hint_key); ioctl(tty, KDSETMODE, KD_TEXT); }
                else if (logfile) draw_logview(&c, logfile);
            } else { mode = SPLASH; panel_dirty = true; if (tty >= 0) ioctl(tty, KDSETMODE, KD_GRAPHICS); }
        }
        if (mode == LOG && tty < 0 && logfile) draw_logview(&c, logfile);          /* test/fallback text view follows the file */
        if (mode == SPLASH) {
            if (panel_dirty) draw_screen(&c, &s);
            if (!s.failed) spinner(&c, cx, cy, WISP_UI_SPIN_ROW_R + 4, (int)((long)(frame + frame0) * WISP_UI_SPIN_SPEED / fps) % WISP_UI_SPIN_DOTS);   /* a failed boot does not spin */
        }
        frame++;
    }
    if (tty >= 0) { ioctl(tty, KDSETMODE, KD_TEXT); close(tty); }
    msync(c.px, mapsize, MS_SYNC);
    return 0;
}
