# Configuration B — Cloud Hypervisor / Docker Runtime (2026-11-19)

A second runtime configuration of Google's Gemini Spark execution sandbox,
measured in a live session on 2026-11-19. The baseline repository (commit
`7fbcb62`) documents a single gVisor runtime (Configuration A). The
measurements in this document show the environment is provisioned in at
least two distinct runtime configurations. Every claim is labeled
`[Tier 1: Directly Observed]` (directly backed by the evidence files cited)
or `[Tier 2: Inference]` (reasoned from Tier 1 observations), using the
repository's three-tier framework.

## 1. Runtime Configurations: Configuration A vs. Configuration B

* **Configuration A (gVisor — Sentry + 9p Gofer + overlayfs)**
  `[Tier 1: Directly Observed]`: User-space application kernel emulating
  `Linux 4.19.0-gvisor`, with `/working_dir`, `/usr/local`, `/home/spark`,
  `/ipc`, and `/mnt/agentdata` mounted over `9p` (`9p_gofer`), `/tmp` on
  `overlayfs`, `/dev/shm` capped at `64 MB`, synthetic `0.00` idle uptime,
  and `os.fsync(fd)` failing on `/working_dir` with `EOPNOTSUPP (95)`.
* **Configuration B (Cloud Hypervisor / KVM + Docker + XFS, not gVisor)**
  `[Tier 1: Directly Observed]` / `[Tier 2: Inference]`: A **Cloud
  Hypervisor** VMM (`sys_vendor: Cloud Hypervisor`, `product_name:
  cloud-hypervisor`, `bios_vendor: cloud-hypervisor`) running over **KVM**
  (`Hypervisor vendor: KVM`) on 2 vCPUs of **Intel Xeon 6985P-C** (AVX-512,
  VNNI, BF16, FP16, `504 MiB` L3) with `Linux 6.12.105+` (`#1 SMP Sat Sep
  19 19:37:41 UTC 2026`, node `ace`), `tsc` clocksource, and `8,138,460 kB`
  (~`7.76 GiB`) RAM (`SwapTotal: 0 kB`) `[Tier 1: Directly Observed]`. The
  VM boots read-only off `/dev/pmem0p3` (`virtio_pmem`, `9 GiB`,
  `rotational=0`) with boot-time `security=apparmor`,
  `sysctl.kernel.kexec_load_disabled=1`,
  `sysctl.vm.compaction_proactiveness=0`, and `systemd.hostname=ace`
  `[Tier 1: Directly Observed]`. Inside the container (`/.dockerenv`
  present), root `/` is a `45`-layer `/var/lib/docker/overlay2` mount;
  `/working_dir`, `/usr/local`, and `/home/spark` are bind-mounted from
  `/home/workload/{working_dir,usr_local,spark_home}` on `/dev/vdb`
  (`20 GiB` XFS, `allocsize=65536k,noatime,nodiratime`); `/tmp` is `3.9G`
  `tmpfs` (`noexec`); `/dev/shm` is `2.0 GiB` (`size=2097152k`); and
  `fsync` succeeds everywhere `[Tier 1: Directly Observed]`. Together, the
  VMM hardware topology, `/var/lib/docker/overlay2` root, and
  `/proc/net/unix` sockets (`containerd`, `NRI`, `runtimed`, `docker`,
  `systemd`) indicate a standard containerd + Docker + systemd stack
  inside a single-tenant Cloud Hypervisor microVM `[Tier 2: Inference]`.

All absolute statements in the baseline repository — such as *"the execution
substrate is gVisor"*, *"`/tmp` is `overlayfs`"*, *"`fsync` fails on
`/working_dir`"*, and *"`/dev/shm` is `64 MB`"* — apply specifically to
**Configuration A** and should be qualified accordingly
`[Tier 1: Directly Observed]`.

### Configuration Comparison Matrix

| Attribute | Configuration A (gVisor) `[Tier 1: Directly Observed]` | Configuration B (Cloud Hypervisor / Docker) `[Tier 1: Directly Observed]` |
| :--- | :--- | :--- |
| **VMM / Hypervisor** | gVisor Sentry (user-space Go kernel) | Cloud Hypervisor over KVM (`Intel Xeon 6985P-C`, 2 vCPU, `1.78×` 2-worker scaling) |
| **Kernel / ABI** | `Linux 4.19.0-gvisor` (emulated 4.19 ABI) | `Linux 6.12.105+` (node `ace`, `tsc` clocksource) |
| **Boot & Confinement** | gVisor syscall interception | Boot-time `security=apparmor` (`ace-workload (enforce)`), `kexec_load_disabled=1`, `CapEff: []`, `Seccomp: 0`, `NoNewPrivs: 0`, hardened sysctls |
| **Root Filesystem** | `overlay` (ephemeral gVisor root) | `overlay` (`45` lowerdir layers under `/var/lib/docker/overlay2/l/...`) |
| **`/working_dir`, `/usr/local`, `/home/spark`** | `9p` (`9p_gofer`) | `/dev/vdb` (`20 GiB` `virtio_blk` XFS, `allocsize=65536k`, `write back`, `mq-deadline`) via `/home/workload/{working_dir,usr_local,spark_home}` |
| **`/tmp` Filesystem** | `overlayfs` | `tmpfs` (`3.9G`, `rw,nosuid,nodev,noexec`) |
| **`/dev/shm` Allocation** | `64 MB` (`tmpfs`) | `2.0 GiB` (`tmpfs`, `size=2097152k`; `df`: `2.0G`) |
| **RAM / Swap / Dirty Limits** | Container quota (gVisor) | `8,138,460 kB` (~`7.76 GiB`), `0` swap, `overcommit_memory: 1`, `dirty_bytes: 61,440,000`, `dirty_background_bytes: 15,360,000` |
| **Cgroup v2 & `ulimit`** | gVisor virtualized | `memory.max: max`, `cpu.max: max 100000` (`nr_throttled: 0`), `pids.max: 9507`, `nofile: 1,048,576` |
| **`/proc/uptime` Idle Field** | Synthetic `0.00` (e.g., `272.97 0.00`) | Active idle ticks (`231.71 437.31`; later `8446.77 16772.07`) |
| **`fsync(2)` on `/working_dir`** | `FAILED: EOPNOTSUPP (95)` | `SUCCESS` (4K `fsync` ×50: mean `4.152 ms`, p95 `3.331 ms`, max `84.263 ms`) |
| **`fsync(2)` on `/tmp`** | `SUCCESS` | `SUCCESS` (4K `fsync` ×50: mean `0.003 ms`, p95 `0.004 ms`, max `0.028 ms`) |
| **4 KB `open+write+close` (`/working_dir`)** | `33.259 ms` avg / `68.593 ms` p95 | `0.734 ms` avg / `2.993 ms` p95 (morning); `1.788 ms` avg / `2.920 ms` p95 (live rerun, 50 iters) |
| **4 KB `open+write+close` (`/tmp`)** | `0.074 ms` avg / `0.130 ms` p95 | `0.003 ms` avg / `0.014 ms` p95 (morning); `0.003 ms` avg / `0.004 ms` p95 (live rerun) |
| **10 MB Seq Write / Read (`/working_dir`)** | `175.11 MB/s` / `236.64 MB/s` | `3389.37 MB/s` / `1827.34 MB/s` (morning); `1740.99 MB/s` / `6992.17 MB/s` (live rerun) |
| **10 MB Seq Write / Read (`/tmp`)** | `1,113.62 MB/s` / `1,551.75 MB/s` | `1607.11 MB/s` / `1479.27 MB/s` (morning); `977.62 MB/s` / `4506.13 MB/s` (live rerun) |
| **Memory Bandwidth (256 MB)** | Not measured | Pre-faulted `ctypes.memset`: `5,368 MB/s`; `memcpy`: `6,366 MB/s` (Python `b'\x00'*n` alloc+fill: `156 MB/s`) |
| **`nanosleep(500 us)`** | `1,392.88 us` (single sample, indicative) | `571.86 us` (single sample); 20-sample live rerun: mean `565.40 us`, median `564.64 us`, min `559.74 us`, max `573.79 us` |
| **Network & Virtio Bus** | Loopback only (`lo`), no routes | Loopback only (`lo`), **no `virtio_net`**; 8 virtio devices (`rng`, `pmem`, `vsock`, `balloon`, 4× `blk`); dedicated loopback-only `net:[4026531840]` namespace; `106` `/proc/net/unix` entries |
| **Listening TCP Ports** | `5901` (TigerVNC) | `0.0.0.0:5901` (v4/v6), `0.0.0.0:8080`, `127.0.0.1:12345` (below ephemeral range `32768–60999`), `127.0.0.1:42029` (ephemeral) |
| **OS Image & Packages** | Debian 12 Bookworm (`480` libs, `14` libdevel) | Debian 12 Bookworm (`20261027T000000Z` snapshot, `1,314` pkgs, `480` libs, `14` libdevel, `Python 3.11.2`, `159` pip pkgs, `Node v18.20.4`) |

* **Block & Virtio Disk Map** `[Tier 1: Directly Observed]`: Four
  `virtio_blk` volumes (`vda` `5 GiB` rw, `vdb` `20 GiB` rw, `vdc` `50 GiB`
  `ro=1`, `vdd` `10 GiB` rw), one `virtio_pmem` boot device (`pmem0`
  `9 GiB`, `rotational=0`, rootfs on `/dev/pmem0p3`), and two
  device-mapper devices whose topology is resolved in §6: `dm-0`
  (`ace_vg-root_lv`, an LVM logical volume backed by `vda`) and `dm-1`
  (`docker_snapshot`, origin `vdc` with copy-on-write store `vdd`).
* **Measurement Caveats** `[Tier 1: Directly Observed]`: The 4 KB latency
  benchmark measures `os.open` + `os.write(4096)` + `os.close` per
  iteration over 50 iterations, not pure `write(2)` latency.
  Configuration A's `nanosleep(500 us)` (`1,392.88 us`) remains a single
  indicative sample, whereas Configuration B's `nanosleep(500 us)` is now
  characterized across 20 samples (`559.74–573.79 us`).

Sources: Configuration A from `../part1-the-cage/README.md`,
`../part1-the-cage/evidence/mountinfo-procfs.json`,
`../part1-the-cage/evidence/uptime_probe.log`,
`../part2-the-machinery/README.md`, and
`../part2-the-machinery/data/proc_net_unix.txt`; Configuration B from
`evidence/morning-bundle/track1_virtio_xfs_cage.json`,
`evidence/morning-bundle/syscall_probe.py`,
`evidence/morning-bundle/proc_net_unix.txt`,
`../part1-the-cage/evidence/vmm_cloud_hypervisor_boot.txt`,
`../part1-the-cage/evidence/confinement_cgroup_limits.txt`,
`../part1-the-cage/evidence/storage_io_compute_benchmarks.txt`,
`../part1-the-cage/evidence/network_proc_airgap.txt`, and
`../part2-the-machinery/data/toolchain_packages_fonts.txt`.

---
## 2. Corrected Storage Note (`/dev/shm` & Chromium Behavior)

* **`/dev/shm` Size Correction on Configuration B**
  `[Tier 1: Directly Observed]`: On Configuration B, `/dev/shm` is
  `2.0 GiB` (`size=2097152k`, `df`: `2.0G`, `findmnt -bnr -o avail -T
  /dev/shm`: `2147483648` bytes). The `64 MB` (`size=65536k`) `tmpfs`
  entry in Configuration B's mount list is `/dev`, not `/dev/shm`. The
  `64 MB` `/dev/shm` cap applies only to Configuration A.
* **`/etc/chromium.d/dev-shm` Wrapper Confirmed**
  `[Tier 1: Directly Observed]`: `/etc/chromium.d/dev-shm` explicitly
  runs `shm_avail=$(findmnt -bnr -o avail -T /dev/shm)` and injects
  `--disable-dev-shm-usage` whenever `shm_avail -lt 4080218931`
  (`3.8 GB`). Because `2,147,483,648 < 4,080,218,931`, the conclusion
  that `--disable-dev-shm-usage` is injected stands, while the earlier
  `64 MB` figure for Configuration B does not.
* **Chromium Runtime Behavior (`Chromium 156.0.8078.73`)**
  `[Tier 1: Directly Observed]`:
  * **GUI Mode (`DISPLAY=:1`)**: `DISPLAY=:1 chromium about:blank` maps
    an X11 window (`0x400002 "about:blank - Chromium"`,
    `1265x1420+10+10`) within `3 s` alongside `tmux` (`0x200005`).
    D-Bus errors (`/run/dbus/system_bus_socket: No such file or
    directory`) are non-fatal.
  * **Headless Mode**: `chromium --headless --disable-gpu --dump-dom
    about:blank` hangs until `timeout 10` (`exit 124`, empty stdout),
    logging `Fontconfig error: Cannot load default config file: No such
    file: (null)` and `zygote_communication_linux.cc:313 Failed to send
    GetTerminationStatus message to zygote`.
  * **Flag Isolation (`timeout 6` each, `about:blank`)**:
    `--headless=new --disable-gpu --dump-dom`, `--headless --no-sandbox
    --disable-gpu --dump-dom`, and `--headless --no-sandbox --no-zygote
    --disable-gpu --dump-dom` all time out (`exit 124`, empty stdout).
    Passing `--no-zygote` suppresses the zygote error line, showing the
    hang persists independently of the zygote.
  * **Two corrections to the earlier reading of the headless failure**
    `[Tier 1: Directly Observed]`: (1) The Fontconfig error is **not**
    a missing config path — `/etc/fonts/fonts.conf` exists, `fc-list`
    returns `626` fonts, and the error persists with
    `FONTCONFIG_FILE=/etc/fonts/fonts.conf` set explicitly in a verbose
    (`--v=1`) run. (2) The `zygote:313` error is a **SIGTERM teardown
    artifact**, not the stall: the verbose log shows normal startup,
    then `Handling shutdown for signal 15` (the timeout kill), and only
    then the zygote errors during teardown. The real stall is upstream
    (renderer / Mojo / IPC), in the unshown middle of the log. The
    verbose run also confirmed a managed Chrome policy file exists
    (`/etc/chromium/policies/managed/chrome_policy.json`); see §8.

Sources: `/dev/shm` (`size=2097152k`, `2.0G`) and `/dev` (`size=65536k`)
from `evidence/morning-bundle/track1_virtio_xfs_cage.json` and
`../part1-the-cage/evidence/storage_io_compute_benchmarks.txt`;
`/etc/chromium.d/dev-shm` (`4080218931`), `findmnt` (`2147483648`),
`Chromium 156.0.8078.73`, GUI `xwininfo`, and headless flag runs from
`../part2-the-machinery/data/chromium_devshm_and_runs.txt`;
`/etc/fonts/` and `fc-list` (`626`) from
`../part2-the-machinery/data/toolchain_packages_fonts.txt`; verbose-run
corrections from `evidence/fresh-probe-live/step19-workload-runtime-output.txt`;
Configuration A `64 MB` `/dev/shm` from
`../part1-the-cage/README.md`.

---

## 3. Memory Service (`mfs`), Conditionally Provisioned

* **Observed Configuration B State** `[Tier 1: Directly Observed]`:
  `/usr/bin/mfs` remains installed (`mfs --help` returns the full
  `Jetski Memory FUSE CLI for Sandbox` command set), and
  `~/.gemini/jetski/memory/config.txtpb` (`281` bytes) is present,
  configuring `root_dir: "/home/spark/memory"` with a `default` Dumbo
  mount (`users/me/groups/default`). However, the runtime service is
  inactive: `mfs status` (`rc: 0`) reports `Daemon: offline` and
  `default ~/memory/default unavail`, `/ipc` does not exist (`No such
  file or directory`), and `~/.gemini/jetski/memory/logs/` does not
  exist (`No such file or directory`).
* **Configuration-Dependent Provisioning**
  `[Tier 1: Directly Observed]` / `[Tier 2: Inference]`: Baseline
  Configuration A ran `mfs` with `Daemon: online (pid 67)`, `Root:
  /run/user/1235/memory`, and `/ipc` mounted via `9p`
  (`../part3-the-mind/data/mfs_cli_status.txt`,
  `../part1-the-cage/evidence/mountinfo-procfs.json`). Because `/ipc` is
  absent alongside an entirely different Cloud Hypervisor + Docker
  substrate, this should be framed as **configuration-dependent
  provisioning** rather than a regression `[Tier 2: Inference]`.
* **`mfs debug` Confirms Logs Uninitialized** `[Tier 1: Directly
  Observed]`: `mfs debug` reports the FUSE daemon offline
  (`FuseDaemonManager`), no FUSE mount under `~/memory` (`findmnt`),
  and its log-artifact collection copied **0 files** (0 `.recordio`,
  0 `.log`) — the logs-uninitialized state is confirmed, not inferred.
  The plugin supervisor heartbeat is missing/stale (`>120 s`) and the
  hooks sidecar port file is missing. Two Gemini CLI config files are
  absent (`/home/spark/.gemini/config/config.json`,
  `/home/spark/.gemini/jetski/cli/settings.json`) — the CLI itself is not
  configured in this container; only the `jetski/memory` `config.txtpb`
  exists. The debug tarball (`/tmp/mfs_debug_20261119_104702.tar.gz`) is
  `2.7 KB` because there are no logs to include.
* **String Extraction Count** `[Tier 1: Directly Observed]`:
  `mfs_symbols.txt` contains `211` unique matching lines
  (`mfs_symbols_count: 211`) from `strings /usr/bin/mfs | grep -E
  'learning\.gemini\.memory|--(fireaxe|loas|lockservice|rpc_use|dapper|
  census|streamz)' | sort -u` — not *"211 protobuf/ABSL symbols"*.
* **GRTE v5 ELF Interpreter Verified** `[Tier 1: Directly Observed]`:
  In addition to `/usr/grte/v5/lib64/ld-linux-x86-64.so.2` resolving to
  `/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2`
  (`mfs_telemetry.json`), `readelf -l /usr/bin/mfs | grep -i interp`
  confirms `[Requesting program interpreter:
  /usr/grte/v5/lib64/ld-linux-x86-64.so.2]` on Configuration B.

Sources: `mfs status`, `grte_link`, `ipc_dir`, and `mfs_symbols_count`
(`211`) from `evidence/morning-bundle/mfs_telemetry.json`,
`evidence/morning-bundle/mfs_symbols.txt`, and
`evidence/morning-bundle/syscall_probe.py`; `readelf -l /usr/bin/mfs`,
`ls /ipc`, missing `~/.gemini/jetski/memory/logs/`, and `config.txtpb`
from `../part3-the-mind/data/mfs_grte_interp_and_config.txt`; `mfs debug`
output from `evidence/fresh-probe-live/step20-dm-mfs-output.txt`;
Configuration A baseline (`pid 67`) from
`../part3-the-mind/data/mfs_cli_status.txt` and
`../part1-the-cage/evidence/mountinfo-procfs.json`.

---
## 4. API & Execution Engine Notes (`dynamo_exec` & Process Lifecycle)

* **`POST /execute_python` Undocumented JSON Response**
  `[Tier 1: Directly Observed]`: Calling `POST /execute_python` over
  `/tmp/shell.sock` returns HTTP `200` (`application/json`) keyed by
  `msg_out`, `msg_err`, and `exit_status_int` (`0` on success, `1` on
  exception). By contrast, `openapi.json` (`3,706` bytes) declares the
  `200` response as `text/plain` (`{"type": "string"}`) and omits this
  JSON schema.
* **`POST /execute_bash` `cwd` Parameter (Verified Live)**
  `[Tier 1: Directly Observed]`: `ExecuteBashRequest` in the `2026-11-19`
  `openapi.json` adds an optional `cwd` string property absent from the
  baseline `openapi-schema.json`. Live call `POST /execute_bash` with
  `{"command":"pwd","cwd":"/tmp"}` returned `{"status":"Executed shell
  script","stdout":"/tmp\n","stderr":"","combined_output":"/tmp\n",
  "exit_code":0}`.
* **PID 1 (`shell_wrapper.py`) Footprint, Routes & Middleware**
  `[Tier 1: Directly Observed]`:
  * `/proc/1/status` reports `Name: shell_wrapper.p`, `Pid: 1`, `State:
    S (sleeping)`, `Threads: 2`, `VmRSS: 38,768 kB` (`40,236–40,668 kB`
    later in session), and `VmSize: 119,484 kB` (`267,084 kB` later in
    session).
  * Runtime introspection confirms `475` pre-loaded `sys.modules`, all
    `8` FastAPI routes (`/openapi.json`, `/docs`,
    `/docs/oauth2-redirect`, `/redoc`, `/execute_bash`,
    `/execute_python`, `/save_file`, `/fetch_file`), and
    `user_middleware` classes `['CORSMiddleware', 'BaseHTTPMiddleware']`
    (`fastapi 0.92.0`, `starlette 0.26.1`, `uvicorn 0.17.6`), with
    `BaseHTTPMiddleware` dispatching `block_localhost` (via
    `m.options['dispatch']`).
  * `block_localhost` bytecode logic is identical to Configuration A
    (rejecting `localhost`, `127.0.0.1`, and `0.0.0.0` with HTTP `403`),
    shifted `+110` lines from lines `322–350`
    (`../part2-the-machinery/data/block-localhost-dis.txt`) to lines
    `432–460` (`evidence/morning-bundle/block_localhost_dis.txt`).
* **Zombie Reaping & Background Daemons** `[Tier 1: Directly Observed]`:
  `ps aux` shows active services `shell_wrapper.py` (PID 1),
  `Xtigervnc :1` (PID 15, RFB `003.008`, security type `[1]` None),
  `tmux`/`stterm` (PIDs 49/53/71/77), `webrtc_server.py` (PID 68), and
  `vmaas_tiered_fs --sweep_only=true` (PID 69, TTL scopes
  `tool_output_artifacts`, `user_uploaded_files`, `uploaded_files`,
  `file_generation`), alongside `54` `<defunct>` processes
  (`[chromium]`, `[chrome_crashpad]`, `[python3]`) out of `69` total
  tasks (`1 R`, `14 S`, `54 Z`). PID 1's signal masks (`SigIgn:
  0000000001001000`, `SigCgt: 00000001000044ea`) show `SIGCHLD` is
  neither ignored nor caught, and a double-fork test confirmed an
  orphan's parent is PID 1 — so dead children reparented to PID 1
  accumulate as zombies because PID 1 never calls `wait()`.

Sources: Morning PID 1 footprint, `execute_python_raw`, and `rfb_5901`
from `evidence/morning-bundle/track2_machinery.json`; `475`
`sys.modules` and `8` routes from
`evidence/morning-bundle/dynamo_routes.json`; OpenAPI `cwd` diff from
`evidence/morning-bundle/openapi.json` vs.
`../part2-the-machinery/data/openapi-schema.json`; bytecode line shift
(`432–460` vs. `322–350`) from
`evidence/morning-bundle/block_localhost_dis.txt` and
`../part2-the-machinery/data/block-localhost-dis.txt`; live `cwd` test,
`CORSMiddleware`/`BaseHTTPMiddleware` (`block_localhost`), `ps aux`,
`54 Z`, and PID 1 `Sig*` masks from
`../part2-the-machinery/data/pid1_middleware_and_ps.txt`;
`fastapi`/`starlette`/`uvicorn` versions from
`../part2-the-machinery/data/toolchain_packages_fonts.txt`; double-fork
reparenting from `evidence/fresh-probe-live/step20-dm-mfs-output.txt`.

---

## 5. Workload & Runtime Telemetry

`[Tier 1: Directly Observed]` unless noted. Raw outputs:
`evidence/fresh-probe-live/step19-workload-runtime-output.txt`.

* **FP64 compute**: A 2048³ double-precision GEMM (NumPy / OpenBLAS)
  runs at **156.16 GFLOP/s** (28 iterations in 3.080 s; ~81% of the
  2-core AVX-512 FP64 peak). The CPU exposes full AVX-512 (`f/dq/cd/bw/
  vl/ifma/vbmi/vbmi2/vnni/bitalg/vpopcntdq/bf16/fp16`) plus VAES,
  VPCLMULQDQ, GFNI, and SHA-NI; **no AMX flags** are present. Two
  physical cores, no SMT (`thread_siblings_list` `cpu0="0"`,
  `cpu1="1"`); L3 is `504 MiB`. A 2-worker run doing twice the work of a
  1-worker run takes 0.157 s vs 0.140 s — **1.78× scaling** (89%
  efficiency). Steal time is ~0: 84 ticks of 3,075,110 (0.0027%), and
  **zero increase during the sustained 2-core GEMM** — both vCPUs are
  real and there is no host CPU contention. The day's I/O variance is
  therefore a storage-path effect, not CPU contention.
* **Timer slack is fixed, not proportional**: A 20-sample `nanosleep`
  curve shows an overhead of ~54–68 µs at every requested duration
  (50 µs → 54.36 µs; 200 µs → 65.83 µs; 500 µs → 66.59 µs, mean 564.51 /
  median 566.59 µs; 2000 µs → 68.26 µs).
* **Direct I/O vs buffered I/O**: `O_DIRECT` on `/working_dir` gives
  16 MB sequential write+fsync at **714.81 MB/s** and sequential read at
  **4754.76 MB/s**; 4 KB random read/write averages **0.026 ms** (p95
  ~0.032 ms) — versus buffered `open+write+close` at 1.788 ms (~70×
  slower). The buffered cost is `open`/`close` plus XFS allocation, not
  the device.
* **XFS `allocsize` confirmed**: Any small write shows
  `st_blocks*512 = 67,108,864` (64 MiB) while the file is **open**,
  reclaimed to the actual size on close. This is the
  `allocsize=65536k` speculative preallocation; file handles must be
  explicitly closed or the 64 MiB extent stays pinned.
* **Root overlay is a third storage tier**: `/var/tmp` (overlayfs)
  gives 4 KB avg 0.981 ms / p95 5.830 ms and 10 MB write 980.34 /
  read 2050.51 MB/s — faster than vdb XFS for 4 KB, slower for large
  sequential. The overlay is ~48.1 GiB total, 15% used.
* **Bind mounts are separate**: A cross-bind-mount rename
  (`/working_dir` → `/home/spark`) fails with `errno 18 EXDEV`,
  confirming the three host paths are separate bind mounts.
* **The VM never suspends**: `CLOCK_BOOTTIME == CLOCK_MONOTONIC` to the
  microsecond across an idle gap; uptime 20,499.53 s (5.7 h) at
  measurement. The host VM is always-hot, which answers the warm-pooling
  question.

---

## 6. Block & Device-Mapper Topology (Resolved)

`[Tier 1: Directly Observed]`. Raw outputs:
`evidence/fresh-probe-live/step20-dm-mfs-output.txt`.

* `dm-0` is **`ace_vg-root_lv`**, an LVM logical volume backed by `vda`
  (5 GiB, `virtio4`, PCI `0000:00:05.0`).
* `dm-1` is **`docker_snapshot`**, a device-mapper snapshot whose origin
  is `vdc` (50 GiB, read-only) and whose copy-on-write store is `vdd`
  (10 GiB, writable). The name matches `/dev/mapper/docker_snapshot`
  from the morning bundle, which backs `/etc/resolv.conf`,
  `/etc/hostname`, and `/etc/hosts`. The earlier inference that "`vdc`
  backs `dm-1`" was incomplete, not wrong: `vdc` is the origin, `vdd`
  is the COW store.
* **Full PCI map**: slot 1 `virtio0` (rng), slot 2 `virtio1` (pmem),
  slot 3 `virtio2` (vsock), slot 4 `virtio3` (balloon), slot 5 `virtio4`
  (`vda`), slot 6 `virtio5` (`vdb`), slot 7 `virtio6` (`vdc`), slot 8
  `virtio7` (`vdd`). The block map is now complete at Tier 1.
* **Disk queue** (`/dev/vdb`): `nr_requests 256`, `read_ahead_kb 128`,
  `max_sectors_kb 1280`, `write_cache: write back`, scheduler
  `mq-deadline`. The write-back cache plus 1.25 MB maximum I/O size
  explain the fast buffered writes; `fsync` forces a cache flush
  (4 ms typical, 84 ms tail when the host disk is busy).

---
## 7. Capabilities Check: Lattice Boltzmann Vortex Street

`[Tier 1: Directly Observed]`. Raw outputs:
`evidence/fresh-probe-live/step21-lbm-results.txt`. An end-to-end offline
workload (D2Q9 Lattice Boltzmann, 420×120 grid, flow past a circular
cylinder at Re = 150, 6,000 timesteps) was run to validate Configuration B
as a usable environment, not just to document it.

* **Physics (ground truth)**: Measured Strouhal number **St = 0.1831**
  (shedding frequency `f = 0.00061` / timestep), matching the theoretical
  0.18–0.19 for a von Kármán vortex street at Re = 150. The solver is
  numerically correct.
* **Throughput**: **6.53 MLUPS** (302.4M lattice updates in 46.342 s
  wall time), below the 15–40 MLUPS expected for a vectorized NumPy
  implementation. This is an implementation artifact, not a VM limit:
  the run uses ~940 MB/s of memory bandwidth against the ~6 GB/s
  measured in §5, and the FP64 GEMM (§5) proves the CPU is fast. Peak
  RSS was **86.75 MiB**.
* **GUI render**: The report was served over loopback and opened in GUI
  Chromium on `DISPLAY=:1`. The window mapped (`0x400002`,
  `1280x900+40+40`) and was captured with `ffmpeg -f x11grab` — but the
  page itself was **blocked by the managed Chrome policy** ("This page
  is blocked. Your organization doesn't allow you to view this site."),
  so the screenshot shows a blocked page, not the report. See §8.

---

## 8. Managed Chrome Policy

`[Tier 1: Directly Observed]`. Raw outputs:
`evidence/fresh-probe-live/step22-chrome-policy.txt`. The policy file is
`/etc/chromium/policies/managed/chrome_policy.json` (2,845 bytes, dated
Jan 1 2000 — a baked-in placeholder; the directory is dated Oct 30
15:17, the image build). The `recommended/` directory does not exist.
The file contains a duplicate `FullscreenAllowed: false` key (both
occurrences `false`), suggesting it was assembled by merging two policy
sets.

* **URL posture is default-deny**: The blocklist ends with a `*`
  catch-all, so everything is blocked unless explicitly allowed. The
  blocklist names `localhost`, `127.0.0.1`, `0.0.0.0`,
  `accounts.google.com`, `file://*`, `chrome://*`, and two files of one
  extension (`chrome-extension://bedihmapklmlijmdcinimjbifcbakppm/
  worker_bin.js`, `.../pinned_tab.html`). The allowlist names
  `blob://*`, `data://*`, `file:///tmp/*`, `file:///working_dir/*`,
  `http://*`, `https://*`, `chrome://newtab`, `chrome://new-tab-page`,
  `slack://*`, `chrome-devtools://*`, `devtools://*`, and thirteen
  specific `https://accounts.google.com/*` OAuth/sign-in endpoints
  (accounts.google.com is blocked generally, but the sign-in flows are
  carved out).
* **Why the render was blocked, and the workaround**: Chrome resolves
  blocklist vs allowlist by specificity. The blocklist's `127.0.0.1` (a
  specific host) beats the allowlist's `http://*` (a scheme wildcard),
  so `http://127.0.0.1:<port>/report.html` is blocked. But `http://*`
  beats the `*` catch-all, so `http://127.0.0.2:<port>/...` renders
  cleanly. The block is **address-specific, not range-based**: other
  `127.0.0.0/8` addresses are not covered by the policy — and the API
  daemon's `block_localhost` middleware likewise only matches
  `localhost`, `127.0.0.1`, and `0.0.0.0`, not `127.0.0.2`. Serving the
  report on `127.0.0.2` is the documented workaround for the render
  step.
* **Unexplained discrepancy**: `file:///working_dir/*` is in the
  allowlist and is more specific than the blocklist's `file://*`, so it
  should be permitted — yet `file:///working_dir/vortex_run/report.html`
  was blocked in practice. The exact mechanism is unconfirmed (see §9).
* **Hardening**: No browser sign-in, no sync, no password manager, no
  autofill, no guest mode, no adding persons, no Labs, no DevTools
  (`DeveloperToolsAvailability: 0`), no audio/video capture, no file
  selection dialogs, geolocation blocked, filesystem read/write guards
  blocked, downloads restricted (`DownloadRestrictions: 4`), Incognito
  disabled, no bookmark bar or bookmark editing, no media router, no
  fullscreen, no task-manager process termination. Printing is enabled;
  the default search provider is Google.

---

## 9. Open Questions & Remaining Unverified Claims

Most questions from the initial morning bundle are now settled at
`[Tier 1: Directly Observed]` (GRTE v5 ELF interpreter,
`/etc/chromium.d/dev-shm` threshold, Chromium GUI/headless behavior,
`/run/docker.sock` mount presence, `mfs` log directory state,
multi-sample Configuration B `nanosleep`, device-mapper topology, and
zombie reparenting). Four narrow items remain open:

| Open / Unverified Item | Current Status | Material That Would Settle It |
| :--- | :--- | :--- |
| **Owner of listening TCP ports `12345` and `8080`** | `127.0.0.1:12345` sits below `ip_local_port_range` (`32768–60999`, indicating a configured service), while `127.0.0.1:42029` is ephemeral (`[Tier 2: Inference]`). Mapping `12345` / `8080` to owning PIDs requires reading root-owned `/proc/<pid>/fd` (out of scope). | Process-to-socket mapping (`ss -tlnp`) from a privileged host/VM context. |
| **Configuration A `nanosleep(500 us)` distribution & 4 KB benchmark script** | Configuration A's `1,392.88 us` `nanosleep` remains a single sample, and `part1-the-cage/evidence/syscall_probe.py` in the baseline contains only the 13-syscall harness rather than the original I/O loop. | Multi-sample `nanosleep` run and original I/O benchmark script from a Configuration A session. |
| **Root cause of headless Chromium hang (`exit 124`)** | Narrowed, not solved: not the zygote (flag isolation), not the sandbox, and not the Fontconfig path (the error persists with `FONTCONFIG_FILE` set). The `zygote:313` error is a teardown artifact. The stall is upstream (renderer / Mojo / IPC), in the unshown middle of the verbose log. | The middle of a verbose (`--enable-logging=stderr --v=1`) log on `about:blank`, or a renderer-level trace. |
| **`file:///working_dir/*` blocked despite allowlist entry** | The policy allowlists `file:///working_dir/*`, which is more specific than the blocklist's `file://*` — yet `file:///working_dir/vortex_run/report.html` was blocked in practice. Mechanism unconfirmed. | Direct re-test of the `file://` URL in GUI Chromium, with the exact block message. |

Sources: Listening ports (`12345`, `8080`, `42029`) and
`ip_local_port_range` (`32768–60999`) from
`../part1-the-cage/evidence/network_proc_airgap.txt`; headless flag runs
from `../part2-the-machinery/data/chromium_devshm_and_runs.txt` and the
verbose run from `evidence/fresh-probe-live/step19-workload-runtime-output.txt`;
`file://` block from `evidence/fresh-probe-live/step22-chrome-policy.txt`;
Configuration A baseline from `../part1-the-cage/README.md` and
`../part1-the-cage/evidence/syscall_probe.py`.
