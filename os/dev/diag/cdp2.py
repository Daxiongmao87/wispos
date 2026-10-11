# Attach DevTools EARLY (before a stall starts), keep the console log, and if the UI has not reached its main menu after WAIT s, sample the
# JS call stack with Debugger.pause (interrupt-based, works on a busy thread). usage: cdp2.py [wait_seconds]
import sys, time, json, collections
sys.argv = [sys.argv[0]] + sys.argv[1:2] if False else sys.argv
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 75
import cdplib
from cdplib import WS, targets
cdplib.PORT = 8080
t0 = time.time(); conns = {}; console = collections.deque(maxlen=60); seen = set()
def attach(t):
    path = '/' + t['webSocketDebuggerUrl'].split('/', 3)[3]
    w = WS(path, timeout=6)
    w.call('Debugger.enable'); w.call('Runtime.enable'); w.call('Log.enable')
    return w
def drain(w, secs=0.2):
    w.s.settimeout(secs)
    try:
        while True:
            m = w.recv()
            if m.get('method') == 'Runtime.consoleAPICalled':
                p = m['params']; txt = ' '.join(str(a.get('value', a.get('description', ''))) for a in p.get('args', []))[:200]
                console.append(f"{time.time()-t0:5.1f}s [{p.get('type')}] {txt}")
            elif m.get('method') == 'Runtime.exceptionThrown':
                d = m['params']['exceptionDetails']; console.append(f"{time.time()-t0:5.1f}s [exception] {d.get('text')} {(d.get('exception') or {}).get('description','')[:160]}")
            elif m.get('method') == 'Log.entryAdded':
                e = m['params']['entry']; console.append(f"{time.time()-t0:5.1f}s [log {e.get('level')}] {e.get('text','')[:180]} {e.get('url','')[-40:]}")
            else: w.events.append(m)
    except Exception: pass
    w.s.settimeout(6)
menu = False
while time.time() - t0 < WAIT + 20:
    try: ts = targets()
    except Exception: time.sleep(1); continue
    for t in ts:
        if t.get('id') in seen or not t.get('webSocketDebuggerUrl'): continue
        if t.get('type') != 'page': continue
        try: conns[t['id']] = (t, attach(t)); seen.add(t['id']); console.append(f"{time.time()-t0:5.1f}s attached: {t.get('title','')[:30]} | {t.get('url','')[:80]}")
        except Exception as e: console.append(f"{time.time()-t0:5.1f}s attach failed: {e}")
    menu = any('MainMenu' in (t.get('url','') + t.get('title','')) or 'browserviewpopup' in t.get('url','') for t in ts)
    for tid, (t, w) in list(conns.items()): drain(w, 0.3)
    if time.time() - t0 >= WAIT: break
    if menu and time.time() - t0 > 25: break
    time.sleep(0.5)
print(f"after {time.time()-t0:.0f}s: main-menu targets present: {menu}; attached to {len(conns)} page(s)")
if menu:
    print("UI is up; no stall this time.")
else:
    print("STALLED: sampling JS stacks with Debugger.pause")
    for tid, (t, w) in conns.items():
        print(f"== {t.get('title','')[:35]} | {t.get('url','')[:80]}")
        frames_seen = collections.Counter()
        for k in range(10):
            try:
                w.call('Debugger.pause', wait=6); ev = w.wait_event('Debugger.paused', 6)
                if not ev: print(f"  sample {k}: no pause within 6 s"); continue
                fr = ev['params']['callFrames'][:24]
                key = ' <- '.join(f"{(f.get('functionName') or '(anon)')[:30]}@{f['url'].split('/')[-1][:30]}:{f['location']['lineNumber']}" for f in fr[:4])
                frames_seen[key] += 1
                if k < 3:
                    print(f"  sample {k} stack ({len(fr)} frames):"); [print('     ', (f.get('functionName') or '(anon)')[:50], f['url'][-60:], f"{f['location']['lineNumber']}:{f['location']['columnNumber']}") for f in fr]
                if k < 2:
                    shown = set()
                    for f in fr[:6]:
                        loc = f['location']; key = (loc['scriptId'], loc['lineNumber'], loc['columnNumber'] // 400)
                        if key in shown or len(shown) >= 3: continue
                        shown.add(key)
                        try:
                            src = w.call('Debugger.getScriptSource', {'scriptId': loc['scriptId']}, wait=20)['result']['scriptSource']
                            line = src.split('\n')[loc['lineNumber']] if loc['lineNumber'] < src.count('\n') + 1 else ''
                            c = loc['columnNumber']; print(f"   --- source around {f.get('functionName') or '(anon)'} {loc['lineNumber']}:{c} (script {len(src)} chars):"); print('      ' + line[max(0, c - 450): c + 350].replace('\n', ' '))
                        except Exception as e: print('   source fetch failed:', type(e).__name__, e)
                w.call('Debugger.resume', wait=6); time.sleep(1.2)
            except Exception as e: print(f"  sample {k}: {type(e).__name__} {e}"); break
        print("  distinct top-of-stack signatures:"); [print(f"    {c}x {k}") for k, c in frames_seen.most_common(6)]
print("== console / exceptions collected (last 40):"); [print("  ", l) for l in list(console)[-40:]]
