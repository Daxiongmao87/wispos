/* One frame of the boot screen (os/setup/bootframe.c): XRGB8888, w x h pixels, the spinner at `spin_frame` (the splash's frame number). status/detail are the lines on the screen. */
#ifndef WISP_SETUP_BOOTFRAME_H
#define WISP_SETUP_BOOTFRAME_H

#include <stdint.h>

void bootframe_render(uint32_t *px, int w, int h, const char *status, const char *detail, int dev, int spin_frame);

#endif
