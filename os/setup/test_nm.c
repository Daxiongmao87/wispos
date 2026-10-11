/* Unit tests for nm.h, built and run natively by os/test_setup.py. */
#include "nm.h"
static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)
int main(void) {
    char f[6][96];
    CHECK(nm_split_terse("*:Home:92:WPA2", f, 6) == 4 && f[0][0] == '*' && !strcmp(f[1], "Home") && !strcmp(f[3], "WPA2"), "plain split");
    CHECK(nm_split_terse(" :Cafe\\: Wi\\\\Fi:61:", f, 6) == 4 && !strcmp(f[1], "Cafe: Wi\\Fi") && !f[3][0], "escaped colon and backslash: got '%s'", f[1]);
    CHECK(nm_split_terse(":::", f, 6) == 4 && !f[1][0], "empty fields");

    Net n[16];
    const char *scan =
        "*:Home-WiFi-5G:92:WPA2\n"
        " :TP-Link_A3F2:74:WPA2 WPA3\n"
        " :Guest network:61:--\n"
        " :Home-WiFi-5G:48:WPA2\n"          /* same SSID, weaker access point: keep the strong one, stay active */
        " ::33:WPA2\n"                       /* hidden network: left out */
        " :Cafe\\: Open:20:\n"               /* empty security = open */
        "garbage line without enough fields\n";
    int c = nm_parse_list(scan, n, 16);
    CHECK(c == 4, "count %d", c);
    CHECK(!strcmp(n[0].ssid, "Home-WiFi-5G") && n[0].signal == 92 && n[0].active && n[0].secured, "strongest first, active kept");
    CHECK(!strcmp(n[1].ssid, "TP-Link_A3F2") && n[1].secured, "second");
    CHECK(!strcmp(n[2].ssid, "Guest network") && !n[2].secured, "'--' is open");
    CHECK(!strcmp(n[3].ssid, "Cafe: Open") && !n[3].secured, "escaped colon in ssid, empty security is open: '%s'", n[3].ssid);
    CHECK(nm_parse_list("", n, 16) == 0, "empty input");
    CHECK(nm_parse_list(scan, n, 2) == 2, "capacity respected");

    CHECK(!strcmp(nm_failure_reason("Error: Connection activation failed: (7) Secrets were required, but not provided."), "Wrong password."), "wrong password");
    CHECK(!strcmp(nm_failure_reason("Error: Connection activation failed: (53) 802.1X supplicant failed"), "Wrong password."), "supplicant");
    CHECK(strstr(nm_failure_reason("Error: No network with SSID 'x' found."), "in range") != NULL, "not found");
    CHECK(strstr(nm_failure_reason("Error: Timeout expired (90 seconds)"), "in time") != NULL, "timeout");
    CHECK(strstr(nm_failure_reason("something odd"), "Couldn't join") != NULL, "fallback");

    CHECK(nm_steam_reply_ok(200, "\"linuxarm64\"\n{\n\t\"version\"\t\"1\""), "real manifest");
    CHECK(nm_steam_reply_ok(200, "  \n\"linuxarm64\"\n{"), "leading whitespace");
    CHECK(!nm_steam_reply_ok(200, "<html><body>Please sign in to the hotspot</body></html>"), "captive portal page must not pass");
    CHECK(!nm_steam_reply_ok(403, "\"linuxarm64\""), "non-200");
    CHECK(!nm_steam_reply_ok(200, ""), "empty body");
    CHECK(!nm_steam_reply_ok(0, NULL), "no reply");

    char out[256]; char *echo[] = {"sh", "-c", "printf 'a b'; printf ' err' >&2; exit 3", NULL};
    CHECK(nm_run(echo, out, sizeof out, 5) == 3 && !strcmp(out, "a b err"), "run captures both streams and the status: '%s'", out);
    char *slow[] = {"sleep", "20", NULL}; time_t t0 = time(NULL);
    CHECK(nm_run(slow, out, sizeof out, 2) == -2 && time(NULL) - t0 < 6, "timeout kills the process");
    char *evil[] = {"printf", "%s", "$(touch /tmp/should-not-exist); `id`", NULL};
    CHECK(nm_run(evil, out, sizeof out, 5) == 0 && strstr(out, "$(touch") != NULL && access("/tmp/should-not-exist", F_OK) != 0, "arguments are never interpreted by a shell");
    char *missing[] = {"definitely-not-a-program", NULL};
    CHECK(nm_run(missing, out, sizeof out, 5) == 127, "missing program");
    if (!fails) printf("all nm.h tests passed\n");
        /* the Steam installer's line protocol */
    InstEvent ev;
    CHECK(nm_parse_install_line("phase download", &ev) == INST_PHASE && !strcmp(ev.text, "download"), "phase");
    CHECK(nm_parse_install_line("progress 1048576 377000000 bins_linuxarm64", &ev) == INST_PROGRESS && ev.done == 1048576 && ev.total == 377000000, "progress");
    CHECK(nm_parse_install_line("progress 5 5", &ev) == INST_PROGRESS && ev.done == ev.total, "progress with no component name");
    CHECK(nm_parse_install_line("progress 6 5", &ev) == INST_NONE, "done > total is refused");
    CHECK(nm_parse_install_line("progress -1 5", &ev) == INST_NONE && nm_parse_install_line("progress x y", &ev) == INST_NONE && nm_parse_install_line("progress 3 0", &ev) == INST_NONE, "bad numbers are refused");
    CHECK(nm_parse_install_line("error Lost the connection to Steam's servers.\r\n", &ev) == INST_ERROR && !strcmp(ev.text, "Lost the connection to Steam's servers."), "error text, trailing newline trimmed: '%s'", ev.text);
    CHECK(nm_parse_install_line("done", &ev) == INST_DONE, "done");
    CHECK(nm_parse_install_line("done and more", &ev) == INST_NONE && nm_parse_install_line("", &ev) == INST_NONE && nm_parse_install_line("Traceback (most recent call last):", &ev) == INST_NONE, "anything else is ignored");
    { char big[600]; memset(big, 'a', sizeof big - 1); memcpy(big, "error ", 6); big[sizeof big - 1] = 0;
      CHECK(nm_parse_install_line(big, &ev) == INST_ERROR && strlen(ev.text) == 150, "long error text is truncated, not overrun: %zu", strlen(ev.text)); }
    /* streaming: lines arrive as the program writes them, without a shell */
    { char *argv[] = {"/bin/sh", "-c", "echo phase download; echo progress 1 2; echo done", NULL}; pid_t pid; int fd = nm_spawn(argv, &pid);
      char buf[256] = {0}; ssize_t n = 0, k; while ((k = read(fd, buf + n, sizeof buf - 1 - n)) > 0) n += k; close(fd); int st; waitpid(pid, &st, 0);
      CHECK(strstr(buf, "phase download\nprogress 1 2\ndone\n") != NULL, "nm_spawn reads the program's output: '%s'", buf); }
    { char *argv[] = {"/nonexistent/program", NULL}; pid_t pid; int fd = nm_spawn(argv, &pid); char buf[8]; ssize_t k = fd >= 0 ? read(fd, buf, sizeof buf) : -1;
      int st = 0; if (fd >= 0) { close(fd); waitpid(pid, &st, 0); }
      CHECK(fd >= 0 && k <= 0 && WIFEXITED(st) && WEXITSTATUS(st) == 127, "a program that cannot start exits 127 and says nothing"); }
    return fails ? 1 : 0;
}
