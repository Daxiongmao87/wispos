/* The boot screens' input: a toggle key (SELECT) on any evdev device that has it, read directly (the boot log toggle works before and without a compositor). Shared by the
 * framebuffer splash and the Wayland boot screen. */
#ifndef WISPOS_SPLASH_EVINPUT_H
#define WISPOS_SPLASH_EVINPUT_H

#include <dirent.h>
#include <fcntl.h>
#include <linux/input.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAXDEV 16
static int in_fd[MAXDEV], in_n;

static inline bool has_key(int fd, int code)
{
    unsigned char bits[(KEY_MAX + 7) / 8 + 1] = { 0 };
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof bits), bits) < 0) return false;
    return bits[code / 8] & (1 << (code % 8));
}

static inline void scan_input(const char *dir, int code)
{
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (strncmp(e->d_name, "event", 5) || in_n >= MAXDEV) continue;
        char p[512]; snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
        int fd = open(p, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        bool dup = false;                                  /* same device number already open? */
        struct stat a, b; fstat(fd, &a);
        for (int i = 0; i < in_n; i++) if (!fstat(in_fd[i], &b) && a.st_rdev == b.st_rdev) dup = true;
        if (dup || !has_key(fd, code)) { close(fd); continue; }
        in_fd[in_n++] = fd;
    }
    closedir(d);
}

#endif
