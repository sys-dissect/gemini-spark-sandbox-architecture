# Part 1: The Cage — Sandbox Confinement, Hypervisor Mediation & Syscall Profile

## Executive Summary

The execution substrate of Google's Gemini Spark environment is governed by **Google gVisor** (the Sentry application kernel). Rather than allowing container workloads direct execution against the host Linux kernel, gVisor acts as a user-space operating system kernel written in Go. Sentry intercepts guest syscalls, implements an emulation table for the `Linux 4.19` ABI, virtualizes internal kernel structures (such as namespaces and process descriptors) within Go memory, and mediates all external filesystem operations via Plan 9 (`9p` / Gofer) channels.

This section presents empirical verification of gVisor's behavioral fingerprint, a complete 13-syscall ctypes probe matrix, namespace unsharing mechanics, capability bitmasks, storage durability semantics on 9P, network air-gap enforcement, and resource quotas.

---

## 1.1 gVisor Sentry Application Kernel & Behavioral Fingerprinting

**[Tier 1: Directly Observed]**

The guest environment reports kernel identity as:
```text
Linux 4.19.0-gvisor #1 SMP Sun Jan 1 00:00:00 2017 x86_64 GNU/Linux
```

Beyond the `uname -r` string, empirical verification confirms gVisor through several distinct architectural artifacts:

* **Satirical Boot Logs**: Reading `/dev/kmsg` or running `dmesg` returns gVisor's internal boot sequence:
  ```text
  [ 0.000000] Starting gVisor...
  [ 0.109587] Granting licence to kill(2)...
  [ 0.467735] Reticulating splines...
  [ 0.852820] Constructing home...
  [ 1.243247] Reading process obituaries...
  [ 1.517773] Generating random numbers by fair dice roll...
  [ 1.906657] DeFUSEing fork bombs...
  [ 2.053910] Mounting deweydecimalfs...
  [ 2.428688] Digging up root...
  [ 2.553202] Moving files to filing cabinet...
  [ 2.636610] Checking naughty and nice process list...
  [ 2.709367] Ready!
  ```
  *(See raw artifact: [`evidence/dmesg-gvisor-boot.log`](evidence/dmesg-gvisor-boot.log))*

* **Synthetic Scheduling Counters**:
  * `/proc/uptime`: Constant `0.00` idle time across all sessions (`272.97 0.00`).
  * `/proc/loadavg`: Permanently pinned to `0.00 0.00 0.00 0/0 0`.
  * `/proc/self/status`: Both `voluntary_ctxt_switches` and `nonvoluntary_ctxt_switches` remain hard-pegged at `0`.

* **Hardware & Hypervisor Layer**:
  * `/proc/cpuinfo`: 2 vCPUs allocated from an Intel Xeon Broadwell (Model 79) processor.
  * `/sys/class/dmi/id/product_name` returns `Google Compute Engine`.

---

## 1.2 The 13-Syscall Probe Matrix

**[Tier 1: Directly Observed]**

A Python ctypes test harness ([`evidence/syscall_probe.py`](evidence/syscall_probe.py)) was executed directly inside the container against `libc.syscall` to evaluate Sentry's selective syscall dispatch table:

| Syscall | Number | Arguments Passed | Return | Errno | Status / Error String | Architectural Finding |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`ptrace`** | 101 | `0, 0, 0, 0` | `0` | `0` | `SUCCESS` | **Indistinguishable from real kernel**: `PTRACE_TRACEME` succeeds; `PTRACE_ATTACH` against PID 1 fails with `EPERM` (standard Linux setuid non-dumpable behavior). |
| **`unshare`** | 272 | `0` | `0` | `0` | `SUCCESS` | **Emulated**: Sentry accepts namespace unsharing across all flags (see §1.3). |
| **`memfd_create`** | 319 | `ptr("audit_test"), 0` | `4` | `0` | `SUCCESS` (FD 4) | **Anonymous RAM FD**: Fully supported; verified with valid string pointer. |
| **`pidfd_open`** | 434 | `self_pid, 0` | `5` | `0` | `SUCCESS` (FD 5) | **Process Handles**: Modern Linux process tracking primitives supported. |
| **`clone3`** | 435 | `0, 0` | `-1` | `22` | `Invalid argument (EINVAL)` | **Implemented**: Handled by gVisor dispatch table (fails cleanly on NULL struct pointer). |
| **`io_uring_setup`**| 425 | `1, 0` | `-1` | `38` | `Function not implemented (ENOSYS)` | **Eliminated**: gVisor drops io_uring entirely to prevent host kernel attack surfaces. |
| **`userfaultfd`** | 323 | `0` | `-1` | `38` | `Function not implemented (ENOSYS)` | **Eliminated**: User-space page fault handling is omitted. |
| **`syslog`** | 103 | `2, 0, 0` | `-1` | `38` | `Function not implemented (ENOSYS)` | **Eliminated**: Kernel ring buffer manipulation rejected. |
| **`perf_event_open`**| 298| `0, 0, -1, -1, 0` | `-1` | `19` | `No such device (ENODEV)` | **Unmapped**: Hardware performance monitoring registers are not exposed. |
| **`bpf`** | 321 | `0, 0, 0` | `-1` | `1` | `Operation not permitted (EPERM)` | **Capability Denial**: Process runs as unprivileged `CapEff 0`; direct eBPF program loading blocked. |
| **`mount`** | 165 | `ptr("none"), ptr("/tmp"), ...` | `-1` | `1` | `Operation not permitted (EPERM)` | **Capability Denial**: In-guest mounting rejected under unprivileged capability mask. |
| **`kexec_load`** | 246 | `0, 0, 0, 0` | `-1` | `1` | `Operation not permitted (EPERM)` | **Capability Denial**: Kernel replacement rejected. |
| **`reboot`** | 169 | `0xfee1dead, 672274793, ...` | `-1` | `1` | `Operation not permitted (EPERM)` | **Capability Denial**: System reboot rejected. |

---

## 1.3 Detailed `unshare` Flag Exploration

**[Tier 1: Directly Observed]**

Probing individual `CLONE_NEW*` flags from `<linux/sched.h>` confirms that gVisor Sentry accepts namespace creation requests across **all** namespaces:

| Flag Name | Flag Hex | Syscall Arguments | Return Code | Errno / Status | Architectural Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`0 (no-op)`** | `0x00000000` | `272, 0x0` | `0` | `SUCCESS` | No-op namespace rebind accepted. |
| **`CLONE_NEWUSER`** | `0x10000000` | `272, 0x10000000` | `0` | `SUCCESS` | User namespace virtualized in Go memory. |
| **`CLONE_NEWNET`** | `0x40000000` | `272, 0x40000000` | `0` | `SUCCESS` | Virtualized network stack; informative because a real kernel denies this to unprivileged callers without user namespaces. |
| **`CLONE_NEWNS`** | `0x00020000` | `272, 0x00020000` | `0` | `SUCCESS` | Virtualized mount table fork. |
| **`CLONE_NEWUTS`** | `0x04000000` | `272, 0x04000000` | `0` | `SUCCESS` | Virtualized hostname/domain table. |
| **`CLONE_NEWIPC`** | `0x08000000` | `272, 0x08000000` | `0` | `SUCCESS` | Virtualized System V / POSIX IPC tables. |
| **`CLONE_NEWPID`** | `0x20000000` | `272, 0x20000000` | `0` | `SUCCESS` | Virtualized PID namespace hierarchy. |

**Key Finding**: Sentry maintains virtualized internal namespace trees inside user-space Go memory without delegating namespace changes to the host kernel.

---

## 1.4 Socket Family Probe & AF_VSOCK Absence

**[Tier 1: Directly Observed]**

Testing socket domain availability via Python socket bindings demonstrates strict domain pruning:

```json
{
  "AF_UNIX": {"supported": true},
  "AF_INET": {"supported": true},
  "AF_INET6": {"supported": true},
  "AF_NETLINK": {"supported": true},
  "AF_PACKET": {"supported": false, "error": "[Errno 1] Operation not permitted"},
  "AF_VSOCK": {"supported": false, "error": "[Errno 97] Address family not supported by protocol"},
  "AF_BLUETOOTH": {"supported": false, "error": "[Errno 97] Address family not supported by protocol"}
}
```

* `AF_VSOCK` returns `EAFNOSUPPORT` (`Errno 97`). gVisor does not virtualize a PCI/virtio bus or expose a host-guest VSOCK device node into Sentry.
* `AF_PACKET` returns `EPERM` (`Errno 1`). Raw packet synthesis is blocked.
* All inter-process communication relies exclusively on `AF_UNIX` (domain sockets) and `AF_INET`/`AF_INET6` over loopback.

---

## 1.5 Privilege Architecture & Capability Bitmask Decoding

**[Tier 1: Directly Observed]**

Inspection of `/proc/<pid>/status` across root daemons and worker processes establishes a two-tiered privilege boundary:

```text
Process / Command                  PID    UID     CapPrm             CapEff             Seccomp
Xtigervnc :1                        8       0     00000000a82405fb   00000000a82405fb   0
entrypoint_secure.sh               34       0     00000000a82405fb   00000000a82405fb   0
sudo -u spark stterm (tmux)        49       0     00000000a82405fb   00000000a82405fb   0
sudo -u spark mfs daemon start     66       0     00000000a82405fb   00000000a82405fb   0
runsc exec curl injected bridge  2866       0     00000000a82405fb   00000000a82405fb   0
-------------------------------------------------------------------------------------------------
PID 1: shell_wrapper.py             1    1235     0000000000000000   0000000000000000   0
Bash / Python worker children    2867    1235     0000000000000000   0000000000000000   0
```

### Decoded Root Capability Bitmask (`0x00000000a82405fb`)

The container root tier holds 14 capabilities:
* `CAP_CHOWN` (Bit 0)
* `CAP_DAC_OVERRIDE` (Bit 1)
* `CAP_FOWNER` (Bit 3)
* `CAP_FSETID` (Bit 4)
* `CAP_KILL` (Bit 5)
* `CAP_SETGID` (Bit 6)
* `CAP_SETUID` (Bit 7)
* `CAP_SETPCAP` (Bit 8)
* `CAP_NET_BIND_SERVICE` (Bit 10)
* `CAP_SYS_CHROOT` (Bit 18)
* `CAP_SYS_ADMIN` (Bit 21)
* `CAP_MKNOD` (Bit 27)
* `CAP_AUDIT_WRITE` (Bit 29)
* `CAP_SETFCAP` (Bit 31)

**Architectural Inference [Tier 2]**: `Seccomp: 0` is set across all processes. In-guest Seccomp-BPF filtering is absent because gVisor's Sentry user-space kernel intercepts syscalls before they reach the host kernel. Because container root holds `CAP_SYS_ADMIN`, container UID 0 is not the security boundary—Sentry itself is the sole security boundary.

---

## 1.6 Sudo Rights & Local Package Operations

**[Tier 1: Directly Observed]**

Verbatim output of `sudo -l`:
```text
Matching Defaults entries for spark on <sandbox_container_id>:
    env_reset, mail_badpass, secure_path=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin, use_pty

User spark may run the following commands on <sandbox_container_id>:
    (ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /usr/bin/dpkg
```

### Operational Scope Under Hard Air-Gap:
1. **Remote Repository Actions Fail**: `sudo apt-get update` or `sudo apt-get install <pkg>` fails immediately because mirrors cannot be contacted (`ENETUNREACH`).
2. **Local Package Installation Works**: `sudo dpkg -i /path/to/local.deb` executes as root, allowing installation of pre-transferred `.deb` archives if dependencies are met.
3. **Local Removal & Reconfiguration**: `sudo apt-get remove <pkg>` or `sudo dpkg -r <pkg>` successfully uninstalls system packages and modifies `/var/lib/dpkg/status`.
4. **Offline Database Audits**: `dpkg -l`, `dpkg-query`, and `dpkg -c` execute unprivileged without requiring sudo.

---

## 1.7 Storage Mediation: Overlay Root vs. Plan 9 (9P) Host Descriptors

**[Tier 1: Directly Observed]**

Inspection of `/proc/self/mountinfo` ([`evidence/mountinfo-procfs.json`](evidence/mountinfo-procfs.json)) establishes a bifurcated storage model:

* **Ephemeral Root (`/`)**: Mounted as `overlayfs`. Writes to `/tmp`, `/var`, or root directories reside in volatile memory and vanish upon container recycling.
* **Persistent Gofer Mounts**:
  * `/working_dir` (`rfdno=5,wfdno=5`)
  * `/home/spark` (`rfdno=7,wfdno=7`)
  * `/usr/local` (`rfdno=8,wfdno=8`)
  * `/mnt/agentdata` (`rfdno=9,wfdno=9`)
  * `/ipc` (`rfdno=6,wfdno=6`)
  All mounted via Plan 9 (`9p`, `v9fs`) using `trans=fd`, `cache=remote_revalidating`, and `directfs`.
* **FUSE Mounts**:
  * `/run/user/1235/memory` and `/working_dir/memory` (`fd=3`, `user_id=1235`, `group_id=1235`).

### POSIX Semantic Fidelity Matrix

| POSIX Operation | Primitive Tested | Result on 9P (`/working_dir`) | Result on OverlayFS (`/tmp`) | Semantic Fidelity Note |
| :--- | :--- | :--- | :--- | :--- |
| **Durability Flush** | `os.fsync(fd)` | **`FAILED: EOPNOTSUPP (95)`** | **`SUCCESS`** | **9P drops `fsync` down the wire.** Databases requiring strict fsync durability will fail or require configuration adjustments. |
| **Advisory Locking** | `fcntl.flock(LOCK_EX \| LOCK_NB)` | **`SUCCESS`** | **`SUCCESS`** | Fully functional across host file descriptors via Gofer. |
| **Atomic Replace** | `os.replace(src, dst)` | **`SUCCESS`** | **`SUCCESS`** | Fully compliant with atomic replacement semantics. |
| **Exclusive Creation** | `os.open(O_CREAT \| O_EXCL)` | **`ENFORCED`** (`FileExistsError`) | **`ENFORCED`** (`FileExistsError`) | Duplicate file creation raises `EEXIST`. |
| **Named Pipes (FIFO)** | `os.mkfifo()` + `open(O_NONBLOCK)` | **`SUCCESS`** (FD 3) | **`SUCCESS`** (FD 3) | Sentry emulates FIFO creation and user-space I/O despite `disable_fifo_open`. |
| **Change Watches** | `inotify_init1()` + `inotify_add_watch` | **`SUCCESS`** (WD 1) | **`SUCCESS`** (WD 1) | In-guest operations trigger notifications; host modifications rely on cache revalidation. |

### Storage Performance Benchmark: 9P Gofer vs. OverlayFS

| Performance Metric | Ephemeral OverlayFS (`/tmp`) | Persistent 9P Gofer (`/working_dir`) | Relative Difference |
| :--- | :--- | :--- | :--- |
| **Small-Block (4 KB) Write Latency** | `0.074 ms` (avg) / `0.130 ms` (p95) | `33.259 ms` (avg) / `68.593 ms` (p95) | **~450× slower** on 9P due to Gofer RPC roundtrips. |
| **Sequential Write Throughput** | `1,113.62 MB/s` | `175.11 MB/s` | OverlayFS operates at RAM speeds; 9P is throttled by FD translation. |
| **Sequential Read Throughput** | `1,551.75 MB/s` | `236.64 MB/s` | 9P benefits from `cache=remote_revalidating`, achieving ~236 MB/s. |

### Persistence Scope: Turn-to-Turn vs. Across-Recycle

* **Turn-to-turn persistence [Tier 1]**: Verified empirically. Environment variables appended to `/working_dir/.bashrc` automatically persist into fresh subshells on subsequent turns (via `BASH_ENV`, since `$HOME=/working_dir`).
* **Across-recycle persistence [Tier 2]**: `/working_dir` contains active subdirectories (`c_<session_id>`) dating back several weeks. Files written to `/working_dir` survive container recycling.

---

## 1.8 Network Confinement: Hard Air-Gap & Socket Failure Mechanics

**[Tier 1: Directly Observed]**

Probing the network stack established that network isolation is enforced at the device level ([`evidence/network-failure-probes.json`](evidence/network-failure-probes.json)):

* **Interfaces**: `/proc/net/dev` registers **only `lo`** (receive: 240 bytes, transmit: 296 bytes). No virtual Ethernet (`eth0`), bridge, or tap device is exposed.
* **Routing Table**: `/proc/net/route` (IPv4) and `/proc/net/ipv6_route` (IPv6) contain zero routes.
* **Failure Mechanics**:
  * TCP connection attempts to external IPs (`142.250.190.46:80`) or cloud metadata (`169.254.169.254:80`) terminate immediately with `OSError: [Errno 101] Network is unreachable` (`ENETUNREACH`).
  * Name resolution fails at the socket layer (`gaierror: [Errno -3] Temporary failure in name resolution`) because UDP packets cannot leave the container to contact `/etc/resolv.conf`'s synthetic entry (`169.254.169.254`).

---

## 1.9 Resource Quotas: Cgroups v1 Accounting

**[Tier 1: Directly Observed]**

Inspection of `/sys/fs/cgroup/*` controller boundaries:

```json
{
  "memory.limit_in_bytes": "5368709120",
  "memory.usage_in_bytes": "382623744",
  "cpu.cfs_quota_us": "-1",
  "cpu.cfs_period_us": "100000",
  "cpu.shares": "1024",
  "pids.max": "max",
  "pids.current": "38"
}
```

* **Memory Ceiling**: Limit of exactly `5,368,709,120` bytes (5.00 GiB). The cgroup v1 interface inside the sandbox is emulated and read-only.
* **CPU Scheduling**: Unthrottled quota (`-1`) with standard 1024 proportional CPU shares on a 100ms CFS period—no hard CPU cap, shares-based scheduling only.
* **Process Accounting**: PIDs ceiling is unconstrained (`max`), with an active idle count of ~38 processes.

---

## 1.10 Host Identity Markers: Negative Result

**[Tier 1: Directly Observed]**

Testing whether Spark's sandbox leaks Borg or Google infrastructure markers:

* `/proc/1/environ` and `/proc/8/environ` are owned `root:root` mode `0400`—unreadable by the unprivileged worker (`EACCES`).
* Reading `os.environ` in-process via `/execute_python` (which executes inside PID 1's address space) returned 15 keys; a prefix sweep for `BORG_`, `GOOGLE_`, `CONTAINER_`, `K8S_` returned **zero hits**.
* **Finding**: No Borg or internal Google host identifiers are present in the observable container environment. The hypervisor substrate is attributed solely by the DMI `product_name` (`Google Compute Engine`).

---

## 1.11 LSM Absence: AppArmor Intent vs. Reality

**[Tier 1: Directly Observed]**

Probing for Linux Security Modules (LSM) within the guest:

| Path / Command | Exit Code | Raw Output / Error | Diagnostic State |
| :--- | :--- | :--- | :--- |
| `cat /proc/1/attr/current` | `1` | `cat: /proc/1/attr/current: No such file or directory` | **Missing path** (`ENOENT`) |
| `ls -la /proc/1/attr` | `2` | `ls: cannot access '/proc/1/attr': No such file or directory` | **Directory absent** (`ENOENT`) |
| `cat /proc/self/attr/current` | `1` | `cat: /proc/self/attr/current: No such file or directory` | **Missing path** (`ENOENT`) |
| `ls -la /sys/kernel/security` | `2` | `ls: cannot access '/sys/kernel/security': No such file or directory` | **SecurityFS absent** (`ENOENT`) |
| `ls -la /sys/module/apparmor` | `2` | `ls: cannot access '/sys/module/apparmor': No such file or directory` | **Kernel module absent** (`ENOENT`) |
| `which apparmor_status` | `0` | `/usr/sbin/apparmor_status` | **Binary present on rootfs** |
| `/usr/sbin/apparmor_status` | `1` | `apparmor not present.` | **LSM completely inactive** |

* gVisor's emulated procfs does not implement `/proc/<pid>/attr/`, and SecurityFS is unmounted and unmapped.
* The `apparmor 3.0.8-3` Debian package exists on disk as an artifact of base rootfs assembly, but the LSM is completely inactive.
* Together with `Seccomp: 0`, this confirms that containment is enforced exclusively by the Sentry user-space kernel.
