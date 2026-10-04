#!/usr/bin/env python3
"""
syscall_probe.py - Empirical 13-Syscall Probe Harness for gVisor Sentry
Used by sys-dissect to map syscall dispatch table and capability boundaries.
"""

import os
import ctypes
import json

libc = ctypes.CDLL(None, use_errno=True)

tests = [
    ('ptrace', 101, [0, 0, 0, 0]),
    ('syslog', 103, [2, 0, 0]),
    ('mount', 165, [ctypes.c_char_p(b'none'), ctypes.c_char_p(b'/tmp'), ctypes.c_char_p(b'tmpfs'), 0, 0]),
    ('reboot', 169, [0xfee1dead, 672274793, 0x1234567, 0]),
    ('kexec_load', 246, [0, 0, 0, 0]),
    ('unshare', 272, [0]),
    ('perf_event_open', 298, [0, 0, -1, -1, 0]),
    ('memfd_create', 319, [ctypes.c_char_p(b'audit_test'), 0]),
    ('bpf', 321, [0, 0, 0]),
    ('userfaultfd', 323, [0]),
    ('io_uring_setup', 425, [1, 0]),
    ('clone3', 435, [0, 0]),
    ('pidfd_open', 434, [os.getpid(), 0])
]

results = {}
for sname, snum, sargs in tests:
    # Pad to standard 6 syscall arguments
    while len(sargs) < 6:
        sargs.append(0)
    ret = libc.syscall(snum, *sargs)
    err = ctypes.get_errno()
    results[sname] = {
        'syscall_nr': snum,
        'return_code': ret,
        'errno': err,
        'status': os.strerror(err) if ret == -1 else 'SUCCESS'
    }

print(json.dumps(results, indent=2))
