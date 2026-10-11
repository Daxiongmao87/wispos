#!/usr/bin/python3
"""Dev: where is a busy thread? Samples the program counter of one thread with ptrace and prints a histogram by mapped file (aarch64 only; run ON the device as root, Python 3.14 with ctypes).
usage: pc-sample.py PID [TID=PID] [SAMPLES=60] [INTERVAL_S=0.2]
Why: 2026-10-10, a Steam-launched Worms match load sat on a loading screen for minutes with WA.exe's main thread running (state R) at ~90% CPU, 70% of it kernel time, no page faults,
no swap traffic and no syscall in flight: i.e. exceptions or signals. This tells which code the thread is in at the moment of each sample (the FEX JIT's anonymous code, FEX itself,
Wine, the SIGILL fallback `libwispos-ldadd.so`, libc ...). Each sample is: PTRACE_SEIZE, PTRACE_INTERRUPT, wait, GETREGSET(NT_PRSTATUS), PTRACE_DETACH, so the thread is stopped for well
under a millisecond and is never left stopped (a `finally` detaches). It perturbs the thread slightly and says nothing about time spent in the kernel itself: a thread in a signal
handler shows the handler's PC; a thread inside the kernel stops at its user-space return address.
"""
import collections, ctypes, os, re, struct, sys, time

PTRACE_SEIZE, PTRACE_INTERRUPT, PTRACE_GETREGSET, PTRACE_DETACH = 0x4206, 0x4207, 0x4204, 17
NT_PRSTATUS, WALL = 1, 0x40000000
libc = ctypes.CDLL(None, use_errno=True)
libc.ptrace.restype = ctypes.c_long
libc.ptrace.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]


class Iovec(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('len', ctypes.c_size_t)]


def maps(pid):
    out = []
    for line in open('/proc/%d/maps' % pid):
        m = re.match(r'([0-9a-f]+)-([0-9a-f]+) (\S+) ([0-9a-f]+) \S+ \S+\s*(.*)', line)
        if m: out.append((int(m.group(1), 16), int(m.group(2), 16), m.group(3), int(m.group(4), 16), m.group(5).strip()))
    return out


def where(regions, pc):
    for lo, hi, perms, off, path in regions:
        if lo <= pc < hi:
            return ('%s%s' % (os.path.basename(path) or '[anon]', ' ' + perms if not path else ''), pc - lo + off)
    return ('[unmapped]', pc)


def sample_pc(tid):
    if libc.ptrace(PTRACE_SEIZE, tid, None, None) != 0: raise OSError(ctypes.get_errno(), 'SEIZE')
    try:
        if libc.ptrace(PTRACE_INTERRUPT, tid, None, None) != 0: raise OSError(ctypes.get_errno(), 'INTERRUPT')
        os.waitpid(tid, WALL)
        buf = ctypes.create_string_buffer(34 * 8)                   # user_pt_regs: regs[31], sp, pc, pstate
        iov = Iovec(ctypes.cast(buf, ctypes.c_void_p), len(buf))
        if libc.ptrace(PTRACE_GETREGSET, tid, NT_PRSTATUS, ctypes.byref(iov)) != 0: raise OSError(ctypes.get_errno(), 'GETREGSET')
        return struct.unpack_from('<Q', buf, 32 * 8)[0], struct.unpack_from('<Q', buf, 30 * 8)[0]      # pc, x30 (the link register: the caller of a leaf function such as a libc syscall wrapper)
    finally:
        libc.ptrace(PTRACE_DETACH, tid, None, None)


def main(argv):
    pid = int(argv[0]); tid = int(argv[1]) if len(argv) > 1 else pid
    n = int(argv[2]) if len(argv) > 2 else 60; dt = float(argv[3]) if len(argv) > 3 else 0.2
    regions = maps(pid); files = collections.Counter(); spots = collections.Counter(); callers = collections.Counter(); bad = 0
    for _ in range(n):
        try:
            pc, lr = sample_pc(tid)
        except OSError as e:
            bad += 1; continue
        name, off = where(regions, pc); cname, coff = where(regions, lr)
        files[name] += 1; spots[(name, off >> 6 << 6)] += 1         # 64-byte buckets: hot loops show as a few buckets
        callers[(name, off >> 6 << 6, cname, coff)] += 1
        time.sleep(dt)
    print('samples %d (failed %d) of thread %d in process %d' % (n - bad, bad, tid, pid))
    for name, c in files.most_common(8): print('  %3d%%  %s' % (100 * c // max(1, n - bad), name))
    print('hottest 64-byte buckets:')
    for (name, off), c in spots.most_common(8): print('  %3d  %s +0x%x' % (c, name, off))
    print('callers (x30) of the hottest spots:')
    for (name, off, cname, coff), c in callers.most_common(8): print('  %3d  %s +0x%x  called from  %s +0x%x' % (c, name, off, cname, coff))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
