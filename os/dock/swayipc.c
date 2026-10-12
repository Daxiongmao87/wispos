/* The shell's piece of sway's IPC: see swayipc.h. */
#include "swayipc.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#define MAGIC "i3-ipc"

size_t ipc_frame(uint32_t type, const char *payload, unsigned char *out, size_t cap)
{
    size_t n = payload ? strlen(payload) : 0;
    if (cap < 14 + n) return 0;
    uint32_t len = (uint32_t)n;
    memcpy(out, MAGIC, 6);
    memcpy(out + 6, &len, 4);
    memcpy(out + 10, &type, 4);
    if (n) memcpy(out + 14, payload, n);
    return 14 + n;
}

bool ipc_feed(IpcBuf *b, const void *data, size_t n)
{
    if (n > sizeof b->buf - b->len) return false;
    memcpy(b->buf + b->len, data, n);
    b->len += n;
    return true;
}

int ipc_next(IpcBuf *b, uint32_t *type, char *payload, size_t cap)
{
    if (b->len >= 6 && memcmp(b->buf, MAGIC, 6) != 0) { b->len = 0; return -1; }
    if (b->len < 14) return 0;
    uint32_t len;
    memcpy(&len, b->buf + 6, 4);
    memcpy(type, b->buf + 10, 4);
    if (len > sizeof b->buf - 14) { b->len = 0; return -1; }                   /* a message this large cannot be held: the stream is not what we think */
    if (b->len < 14 + (size_t)len) return 0;
    size_t take = len < cap - 1 ? len : cap - 1;
    memcpy(payload, b->buf + 14, take);
    payload[take] = 0;
    memmove(b->buf, b->buf + 14 + len, b->len - 14 - len);
    b->len -= 14 + len;
    return 1;
}

/* ------------------------------------------------------------------ a small JSON reader: enough to find a key of an object and read a string, a number or null */
static const char *ws(const char *s) { while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') s++; return s; }

static const char *skip_string(const char *s)                                    /* at the opening quote; returns past the closing one, NULL when it never closes */
{
    for (s++; *s; s++) {
        if (*s == '\\' && s[1]) { s++; continue; }
        if (*s == '"') return s + 1;
    }
    return NULL;
}

static const char *skip_value(const char *s)
{
    s = ws(s);
    if (*s == '"') return skip_string(s);
    if (*s == '{' || *s == '[') {
        int depth = 0;
        for (; *s; s++) {
            if (*s == '"') { s = skip_string(s); if (!s) return NULL; s--; continue; }
            if (*s == '{' || *s == '[') depth++;
            else if (*s == '}' || *s == ']') { if (--depth == 0) return s + 1; }
        }
        return NULL;
    }
    while (*s && *s != ',' && *s != '}' && *s != ']') s++;                       /* a number, true, false or null */
    return s;
}

/* the value of `key` in the object that starts at s (a '{'); NULL when it has no such key */
static const char *obj_get(const char *s, const char *key)
{
    s = ws(s);
    if (*s != '{') return NULL;
    size_t klen = strlen(key);
    for (s = ws(s + 1); *s && *s != '}';) {
        if (*s != '"') return NULL;
        const char *kend = skip_string(s);
        if (!kend) return NULL;
        bool match = (size_t)(kend - s - 2) == klen && memcmp(s + 1, key, klen) == 0;
        s = ws(kend);
        if (*s != ':') return NULL;
        s = ws(s + 1);
        if (match) return s;
        s = skip_value(s);
        if (!s) return NULL;
        s = ws(s);
        if (*s == ',') s = ws(s + 1);
    }
    return NULL;
}

static bool read_string(const char *v, char *out, size_t cap)
{
    if (!v || *v != '"') return false;
    size_t n = 0;
    for (v++; *v && *v != '"'; v++) {
        char c = *v;
        if (c == '\\' && v[1]) { v++; c = *v == 'n' ? '\n' : *v == 't' ? '\t' : *v; }
        if (n + 1 < cap) out[n++] = c;
    }
    out[n] = 0;
    return true;
}

bool ipc_parse_window(const char *json, IpcWindow *w)
{
    memset(w, 0, sizeof *w);
    const char *root = ws(json);
    if (*root != '{' || !read_string(obj_get(root, "change"), w->change, sizeof w->change)) return false;
    const char *c = obj_get(root, "container");
    if (!c || *c != '{') return true;                                            /* an event with no container: only the change is known */
    w->has_container = true;
    const char *id = obj_get(c, "id");
    if (id && (isdigit((unsigned char)*id) || *id == '-')) w->id = strtol(id, NULL, 10);
    char fl[16] = "";
    if (read_string(obj_get(c, "floating"), fl, sizeof fl)) w->floating = !strcmp(fl, "auto_on") || !strcmp(fl, "user_on");
    const char *fo = obj_get(c, "focused");
    w->focused = fo && !strncmp(fo, "true", 4);
    read_string(obj_get(c, "app_id"), w->app_id, sizeof w->app_id);
    return true;
}
