# Part 2: The Machinery — Binary Architecture, Daemon Lifecycle & IPC Matrix

## Executive Summary

Inside the sandboxed workload cell, the execution engine does not run standard systemd or an interactive shell daemon. Instead, execution and environment state are mediated through an in-memory **FastAPI/Uvicorn** application loaded at PID 1 (`/root/shell_wrapper.py` importing `/root/dynamo_exec.py`). The daemon operates under an unprivileged user context (`UID 1235`, `spark`), exposes specialized HTTP endpoints over a local Unix domain socket (`/tmp/shell.sock`), and installs custom bytecode middleware to reject all TCP requests originating from localhost.

In parallel with command execution, the container hosts an integrated document and visual processing workbench consisting of a 1440p **TigerVNC** virtual display, terminal multiplexers (`stterm`/`tmux`), screen capture primitives (`ffmpeg x11grab`), vector graphics libraries, and a 314-family font registry.

---

## 2.1 PID 1 Architecture & Execution Engine (`dynamo_exec.py`)

**[Tier 1: Directly Observed]**

Inspection of `/proc/1/cmdline`:
```bash
/usr/bin/python3\x00/root/shell_wrapper.py\x00--socket_path=/tmp/shell.sock\x00--mock_logging=True\x00
```

* **Daemon Implementation**: PID 1 initializes from `/root/shell_wrapper.py` and runs an in-memory **FastAPI/Uvicorn** server defined in `/root/dynamo_exec.py`.
* **Privilege Separation**: While initialized from `/root`, PID 1 drops privileges to `UID 1235` (`spark`, `GID 1235`) with capabilities completely cleared (`CapEff: 0x0`, `CapPrm: 0x0`).

### Endpoint Specifications

*(See extracted schema: [`data/dynamo-exec-routes.json`](data/dynamo-exec-routes.json))*

#### 1. `POST /execute_bash`
* **Request**: `{"command": "<bash_script>"}`
* **Implementation**: Invokes `/usr/bin/bash -O expand_aliases -c <command>` as `UID 1235`.
* **Non-blocking I/O**: Employs Python `selectors` with 4096-byte non-blocking chunks on stdout and stderr pipes to avoid buffer deadlocks.
* **Timeout Handling**: **No timeout is hardcoded inside the container.** Timeouts are managed externally by the host-side RPC caller. When the RPC caller times out, it severs the connection; unkilled subshell processes continue executing until completion, reparented to PID 1.

#### 2. `POST /execute_python`
* **Request**: `{"code": "<python_code>"}`
* **Implementation**: Executes Python code via `exec()` directly inside a global dictionary named `_PYTHON_EXEC_SCOPE`.
* **In-Memory Statefulness**: Variables, imported modules, functions, and class definitions declared during one turn persist in memory across sequential tool calls within the same container lifecycle. Generational GC thresholds are standard CPython 3.11 defaults (`[700, 10, 10]`) with recursion limit `1000`.

#### 3. `POST /save_file` & `POST /fetch_file`
* Implements base64 serialization to read and write files within the persistent `/working_dir` mount. Paths are resolved via an internal helper function `_resolve_working_path`.

---

## 2.2 Path Traversal in `_resolve_working_path`

**[Tier 1: Directly Observed]**

Decompilation and inspection of `/root/dynamo_exec.py` reveals the file path resolution logic:

```python
def _resolve_working_path(file_path: str, base_dir: str = WORKING_DIR) -> str:
    """Resolves a file path against the given base directory if it is relative."""
    if os.path.isabs(file_path):
        return os.path.realpath(file_path)
    return os.path.realpath(os.path.join(base_dir, file_path))
```

### Empirical Probe Results
* `test.txt` $\to$ `/working_dir/test.txt`
* `../../../../etc/passwd` $\to$ `/etc/passwd`
* `/tmp/outside.txt` $\to$ `/tmp/outside.txt`

### Severity & Threat-Model Assessment
* **API-Contract Discrepancy**: The platform tool documentation implies file operations are scoped strictly within the user workspace. However, `_resolve_working_path` performs path canonicalization without verifying common-path prefix containment (`os.path.commonpath`).
* **Threat-Model Impact (Minimal)**: In context, the security impact within the guest is negligible. The caller already possesses unrestricted arbitrary command execution via `/execute_bash` running under the identical UID (`spark`, `1235`). Consequently, no privilege escalation or cross-tenant boundary breach occurs.

---

## 2.3 Disassembled Bytecode: `block_localhost` Middleware

**[Tier 1: Directly Observed]**

To prevent unprivileged code executing inside the sandbox from contacting the internal HTTP server via the loopback interface, `dynamo_exec.py` installs a custom FastAPI middleware.

The verbatim Python 3.11 bytecode disassembly of `block_localhost` ([`data/block-localhost-dis.txt`](data/block-localhost-dis.txt)) shows the exact short-circuit mechanism:

```text
334           6 LOAD_FAST                0 (request)
              8 LOAD_ATTR                0 (client)
             18 POP_JUMP_FORWARD_IF_FALSE   123 (to 266)

335          20 LOAD_FAST                0 (request)
             22 LOAD_ATTR                0 (client)
             32 LOAD_ATTR                1 (host)
             42 LOAD_CONST               1 ('localhost')
             44 COMPARE_OP               2 (==)
             50 POP_JUMP_FORWARD_IF_TRUE    32 (to 116)

336          52 LOAD_FAST                0 (request)
             54 LOAD_ATTR                0 (client)
             64 LOAD_ATTR                1 (host)
             74 LOAD_CONST               2 ('127.0.0.1')
             76 COMPARE_OP               2 (==)
             82 POP_JUMP_FORWARD_IF_TRUE    16 (to 116)

337          84 LOAD_FAST                0 (request)
             86 LOAD_ATTR                0 (client)
             96 LOAD_ATTR                1 (host)
            106 LOAD_CONST               3 ('0.0.0.0')
            108 COMPARE_OP               2 (==)
            114 POP_JUMP_FORWARD_IF_FALSE    75 (to 266)

339     >>  116 LOAD_GLOBAL              5 (NULL + logging)
            ...
344         200 LOAD_GLOBAL             12 (fastapi)
            212 LOAD_ATTR                7 (responses)
            222 LOAD_METHOD              8 (Response)
345         244 LOAD_CONST               5 ('Access from localhost is forbidden.')
346         246 LOAD_CONST               6 (403)
            254 CALL                     2
            264 RETURN_VALUE
```

**Operational Impact**: Any inbound HTTP request arriving over TCP matching `localhost`, `127.0.0.1`, or `0.0.0.0` is immediately short-circuited with `403 Forbidden`. The API server can only be reached over the `/tmp/shell.sock` Unix domain socket.

---

## 2.4 Workload Profile & Packaging PATH Anomaly

**[Tier 1: Directly Observed]**

### Specialized Workload Profile: Document Synthesis Workbench
Rather than a generic Linux development shell, the environment is provisioned as an enterprise-grade document extraction, computer vision, and vector graphics workbench:

* **Deep Learning Classifier**: Google `magika` (standalone 32.5 MB deep-learning file identification binary).
* **Document Extraction & Rendering**: `weasyprint` (70.0), `pdfplumber`, `pypdfium2`, `markitdown`, `pdf2txt.py`, `dumppdf.py`, `vba_extract.py`, `runxlrd.py`, `striprtf`, `markdownify`.
* **OCR & Computer Vision**: `pytesseract`, `opencv-python` (4.11.0), `sharp` (libvips for Node.js).
* **Office Document Generation**: `pptxgenjs` (Node.js PowerPoint generator), `python-docx`, `python-pptx`, `openpyxl`.

### The Packaging Tell: PATH Misconfiguration
* `/opt/spark/bin` is exported in the system `$PATH`, but is **completely empty** (0 entries).
* The populated binary directory containing the active toolchain is **`/opt/spark/local/bin`**, which is **omitted from `$PATH`**.
* This demonstrates that image construction combined distinct package build prefixes without reconciling environment search paths.

---

## 2.5 Cross-Persona Artifacts: `worker_bin.js` & `entrypoint_browser.sh`

**[Tier 1: Directly Observed]**

Inspection of `/opt/chrome_extensions/dynamo/worker_bin.js` (a ~4.5 MB compiled Chrome extension) provides insight into image layering ([`data/worker_bin_telemetry.txt`](data/worker_bin_telemetry.txt)):

* **Internal Build Path**: `//blaze-out/k8-fastbuild/bin/assistant/boq/lamda/agents/dynamo/extension/worker_bin.jstrimmer_bootstrap.js`.
* **APC Identity**: Log prefix identifies the module as `Service Worker (APC Extension):`, implementing Annotated Page Content schemas (`MutableAnnotatedPageContent.proto`).
* **WebSocket Bridge**: Contains hardcoded client connections to `ws://localhost:8080/ws/register_worker` ("WebSocket connection established with Selenium server").
* **Accessibility Schemas**: Implements `MutableAXTreeUpdate.proto`, `MutableAXNodeData.proto`, `MutableAXRelativeBounds.proto`, and `.chrome.intelligence.modeling.features.forms_ai.proto`.

### Calibrated Shared-Base Thesis
File presence auditing established:
* `/opt/chrome_extensions/dynamo/`: **Present**
* `/usr/bin/entrypoint_browser.sh`: **Present**
* `/usr/bin/dynamo_browser_wrapper.py`: **Absent** (`False`)
* `/usr/local/share/ca-certificates/agent-gateway-ca.crt`: **Absent** (`False`)

**Finding**: The Spark container image is derived from a broader base image shared with browser-automation personas. However, in the Spark code execution flavor, browser execution daemons and egress TLS trust certificates are intentionally pruned.

---

## 2.6 Complete Census & Environment Inventories

**[Tier 1: Directly Observed]**

### Package & Binary Census
* **Debian Packages (`dpkg -l`)**: Exactly **1,309** installed packages.
* **Python Packages (`pip list`)**: Exactly **151** packages installed in `/opt/spark/local/lib/python3.11/dist-packages` (including `numpy 2.1.3`, `scipy 1.15.2`, `pandas 2.2.3`, `onnxruntime 1.30.0`, `ortools 9.14.6206`, `sympy 1.13.3`, `fastapi 0.92.0`, `uvicorn 0.17.6`).
* **Node.js Environment**:
  * Node.js: `v18.20.4`
  * npm: `9.2.0`
  * Module Path: `NODE_PATH=/usr/local/lib/node_modules:/opt/spark/lib/node_modules:/opt/spark/node_modules`
  * Global Pre-installed Bundles (`/opt/spark/lib/node_modules`):
    * `pptxgenjs` (Office Open XML PowerPoint generation)
    * `sharp` (High-performance libvips image processing)
* **Binary Distribution**:
  * `/bin` & `/usr/bin`: 1,090 binaries
  * `/sbin` & `/usr/sbin`: 174 binaries
  * `/opt/spark/bin`: 0 binaries

### Sanitized Environment Variables
*(See full dump: [`data/env_dump.json`](data/env_dump.json))*

```bash
HOME=/working_dir
HOSTNAME=<sandbox_container_id>
MFS_RUNTIME_DIR=/run/user/1235
NODE_PATH=/usr/local/lib/node_modules:/opt/spark/lib/node_modules:/opt/spark/node_modules
PATH=/usr/local/bin:/opt/spark/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PYTHONPATH=/opt/spark/lib/python3/dist-packages:/opt/spark/lib/python3.11/dist-packages:...
```

### Active Unix Domain Sockets (`/proc/net/unix`)
*(See raw table: [`data/proc_net_unix.txt`](data/proc_net_unix.txt))*

| Inode | Type | Path | Bound Process | Description |
| :--- | :--- | :--- | :--- | :--- |
| `162` | STREAM | `/tmp/shell.sock` | PID 1 (`shell_wrapper.py`) | FastAPI command injection socket |
| `18` / `19` | STREAM | `/tmp/.X11-unix/X1` | PID 8 (`Xtigervnc :1`) | TigerVNC X11 display socket |
| `72` | STREAM | `/tmp/tmux-1235/default` | PID 45 (`tmux`) | Terminal multiplexer daemon |
| `149` | STREAM | `/working_dir/.gemini/jetski/memory/daemon.sock` | PID 69 (`mfs daemon`) | Local Jetski IPC socket |
| Host | STREAM | `/ipc/remy_memfs_proxy.sock` | Host Broker (`dumbo`) | 9P-backed host memory proxy |

---

## 2.7 Visual Subsystem & Document Synthesis Pipeline

**[Tier 1: Directly Observed]**

The sandbox is configured with a fully functional X11 graphical display environment:

* **Virtual Display**: PID 8 executes `Xtigervnc :1 -geometry 2560x1440 -depth 24 -rfbport 5901 -FrameRate 30`.
* **Active Window Tree (`xwininfo -root -tree`)**:
  ```text
  xwininfo: Window id: 0x51a (the root window) (has no name)
     1 child:
     0x200005 "tmux": ("st-256color" "st-256color")  2244x1280+60+50  +60+50
  ```
* **X11 Extensions & MIT-SHM Telemetry** ([`data/x11_mit_shm_telemetry.txt`](data/x11_mit_shm_telemetry.txt)):
  * Probing `xdpyinfo -ext MIT-SHM` confirms that **MIT-SHM shared memory extension is supported and active**: `version 1.2 opcode: 131, base event: 68, base error: 128` with `shared pixmaps: yes, format: 2`.
  * Display attributes: `2560x1440` pixels @ 96 DPI, 24 planes TrueColor/DirectColor.
  * Root properties (`xprop -root`): Initialized with standard evdev rules `_XKB_RULES_NAMES = "evdev", "pc105", "us", "", ""`.
* **Screen Capture & Automation**:
  * `ffmpeg` is compiled with native `x11grab` support enabled.
  * `xdotool` is installed for synthetic keyboard typing, mouse navigation, and window resizing against `DISPLAY=:1`.
  * `xclip` is integrated with tmux copy-paste buffers (`xclip -selection clipboard -i / -o`).
* **Typography Stack**:
  * **314 font entries** registered in `fc-list`.
  * Comprehensive East Asian typographic support via **Noto Sans CJK** and **Noto Serif CJK** across Regular and Bold weights (covering Hong Kong, Japan, Korea, Simplified Chinese, and Traditional Chinese).
  * Color emoji and monospace coverage via `NotoColorEmoji.ttf` and `NotoMono-Regular.ttf`.
  * Full vector rendering stack: `libcairo2`, `python3-cairo`, `libpango-1.0-0`, `libpangocairo-1.0-0`, `libharfbuzz0b`, and `libfreetype6`.

---

## 2.8 File Descriptors & Sentry Seek Tracking

**[Tier 1: Directly Observed]**

Inspection of guest process file descriptors and fdinfo ([`data/fdinfo_9p_probe.txt`](data/fdinfo_9p_probe.txt)):

### Worker Descriptors (`/proc/self/fd`)
* `0 -> host:[1]`: Standard input translated from host gVisor FD mapping.
* `1 -> pipe:[165]`: Stdout captured by the FastAPI supervisor selector.
* `2 -> pipe:[165]`: Stderr captured by the FastAPI supervisor selector.

### 9P Seek Tracking (`/proc/self/fdinfo/<fd>`)
Reading `fdinfo` for an open file handle on the 9P-backed `/working_dir` mount:
```text
pos:    0
flags:  02100000
mnt_id: 29
```
* **Architectural Significance**: Confirms that gVisor Sentry maintains and updates file seek positions (`pos`) and mount associations (`mnt_id: 29`) directly in user-space Go memory across host 9P file descriptors.

