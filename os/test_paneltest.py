"""Host tests for the dev panel test: which frame rates are shown per refresh rate, and the pad trigger (SELECT+START held 3 s)."""
import os, struct, sys, threading, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dev', 'diag'))
import paneltest as P


def ev(code, value): return struct.pack('qqHHi', 0, 0, 1, code, value)


class PanelTest(unittest.TestCase):
    def test_the_watcher_runs_the_test_in_a_separate_process_so_the_display_is_released(self):
        calls = []; real = P.subprocess.run; P.subprocess.run = lambda cmd, **kw: calls.append(cmd)
        try:
            r, w = os.pipe(); os.set_blocking(r, False)
            t = threading.Thread(target=P.watch, kwargs=dict(fd=r, max_seconds=4.5)); t.start()
            os.write(w, ev(P.BTN_SELECT, 1) + ev(P.BTN_START, 1)); t.join(); os.close(r); os.close(w)
        finally: P.subprocess.run = real
        self.assertEqual(len(calls), 1); self.assertEqual(calls[0][0], sys.executable); self.assertEqual(calls[0][-1], 'run')

    def test_frame_rates_divide_the_refresh_rate_evenly_and_are_at_least_10(self):
        self.assertEqual(P.divisors(60), [10, 12, 15, 20, 30, 60])
        self.assertEqual(P.divisors(48), [12, 16, 24, 48])
        self.assertEqual(P.divisors(31), [31])
        for hz in P.HZ:
            for fps in P.divisors(hz): self.assertEqual(hz % fps, 0); self.assertGreaterEqual(fps, 10)

    def _watch(self, script, seconds):
        r, w = os.pipe(); os.set_blocking(r, False); calls = []
        t = threading.Thread(target=P.watch, kwargs=dict(fd=r, runner=lambda: calls.append(1), max_seconds=seconds)); t.start()
        for delay, data in script: time.sleep(delay); os.write(w, data)
        t.join(); os.close(r); os.close(w); return calls

    def test_holding_select_and_start_for_3_seconds_starts_the_test_once(self):
        calls = self._watch([(0.2, ev(P.BTN_SELECT, 1)), (0.1, ev(P.BTN_START, 1))], 4.5)
        self.assertEqual(len(calls), 1)

    def test_releasing_before_3_seconds_or_pressing_one_button_does_not_start_it(self):
        self.assertEqual(self._watch([(0.2, ev(P.BTN_SELECT, 1)), (0.1, ev(P.BTN_START, 1)), (1.5, ev(P.BTN_START, 0))], 4.5), [])
        self.assertEqual(self._watch([(0.2, ev(P.BTN_SELECT, 1))], 4.0), [])
        self.assertEqual(self._watch([(0.2, ev(P.BTN_B, 1)), (0.1, ev(P.BTN_START, 1))], 4.0), [])


if __name__ == '__main__': unittest.main()
