# Phoenix V1 setup and design

## Run on KDE Wayland

Use Python 3.11 or later with PySide6. Install Poppler (`pdftotext`), bubblewrap, util-linux (`prlimit`), Kate, Dolphin and GLib (`gdbus`). PDF extraction requires working unprivileged namespaces. The Kate proof also uses system PyGObject with Gio, GStreamer and AT-SPI introspection, PipeWire's GStreamer plugin and `pw-dump`, and libei. Use the distribution's Python if it already imports both `PySide6` and `gi`, or create a venv with `--system-site-packages` using that Python and install the project with `pip install -e .`.

Get [llama.cpp b12359](https://github.com/ggml-org/llama.cpp/releases/tag/b12359) and the [Unsloth Qwen3.5-4B Q4_K_M GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/blob/main/Qwen3.5-4B-Q4_K_M.gguf). Keep both under the ignored `.local/` directory. Run these in two terminals from the checkout:

```bash
export PHOENIX_API_KEY="$(openssl rand -hex 32)"
LLAMA_SERVER="$PWD/.local/llama-b12359/llama-server" PHOENIX_GPU_LAYERS=20 \
  scripts/start-model .local/Qwen3.5-4B-Q4_K_M.gguf
```

```bash
# Set PHOENIX_API_KEY to the same private token as the server.
python -m phoenix.gui
```

Both processes require the same private token. Avoid putting it in shell history. Use `PHOENIX_GPU_LAYERS=0` for CPU. Set `PHOENIX_PORT` and `PHOENIX_SERVER` together to change ports. The launch script requires readable local weights and checks GPU availability before attempting offload. If llama.cpp reports an allocation failure, lower the offload count or use CPU. This build sends text to the model and has no vision adapter or mmproj dependency. To add a KDE shortcut, add `phoenix-assistant` as a command under System Settings → Keyboard → Shortcuts after installing the entry point.

## Reproduce

```bash
python -m unittest discover -s tests -v
python -m phoenix.demo .local/my-demo
```

Use **Grant folder** in the chat window for each printed folder and enter the corresponding alias. Paste the printed request, then review and confirm the proposed paths and action. Check that `Documents/Invoices/summary.txt` reports `2026-11-07` and `USD 148.25`, and compare the copied PDF with `cmp`. Use a new directory for each run. For a failure fixture pass `--failure unreadable` or `--failure missing-date` to the generator.

For desktop input, use **Kate append**, choose the generated summary, enter `phoenix portal proof`, and confirm the file and line. In KDE's share dialog allow keyboard control, select the screen showing Kate or the Kate window, click **Share**, and focus the new Kate window. Use a US keyboard layout. Check the saved line on disk. The same operation can be run from the checkout with `PYTHONPATH=. scripts/kate-proof .local/my-demo/Documents/Invoices/summary.txt`.

Use `--stop-after-capture` on a disposable file to reproduce live cancellation before input. The file should stay byte-identical and the KDE screen sharing indicator should go away. Ctrl+C also sets Stop in the proof script. In the app, use **Stop**.

To run the isolated offline chat demo, use a system with unprivileged network namespaces and run:

```bash
LLAMA_SERVER="$PWD/.local/llama-b12359/llama-server" PHOENIX_GPU_LAYERS=20 \
  scripts/offline-proof .local/Qwen3.5-4B-Q4_K_M.gguf .local/my-offline-demo
```

## Architecture and current boundaries

- Qt runs on the main thread. Each task has one worker, a cancellation event and a bounded planner loop. Stop prevents queued actions and releases the portal. An outstanding model response can take until its timeout, and a file operation that already started can finish.
- The planner speaks llama.cpp chat completions using the GGUF template and requests one typed action at a time. The invoice action uses deterministic extraction and ordering, then checks the copy and summary on disk. The folder open goes through FileManager1. The native UI previews each write, copy, folder creation, and application open for explicit approval. The invoice workflow shows one preview for its combined changes. The isolated proof automatically confirms the expected operation on its generated fixture. Single-file reads terminate the tool loop after displaying their text, and invoice document text stays out of the planner. The invoice parser supports labelled ISO dates and decimal totals in extractable PDFs. It pauses when any candidate cannot be ranked or the latest date is tied.
- Folder grants are scoped to the process and revocable in the UI. Folder grants pin the selected directory descriptor. File operations reject symlinks, hard-linked files, moved directories and misleading control characters in paths. Poppler receives the PDF over stdin in a network-isolated bubblewrap namespace with memory and output limits. Create-only writes publish atomically on filesystems supporting hard links and verify the bytes. If a later step fails, completed outputs remain visible. SQLite stores explicit preferences and action names/statuses in an owner-only directory and database, with secure deletion enabled for live SQLite pages. Existing backups and filesystem snapshots can retain earlier data. The app does not persist chat, document contents or capture frames.
- KDE grants capture and keyboard access through a nonpersistent portal session. The Kate edit uses the app's AT-SPI process and active window title before each chord, then verifies bytes after saving. Portal keyboard input is seat-wide, so the focus check and input dispatch are separate operations with a small race. This feature is limited to the confirmed Kate append flow and a US keyboard layout. General screenshot interpretation, pointer placement, and scaling/offset mapping remain future work.
- Overwrites through file tools, deletion, external sends, uploads, speech and unattended workflows have no V1 tools. Gemma 4 E4B, Qwen3.5-9B and Holo2-4B have not been benchmarked here. The baseline is the only measured model, so there is no comparative default selection yet.

## Security review

The supported launcher requires an API token, binds to loopback, restricts browser origins, disables the Web UI and server-side agents, and drops inherited llama.cpp feature options. The desktop client bounds responses and accepts only loopback URLs. Treat any separately configured llama.cpp process and GGUF as trusted components. Consult [upstream llama.cpp advisories](https://github.com/ggml-org/llama.cpp/security/advisories/) when choosing a build.

Folder checks protect normal application paths. A same-user process can still race a concurrent rename, manipulate an app after a validated path is handed off, or impersonate a listener on a configured loopback port. Phoenix runs as the signed-in user. The Poppler subprocess has its own containment boundary. KDE's EIS input remains seat-wide and focus verification is best effort. Model responses and extracted document text can contain misleading content, so a preview requires human review before external side effects.

The hardened server returned HTTP 401 for unauthenticated chat requests and HTTP 403 for the tested external browser origin. The hardened offline invoice workflow passed in 11.78 s, with 3,399,064 kB model peak RSS and 2,525 MiB whole-GPU peak VRAM. The live Kate proof saved the expected line after the cleanup changes and the portal exposed no remaining session. All 13 regression tests passed.

## Initial live results on 2026-11-13

CachyOS, Plasma/portal KDE 6.8.2, XDG Desktop Portal 1.22.1 (RemoteDesktop v2), PipeWire 1.6.11, libei 1.6.0, PySide6 6.12.0. i7-7700, 15 GiB usable RAM, GTX 1050 Ti 4 GiB, NVIDIA 580.196.07. The portal exposed ConnectToEIS and legacy keyboard notification methods. The implementation used EIS.

- Qwen3.5-4B Q4_K_M, SHA-256 `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`. llama.cpp 1.0.0-dev build 12359, commit `b1b770d90`, Vulkan, 20 GPU layers, four CPU threads, 4096 context and one slot. The first request took 17.73 s and had valid function-call syntax but used an invalid destination alias. After simplifying the schema to one grant-qualified destination path, the native chat fixture completed in 8.87 s with a warm cache.
- The cold isolated chat demo completed in 17.41 s with loopback as its only network interface. The model produced the expected typed invoice request, the copied PDF matched byte for byte, the summary matched all expected fields, the embedded document instruction produced no extra file, and Dolphin accepted the destination request over D-Bus. Model peak RSS was 3,399,324 kB. Sampled whole-GPU memory rose from 610 to 2,580 MiB. A repeat after switching to atomic file publication took 11.80 s, with 3,399,444 kB peak model RSS and 2,557 MiB peak whole-GPU VRAM.
- CPU-only offline invoice run completed in 15.48 s with 3,291,488 kB model peak RSS and 705 MiB whole-GPU peak VRAM. Whole-GPU readings include KDE and the other running desktop apps.
- The initial PipeWire attempt using only the portal node ID hit `target not found`. The revised capture used the PipeWire object serial and captured 1920×1080. EIS typed and saved the expected line in both the disposable Kate document and the generated invoice summary. Stop after capture left a second disposable document unchanged. A subsequent portal object listing showed no remaining session object.
- Regression tests use a simulated planner or portal where applicable. They cover unreadable and undated PDFs, filename ambiguity, destination conflicts, document instruction isolation, queued Stop, scope and symlinks, preference persistence and deletion, malformed calls, portal denial/revocation, and changed target. Portal denial/revocation and changed target were exercised in simulation. The isolated run covered the invoice workflow, and the Kate proof ran in the normal KDE session.
