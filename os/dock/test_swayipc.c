/* Tests of the shell's piece of sway IPC (swayipc.c): framing, taking messages out of a stream in any chunking, and reading window events as sway writes them. */
#include "swayipc.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int checks, failed;
static void check_true(const char *w, int ok) { checks++; if (!ok) { failed++; printf("FAIL %s\n", w); } }
static void check_int(const char *w, long g, long x) { checks++; if (g != x) { failed++; printf("FAIL %s: got %ld, want %ld\n", w, g, x); } }
static void check_str(const char *w, const char *g, const char *x) { checks++; if (strcmp(g, x)) { failed++; printf("FAIL %s\n  got [%s]\n  want [%s]\n", w, g, x); } }

static const char NEW_TILED[] = "{\"change\":\"new\",\"container\":{\"id\":4,\"type\":\"con\",\"orientation\":\"none\",\"percent\":1.0,\"urgent\":false,\"marks\":[],\"focused\":true,\"layout\":\"none\","
    "\"border\":\"none\",\"current_border_width\":0,\"rect\":{\"x\":0,\"y\":0,\"width\":720,\"height\":436},\"deco_rect\":{\"x\":0,\"y\":0,\"width\":0,\"height\":0},"
    "\"window_rect\":{\"x\":0,\"y\":0,\"width\":720,\"height\":436},\"geometry\":{\"x\":0,\"y\":0,\"width\":0,\"height\":0},\"name\":\"win \\\"terminal\\\"\",\"window\":null,"
    "\"nodes\":[],\"floating_nodes\":[],\"focus\":[],\"fullscreen_mode\":0,\"sticky\":false,\"floating\":\"auto_off\",\"pid\":1234,\"app_id\":\"terminal\",\"visible\":true,"
    "\"max_render_time\":0,\"shell\":\"xdg_shell\",\"inhibit_idle\":false,\"idle_inhibitors\":{\"application\":\"none\",\"user\":\"none\"}}}";

static void t_frame(void)
{
    unsigned char out[200];
    size_t n = ipc_frame(IPC_SUBSCRIBE, "[\"window\"]", out, sizeof out);
    check_int("a frame is the header and the payload", (long)n, 14 + 10);
    check_true("it starts with the magic", memcmp(out, "i3-ipc", 6) == 0);
    uint32_t len, type;
    memcpy(&len, out + 6, 4); memcpy(&type, out + 10, 4);
    check_int("the length", len, 10);
    check_int("the type", type, IPC_SUBSCRIBE);
    check_int("no payload", (long)ipc_frame(IPC_RUN_COMMAND, NULL, out, sizeof out), 14);
    check_int("too small for it", (long)ipc_frame(IPC_RUN_COMMAND, "abcdef", out, 18), 0);
    check_int("exactly big enough", (long)ipc_frame(IPC_RUN_COMMAND, "abcd", out, 18), 18);
}

static void t_stream(void)
{
    unsigned char msg[400];
    size_t n1 = ipc_frame(IPC_EVENT_WINDOW, "{\"change\":\"new\"}", msg, sizeof msg);
    size_t n2 = ipc_frame(0, "[{\"success\":true}]", msg + n1, sizeof msg - n1);
    IpcBuf *b = calloc(1, sizeof *b);
    char payload[100];
    uint32_t type;
    check_int("an empty buffer has nothing", ipc_next(b, &type, payload, sizeof payload), 0);
    for (size_t chunk = 1; chunk <= 40; chunk += 3) {                         /* the same two messages, however the bytes arrive */
        b->len = 0;
        int got = 0;
        for (size_t off = 0; off < n1 + n2; off += chunk) {
            size_t take = off + chunk > n1 + n2 ? n1 + n2 - off : chunk;
            check_true("fed", ipc_feed(b, msg + off, take));
            int r;
            while ((r = ipc_next(b, &type, payload, sizeof payload)) == 1) {
                if (got == 0) { check_int("first type", type, IPC_EVENT_WINDOW); check_str("first payload", payload, "{\"change\":\"new\"}"); }
                else { check_int("second type", type, 0); check_str("second payload", payload, "[{\"success\":true}]"); }
                got++;
            }
            check_true("no error", r >= 0);
        }
        check_int("both messages came out", got, 2);
        check_int("and nothing is left", (long)b->len, 0);
    }
    b->len = 0;
    unsigned char look[16] = "XX-ipc";
    uint32_t two = 2, zero = 0;
    memcpy(look + 6, &two, 4); memcpy(look + 10, &zero, 4); memcpy(look + 14, "ok", 2);
    ipc_feed(b, look, 16);
    check_int("a stream that looks like a message with the wrong magic is refused", ipc_next(b, &type, payload, sizeof payload), -1);
    b->len = 0;
    ipc_feed(b, "garbage that is no ipc message at all", 37);
    check_int("a stream that is not IPC is refused", ipc_next(b, &type, payload, sizeof payload), -1);
    check_int("and dropped", (long)b->len, 0);
    unsigned char big[14];
    uint32_t huge = 1u << 30;
    memcpy(big, "i3-ipc", 6); memcpy(big + 6, &huge, 4); memset(big + 10, 0, 4);
    ipc_feed(b, big, 14);
    check_int("a message too large to hold is refused", ipc_next(b, &type, payload, sizeof payload), -1);
    size_t m = ipc_frame(0, "0123456789", msg, sizeof msg);
    ipc_feed(b, msg, m);
    char small[5];
    check_int("a payload longer than the room is cut, not overrun", ipc_next(b, &type, small, sizeof small), 1);
    check_str("to what fits", small, "0123");
    check_int("and the message is consumed", (long)b->len, 0);
    char exact[1];
    ipc_feed(b, msg, m);
    check_int("room for nothing but the terminator", ipc_next(b, &type, exact, sizeof exact), 1);
    check_true("is empty", exact[0] == 0);
    uint32_t cut = 100;
    unsigned char partial[20];
    memcpy(partial, "i3-ipc", 6); memcpy(partial + 6, &cut, 4); memset(partial + 10, 0, 4); memset(partial + 14, 'x', 6);
    b->len = 0; ipc_feed(b, partial, 20);
    check_int("half a message waits", ipc_next(b, &type, payload, sizeof payload), 0);
    b->len = 0;
    check_true("the buffer has a limit", !ipc_feed(b, big, sizeof b->buf + 1));
    free(b);
}

static void t_window(void)
{
    IpcWindow w;
    check_true("a new tiled window", ipc_parse_window(NEW_TILED, &w));
    check_str("change", w.change, "new");
    check_int("id", w.id, 4);
    check_true("not floating, and it has the focus", !w.floating && w.has_container && w.focused);
    check_str("app id", w.app_id, "terminal");
    char fl[800];
    snprintf(fl, sizeof fl, "%s", NEW_TILED);
    char *p = strstr(fl, "auto_off");
    memcpy(p, "user_on", 7); memmove(p + 7, p + 8, strlen(p + 8) + 1);
    check_true("a floating window", ipc_parse_window(fl, &w) && w.floating);
    snprintf(fl, sizeof fl, "%s", NEW_TILED);
    p = strstr(fl, "auto_off");
    memcpy(p, "auto_on", 7); memmove(p + 7, p + 8, strlen(p + 8) + 1);
    check_true("a window sway floated by itself (a dialog) floats too", ipc_parse_window(fl, &w) && w.floating);
    check_true("close", ipc_parse_window("{\"change\":\"close\",\"container\":{\"id\":17,\"focused\":false,\"floating\":null,\"app_id\":null}}", &w) && !strcmp(w.change, "close") && w.id == 17 && !w.floating && !w.focused && !w.app_id[0]);
    check_true("no container", ipc_parse_window("{\"change\":\"focus\"}", &w) && !w.has_container && !strcmp(w.change, "focus"));
    check_true("whitespace and another order", ipc_parse_window(" { \"container\" : { \"floating\" : \"auto_off\" , \"id\" : 9 } , \"change\" : \"new\" } ", &w) && w.id == 9 && !strcmp(w.change, "new"));
    check_true("a nested id is not the container's", ipc_parse_window("{\"change\":\"new\",\"container\":{\"rect\":{\"id\":5},\"id\":6}}", &w) && w.id == 6);
    check_true("a key inside a string is not a key", ipc_parse_window("{\"change\":\"new\",\"container\":{\"name\":\"\\\"id\\\": 99\",\"id\":7}}", &w) && w.id == 7);
    check_true("an escaped quote in an earlier value", ipc_parse_window("{\"x\":\"a\\\"b\\\\\",\"change\":\"title\",\"container\":{\"id\":8}}", &w) && !strcmp(w.change, "title") && w.id == 8);
    check_true("not an event", !ipc_parse_window("[{\"success\":true}]", &w));
    check_true("no change", !ipc_parse_window("{\"container\":{\"id\":1}}", &w));
    check_true("garbage", !ipc_parse_window("", &w) && !ipc_parse_window("{", &w) && !ipc_parse_window("{\"change\"", &w) && !ipc_parse_window("{\"change\":", &w) && !ipc_parse_window("not json", &w));
    check_true("a negative id", ipc_parse_window("{\"change\":\"new\",\"container\":{\"id\":-3}}", &w) && w.id == -3);
    check_true("a long application ID is cut", ipc_parse_window("{\"change\":\"new\",\"container\":{\"id\":1,\"app_id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}}", &w)
               && strlen(w.app_id) == 127);
    check_true("a very long change is cut", ipc_parse_window("{\"change\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}", &w) && strlen(w.change) == 15);
}

static unsigned long long rng = 88172645463325252ULL;
static unsigned rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (unsigned)(rng >> 11); }

static void t_fuzz(void)
{
    for (int i = 0; i < 100000; i++) {
        char s[700];
        snprintf(s, sizeof s, "%s", NEW_TILED);
        size_t len = strlen(s);
        for (int k = 0; k < 8; k++) s[rnd() % len] = (char)(rnd() % 128);
        if (rnd() % 3 == 0) s[rnd() % len] = 0;
        IpcWindow w;
        ipc_parse_window(s, &w);
    }
    IpcBuf *b = calloc(1, sizeof *b);
    for (int i = 0; i < 20000; i++) {
        unsigned char junk[64];
        size_t n = rnd() % 64;
        for (size_t k = 0; k < n; k++) junk[k] = (unsigned char)rnd();
        if (rnd() % 2) { size_t m = ipc_frame(IPC_EVENT_WINDOW, "{\"change\":\"new\"}", junk, sizeof junk); n = m; }
        ipc_feed(b, junk, n);
        char payload[40];
        uint32_t type;
        int r;
        while ((r = ipc_next(b, &type, payload, sizeof payload)) == 1) {}
        if (b->len > 4000) b->len = 0;
    }
    free(b);
    check_true("random JSON and random bytes went through without a fault", 1);
}

int main(void)
{
    t_frame(); t_stream(); t_window(); t_fuzz();
    printf("%d checks, %d failed\n", checks, failed);
    return failed ? 1 : 0;
}
