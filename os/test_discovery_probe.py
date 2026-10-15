"""os/dev/diag/discovery-probe.sh: the read-only discovery probe (docs/WISPOS-DISCOVERY-PLAN.md).

Its one promise is that it changes NOTHING on the handheld (operator, 2026-10-14: verify the concerns on the hardware before making changes). The text that runs on the device is read through
`--print-remote` and checked against a deny-list of every command and redirection that writes or changes state; each deny rule is shown to fire on a mutant line, so a rule that stopped
working fails here. The script is also run end to end through a fake `ssh` that executes the remote text locally with an empty PATH (every command "not found": the structure, the
section split and the exit status are what is under test), and the output-directory guard is checked."""
import os, re, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / 'dev/diag/discovery-probe.sh'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))

# Commands that write or change state, as words; and sub-commands/verbs of read-mostly tools that do.
# a command word counts only where a command starts (line start, after ; & | ( then do, or as the argument of the probe's cmd/t/timeout helpers), so names like wisp-tf2-mount or steamos-reboot are not commands
CMDPOS = r'(?:^\s*|[;&|(]\s*|\bcmd\s+|\bt\s+|\btimeout\s+\d+\s+|\bthen\s+|\bdo\s+)'
DENY_WORDS = CMDPOS + r'(?:rm|rmdir|mv|cp|tee|dd|mkdir|touch|chmod|chown|ln|mount|umount|kill|pkill|killall|reboot|poweroff|halt|shutdown|modprobe|rmmod|insmod|mkfs\.\w+|mkswap|swapon|swapoff|sed -i|truncate|wget|curl|scp)(?=\s|$|;)'
DENY_VERBS = [
    r'\bsystemctl\s+(?:--\S+\s+)*(?:start|stop|restart|reload|reload-or-restart|try-restart|enable|disable|mask|unmask|daemon-reload|daemon-reexec|kill|set-\S+|edit|isolate|reset-failed|suspend|hibernate|poweroff|reboot)\b',
    r'\bbusctl\b[^\n]*\b(?:call|set-property|emit|monitor|capture)\b',
    r'\bpactl\s+(?:set-\S+|load-module|unload-module|move-\S+|suspend-\S+|subscribe)\b',
    r'\bwpctl\s+(?:set-\S+|clear-\S+|inspect-\S+--)\b',
    r'\bnmcli\b[^\n]*\b(?:connect|modify|delete|up|down|add|reload|monitor)\b',
    r'\bnmcli\b[^\n]*\bradio\s+\S+\s+(?:on|off)\b',
    r'\bbluetoothctl\b[^\n]*\b(?:power|pair|connect|disconnect|remove|scan|trust|block|agent|discoverable|pairable)\b',
    r'\btimedatectl\s+set-\S+', r'\bhostnamectl\s+set-\S+', r'\bloginctl\s+(?:lock|unlock|terminate|kill|poweroff|reboot|suspend)\S*',
    r'\bswaymsg\b(?![^\n]*-t\s+get_)', r'\bip\s+(?:link|addr|route)\s+(?:set|add|del)', r'\brfkill\s+(?:block|unblock|toggle)',
    r'\bjournalctl\b[^\n]*--(?:vacuum\S*|rotate|flush|sync)', r'\becho\b[^\n]*\s>', r'\bprintf\b[^\n]*\s>[^&]',
]
# A redirection that writes a file: a `>` that is not `2>&1`, `>&2`, `/dev/null`, a heredoc marker, or inside the `$ %s` prints.
REDIR = re.compile(r'(?<![0-9&>])>(?!&)(?!\s*/dev/null)')


def violations(text):
    """Lines of the remote text that write or change something."""
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split('#', 1)[0] if not line.lstrip().startswith('printf') else line
        stripped = re.sub(r"'[^']*'|\"[^\"]*\"", lambda m: ' ' * len(m.group(0)), code)      # words inside quotes are text, not commands
        if re.search(DENY_WORDS, stripped) or any(re.search(v, code) for v in DENY_VERBS) or REDIR.search(re.sub(r'2>&1|>&2|>\s*/dev/null|2>\s*/dev/null', '', stripped)):
            bad.append((n, line.strip()))
    return bad


def remote_text():
    return subprocess.run(['bash', str(PROBE), '--print-remote'], capture_output=True, text=True, check=True).stdout


class ProbeTests(unittest.TestCase):
    def test_the_remote_text_parses_as_posix_sh(self):
        r = subprocess.run(['sh', '-n'], input=remote_text(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_remote_text_changes_nothing(self):
        text = remote_text()
        self.assertGreater(len(text.splitlines()), 60, 'the remote text is suspiciously short')
        self.assertEqual(violations(text), [])

    def test_every_deny_rule_fires_on_a_mutant(self):
        # Mutation check: a rule that no longer matches would let a writing line through unnoticed.
        base = remote_text()
        mutants = [
            'rm -f /storage/x', 'mv /a /b', 'cp /a /b', 'echo 1 > /sys/class/backlight/x/brightness', 'cat /etc/os-release > /tmp/x', 'tee /tmp/x', 'dd if=/dev/zero of=/tmp/x',
            'mkdir -p /storage/x', 'touch /storage/x', 'mount -o remount,rw /', 'umount /flash', 'kill 1', 'pkill steam', 'reboot', 'systemctl restart sway', 'systemctl stop wisp-steam',
            'systemctl daemon-reload', 'swaymsg output DSI-1 power off', 'swaymsg "output DSI-1 scale 2"', 'busctl --system call a / b c', 'busctl --system set-property a / b c s x',
            'pactl set-sink-volume @DEFAULT_SINK@ 50%', 'wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.5', 'nmcli device wifi connect X', 'nmcli connection delete X', 'nmcli radio wifi off',
            'bluetoothctl power off', 'bluetoothctl pair AA', 'timedatectl set-timezone UTC', 'hostnamectl set-hostname x', 'loginctl poweroff', 'ip link set wlan0 down', 'rfkill block all',
            'journalctl --vacuum-size=1M', 'sed -i s/a/b/ /etc/x', 'modprobe zram', 'swapoff -a', 'curl http://x', 'printf x > /tmp/x',
        ]
        missed = [m for m in mutants if not violations(base + '\n' + m)]
        self.assertEqual(missed, [], 'deny rules that did not fire on a writing line')
        self.assertEqual(violations(base), [])

    def test_the_innocent_forms_are_not_flagged(self):
        for ok in ['cmd busctl --system list --no-pager', 'cmd swaymsg -t get_outputs', 'cmd systemctl status wisp-steam --no-pager', 'cmd systemctl is-active sway', 'cmd pactl list short sinks',
                   'cmd nmcli -t general', 'cmd bluetoothctl show', 'cmd journalctl -b -p warning --no-pager -n 200', 'x=$(cat /sys/x 2>/dev/null)', 'tr "\\0" " " < /proc/1/cmdline 2>&1']:
            self.assertEqual(violations(ok), [], ok)

    def test_it_stops_before_touching_anything_without_a_hdd_output_directory(self):
        env = dict(os.environ, WISP_DEV_HOST='203.0.113.1')
        r = subprocess.run(['bash', str(PROBE), '/tmp/probe-test-not-on-hdd'], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2); self.assertIn('HDD', r.stderr); self.assertFalse(Path('/tmp/probe-test-not-on-hdd').exists())

    def test_it_needs_a_host(self):
        env = {k: v for k, v in os.environ.items() if k != 'WISP_DEV_HOST'}
        r = subprocess.run(['bash', str(PROBE), str(WORK / 'tmp/never')], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0); self.assertIn('WISP_DEV_HOST', r.stderr)

    def test_run_end_to_end_through_a_fake_ssh_splits_the_sections(self):
        if not (WORK / 'tmp').is_dir(): self.skipTest('no work directory at %s' % WORK)
        with tempfile.TemporaryDirectory(dir=WORK / 'tmp') as d:
            d = Path(d); (d / 'bin').mkdir()
            # a fake ssh that "logs in" by running the standard input locally with an EMPTY PATH: nothing can execute but shell builtins, so nothing is read or changed
            (d / 'bin/ssh').write_text('#!/bin/sh\nexec env -i PATH=/nonexistent /bin/sh -s\n'); (d / 'bin/ssh').chmod(0o755)
            out = d / 'out'
            env = dict(os.environ, PATH=str(d / 'bin') + ':' + os.environ['PATH'], WISP_DEV_HOST='fake')
            r = subprocess.run(['bash', str(PROBE), str(out)], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            index = (out / 'INDEX.txt').read_text().split()
            for sec in ('identity', 'steam-process-and-flags', 'steam-what-it-found', 'dbus-system', 'power-battery-suspend-rtc', 'backlight-display', 'audio', 'video-engine-hdmi-memory',
                        'network-bluetooth', 'storage-sd', 'steamos-helpers-steam-may-call', 'session-and-ui-state', 'journal-warnings'):
                self.assertIn(sec, index); self.assertTrue((out / ('%s.txt' % sec)).is_file(), sec)
            self.assertIn('### identity', (out / 'probe.txt').read_text())
            self.assertEqual(sorted(p.name for p in d.iterdir()), ['bin', 'out'])        # nothing else was created


if __name__ == '__main__':
    unittest.main()
