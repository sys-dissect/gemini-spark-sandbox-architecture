# Gemini Spark Systems Architecture: Empirical Sandbox Dissection

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Debian%2012%20%7C%20Linux%204.19--gvisor-informational.svg)](part1-the-cage/)
[![Isolation](https://img.shields.io/badge/Isolation-gVisor%20Sentry%20%2B%20Plan%209-orange.svg)](part1-the-cage/)
[![Engine](https://img.shields.io/badge/Engine-FastAPI%20%2F%20Uvicorn%20%7C%20Python%203.11-blueviolet.svg)](part2-the-machinery/)
[![Memory Plane](https://img.shields.io/badge/Memory-Jetski%20mfs%20(GRTE%20v5)-red.svg)](part3-the-mind/)
[![Methodology](https://img.shields.io/badge/Methodology-Black--Box%20Verification-brightgreen.svg)](part1-the-cage/evidence/)

> Google engineered the sandbox. We audited the syscalls.<br>
> Independent black-box systems verification of Google's Gemini Spark execution sandbox (`c_<session_id>`), with empirical measurements, bytecode inspection, and runtime telemetry extracted directly from inside the live workload cell.

---

## Executive Summary

This repository presents an empirical, non-invasive systems characterization of Google's **Gemini Spark** execution sandbox. Rather than analyzing high-level prompt templates or public marketing disclosures, this study documents the guest execution environment from the inside out—verifying kernel syscall arbitration, storage durability semantics, inter-process communication, visual automation pipelines, and cognitive state persistence.

The architecture comprises five primary operational layers:

1. **Virtualization & Syscall Confinement**: A user-space application kernel (**Google gVisor / Sentry**) providing multi-tenant isolation, emulating a `Linux 4.19` ABI, enforcing an absolute device-level network air-gap (zero routes, `lo` only), and mediating all hardware access.
2. **Storage Subsystem**: A hybrid architecture consisting of an ephemeral root `overlayfs` combined with persistent storage mediated over **Plan 9 (9P / Gofer)** file descriptors backed by Google's internal **VMaaS (VM-as-a-Service)** storage fabric.
3. **Execution Engine & IPC**: A PID 1 supervisor running an in-memory **FastAPI/Uvicorn** daemon (`dynamo_exec.py`) that drops privileges to unprivileged user `spark` (`UID 1235`), enforces localhost blocking via custom bytecode middleware, and maintains a stateful Python execution scope across tool invocations.
4. **Visual & Document Synthesis Pipeline**: A 1440p **TigerVNC** virtual display hosting an `stterm`/`tmux` console, coupled with native `ffmpeg` (`x11grab`), `xdotool`, and a 314-family typography stack (full Noto CJK and Cairo/Pango vector libraries) tailored for multimodal document processing.
5. **Cognitive State Plane**: A dedicated Google Runtime Environment (GRTE v5) FUSE daemon (**Jetski `mfs`**) connected via an air-gapped Unix domain socket to the host-side **Dumbo / Remy** memory service, implementing schemas for memory retrieval, snapshots, and background "Dreaming" consolidation routines.

---

## Provenance Framework & Classification

To ensure strict scientific and engineering rigor, all statements and measurements in this repository are categorized according to a three-tier evidential framework:

* **[Tier 1: Directly Observed]**: Directly measured via command execution, raw kernel outputs, bytecode dumps, or in-memory reflection from within the live container session.
* **[Tier 2: Architectural Inference]**: Inferred from observed configurations, code artifacts, or standard virtualization design patterns where host-side infrastructure cannot be directly queried.
* **[Tier 3: Platform Definition]**: Sourced from platform tool schemas, system prompts, or public documentation; unverified from within the container boundary.

---

## Architectural Topography

```
+=============================================================================================+
|                                    1. USER / CLIENT LAYER                                   |
|   Prompt / Instructions (Web UI, API) <-----------------------> Formatted Responses / Files  |
+=============================================================================================+
                                              | HTTPS / RPC
                                              v
+=============================================================================================+
|                             2. AGENT & ORCHESTRATION LAYER                                  |
|   +-------------------------------------------------------------------------------------+   |
|   |  Gemini Agent Engine                                                                |   |
|   |  - Multi-Turn Reasoning & Tool Dispatcher                                           |   |
|   |  - Cognitive Memory Sync: Out-of-band state updates to Dumbo / Remy backend        |   |
|   |  - Execution Dispatcher: Segregates Cloud Tools vs. Browser VM vs. Code Sandbox    |   |
|   +------------------------------------------+------------------------------------------+   |
+==============================================|==============================================+
                                               | Host RPC / runsc exec (UID 0, PPID 0)
                                               v
+=============================================================================================+
|                              3. HOST INFRASTRUCTURE LAYER                                   |
|   +-------------------------------------------------------------------------------------+   |
|   |  Host Node (Google Compute Engine — per DMI product_name)                            |   |
|   |  - gVisor Sentry: User-space application kernel intercepting guest syscalls          |   |
|   |  - gVisor Gofer: Mediates host filesystem I/O over Plan 9 (9P) file descriptors     |   |
|   |  - Remy / Dumbo Backend: Host memory proxy serving semantic vector indexes          |   |
|   +------------------------------------------+------------------------------------------+   |
+==============================================|==============================================+
                                               | Syscall Virtualization, 9P FDs, & IPC Sockets
                                               v
+=============================================================================================+
|                           4. SANDBOXED WORKLOAD CELL (container)                            |
|                                                                                             |
|   [ gVisor Security Boundary: Linux 4.19.0-gvisor | CapEff: 0x0 | 2 vCPUs | 5 GiB RAM ]     |
|                                                                                             |
|   +-------------------------------------------------------------------------------------+   |
|   |  Filesystem Hierarchy                                                               |   |
|   |  - Root (/): Ephemeral OverlayFS (writes discarded on container recycle)             |   |
|   |  - 9P Host Mounts: /working_dir, /home/spark, /mnt/agentdata (Persistent host PD)   |   |
|   |  - In-Memory / FUSE: /run/user/1235/memory & /working_dir/memory (Jetski mfs)        |   |
|   +-------------------------------------------------------------------------------------+   |
|                                                                                             |
|   +-------------------------------------------------------------------------------------+   |
|   |  Network Namespace                                                                  |   |
|   |  - Loopback (lo) only | Zero external interfaces | Zero routing table entries       |   |
|   |  - Direct socket attempts to external or metadata IPs (169.254.169.254) -> ENETUNREACH|   |
|   +-------------------------------------------------------------------------------------+   |
|                                                                                             |
|   +-------------------------------------------------------------------------------------+   |
|   |  Cognitive State Subsystem (Jetski Memory FUSE)                                     |   |
|   |  - PID 66: sudo -u spark mfs daemon start --config=/etc/mfs/config.txtpb (GRTE v5)  |   |
|   |  - Sockets: /working_dir/.gemini/jetski/memory/daemon.sock                          |   |
|   |  - Upstream Host Bridge: /ipc/remy_memfs_proxy.sock (Dumbo IPC Proxy)               |   |
|   |  - Lifecycle API: "Dream" request/response symbols (learning::gemini::memory)       |   |
|   +-------------------------------------------------------------------------------------+   |
|                                                                                             |
|   +-------------------------------------------------------------------------------------+   |
|   |  Command Execution & IPC Engine                                                     |   |
|   |                                                                                     |   |
|   |   Host Injected Command (runsc exec -> curl [UID 0, PPID 0])                        |   |
|   |            │                                                                        |   |
|   |            ▼ (Unix Domain Socket: /tmp/shell.sock)                                  |   |
|   |   +-----------------------------------------------------------------------------+   |   |
|   |   | PID 1: python3 /root/shell_wrapper.py (UID 1235, Uvicorn + FastAPI)         |   |   |
|   |   | Middleware: block_localhost (Drops 127.0.0.1 TCP -> 403 Forbidden)          |   |   |
|   |   +--------------------------------------+--------------------------------------+   |   |
|   |                                          |                                          |   |
|   |               ┌──────────────────────────┴──────────────────────────┐               |   |
|   |               ▼                                                     ▼               |   |
|   |   +───────────────────────────────+     +───────────────────────────────────────+   |   |
|   |   | POST /execute_bash            |     | POST /execute_python                  |   |   |
|   |   | Stateless fork -> subprocess  |     | Stateful in-memory exec()             |   |   |
|   |   | Spawns as UID 1235 (spark)    |     | Persistent namespace:                 |   |   |
|   |   | Returns stdout/stderr text    |     | _PYTHON_EXEC_SCOPE dictionary         |   |   |
|   |   +───────────────────────────────+     +───────────────────────────────────────+   |   |
|   |                                                                                     |   |
|   |   +-----------------------------------------------------------------------------+   |   |
|   |   | Virtual Desktop Subsystem (DISPLAY=:1)                                      |   |   |
|   |   | - PID 8: Xtigervnc :1 -geometry 2560x1440 -FrameRate 30 (UID 0)             |   |   |
|   |   | - PID 49: stterm -e tmux attach-session -t main (UID 0 -> sudo spark)       |   |   |
|   |   | - 314 vector typography families + ffmpeg x11grab + xdotool                 |   |   |
|   |   +-----------------------------------------------------------------------------+   |   |
|   +-------------------------------------------------------------------------------------+   |
+=============================================================================================+
```

---

## Research Tracks

The dissection is organized across three primary tracks:

* 📁 [**Part 1: The Cage — Sandbox Confinement, Hypervisor Mediation & Syscall Profile**](part1-the-cage/)
  User-space syscall virtualization via gVisor (Sentry), the 13-syscall ctypes probe matrix, unshare flag behavior, Plan 9 (`9p`) host filesystem mediation (Gofer), ephemeral root overlay versus persistent 9P storage benchmarks, zero-route network namespace isolation, and cgroups v1 resource quotas.

* 📁 [**Part 2: The Machinery — Binary Architecture, Daemon Lifecycle & IPC Matrix**](part2-the-machinery/)
  PID 1 supervisor architecture (`shell_wrapper.py` & `dynamo_exec.py`), live FastAPI route tables, path traversal semantics in `_resolve_working_path`, in-memory Python statefulness (`_PYTHON_EXEC_SCOPE`), disassembled Python 3.11 bytecode of the `block_localhost` middleware, workload profile (document synthesis workbench), packaging PATH anomalies, and cross-persona extension artifacts.

* 📁 [**Part 3: The Mind — Cognitive Engine, Memory FUSE & the "Dream" Lifecycle API**](part3-the-mind/)
  The Jetski Memory FUSE subsystem (`mfs`), raw protobuf configuration (`config.txtpb`), demangled C++ symbol extraction (`learning::gemini::memory`), Dumbo/Remy IPC proxying over `/ipc/remy_memfs_proxy.sock`, the autonomous "Dream" consolidation cadence, and host-level `runsc exec` command injection mechanics.

---

## Corrections Log

During the investigation, several initial observations were challenged, refined, or corrected through empirical re-probing:

1. **`0o40700` Mode Bit Correction**: An initial report labeled `/root/shell_wrapper.py` as a directory based on mode bits. Re-probing confirmed `0o40700` belonged to the parent directory `/root`; `shell_wrapper.py` is an unreadable script file inside `/root`, which is owned by `root:root` with permissions `0700`.
2. **UID 1235 vs. UID 0 Dual-Privilege Structure**: Corrected the early assumption that "everything runs as unprivileged UID 1235." While worker commands run as `spark` (`UID 1235`), background system processes (`Xtigervnc`, entrypoint supervisor, and host `runsc exec` injected bridges) run as container `root` (`UID 0`).
3. **`memfd_create` Pointer Resolution**: An initial `EFAULT` (errno 14) result was recognized as a probe flaw (passing a NULL pointer). Re-probing with a valid string buffer (`ctypes.c_char_p(b"audit_test")`) succeeded, allocating file descriptor `4`.
4. **Shared-Image Thesis Calibration**: The presence of `/usr/bin/entrypoint_browser.sh` initially suggested a unified dual-persona runtime. File existence audits proved `/usr/bin/dynamo_browser_wrapper.py` and the egress CA cert are absent; browser automation components are present, but the browser execution runtime is absent.
5. **Persistence Scope**: Clarified that container root (`overlayfs`, `/tmp`) is ephemeral across recycles, whereas `/working_dir`, `/home/spark`, and `/mnt/agentdata` are persistent Plan 9 (`9p`) mounts.

---

## Repository Structure

```text
.
├── LICENSE
├── README.md                            # Executive Summary, Architecture Topography, Framework
├── part1-the-cage/                      # Track 1: Systems & Confinement Whitepaper
│   ├── README.md                        # Full systems whitepaper (gVisor, 9P, Network Air-Gap)
│   └── evidence/                        # Raw empirical probe logs & command output
│       ├── 9p_mount_fd_map.json         # Complete 9P descriptor map (FDs 5–12)
│       ├── agentdata-hierarchy.txt      # Parent pool layout & /dev/shm tmpfs limits
│       ├── debian_sources_deb822.txt    # Debian Bookworm snapshot pinning (20260918)
│       ├── dmesg-gvisor-boot.log        # Captured gVisor satirical boot sequence
│       ├── gvisor_sysctl_census.json    # Exact 17 emulated /proc/sys/kernel/ nodes
│       ├── mountinfo-procfs.json        # Complete 9P and overlayfs mount table
│       ├── network-failure-probes.json  # Socket domain & ENETUNREACH logs
│       ├── syscall_probe.py             # Complete 13-syscall ctypes test script with arguments
│       └── uptime_probe.log             # Multi-hour continuous uptime log (4.5h)
├── part2-the-machinery/                 # Track 2: Binary Architecture, Daemon Lifecycle & IPC
│   ├── README.md                        # PID 1 analysis, FastAPI router, privilege mapping, VNC
│   └── data/                            # Extracted schemas, bytecode dumps & inventories
│       ├── block-localhost-dis.txt      # Disassembled bytecode of block_localhost middleware
│       ├── debian_package_sections.txt  # Debian section census (387 JS vs 90 Python)
│       ├── dynamo-exec-routes.json      # In-memory FastAPI route table dump
│       ├── dynamo_manifest.json         # Chrome extension Manifest V3 & experimental APIs
│       ├── env_dump.json                # Verbatim environment variable table
│       ├── fdinfo_9p_probe.txt          # Open descriptors & 9P seek tracking in Sentry
│       ├── fastapi_route_census.txt     # In-memory route table census from dynamo_exec.http_app
│       ├── openapi-schema.json          # Live canonical OpenAPI 3.0.2 schema from shell.sock
│       ├── proc_net_unix.txt            # Active Unix domain socket table with inodes
│       ├── worker_bin_telemetry.txt     # Extracted WebSocket strings and APC schemas
│       ├── vnc_visual_telemetry.json    # TigerVNC flags, window hierarchy & framebuffer metrics
│       └── x11_mit_shm_telemetry.txt    # X11 MIT-SHM extension and XKB rules probe
└── part3-the-mind/                      # Track 3: Cognitive Engine & Memory FUSE
    ├── README.md                        # Jetski Memory FUSE (mfs), Dumbo proxy & Dreaming loops
    └── data/                            # Memory engine schemas and debug artifacts
        ├── google_internal_absl_flags.txt # LOAS, Chubby, Stubby, Dapper, Monarch flags
        ├── mfs_cli_status.txt           # Live mfs status and group CLI hierarchy
        ├── mfs_config.txtpb             # Raw protobuf configuration for the Jetski FUSE daemon
        ├── mfs_debug.txt                # Diagnostic report, FUSE mount options, Buganizer ID
        └── mfs_symbols.txt              # 350+ demangled C++ symbols from learning::gemini::memory
```

---

## Open Questions & Falsifiability Status

All three primary architectural questions raised during the initial dissection have now been **empirically resolved**:

1. **Recycle Cadence Policy** — **[RESOLVED (§1.12)]**:
   * *Finding*: Uptime telemetry at turn start registered `16,199.99` seconds (~4.50 hours). The container cell is not pruned on short 5-minute or 15-minute idle windows, remaining active as long as the host session lease persists.
2. **The Purpose of `/home/spark`** — **[RESOLVED (§1.13)]**:
   * *Finding*: While `/working_dir` serves as `$HOME` for tool subshells, `/home/spark` houses persistent profiles and clipboard integration (`.tmux.conf`, `xclip`) for the TigerVNC virtual desktop session, with its `.bashrc` delegating execution context to `/working_dir`.
3. **The Sudo-Grant Rationale** — **[RESOLVED (§1.14)]**:
   * *Finding*: Deb822 repository auditing confirmed that the rootfs was constructed from an upstream Debian Bookworm snapshot frozen on September 18, 2026 (`20260918T000000Z`). Sudo delegation is an inherited build-time template artifact that remains completely inert in production due to device-level network isolation (`ENETUNREACH`).

---

## Methodology & Safety

All measurements were performed via non-invasive, in-situ systems diagnostics from within an authorized container session. No exploits were attempted, no host boundaries were breached, and all user-specific session paths (`c_<hex>`) and identifiers have been scrubbed.

---

## About `sys-dissect`

**sys-dissect** is an independent systems research initiative dedicated to the empirical dissection, boundary verification, and containment analysis of production AI runtimes and autonomous agent infrastructure.

### Research Principles

* **Measure Runtime Realities:** Architecture blueprints and design disclosures present intended models; empirical probing verifies actual kernel filters, hypervisor mediation, and storage behavior.
* **Zero-Trust Boundary Analysis:** Model token streams and prompt wrappers are never trusted for authorization; isolation guarantees must be physically enforced out-of-band by the operating system, container runtime, and hypervisor.
* **Responsible & Non-Invasive:** All diagnostics are conducted via bounded, black-box inspection inside authorized workload sessions. No exploits are staged, no host boundaries are breached, and all proprietary tokens or user telemetry are strictly sanitized before publication.

---

## License & Attribution

Published by [sys-dissect](https://github.com/sys-dissect). Released under the MIT License.
