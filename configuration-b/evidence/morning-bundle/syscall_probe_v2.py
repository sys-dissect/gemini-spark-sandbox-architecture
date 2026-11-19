#!/usr/bin/env python3
"""Spark sandbox fresh probe, v2 (2026-11-19).

Changes vs v1 (2026-11-19 review):
- /execute_python response parsing fixed: the endpoint returns JSON keyed
  by msg_out (plus msg_err / exit_status_int), NOT stdout. v1's stdout
  branch never fired on this endpoint, so dynamo_routes.json and
  block_localhost_dis.txt had to be hand-derived from the raw body.
  v2 parses msg_out (with a stdout fallback for older sessions).
- New: runtime-variant verdict (gVisor vs virtio/Docker) from mounts+uname.
- New: /run/docker.sock connectivity test (read-only GET /version).
- New: GRTE check via readelf PT_INTERP, not just the /usr/grte path.
- New: chromium evidence capture (dev-shm script, GUI window, headless run).
- New: /execute_bash cwd parameter test (does the new field actually work?).
- mfs "symbols" count relabelled: it counts matching strings lines, not symbols.

Run inside the Spark container: python3 /working_dir/syscall_probe_v2.py
Evidence lands in /working_dir/evidence/. Zip that dir and send it back.
"""
import ctypes
import dis
import errno
import fcntl
import http.client
import io
import json
import os
import platform
import re
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


def detect_runtime_variant(mounts, uname):
    """Verdict from mounts + uname. Returns (variant, evidence list)."""
    mounts_txt = "\n".join(mounts)
    ev = []
    has_9p = "9p" in mounts_txt or "gofer" in mounts_txt.lower()
    has_vdb_xfs = "/dev/vdb" in mounts_txt and "xfs" in mounts_txt
    has_tmpfs_tmp = re.search(r"tmpfs /tmp ", mounts_txt) is not None
    has_overlay_tmp = re.search(r"overlay /tmp |overlayfs", mounts_txt) is not None
    has_docker = "/var/lib/docker/overlay2" in mounts_txt
    has_containerd = os.path.exists("/run/containerd/containerd.sock")
    has_runtimed = os.path.exists("/run/runtimed/guest_control.sock")
    release = uname.get("release", "")
    if has_9p:
        ev.append("9p/gofer mount present")
        variant = "A-gvisor"
    elif has_vdb_xfs:
        ev.append("/dev/vdb xfs mount present")
        variant = "B-virtio"
    else:
        variant = "unknown"
    if has_docker:
        ev.append("docker overlay2 lowerdirs")
    if has_containerd:
        ev.append("containerd socket present")
    if has_runtimed:
        ev.append("runtimed guest_control.sock present")
    if has_tmpfs_tmp:
        ev.append("/tmp is tmpfs")
    if has_overlay_tmp:
        ev.append("/tmp is overlayfs")
    ev.append("uname release: %s" % release)
    return variant, ev


# ==========================================
# TRACK 1: THE CAGE (Confinement, I/O, POSIX)
# ==========================================
def probe_track1():
    res = {}
    with open("/proc/uptime") as f:
        res["uptime"] = f.read().strip()
    res["uname"] = platform.uname()._asdict()

    # 1. Storage I/O Benchmark (open+write+close per 4K iteration)
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

    # 3. Clocks (nanosleep averaged over 20 samples now, not single-shot),
    # Dumpability, Sysctls, Orphan Reparenting
    class Timespec(ctypes.Structure):
        _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]

    clocks = {}
    for name, cid in [("CLOCK_REALTIME", 0), ("CLOCK_MONOTONIC", 1), ("CLOCK_BOOTTIME", 7)]:
        ts = Timespec()
        rc = libc.clock_getres(cid, ctypes.byref(ts))
        clocks[name] = {"rc": rc, "sec": ts.tv_sec, "nsec": ts.tv_nsec}

    samples = []
    for _ in range(20):
        req = Timespec(0, 500_000)  # 500 us
        t0 = time.perf_counter_ns()
        libc.nanosleep(ctypes.byref(req), None)
        samples.append((time.perf_counter_ns() - t0) / 1000.0)
    samples.sort()
    clocks["nanosleep_500us_actual_us"] = {
        "mean": round(statistics.mean(samples), 2),
        "median": round(samples[len(samples) // 2], 2),
        "min": round(samples[0], 2),
        "max": round(samples[-1], 2),
    }
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

    # Mounts & /dev/shm (size parsed explicitly this time)
    with open("/proc/mounts") as f:
        res["mounts"] = [line.strip() for line in f.readlines()]
    res["df_shm"] = run_cmd("df -h /dev/shm")["stdout"].strip()
    m = re.search(r"shm /dev/shm tmpfs [^ ]*size=(\d+)k", "\n".join(res["mounts"]))
    res["dev_shm_size_kb"] = int(m.group(1)) if m else None
    m2 = re.search(r"tmpfs /dev tmpfs [^ ]*size=(\d+)k", "\n".join(res["mounts"]))
    res["dev_size_kb"] = int(m2.group(1)) if m2 else None

    # Runtime variant verdict
    variant, variant_ev = detect_runtime_variant(res["mounts"], res["uname"])
    res["runtime_variant"] = variant
    res["runtime_variant_evidence"] = variant_ev

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

    # /execute_bash cwd parameter test (new in this schema — does it work?)
    try:
        st, body = unix_req("POST", "/execute_bash", {"command": "pwd", "cwd": "/tmp"})
        res["execute_bash_cwd_test"] = {"status": st, "body": body[:200]}
    except Exception as e:
        res["execute_bash_cwd_test"] = {"error": str(e)}

    # Disassemble dynamo_exec.block_localhost & inspect routes via /execute_python.
    # RESPONSE SCHEMA (v2 fix): body is JSON keyed by msg_out, NOT stdout.
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
        # msg_out holds the print() output as a JSON string; older sessions
        # used stdout. Accept either, record which one fired.
        payload_str = parsed.get("msg_out") or parsed.get("stdout", "")
        res["execute_python_response_keys"] = sorted(parsed.keys())
        res["execute_python_payload_source"] = (
            "msg_out" if parsed.get("msg_out") else ("stdout" if parsed.get("stdout") else "none")
        )
        if payload_str:
            info = json.loads(payload_str)
            with open(os.path.join(EVIDENCE_DIR, "block_localhost_dis.txt"), "w") as f:
                f.write(info.pop("block_localhost_dis", ""))
            res["dynamo_exec"] = info
            with open(os.path.join(EVIDENCE_DIR, "dynamo_routes.json"), "w") as f:
                json.dump(
                    {
                        "has_dynamo_exec": info.get("has_dynamo_exec"),
                        "routes": info.get("routes"),
                        "daemon_sys_modules_count": info.get("daemon_sys_modules_count"),
                    },
                    f,
                    indent=2,
                )
        else:
            res["execute_python_raw"] = parsed
    except Exception as e:
        res["execute_python_error"] = str(e)

    # /run/docker.sock connectivity (read-only GET /version, 3s timeout)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect("/run/docker.sock")
        s.sendall(b"GET /version HTTP/1.0\r\n\r\n")
        data = s.recv(2048).decode("utf-8", errors="replace")
        s.close()
        res["docker_sock"] = {"answers": True, "head": data[:500]}
    except Exception as e:
        res["docker_sock"] = {"answers": False, "error": str(e)}

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

    # Chromium evidence (was unevidenced in v1 — capture it properly)
    chrom = {}
    for name in ("default-flags", "dev-shm", "extensions"):
        p = "/etc/chromium.d/" + name
        if os.path.exists(p):
            with open(p) as f:
                chrom["chromium_d_" + name] = f.read()[:2000]
        else:
            chrom["chromium_d_" + name] = "MISSING"
    chrom["dev_shm_avail_bytes"] = run_cmd(
        "findmnt -bnr -o avail -T /dev/shm"
    )["stdout"].strip()
    chrom["which_chromium"] = run_cmd("which chromium || which chromium-browser || ls -la /usr/bin/chromium")["stdout"].strip()
    # GUI mode: launch, wait, capture window tree, kill. Bounded everywhere.
    try:
        proc = subprocess.Popen(
            ["chromium", "--no-sandbox", "--disable-gpu", "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": ":1"},
        )
        time.sleep(3)
        chrom["gui_xwininfo"] = run_cmd("DISPLAY=:1 xwininfo -root -tree")["stdout"].strip()
        chrom["gui_alive_after_3s"] = proc.poll() is None
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        chrom["gui_error"] = ""
    except Exception as e:
        chrom["gui_error"] = str(e)
    # Headless mode: bounded 10s run, capture outcome verbatim.
    headless = run_cmd(
        "chromium --headless --no-sandbox --disable-gpu "
        "--disable-dev-shm-usage --dump-dom about:blank",
        timeout=10,
    )
    chrom["headless"] = {
        "rc": headless["rc"],
        "stdout_tail": headless["stdout"][-1000:],
        "stderr_tail": headless["stderr"][-1000:],
    }
    res["chromium"] = chrom

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
    # GRTE: read the binary's actual interpreter instead of the path alone.
    interp = run_cmd("readelf -l /usr/bin/mfs | grep -A1 INTERP")["stdout"].strip()
    res["mfs_interp"] = interp if interp else "readelf unavailable/empty"
    res["grte_path"] = (
        os.path.realpath("/usr/grte/v5/lib64/ld-linux-x86-64.so.2")
        if os.path.exists("/usr/grte/v5/lib64/ld-linux-x86-64.so.2")
        else "MISSING"
    )
    res["ipc_dir"] = os.listdir("/ipc") if os.path.isdir("/ipc") else "MISSING"
    res["mfs_daemon_sock"] = os.path.exists(
        "/working_dir/.gemini/jetski/memory/daemon.sock"
    )
    res["mfs_log_exists"] = os.path.exists(
        os.path.expanduser("~/.gemini/jetski/memory/logs/memoryfs.INFO.log")
    )

    strings_out = run_cmd("strings /usr/bin/mfs | grep -E 'learning\\.gemini\\.memory|--(fireaxe|loas|lockservice|rpc_use|dapper|census|streamz)' | sort -u")
    with open(os.path.join(EVIDENCE_DIR, "mfs_symbols.txt"), "w") as f:
        f.write(strings_out["stdout"])
    # NOTE: this counts matching strings lines, NOT symbols. Label it as such.
    res["mfs_matching_strings_count"] = len(strings_out["stdout"].splitlines())

    with open(os.path.join(EVIDENCE_DIR, "mfs_telemetry.json"), "w") as f:
        json.dump(res, f, indent=2)
    return res


if __name__ == "__main__":
    t1 = probe_track1()
    t2 = probe_track2()
    t3 = probe_track3()
    summary = {
        "session_dir": os.getcwd(),
        "runtime_variant": t1["runtime_variant"],
        "runtime_variant_evidence": t1["runtime_variant_evidence"],
        "uptime": t1["uptime"],
        "dev_shm_size_kb": t1["dev_shm_size_kb"],
        "dev_size_kb": t1["dev_size_kb"],
        "io_benchmark": t1["io_benchmark"],
        "posix_working_dir": t1["posix_semantics"]["/working_dir"],
        "posix_tmp": t1["posix_semantics"]["/tmp"],
        "nanosleep_500us_actual_us": t1["clocks"]["nanosleep_500us_actual_us"],
        "double_fork_grandchild_ppid": t1["double_fork_grandchild_ppid"],
        "dynamo_exec": t2.get("dynamo_exec"),
        "execute_python_payload_source": t2.get("execute_python_payload_source"),
        "execute_bash_cwd_test": t2.get("execute_bash_cwd_test"),
        "docker_sock": t2.get("docker_sock"),
        "rfb_5901": t2.get("rfb_5901"),
        "chromium_gui_alive": t2.get("chromium", {}).get("gui_alive_after_3s"),
        "chromium_headless_rc": t2.get("chromium", {}).get("headless", {}).get("rc"),
        "mfs_status": t3["mfs_status"]["stdout"].strip(),
        "mfs_interp": t3["mfs_interp"],
        "ipc_dir": t3["ipc_dir"],
        "mfs_matching_strings_count": t3["mfs_matching_strings_count"],
    }
    print(json.dumps(summary, indent=2))
