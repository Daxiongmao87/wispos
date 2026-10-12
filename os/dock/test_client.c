/* A window for the shell's integration tests (os/test_dock_shell.py): an xdg toplevel of a solid colour with an application ID, that writes what the compositor tells it about its keyboard
 * focus and keys to a log file, one line each: "enter", "leave", "key <code> <pressed|released>". Test only; it is not part of the image.
 *   test_client APP_ID TITLE LOGFILE [RRGGBB] */
#define _GNU_SOURCE
#include <fcntl.h>
#include <poll.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client.h>

#include "xdg-shell-client.h"

static struct wl_compositor *comp;
static struct wl_shm *shm;
static struct wl_seat *seat;
static struct xdg_wm_base *wm;
static struct wl_surface *surf;
static FILE *logfile;
static unsigned color = 0x2a3b4c;
static int W = 400, H = 300;

static void logline(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void logline(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(logfile, fmt, ap);
    va_end(ap);
    fputc('\n', logfile);
    fflush(logfile);
}

static void draw(void)
{
    int stride = W * 4;
    size_t size = (size_t)stride * (size_t)H;
    int fd = memfd_create("test-client", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, (off_t)size) != 0) exit(1);
    uint32_t *px = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    for (size_t i = 0; i < size / 4; i++) px[i] = 0xff000000u | color;
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, (int32_t)size);
    struct wl_buffer *b = wl_shm_pool_create_buffer(pool, 0, W, H, stride, WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    close(fd);
    wl_surface_attach(surf, b, 0, 0);
    wl_surface_damage_buffer(surf, 0, 0, W, H);
    wl_surface_commit(surf);
}

static void wm_ping(void *d, struct xdg_wm_base *w, uint32_t s) { (void)d; xdg_wm_base_pong(w, s); }
static const struct xdg_wm_base_listener wm_listener = { wm_ping };
static void xs_configure(void *d, struct xdg_surface *xs, uint32_t serial) { (void)d; xdg_surface_ack_configure(xs, serial); draw(); }
static const struct xdg_surface_listener xs_listener = { xs_configure };
static void tl_configure(void *d, struct xdg_toplevel *t, int32_t w, int32_t h, struct wl_array *s) { (void)d; (void)t; (void)s; if (w > 0 && h > 0) { W = w; H = h; } logline("size %d %d", W, H); }
static void tl_close(void *d, struct xdg_toplevel *t) { (void)d; (void)t; exit(0); }
static void tl_bounds(void *d, struct xdg_toplevel *t, int32_t w, int32_t h) { (void)d; (void)t; (void)w; (void)h; }
static void tl_caps(void *d, struct xdg_toplevel *t, struct wl_array *c) { (void)d; (void)t; (void)c; }
static const struct xdg_toplevel_listener tl_listener = { tl_configure, tl_close, tl_bounds, tl_caps };

static void k_keymap(void *d, struct wl_keyboard *k, uint32_t f, int32_t fd, uint32_t s) { (void)d; (void)k; (void)f; (void)s; close(fd); }
static void k_enter(void *d, struct wl_keyboard *k, uint32_t s, struct wl_surface *sf, struct wl_array *keys) { (void)d; (void)k; (void)s; (void)sf; (void)keys; logline("enter"); }
static void k_leave(void *d, struct wl_keyboard *k, uint32_t s, struct wl_surface *sf) { (void)d; (void)k; (void)s; (void)sf; logline("leave"); }
static void k_key(void *d, struct wl_keyboard *k, uint32_t s, uint32_t t, uint32_t key, uint32_t st) { (void)d; (void)k; (void)s; (void)t; logline("key %d %s", (int)key, st ? "pressed" : "released"); }
static void k_mods(void *d, struct wl_keyboard *k, uint32_t s, uint32_t a, uint32_t b, uint32_t c, uint32_t g) { (void)d; (void)k; (void)s; (void)a; (void)b; (void)c; (void)g; }
static void k_repeat(void *d, struct wl_keyboard *k, int32_t r, int32_t dl) { (void)d; (void)k; (void)r; (void)dl; }
static const struct wl_keyboard_listener k_listener = { k_keymap, k_enter, k_leave, k_key, k_mods, k_repeat };
static void s_caps(void *d, struct wl_seat *s, uint32_t caps) { (void)d; if (caps & WL_SEAT_CAPABILITY_KEYBOARD) wl_keyboard_add_listener(wl_seat_get_keyboard(s), &k_listener, NULL); }
static void s_name(void *d, struct wl_seat *s, const char *n) { (void)d; (void)s; (void)n; }
static const struct wl_seat_listener s_listener = { s_caps, s_name };

static void g_add(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t ver)
{
    (void)d;
    if (!strcmp(iface, "wl_compositor")) comp = wl_registry_bind(r, name, &wl_compositor_interface, 4);
    else if (!strcmp(iface, "wl_shm")) shm = wl_registry_bind(r, name, &wl_shm_interface, 1);
    else if (!strcmp(iface, "wl_seat")) { seat = wl_registry_bind(r, name, &wl_seat_interface, ver < 5 ? ver : 5); wl_seat_add_listener(seat, &s_listener, NULL); }
    else if (!strcmp(iface, "xdg_wm_base")) { wm = wl_registry_bind(r, name, &xdg_wm_base_interface, 1); xdg_wm_base_add_listener(wm, &wm_listener, NULL); }
}
static void g_rm(void *d, struct wl_registry *r, uint32_t n) { (void)d; (void)r; (void)n; }
static const struct wl_registry_listener g_listener = { g_add, g_rm };

int main(int argc, char **argv)
{
    if (argc < 4) { fprintf(stderr, "usage: test_client APP_ID TITLE LOGFILE [RRGGBB]\n"); return 2; }
    if (argc > 4) color = (unsigned)strtoul(argv[4], NULL, 16);
    logfile = fopen(argv[3], "a");
    if (!logfile) return 1;
    struct wl_display *dpy = wl_display_connect(NULL);
    if (!dpy) { fprintf(stderr, "test_client: no display\n"); return 1; }
    struct wl_registry *reg = wl_display_get_registry(dpy);
    wl_registry_add_listener(reg, &g_listener, NULL);
    wl_display_roundtrip(dpy);
    wl_display_roundtrip(dpy);
    if (!comp || !shm || !wm) { fprintf(stderr, "test_client: missing globals\n"); return 1; }
    surf = wl_compositor_create_surface(comp);
    struct xdg_surface *xs = xdg_wm_base_get_xdg_surface(wm, surf);
    xdg_surface_add_listener(xs, &xs_listener, NULL);
    struct xdg_toplevel *tl = xdg_surface_get_toplevel(xs);
    xdg_toplevel_add_listener(tl, &tl_listener, NULL);
    xdg_toplevel_set_app_id(tl, argv[1]);
    xdg_toplevel_set_title(tl, argv[2]);
    wl_surface_commit(surf);
    while (wl_display_dispatch(dpy) != -1) {}
    return 0;
}
