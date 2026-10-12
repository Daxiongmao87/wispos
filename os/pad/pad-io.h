/* The pad's I/O: reads input events from the pad devices, runs them through the logic (pad-logic.h), writes the results to a virtual keyboard and a virtual mouse, and talks to the
 * shell over a Unix socket. Everything here works on file descriptors and a clock handed in, so the tests (os/pad/test_io.c) drive it with pipes and a scripted clock; the parts that
 * need the real device (finding it, grabbing it, creating the uinput devices) are in wisp-pad.c.
 *
 * The control socket is line based text, one command a line, both ways.
 *   from the shell:   focus <app_id>      the focused window's application ID (empty: none); decides the browser layer
 *                     osk on | osk off    the on-screen keyboard has / has not the pad
 *                     key <code> [shift] [ctrl] [alt]   type a key (evdev code) through the virtual keyboard, with those modifiers held
 *   to the shell:     the commands of PadOut (menu, summon, osk, actions, scroll-on, scroll-off, nav-up ... nav-right, osk-type, osk-del, osk-shift, osk-space, osk-enter, osk-hide) */
#ifndef WISPOS_PAD_IO_H
#define WISPOS_PAD_IO_H

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "pad-logic.h"

#define PADIO_SRC 8
#define PADIO_CLIENTS 4
#define PADIO_LINE 160

typedef struct { int min[4], max[4]; bool invert[4]; } PadIoRange;     /* ABS_X, ABS_Y, ABS_RX, ABS_RY of one device, and whether to flip it */

typedef struct { int fd; PadIoRange r; int hat[2]; } PadIoSrc;         /* fd -1: a free slot */

typedef struct {
    Pad pad;
    PadIoSrc src[PADIO_SRC];
    int kbd, mouse;                     /* where the results go: virtual keyboard, virtual mouse (-1: nowhere, see `dry`) */
    int listen_fd;
    char sock_path[108];
    int cl[PADIO_CLIENTS];
    char line[PADIO_CLIENTS][PADIO_LINE];
    int linelen[PADIO_CLIENTS];
    bool osk;                           /* the shell says the on-screen keyboard has the pad */
    char focus[96];                     /* the focused window's application ID */
    int64_t (*clock)(void);             /* milliseconds, monotonic */
    int64_t deadline;                   /* when the logic wants its next tick, -1 never */
    FILE *dry;                          /* when set, results are printed here instead of written to the virtual devices */
    int sink_errors;
} PadIo;

void padio_init(PadIo *io, const PadConfig *cfg, int64_t (*clock)(void));
int  padio_add_source(PadIo *io, int fd, const PadIoRange *r);       /* makes it non-blocking; returns the slot or -1 */
int  padio_sources(const PadIo *io);
void padio_set_sinks(PadIo *io, int kbd, int mouse);
int  padio_listen(PadIo *io, const char *path);                      /* 0, or -1 with errno */
/* One round: wait at most max_wait_ms (-1: until something happens or the logic's own tick), read what is there, step the logic, write the results. Returns 0, or -1 on a poll failure. */
int  padio_step(PadIo *io, int max_wait_ms);
/* Everything up, the clients and the socket closed. */
void padio_stop(PadIo *io);

#endif
