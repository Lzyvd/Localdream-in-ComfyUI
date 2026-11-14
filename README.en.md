# Localdream-in-ComfyUI

A ComfyUI custom node pack that connects to **Local Dream "Controlled Mode"** on an Android phone.

All inference runs on the phone's NPU / CPU. ComfyUI only orchestrates the parameters, shows its
native progress bar, and displays the resulting image. **This single plugin is all you need**: the
settings UI is served by ComfyUI's own HTTP routes, and generation is requested directly from the
ComfyUI process to the phone. No extra background program, reverse proxy, standalone service, or
separate deployment is required.

**Project home**: <https://github.com/Lzyvd/Localdream-in-ComfyUI> · 📖 [中文 README](README.md)

> ⭐ If you like this project, please give it a **Star** on GitHub — it is the main motivation for
> keeping a small independent project maintained. Bug reports and ideas are welcome as
> [Issues](https://github.com/Lzyvd/Localdream-in-ComfyUI/issues) or Pull Requests.

---

## 1. What's included

| Node | Input | Output | Menu path |
|---|---|---|---|
| `LocalDream Text to Image (Prompt → Image)` | `STRING` prompt + sampling params | `IMAGE` | `LocalDream` |
| `LocalDream Image to Image (Image → Image)` | `IMAGE` base (optional `MASK`) + `STRING` prompt + sampling params | `IMAGE` | `LocalDream` |

Both nodes carry a **"⚙ LocalDream Settings" button** (also available in the node's right-click menu)
that opens a standalone settings dialog. It covers the device IP and both ports, default model and
load resolution, connection test, model list refresh, model loading, diffusion-process previews,
timeouts and address overrides, reset to defaults, and a "test generation" that needs no workflow.

Both nodes map the sampling process onto `comfy.utils.ProgressBar` — the same native progress bar at
the top of ComfyUI that KSampler uses. When intermediate previews are enabled, the device's
`progress` frames are also pushed into ComfyUI's native preview channel.

---

## 2. Installation and phone setup

### Install

```bash
cd <ComfyUI>/custom_nodes
git clone https://github.com/Lzyvd/Localdream-in-ComfyUI.git Localdream-in-ComfyUI
# or simply copy this directory there
```

Restart ComfyUI. The plugin has **no third-party Python dependencies** (`requirements.txt` is empty).
At runtime it uses only the Python standard library plus `torch` / `numpy` / `Pillow` / `aiohttp`,
which ComfyUI already ships.

### On the phone (one manual step required)

1. Install Local Dream (tested with 3.0.0-alpha.3 and newer; Device Interconnect is on by default
   since 2.8.1).
2. Enter Controlled Mode:
   **Local Dream → ⋮ at the top right of the model list → Device Interconnect → Controlled Mode →
   Enter Controlled Mode**.
   The service is marked `exported="false"` in `AndroidManifest.xml`, so it cannot be launched
   externally — the plugin will not tap it for you.
3. Keep the phone and PC on the same LAN (or use `127.0.0.1` with port forwarding on the phone).
   Disable battery optimization / enable background lock for Local Dream so Android does not kill it
   when the screen turns off.

### Quick start

1. Add a `LocalDream Text to Image` node and connect it to `PreviewImage` / `SaveImage`.
2. Click **⚙ LocalDream Settings** on the node → enter the phone's LAN IP (e.g. `192.168.1.7`) →
   **Test connection** → **Save and apply**.
3. If the generation port is not listening, select a model in the settings and click
   **Load model and wait until ready**, or click **Generate a test image** to verify output.
4. Back on the node, pick a model, write a prompt, and click ComfyUI's **Run**. The progress bar
   advances with the steps streamed back from the device over SSE.

> Configuration lives in `settings.json` in the plugin directory and is shared by both nodes; it
> takes effect immediately after saving, no ComfyUI restart needed.
> This file contains your local device address, is **gitignored, and must not be committed**; see
> `settings.example.json` for a publishable template.
> You can also preset the device address with the `LOCALDREAM_HOST` environment variable (used as a
> default only when `settings.json` does not exist).

---

## 3. Node inputs

Both nodes share the same inputs (`Image to Image` adds a required `image` and an optional `mask`):

| Input | Type | Default | Device field / notes |
|---|---|---|---|
| `prompt` | STRING, multiline | example prompt | `prompt`, required; 77-token CLIP limit |
| `negative_prompt` | STRING, multiline | empty | `negative_prompt`; empty uses the default negative prompt from settings |
| `model` | COMBO | Auto | From `GET :8808/models`; "Auto" keeps the model currently loaded on the device |
| `steps` | INT 1–100 | 20 | `steps` |
| `cfg` | FLOAT 0–30 | 7.5 | `cfg`; on NPU, `cfg = 1.0` skips the unconditional UNet pass, roughly doubling speed while making the negative prompt ineffective |
| `scheduler` | COMBO | `dpmpp_2m` | Sampler name (ComfyUI naming), see below; converted automatically before the request |
| `seed` | INT 0–2³²−1 | 0 | `seed`, always sent explicitly; works with ComfyUI's built-in `control_after_generate` |
| `denoise` | FLOAT 0–1 | t2i `1.0` / i2i `0.6` | i2i → `denoise_strength`; t2i has no such field, so the plugin approximates it as `round(steps * denoise)` effective steps |
| `width` / `height` | INT 64–2048, multiple of 8 | 512 | `width` / `height` (ignored by SDXL NPU, see below) |
| `aspect_ratio` | COMBO | `auto` | SDXL NPU only; `auto` reduces `width/height` to a ratio with a fixed 1024px long side |
| `use_opencl` | BOOLEAN | False | `use_opencl`, SD1.5 CPU mode only |
| `batch_count` | INT 1–8 | 1 | Generates sequentially and concatenates into `[B,H,W,3]`; the progress bar accumulates `steps × count` |
| `image` | IMAGE | — | i2i node only; center-resized to the target size and encoded as PNG for the `image` field |
| `mask` | MASK (optional link) | — | Enables inpainting; white areas are redrawn |
| `unique_id` | hidden | — | Passed to `ProgressBar(node_id=...)` so progress is attributed to the right node |

**`scheduler` values** use standard ComfyUI / KSampler names. The plugin maps them to the short names
the Local Dream backend accepts (short names saved in older workflows still work):

| Node shows | Device `scheduler` | UI equivalent |
|---|---|---|
| `dpmpp_2m` | `dpm` | DPM++ 2M |
| `dpmpp_2m_karras` | `dpm_karras` | DPM++ 2M + Karras |
| `dpmpp_sde` | `dpm_sde` | DPM++ 2M SDE |
| `dpmpp_sde_karras` | `dpm_sde_karras` | DPM++ 2M SDE + Karras |
| `euler_ancestral` | `euler_a` | Euler A |
| `euler_ancestral_karras` | `euler_a_karras` | Euler A + Karras |
| `euler` | `euler` | Euler |
| `euler_karras` | `euler_karras` | Euler + Karras |
| `lcm` | `lcm` | LCM |

> Unknown values silently fall back to `dpm` on the backend. `lcm` does not support the `_karras`
> suffix; for DMD2 fast models stick to `lcm` + `cfg 1` + 8 steps (on a real device, Karras variants
> such as `dpmpp_2m_karras` were observed to stall on that DMD2 model).
> Karras is encoded as a sampler-name suffix because that is how the Controlled Mode API works, so
> there is no separate "scheduler" dropdown.

### Localization

All widget labels, tooltips, and output names are translated through ComfyUI's official i18n
mechanism, in `locales/zh/nodeDefs.json` (Chinese) and `locales/en/nodeDefs.json` (English). In a
Chinese UI the labels read: 正面提示词 / 模型 / 步数 / cfg / 采样器名称 / 种子 / 降噪 / 宽度 / 高度 /
负面提示词 / 宽高比 / 使用 OpenCL / 生成张数 / 图像 / 蒙版, matching the built-in KSampler style.
Internal input keys stay English, so older workflows remain fully compatible.

The source badge at the top right of a node is derived by the ComfyUI frontend from `python_module`;
by default it strips the `-ComfyUI` suffix and shows `Localdream-in`. The plugin corrects this while
node definitions are registered, so the badge reads the full `Localdream-in-ComfyUI`.

---

## 4. Settings dialog

The **⚙ LocalDream Settings** button on each node opens a standalone dialog with four tabs:

| Tab | Contents |
|---|---|
| Connection | Device IP, control port 8808 / generation port 8081, load resolution, default model, load model, refresh model list, test connection, device info and backend status |
| Generation | Default negative prompt, auto-load model, auto-match resolution, diffusion preview toggle and stride, ComfyUI intermediate-frame previews, test generation (with result preview) |
| Advanced | Generation / control-port / model-load timeouts, model cache TTL, control/generation address overrides, bypass system proxy, reset to defaults |
| About | Project URL with a Star link, Issue / English README links, API references, two-port and SSE notes, official documentation links, caveats |

Clicking **Save and apply** writes `settings.json` → clears the model cache → re-fetches the model
list and syncs the model dropdown on every existing node → re-runs the connection diagnosis.

---

## 5. How the progress bar works

```
Phone Local Dream :8081/generate (SSE)
   event: progress  {"type":"progress","step":3,"total_steps":20,...}
   event: complete  {"type":"complete","image":"<base64 RGB24>", ...}
        │
        ▼  ld_client.generate() parses the SSE stream
   comfy.utils.ProgressBar(total = steps × batch)
        ├─ update_absolute(0, total) at the start to light up the bar
        ├─ each progress → update_absolute(...) mapped from step / total_steps
        ├─ intermediate frame → ("PNG", PIL.Image, 512) into ComfyUI's native preview
        └─ complete → update_absolute(..., total) to guarantee a 100% finish
```

`ProgressBar` is ComfyUI's global hook mechanism: updates are pushed to the frontend over `/ws` as
`progress` / `progress_state` messages, so what you see is the very same native bar KSampler uses.
Every event calls `throw_exception_if_processing_interrupted()`, so ComfyUI's **Cancel / Interrupt**
button stops the wait and closes the device connection.

> ComfyUI caches node outputs: running the same inputs and seed twice may hit the cache and show no
> progress (the native KSampler behaves the same). Set `control_after_generate` on `seed` to random,
> or change any parameter, to force a new generation.

---

## 6. API reference and constraints (verified before development)

| Capability | Endpoint | Source |
|---|---|---|
| Device info | `GET :8808/info` | `ld-api/src/device.ts` |
| Model list | `GET :8808/models` → `{use_img2img, models[], upscalers[]}` | same, `Module` type |
| Load model | `POST :8808/select` `{model_id,width,height}` | same |
| Backend status | `GET :8808/status` → `{serving_model_id,state,message,width,height}` | same |
| Stop model | `POST :8808/stop` `{model_id}` | same (the plugin never stops device models) |
| Generate | `POST :8081/generate` → `text/event-stream` | [Official HTTP API reference](https://ld-guide.chino.icu/features/http-api) |
| Token count | `POST :8081/tokenize` | same |

**Main references**

- Local Dream official guide · [HTTP API reference](https://ld-guide.chino.icu/features/http-api)
- [`@kongxiangyiren/ld-api`](https://github.com/kongxiangyiren/ld-api) (`src/device.ts`,
  `src/generation.ts`, `src/type.ts`)
- [Controlled Mode and the two ports](https://dshpluginhub.ai/zh/plugins/dsh-localdream)
- ComfyUI docs: [Custom Node Properties](https://docs.comfy.org/custom-nodes/backend/server_overview),
  [Hidden Inputs](https://docs.comfy.org/custom-nodes/backend/more_on_inputs),
  [JavaScript Hooks](https://docs.comfy.org/custom-nodes/js/javascript_hooks),
  [Server Routes](https://docs.comfy.org/development/comfyui-server/comms_routes), plus
  `ProgressBar` in `comfy/utils.py` and `hijack_progress` in `main.py`

**Key constraints (all handled in the implementation)**

1. `:8081` **only starts listening after a model is selected in the app**; before that, connections
   are refused. The plugin calls `/select`, polls `/status` until `running`, and retries the
   generation port 3 times (there is a few-hundred-millisecond gap after `running` appears).
2. `complete.image` is **Base64-encoded raw RGB24 pixel bytes**, not a PNG/JPG; it must be reshaped
   as `(height, width, 3)`. The plugin also tolerates backends returning PNG/JPEG or RGBA.
3. `progress` events carry no preview dimensions; the plugin first compares against the requested
   size, then infers factors from the byte count and aspect ratio.
4. `seed` is random **only when omitted**; passing `-1` becomes the fixed seed `4294967295`. The
   plugin always sends an explicit uint32.
5. `size` and `width`/`height` are mutually exclusive, with `size` taking priority. The plugin only
   sends `width`/`height`.
6. Size behavior depends on the model family: SD1.5 CPU accepts any multiple of 8; SD1.5 / DiT NPU
   must match the resolution chosen at `/select` (the plugin snaps to `resolutions`); **SDXL NPU
   ignores `width`/`height` and derives the size from `aspect_ratio`** (long side 1024, short side
   rounded up to a multiple of 8).
7. `image` / `mask` must be **encoded PNG/JPG bytes**, not raw RGB; the plugin resizes and encodes
   ComfyUI `IMAGE` / `MASK` tensors to PNG.
8. `cfg == 1.0` skips the unconditional UNet pass on NPU; the `_karras` suffix selects the Karras
   noise schedule, except for `lcm`.
9. `show_diffusion_process` also requires "Show generation process" to be enabled on the phone; step
   progress is still streamed when it is off.
10. The device allows only one generation at a time — the plugin serializes them with a global lock.
11. Both ports are plain HTTP with no CORS headers, so the settings UI never talks to the phone
    directly; it goes through ComfyUI's same-origin routes.
12. `urllib` reads the `http_proxy` environment variable by default and would send LAN requests into
    a local proxy — the plugin explicitly disables proxies.

> On bind addresses: the official docs say 8081 binds to `127.0.0.1` (older versions, requiring
> `adb forward`), while `ld-api` and the 3.x Controlled Mode tested here are reachable over the LAN.
> The plugin therefore supports any device IP and also lets you override the control / generation
> host separately under "Advanced" for port-forwarding setups.

---

## 7. Only this plugin is needed

| Piece | Provided by |
|---|---|
| Settings UI | The plugin's `js/localdream.js` (ComfyUI loads `WEB_DIRECTORY` automatically) |
| Settings / diagnosis / model / test-generation HTTP endpoints | `/localdream/*` on `PromptServer.instance.routes` (ComfyUI's own aiohttp server) |
| Generation request and SSE parsing | The node calls the phone with `urllib` inside the ComfyUI execution thread |
| Progress bar | `comfy.utils.ProgressBar` global hook → ComfyUI WebSocket |
| Image tensor | ComfyUI's own `torch` / `numpy` / `Pillow` |

There is no `serve.py`, no separate process, no Docker, and no extra port to expose. The only
prerequisite is that Local Dream on the phone has been manually put into Controlled Mode.

---

## 8. Verification

A **zero-dependency offline end-to-end test** ships in `tests/`. It emulates a Controlled Mode device
with the standard library only — no real phone and no ComfyUI installation required:

```bash
cd <ComfyUI>/custom_nodes/Localdream-in-ComfyUI
python tests/run_tests.py
```

It covers node registration and `INPUT_TYPES`, parameter validation, settings save/load/reset and
every HTTP route, `/info` `/models` `/status` `/select`, SSE `progress`/`complete` parsing, native
progress-bar callbacks, raw RGB → ComfyUI `IMAGE` tensor, img2img PNG encoding, batch concatenation,
and JS syntax plus settings-dialog interaction smoke tests. **All 117 assertions currently pass**;
a real screenshot of the settings dialog is at `tests/settings_dialog_preview.png`.

Real-device testing uncovered and fixed cases where the device reports a different `total_steps` than
the requested `steps`, or even changes `total_steps` mid-generation. Progress is now mapped from the
device-reported ratio onto a fixed total, keeping `max` stable and `value` monotonically reaching
100%.

See `tests/README.md` for details.

### Real-device record (ComfyUI 0.42.5 + Local Dream 3.0.0-alpha.7)

ComfyUI was started with only this plugin loaded
(`--disable-all-custom-nodes --whitelist-custom-nodes Localdream-in-ComfyUI`), using a single DMD2
model throughout:

| Item | Result |
|---|---|
| Node loading | Both nodes appear in `/object_info`; `/extensions` loads `localdream.js`; 20 routes registered |
| Frontend | In the real Vue frontend, nodes get a "⚙ LocalDream Settings" button (`serialize:false`) that opens the dialog; IP / ports / model dropdown / device status all populate correctly |
| Settings | Test connection, load model, refresh models, save and read back, reset, and settings-panel test generation all pass |
| Output | Settings-panel test generation, `/prompt` text-to-image, and a `/prompt` t2i→i2i chain all produced 1024×1024 RGB PNGs with no visual corruption |
| Progress bar | WebSocket `progress` events attributed to the correct node; both nodes keep a stable `max`, a monotonic `value`, and finish at 100%; `progress_state` is emitted |
| Device constraint | `serving_model_id` stayed on the designated model; no other model was ever loaded |
| Source badge | Fixed the frontend's `shortenNodeName()` stripping `-ComfyUI`; the canvas badge now shows `Localdream-in-ComfyUI` |
| Sampler naming | Dropdown uses `dpmpp_2m` / `euler_ancestral` etc.; a real workflow log confirmed `dpmpp_2m_karras → dpm_karras` and the device value stayed valid |
| Chinese i18n | `locales/zh/nodeDefs.json` took effect via `/i18n`: 模型 / 步数 / cfg / 采样器名称 / 种子 / 生成后控制 / 降噪 / 宽度 / 高度 / 负面提示词 / 宽高比 / 使用 OpenCL / 生成张数, matching KSampler |

To re-verify on your own machine, these five steps are recommended:

1. After restart, "Add Node → LocalDream" shows both nodes and `/object_info` contains
   `LocalDreamTextToImage` / `LocalDreamImageToImage`.
2. The ⚙ button opens the dialog; test connection shows device info; saving populates the model
   dropdown with the device's model ids.
3. Running text-to-image advances ComfyUI's top progress bar and `PreviewImage` shows an image.
4. Connect the t2i output to the i2i node's `image` input and run again for a redraw.
5. Clicking "Cancel" during a run interrupts it promptly.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Control port 8808 unreachable | Re-enter Controlled Mode in the app; check the same subnet, unchanged IP, and that Android has not killed the app |
| Control port fine, 8081 not listening | Normal: no model selected yet. Click "Load model and wait until ready" in settings |
| `Img size mismatch` / size errors | Enable "Auto-match model resolution"; SDXL uses `aspect_ratio`, not `width/height` |
| Output is all black / corrupted | Check whether "Show generation process" is enabled but the device does not support intermediate frames; turn previews off |
| Requests fail instantly with proxy messages in the log | Keep "Bypass system HTTP proxy" enabled |
| Second run shows no progress | ComfyUI cache hit — change the seed or any parameter |
| Model dropdown is empty | Click "Refresh model list" in settings; it falls back to the last cache when the device is offline |
| DMD2 model stalls after switching to a Karras sampler | Use `lcm` + `cfg 1` + 8 steps for that model; click ComfyUI's "Cancel", and re-enter Controlled Mode in the app if needed |
| Badge shows `Localdream-in` instead of the full name | Refresh the browser page (re-registers the JS extension); Python-side changes require a ComfyUI restart |

---

## 10. Directory structure

```
Localdream-in-ComfyUI/
├── README.md                # Chinese documentation (this project's primary README)
├── README.en.md             # English README
├── __init__.py              # Node mappings + WEB_DIRECTORY + route registration
├── nodes.py                 # t2i / i2i nodes + native progress bar
├── ld_client.py             # Config, two-port HTTP, SSE, models, image codecs (stdlib only)
├── web_api.py               # Same-origin settings / diagnosis / model / test-generation routes
├── js/localdream.js         # Settings button + standalone dialog + model dropdown + source badge fix
├── locales/                 # zh / en official i18n (widget labels, tooltips, output names)
├── examples/                # Example workflows in /prompt format
├── tests/                   # Mock device + offline end-to-end tests + settings dialog screenshot
├── settings.example.json    # Publishable config template (example IP)
├── settings.json            # Generated at runtime, contains the real device IP (gitignored, do not commit)
├── models_cache.json        # Generated at runtime, contains device models and phone paths (gitignored)
├── pyproject.toml
├── requirements.txt         # Empty: no extra dependencies
└── LICENSE                  # MIT
```

---

## 11. Privacy and publishing

**Do not push these to GitHub** (the first five are already covered by `.gitignore`):

| File / directory | What it contains |
|---|---|
| `settings.json`, `settings.json.tmp`, `settings.*.json` | The phone's LAN IP, both ports, address overrides, default model, timeouts |
| `models_cache.json` | Device model list and absolute paths on the phone (`/storage/emulated/0/...`) |
| `*.log`, `*.log.*` | Logs record device URLs, model ids, prompts, and request timings |
| `.env`, `.env.*`, `*_local.json` | May contain `LOCALDREAM_HOST` and other local addresses |
| `tests/`, `realdevice*/`, `output/` | Local test artifacts and generated images; PNG metadata also embeds prompts and workflows |
| `custom_nodes/Localdream-in-ComfyUI/settings.json` in your ComfyUI installation | Same as the first row — an installed copy, not plugin source |

**Safe to commit**: `settings.example.json` (example IP `192.168.1.7` and defaults only),
`README.md`, `README.en.md`, `locales/`, the four `.py` files, `js/`, `examples/`,
`pyproject.toml`, `requirements.txt`, `LICENSE`, `.gitignore`. The addresses
`192.168.1.7` / `10.0.0.42` / `192.0.2.1` in docs and offline tests are examples or reserved
ranges, not real device addresses.

Before committing:

```bash
git status --short
git grep -nE "([0-9]{1,3}\.){3}[0-9]{1,3}"   # should only show example IPs and 127.0.0.1
git check-ignore -v settings.json models_cache.json
```

> If you use `LOCALDREAM_HOST` to preset the device address, keep it in your shell config or a local
> `.env` file — never in a file that will be committed.

---

## 12. Support and feedback

Project home: **<https://github.com/Lzyvd/Localdream-in-ComfyUI>**

If this project put your phone's NPU to work, or if "install one plugin and it just runs" saved you
some time, a **⭐ Star** is very welcome — for a small independently maintained project, stars and
feedback are what keep updates coming.

| What you want to do | Where |
|---|---|
| ⭐ Star / follow releases | <https://github.com/Lzyvd/Localdream-in-ComfyUI> |
| 🐛 Report a bug or request a feature | <https://github.com/Lzyvd/Localdream-in-ComfyUI/issues> |
| 🔧 Contribute a change | <https://github.com/Lzyvd/Localdream-in-ComfyUI/pulls> |
| 📖 Read the Chinese docs | [README.md](README.md) |
| 💬 Share device compatibility | Open an Issue with your phone model, Local Dream version, and `/info` output |

Reports of phone/model combinations you have actually tested are especially welcome — they save the
next person a lot of trial and error.

---

## 13. License

MIT. This is a third-party integration plugin and is not affiliated with the official Local Dream
project.
