/* wisp-pad: the handheld's controls as a pointer and a keyboard, for desktop mode. Started by the switch to desktop (wisp-pad.service), stopped by the switch back, so in Game mode Steam
 * Input is the only thing that maps the pad. Finds the pad device(s), grabs them, and presents a virtual keyboard and a virtual mouse (uinput). The behaviour is pad-logic.c, the
 * loop and the shell socket pad-io.c; this file is only what needs the real devices.
 *
 *   wisp-pad            run
 *   wisp-pad --probe    list the pad devices it would use, with their axis ranges, and exit (the first thing to run on the device)
 *   wisp-pad --dry      read the pad but touch nothing: print what it would send (no grab, no virtual devices, no socket)
 *
 * Environment (all optional; the defaults are the locked layout): WISP_PAD_SOCK (the shell's socket, /run/wisp-pad.sock), WISP_PAD_INVERT (axes to flip: a list of lx ly rx ry),
 * WISP_PAD_SPEED (pointer pixels a second at full deflection), WISP_PAD_CURVE, WISP_PAD_DEAD, WISP_PAD_SLOW, WISP_PAD_SCROLL (wheel notches a second). */
#define _GNU_SOURCE
#include "pad-io.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define BITS (8 * sizeof(unsigned long))
#define BITSET(n) (((n) + BITS) / BITS)
#define DEFAULT_SOCK "/run/wisp-pad.sock"

static volatile sig_atomic_t stop_flag;
static void on_signal(int sig) { (void)sig; stop_flag = 1; }

static int64_t mono_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static bool bit(const unsigned long *v, int n) { return (v[n / BITS] >> (n % BITS)) & 1UL; }

/* ------------------------------------------------------------------ configuration */
static void env_float(const char *name, float *v, float lo, float hi)
{
    const char *s = getenv(name);
    if (!s || !*s) return;
    char *end;
    float f = strtof(s, &end);
    if (*end || !(f >= lo && f <= hi)) { fprintf(stderr, "wisp-pad: ignoring %s=%s (wanted %g to %g)\n", name, s, lo, hi); return; }
    *v = f;
}

static void load_config(PadConfig *c)
{
    pad_config_default(c);
    env_float("WISP_PAD_SPEED", &c->ptr_speed, 50.f, 4000.f);
    env_float("WISP_PAD_CURVE", &c->ptr_curve, 1.f, 4.f);
    env_float("WISP_PAD_DEAD", &c->dead, 0.f, 0.5f);
    env_float("WISP_PAD_SLOW", &c->slow, 0.05f, 1.f);
    env_float("WISP_PAD_SCROLL", &c->scroll_rate, 1.f, 60.f);
}

static void load_invert(bool inv[4])
{
    static const char *const names[4] = { "lx", "ly", "rx", "ry" };
    const char *s = getenv("WISP_PAD_INVERT");
    for (int a = 0; a < 4; a++) inv[a] = false;
    if (!s) return;
    char buf[64];
    snprintf(buf, sizeof buf, "%s", s);
    for (char *tok = strtok(buf, ", "); tok; tok = strtok(NULL, ", ")) {
        int found = 0;
        for (int a = 0; a < 4; a++) if (strcasecmp(tok, names[a]) == 0) { inv[a] = true; found = 1; }
        if (!found) fprintf(stderr, "wisp-pad: ignoring unknown axis \"%s\" in WISP_PAD_INVERT\n", tok);
    }
}

/* ------------------------------------------------------------------ finding the pad */
/* A pad is a device with the four face buttons' codes and a left stick, and neither a mouse's relative axes nor a keyboard's letters. The handheld's own is "H700 Gamepad". */
static bool probe_pad(int fd, char *name, size_t cap, PadIoRange *rg)
{
    unsigned long ev[BITSET(EV_MAX)] = { 0 }, keys[BITSET(KEY_MAX)] = { 0 }, abs_[BITSET(ABS_MAX)] = { 0 };
    if (ioctl(fd, EVIOCGBIT(0, sizeof ev), ev) < 0 || !bit(ev, EV_KEY) || !bit(ev, EV_ABS) || bit(ev, EV_REL)) return false;
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof keys), keys) < 0 || !bit(keys, BTN_SOUTH) || !bit(keys, BTN_EAST) || bit(keys, KEY_A)) return false;
    if (ioctl(fd, EVIOCGBIT(EV_ABS, sizeof abs_), abs_) < 0 || !bit(abs_, ABS_X) || !bit(abs_, ABS_Y)) return false;
    if (ioctl(fd, EVIOCGNAME((int)cap), name) < 0) snprintf(name, cap, "?");
    if (strncmp(name, "wisp-pad", 8) == 0) return false;                 /* our own virtual devices */
    static const int codes[4] = { ABS_X, ABS_Y, ABS_RX, ABS_RY };
    bool inv[4];
    load_invert(inv);
    for (int a = 0; a < 4; a++) {
        struct input_absinfo ai;
        rg->invert[a] = inv[a];
        if (bit(abs_, codes[a]) && ioctl(fd, EVIOCGABS(codes[a]), &ai) == 0) { rg->min[a] = ai.minimum; rg->max[a] = ai.maximum; }
        else rg->min[a] = rg->max[a] = 0;                                  /* absent: ignored by the loop */
    }
    return true;
}

typedef struct { char path[280]; } Opened;
static Opened opened[PADIO_SRC];
static int nopened;

static int scan(PadIo *io, bool probe_only, bool grab)
{
    DIR *d = opendir("/dev/input");
    if (!d) { fprintf(stderr, "wisp-pad: cannot read /dev/input: %s\n", strerror(errno)); return 0; }
    int found = 0;
    struct dirent *de;
    while ((de = readdir(d))) {
        if (strncmp(de->d_name, "event", 5) != 0) continue;
        char path[280];
        snprintf(path, sizeof path, "/dev/input/%s", de->d_name);
        bool have = false;
        for (int i = 0; i < nopened; i++) if (strcmp(opened[i].path, path) == 0) have = true;
        if (have) continue;
        int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        char name[96];
        PadIoRange rg;
        if (!probe_pad(fd, name, sizeof name, &rg)) { close(fd); continue; }
        found++;
        printf("%s \"%s\"  lx %d..%d%s  ly %d..%d%s  rx %d..%d%s  ry %d..%d%s\n", path, name,
               rg.min[0], rg.max[0], rg.invert[0] ? " inverted" : "", rg.min[1], rg.max[1], rg.invert[1] ? " inverted" : "",
               rg.min[2], rg.max[2], rg.invert[2] ? " inverted" : "", rg.min[3], rg.max[3], rg.invert[3] ? " inverted" : "");
        if (probe_only) { close(fd); continue; }
        if (grab && ioctl(fd, EVIOCGRAB, 1) < 0) fprintf(stderr, "wisp-pad: could not grab %s (%s): other programs will see the pad too\n", path, strerror(errno));
        if (padio_add_source(io, fd, &rg) < 0 || nopened >= PADIO_SRC) { fprintf(stderr, "wisp-pad: no room for %s\n", path); close(fd); continue; }
        snprintf(opened[nopened++].path, sizeof opened[0].path, "%s", path);
        fflush(stdout);
    }
    closedir(d);
    return found;
}

/* ------------------------------------------------------------------ the virtual devices */
static int uinput_create(const char *name, bool mouse)
{
    int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) fd = open("/dev/input/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) { fprintf(stderr, "wisp-pad: cannot open /dev/uinput: %s\n", strerror(errno)); return -1; }
    int ok = ioctl(fd, UI_SET_EVBIT, EV_KEY) == 0;
    if (mouse) {
        for (int c = BTN_LEFT; c <= BTN_EXTRA; c++) ok &= ioctl(fd, UI_SET_KEYBIT, c) == 0;
        ok &= ioctl(fd, UI_SET_EVBIT, EV_REL) == 0;
        static const int rel[] = { REL_X, REL_Y, REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES };
        for (size_t i = 0; i < sizeof rel / sizeof *rel; i++) ok &= ioctl(fd, UI_SET_RELBIT, rel[i]) == 0;
    } else {
        for (int c = 1; c <= PAD_KEY_MAX; c++) ok &= ioctl(fd, UI_SET_KEYBIT, c) == 0;
    }
    struct uinput_setup us;
    memset(&us, 0, sizeof us);
    us.id.bustype = BUS_VIRTUAL;
    us.id.vendor = 0;
    us.id.product = 0;
    us.id.version = 1;
    snprintf(us.name, sizeof us.name, "%s", name);
    ok &= ioctl(fd, UI_DEV_SETUP, &us) == 0;
    ok &= ioctl(fd, UI_DEV_CREATE) == 0;
    if (!ok) { fprintf(stderr, "wisp-pad: could not create \"%s\": %s\n", name, strerror(errno)); close(fd); return -1; }
    return fd;
}

static void uinput_destroy(int fd)
{
    if (fd < 0) return;
    ioctl(fd, UI_DEV_DESTROY);
    close(fd);
}

/* ------------------------------------------------------------------ main */
int main(int argc, char **argv)
{
    bool probe = false, dry = false;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--probe") == 0) probe = true;
        else if (strcmp(argv[i], "--dry") == 0) dry = true;
        else { fprintf(stderr, "usage: wisp-pad [--probe | --dry]\n"); return 2; }
    }
    PadConfig cfg;
    load_config(&cfg);
    PadIo io;
    padio_init(&io, &cfg, mono_ms);

    if (probe) return scan(&io, true, false) ? 0 : 1;

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;                                  /* no SA_RESTART: poll returns, and the flag is looked at */
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    int kbd = -1, mouse = -1;
    if (dry) {
        io.dry = stdout;
    } else {
        kbd = uinput_create("wisp-pad keyboard", false);
        mouse = uinput_create("wisp-pad mouse", true);
        if (kbd < 0 || mouse < 0) { uinput_destroy(kbd); uinput_destroy(mouse); return 1; }
        padio_set_sinks(&io, kbd, mouse);
        const char *sock = getenv("WISP_PAD_SOCK");
        if (padio_listen(&io, sock && *sock ? sock : DEFAULT_SOCK) < 0) {
            fprintf(stderr, "wisp-pad: cannot listen on %s: %s\n", sock && *sock ? sock : DEFAULT_SOCK, strerror(errno));
            uinput_destroy(kbd);
            uinput_destroy(mouse);
            return 1;
        }
    }
    if (!scan(&io, false, !dry)) fprintf(stderr, "wisp-pad: no pad device yet, looking again every 2 s\n");

    int64_t next_scan = mono_ms() + 2000;
    while (!stop_flag) {
        if (padio_step(&io, 1000) < 0) { fprintf(stderr, "wisp-pad: poll failed: %s\n", strerror(errno)); break; }
        if (padio_sources(&io) == 0) {
            nopened = 0;                                        /* every device we had is gone: a returning one is new */
            if (mono_ms() >= next_scan) { scan(&io, false, !dry); next_scan = mono_ms() + 2000; }
        }
    }
    padio_stop(&io);
    uinput_destroy(kbd);
    uinput_destroy(mouse);
    return 0;
}
