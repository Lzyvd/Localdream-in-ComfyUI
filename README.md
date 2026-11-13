# Localdream-in-ComfyUI

把手机端 **Local Dream「受控模式」** 接进 ComfyUI 的自定义节点插件。

所有推理都在手机 NPU / CPU 上完成，ComfyUI 只负责编排参数、显示原生进度条与结果图。
**只需要这一个插件**：设置界面走 ComfyUI 自带的 HTTP 路由，生成请求在 ComfyUI 进程内直连手机，
不需要额外后台程序、反向代理、独立服务或额外部署。

---

## 1. 交付内容

| 节点 | 输入 | 输出 | 菜单路径 |
|---|---|---|---|
| `LocalDream 文生图 (提示词 → 图像)` | `STRING` 提示词 + 采样参数 | `IMAGE` | `LocalDream` |
| `LocalDream 图生图 (图像 → 图像)` | `IMAGE` 底图（可选 `MASK`）+ `STRING` 提示词 + 采样参数 | `IMAGE` | `LocalDream` |

两个节点都带 **「⚙ LocalDream 设置」按钮**（节点右键菜单里也有同名入口），点击打开独立设置弹窗。
设置弹窗包含：设备 IP / 双端口、默认模型与载入分辨率、测试连接、刷新模型列表、载入模型、
扩散过程预览、超时与地址覆盖、恢复默认、以及不需要搭工作流的「测试生成」。

两个节点的采样过程都映射到 `comfy.utils.ProgressBar`，也就是 ComfyUI 顶部那条原生进度条；
开启中间帧预览后还会把设备回传的 `progress` 帧送进 ComfyUI 原生预览通道。

---

## 2. 安装与手机端准备

### 安装

```bash
cd <ComfyUI>/custom_nodes
git clone <本仓库> Localdream-in-ComfyUI     # 或直接把本目录复制过去
```

重启 ComfyUI。插件**没有第三方 Python 依赖**（`requirements.txt` 为空），
运行时只用到 Python 标准库以及 ComfyUI 自带的 `torch` / `numpy` / `Pillow` / `aiohttp`。

### 手机端（必须手动做一次）

1. 安装 Local Dream（3.0.0-alpha.3 及以上实测可用；设备互连 2.8.1+ 默认开启）。
2. 进入受控模式：
   **Local Dream → 模型列表右上角 ⋮ → 设备互联 → 受控模式 → 进入受控模式**
   该服务在 `AndroidManifest.xml` 里是 `exported="false"`，外部无法唤起，插件不会替你点。
3. 手机与电脑处于同一局域网（或在手机上用 `127.0.0.1` + 端口转发）。
   建议在系统设置里给 Local Dream 关闭电池优化 / 后台锁定，避免熄屏后被回收。

### 快速开始

1. 在 ComfyUI 里添加 `LocalDream 文生图` 节点，接一个 `PreviewImage` / `SaveImage`。
2. 点节点上的 **⚙ LocalDream 设置** → 填手机局域网 IP（例如 `192.168.1.7`）→ **测试连接** → **保存并生效**。
3. 若「生成口未监听」，在设置里选一个模型点 **载入模型并等待就绪**，或点 **生成一张测试图** 验证出图。
4. 回到节点，选模型、写提示词，点 ComfyUI 的「运行」。进度条会按设备 SSE 回传的步数推进。

> 配置保存在插件目录 `settings.json`，两个节点共用；保存后立即生效，无需重启 ComfyUI。
> 该文件含本机设备地址，**已 gitignore，请勿提交**；可公开的配置模板见 `settings.example.json`。
> 也可用环境变量 `LOCALDREAM_HOST` 预置设备地址（仅在没有 `settings.json` 时作为默认值）。

---

## 3. 节点参数

两个节点的输入一致（`图生图` 多一个 `image` 必需输入与 `mask` 可选输入）：

| 参数 | 类型 | 默认 | 对应设备字段 / 说明 |
|---|---|---|---|
| `prompt` | STRING 多行 | 示例提示词 | `prompt`，必填；CLIP 上限 77 token |
| `negative_prompt` | STRING 多行 | 空 | `negative_prompt`；留空用设置里的默认反向词 |
| `model` | COMBO | 自动 | 来自 `GET :8808/models`；「自动」沿用设备已载入模型 |
| `steps` | INT 1–100 | 20 | `steps` |
| `cfg` | FLOAT 0–30 | 7.5 | `cfg`；NPU 上 `cfg=1.0` 会跳过无条件 UNet，速度约翻倍但反向词失效 |
| `scheduler` | COMBO | `dpm` | 采样 / 去噪方式，见下表 |
| `seed` | INT 0–2³²−1 | 0 | `seed`，始终显式发送；配合 ComfyUI 自带的 `control_after_generate` |
| `denoise` | FLOAT 0–1 | 文生图 `1.0` / 图生图 `0.6` | 图生图 → `denoise_strength`；文生图接口无此字段，插件以等效步数 `round(steps*denoise)` 近似 |
| `width` / `height` | INT 64–2048，8 的倍数 | 512 | `width` / `height`（SDXL NPU 忽略，见下） |
| `aspect_ratio` | COMBO | `auto` | 仅 SDXL NPU 生效；`auto` 时由 `width/height` 约分得到，长边固定 1024 |
| `use_opencl` | BOOLEAN | False | `use_opencl`，仅 SD1.5 CPU 模式有效 |
| `batch_count` | INT 1–8 | 1 | 顺序生成多张并拼成 `[B,H,W,3]`；进度条按 `steps×张数` 累计 |
| `image` | IMAGE | — | 仅图生图节点；按目标尺寸居中缩放后编码成 PNG 传给 `image` |
| `mask` | MASK（可选连接） | — | 连接后切换 inpaint；白色为需要重绘的区域 |
| `unique_id` | hidden | — | 传给 `ProgressBar(node_id=...)`，让进度归属到正确节点 |

**`scheduler` 合法值**（未知值后端会静默回退到 `dpm`）：

`dpm` · `dpm_karras` · `dpm_sde` · `dpm_sde_karras` · `euler_a` · `euler_a_karras` · `euler` · `euler_karras` · `lcm`

> `lcm` 不支持 `_karras` 后缀；DMD2 极速模型通常用 `lcm` + `cfg 1` + 8 步。

---

## 4. 设置界面

节点上的 **⚙ LocalDream 设置** 按钮打开一个独立弹窗，四个分页：

| 分页 | 内容 |
|---|---|
| 连接 | 设备 IP、控制口 8808 / 生成口 8081、载入分辨率、默认模型、载入模型、刷新模型列表、测试连接、设备信息与后端状态 |
| 生成 | 默认反向词、自动载入模型、自动匹配分辨率、扩散过程预览开关与步幅、ComfyUI 中间帧预览、测试生成（含结果预览图） |
| 高级 | 生成 / 控制口 / 模型载入超时、模型缓存 TTL、控制口与生成口地址覆盖、绕过系统代理、恢复默认 |
| 关于 | 接口依据、双端口与 SSE 说明、官方文档链接、注意事项 |

点击 **保存并生效** 会：写入 `settings.json` → 清模型缓存 → 重新拉取模型列表并同步到所有已存在节点的模型下拉框 → 重新诊断。

---

## 5. 进度条如何工作

```
手机 Local Dream :8081/generate (SSE)
   event: progress  {"type":"progress","step":3,"total_steps":20,...}
   event: complete  {"type":"complete","image":"<base64 RGB24>", ...}
        │
        ▼  插件 ld_client.generate() 逐行解析 SSE
   comfy.utils.ProgressBar(total = steps × batch)
        ├─ 开始即 update_absolute(0, total) 点亮进度条
        ├─ 每个 progress → update_absolute(已完成步数 + step, total)
        ├─ 有中间帧 → ("PNG", PIL.Image, 512) 送进 ComfyUI 原生预览
        └─ complete → update_absolute(..., total) 确保收尾 100%
```

`ProgressBar` 是 ComfyUI 的全局 hook 机制：它的更新会自动经 `/ws` 以 `progress` /
`progress_state` 消息推给前端，所以显示的就是原生 K 采样器同一条进度条。
生成期间每次事件都会调用 `throw_exception_if_processing_interrupted()`，
ComfyUI 的「取消 / 中断」按钮可以立即中止等待并关闭设备连接。

> ComfyUI 有节点缓存：同样的输入与种子第二次运行可能直接命中缓存而不显示进度（原生 KSampler 也一样）。
> 把 `seed` 的 `control_after_generate` 设为随机，或改任意参数即可重新生成。

---

## 6. 接口依据与调用约束（开发前核对）

| 能力 | 接口 | 依据 |
|---|---|---|
| 设备信息 | `GET :8808/info` | `ld-api/src/device.ts` |
| 模型列表 | `GET :8808/models` → `{use_img2img, models[], upscalers[]}` | 同上 `Module` 类型 |
| 载入模型 | `POST :8808/select` `{model_id,width,height}` | 同上 |
| 后端状态 | `GET :8808/status` → `{serving_model_id,state,message,width,height}` | 同上 |
| 停止模型 | `POST :8808/stop` `{model_id}` | 同上（本插件不主动停止设备模型） |
| 生成 | `POST :8081/generate` → `text/event-stream` | [官方 HTTP API 参考](https://ld-guide.chino.icu/zh/features/http-api) |
| Token 计数 | `POST :8081/tokenize` | 同上 |

**主要参考**

- Local Dream 官方指南 · [HTTP API 参考](https://ld-guide.chino.icu/zh/features/http-api)（中/英）
- [`@kongxiangyiren/ld-api`](https://github.com/kongxiangyiren/ld-api)（`src/device.ts`、`src/generation.ts`、`src/type.ts`）
- [受控模式与双端口说明](https://dshpluginhub.ai/zh/plugins/dsh-localdream)
- ComfyUI 官方文档：[自定义节点属性](https://docs.comfy.org/custom-nodes/backend/server_overview)、
  [隐藏输入](https://docs.comfy.org/custom-nodes/backend/more_on_inputs)、
  [JavaScript Hooks](https://docs.comfy.org/custom-nodes/js/javascript_hooks)、
  [Server Routes](https://docs.comfy.org/development/comfyui-server/comms_routes)、
  `comfy/utils.py` 的 `ProgressBar` 与 `main.py` 的 `hijack_progress`

**关键约束（已在实现中处理）**

1. `:8081` **只有在 App 内选定模型后才开始监听**；未选模型时连接直接被拒。插件会先 `/select`、轮询 `/status` 到 `running`，并对生成口连接失败重试 3 次（状态变 running 后仍有数百毫秒空窗期）。
2. `complete.image` 是 **Base64 编码的原始 RGB24 像素**，不是 PNG/JPG；必须按 `(height,width,3)` 还原。插件同时兼容少数返回 PNG/JPEG、RGBA 的后端版本。
3. `progress` 事件不带预览图尺寸，插件先按请求尺寸比对，再按字节数与宽高比反推因数。
4. `seed` **省略才是随机**；传 `-1` 会被当成固定种子 `4294967295`。插件始终显式发送 uint32。
5. `size` 与 `width/height` 互斥；`size` 优先。插件只发 `width`/`height`。
6. 尺寸随模型族不同：SD1.5 CPU 任意 8 的倍数；SD1.5 / DiT NPU 必须与 `/select` 时选定的分辨率一致（插件自动向 `resolutions` 靠拢）；**SDXL NPU 忽略 `width/height`，由 `aspect_ratio` 决定**（长边 1024，短边向上取整到 8 的倍数）。
7. `image` / `mask` 必须是**编码后的 PNG/JPG 字节**，不是原始 RGB；插件会把 ComfyUI `IMAGE` / `MASK` 张量缩放编码成 PNG。
8. `cfg == 1.0` 在 NPU 上跳过无条件 UNet 计算；`_karras` 后缀启用 Karras 噪声调度，`lcm` 例外。
9. `show_diffusion_process` 需要手机端同时开启「显示生成过程」；未开启时步数进度仍正常推送。
10. 设备同一时刻只允许一个生成任务 —— 插件用全局锁串行化。
11. 两个端口是裸 HTTP 且不带 CORS 头 —— 所以设置界面不直连手机，而是经 ComfyUI 同源路由。
12. `urllib` 默认会读取 `http_proxy` 环境变量，把局域网请求塞进本机代理 —— 插件显式禁用代理。

> 关于监听地址：官方文档记载 8081 绑定 `127.0.0.1`（旧版本，需 `adb forward`），
> `ld-api` 与实测的 3.x 受控模式则对局域网开放。因此插件支持任意设备 IP，
> 也支持在「高级」里分别覆盖控制口 / 生成口地址，以兼容端口转发场景。

---

## 7. 仅启用本插件即可工作

| 环节 | 由谁提供 |
|---|---|
| 设置界面 | 插件的 `js/localdream.js`（ComfyUI 自动加载 `WEB_DIRECTORY`） |
| 设置 / 诊断 / 模型 / 测试生成的 HTTP 接口 | `PromptServer.instance.routes` 上的 `/localdream/*`（ComfyUI 自带 aiohttp） |
| 生成请求与 SSE 解析 | 节点在 ComfyUI 执行线程内直接 `urllib` 请求手机 |
| 进度条 | `comfy.utils.ProgressBar` 全局 hook → ComfyUI WebSocket |
| 图像张量 | ComfyUI 自带 `torch` / `numpy` / `Pillow` |

没有 `serve.py`、没有独立进程、没有 Docker、没有额外端口需要开放给用户。
唯一的前提是手机上的 Local Dream App 已手动进入受控模式。

---

## 8. 验证

插件附带一套**零依赖离线端到端验证**（`tests/`），用纯标准库模拟受控模式设备，
不需要真实手机、不需要安装 ComfyUI：

```bash
cd <ComfyUI>/custom_nodes/Localdream-in-ComfyUI
python tests/run_tests.py
```

覆盖：节点映射与 `INPUT_TYPES` 加载、参数校验、设置保存/读取/重置与全部 HTTP 路由、
`/info` `/models` `/status` `/select`、SSE `progress`/`complete` 解析、原生进度条回调序列、
原始 RGB → ComfyUI `IMAGE` 张量、图生图 PNG 编码、批量拼接、设置界面 JS 语法与交互冒烟测试。
当前 **102 项断言全部通过**，设置界面实际渲染截图见 `tests/settings_dialog_preview.png`。
真机测试中发现并修复了设备 `total_steps` 与请求 `steps` 不一致、甚至同一次生成中途变化的情况，
现在进度条按设备报告比例映射到固定总量，max 稳定、value 单调走到 100%。

更详细的验证说明见 `tests/README.md`。

### 真机验证记录（ComfyUI 0.42.5 + Local Dream 3.0.0-alpha.7）

只加载本插件启动 ComfyUI（`--disable-all-custom-nodes --whitelist-custom-nodes Localdream-in-ComfyUI`），
全程仅使用一个 DMD2 模型，验证通过：

| 项目 | 结果 |
|---|---|
| 节点加载 | `/object_info` 出现两个节点；`/extensions` 加载 `localdream.js`；20 条路由注册成功 |
| 前端 | 真实 Vue 前端创建节点后生成「⚙ LocalDream 设置」按钮（`serialize:false`），点击可打开弹窗；IP / 端口 / 模型下拉 / 设备状态正确回填 |
| 设置 | 测试连接、载入模型、模型刷新、保存并读回、恢复默认、设置面板测试生成均正常 |
| 出图 | 设置面板测试生成、`/prompt` 文生图、`/prompt` 文生图→图生图串联均成功，输出 1024×1024 RGB PNG，图像正常无花屏 |
| 进度条 | WebSocket `progress` 事件归属到正确节点；两节点 max 稳定、value 单调，均收尾 100%；`progress_state` 正常推送 |
| 设备约束 | 全程 `serving_model_id` 保持为指定模型，未载入或切换到任何其它模型 |

若自行复测，建议仍按下面 5 步确认：

1. 重启后「添加节点 → LocalDream」能看到两个节点，`/object_info` 里有 `LocalDreamTextToImage` / `LocalDreamImageToImage`。
2. 点节点上的 ⚙ 按钮能打开设置弹窗，测试连接显示设备信息，保存后模型下拉框出现设备上的模型 id。
3. 运行文生图，ComfyUI 顶部进度条按步数推进，`PreviewImage` 出现图片。
4. 把文生图输出接到图生图节点的 `image`，再运行，得到重绘结果。
5. 运行中点「取消」能及时中断。

---

## 9. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 控制口 8808 不可达 | 回 App 重新「进入受控模式」；确认同网段、IP 未变、App 未被系统回收 |
| 控制口正常、8081 未监听 | 正常状态：尚未选定模型。设置里点「载入模型并等待就绪」 |
| `Img size mismatch` / 尺寸报错 | 开启「自动匹配模型分辨率」；SDXL 用 `aspect_ratio`，不要指望 `width/height` |
| 生成结果全黑 / 花屏 | 检查是否开启了「显示生成过程」但设备不支持中间帧；关闭预览再试 |
| 请求立刻失败、日志出现代理字样 | 保持「绕过系统 HTTP 代理」开启 |
| 工作流第二次运行没有进度 | ComfyUI 缓存命中，改种子或参数即可 |
| 模型下拉框是空的 | 点设置里的「刷新模型列表」；设备离线时会回退到上次缓存 |

---

## 10. 目录结构

```
Localdream-in-ComfyUI/
├── __init__.py              # 节点映射 + WEB_DIRECTORY + 注册路由
├── nodes.py                 # 文生图 / 图生图节点 + 原生进度条
├── ld_client.py             # 配置、双端口 HTTP、SSE、模型、图像编解码（零第三方依赖）
├── web_api.py               # ComfyUI 同源设置/诊断/模型/测试生成路由
├── js/localdream.js         # 设置按钮 + 独立设置弹窗 + 模型下拉框同步
├── examples/                # /prompt 格式的示例工作流
├── tests/                   # 模拟设备 + 离线端到端验证 + 设置界面截图
├── settings.example.json    # 可公开的配置模板（示例 IP，可提交）
├── settings.json            # 运行时生成，含真实设备 IP（已 gitignore，勿提交）
├── models_cache.json        # 运行时生成，含设备模型与手机路径（已 gitignore，勿提交）
├── pyproject.toml
├── requirements.txt         # 空：无额外依赖
└── LICENSE                  # MIT
```

---

## 11. 隐私与发布注意事项

**不要 push 到 GitHub 的文件**（`.gitignore` 已覆盖前 5 项）：

| 文件 / 目录 | 含什么隐私 |
|---|---|
| `settings.json`、`settings.json.tmp`、`settings.*.json` | 手机局域网 IP、控制口/生成口、地址覆盖、默认模型、超时等本机连接配置 |
| `models_cache.json` | 设备上的模型清单与手机内部存储绝对路径（`/storage/emulated/0/...`） |
| `*.log`、`*.log.*` | 运行日志会记录设备 URL、模型 id、提示词与请求耗时 |
| `.env`、`.env.*`、`*_local.json` | 可能含 `LOCALDREAM_HOST` 等本机地址 |
| `tests/`、`realdevice*/`、`output/` | 本地验证产物、真机测试脚本与生成图；生成图 PNG 元数据里还带提示词与工作流 |
| ComfyUI 安装目录下 `custom_nodes/Localdream-in-ComfyUI/settings.json` | 与上面第一项相同，是本机安装副本，别把它当插件源码提交 |

**可以安全提交**：`settings.example.json`（只含示例 IP `192.168.1.7` 与默认值）、
`README.md`、四个 `.py`、`js/`、`examples/`、`pyproject.toml`、`requirements.txt`、`LICENSE`、
`.gitignore`。文档与离线测试里出现的 `192.168.1.7` / `10.0.0.42` / `192.0.2.1` 都是示例或保留网段，
不是真实设备地址。

提交前自检：

```bash
git status --short
git grep -nE "([0-9]{1,3}\.){3}[0-9]{1,3}"   # 只应看到示例 IP 与 127.0.0.1
git check-ignore -v settings.json models_cache.json
```

> 若用环境变量 `LOCALDREAM_HOST` 预置设备地址，请放在 shell 配置或本机 `.env` 里，
> 不要写进任何会被提交的文件。

---

## 12. 许可

MIT。本项目是第三方接入插件，与 Local Dream 官方无隶属关系。
