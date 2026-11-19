#!/usr/bin/env python3
import ctypes
import dis
import errno
import fcntl
import http.client
import io
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import time

EVIDENCE_DIR = "/working_dir/evidence"
os.makedirs(EVIDENCE_DIR, exist_ok=True)


def run_cmd(cmd, timeout=10):
    try:
        p = subprocess.run(
            cmd,
            shell=isinstance(cmd, str),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except Exception as e:
        return {"rc": -1, "stdout": "", "stderr": str(e)}


# ==========================================
# TRACK 1: THE CAGE (Confinement, I/O, POSIX)
# ==========================================
def probe_track1():
    res = {}
    with open("/proc/uptime") as f:
        res["uptime"] = f.read().strip()
    res["uname"] = platform.uname()._asdict()

    # 1. Storage I/O Benchmark
    def bench_dir(d):
        p4 = os.path.join(d, "bench_4k.bin")
        latencies = []
        payload4k = b"A" * 4096
        for _ in range(50):
            t0 = time.perf_counter()
            fd = os.open(p4, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            os.write(fd, payload4k)
            os.close(fd)
            latencies.append((time.perf_counter() - t0) * 1000.0)
        os.remove(p4)
        lat_sorted = sorted(latencies)
        p95 = lat_sorted[int(0.95 * len(lat_sorted))]

        p10m = os.path.join(d, "bench_10m.bin")
        payload10m = b"B" * (10 * 1024 * 1024)
        t0 = time.perf_counter()
        with open(p10m, "wb") as f:
            f.write(payload10m)
        w_sec = time.perf_counter() - t0

        t0 = time.perf_counter()
        with open(p10m, "rb") as f:
            _ = f.read()
        r_sec = time.perf_counter() - t0
        os.remove(p10m)

        return {
            "write_4k_avg_ms": round(statistics.mean(latencies), 3),
            "write_4k_p95_ms": round(p95, 3),
            "seq_write_10m_MBps": round(10.0 / w_sec, 2),
            "seq_read_10m_MBps": round(10.0 / r_sec, 2),
        }

    res["io_benchmark"] = {
        "/tmp": bench_dir("/tmp"),
        "/working_dir": bench_dir("/working_dir"),
    }

    # 2. POSIX Semantics on /tmp vs /working_dir
    libc = ctypes.CDLL("libc.so.6", use_errno=True)

    def test_posix(d):
        out = {}
        fpath = os.path.join(d, "posix_test.txt")
        fd = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.write(fd, b"test")
        try:
            os.fsync(fd)
            out["fsync"] = "SUCCESS"
        except OSError as e:
            out["fsync"] = f"FAILED: [Errno {e.errno}] {e.strerror}"

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            out["flock"] = "SUCCESS"
        except OSError as e:
            out["flock"] = f"FAILED: {e}"
        os.close(fd)

        try:
            fd2 = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            os.close(fd2)
            out["O_CREAT_O_EXCL"] = "UNEXPECTED_SUCCESS"
        except FileExistsError:
            out["O_CREAT_O_EXCL"] = "ENFORCED (FileExistsError)"

        fpath2 = os.path.join(d, "posix_test2.txt")
        with open(fpath2, "wb") as f:
            f.write(b"new")
        os.replace(fpath2, fpath)
        out["os_replace"] = "SUCCESS"
        os.remove(fpath)

        fifo_p = os.path.join(d, "posix_fifo")
        try:
            if os.path.exists(fifo_p):
                os.remove(fifo_p)
            os.mkfifo(fifo_p)
            ffd = os.open(fifo_p, os.O_RDONLY | os.O_NONBLOCK)
            os.close(ffd)
            out["mkfifo_nonblock"] = "SUCCESS"
            os.remove(fifo_p)
        except OSError as e:
            out["mkfifo_nonblock"] = f"FAILED: {e}"

        ifd = libc.inotify_init1(0)
        if ifd >= 0:
            wd = libc.inotify_add_watch(ifd, d.encode(), 0x102)
            err = ctypes.get_errno()
            out["inotify"] = f"fd={ifd}, wd={wd}, errno={err}"
            os.close(ifd)
        else:
            out["inotify"] = f"FAILED: errno={ctypes.get_errno()}"
        return out

    res["posix_semantics"] = {
        "/tmp": test_posix("/tmp"),
        "/working_dir": test_posix("/working_dir"),
    }

    # 3. Clocks, Dumpability, Sysctls, Orphan Reparenting
    class Timespec(ctypes.Structure):
        _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]

    clocks = {}
    for name, cid in [("CLOCK_REALTIME", 0), ("CLOCK_MONOTONIC", 1), ("CLOCK_BOOTTIME", 7)]:
        ts = Timespec()
        rc = libc.clock_getres(cid, ctypes.byref(ts))
        clocks[name] = {"rc": rc, "sec": ts.tv_sec, "nsec": ts.tv_nsec}

    req = Timespec(0, 500_000)  # 500 us
    t0 = time.perf_counter_ns()
    libc.nanosleep(ctypes.byref(req), None)
    elapsed_us = (time.perf_counter_ns() - t0) / 1000.0
    clocks["nanosleep_500us_actual_us"] = round(elapsed_us, 2)
    res["clocks"] = clocks

    res["prctl_dumpable"] = libc.prctl(3, 0, 0, 0, 0)  # PR_GET_DUMPABLE=3
    sysctls = sorted(os.listdir("/proc/sys/kernel")) if os.path.isdir("/proc/sys/kernel") else []
    res["proc_sys_kernel"] = sysctls

    # Double-fork orphan reparenting check
    r_fd, w_fd = os.pipe()
    pid1 = os.fork()
    if pid1 == 0:
        pid2 = os.fork()
        if pid2 == 0:
            time.sleep(0.05)
            os.write(w_fd, str(os.getppid()).encode())
            os._exit(0)
        os._exit(0)
    os.close(w_fd)
    os.waitpid(pid1, 0)
    grandchild_ppid = os.read(r_fd, 32).decode().strip()
    os.close(r_fd)
    res["double_fork_grandchild_ppid"] = grandchild_ppid

    # Mounts & /dev/shm
    with open("/proc/mounts") as f:
        res["mounts"] = [line.strip() for line in f.readlines()]
    res["df_shm"] = run_cmd("df -h /dev/shm")["stdout"].strip()

    with open(os.path.join(EVIDENCE_DIR, "track1_cage.json"), "w") as f:
        json.dump(res, f, indent=2)
    return res


# ==========================================
# TRACK 2: THE MACHINERY (dynamo_exec, X11/VNC)
# ==========================================
class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        self.sock = sock


def unix_req(method, path, body=None):
    conn = UnixHTTPConnection("/tmp/shell.sock")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return resp.status, data


def probe_track2():
    res = {}
    with open("/proc/1/status") as f:
        res["pid1_status"] = {
            k.strip(): v.strip()
            for line in f
            if ":" in line
            for k, v in [line.split(":", 1)]
            if k.strip() in ("Name", "State", "Pid", "Threads", "VmRSS", "VmSize")
        }
    with open("/proc/net/unix") as f:
        unix_txt = f.read()
    with open(os.path.join(EVIDENCE_DIR, "proc_net_unix.txt"), "w") as f:
        f.write(unix_txt)
    res["unix_sockets_count"] = len(unix_txt.splitlines()) - 1

    # Query /tmp/shell.sock OpenAPI
    try:
        st, oa = unix_req("GET", "/openapi.json")
        res["openapi_status"] = st
        if st == 200:
            with open(os.path.join(EVIDENCE_DIR, "openapi.json"), "w") as f:
                f.write(oa)
    except Exception as e:
        res["openapi_error"] = str(e)

    # Disassemble dynamo_exec.block_localhost & inspect routes via /execute_python
    py_probe = """
import sys, dis, io, json
mod = sys.modules.get('dynamo_exec')
out = {'has_dynamo_exec': mod is not None}
if mod:
    buf = io.StringIO()
    dis.dis(mod.block_localhost, file=buf)
    out['block_localhost_dis'] = buf.getvalue()
    out['routes'] = [
        {
            'methods': sorted(list(getattr(r, 'methods', []) or [])),
            'path': getattr(r, 'path', None),
            'name': getattr(r, 'name', None),
            'include_in_schema': getattr(r, 'include_in_schema', None),
        }
        for r in mod.http_app.routes
    ]
    out['daemon_sys_modules_count'] = len(sys.modules)
print(json.dumps(out))
"""
    try:
        st, body = unix_req("POST", "/execute_python", {"code": py_probe})
        res["execute_python_status"] = st
        parsed = json.loads(body)
        stdout_str = parsed.get("stdout", "")
        if stdout_str:
            info = json.loads(stdout_str)
            with open(os.path.join(EVIDENCE_DIR, "block_localhost_dis.txt"), "w") as f:
                f.write(info.pop("block_localhost_dis", ""))
            res["dynamo_exec"] = info
        else:
            res["execute_python_raw"] = parsed
    except Exception as e:
        res["execute_python_error"] = str(e)

    # Display / RFB / Toolchain
    res["xwininfo"] = run_cmd("DISPLAY=:1 xwininfo -root -tree")["stdout"].strip()
    res["xdpyinfo_shm"] = run_cmd("DISPLAY=:1 xdpyinfo -ext MIT-SHM")["stdout"].strip()

    try:
        s = socket.create_connection(("127.0.0.1", 5901), timeout=3)
        banner = s.recv(12).decode("ascii", errors="replace").strip()
        s.sendall(b"RFB 003.008\n")
        sec_cnt = s.recv(1)[0]
        sec_types = list(s.recv(sec_cnt))
        s.close()
        res["rfb_5901"] = {"banner": banner, "security_types": sec_types}
    except Exception as e:
        res["rfb_5901_error"] = str(e)

    res["node_version"] = run_cmd("node -v")["stdout"].strip()
    res["npm_version"] = run_cmd("npm -v")["stdout"].strip()
    res["fc_list_count"] = len(run_cmd("fc-list :family")["stdout"].splitlines())

    with open(os.path.join(EVIDENCE_DIR, "track2_machinery.json"), "w") as f:
        json.dump(res, f, indent=2)
    return res


# ==========================================
# TRACK 3: THE MIND (mfs / GRTE)
# ==========================================
def probe_track3():
    res = {}
    res["mfs_status"] = run_cmd("mfs status")
    res["mfs_help"] = run_cmd("mfs --help")["stdout"]
    res["grte_link"] = os.path.realpath("/usr/grte/v5/lib64/ld-linux-x86-64.so.2") if os.path.exists("/usr/grte/v5/lib64/ld-linux-x86-64.so.2") else None
    res["ipc_dir"] = os.listdir("/ipc") if os.path.isdir("/ipc") else "MISSING"

    strings_out = run_cmd("strings /usr/bin/mfs | grep -E 'learning\\.gemini\\.memory|--(fireaxe|loas|lockservice|rpc_use|dapper|census|streamz)' | sort -u")
    with open(os.path.join(EVIDENCE_DIR, "mfs_symbols.txt"), "w") as f:
        f.write(strings_out["stdout"])
    res["mfs_symbols_count"] = len(strings_out["stdout"].splitlines())

    with open(os.path.join(EVIDENCE_DIR, "mfs_telemetry.json"), "w") as f:
        json.dump(res, f, indent=2)
    return res


if __name__ == "__main__":
    t1 = probe_track1()
    t2 = probe_track2()
    t3 = probe_track3()
    summary = {
        "session_dir": os.getcwd(),
        "uptime": t1["uptime"],
        "io_benchmark": t1["io_benchmark"],
        "posix_working_dir": t1["posix_semantics"]["/working_dir"],
        "posix_tmp": t1["posix_semantics"]["/tmp"],
        "nanosleep_500us_actual_us": t1["clocks"]["nanosleep_500us_actual_us"],
        "double_fork_grandchild_ppid": t1["double_fork_grandchild_ppid"],
        "dynamo_exec": t2.get("dynamo_exec"),
        "rfb_5901": t2.get("rfb_5901"),
        "mfs_status": t3["mfs_status"]["stdout"].strip(),
        "ipc_dir": t3["ipc_dir"],
        "mfs_symbols_count": t3["mfs_symbols_count"],
    }
    print(json.dumps(summary, indent=2))
