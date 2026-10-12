/* The pad's I/O: see pad-io.h. */
#define _GNU_SOURCE
#include "pad-io.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <math.h>
#include <poll.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

static void note(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("wisp-pad: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

void padio_init(PadIo *io, const PadConfig *cfg, int64_t (*clock)(void))
{
    memset(io, 0, sizeof *io);
    pad_init(&io->pad, cfg);
    for (int i = 0; i < PADIO_SRC; i++) io->src[i].fd = -1;
    for (int i = 0; i < PADIO_CLIENTS; i++) io->cl[i] = -1;
    io->kbd = io->mouse = io->listen_fd = -1;
    io->clock = clock;
    io->deadline = -1;
}

int padio_add_source(PadIo *io, int fd, const PadIoRange *r)
{
    for (int i = 0; i < PADIO_SRC; i++) {
        if (io->src[i].fd >= 0) continue;
        int fl = fcntl(fd, F_GETFL);
        if (fl < 0 || fcntl(fd, F_SETFL, fl | O_NONBLOCK) < 0) return -1;
        memset(&io->src[i], 0, sizeof io->src[i]);
        io->src[i].fd = fd;
        if (r) io->src[i].r = *r;
        else for (int a = 0; a < 4; a++) { io->src[i].r.min[a] = -PAD_AXIS_MAX; io->src[i].r.max[a] = PAD_AXIS_MAX; }
        return i;
    }
    return -1;
}

int padio_sources(const PadIo *io)
{
    int n = 0;
    for (int i = 0; i < PADIO_SRC; i++) n += io->src[i].fd >= 0;
    return n;
}

void padio_set_sinks(PadIo *io, int kbd, int mouse) { io->kbd = kbd; io->mouse = mouse; }

int padio_listen(PadIo *io, const char *path)
{
    struct sockaddr_un a;
    memset(&a, 0, sizeof a);
    a.sun_family = AF_UNIX;
    if (strlen(path) >= sizeof a.sun_path) { errno = ENAMETOOLONG; return -1; }
    strcpy(a.sun_path, path);
    struct stat st;
    if (lstat(path, &st) == 0 && S_ISSOCK(st.st_mode)) unlink(path);       /* left by a previous run; anything else at that path is not ours to remove */
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    if (bind(fd, (struct sockaddr *)&a, sizeof a) < 0 || chmod(path, 0600) < 0 || listen(fd, PADIO_CLIENTS) < 0) {
        int e = errno;
        close(fd);
        errno = e;
        return -1;
    }
    io->listen_fd = fd;
    snprintf(io->sock_path, sizeof io->sock_path, "%s", path);
    return 0;
}

/* ------------------------------------------------------------------ results out */
static void add_ev(struct input_event *v, int *n, int type, int code, int value)
{
    memset(&v[*n], 0, sizeof v[0]);
    v[*n].type = (uint16_t)type;
    v[*n].code = (uint16_t)code;
    v[*n].value = value;
    (*n)++;
}

static void sink_write(PadIo *io, int fd, const struct input_event *v, int n)
{
    if (fd < 0 || n == 0) return;
    const char *p = (const char *)v;
    size_t left = (size_t)n * sizeof *v;
    while (left) {
        ssize_t w = write(fd, p, left);
        if (w < 0) {
            if (errno == EINTR) continue;
            if (io->sink_errors++ == 0) note("writing to a virtual device failed: %s", strerror(errno));
            return;
        }
        p += w;
        left -= (size_t)w;
    }
}

static void drop_client(PadIo *io, int i)
{
    if (io->cl[i] >= 0) close(io->cl[i]);
    io->cl[i] = -1;
    io->linelen[i] = 0;
}

static void to_clients(PadIo *io, const char *word)
{
    char line[40];
    int n = snprintf(line, sizeof line, "%s\n", word);
    for (int i = 0; i < PADIO_CLIENTS; i++) {
        if (io->cl[i] < 0) continue;
        if (send(io->cl[i], line, (size_t)n, MSG_NOSIGNAL | MSG_DONTWAIT) != n) {      /* gone or not reading: a command must not be delayed for it */
            note("dropping a shell connection that is not keeping up");
            drop_client(io, i);
        }
    }
}

static void flush(PadIo *io, PadOuts *o, int64_t now)
{
    struct input_event kb[PAD_OUTS * 2], ms[PAD_OUTS * 5];
    int nk = 0, nm = 0;
    for (int i = 0; i < o->n; i++) {
        const PadOut *v = &o->v[i];
        if (io->dry) {
            char w[64];
            pad_out_text(v, w, sizeof w);
            fprintf(io->dry, "%lld %s\n", (long long)now, w);
            fflush(io->dry);
            continue;
        }
        switch (v->kind) {
        case PO_KEY:
            add_ev(kb, &nk, EV_KEY, v->code, v->value);
            add_ev(kb, &nk, EV_SYN, SYN_REPORT, 0);
            break;
        case PO_BTN:
            add_ev(ms, &nm, EV_KEY, v->code, v->value);
            add_ev(ms, &nm, EV_SYN, SYN_REPORT, 0);
            break;
        case PO_REL:
            if (v->dx) add_ev(ms, &nm, EV_REL, REL_X, v->dx);
            if (v->dy) add_ev(ms, &nm, EV_REL, REL_Y, v->dy);
            add_ev(ms, &nm, EV_SYN, SYN_REPORT, 0);
            break;
        case PO_WHEEL:                                         /* a hi-res wheel also sends the whole notches, as the kernel documents */
            if (v->dy) add_ev(ms, &nm, EV_REL, REL_WHEEL_HI_RES, v->dy);
            if (v->dx) add_ev(ms, &nm, EV_REL, REL_HWHEEL_HI_RES, v->dx);
            if (v->notch_y) add_ev(ms, &nm, EV_REL, REL_WHEEL, v->notch_y);
            if (v->notch_x) add_ev(ms, &nm, EV_REL, REL_HWHEEL, v->notch_x);
            add_ev(ms, &nm, EV_SYN, SYN_REPORT, 0);
            break;
        case PO_CMD:
            to_clients(io, v->cmd);
            break;
        }
    }
    sink_write(io, io->kbd, kb, nk);
    sink_write(io, io->mouse, ms, nm);
    o->n = 0;
}

/* ------------------------------------------------------------------ events in */
static void axis_in(PadIo *io, const PadIoSrc *s, int a, int v, int64_t now, PadOuts *o)
{
    int lo = s->r.min[a], hi = s->r.max[a];
    if (hi <= lo) return;                                      /* a device that did not report a range for this axis */
    double mid = ((double)lo + hi) / 2.0, half = ((double)hi - lo) / 2.0;
    double n = ((double)v - mid) / half;
    n = n < -1.0 ? -1.0 : n > 1.0 ? 1.0 : n;
    if (s->r.invert[a]) n = -n;
    pad_axis(&io->pad, now, a, (int)lround(n * PAD_AXIS_MAX), o);
}

static void hat_in(PadIo *io, PadIoSrc *s, int which, int v, int64_t now, PadOuts *o)
{
    v = v < 0 ? -1 : v > 0 ? 1 : 0;
    int prev = s->hat[which];
    if (v == prev) return;
    s->hat[which] = v;
    int neg = which ? PAD_DUP : PAD_DLEFT, pos = which ? PAD_DDOWN : PAD_DRIGHT;
    if (prev < 0) pad_button(&io->pad, now, neg, false, o);
    if (prev > 0) pad_button(&io->pad, now, pos, false, o);
    if (v < 0) pad_button(&io->pad, now, neg, true, o);
    if (v > 0) pad_button(&io->pad, now, pos, true, o);
}

static void event_in(PadIo *io, PadIoSrc *s, const struct input_event *e, int64_t now, PadOuts *o)
{
    if (e->type == EV_KEY) {
        if (e->value == 2) return;                             /* the kernel's own auto-repeat */
        pad_button(&io->pad, now, e->code, e->value != 0, o);
    } else if (e->type == EV_ABS) {
        switch (e->code) {
        case ABS_X:  axis_in(io, s, PAD_LX, e->value, now, o); break;
        case ABS_Y:  axis_in(io, s, PAD_LY, e->value, now, o); break;
        case ABS_RX: axis_in(io, s, PAD_RX, e->value, now, o); break;
        case ABS_RY: axis_in(io, s, PAD_RY, e->value, now, o); break;
        case ABS_HAT0X: hat_in(io, s, 0, e->value, now, o); break;     /* a D-pad reported as a hat rather than as four buttons */
        case ABS_HAT0Y: hat_in(io, s, 1, e->value, now, o); break;
        default: break;
        }
    }
}

static void drop_source(PadIo *io, int i, int64_t now, PadOuts *o)
{
    io->src[i].fd = -1;
    if (padio_sources(io) == 0) { pad_release_all(&io->pad, o); flush(io, o, now); }      /* the last pad is gone: no key may stay down */
}

/* ------------------------------------------------------------------ the shell's commands */
static void relayer(PadIo *io) { pad_set_layer(&io->pad, pad_layer_for(io->focus[0] ? io->focus : NULL, io->osk)); }

/* "shift ctrl alt" in any order, each at most once */
static bool parse_mods(const char *s, unsigned *mods)
{
    *mods = 0;
    while (*s) {
        while (*s == ' ') s++;
        if (!*s) break;
        size_t n = strcspn(s, " ");
        unsigned bit = n == 5 && !strncmp(s, "shift", 5) ? PAD_MOD_SHIFT : n == 4 && !strncmp(s, "ctrl", 4) ? PAD_MOD_CTRL : n == 3 && !strncmp(s, "alt", 3) ? PAD_MOD_ALT : 0;
        if (!bit || (*mods & bit)) return false;
        *mods |= bit;
        s += n;
    }
    return true;
}

static void handle_line(PadIo *io, const char *line, PadOuts *o)
{
    if (strcmp(line, "focus") == 0 || strncmp(line, "focus ", 6) == 0) {
        snprintf(io->focus, sizeof io->focus, "%s", line[5] ? line + 6 : "");
        relayer(io);
    } else if (strcmp(line, "osk on") == 0 || strcmp(line, "osk off") == 0) {
        io->osk = line[5] == 'n';
        relayer(io);
    } else if (strncmp(line, "key ", 4) == 0) {
        char *end;
        long code = strtol(line + 4, &end, 10);
        unsigned mods;
        if (end == line + 4 || code < 1 || code > PAD_KEY_MAX || !parse_mods(end, &mods)) note("bad key command: %.60s", line);
        else pad_type(&io->pad, (int)code, mods, o);
    } else if (line[0]) {
        note("unknown command: %.60s", line);
    }
}

static void client_in(PadIo *io, int i, PadOuts *o)
{
    char buf[PADIO_LINE];
    ssize_t n = recv(io->cl[i], buf, sizeof buf, MSG_DONTWAIT);
    if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) { drop_client(io, i); return; }
    for (ssize_t k = 0; k < n; k++) {
        if (buf[k] == '\n') {
            io->line[i][io->linelen[i]] = 0;
            handle_line(io, io->line[i], o);
            io->linelen[i] = 0;
        } else if (io->linelen[i] < PADIO_LINE - 1) {
            io->line[i][io->linelen[i]++] = buf[k];
        } else {
            note("dropping a shell connection that sent a line that is too long");
            drop_client(io, i);
            return;
        }
    }
}

/* ------------------------------------------------------------------ the loop */
int padio_step(PadIo *io, int max_wait_ms)
{
    struct pollfd pf[PADIO_SRC + 1 + PADIO_CLIENTS];
    int kind[PADIO_SRC + 1 + PADIO_CLIENTS], idx[PADIO_SRC + 1 + PADIO_CLIENTS], n = 0;
    for (int i = 0; i < PADIO_SRC; i++)
        if (io->src[i].fd >= 0) { pf[n] = (struct pollfd){ io->src[i].fd, POLLIN, 0 }; kind[n] = 0; idx[n++] = i; }
    if (io->listen_fd >= 0) { pf[n] = (struct pollfd){ io->listen_fd, POLLIN, 0 }; kind[n] = 1; idx[n++] = 0; }
    for (int i = 0; i < PADIO_CLIENTS; i++)
        if (io->cl[i] >= 0) { pf[n] = (struct pollfd){ io->cl[i], POLLIN, 0 }; kind[n] = 2; idx[n++] = i; }

    int64_t now = io->clock();
    int timeout = max_wait_ms;
    if (io->deadline >= 0) {
        int64_t d = io->deadline - now;
        int t = d < 0 ? 0 : d > 1000000 ? 1000000 : (int)d;
        if (timeout < 0 || t < timeout) timeout = t;
    }
    int r = poll(pf, (nfds_t)n, timeout);
    if (r < 0 && errno != EINTR) return -1;
    now = io->clock();

    PadOuts o;
    o.n = 0;
    if (r > 0) {
        for (int k = 0; k < n; k++) {
            if (!pf[k].revents) continue;
            if (kind[k] == 0) {
                PadIoSrc *s = &io->src[idx[k]];
                struct input_event ev[32];
                for (;;) {
                    ssize_t got = read(s->fd, ev, sizeof ev);
                    if (got < 0 && errno == EINTR) continue;
                    if (got < 0 && errno == EAGAIN) break;
                    if (got <= 0) {                                  /* ENODEV: the device went away; 0: a pipe closed */
                        note("a pad device went away");
                        drop_source(io, idx[k], now, &o);
                        break;
                    }
                    for (size_t e = 0; e < (size_t)got / sizeof ev[0]; e++) event_in(io, s, &ev[e], now, &o);
                    flush(io, &o, now);
                }
            } else if (kind[k] == 1) {
                int c = accept4(io->listen_fd, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
                if (c < 0) continue;
                int slot = -1;
                for (int i = 0; i < PADIO_CLIENTS && slot < 0; i++) if (io->cl[i] < 0) slot = i;
                if (slot < 0) { note("too many shell connections"); close(c); continue; }
                io->cl[slot] = c;
                io->linelen[slot] = 0;
            } else {
                client_in(io, idx[k], &o);
                flush(io, &o, now);
            }
        }
    }
    int wake = pad_tick(&io->pad, now, &o);
    io->deadline = wake < 0 ? -1 : now + wake;
    flush(io, &o, now);
    return 0;
}

void padio_stop(PadIo *io)
{
    PadOuts o;
    o.n = 0;
    pad_release_all(&io->pad, &o);
    flush(io, &o, io->clock());
    for (int i = 0; i < PADIO_CLIENTS; i++) drop_client(io, i);
    if (io->listen_fd >= 0) close(io->listen_fd);
    io->listen_fd = -1;
    if (io->sock_path[0]) unlink(io->sock_path);
    io->sock_path[0] = 0;
}
