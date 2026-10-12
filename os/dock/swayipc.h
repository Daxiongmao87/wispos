/* The little of sway's IPC (the i3 protocol: "i3-ipc", a length, a type, then JSON) that the shell needs: frame a message, take whole messages out of a byte stream, and read the one kind of
 * event it subscribes to (a window appeared or went) without a JSON library. Pure functions over bytes, tested in os/dock/test_swayipc.c with messages as sway sends them. */
#ifndef WISPOS_DOCK_SWAYIPC_H
#define WISPOS_DOCK_SWAYIPC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define IPC_RUN_COMMAND 0u
#define IPC_SUBSCRIBE 2u
#define IPC_EVENT_WINDOW 0x80000003u

typedef struct { unsigned char buf[16384]; size_t len; } IpcBuf;

/* A message ready to send: the 14-byte header and the payload. Returns its size, or 0 when it does not fit. */
size_t ipc_frame(uint32_t type, const char *payload, unsigned char *out, size_t cap);
/* Append what was read from the socket. Returns false when the buffer is full (the stream is then lost: close it). */
bool ipc_feed(IpcBuf *b, const void *data, size_t n);
/* Take the next whole message out. Returns 1 and fills type and payload (NUL-terminated, cut to cap-1), 0 when more bytes are needed, -1 when the stream is not IPC (the buffer is cleared). */
int ipc_next(IpcBuf *b, uint32_t *type, char *payload, size_t cap);

typedef struct { char change[16]; long id; bool floating, focused; char app_id[128]; bool has_container; } IpcWindow;
/* A window event's JSON: change ("new", "close", "focus", "title", ...), and the container's id, whether it floats, whether it had the focus and its application ID. False when it is not such an event. */
bool ipc_parse_window(const char *json, IpcWindow *w);

#endif
