The WispOS badge as ASCII art, generated from the badge artwork by tools/make-ascii-badge.py (never edited by hand).
WispOS identifies itself in /etc/os-release (ID=wispos, NAME=WispOS, PRETTY_NAME=WispOS <version>), which is what neofetch and fastfetch print as the OS name.
Neither tool knows the WispOS logo by itself, so give it the file:

  neofetch --ascii /usr/share/wispos/ascii/wispos-neofetch.txt --ascii_colors 4 6
  fastfetch --logo /usr/share/wispos/ascii/wispos-fastfetch.txt --logo-type file --logo-color-1 blue --logo-color-2 cyan
  fastfetch -c wispos          (the preset /usr/share/fastfetch/presets/wispos.jsonc does the same)

Colours: 1 = the solid circle (blue), 2 = the wisps (cyan). The neofetch file uses neofetch's ${c1}/${c2} markers, the fastfetch file uses $1/$2.
