/* Types and points on the compositor's seat through the virtual-keyboard and virtual-pointer protocols, for the shell's integration tests (os/test_dock_shell.py): real events, delivered to
 * whatever has the focus, exactly as the pad daemon's uinput devices would deliver them. A headless compositor has no input devices, so a seat has no keyboard or pointer until these exist;
 * the process creates both and keeps them until stdin closes. Test only.
 *   test_inject                  then lines on stdin, each answered "ok" (after "ready" at the start):
 *     key tap CODE...            each key down and up, 40 ms apart        key down CODE...   presses only        key up CODE...   releases only
 *     key shifttap CODE          Shift down, the key, Shift up (a capital)
 *     ptr move X Y               the pointer to a point of the 720 x 480 output        ptr down|up|click BUTTON        ptr wheel N (notches, positive down)
 *     quit */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>

#include "virtual-keyboard-unstable-v1-client.h"
#include "wlr-virtual-pointer-unstable-v1-client.h"

static struct wl_seat *seat;
static struct zwp_virtual_keyboard_manager_v1 *kmgr;
static struct zwlr_virtual_pointer_manager_v1 *pmgr;

static void g_add(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t ver)
{
    (void)d;
    if (!strcmp(iface, "wl_seat")) seat = wl_registry_bind(r, name, &wl_seat_interface, ver < 5 ? ver : 5);
    else if (!strcmp(iface, "zwp_virtual_keyboard_manager_v1")) kmgr = wl_registry_bind(r, name, &zwp_virtual_keyboard_manager_v1_interface, 1);
    else if (!strcmp(iface, "zwlr_virtual_pointer_manager_v1")) pmgr = wl_registry_bind(r, name, &zwlr_virtual_pointer_manager_v1_interface, 1);
}
static void g_rm(void *d, struct wl_registry *r, uint32_t n) { (void)d; (void)r; (void)n; }
static const struct wl_registry_listener g_listener = { g_add, g_rm };

static struct wl_display *dpy;

static void pause_ms(int ms)
{
    wl_display_roundtrip(dpy);
    struct timespec ts = { 0, (long)ms * 1000000L };
    nanosleep(&ts, NULL);
}

int main(void)
{
    dpy = wl_display_connect(NULL);
    if (!dpy) { fprintf(stderr, "test_inject: no display\n"); return 1; }
    struct wl_registry *reg = wl_display_get_registry(dpy);
    wl_registry_add_listener(reg, &g_listener, NULL);
    wl_display_roundtrip(dpy);
    if (!seat || !kmgr || !pmgr) { fprintf(stderr, "test_inject: the compositor lacks the virtual keyboard or pointer\n"); return 1; }
    struct zwp_virtual_keyboard_v1 *kb = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(kmgr, seat);
    struct zwlr_virtual_pointer_v1 *ptr = zwlr_virtual_pointer_manager_v1_create_virtual_pointer(pmgr, seat);

    struct xkb_context *ctx = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    struct xkb_rule_names names = { .layout = "us" };
    struct xkb_keymap *map = xkb_keymap_new_from_names(ctx, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!map) { fprintf(stderr, "test_inject: no keymap\n"); return 1; }
    char *text = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
    size_t size = strlen(text) + 1;
    int fd = memfd_create("keymap", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, (off_t)size) != 0) return 1;
    void *m = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    memcpy(m, text, size);
    zwp_virtual_keyboard_v1_keymap(kb, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fd, (uint32_t)size);
    wl_display_roundtrip(dpy);
    printf("ready\n");
    fflush(stdout);

    char line[256];
    uint32_t t = 1000;
    while (fgets(line, sizeof line, stdin)) {
        char *save, *cmd = strtok_r(line, " \n", &save), *mode = strtok_r(NULL, " \n", &save);
        if (!cmd) continue;
        if (!strcmp(cmd, "quit")) break;
        if (!strcmp(cmd, "key") && mode) {
            for (char *a = strtok_r(NULL, " \n", &save); a; a = strtok_r(NULL, " \n", &save)) {
                uint32_t code = (uint32_t)atoi(a);
                if (!strcmp(mode, "shifttap")) {
                    zwp_virtual_keyboard_v1_modifiers(kb, 1, 0, 0, 0);
                    zwp_virtual_keyboard_v1_key(kb, t += 10, 42, WL_KEYBOARD_KEY_STATE_PRESSED);
                    pause_ms(20);
                }
                if (strcmp(mode, "up")) { zwp_virtual_keyboard_v1_key(kb, t += 10, code, WL_KEYBOARD_KEY_STATE_PRESSED); pause_ms(40); }
                if (strcmp(mode, "down")) { zwp_virtual_keyboard_v1_key(kb, t += 10, code, WL_KEYBOARD_KEY_STATE_RELEASED); pause_ms(40); }
                if (!strcmp(mode, "shifttap")) {
                    zwp_virtual_keyboard_v1_key(kb, t += 10, 42, WL_KEYBOARD_KEY_STATE_RELEASED);
                    zwp_virtual_keyboard_v1_modifiers(kb, 0, 0, 0, 0);
                    pause_ms(20);
                }
            }
        } else if (!strcmp(cmd, "ptr") && mode) {
            int a = 0, b = 0;
            char *x = strtok_r(NULL, " \n", &save), *y = strtok_r(NULL, " \n", &save);
            a = x ? atoi(x) : 0;
            b = y ? atoi(y) : 0;
            t += 10;
            if (!strcmp(mode, "move")) zwlr_virtual_pointer_v1_motion_absolute(ptr, t, (uint32_t)a, (uint32_t)b, 720, 480);
            else if (!strcmp(mode, "down")) zwlr_virtual_pointer_v1_button(ptr, t, (uint32_t)a, WL_POINTER_BUTTON_STATE_PRESSED);
            else if (!strcmp(mode, "up")) zwlr_virtual_pointer_v1_button(ptr, t, (uint32_t)a, WL_POINTER_BUTTON_STATE_RELEASED);
            else if (!strcmp(mode, "click")) {
                zwlr_virtual_pointer_v1_button(ptr, t, (uint32_t)a, WL_POINTER_BUTTON_STATE_PRESSED);
                zwlr_virtual_pointer_v1_frame(ptr);
                pause_ms(30);
                zwlr_virtual_pointer_v1_button(ptr, t += 30, (uint32_t)a, WL_POINTER_BUTTON_STATE_RELEASED);
            } else if (!strcmp(mode, "wheel")) {
                zwlr_virtual_pointer_v1_axis_source(ptr, WL_POINTER_AXIS_SOURCE_WHEEL);
                zwlr_virtual_pointer_v1_axis_discrete(ptr, t, WL_POINTER_AXIS_VERTICAL_SCROLL, wl_fixed_from_int(a * 15), a);
            }
            zwlr_virtual_pointer_v1_frame(ptr);
            pause_ms(20);
        }
        wl_display_roundtrip(dpy);
        printf("ok\n");
        fflush(stdout);
    }
    zwlr_virtual_pointer_v1_destroy(ptr);
    zwp_virtual_keyboard_v1_destroy(kb);
    wl_display_roundtrip(dpy);
    wl_display_disconnect(dpy);
    return 0;
}
