"""Host tests for the display-mode piece of the gamescope stand-in (the pure logic; the X11 part is exercised on the device)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dev', 'diag'))
import gsmodes as G


class ModeTests(unittest.TestCase):
    def test_the_published_list_has_native_and_half_resolution_in_gamescopes_format(self):
        self.assertEqual(G.mode_list((720, 480), 60), '720x480@60 360x240@60')              # "WxH@R" separated by spaces, as update_mode_atoms builds it

    def test_the_saved_mode_is_read_the_way_gamescope_reads_it(self):
        self.assertEqual(G.parse_saved_mode('Anbernic RG34XXSP:360x240@60 0\n'), (360, 240, 60))
        self.assertEqual(G.parse_saved_mode('Anbernic RG34XXSP:720x480@60\n'), (720, 480, 60))                  # the broadcast-mode number is optional here
        self.assertEqual(G.parse_saved_mode('A:720x480@60 0\nB:360x240@60 0\n'), (360, 240, 60))                  # the last line wins
        for bad in ('', 'garbage', 'no colon 360x240@60', 'A:360x@60 0', 'A:axb@60'): self.assertIsNone(G.parse_saved_mode(bad), bad)

    def test_only_exact_halves_of_the_panel_become_scales(self):
        self.assertEqual(G.scale_for_mode(720, 480), 1)
        self.assertEqual(G.scale_for_mode(360, 240), 2)
        for w, h in ((480, 320), (640, 480), (1280, 800), (360, 480), (0, 0), (240, 160)): self.assertIsNone(G.scale_for_mode(w, h), (w, h))   # 1.5x / 3x / non-uniform: refused


if __name__ == '__main__': unittest.main()
