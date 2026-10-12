// Firefox preferences for the handheld (copied into the profile by wisp-browser; read at every start, so a change here reaches a profile that already exists).
// Measured on the device: none of them yet. Why each one is here (docs/WISPOS-OS-DESIGN.md "Web browsing in desktop mode"):
// the panel is 720x480 at about 254 ppi: page and interface are scaled to about 410 CSS pixels wide, a phone in landscape
user_pref("layout.css.devPixelsPerPx", "1.75");
// the SD card is written rarely: no disk cache, a session save every ten minutes
user_pref("browser.cache.disk.enable", false);
user_pref("browser.sessionstore.interval", 600000);
// memory is 1 GiB for everything: a small memory cache, few cached pages, background tabs unloaded when memory runs low, few content processes
user_pref("browser.cache.memory.capacity", 32768);
user_pref("browser.sessionhistory.max_total_viewers", 1);
user_pref("browser.tabs.unloadOnLowMemory", true);
user_pref("dom.ipc.processCount", 2);
// our start page, the downloads folder the shell watches, and no first-run or default-browser questions
user_pref("browser.startup.homepage", "file:///usr/share/wispos/startpage/index.html");
user_pref("browser.startup.page", 1);
user_pref("browser.download.folderList", 2);
user_pref("browser.download.dir", "/storage/downloads");
user_pref("browser.download.useDownloadDir", true);
user_pref("browser.download.alwaysOpenPanel", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.tabs.warnOnClose", false);
// the interface follows the system's dark look, scrolling is smooth with the pad's wheel
user_pref("ui.systemUsesDarkTheme", 1);
user_pref("general.smoothScroll", true);
