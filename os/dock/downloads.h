/* What the shell does when a download finishes (docs/WISPOS-OS-DESIGN.md "Downloads"): decide from the file's name and its first bytes what it is, and what to offer. Pure functions, no
 * I/O (the shell reads the first bytes and watches the folder), tested in os/dock/test_downloads.c. The offers are carried out by wisp-download-action (os/rootfs/usr/bin).
 * A downloaded file is untrusted: nothing here opens, runs or trusts it, and a name that comes from outside is only ever shortened, never used as a path. */
#ifndef WISPOS_DOCK_DOWNLOADS_H
#define WISPOS_DOCK_DOWNLOADS_H

#include <stdbool.h>
#include <stddef.h>

typedef enum { DL_OTHER, DL_APPIMAGE_ARM, DL_APPIMAGE_X86, DL_ARCHIVE, DL_WINDOWS, DL_IMAGE } DlKind;
typedef enum { DL_ACT_NONE, DL_ACT_INSTALL, DL_ACT_EXTRACT, DL_ACT_OPEN } DlAction;

typedef struct { char title[48]; char text[96]; char label[16]; DlAction action; } DlOffer;

#define DL_HEAD 64                 /* the first bytes the classifier looks at */

/* The folder's own files that are not downloads (a browser's partial file, a hidden file, a backup): never offered. */
bool dl_ignore(const char *name);
/* What it is, from its name (the last path component is used) and its first n bytes (n up to DL_HEAD; fewer is fine). */
DlKind dl_classify(const char *name, const unsigned char *head, size_t n);
/* The offer for a file: the toast's title and text (the name shortened to fit), the label of the one action and which it is (none for an information-only toast). */
void dl_offer(DlKind kind, const char *name, DlOffer *out);
/* The name without its directory and its known extensions ("Foo-1.0-aarch64.AppImage" -> "Foo-1.0-aarch64"). */
void dl_stem(const char *name, char *out, size_t cap);

#endif
