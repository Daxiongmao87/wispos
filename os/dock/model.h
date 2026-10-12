/* The dock's logic: what the desktop-mode shell knows, decides and lays out, with no Wayland, no drawing and (apart from the named loaders) no I/O, so it is tested natively on the
 * host (os/test_dock_model.py) and used unchanged by the program that draws it.
 * Design: docs/WISPOS-UI-LANGUAGE.md "Desktop mode" (LOCKED) and docs/WISPOS-OS-DESIGN.md "The dock and the application menu". Sizes come from os/ui/wisp-ui.h.
 *
 *   Applications   .desktop files -> WdApps: parsed to the Desktop Entry Specification 1.5 (UTF-8, groups, escapes, localised Name, Hidden / NoDisplay / OnlyShowIn / NotShowIn / TryExec /
 *                  Terminal / StartupWMClass), desktop file IDs, earlier directories win, sorted by name; Exec turned into an argv (quoting, field codes) without a shell.
 *   Pins           the dock's list of application IDs, in order: text file, atomic save, rewritten only when it changed (the card is written rarely).
 *   Windows        the open windows as the compositor reports them (id, app_id, title, in front); matched to applications by app_id or StartupWMClass.
 *   Dock           items (Applications, pins, other windows, Game mode, status), their layout in pixels (hit testing for the pointer), focus movement, the actions of an item.
 *   Menu           the application grid: filter by typing, movement by the D-pad (or arrow keys), scrolling.
 *
 * Everything here treats its input as untrusted (a downloaded AppImage brings its own .desktop file): sizes are bounded, nothing is executed, nothing is passed to a shell. */
#ifndef WISPOS_DOCK_MODEL_H
#define WISPOS_DOCK_MODEL_H

#include <stdbool.h>
#include <stddef.h>

#define WD_ID       128
#define WD_NAME     96
#define WD_ICON     160
#define WD_EXEC     512
#define WD_CLASS    64
#define WD_PATH     256
#define WD_TITLE    128
#define WD_MAX_PINS 32
#define WD_MAX_WINDOWS 64
#define WD_MAX_APPS 512
#define WD_MAX_ITEMS (3 + WD_MAX_PINS + WD_MAX_WINDOWS + 3)
#define WD_MAX_FILE 65536                   /* a desktop file larger than this is not read */

/* ------------------------------------------------------------------ applications */
typedef struct {
    char id[WD_ID];          /* the desktop file ID, with the suffix: "org.mozilla.firefox.desktop" (a subdirectory's slash becomes a dash) */
    char name[WD_NAME];      /* Name, localised */
    char icon[WD_ICON];      /* Icon as written: a name or an absolute path */
    char exec[WD_EXEC];      /* Exec with the string escapes undone and the field codes still in it (see wd_exec_argv) */
    char tryexec[WD_PATH];
    char wm_class[WD_CLASS]; /* StartupWMClass */
    char path[WD_PATH];      /* the file it came from */
    bool terminal;           /* Terminal=true: run inside a terminal window */
    bool nodisplay;          /* NoDisplay=true: launchable and matched to windows, but not in the menu */
} WdApp;

typedef struct { WdApp *v; int n, cap; } WdApps;

typedef struct {
    const char *lang;        /* LC_MESSAGES-style locale for Name: "de_DE.UTF-8@euro"; NULL = the unlocalised Name */
    const char *desktops;    /* XDG_CURRENT_DESKTOP-style list "WispOS:sway" for OnlyShowIn / NotShowIn; NULL = none: OnlyShowIn entries are then not shown */
    const char *path_env;    /* a PATH to check TryExec against; NULL = do not check */
} WdLoadOpts;

enum { WD_OK = 0, WD_HIDDEN = 1, WD_NOT_APP = 2, WD_NOT_SHOWN = 3, WD_INCOMPLETE = 4, WD_NO_TRYEXEC = 5, WD_BAD = -1 };

/* Parse one desktop file. `id` and `path` are copied into the result. Returns WD_OK or the reason the entry is not an application to offer. */
int  wd_desktop_parse(const char *text, size_t len, const char *id, const char *path, const WdLoadOpts *opt, WdApp *out);
/* Scan the directories (highest priority first; the first file for an ID decides, even if it turns out hidden or not shown). Sorted by name, then ID. Returns the count or -1. */
int  wd_apps_load(WdApps *apps, const char *const *dirs, int ndirs, const WdLoadOpts *opt);
void wd_apps_free(WdApps *apps);
int  wd_app_find(const WdApps *apps, const char *id);             /* index or -1 */
int  wd_app_for_window(const WdApps *apps, const char *app_id);   /* by ID, StartupWMClass, then the program named by Exec; case-insensitive; index or -1 */

/* Exec to argv, without a shell. Quoting and field codes as the specification says (double quotes with backslash escapes; single quotes are also honoured, as GLib does); %f %F %u %U and the
 * deprecated codes vanish (nothing is opened), %i is "--icon" and the icon, %c the name, %k the file, %% a percent; an unknown code makes the command line invalid. With Terminal=true
 * `terminal` ("foot") goes first. argv strings live in buf. Returns the argument count, 0 when invalid or too big. */
int  wd_exec_argv(const WdApp *app, const char *terminal, char *buf, size_t cap, char **argv, int maxargs);

/* Icons: an absolute path is used as it is when it exists; a name is looked for as <root>/<S>x<S>/apps/<name>.png (S nearest to `want` first) and <root>/<name>.png in each root.
 * Returns true and fills `out`, or false (then the shell draws a letter tile: wd_tile_letter, wd_tile_colour). */
bool wd_icon_find(const char *icon, int want, const char *const *roots, int nroots, char *out, size_t cap);
char wd_tile_letter(const char *name);
int  wd_tile_colour(const char *name);                            /* 0..3: accent, green, glyph grey, dim: the palette, chosen from the name */

/* ------------------------------------------------------------------ pins */
typedef struct { char id[WD_MAX_PINS][WD_ID]; int n; } WdPins;
int  wd_pins_parse(const char *text, WdPins *p);                 /* one ID per line, # comments, blanks and duplicates dropped; returns the count */
int  wd_pins_format(const WdPins *p, char *out, size_t cap);     /* returns the length, or -1 when it does not fit */
int  wd_pins_find(const WdPins *p, const char *id);
int  wd_pins_toggle(WdPins *p, const char *id);                  /* pin at the end if absent (1), unpin if present (0), -1 when full */
int  wd_pins_move(WdPins *p, const char *id, int delta);         /* -1 left, +1 right, clamped; returns the new index or -1 */
int  wd_pins_seed(WdPins *p, const WdApps *apps, const char *const *preferred, int n);   /* the first-run pins: the preferred IDs that exist */
int  wd_pins_load(WdPins *p, const char *path);                  /* 1 loaded, 0 no file, -1 error */
int  wd_pins_save(const WdPins *p, const char *path);            /* 0 written (atomically), 1 unchanged and not touched, -1 error */

/* ------------------------------------------------------------------ windows */
typedef struct { unsigned long id; char app_id[WD_ID]; char title[WD_TITLE]; bool activated; } WdWindow;
typedef struct { WdWindow w[WD_MAX_WINDOWS]; int n; } WdWindows;
void wd_windows_update(WdWindows *ws, unsigned long id, const char *app_id, const char *title, int activated);   /* adds it when new; NULL or -1 keep the old value; activating one clears the rest */
void wd_windows_remove(WdWindows *ws, unsigned long id);

/* ------------------------------------------------------------------ the dock */
typedef enum { WD_APPS, WD_PIN, WD_WINDOW, WD_GAME, WD_STATUS, WD_SEP } WdKind;
typedef struct {
    WdKind kind;
    char key[WD_ID];         /* the application ID of a pin, "w<id>" of a window, "apps" "game" "status" */
    char label[WD_NAME];
    char icon[WD_ICON];
    int  app;                /* index into the application list, or -1 */
    int  nwin;               /* open windows of this item */
    unsigned long window;    /* the one to bring forward: the one in front, else the first */
    bool running, active;
    int  x, y, w, h;         /* filled by wd_dock_layout */
} WdItem;

/* The items left to right: Applications, a divider, the pins (the ones that exist), a divider and one item per other window, Game mode, the status group. Returns the count. */
int  wd_dock_build(const WdApps *apps, const WdPins *pins, const WdWindows *ws, WdItem *out, int cap);
/* Pixel positions in the dock's own surface (`width` wide, WISP_UI_DOCK_H high, y from its top), from the tokens of wisp-ui.h; clock_w is the clock text's width. Items and dividers get x, y, w, h. */
void wd_dock_layout(WdItem *items, int n, int width, int clock_w);
int  wd_dock_hit(const WdItem *items, int n, int x, int y);       /* the item under the pointer (never a divider), or -1 */
int  wd_dock_move(const WdItem *items, int n, int cur, int dir);  /* -1 left, +1 right, skipping dividers, clamped (no wrap) */
int  wd_dock_first_focus(const WdItem *items, int n);             /* where the focus starts when the dock is summoned: the item in front, else the first pin, else Applications */

typedef enum { WD_ACT_OPEN, WD_ACT_PIN, WD_ACT_UNPIN, WD_ACT_LEFT, WD_ACT_RIGHT, WD_ACT_CLOSE } WdAction;
/* The actions list of an item, in the order of the locked picture. Returns the count. */
int  wd_item_actions(const WdItem *it, const WdPins *pins, WdAction *out);
const char *wd_action_label(WdAction a);

/* ------------------------------------------------------------------ the application menu */
int  wd_menu_list(const WdApps *apps, const char *query, int *out, int cap);   /* indices of the apps shown (not NoDisplay) whose name contains the query, case-insensitive */
int  wd_menu_move(int cur, int count, int cols, int dir);                       /* 0 up, 1 down, 2 left, 3 right: by row and by cell, clamped; down from a short last row lands on the last cell */
int  wd_menu_top_row(int cur, int cols, int rows_visible, int top_row);         /* the first visible row that keeps the selection on screen */

#endif
