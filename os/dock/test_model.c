/* Unit tests for model.c, built and run natively (with the address and undefined-behaviour sanitizers) by os/test_dock_model.py. Fixtures are made under $TMPDIR. */
#define _GNU_SOURCE
#include "model.h"
#include "../ui/wisp-ui.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static char root[512];

static void put(const char *rel, const char *text) {
    char path[1024], dir[1024];
    snprintf(path, sizeof path, "%s/%s", root, rel);
    snprintf(dir, sizeof dir, "%s", path);
    for (char *p = dir + strlen(root) + 1; *p; p++) if (*p == '/') { *p = 0; mkdir(dir, 0755); *p = '/'; }
    FILE *f = fopen(path, "w");
    if (!f) { printf("cannot write %s\n", path); exit(2); }
    fputs(text, f);
    fclose(f);
}

static void path_of(char *out, size_t cap, const char *rel) { snprintf(out, cap, "%s/%s", root, rel); }

static int parse(const char *text, const char *lang, const char *desktops, const char *path_env, WdApp *a) {
    WdLoadOpts o = {lang, desktops, path_env};
    return wd_desktop_parse(text, strlen(text), "t.desktop", "/x/t.desktop", &o, a);
}


static void test_parser(void) {
    WdApp a;
    CHECK(parse("[Desktop Entry]\nType=Application\nName=Firefox\nExec=firefox %u\nIcon=firefox\nTerminal=false\nStartupWMClass=Navigator\n", NULL, NULL, NULL, &a) == WD_OK, "basic");
    CHECK(!strcmp(a.name, "Firefox") && !strcmp(a.exec, "firefox %u") && !strcmp(a.icon, "firefox") && !strcmp(a.wm_class, "Navigator") && !a.terminal && !a.nodisplay, "fields");
    CHECK(!strcmp(a.id, "t.desktop") && !strcmp(a.path, "/x/t.desktop"), "id and path are copied");
    CHECK(parse("[Desktop Entry]\nName=Foo\nExec=foo\n", NULL, NULL, NULL, &a) == WD_NOT_APP, "no Type is not an application");
    CHECK(parse("[Desktop Entry]\nType=Link\nName=Foo\nExec=foo\n", NULL, NULL, NULL, &a) == WD_NOT_APP, "Type=Link");
    CHECK(parse("[Desktop Entry]\nType=Directory\nName=Foo\n", NULL, NULL, NULL, &a) == WD_NOT_APP, "Type=Directory");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=Foo\nExec=foo\nHidden=true\n", NULL, NULL, NULL, &a) == WD_HIDDEN, "Hidden=true");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=Foo\nExec=foo\nHidden=false\nNoDisplay=true\n", NULL, NULL, NULL, &a) == WD_OK && a.nodisplay, "NoDisplay stays an application");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=Foo\nExec=foo\nNoDisplay=True\n", NULL, NULL, NULL, &a) == WD_OK && !a.nodisplay, "booleans are exactly true or false");
    CHECK(parse("[Desktop Entry]\nType=Application\nExec=foo\n", NULL, NULL, NULL, &a) == WD_INCOMPLETE, "no Name");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=Foo\n", NULL, NULL, NULL, &a) == WD_INCOMPLETE, "no Exec");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=Foo\nExec=\n", NULL, NULL, NULL, &a) == WD_INCOMPLETE, "empty Exec");
    CHECK(parse("", NULL, NULL, NULL, &a) == WD_NOT_APP && parse("garbage", NULL, NULL, NULL, &a) == WD_NOT_APP, "empty and garbage");
    CHECK(wd_desktop_parse(NULL, 0, "x", "y", NULL, &a) == WD_BAD && wd_desktop_parse("a", 1, NULL, "y", NULL, &a) == WD_BAD && wd_desktop_parse("a", 1, "x", "y", NULL, NULL) == WD_BAD, "bad arguments");

    /* layout of the file: comments, blanks, spaces around =, CRLF, other groups, text before the group, duplicate keys */
    CHECK(parse("# a comment\n\n[Desktop Entry]\r\n# another\r\nType = Application\r\nName =  Spaced Name  \r\nExec=\tfoo --x\r\n\r\n[Desktop Action Other]\nName=Other\nExec=other\n", NULL, NULL, NULL, &a) == WD_OK, "CRLF, comments, spaces");
    CHECK(!strcmp(a.name, "Spaced Name") && !strcmp(a.exec, "foo --x"), "values trimmed: '%s' '%s'", a.name, a.exec);
    CHECK(parse("Name=Before\n[Desktop Entry]\nType=Application\nName=Foo\nExec=foo\n[Desktop Action X]\nName=Later\nExec=later\n", NULL, NULL, NULL, &a) == WD_OK && !strcmp(a.name, "Foo") && !strcmp(a.exec, "foo"), "only the Desktop Entry group counts");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=First\nName=Second\nExec=a\nExec=b\n", NULL, NULL, NULL, &a) == WD_OK && !strcmp(a.name, "First") && !strcmp(a.exec, "a"), "the first of a repeated key wins");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=A\nExec=a\n[Desktop Entry]\nName=B\nExec=b\n", NULL, NULL, NULL, &a) == WD_OK && !strcmp(a.name, "A"), "a second Desktop Entry group is ignored");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=No newline at the end\nExec=foo", NULL, NULL, NULL, &a) == WD_OK && !strcmp(a.exec, "foo"), "no final newline");
    CHECK(parse("[Desktop Entry]\nType=Application\nName=Foo\nExec=foo\nUnknown-Key=whatever\nX-Custom=1\nBad key=2\n=3\n", NULL, NULL, NULL, &a) == WD_OK, "unknown and odd keys are ignored");

    /* string escapes */
    CHECK(parse("[Desktop Entry]\nType=Application\nName=Foo\\sBar\\\\Baz\\tQ\nExec=foo\\sbar\nIcon=a\\;b\n", NULL, NULL, NULL, &a) == WD_OK, "escapes");
    CHECK(!strcmp(a.name, "Foo Bar\\Baz\tQ") && !strcmp(a.exec, "foo bar") && !strcmp(a.icon, "a\\;b"), "escapes undone: '%s' '%s' '%s'", a.name, a.exec, a.icon);

    /* localised Name: the specification's order lang_COUNTRY@MODIFIER, lang_COUNTRY, lang@MODIFIER, lang, default */
    const char *loc = "[Desktop Entry]\nType=Application\nExec=x\nName=Def\nName[de]=D\nName[de@euro]=DE\nName[de_DE]=DC\nName[de_DE@euro]=DCE\nName[fr]=F\nName[sr_YU]=SY\nName[sr@Latn]=SL\nName[sr]=S\n";
    CHECK(parse(loc, "de_DE.UTF-8@euro", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "DCE"), "lang_COUNTRY@MODIFIER first: %s", a.name);
    CHECK(parse(loc, "de_DE", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "DC"), "lang_COUNTRY: %s", a.name);
    CHECK(parse(loc, "de@euro", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "DE"), "lang@MODIFIER: %s", a.name);
    CHECK(parse(loc, "de_AT", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "D"), "another country falls back to lang: %s", a.name);
    CHECK(parse(loc, "de", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "D"), "lang: %s", a.name);
    CHECK(parse(loc, "fr_CA.UTF-8", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "F"), "fr_CA falls back to fr: %s", a.name);
    CHECK(parse(loc, "sr_YU@Latn", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "SY"), "the spec's own example, sr_YU first: %s", a.name);
    CHECK(parse(loc, "sr@Latn", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "SL"), "sr@Latn: %s", a.name);
    CHECK(parse(loc, "ja_JP.UTF-8", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "Def"), "no match: the default: %s", a.name);
    CHECK(parse(loc, NULL, NULL, NULL, &a) == WD_OK && !strcmp(a.name, "Def") && parse(loc, "C", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "Def") && parse(loc, "", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "Def"), "no locale, C, empty: the default");
    CHECK(parse("[Desktop Entry]\nType=Application\nExec=x\nName[de]=OnlyGerman\n", "de", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "OnlyGerman"), "a localised Name with no default (malformed) is still used when it matches");
    CHECK(parse("[Desktop Entry]\nType=Application\nExec=x\nName[de]=OnlyGerman\n", "fr", NULL, NULL, &a) == WD_INCOMPLETE, "... and is no name at all for another locale");
    CHECK(parse("[Desktop Entry]\nType=Application\nExec=x\nName[de]=D\nName=Def\n", "de", NULL, NULL, &a) == WD_OK && !strcmp(a.name, "D"), "the order of the lines does not matter");

    /* OnlyShowIn / NotShowIn against the current desktops */
    const char *head = "[Desktop Entry]\nType=Application\nName=Foo\nExec=foo\n";
    char buf[512];
#define SHOWN(extra, desktops) (snprintf(buf, sizeof buf, "%s%s", head, extra), parse(buf, NULL, desktops, NULL, &a))
    CHECK(SHOWN("", "WispOS:sway") == WD_OK, "no lists: shown");
    CHECK(SHOWN("", NULL) == WD_OK, "no lists and no desktops: shown");
    CHECK(SHOWN("OnlyShowIn=GNOME;\n", "WispOS:sway") == WD_NOT_SHOWN, "OnlyShowIn without us: not shown");
    CHECK(SHOWN("OnlyShowIn=GNOME;WispOS;\n", "WispOS:sway") == WD_OK, "OnlyShowIn with us: shown");
    CHECK(SHOWN("OnlyShowIn=sway\n", "WispOS:sway") == WD_OK, "no trailing semicolon, second desktop");
    CHECK(SHOWN("OnlyShowIn=GNOME;\n", NULL) == WD_NOT_SHOWN, "OnlyShowIn and no desktops: not shown");
    CHECK(SHOWN("NotShowIn=WispOS;\n", "WispOS:sway") == WD_NOT_SHOWN, "NotShowIn with us: not shown");
    CHECK(SHOWN("NotShowIn=GNOME;KDE;\n", "WispOS:sway") == WD_OK, "NotShowIn without us: shown");
    CHECK(SHOWN("OnlyShowIn=B;\nNotShowIn=A;\n", "A:B") == WD_NOT_SHOWN, "the first desktop named in either list decides (A, in NotShowIn)");
    CHECK(SHOWN("OnlyShowIn=B;\nNotShowIn=A;\n", "B:A") == WD_OK, "... and B first shows it");
    CHECK(SHOWN("OnlyShowIn=Foo\\;Bar;\n", "Foo") == WD_NOT_SHOWN && SHOWN("OnlyShowIn=Foo\\;Bar;\n", "Foo;Bar") == WD_OK, "an escaped semicolon belongs to the name");
#undef SHOWN

    /* TryExec */
    char bin[600], tryexec_text[1200], pathenv[700];
    put("bin/present", "#!/bin/sh\n");
    path_of(bin, sizeof bin, "bin/present"); chmod(bin, 0755);
    put("bin/notexec", "x\n");
    path_of(pathenv, sizeof pathenv, "bin");
    snprintf(tryexec_text, sizeof tryexec_text, "%sTryExec=present\n", head);
    CHECK(parse(tryexec_text, NULL, NULL, pathenv, &a) == WD_OK, "TryExec found in PATH");
    snprintf(tryexec_text, sizeof tryexec_text, "%sTryExec=absent\n", head);
    CHECK(parse(tryexec_text, NULL, NULL, pathenv, &a) == WD_NO_TRYEXEC, "TryExec not in PATH");
    snprintf(tryexec_text, sizeof tryexec_text, "%sTryExec=notexec\n", head);
    CHECK(parse(tryexec_text, NULL, NULL, pathenv, &a) == WD_NO_TRYEXEC, "TryExec not executable");
    snprintf(tryexec_text, sizeof tryexec_text, "%sTryExec=%s\n", head, bin);
    CHECK(parse(tryexec_text, NULL, NULL, "/nonexistent", &a) == WD_OK, "TryExec absolute");
    snprintf(tryexec_text, sizeof tryexec_text, "%sTryExec=absent\n", head);
    CHECK(parse(tryexec_text, NULL, NULL, NULL, &a) == WD_OK, "TryExec is not checked without a PATH");

    /* truncation never cuts a UTF-8 sequence */
    char longname[400];
    memset(longname, 'a', 95); strcpy(longname + 95, "\xc3\xa9\xc3\xa9");
    snprintf(buf, sizeof buf, "[Desktop Entry]\nType=Application\nExec=x\nName=%s\n", longname);
    CHECK(parse(buf, NULL, NULL, NULL, &a) == WD_OK && strlen(a.name) == 95, "a 2-byte letter that does not fit is dropped whole: %zu", strlen(a.name));
    memset(longname, 'a', 94); strcpy(longname + 94, "\xc3\xa9tail");
    snprintf(buf, sizeof buf, "[Desktop Entry]\nType=Application\nExec=x\nName=%s\n", longname);
    CHECK(parse(buf, NULL, NULL, NULL, &a) == WD_OK && strlen(a.name) == 94, "half a letter at the limit is dropped, not kept: %zu", strlen(a.name));
    memset(longname, 'a', 93); strcpy(longname + 93, "\xe2\x82\xac");                /* a 3-byte letter that starts at 93: it needs bytes 93, 94 and 95 */
    snprintf(buf, sizeof buf, "[Desktop Entry]\nType=Application\nExec=x\nName=%s\n", longname);
    CHECK(parse(buf, NULL, NULL, NULL, &a) == WD_OK && strlen(a.name) == 93, "a 3-byte letter that does not fit is dropped whole: %zu", strlen(a.name));
    memset(longname, 'b', 300); longname[300] = 0;
    snprintf(buf, sizeof buf, "[Desktop Entry]\nType=Application\nExec=x\nName=%s\n", longname);
    CHECK(parse(buf, NULL, NULL, NULL, &a) == WD_OK && strlen(a.name) == WD_NAME - 1, "a very long name is cut");

    /* NUL inside the text ends it */
    CHECK(wd_desktop_parse("[Desktop Entry]\nType=Application\nName=Foo\0Exec=foo\n", 49, "x", "y", NULL, &a) == WD_INCOMPLETE, "text after a NUL byte is not read");

    /* fuzz: structured and random input must never fault (the sanitizers watch) */
    unsigned long long st = 88172645463325252ULL;
#define RND() (st ^= st << 13, st ^= st >> 7, st ^= st << 17, st)
    const char *frag[] = {"[Desktop Entry]\n", "Type=Application\n", "Name=", "Name[de_DE@x]=", "Exec=", "\"", "'", "%", "%%", "\\", "\\s", ";", "\n", "\r\n", "=", "[", "]", "Hidden=true\n", "OnlyShowIn=", "TryExec=", "\xff\xfe", "\xc3", "a", " ", "\t", "#"};
    for (int round = 0; round < 3000; round++) {
        char fz[2048]; size_t o = 0;
        int parts = (int)(RND() % 40);
        for (int i = 0; i < parts && o + 80 < sizeof fz; i++) { const char *f = frag[RND() % (sizeof frag / sizeof *frag)]; size_t n = strlen(f); memcpy(fz + o, f, n); o += n; }
        WdLoadOpts fo = {"de_DE.UTF-8@euro", "WispOS:sway", NULL};
        wd_desktop_parse(fz, o, "f.desktop", "/f", &fo, &a);
        char fbuf[1024]; char *av[16];
        WdApp ex; memset(&ex, 0, sizeof ex);
        size_t en = o < sizeof ex.exec - 1 ? o : sizeof ex.exec - 1; memcpy(ex.exec, fz, en); ex.exec[en] = 0;
        strcpy(ex.name, "N"); strcpy(ex.icon, "i"); strcpy(ex.path, "/p");
        wd_exec_argv(&ex, "foot", fbuf, sizeof fbuf, av, 16);
        WdPins fp; fz[o] = 0; wd_pins_parse(fz, &fp);
    }
    for (int round = 0; round < 500; round++) {
        unsigned char rb[600]; for (size_t i = 0; i < sizeof rb; i++) rb[i] = (unsigned char)RND();
        WdLoadOpts fo = {"de", "WispOS", NULL};
        wd_desktop_parse((char *)rb, sizeof rb, "f.desktop", "/f", &fo, &a);
    }
#undef RND
}

static void test_exec(void) {
    WdApp a; char buf[512]; char *av[16];
    struct { const char *exec, *name, *icon; int terminal; const char *want; } t[] = {      /* want: the arguments joined by '|', or NULL for invalid */
        {"foot", "N", "", 0, "foot"},
        {"firefox %u", "N", "", 0, "firefox"},
        {"firefox %U %F %f", "N", "", 0, "firefox"},
        {"sh -c \"echo hi && ls\"", "N", "", 0, "sh|-c|echo hi && ls"},
        {"sh -c 'echo hi && ls'", "N", "", 0, "sh|-c|echo hi && ls"},
        {"env FOO=bar app --flag %F", "N", "", 0, "env|FOO=bar|app|--flag"},
        {"app --name=%c", "My App", "", 0, "app|--name=My App"},
        {"app %c", "My App", "", 0, "app|My App"},
        {"app %i", "N", "foo", 0, "app|--icon|foo"},
        {"app %i", "N", "", 0, "app"},
        {"app x%iy", "N", "foo", 0, "app|xy"},
        {"app %k", "N", "", 0, "app|/p"},
        {"app 100%%", "N", "", 0, "app|100%"},
        {"app %d %D %n %N %v %m", "N", "", 0, "app"},
        {"app %z", "N", "", 0, NULL},
        {"app %", "N", "", 0, NULL},
        {"app \"unterminated", "N", "", 0, NULL},
        {"app 'unterminated", "N", "", 0, NULL},
        {"app \"a\\\"b\"", "N", "", 0, "app|a\"b"},
        {"app \"a\\\\b\"", "N", "", 0, "app|a\\b"},
        {"app \"cost: \\$5 \\`x\\`\"", "N", "", 0, "app|cost: $5 `x`"},
        {"app \"$HOME\"", "N", "", 0, "app|$HOME"},
        {"app a\\ b", "N", "", 0, "app|a b"},
        {"app \"\" x", "N", "", 0, "app||x"},
        {"  app \t x  ", "N", "", 0, "app|x"},
        {"app \"a b\"c d", "N", "", 0, "app|a bc|d"},
        {"app --x=\"%c\"", "N A", "", 0, "app|--x=N A"},
        {"htop", "N", "", 1, "foot|htop"},
        {"htop -d 5", "N", "", 1, "foot|htop|-d|5"},
        {"", "N", "", 0, NULL},
        {"   ", "N", "", 0, NULL},
        {"%U", "N", "", 0, NULL},
    };
    for (size_t i = 0; i < sizeof t / sizeof *t; i++) {
        memset(&a, 0, sizeof a);
        snprintf(a.exec, sizeof a.exec, "%s", t[i].exec); snprintf(a.name, sizeof a.name, "%s", t[i].name); snprintf(a.icon, sizeof a.icon, "%s", t[i].icon);
        snprintf(a.path, sizeof a.path, "/p"); a.terminal = t[i].terminal;
        int n = wd_exec_argv(&a, "foot", buf, sizeof buf, av, 16);
        char got[512] = "";
        for (int j = 0; j < n; j++) { if (j) strcat(got, "|"); strcat(got, av[j]); }
        if (!t[i].want) CHECK(n == 0, "'%s' must be invalid, got '%s'", t[i].exec, got);
        else CHECK(n > 0 && !strcmp(got, t[i].want) && av[n] == NULL, "'%s' -> '%s', wanted '%s'", t[i].exec, got, t[i].want);
    }
    snprintf(a.exec, sizeof a.exec, "app one two three");
    CHECK(wd_exec_argv(&a, NULL, buf, 8, av, 16) == 0, "a buffer too small is refused");
    CHECK(wd_exec_argv(&a, NULL, buf, sizeof buf, av, 3) == 0, "too many arguments is refused");
    CHECK(wd_exec_argv(&a, NULL, buf, sizeof buf, av, 5) == 4 && av[4] == NULL, "exactly enough room");
    a.terminal = true;
    CHECK(wd_exec_argv(&a, NULL, buf, sizeof buf, av, 16) == 4, "Terminal without a terminal program: the command alone");
}

static void test_loader(void) {
    put("usr/applications/firefox.desktop", "[Desktop Entry]\nType=Application\nName=Firefox\nExec=firefox %u\nIcon=firefox\nStartupWMClass=firefox\n");
    put("usr/applications/Terminal.desktop", "[Desktop Entry]\nType=Application\nName=terminal\nExec=foot\n");
    put("usr/applications/zeta.desktop", "[Desktop Entry]\nType=Application\nName=Zeta\nExec=zeta\n");
    put("usr/applications/alpha.desktop", "[Desktop Entry]\nType=Application\nName=alpha\nExec=alpha\n");
    put("usr/applications/hidden-helper.desktop", "[Desktop Entry]\nType=Application\nName=Helper\nExec=helper\nNoDisplay=true\n");
    put("usr/applications/broken.desktop", "[Desktop Entry]\nType=Application\nName=Broken\n");
    put("usr/applications/readme.txt", "[Desktop Entry]\nType=Application\nName=Not a desktop file\nExec=x\n");
    put("usr/applications/sub/nested.desktop", "[Desktop Entry]\nType=Application\nName=Nested\nExec=nested\n");
    put("usr/applications/sub/deeper/x.desktop", "[Desktop Entry]\nType=Application\nName=Deeper\nExec=deeper\n");
    put("usr/applications/shared.desktop", "[Desktop Entry]\nType=Application\nName=System Shared\nExec=system-shared\n");
    put("usr/applications/removed.desktop", "[Desktop Entry]\nType=Application\nName=Removed By The User\nExec=removed\n");
    put("usr/applications/gnome-only.desktop", "[Desktop Entry]\nType=Application\nName=GnomeOnly\nExec=g\nOnlyShowIn=GNOME;\n");
    put("user/applications/shared.desktop", "[Desktop Entry]\nType=Application\nName=User Shared\nExec=user-shared\n");
    put("user/applications/removed.desktop", "[Desktop Entry]\nType=Application\nHidden=true\nName=x\nExec=x\n");
    put("user/applications/appimage-game.desktop", "[Desktop Entry]\nType=Application\nName=My Game\nExec=/storage/games/game.AppImage\nIcon=/storage/games/game.png\n");
    char big[WD_MAX_FILE + 100]; memset(big, '#', sizeof big - 1); big[sizeof big - 1] = 0;
    memcpy(big, "[Desktop Entry]\nType=Application\nName=Big\nExec=big\n", 50); big[50] = '\n';
    put("usr/applications/big.desktop", big);

    char user[600], sys[600];
    path_of(user, sizeof user, "user/applications"); path_of(sys, sizeof sys, "usr/applications");
    const char *dirs[] = {user, sys, "/nonexistent/applications"};
    WdLoadOpts o = {NULL, "WispOS:sway", NULL};
    WdApps apps = {0};
    int n = wd_apps_load(&apps, dirs, 3, &o);
    CHECK(n == apps.n && n == 9, "count: %d", n);
    const char *order[] = {"alpha", "Firefox", "Helper", "My Game", "Nested", "terminal", "User Shared", "Zeta", "Deeper"};
    (void)order;
    for (int i = 1; i < apps.n; i++) CHECK(strcasecmp(apps.v[i - 1].name, apps.v[i].name) <= 0, "sorted by name, case-insensitively: '%s' then '%s'", apps.v[i - 1].name, apps.v[i].name);
    int sh = wd_app_find(&apps, "shared.desktop");
    CHECK(sh >= 0 && !strcmp(apps.v[sh].name, "User Shared"), "an earlier directory wins for the same ID");
    CHECK(wd_app_find(&apps, "removed.desktop") < 0, "a Hidden file in an earlier directory removes the later one");
    CHECK(wd_app_find(&apps, "gnome-only.desktop") < 0 && wd_app_find(&apps, "broken.desktop") < 0 && wd_app_find(&apps, "readme.txt") < 0, "not shown, incomplete and non-.desktop files are left out");
    CHECK(wd_app_find(&apps, "sub-nested.desktop") >= 0 && wd_app_find(&apps, "sub-deeper-x.desktop") >= 0, "a subdirectory's slash becomes a dash in the ID");
    CHECK(wd_app_find(&apps, "big.desktop") < 0, "a file over the size limit is not read");
    CHECK(wd_app_find(&apps, "hidden-helper.desktop") >= 0 && apps.v[wd_app_find(&apps, "hidden-helper.desktop")].nodisplay, "NoDisplay entries are kept (they still match windows)");
    CHECK(wd_app_find(&apps, NULL) < 0 && wd_app_find(&apps, "") < 0 && wd_app_find(&apps, "nope.desktop") < 0, "find: not found");

    int idx[64];
    int m = wd_menu_list(&apps, NULL, idx, 64);
    CHECK(m == n - 1, "the menu leaves out NoDisplay: %d of %d", m, n);
    int q = wd_menu_list(&apps, "FIRE", idx, 64);
    CHECK(q == 1 && !strcmp(apps.v[idx[0]].name, "Firefox"), "type to filter, case-insensitively");
    q = wd_menu_list(&apps, "e", idx, 64);
    CHECK(q >= 3, "substring anywhere in the name: %d", q);
    CHECK(wd_menu_list(&apps, "no such app", idx, 64) == 0, "no match");
    CHECK(wd_menu_list(&apps, "", idx, 3) == 3, "capacity respected");

    WdApps empty = {0};
    CHECK(wd_apps_load(&empty, NULL, 0, &o) == 0 && empty.n == 0, "no directories");
    wd_apps_free(&empty);
    CHECK(wd_apps_load(NULL, dirs, 1, &o) == -1, "no list to fill");

    /* window matching */
    put("m/applications/org.mozilla.firefox.desktop", "[Desktop Entry]\nType=Application\nName=Firefox\nExec=/usr/bin/firefox %u\n");
    put("m/applications/steam.desktop", "[Desktop Entry]\nType=Application\nName=Steam\nExec=env LD_PRELOAD=x steam %U\nStartupWMClass=Steam\n");
    put("m/applications/images.desktop", "[Desktop Entry]\nType=Application\nName=Images\nExec=/usr/bin/swayimg %F\n");
    put("m/applications/Foo.Bar.desktop", "[Desktop Entry]\nType=Application\nName=FooBar\nExec=nothing-like-it\n");
    put("m/applications/class-only.desktop", "[Desktop Entry]\nType=Application\nName=ClassOnly\nExec=something-else\nStartupWMClass=WeirdClass\n");
    char md[600]; path_of(md, sizeof md, "m/applications");
    const char *mdirs[] = {md};
    WdApps m_apps = {0};
    wd_apps_load(&m_apps, mdirs, 1, &o);
    int ff = wd_app_find(&m_apps, "org.mozilla.firefox.desktop"), st = wd_app_find(&m_apps, "steam.desktop"), im = wd_app_find(&m_apps, "images.desktop"), fb = wd_app_find(&m_apps, "Foo.Bar.desktop");
    CHECK(wd_app_for_window(&m_apps, "org.mozilla.firefox") == ff, "app_id equals the ID without its suffix");
    CHECK(wd_app_for_window(&m_apps, "org.mozilla.firefox.desktop") == ff && wd_app_for_window(&m_apps, "ORG.MOZILLA.FIREFOX") == ff, "with the suffix, any case");
    CHECK(wd_app_for_window(&m_apps, "Steam") == st && wd_app_for_window(&m_apps, "steam") == st, "ID, and StartupWMClass");
    CHECK(wd_app_for_window(&m_apps, "swayimg") == im, "the program Exec starts (a path, field codes)");
    CHECK(wd_app_for_window(&m_apps, "foo.bar") == fb, "ID with dots, any case");
    int co = wd_app_find(&m_apps, "class-only.desktop");
    CHECK(wd_app_for_window(&m_apps, "weirdclass") == co && wd_app_for_window(&m_apps, "WeirdClass") == co, "StartupWMClass alone, any case");
    CHECK(wd_app_for_window(&m_apps, "unknown") == -1 && wd_app_for_window(&m_apps, "") == -1 && wd_app_for_window(&m_apps, NULL) == -1, "no match");
    wd_apps_free(&m_apps);

    /* a pin set against these apps */
    wd_apps_free(&apps);
}

static void test_icons(void) {
    put("icons/hicolor/48x48/apps/foo.png", "PNG");
    put("icons/hicolor/96x96/apps/foo.png", "PNG");
    put("icons/hicolor/22x22/apps/small.png", "PNG");
    put("pix/bar.png", "PNG");
    put("pix/notpng.svg", "<svg/>");
    char r1[600], r2[600], abs_ok[600], out[600];
    path_of(r1, sizeof r1, "icons/hicolor"); path_of(r2, sizeof r2, "pix"); path_of(abs_ok, sizeof abs_ok, "pix/bar.png");
    const char *roots[] = {r1, r2};
    CHECK(wd_icon_find("foo", 22, roots, 2, out, sizeof out) && strstr(out, "48x48"), "22 wanted, 48 is the nearest there is: %s", out);
    CHECK(wd_icon_find("foo", 90, roots, 2, out, sizeof out) && strstr(out, "96x96"), "90 wanted: 96");
    CHECK(wd_icon_find("foo", 72, roots, 2, out, sizeof out) && strstr(out, "96x96"), "72 is as near 48 as 96: the larger");
    CHECK(wd_icon_find("small", 48, roots, 2, out, sizeof out) && strstr(out, "22x22"), "the only size there is");
    CHECK(wd_icon_find("bar", 48, roots, 2, out, sizeof out) && strstr(out, "pix/bar.png"), "a plain <root>/<name>.png (pixmaps)");
    CHECK(wd_icon_find("bar.png", 48, roots, 2, out, sizeof out), "a name with its .png");
    CHECK(wd_icon_find(abs_ok, 48, roots, 2, out, sizeof out) && !strcmp(out, abs_ok), "an absolute path that exists");
    char abs_bad[600]; path_of(abs_bad, sizeof abs_bad, "pix/none.png");
    CHECK(!wd_icon_find(abs_bad, 48, roots, 2, out, sizeof out), "an absolute path that does not exist");
    char abs_svg[600]; path_of(abs_svg, sizeof abs_svg, "pix/notpng.svg");
    CHECK(!wd_icon_find(abs_svg, 48, roots, 2, out, sizeof out), "only PNG is drawn");
    put("secret.png", "PNG");                                                    /* two levels above the first root: reachable only by ../../secret */
    CHECK(!wd_icon_find("../../secret", 48, roots, 2, out, sizeof out), "a name with .. cannot reach a file outside the roots");
    CHECK(!wd_icon_find("../etc/passwd", 48, roots, 2, out, sizeof out) && !wd_icon_find("a/b", 48, roots, 2, out, sizeof out) && !wd_icon_find("..", 48, roots, 2, out, sizeof out), "a name is never a path");
    CHECK(!wd_icon_find("", 48, roots, 2, out, sizeof out) && !wd_icon_find(NULL, 48, roots, 2, out, sizeof out) && !wd_icon_find("nothing", 48, roots, 2, out, sizeof out), "empty, null, missing");
    CHECK(!wd_icon_find("foo", 48, roots, 2, out, 8), "an output buffer too small");

    CHECK(wd_tile_letter("firefox") == 'F' && wd_tile_letter("Terminal") == 'T' && wd_tile_letter("  7zip") == '7' && wd_tile_letter("\xe6\x97\xa5\xe6\x9c\xac") == '?' && wd_tile_letter("") == '?' && wd_tile_letter(NULL) == '?', "tile letters");
    CHECK(wd_tile_colour("Firefox") == wd_tile_colour("firefox") && wd_tile_colour("a") >= 0 && wd_tile_colour("a") < 4 && wd_tile_colour("zzz") < 4 && wd_tile_colour(NULL) >= 0, "tile colour: from the name, case-blind, in range");
    int seen[4] = {0};
    const char *names[] = {"alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"};
    for (size_t i = 0; i < sizeof names / sizeof *names; i++) seen[wd_tile_colour(names[i])] = 1;
    CHECK(seen[0] + seen[1] + seen[2] + seen[3] >= 3, "the four colours are all in use");
}

static void test_pins(void) {
    WdPins p;
    CHECK(wd_pins_parse("# comment\n\n  a.desktop  \r\nb.desktop\na.desktop\n\tc.desktop\nlast.desktop", &p) == 4 && !strcmp(p.id[0], "a.desktop") && !strcmp(p.id[3], "last.desktop"), "parse: comments, blanks, spaces, CRLF, duplicates, no final newline");
    char toolong[300]; memset(toolong, 'x', 200); toolong[200] = 0;
    char text[600]; snprintf(text, sizeof text, "%s\nok.desktop\n", toolong);
    CHECK(wd_pins_parse(text, &p) == 1 && !strcmp(p.id[0], "ok.desktop"), "an ID that is too long is skipped");
    char many[WD_MAX_PINS * 20 + 400] = "";
    for (int i = 0; i < WD_MAX_PINS + 10; i++) { char l[32]; snprintf(l, sizeof l, "p%d.desktop\n", i); strcat(many, l); }
    CHECK(wd_pins_parse(many, &p) == WD_MAX_PINS, "the list is capped");
    CHECK(wd_pins_parse(NULL, &p) == 0 && wd_pins_parse("", &p) == 0, "nothing");

    wd_pins_parse("a.desktop\nb.desktop\nc.desktop\n", &p);
    char out[2048];
    int len = wd_pins_format(&p, out, sizeof out);
    CHECK(len > 0 && strstr(out, "# WispOS dock") == out && strstr(out, "a.desktop\nb.desktop\nc.desktop\n"), "format");
    WdPins q; wd_pins_parse(out, &q);
    CHECK(q.n == 3 && !strcmp(q.id[2], "c.desktop"), "format and parse round-trip");
    CHECK(wd_pins_format(&p, out, 10) == -1, "a buffer too small");

    CHECK(wd_pins_toggle(&p, "d.desktop") == 1 && p.n == 4 && !strcmp(p.id[3], "d.desktop"), "pin: appended");
    CHECK(wd_pins_toggle(&p, "b.desktop") == 0 && p.n == 3 && !strcmp(p.id[1], "c.desktop"), "unpin: closes the gap");
    CHECK(wd_pins_toggle(&p, "") == -1, "an empty ID is not pinned");
    WdPins full; full.n = 0;
    for (int i = 0; i < WD_MAX_PINS; i++) { char l[32]; snprintf(l, sizeof l, "f%d.desktop", i); wd_pins_toggle(&full, l); }
    CHECK(full.n == WD_MAX_PINS && wd_pins_toggle(&full, "one-more.desktop") == -1, "full: -1");

    wd_pins_parse("a.desktop\nb.desktop\nc.desktop\n", &p);
    CHECK(wd_pins_move(&p, "b.desktop", -1) == 0 && !strcmp(p.id[0], "b.desktop") && !strcmp(p.id[1], "a.desktop"), "move left");
    CHECK(wd_pins_move(&p, "b.desktop", -1) == 0 && !strcmp(p.id[0], "b.desktop"), "move left at the start: stays");
    CHECK(wd_pins_move(&p, "c.desktop", 1) == 2 && !strcmp(p.id[2], "c.desktop"), "move right at the end: stays");
    CHECK(wd_pins_move(&p, "a.desktop", 1) == 2 && !strcmp(p.id[1], "c.desktop") && !strcmp(p.id[2], "a.desktop"), "move right");
    CHECK(wd_pins_move(&p, "nope.desktop", 1) == -1, "move: not pinned");
    CHECK(wd_pins_find(&p, "a.desktop") == 2 && wd_pins_find(&p, "x") == -1 && wd_pins_find(&p, NULL) == -1, "find");

    /* first-run seeding */
    put("seed/applications/a.desktop", "[Desktop Entry]\nType=Application\nName=A\nExec=a\n");
    put("seed/applications/b.desktop", "[Desktop Entry]\nType=Application\nName=B\nExec=b\n");
    char sd[600]; path_of(sd, sizeof sd, "seed/applications");
    const char *sdirs[] = {sd};
    WdApps sapps = {0}; WdLoadOpts o = {NULL, NULL, NULL};
    wd_apps_load(&sapps, sdirs, 1, &o);
    const char *pref[] = {"missing.desktop", "b.desktop", "a.desktop", "b.desktop"};
    WdPins sp; sp.n = 0;
    CHECK(wd_pins_seed(&sp, &sapps, pref, 4) == 2 && !strcmp(sp.id[0], "b.desktop") && !strcmp(sp.id[1], "a.desktop"), "seed: the preferred IDs that exist, in that order, once");
    wd_apps_free(&sapps);

    /* load and save: atomic, and the file is not rewritten when nothing changed */
    char file[600]; path_of(file, sizeof file, "config/wispos/dock");
    WdPins lp;
    CHECK(wd_pins_load(&lp, file) == 0 && lp.n == 0, "no file yet");
    wd_pins_parse("x.desktop\ny.desktop\n", &p);
    CHECK(wd_pins_save(&p, file) == 0, "first save writes (and makes the directory)");
    CHECK(wd_pins_load(&lp, file) == 1 && lp.n == 2 && !strcmp(lp.id[1], "y.desktop"), "load what was saved");
    struct timeval old[2] = {{1000000000, 0}, {1000000000, 0}};
    utimes(file, old);
    CHECK(wd_pins_save(&p, file) == 1, "unchanged: 1, nothing written");
    struct stat sb; stat(file, &sb);
    CHECK(sb.st_mtime == 1000000000, "the file was not touched");
    wd_pins_toggle(&p, "z.desktop");
    CHECK(wd_pins_save(&p, file) == 0, "changed: written");
    stat(file, &sb);
    CHECK(sb.st_mtime != 1000000000, "and now touched");
    char tmp[700]; snprintf(tmp, sizeof tmp, "%s.tmp", file);
    CHECK(access(tmp, F_OK) != 0, "no temporary file left behind");
    put("a-file-not-a-directory", "x");
    char bad[700]; path_of(bad, sizeof bad, "a-file-not-a-directory/sub/dock");
    CHECK(wd_pins_save(&p, bad) == -1, "an unwritable place (a file where a directory should be): -1");
}

static void test_windows(void) {
    WdWindows w; memset(&w, 0, sizeof w);
    wd_windows_update(&w, 1, "foot", "term", 0);
    wd_windows_update(&w, 2, "firefox", "page", 1);
    CHECK(w.n == 2 && !strcmp(w.w[1].app_id, "firefox") && w.w[1].activated && !w.w[0].activated, "add");
    wd_windows_update(&w, 1, NULL, NULL, 1);
    CHECK(w.w[0].activated && !w.w[1].activated, "activating one clears the other");
    CHECK(!strcmp(w.w[0].app_id, "foot") && !strcmp(w.w[0].title, "term"), "NULL keeps the old values");
    wd_windows_update(&w, 1, NULL, "new title", -1);
    CHECK(!strcmp(w.w[0].title, "new title") && w.w[0].activated, "-1 keeps the state");
    wd_windows_update(&w, 1, NULL, NULL, 0);
    CHECK(!w.w[0].activated, "deactivate");
    wd_windows_remove(&w, 1);
    CHECK(w.n == 1 && w.w[0].id == 2, "remove closes the gap");
    wd_windows_remove(&w, 99);
    CHECK(w.n == 1, "removing an unknown window is harmless");
    for (unsigned long i = 10; i < 10 + WD_MAX_WINDOWS + 5; i++) wd_windows_update(&w, i, "x", "y", 0);
    CHECK(w.n == WD_MAX_WINDOWS, "capacity");
    char longt[400]; memset(longt, 'T', 399); longt[399] = 0;
    wd_windows_update(&w, 2, longt, longt, -1);
    CHECK(strlen(w.w[0].title) == WD_TITLE - 1, "long strings are cut");
    WdWindows u; memset(&u, 0, sizeof u);
    char lt[300]; memset(lt, 'a', 126); strcpy(lt + 126, "\xc3\xa9zz");
    wd_windows_update(&u, 1, "x", lt, 0);
    CHECK(strlen(u.w[0].title) == 126, "a title cut inside a letter loses the whole letter: %zu", strlen(u.w[0].title));
}

/* the scenario of the locked picture desktop-window.png: Applications, three pins (browser, terminal, settings), the terminal and Images running */
static void scenario(WdApps *apps, WdPins *pins, WdWindows *ws) {
    put("dock/applications/browser.desktop", "[Desktop Entry]\nType=Application\nName=Browser\nExec=firefox\nIcon=browser\n");
    put("dock/applications/terminal.desktop", "[Desktop Entry]\nType=Application\nName=Terminal\nExec=foot\nIcon=terminal\n");
    put("dock/applications/settings.desktop", "[Desktop Entry]\nType=Application\nName=Settings\nExec=wisp-settings\nIcon=settings\n");
    put("dock/applications/images.desktop", "[Desktop Entry]\nType=Application\nName=Images\nExec=swayimg\nIcon=images\nStartupWMClass=swayimg\n");
    char d[600]; path_of(d, sizeof d, "dock/applications");
    const char *dirs[] = {d};
    WdLoadOpts o = {NULL, NULL, NULL};
    memset(apps, 0, sizeof *apps);
    wd_apps_load(apps, dirs, 1, &o);
    wd_pins_parse("browser.desktop\nterminal.desktop\nsettings.desktop\n", pins);
    memset(ws, 0, sizeof *ws);
    wd_windows_update(ws, 11, "foot", "wisp@WispOS: ~", 1);
    wd_windows_update(ws, 12, "swayimg", "photo.png", 0);
}

static void test_dock(void) {
    WdApps apps; WdPins pins; WdWindows ws;
    scenario(&apps, &pins, &ws);
    WdItem it[WD_MAX_ITEMS];
    int n = wd_dock_build(&apps, &pins, &ws, it, WD_MAX_ITEMS);
    WdKind want[] = {WD_APPS, WD_SEP, WD_PIN, WD_PIN, WD_PIN, WD_SEP, WD_WINDOW, WD_GAME, WD_SEP, WD_STATUS};
    CHECK(n == 10, "item count %d", n);
    for (int i = 0; i < n && i < 10; i++) CHECK(it[i].kind == want[i], "item %d kind %d, wanted %d", i, it[i].kind, want[i]);
    CHECK(!strcmp(it[2].label, "Browser") && !it[2].running && !it[2].active, "a pin that is not running");
    CHECK(!strcmp(it[3].label, "Terminal") && it[3].running && it[3].active && it[3].nwin == 1 && it[3].window == 11, "the terminal runs and is in front");
    CHECK(it[6].kind == WD_WINDOW && !strcmp(it[6].label, "Images") && it[6].running && !it[6].active && it[6].window == 12 && it[6].app >= 0 && !strcmp(it[6].icon, "images"), "an unpinned window shows as its application");
    CHECK(!strcmp(it[7].key, "game") && !strcmp(it[9].key, "status"), "Game mode and the status group close the strip");

    /* layout: where the locked picture has them (dock-local coordinates, a 45 px clock) */
    wd_dock_layout(it, n, 720, 45);
    CHECK(it[0].x == 8 && it[0].y == 5 && it[0].w == 36 && it[0].h == 36, "Applications at the inset");
    CHECK(it[1].kind == WD_SEP && it[1].x == 48 && it[1].w == 1 && it[1].y == 11 && it[1].h == 24, "the first divider sits in the gap");
    CHECK(it[2].x == 56 && it[3].x == 100 && it[4].x == 144, "pins 44 px apart after the divider: %d %d %d", it[2].x, it[3].x, it[4].x);
    CHECK(it[5].x == 184 && it[6].x == 192, "a divider and the other windows: %d %d", it[5].x, it[6].x);
    CHECK(it[9].x == 561 && it[9].w == 153 && it[8].x == 554 && it[7].x == 513, "the right end: status %d (w %d), divider %d, Game mode %d", it[9].x, it[9].w, it[8].x, it[7].x);
    CHECK(it[9].x + it[9].w == 714, "the status group ends 2 px past the inset");
    CHECK(wd_dock_hit(it, n, 20, 20) == 0 && wd_dock_hit(it, n, 100, 5) == 3 && wd_dock_hit(it, n, 143, 20) == -1 && wd_dock_hit(it, n, 144, 20) == 4, "hit testing: tiles are 36 px, the gaps are nothing");
    CHECK(wd_dock_hit(it, n, 48, 20) == -1, "a divider is never hit");
    CHECK(wd_dock_hit(it, n, 600, 20) == 9 && wd_dock_hit(it, n, 520, 20) == 7 && wd_dock_hit(it, n, 719, 20) == -1 && wd_dock_hit(it, n, -1, 0) == -1 && wd_dock_hit(it, n, 10, 44) == -1, "status, Game mode, outside");

    /* focus moves over the dividers, never wraps */
    CHECK(wd_dock_move(it, n, 0, 1) == 2 && wd_dock_move(it, n, 4, 1) == 6 && wd_dock_move(it, n, 6, 1) == 7 && wd_dock_move(it, n, 7, 1) == 9, "right skips dividers");
    CHECK(wd_dock_move(it, n, 9, 1) == 9 && wd_dock_move(it, n, 0, -1) == 0, "no wrap at either end");
    CHECK(wd_dock_move(it, n, 9, -1) == 7 && wd_dock_move(it, n, 6, -1) == 4 && wd_dock_move(it, n, 2, -1) == 0, "left skips dividers");
    CHECK(wd_dock_first_focus(it, n) == 3, "summoned: the focus starts on the window in front");
    WdWindows none; memset(&none, 0, sizeof none);
    int n2 = wd_dock_build(&apps, &pins, &none, it, WD_MAX_ITEMS);
    CHECK(n2 == 8 && wd_dock_first_focus(it, n2) == 2, "no windows: the first pin");
    WdPins nopins; nopins.n = 0;
    n2 = wd_dock_build(&apps, &nopins, &none, it, WD_MAX_ITEMS);
    CHECK(n2 == 5 && wd_dock_first_focus(it, n2) == 0, "nothing at all: Applications (%d items)", n2);

    /* two windows of a pinned application fold into the pin; a pin without an application is skipped; an unknown window shows its title */
    wd_windows_update(&ws, 13, "foot", "second terminal", 0);
    wd_windows_update(&ws, 14, "mystery", "Some Title", 0);
    wd_pins_toggle(&pins, "ghost.desktop");
    n = wd_dock_build(&apps, &pins, &ws, it, WD_MAX_ITEMS);
    CHECK(it[3].nwin == 2 && it[3].window == 11, "two terminals: one pin, two windows, the one in front");
    int found = 0;
    for (int i = 0; i < n; i++) if (it[i].kind == WD_WINDOW && !strcmp(it[i].label, "Some Title") && it[i].app == -1 && !it[i].icon[0]) found = 1;
    CHECK(found, "a window with no application: its title, no icon (the shell draws a letter tile)");
    for (int i = 0; i < n; i++) CHECK(strcmp(it[i].key, "ghost.desktop"), "a missing pin is not shown");
    CHECK(n == 11, "count with an unknown window: %d", n);
    int cnt = wd_dock_build(&apps, &pins, &ws, it, 4);
    CHECK(cnt == 4, "a small output array is respected: %d", cnt);

    /* actions of an item, in the order of the locked picture */
    WdAction act[8];
    n = wd_dock_build(&apps, &pins, &none, it, WD_MAX_ITEMS);
    int c = wd_item_actions(&it[3], &pins, act);          /* the terminal: a middle pin, not running */
    CHECK(c == 4 && act[0] == WD_ACT_OPEN && act[1] == WD_ACT_UNPIN && act[2] == WD_ACT_LEFT && act[3] == WD_ACT_RIGHT, "a middle pin: open, unpin, move both ways (%d)", c);
    c = wd_item_actions(&it[2], &pins, act);
    CHECK(c == 3 && act[2] == WD_ACT_RIGHT, "the first pin cannot move left (%d)", c);
    n = wd_dock_build(&apps, &pins, &ws, it, WD_MAX_ITEMS);
    c = wd_item_actions(&it[3], &pins, act);
    CHECK(c == 5 && act[4] == WD_ACT_CLOSE, "a running pin can be closed (%d)", c);
    int win_item = -1;
    for (int i = 0; i < n; i++) if (it[i].kind == WD_WINDOW && it[i].app >= 0) win_item = i;
    c = wd_item_actions(&it[win_item], &pins, act);
    CHECK(c == 3 && act[0] == WD_ACT_OPEN && act[1] == WD_ACT_PIN && act[2] == WD_ACT_CLOSE, "an unpinned window of a known application: open, pin, close (%d)", c);
    int unknown = -1;
    for (int i = 0; i < n; i++) if (it[i].kind == WD_WINDOW && it[i].app < 0) unknown = i;
    c = wd_item_actions(&it[unknown], &pins, act);
    CHECK(c == 2 && act[0] == WD_ACT_OPEN && act[1] == WD_ACT_CLOSE, "a window with no desktop file cannot be pinned (%d)", c);
    CHECK(wd_item_actions(&it[0], &pins, act) == 0 && wd_item_actions(&it[n - 1], &pins, act) == 0, "Applications and the status group have no actions");
    CHECK(!strcmp(wd_action_label(WD_ACT_UNPIN), "Unpin from dock") && !strcmp(wd_action_label(WD_ACT_CLOSE), "Close window") && !strcmp(wd_action_label(WD_ACT_PIN), "Pin to dock"), "labels");

    /* print the layout of the locked scenario for the picture comparison in the Python test */
    n = wd_dock_build(&apps, &pins, &ws, it, WD_MAX_ITEMS);
    wd_pins_parse("browser.desktop\nterminal.desktop\nsettings.desktop\n", &pins);
    memset(&ws, 0, sizeof ws);
    wd_windows_update(&ws, 11, "foot", "t", 1);
    wd_windows_update(&ws, 12, "swayimg", "p", 0);
    n = wd_dock_build(&apps, &pins, &ws, it, WD_MAX_ITEMS);
    wd_dock_layout(it, n, 720, 45);
    for (int i = 0; i < n; i++) printf("LAYOUT %d %s %d %d %d %d\n", it[i].kind, it[i].key[0] ? it[i].key : "-", it[i].x, it[i].y, it[i].w, it[i].h);

    wd_apps_free(&apps);
}

static void test_menu(void) {
    /* 7 apps in 4 columns: two rows, the second one short */
    CHECK(wd_menu_move(0, 7, 4, 3) == 1 && wd_menu_move(3, 7, 4, 3) == 4 && wd_menu_move(6, 7, 4, 3) == 6, "right: by cell, row to row, stops at the end");
    CHECK(wd_menu_move(0, 7, 4, 2) == 0 && wd_menu_move(4, 7, 4, 2) == 3, "left: stops at the start, reads back across rows");
    CHECK(wd_menu_move(1, 7, 4, 1) == 5 && wd_menu_move(3, 7, 4, 1) == 6, "down: the same column, a short last row lands on its last cell");
    CHECK(wd_menu_move(5, 7, 4, 1) == 5 && wd_menu_move(5, 7, 4, 0) == 1 && wd_menu_move(1, 7, 4, 0) == 1, "down at the last row and up at the first stay");
    CHECK(wd_menu_move(0, 0, 4, 1) == 0 && wd_menu_move(9, 7, 4, 3) == 6 && wd_menu_move(-3, 7, 4, 3) == 1, "empty list; an out-of-range selection is brought back");
    CHECK(wd_menu_move(0, 1, 4, 1) == 0 && wd_menu_move(0, 4, 4, 1) == 0, "one row: down stays");
    CHECK(wd_menu_move(2, 7, 4, 99) == 2, "an unknown direction changes nothing");
    CHECK(wd_menu_top_row(0, 4, 2, 0) == 0 && wd_menu_top_row(5, 4, 2, 0) == 0 && wd_menu_top_row(8, 4, 2, 0) == 1 && wd_menu_top_row(12, 4, 2, 1) == 2, "scrolling keeps the selection visible");
    CHECK(wd_menu_top_row(0, 4, 2, 3) == 0 && wd_menu_top_row(7, 4, 2, 1) == 1, "scrolling back up; no change when already visible");
    CHECK(wd_menu_top_row(5, 0, 2, 0) == 0 && wd_menu_top_row(5, 4, 0, 0) == 0, "degenerate sizes");
}

/* Regression (device, 2026-10-15): the shell copied each glyph name into a ring of 8 buffers, so once the dock and the 7-tile application menu had asked for more than 8 icons the
 * Browser's tile read "settings" (a blue gear). A name returned for one tile must stay valid for as long as the shell lives. */
static void test_glyph_names(void) {
    const char *ids[] = { "wisp-browser", "wisp-terminal", "wisp-settings", "wisp-game", "wisp-images", "wisp-monitor", "wisp-about", "wisp-apps", "wisp-browser", "wisp-terminal" };
    const char *got[10];
    for (int i = 0; i < 10; i++) got[i] = wd_glyph_name(ids[i]);
    for (int round = 0; round < 3; round++) for (int i = 0; i < 10; i++) (void)wd_glyph_name(ids[(i + round) % 10]);
    for (int i = 0; i < 10; i++) CHECK(got[i] && !strcmp(got[i], ids[i] + 5), "a glyph name stays what it was after many later requests");
    CHECK(!wd_glyph_name("firefox") && !wd_glyph_name("wisp-nonesuch") && !wd_glyph_name("wisp-") && !wd_glyph_name("") && !wd_glyph_name(NULL), "only our own icon set has glyphs");
}

static void test_tokens(void) {
    CHECK(WISP_UI_DOCK_H == 44 && WISP_UI_DOCK_TILE == 36 && WISP_UI_DOCK_PITCH == 44 && WISP_UI_MENU_COLS == 4, "the locked tokens the layout relies on");
}

int main(void) {
    const char *tmp = getenv("TMPDIR");
    snprintf(root, sizeof root, "%s/wisp-dock-model-XXXXXX", tmp && *tmp ? tmp : "/tmp");
    if (!mkdtemp(root)) { perror("mkdtemp"); return 2; }
    test_parser();
    test_exec();
    test_loader();
    test_icons();
    test_pins();
    test_windows();
    test_dock();
    test_menu();
    test_glyph_names();
    test_tokens();
    char cmd[600]; snprintf(cmd, sizeof cmd, "rm -rf '%s'", root);
    if (system(cmd)) return 2;
    printf("%d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
