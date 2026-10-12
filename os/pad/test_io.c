/* Tests of the pad's I/O (pad-io.c) with pipes for the pad device and the virtual keyboard and mouse, a socket for the shell, and a clock the test moves by hand. What the real device
 * does differently (the ioctls that find and grab it and create the uinput devices) is in wisp-pad.c and is checked on the device and in the QEMU run.
 * Words for the virtual devices: k<code>+ / k<code>- key, rel<code>=<n> relative axis, "." the end of a frame (SYN_REPORT). */
#include "pad-io.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <linux/input.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

static int checks, failed;
static int64_t NOW;
static int64_t fake_clock(void) { return NOW; }

static void check_str(const char *what, const char *got, const char *want)
{
    checks++;
    if (strcmp(got, want) != 0) { failed++; printf("FAIL %s\n  got:  [%s]\n  want: [%s]\n", what, got, want); }
}
static void check_int(const char *what, long got, long want)
{
    checks++;
    if (got != want) { failed++; printf("FAIL %s: got %ld, want %ld\n", what, got, want); }
}
static void check_true(const char *what, int c)
{
    checks++;
    if (!c) { failed++; printf("FAIL %s\n", what); }
}

typedef struct {
    PadIo io;
    int src_w, src_r, kbd_w, kbd_r, mouse_w, mouse_r;
    int client;
    char path[256];
} Rig;

static void addr_of(struct sockaddr_un *a, const char *path)
{
    memset(a, 0, sizeof *a);
    a->sun_family = AF_UNIX;
    check_true("the socket path fits", strlen(path) < sizeof a->sun_path);
    memcpy(a->sun_path, path, strnlen(path, sizeof a->sun_path - 1));
}

static void nonblock(int fd) { fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK); }

static void rig_open(Rig *r, const PadIoRange *range, const char *dir, bool socket_too)
{
    memset(r, 0, sizeof *r);
    PadConfig c;
    pad_config_default(&c);
    padio_init(&r->io, &c, fake_clock);
    int p[2];
    check_int("pipe", pipe(p), 0); r->src_r = p[0]; r->src_w = p[1];
    check_int("pipe", pipe(p), 0); r->kbd_r = p[0]; r->kbd_w = p[1];
    check_int("pipe", pipe(p), 0); r->mouse_r = p[0]; r->mouse_w = p[1];
    nonblock(r->kbd_r); nonblock(r->mouse_r);
    check_true("source added", padio_add_source(&r->io, r->src_r, range) >= 0);
    padio_set_sinks(&r->io, r->kbd_w, r->mouse_w);
    r->client = -1;
    NOW = 100000;
    if (socket_too) {
        snprintf(r->path, sizeof r->path, "%s/pad.sock", dir);
        check_int("listen", padio_listen(&r->io, r->path), 0);
        r->client = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un a;
        addr_of(&a, r->path);
        check_int("connect", connect(r->client, (struct sockaddr *)&a, sizeof a), 0);
        nonblock(r->client);
        padio_step(&r->io, 0);                 /* accepts it */
    }
}

static void rig_close(Rig *r)
{
    padio_stop(&r->io);
    if (r->client >= 0) close(r->client);
    close(r->src_w); close(r->kbd_w); close(r->kbd_r); close(r->mouse_w); close(r->mouse_r);
    if (r->src_r >= 0 && fcntl(r->src_r, F_GETFD) >= 0) close(r->src_r);
}

static void put(Rig *r, int type, int code, int value)
{
    struct input_event e;
    memset(&e, 0, sizeof e);
    e.type = (uint16_t)type; e.code = (uint16_t)code; e.value = value;
    check_int("write event", write(r->src_w, &e, sizeof e), (long)sizeof e);
}
static void key(Rig *r, int code, int value) { put(r, EV_KEY, code, value); put(r, EV_SYN, SYN_REPORT, 0); }
static void absv(Rig *r, int code, int value) { put(r, EV_ABS, code, value); put(r, EV_SYN, SYN_REPORT, 0); }
static void step(Rig *r) { padio_step(&r->io, 0); }
static void step_at(Rig *r, int64_t t) { NOW = t; padio_step(&r->io, 0); }

static const char *words(int fd)
{
    static char buf[4096];
    struct input_event ev[256];
    buf[0] = 0;
    size_t n = 0;
    ssize_t got = read(fd, ev, sizeof ev);
    for (ssize_t i = 0; i < got / (ssize_t)sizeof ev[0]; i++) {
        const struct input_event *e = &ev[i];
        if (e->type == EV_KEY) n += (size_t)snprintf(buf + n, sizeof buf - n, "%sk%d%c", n ? " " : "", e->code, e->value ? '+' : '-');
        else if (e->type == EV_REL) n += (size_t)snprintf(buf + n, sizeof buf - n, "%srel%d=%d", n ? " " : "", e->code, e->value);
        else if (e->type == EV_SYN) n += (size_t)snprintf(buf + n, sizeof buf - n, "%s.", n ? " " : "");
        else n += (size_t)snprintf(buf + n, sizeof buf - n, "%s?%d", n ? " " : "", e->type);
    }
    return buf;
}

static const char *lines(Rig *r)
{
    static char buf[1024];
    ssize_t n = recv(r->client, buf, sizeof buf - 1, MSG_DONTWAIT);
    buf[n > 0 ? n : 0] = 0;
    for (char *p = buf; *p; p++) if (*p == '\n') *p = '|';
    return buf;
}

static void say(Rig *r, const char *text) { check_int("client write", write(r->client, text, strlen(text)), (long)strlen(text)); }

#define EXPECT_K(r, what, want) check_str(what, words((r)->kbd_r), want)
#define EXPECT_M(r, what, want) check_str(what, words((r)->mouse_r), want)

static const char *tmpdir(void)
{
    static char d[256];
    if (!d[0]) {
        const char *base = getenv("TMPDIR");
        snprintf(d, sizeof d, "%s/padio-XXXXXX", base ? base : "/tmp");
        if (!mkdtemp(d)) { perror("mkdtemp"); exit(2); }
    }
    return d;
}

/* ------------------------------------------------------------------ */
static void t_buttons(void)
{
    Rig r; rig_open(&r, NULL, NULL, false);
    key(&r, BTN_EAST, 1); step(&r);
    EXPECT_K(&r, "A down is Enter, in its own frame", "k28+ .");
    EXPECT_M(&r, "the mouse hears nothing", "");
    key(&r, BTN_EAST, 0); step(&r);
    EXPECT_K(&r, "A up", "k28- .");
    key(&r, BTN_TR, 1); step(&r);
    EXPECT_M(&r, "R1 is the left click, on the mouse", "k272+ .");
    EXPECT_K(&r, "and not on the keyboard", "");
    key(&r, BTN_TR, 0); step(&r);
    EXPECT_M(&r, "R1 up", "k272- .");
    key(&r, BTN_TL2, 1); step(&r);
    EXPECT_K(&r, "L2: Shift and Tab are two frames, Shift first", "k42+ . k15+ .");
    key(&r, BTN_TL2, 0); step(&r);
    EXPECT_K(&r, "L2 up", "k15- . k42- .");
    put(&r, EV_KEY, BTN_EAST, 2); put(&r, EV_SYN, SYN_REPORT, 0); step(&r);
    EXPECT_K(&r, "a kernel auto-repeat (value 2) is not a press", "");
    key(&r, BTN_EAST, 1); key(&r, BTN_EAST, 1); step(&r);
    EXPECT_K(&r, "a double down is one", "k28+ .");
    key(&r, BTN_EAST, 0); step(&r); words(r.kbd_r);
    rig_close(&r);
}

static void t_axis_ranges(void)
{
    PadIoRange rg;
    for (int a = 0; a < 4; a++) { rg.min[a] = 0; rg.max[a] = 255; rg.invert[a] = false; }
    Rig r; rig_open(&r, &rg, NULL, false);
    absv(&r, ABS_X, 128); step(&r);
    EXPECT_K(&r, "the middle of a 0..255 range is the middle", "");
    absv(&r, ABS_X, 0); step(&r);
    EXPECT_K(&r, "the low end is full left: a tap of Left", "k105+ . k105- .");
    absv(&r, ABS_X, 128); step(&r);
    absv(&r, ABS_X, 255); step(&r);
    EXPECT_K(&r, "the high end is full right", "k106+ . k106- .");
    absv(&r, ABS_X, 128); absv(&r, ABS_X, 400); step(&r);
    EXPECT_K(&r, "a value past the range is clamped (and still just one tap)", "k106+ . k106- .");
    absv(&r, ABS_X, 128); absv(&r, ABS_X, 2147483647); step(&r);
    EXPECT_K(&r, "an absurd value is full deflection, not an overflow", "k106+ . k106- .");
    absv(&r, ABS_X, 128); absv(&r, ABS_X, 2000000000); step(&r);
    EXPECT_K(&r, "a value that would wrap an int is still full deflection", "k106+ . k106- .");
    absv(&r, ABS_X, 128); absv(&r, ABS_X, -2147483647 - 1); step(&r);
    EXPECT_K(&r, "and an absurdly low one is full the other way", "k105+ . k105- .");
    rig_close(&r);

    for (int a = 0; a < 4; a++) rg.invert[a] = true;
    rig_open(&r, &rg, NULL, false);
    absv(&r, ABS_Y, 0); step(&r);
    EXPECT_K(&r, "inverted: the low end of Y is down", "k108+ . k108- .");
    rig_close(&r);

    for (int a = 0; a < 4; a++) { rg.min[a] = 5; rg.max[a] = 5; rg.invert[a] = false; }
    rig_open(&r, &rg, NULL, false);
    absv(&r, ABS_X, 5); absv(&r, ABS_Y, 0); absv(&r, ABS_RX, 99); step(&r);
    EXPECT_K(&r, "an axis with no range is ignored", "");
    EXPECT_M(&r, "and moves nothing", "");
    rig_close(&r);
}

static void t_hat(void)
{
    Rig r; rig_open(&r, NULL, NULL, false);
    absv(&r, ABS_HAT0X, -1); step(&r);
    EXPECT_K(&r, "a hat D-pad: left", "k105+ .");
    absv(&r, ABS_HAT0X, 1); step(&r);
    EXPECT_K(&r, "straight to right: left up, right down", "k105- . k106+ .");
    absv(&r, ABS_HAT0Y, -1); step(&r);
    EXPECT_K(&r, "up while right is held", "k103+ .");
    absv(&r, ABS_HAT0X, 0); absv(&r, ABS_HAT0Y, 0); step(&r);
    EXPECT_K(&r, "centre releases both", "k106- . k103- .");
    absv(&r, ABS_HAT0Y, 1); step(&r);
    EXPECT_K(&r, "down", "k108+ .");
    absv(&r, ABS_HAT0Y, 1); step(&r);
    EXPECT_K(&r, "the same value again is nothing", "");
    absv(&r, ABS_HAT0Y, 0); step(&r); words(r.kbd_r);
    rig_close(&r);
}

static void t_pointer(void)
{
    Rig r; rig_open(&r, NULL, NULL, false);
    absv(&r, ABS_RX, 32767); step_at(&r, 100000);
    EXPECT_M(&r, "the first step after a rest moves nothing", "");
    long sum = 0, frames = 0;
    for (int i = 1; i <= 125; i++) {
        step_at(&r, 100000 + i * 8);
        const char *w = words(r.mouse_r);
        if (w[0]) {
            int d;
            check_int("a pointer frame is rel0=<n> and a frame end", sscanf(w, "rel0=%d .", &d), 1);
            sum += d;
            frames++;
        }
    }
    check_true("about 700 pixels in a second", sum >= 695 && sum <= 705);
    check_true("in a frame at nearly every step", frames >= 120);
    absv(&r, ABS_RX, 0); step_at(&r, 101008);
    step_at(&r, 101016);
    EXPECT_M(&r, "the middle: quiet", "");
    long before = r.io.deadline;
    check_int("and no tick is wanted", before, -1);
    absv(&r, ABS_RY, -32767); step_at(&r, 102000); step_at(&r, 102008);
    EXPECT_M(&r, "up is negative Y", "rel1=-5 .");
    absv(&r, ABS_RY, 0); step_at(&r, 102016); words(r.mouse_r);
    rig_close(&r);
}

static void t_wheel(void)
{
    Rig r; rig_open(&r, NULL, NULL, false);
    key(&r, BTN_THUMBR, 1); step(&r); key(&r, BTN_THUMBR, 0); step(&r);
    absv(&r, ABS_RY, -32767); step_at(&r, 100000);
    words(r.mouse_r);
    long hi = 0, notches = 0;
    for (int i = 1; i <= 125; i++) {
        step_at(&r, 100000 + i * 8);
        const char *w = words(r.mouse_r);
        for (const char *p = w; (p = strstr(p, "rel")); p += 3) {
            int code, v;
            if (sscanf(p, "rel%d=%d", &code, &v) == 2) { if (code == REL_WHEEL_HI_RES) hi += v; if (code == REL_WHEEL) notches += v; }
        }
    }
    check_true("scroll mode: about 10 notches of hi-res units in a second", hi >= 1180 && hi <= 1220);
    check_true("with the legacy notches beside them", labs(notches - hi / 120) <= 1);
    rig_close(&r);
}

static void t_dry(void)
{
    Rig r; rig_open(&r, NULL, NULL, false);
    char path[300];
    snprintf(path, sizeof path, "%s/dry.txt", tmpdir());
    r.io.dry = fopen(path, "w+");
    check_true("dry file", r.io.dry != NULL);
    key(&r, BTN_EAST, 1); key(&r, BTN_EAST, 0); key(&r, BTN_MODE, 1); key(&r, BTN_MODE, 0); step(&r);
    EXPECT_K(&r, "nothing reaches the virtual keyboard in a dry run", "");
    EXPECT_M(&r, "nor the mouse", "");
    rewind(r.io.dry);
    char all[512] = "";
    size_t n = fread(all, 1, sizeof all - 1, r.io.dry);
    all[n] = 0;
    check_true("it is printed with the time", strstr(all, "100000 k28+\n100000 k28-\n100000 c:summon\n") != NULL);
    fclose(r.io.dry);
    r.io.dry = NULL;
    rig_close(&r);
}

static void t_socket(void)
{
    Rig r; rig_open(&r, NULL, tmpdir(), true);
    key(&r, BTN_MODE, 1); key(&r, BTN_MODE, 0); step(&r);
    check_str("a command goes to the shell as a line", lines(&r), "summon|");
    key(&r, BTN_START, 1); key(&r, BTN_START, 0); step(&r);
    check_str("menu", lines(&r), "menu|");
    key(&r, BTN_SELECT, 1); key(&r, BTN_SELECT, 0); step(&r);
    check_str("Select is free on the desktop", lines(&r), "");

    say(&r, "osk on\n"); step(&r);
    key(&r, BTN_EAST, 1); key(&r, BTN_EAST, 0); step(&r);
    check_str("with the keyboard open A is a command, not Enter", lines(&r), "osk-type|");
    EXPECT_K(&r, "no Enter key", "");
    key(&r, BTN_SELECT, 1); key(&r, BTN_SELECT, 0); step(&r);
    check_str("Select hides the keyboard", lines(&r), "osk-hide|");
    say(&r, "key 30\nkey 30 shift\n"); step(&r);
    EXPECT_K(&r, "typing: a, then a capital with Shift around it", "k30+ . k30- . k42+ . k30+ . k30- . k42- .");
    say(&r, "osk off\n"); step(&r);
    key(&r, BTN_EAST, 1); key(&r, BTN_EAST, 0); step(&r);
    EXPECT_K(&r, "keyboard closed: A is Enter again", "k28+ . k28- .");
    lines(&r);

    say(&r, "focus firefox\n"); step(&r);
    key(&r, BTN_SOUTH, 1); step(&r);
    key(&r, BTN_SOUTH, 0); step(&r);
    EXPECT_M(&r, "in a browser B is Back, a mouse side button", "k275+ . k275- .");
    say(&r, "focus Firefox-ESR\nfocus foot\n"); step(&r);
    key(&r, BTN_SOUTH, 1); key(&r, BTN_SOUTH, 0); step(&r);
    EXPECT_K(&r, "focus on a terminal: B is Escape", "k1+ . k1- .");
    say(&r, "focus firefox\nfocus\n"); step(&r);
    key(&r, BTN_SOUTH, 1); key(&r, BTN_SOUTH, 0); step(&r);
    EXPECT_K(&r, "an empty focus is the desktop", "k1+ . k1- .");

    say(&r, "key 30 ctrl\nkey 30 alt shift\nkey 30 ctrl ctrl\n"); step(&r);
    EXPECT_K(&r, "modifiers: Ctrl, then Alt and Shift in any order; a repeated one is refused", "k29+ . k30+ . k30- . k29- . k56+ . k42+ . k30+ . k30- . k42- . k56- .");
    say(&r, "key 9999\nkey\nkey x\nkey 30 bogus\nbogus\n\nosk maybe\nkey 4294967297\nkey -1\nkey 0\nkey 248\nkey 30 shiftx\n"); step(&r);
    EXPECT_K(&r, "bad commands type nothing", "");
    EXPECT_M(&r, "and do nothing", "");
    char big[400];
    memset(big, 'a', sizeof big - 2); big[sizeof big - 2] = '\n'; big[sizeof big - 1] = 0;
    say(&r, big); step(&r);
    char c;
    ssize_t dropped = recv(r.client, &c, 1, MSG_DONTWAIT);                  /* closed with data unread, so the kernel resets it */
    check_true("a line that is too long drops the client", dropped == 0 || (dropped < 0 && errno == ECONNRESET));
    rig_close(&r);
}

static void t_clients(void)
{
    Rig r; rig_open(&r, NULL, tmpdir(), true);
    int extra[PADIO_CLIENTS + 1], ok = 0;
    for (int i = 0; i < PADIO_CLIENTS + 1; i++) {
        extra[i] = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un a;
        addr_of(&a, r.path);
        if (connect(extra[i], (struct sockaddr *)&a, sizeof a) == 0) ok++;
        step(&r);
    }
    check_int("connections are accepted (the kernel queues them)", ok, PADIO_CLIENTS + 1);
    key(&r, BTN_MODE, 1); key(&r, BTN_MODE, 0); step(&r);
    int got = 0;
    char buf[64];
    for (int i = 0; i < PADIO_CLIENTS + 1; i++) {
        ssize_t n = recv(extra[i], buf, sizeof buf, MSG_DONTWAIT);
        if (n > 0) got++;
    }
    check_int("a command reaches every connected shell, no more than the limit", got, PADIO_CLIENTS - 1);   /* the first slot is the rig's own client */
    ssize_t n = recv(r.client, buf, sizeof buf, MSG_DONTWAIT);
    check_true("the rig's own client heard it too", n > 0);
    for (int i = 0; i < PADIO_CLIENTS + 1; i++) close(extra[i]);
    rig_close(&r);
}

static void t_slow_client(void)
{
    Rig r; rig_open(&r, NULL, tmpdir(), true);
    for (int i = 0; i < 5000; i++) { key(&r, BTN_MODE, 1); key(&r, BTN_MODE, 0); step(&r); }
    char buf[8192];
    long total = 0;
    ssize_t n;
    while ((n = recv(r.client, buf, sizeof buf, MSG_DONTWAIT)) > 0) total += n;
    check_true("what fitted before the shell was dropped is there", total > 0 && total < 5000L * 7);
    check_true("a shell that does not read is dropped, not waited for", n == 0 || (n < 0 && errno == ECONNRESET));
    key(&r, BTN_EAST, 1); step(&r);
    EXPECT_K(&r, "and the pad goes on working", "k28+ .");
    key(&r, BTN_EAST, 0); step(&r); words(r.kbd_r);
    rig_close(&r);
}

static void t_waits_for_the_tick(void)
{
    Rig r; rig_open(&r, NULL, NULL, false);
    absv(&r, ABS_RX, 32767); step_at(&r, 100000);
    check_int("the logic wants a tick in 8 ms", r.io.deadline, 100008);
    alarm(5);                                       /* a loop that forgets the tick would wait for ever */
    NOW = 100000;
    int rc = padio_step(&r.io, -1);
    alarm(0);
    check_int("an unbounded wait ends when the logic's tick is due", rc, 0);
    rig_close(&r);
}

static void t_source_gone(void)
{
    Rig r; rig_open(&r, NULL, NULL, false);
    key(&r, BTN_EAST, 1); key(&r, BTN_TR, 1); step(&r);
    words(r.kbd_r); words(r.mouse_r);
    close(r.src_w);                    /* the device goes away */
    r.src_w = open("/dev/null", O_WRONLY);
    step(&r);
    EXPECT_K(&r, "the last pad gone: the held key is released", "k28- .");
    EXPECT_M(&r, "and the held button", "k272- .");
    check_int("no source is left", padio_sources(&r.io), 0);
    step(&r);
    rig_close(&r);

    rig_open(&r, NULL, NULL, false);
    int p[2];
    check_int("pipe", pipe(p), 0);
    padio_add_source(&r.io, p[0], NULL);
    key(&r, BTN_EAST, 1); step(&r);
    words(r.kbd_r);
    close(p[1]);
    step(&r);
    EXPECT_K(&r, "one of two pads gone: the other's held key stays down", "");
    check_int("one source is left", padio_sources(&r.io), 1);
    close(p[0]);
    rig_close(&r);
}

static void t_stop_and_socket_files(void)
{
    char path[280];
    snprintf(path, sizeof path, "%s/files", tmpdir());
    mkdir(path, 0700);
    Rig r; rig_open(&r, NULL, path, true);
    struct stat st;
    check_true("the socket exists", stat(r.path, &st) == 0 && S_ISSOCK(st.st_mode));
    check_int("and is private", st.st_mode & 0777, 0600);
    key(&r, BTN_EAST, 1); step(&r);
    words(r.kbd_r);
    padio_stop(&r.io);
    EXPECT_K(&r, "stopping releases what is held", "k28- .");
    check_true("and removes the socket", stat(r.path, &st) != 0);
    close(r.client); r.client = -1;
    rig_close(&r);

    char stale[400];
    snprintf(stale, sizeof stale, "%s/stale.sock", path);
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a;
    addr_of(&a, stale);
    bind(fd, (struct sockaddr *)&a, sizeof a);
    close(fd);                                       /* the file stays, as after a crash */
    PadIo io;
    PadConfig c;
    pad_config_default(&c);
    padio_init(&io, &c, fake_clock);
    check_int("a socket left by a crash is replaced", padio_listen(&io, stale), 0);
    padio_stop(&io);
    check_true("and removed on stop", stat(stale, &st) != 0);

    char plain[400];
    snprintf(plain, sizeof plain, "%s/plain", path);
    FILE *f = fopen(plain, "w"); fputs("x", f); fclose(f);
    padio_init(&io, &c, fake_clock);
    check_int("a plain file in the way is an error", padio_listen(&io, plain), -1);
    check_true("and is left alone", stat(plain, &st) == 0 && S_ISREG(st.st_mode));
    char longp[400];
    memset(longp, 'x', sizeof longp - 1); longp[sizeof longp - 1] = 0; longp[0] = '/';
    check_int("a path too long for a socket is an error", padio_listen(&io, longp), -1);
    check_int("with its own errno", errno, ENAMETOOLONG);
}

static void t_sink_errors(void)
{
    Rig r; rig_open(&r, NULL, NULL, false);
    close(r.kbd_r);                                  /* nobody reads the virtual keyboard: writes fail */
    signal(13, SIG_IGN);
    key(&r, BTN_EAST, 1); step(&r);
    check_true("a failing sink is counted, once reported", r.io.sink_errors >= 1);
    key(&r, BTN_EAST, 0); step(&r);
    r.kbd_r = open("/dev/null", O_RDONLY);
    rig_close(&r);
}

int main(void)
{
    t_buttons();
    t_axis_ranges();
    t_hat();
    t_pointer();
    t_wheel();
    t_dry();
    t_socket();
    t_clients();
    t_slow_client();
    t_waits_for_the_tick();
    t_source_gone();
    t_stop_and_socket_files();
    t_sink_errors();
    printf("%d checks, %d failed\n", checks, failed);
    return failed ? 1 : 0;
}
