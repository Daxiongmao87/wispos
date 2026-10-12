/* The boot screen as one frame of pixels, for the first-run hand-over (wisp-setup --intro): the setup program has to start from the picture the boot screen was showing, and that picture
 * is drawn by the SAME code (os/splash/scene.h) as the framebuffer splash and the Wayland boot screen, so the first frame is identical to what was on the panel. A separate file
 * because scene.h and the setup program both name functions rrect, text, spinner and hint. */
#include <stdint.h>
#include <string.h>

#include "wisp-ui.h"
#include "wisp-ui-assets.h"
#include "scene.h"
#include "bootframe.h"

void bootframe_render(uint32_t *px, int w, int h, const char *status, const char *detail, int dev, int spin_frame)
{
    struct canvas c = { px, w, h, w };
    struct screen s;
    memset(&s, 0, sizeof s);
    snprintf(s.title, sizeof s.title, "Starting WispOS");
    snprintf(s.status, sizeof s.status, "%s", status);
    snprintf(s.detail, sizeof s.detail, "%s", detail ? detail : "");
    snprintf(s.hint_key, sizeof s.hint_key, "SELECT");
    snprintf(s.hint_label, sizeof s.hint_label, "Boot log");
    s.dev = dev != 0;
    draw_screen(&c, &s);
    spinner(&c, w / 2, WISP_UI_BOOT_SPIN_CY, WISP_UI_SPIN_ROW_R + 4, spin_frame % WISP_UI_SPIN_DOTS);
}
