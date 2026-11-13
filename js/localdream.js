/*
 * Localdream-in-ComfyUI / 前端扩展
 * ---------------------------------------------------------------
 * 1. 给两个节点各加一个「⚙ 设置」按钮，点击打开独立设置界面
 * 2. 设置界面：设备 IP / 双端口 / 超时 / 模型 / 预览 / 诊断 / 测试生成
 * 3. 异步刷新节点上的模型下拉框（设备侧模型可能随时变化）
 *
 * 所有请求都打到 ComfyUI 自己的 /localdream/* 路由，不需要额外服务。
 * 不依赖 jQuery / 外部 CDN / 构建步骤。
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EXT_NAME = "Localdream.ComfyUI.Settings";
const NODE_CLASSES = ["LocalDreamTextToImage", "LocalDreamImageToImage"];
const AUTO_FALLBACK = "自动（使用设备已载入模型）";

// ComfyUI 前端 getNodeSource() 会把 python_module 段的 "-ComfyUI" 后缀砍掉，
// 于是节点来源徽标显示成 "Localdream-in"。这里在节点定义注册前补一段同名后缀，
// 缩短后正好还原成完整的插件名；同时直接修正已生成的 nodeSource 对象。
const PACK_DISPLAY_NAME = "Localdream-in-ComfyUI";
const PACK_PYTHON_MODULE = "custom_nodes.Localdream-in-ComfyUI-ComfyUI";

function fixNodeSourceBadge(nodeData) {
  if (!nodeData) return;
  if (typeof nodeData.python_module === "string" &&
      nodeData.python_module.includes("Localdream-in")) {
    nodeData.python_module = PACK_PYTHON_MODULE;
  }
  const source = nodeData.nodeSource;
  if (source && source.type === "custom_nodes" &&
      /^Localdream-in/i.test(String(source.displayText || ""))) {
    source.displayText = PACK_DISPLAY_NAME;
    source.badgeText = PACK_DISPLAY_NAME;
  }
}

/* ------------------------------------------------------------------ */
/* 小工具                                                              */
/* ------------------------------------------------------------------ */

function isOurNode(node) {
  return !!node && NODE_CLASSES.includes(node.comfyClass || node.type);
}

async function ldFetch(path, options) {
  // 不同 ComfyUI 前端版本的 api.fetchApi 前缀 / 子路径行为略有差异。
  // 优先同源绝对地址（后端已同时注册 /localdream/* 与 /api/localdream/*），
  // 失败时再依次尝试 api.fetchApi，兼容旧版 / 子路径部署。
  const attempts = [
    () => fetch(path, options),
    () => api.fetchApi(path, options),
    () => api.fetchApi("/api" + path, options),
  ];
  let last = null;
  const isPost = (options && String(options.method || "").toUpperCase() === "POST");
  for (const run of attempts) {
    try {
      const res = await run();
      if (res && res.ok) {
        const ct = (res.headers && res.headers.get ? res.headers.get("content-type") : "") || "";
        // GET 时若命中的是前端 SPA 的 HTML 兜底页，继续尝试下一种写法
        if (isPost || !ct || /json|text|javascript/i.test(ct)) return res;
      }
      last = res || last;
    } catch (e) {
      last = e;
    }
  }
  if (last && last.ok === false) return last;
  throw last || new Error("无法访问 " + path);
}

async function ldJSON(path, options) {
  const res = await ldFetch(path, options);
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
  return res.json();
}

function postJSON(path, body) {
  return ldJSON(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.setAttribute("style", v);
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function toast(message, kind) {
  const color = kind === "error" ? "#ff6b6b" : kind === "ok" ? "#4ade80" : "#8ab4f8";
  const tip = h("div", {
    class: "ld-toast",
    style: `position:fixed;z-index:200000;right:18px;bottom:18px;max-width:420px;
            padding:10px 14px;border-radius:8px;border-left:4px solid ${color};
            background:var(--comfy-menu-bg,#22242a);color:var(--fg-color,#eee);
            box-shadow:0 8px 24px rgba(0,0,0,.35);font:13px/1.5 sans-serif;white-space:pre-wrap;`,
    text: message,
  });
  document.body.appendChild(tip);
  setTimeout(() => tip.remove(), 4200);
}

/* ------------------------------------------------------------------ */
/* 样式                                                                */
/* ------------------------------------------------------------------ */

const CSS = `
.ld-root{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;
  background:rgba(8,10,14,.62);backdrop-filter:blur(3px);font:13px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;}
.ld-panel{--ld-bg:var(--comfy-menu-bg,#202228);--ld-fg:var(--fg-color,#e9eaec);
  --ld-sub:var(--descrip-text,#9aa0aa);--ld-line:var(--border-color,#3a3d44);
  --ld-input:var(--comfy-input-bg,#2a2d34);--ld-accent:#4c8dff;
  width:min(760px,94vw);height:min(620px,90vh);display:flex;flex-direction:column;overflow:hidden;
  background:var(--ld-bg);color:var(--ld-fg);border:1px solid var(--ld-line);
  border-radius:12px;box-shadow:0 24px 70px rgba(0,0,0,.55);}
.ld-head{display:flex;align-items:center;gap:12px;padding:14px 18px;border-bottom:1px solid var(--ld-line);}
.ld-head h2{font-size:15px;font-weight:650;margin:0;letter-spacing:.2px;}
.ld-head .ld-sub{font-size:11px;color:var(--ld-sub);margin-top:2px;}
.ld-pill{margin-left:auto;font-size:11px;padding:3px 9px;border-radius:20px;border:1px solid var(--ld-line);
  color:var(--ld-sub);white-space:nowrap;}
.ld-pill.ok{color:#4ade80;border-color:#2f6b46;}
.ld-pill.bad{color:#ff8a8a;border-color:#7a3a3a;}
.ld-x{cursor:pointer;background:transparent;border:0;color:var(--ld-sub);font-size:20px;line-height:1;padding:2px 6px;}
.ld-x:hover{color:var(--ld-fg);}
.ld-body{display:flex;flex:1;min-height:0;}
.ld-nav{width:132px;flex:none;border-right:1px solid var(--ld-line);padding:10px 8px;background:rgba(127,127,127,.04);}
.ld-nav button{display:block;width:100%;text-align:left;padding:8px 10px;margin-bottom:3px;border:0;border-radius:6px;
  background:transparent;color:var(--ld-sub);cursor:pointer;font:inherit;}
.ld-nav button:hover{background:rgba(127,127,127,.12);color:var(--ld-fg);}
.ld-nav button.active{background:rgba(76,141,255,.18);color:#9dc0ff;font-weight:600;}
.ld-main{flex:1;min-width:0;overflow:auto;padding:16px 18px 22px;}
.ld-page{display:none;} .ld-page.active{display:block;}
.ld-page h3{margin:0 0 4px;font-size:13px;font-weight:650;}
.ld-page .desc{color:var(--ld-sub);font-size:11.5px;margin:0 0 14px;}
.ld-row{display:flex;align-items:center;gap:10px;margin-bottom:11px;}
.ld-row>label{flex:0 0 132px;color:var(--ld-sub);font-size:12px;}
.ld-row .field{flex:1;min-width:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.ld-row .unit{color:var(--ld-sub);font-size:11px;}
input.ld,select.ld,textarea.ld{width:100%;box-sizing:border-box;background:var(--ld-input);color:var(--ld-fg);
  border:1px solid var(--ld-line);border-radius:6px;padding:6px 9px;font:inherit;outline:none;}
input.ld:focus,select.ld:focus,textarea.ld:focus{border-color:var(--ld-accent);}
input.ld[type=checkbox]{width:15px;height:15px;accent-color:var(--ld-accent);flex:none;}
textarea.ld{resize:vertical;min-height:52px;}
.ld-row.check>label{flex:0 0 auto;order:2;color:var(--ld-fg);cursor:pointer;}
.ld-row.check .field{order:1;flex:0 0 auto;}
.ld-btn{cursor:pointer;border:1px solid var(--ld-line);background:rgba(127,127,127,.10);color:var(--ld-fg);
  border-radius:6px;padding:6px 12px;font:inherit;white-space:nowrap;}
.ld-btn:hover{border-color:var(--ld-accent);}
.ld-btn.primary{background:var(--ld-accent);border-color:var(--ld-accent);color:#fff;font-weight:600;}
.ld-btn.primary:hover{filter:brightness(1.1);}
.ld-btn:disabled{opacity:.5;cursor:wait;}
.ld-foot{display:flex;align-items:center;gap:8px;padding:11px 18px;border-top:1px solid var(--ld-line);flex-wrap:wrap;}
.ld-foot .spacer{flex:1;}
.ld-status{font-size:11.5px;padding:8px 10px;margin:6px 0 12px;border-radius:6px;
  border:1px solid var(--ld-line);background:rgba(127,127,127,.07);color:var(--ld-sub);white-space:pre-wrap;}
.ld-status.ok{color:#7ee2a8;} .ld-status.bad{color:#ff9b9b;}
.ld-grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 14px;}
.ld-hint{font-size:11px;color:var(--ld-sub);margin:-4px 0 12px 142px;}
.ld-preview{border:1px dashed var(--ld-line);border-radius:8px;min-height:96px;margin-top:10px;
  display:flex;align-items:center;justify-content:center;color:var(--ld-sub);font-size:11px;overflow:hidden;}
.ld-preview img{max-width:100%;max-height:220px;display:block;}
.ld-link{color:#8ab4f8;text-decoration:none;} .ld-link:hover{text-decoration:underline;}
.ld-code{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--ld-sub);
  background:rgba(127,127,127,.08);padding:8px 10px;border-radius:6px;white-space:pre-wrap;word-break:break-all;}
`;

function injectStyle() {
  if (document.getElementById("ld-comfy-style")) return;
  const style = h("style", { id: "ld-comfy-style", text: CSS });
  document.head.appendChild(style);
}

/* ------------------------------------------------------------------ */
/* 设置界面                                                            */
/* ------------------------------------------------------------------ */

let root = null;
const F = {};           // 表单控件
let state = { defaults: {}, settings: {}, models: [], info: null, status: null };
let activeTab = "conn";

function field(name, input) { F[name] = input; return input; }

function row(label, control, hint) {
  const line = h("div", { class: "ld-row" }, h("label", { text: label }),
    h("div", { class: "field" }, control));
  return hint ? h("div", {}, line, h("div", { class: "ld-hint", text: hint })) : line;
}

function checkRow(label, name, hint) {
  const input = field(name, h("input", { class: "ld", id: "ld-chk-" + name, type: "checkbox" }));
  const line = h("div", { class: "ld-row check" }, h("label", { text: label }),
    h("div", { class: "field" }, input));
  return hint ? h("div", {}, line, h("div", { class: "ld-hint", text: hint })) : line;
}

function buildDialog() {
  injectStyle();
  if (root) return root;

  const status = h("div", { class: "ld-status", id: "ld-status", text: "尚未检测设备。" });
  F.status = status;
  const pill = h("span", { class: "ld-pill", id: "ld-pill", text: "未连接" });
  F.pill = pill;

  const modelSelect = field("default_model", h("select", { class: "ld", id: "ld-model" }));
  const testImg = h("div", { class: "ld-preview", id: "ld-preview", text: "测试生成结果会显示在这里" });
  F.preview = testImg;
  const testPrompt = field("test_prompt", h("input", { class: "ld", id: "ld-test-prompt", value: "a cute cat, masterpiece" }));
  const testSteps = field("test_steps", h("input", { class: "ld", type: "number", min: "1", max: "20", value: "6", style: "width:90px" }));

  const setStatus = (msg, kind) => {
    status.textContent = msg;
    status.className = "ld-status" + (kind ? " " + kind : "");
  };
  F.setStatus = setStatus;

  /* --- 连接页 --- */
  const pageConn = h("div", { class: "ld-page active", "data-page": "conn" },
    h("h3", { text: "连接 · 受控模式双端口" }),
    h("p", { class: "desc", text: "手机端：Local Dream → 模型列表右上角 ⋮ → 设备互联 → 受控模式 → 进入受控模式。两个端口默认对局域网开放，无需 adb forward，也无需任何额外后台程序。" }),
    row("设备 IP / 主机", field("host", h("input", { class: "ld", id: "ld-host", placeholder: "例如 192.168.1.7" }))),
    h("div", { class: "ld-grid2" },
      row("控制口", field("control_port", h("input", { class: "ld", id: "ld-ctl-port", type: "number", min: "1", max: "65535" }))),
      row("生成口", field("gen_port", h("input", { class: "ld", id: "ld-gen-port", type: "number", min: "1", max: "65535" }))),
      row("载入分辨率 W", field("default_width", h("input", { class: "ld", id: "ld-width", type: "number", min: "64", max: "2048", step: "8" }))),
      row("载入分辨率 H", field("default_height", h("input", { class: "ld", id: "ld-height", type: "number", min: "64", max: "2048", step: "8" })))),
    row("默认模型", h("div", { class: "field", style: "flex-direction:column;align-items:stretch" },
      modelSelect,
      h("div", { class: "field", style: "margin-top:8px" },
        h("button", { class: "ld-btn", id: "ld-load-model", text: "载入模型并等待就绪", onclick: () => loadModel() }),
        h("button", { class: "ld-btn", id: "ld-refresh", text: "刷新模型列表", onclick: () => refreshModels(true) }))),
      "模型列表来自设备 GET :8808/models；选择后刷新 ComfyUI 节点上的模型下拉框。"),
    h("div", { class: "ld-row" }, h("label", { text: "连接检测" }),
      h("div", { class: "field" },
        h("button", { class: "ld-btn", id: "ld-diag", text: "测试连接", onclick: () => diagnose(true) }),
        h("span", { class: "unit", text: "同时探测 8808 / 8081 的 TCP 可达性" }))),
    status,
    h("div", { class: "ld-code", id: "ld-device-json", text: "{}" }),
  );
  F.deviceJSON = pageConn.querySelector("#ld-device-json");

  /* --- 生成页 --- */
  const pageGen = h("div", { class: "ld-page", "data-page": "gen" },
    h("h3", { text: "生成 · 默认行为" }),
    h("p", { class: "desc", text: "这些是全局默认值；节点上的 CFG / 步数 / 去噪 / 采样器 / 模型等参数优先。" }),
    row("默认反向提示词", field("default_negative_prompt", h("textarea", { class: "ld", id: "ld-neg", placeholder: "节点反向词留空时使用" }))),
    checkRow("自动载入所选模型", "auto_select_model", "节点模型与设备当前模型不一致时自动 POST :8808/select。"),
    checkRow("自动匹配模型分辨率", "auto_match_resolution", "NPU 模式下向模型 resolutions 靠拢，避免尺寸不匹配报错。"),
    checkRow("请求扩散过程预览", "show_diffusion_process", "需要手机端 App 同时开启「显示生成过程」；未开启时仍会推送步数进度。"),
    row("预览步幅", field("preview_stride", h("input", { class: "ld", type: "number", min: "1", max: "20", style: "width:110px" })), "每 N 步回传一帧中间图。"),
    checkRow("在 ComfyUI 显示中间帧", "preview_in_comfy", "把中间帧送进 ComfyUI 原生预览通道。"),
    h("h3", { style: "margin-top:22px", text: "测试生成（不经过工作流）" }),
    h("p", { class: "desc", text: "走与节点完全相同的 /generate 链路，用 6 步出一张小图，用于确认配置与出图都正常。" }),
    row("测试提示词", testPrompt),
    row("测试步数", testSteps),
    h("div", { class: "ld-row" }, h("label", { text: "" }),
      h("div", { class: "field" }, h("button", { class: "ld-btn", id: "ld-test-gen", text: "生成一张测试图", onclick: () => testGenerate() }))),
    testImg,
  );

  /* --- 高级页 --- */
  const pageAdv = h("div", { class: "ld-page", "data-page": "adv" },
    h("h3", { text: "高级 · 超时与地址覆盖" }),
    h("p", { class: "desc", text: "只有把 Local Dream 跑在别的设备或做了端口转发时才需要改下面两项地址覆盖。" }),
    h("div", { class: "ld-grid2" },
      row("生成超时 (秒)", field("timeout", h("input", { class: "ld", type: "number", min: "10", max: "3600" }))),
      row("控制口超时 (秒)", field("connect_timeout", h("input", { class: "ld", type: "number", min: "1", max: "60", step: "0.5" }))),
      row("模型载入超时 (秒)", field("model_load_timeout", h("input", { class: "ld", type: "number", min: "5", max: "900" }))),
      row("模型缓存 TTL (秒)", field("models_ttl", h("input", { class: "ld", type: "number", min: "0", max: "600" })))),
    row("控制口地址覆盖", field("control_host", h("input", { class: "ld", placeholder: "留空则使用设备 IP" }))),
    row("生成口地址覆盖", field("gen_host", h("input", { class: "ld", placeholder: "留空则使用设备 IP" }))),
    checkRow("绕过系统 HTTP 代理", "disable_proxy", "局域网直连必须开启，否则 urllib 会把请求塞进本机代理。"),
    h("div", { class: "ld-row" }, h("label", { text: "重置" }),
      h("div", { class: "field" }, h("button", { class: "ld-btn", text: "恢复默认设置", onclick: () => resetSettings() }))),
  );

  /* --- 关于页 --- */
  const pageAbout = h("div", { class: "ld-page", "data-page": "about" },
    h("h3", { text: "关于 / 接口依据" }),
    h("p", { class: "desc", text: "本插件只与手机端 Local Dream 受控模式通信，所有推理在手机上完成；不需要云端、不需要额外后台程序。" }),
    h("div", { class: "ld-code", text:
      ":8808 控制口  GET /info /models /status · POST /select /stop\n" +
      ":8081 生成口  POST /generate (SSE) · POST /tokenize\n\n" +
      "SSE: progress {step,total_steps} → complete {image:base64 RGB24}\n" +
      "complete.image 为原始 RGB 像素，不是 PNG/JPG。" }),
    h("p", { style: "margin-top:14px" },
      h("a", { class: "ld-link", href: "https://ld-guide.chino.icu/zh/features/http-api", target: "_blank", rel: "noopener", text: "Local Dream 官方 HTTP API 参考" })),
    h("p", {}, h("a", { class: "ld-link", href: "https://github.com/kongxiangyiren/ld-api", target: "_blank", rel: "noopener", text: "@kongxiangyiren/ld-api（受控模式客户端）" })),
    h("p", {}, h("a", { class: "ld-link", href: "https://dshpluginhub.ai/zh/plugins/dsh-localdream", target: "_blank", rel: "noopener", text: "受控模式双端口说明" })),
    h("p", { class: "desc", style: "margin-top:16px", text: "提示：8081 只有在 App 内选定模型后才开始监听；设备熄屏或长时间空闲后可能被系统回收，回 App 重新进入受控模式即可。" }),
  );

  const tabs = [
    ["conn", "连接", pageConn], ["gen", "生成", pageGen],
    ["adv", "高级", pageAdv], ["about", "关于", pageAbout],
  ];
  const nav = h("div", { class: "ld-nav" });
  for (const [key, label] of tabs) {
    nav.appendChild(h("button", {
      "data-tab": key, class: key === activeTab ? "active" : "",
      text: label, onclick: () => switchTab(key),
    }));
  }
  const main = h("div", { class: "ld-main" }, ...tabs.map((t) => t[2]));

  const foot = h("div", { class: "ld-foot" },
    h("span", { class: "unit", text: "配置保存在插件目录 settings.json，所有节点共用" }),
    h("span", { class: "spacer" }),
    h("button", { class: "ld-btn", text: "取消", onclick: () => closeDialog() }),
    h("button", { class: "ld-btn primary", id: "ld-save", text: "保存并生效", onclick: () => saveSettings() }));

  root = h("div", { class: "ld-root", id: "ld-root" },
    h("div", { class: "ld-panel", role: "dialog", "aria-modal": "true" },
      h("div", { class: "ld-head" },
        h("div", {}, h("h2", { text: "LocalDream 受控模式 · 设置" }),
          h("div", { class: "ld-sub", text: "Localdream-in-ComfyUI · 手机端 NPU 生图" })),
        pill,
        h("button", { class: "ld-x", text: "×", "aria-label": "关闭", onclick: () => closeDialog() })),
      h("div", { class: "ld-body" }, nav, main), foot));

  root.addEventListener("mousedown", (ev) => { if (ev.target === root) closeDialog(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && root && root.isConnected) { ev.stopPropagation(); closeDialog(); }
  });
  document.body.appendChild(root);
  return root;
}

function switchTab(key) {
  activeTab = key;
  if (!root) return;
  root.querySelectorAll(".ld-nav button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === key));
  root.querySelectorAll(".ld-page").forEach((p) =>
    p.classList.toggle("active", p.dataset.page === key));
}

function openDialog() {
  buildDialog();
  root.style.display = "flex";
  switchTab("conn");
  loadSettings();
}

function closeDialog() {
  if (root) root.style.display = "none";
}

/* ------------------------------------------------------------------ */
/* 设置读写 / 设备操作                                                  */
/* ------------------------------------------------------------------ */

function fillForm(settings) {
  state.settings = settings || {};
  for (const [key, input] of Object.entries(F)) {
    if (!input || !input.tagName || !(key in settings)) continue;
    if (input.type === "checkbox") input.checked = !!settings[key];
    else input.value = settings[key] ?? "";
  }
  fillModels(state.models);
}

function fillModels(models) {
  state.models = models || [];
  const select = F.default_model;
  if (!select) return;
  const current = state.settings.default_model || AUTO_FALLBACK;
  select.innerHTML = "";
  const auto = h("option", { value: AUTO_FALLBACK, text: "自动（使用设备已载入模型）" });
  select.appendChild(auto);
  for (const m of state.models) {
    const label = `${m.name || m.id}${m.is_sdxl ? " · SDXL" : ""}${m.run_on_cpu ? " · CPU" : ""}`;
    select.appendChild(h("option", { value: String(m.id), text: label }));
  }
  if (![...select.options].some((o) => o.value === current)) {
    select.appendChild(h("option", { value: current, text: current + "（未在当前设备上找到）" }));
  }
  select.value = current;
}

function collectForm() {
  const patch = {};
  for (const [key, input] of Object.entries(F)) {
    if (!input || !input.tagName || !(key in state.defaults)) continue;
    if (input.type === "checkbox") patch[key] = input.checked;
    else if (input.type === "number") patch[key] = input.value === "" ? state.defaults[key] : Number(input.value);
    else patch[key] = input.value;
  }
  return patch;
}

async function loadSettings() {
  try {
    const data = await ldJSON("/localdream/settings");
    state.defaults = data.defaults || {};
    F.setStatus("正在读取配置…");
    fillForm(data.settings);
    await diagnose(false);
  } catch (e) {
    F.setStatus("读取配置失败：" + e.message, "bad");
  }
}

async function diagnose(verbose) {
  F.setStatus("正在检测受控模式两个端口…");
  F.pill.textContent = "检测中…";
  F.pill.className = "ld-pill";
  try {
    const report = await ldJSON("/localdream/diagnose");
    state.info = report.info; state.status = report.status;
    if (Array.isArray(report.models)) { state.models = report.models; fillModels(report.models); }
    const info = report.info || {};
    const st = report.status || {};
    const detail = [
      `控制口 ${report.control?.url || ""}：${report.control?.tcp ? "可达" : "不可达"}`,
      `生成口 ${report.generation?.url || ""}：${report.generation?.tcp ? "可达" : "未监听"}`,
      info.app ? `设备：${info.device || "-"} · App ${info.version || "-"} · 协议 ${info.protocol ?? "-"}` : "",
      st.state ? `后端状态：${st.state} · 模型 ${st.serving_model_id || "无"} · ${st.width || "?"}×${st.height || "?"}` : "",
      `已安装模型：${state.models.length} 个`,
      report.hint || "",
    ].filter(Boolean).join("\n");
    F.setStatus(detail, report.reachable ? "ok" : "");
    F.pill.textContent = report.reachable ? "已连接" : (report.control?.tcp ? "控制口正常" : "未连接");
    F.pill.className = "ld-pill " + (report.reachable ? "ok" : "bad");
    F.deviceJSON.textContent = JSON.stringify({ info, status: st }, null, 2);
    if (verbose) toast(detail, report.reachable ? "ok" : "error");
    return report;
  } catch (e) {
    F.setStatus("检测失败：" + e.message, "bad");
    F.pill.textContent = "检测失败";
    F.pill.className = "ld-pill bad";
    if (verbose) toast("检测失败：" + e.message, "error");
    return null;
  }
}

async function refreshModels(verbose) {
  try {
    const data = await ldJSON("/localdream/models/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (!data.ok) throw new Error(data.error?.message || "未知错误");
    state.models = data.models || [];
    fillModels(state.models);
    await refreshComboWidgets(data.choices || []);
    if (verbose) toast(`模型列表已刷新：${state.models.length} 个模型，节点下拉框已同步。`, "ok");
    F.setStatus(`模型列表已刷新：${state.models.length} 个模型。`, "ok");
  } catch (e) {
    if (verbose) toast("刷新模型列表失败：" + e.message, "error");
    F.setStatus("刷新模型列表失败：" + e.message, "bad");
  }
}

async function refreshComboWidgets(choices) {
  const values = (choices && choices.length) ? choices : await ldJSON("/localdream/models").catch(() => [AUTO_FALLBACK]);
  const list = Array.isArray(values) ? values : [AUTO_FALLBACK];
  let nodes = [];
  try {
    const graph = app.graph || (app.canvas && app.canvas.graph) || {};
    nodes = graph._nodes || graph.nodes || [];
    if (typeof graph.forEachNode === "function")
      graph.forEachNode((n) => { if (n && !nodes.includes(n)) nodes.push(n); });
  } catch (e) {
    nodes = [];
  }
  for (const node of nodes) {
    if (!isOurNode(node)) continue;
    const widget = (node.widgets || []).find((w) => w.name === "model");
    if (!widget) continue;
    if (!widget.options) widget.options = {};
    widget.options.values = list.slice();
    if (!list.includes(widget.value)) widget.value = list[0];
    if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
  }
  return list;
}

async function loadModel() {
  const model = F.default_model.value;
  F.setStatus("正在载入模型并等待后端就绪…");
  try {
    const data = await postJSON("/localdream/model/select", {
      model_id: model,
      width: Number(F.default_width.value || 512),
      height: Number(F.default_height.value || 512),
    });
    if (!data.ok) throw new Error(data.error?.message || "未知错误");
    F.setStatus(`模型已就绪：${data.model_id} · ${data.width}×${data.height}`, "ok");
    toast("模型已载入，生成口 8081 现在可用。", "ok");
    await diagnose(false);
  } catch (e) {
    F.setStatus("载入模型失败：" + e.message, "bad");
    toast("载入模型失败：" + e.message, "error");
  }
}

async function testGenerate() {
  const btn = root.querySelector("#ld-test-gen");
  btn.disabled = true;
  F.preview.innerHTML = "";
  F.preview.textContent = "生成中…（手机端推理，请稍候）";
  try {
    const data = await postJSON("/localdream/test-generate", {
      prompt: F.test_prompt.value || "a cute cat",
      steps: Number(F.test_steps.value || 6),
      model: F.default_model.value,
    });
    if (!data.ok) throw new Error(data.error?.message || "未知错误");
    F.preview.innerHTML = "";
    F.preview.appendChild(h("img", { src: data.image, alt: "测试生成结果" }));
    F.setStatus(`测试生成成功：${data.width}×${data.height} · seed ${data.seed} · ` +
      `步数事件 ${data.progress_events?.length || 0} 个 · 设备耗时 ${data.generation_time_ms ?? "?"} ms`, "ok");
    toast("测试生成成功。", "ok");
  } catch (e) {
    F.preview.textContent = "生成失败";
    F.setStatus("测试生成失败：" + e.message, "bad");
    toast("测试生成失败：" + e.message, "error");
  } finally {
    btn.disabled = false;
  }
}

async function saveSettings() {
  const btn = root.querySelector("#ld-save");
  btn.disabled = true;
  try {
    const data = await ldJSON("/localdream/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectForm()),
    });
    if (!data.ok) throw new Error(data.error?.message || "未知错误");
    fillForm(data.settings);
    await refreshModels(false);
    await diagnose(false);
    toast("设置已保存，下一次生成立即生效（无需重启 ComfyUI）。", "ok");
  } catch (e) {
    F.setStatus("保存失败：" + e.message, "bad");
    toast("保存失败：" + e.message, "error");
  } finally {
    btn.disabled = false;
  }
}

async function resetSettings() {
  try {
    const data = await postJSON("/localdream/settings/reset", {});
    state.defaults = data.defaults || state.defaults;
    fillForm(data.settings);
    await diagnose(false);
    toast("已恢复默认设置。", "ok");
  } catch (e) {
    toast("恢复默认失败：" + e.message, "error");
  }
}

/* ------------------------------------------------------------------ */
/* 节点上的设置按钮                                                     */
/* ------------------------------------------------------------------ */

function addSettingsButton(node) {
  if (!isOurNode(node) || node._ldSettingsButton) return;
  let widget = null;
  try {
    widget = node.addWidget("button", "⚙ LocalDream 设置", null, () => openDialog(), { serialize: false });
    if (widget) {
      widget.serialize = false;
      if (widget.options) widget.options.serialize = false;
    }
  } catch (e) {
    console.warn("[Localdream] addWidget 不可用，尝试 DOM 控件", e);
  }
  if (!widget && typeof node.addDOMWidget === "function") {
    try {
      const button = h("button", { class: "ld-btn", text: "⚙ LocalDream 设置", onclick: () => openDialog() });
      widget = node.addDOMWidget("ld_settings", "button", button, { serialize: false, hideOnZoom: false });
    } catch (e) { /* 交给右键菜单兜底 */ }
  }
  node._ldSettingsButton = true;
  // 设备可能刚上线，异步把模型列表同步到下拉框
  refreshComboWidgets(null).catch(() => {});
}

app.registerExtension({
  name: EXT_NAME,

  async setup() {
    injectStyle();
    // 打开工作流后统一同步一次模型下拉框
    refreshComboWidgets(null).catch(() => {});
  },

  async nodeCreated(node) {
    try {
      fixNodeSourceBadge(node && node.constructor && node.constructor.nodeData);
    } catch (e) { /* 忽略：不同前端版本 nodeData 结构可能不同 */ }
    addSettingsButton(node);
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!nodeType || !NODE_CLASSES.includes(nodeType.comfyClass || nodeType.type)) return;
    fixNodeSourceBadge(nodeData);
    try { fixNodeSourceBadge(nodeType.nodeData); } catch (e) { /* 忽略 */ }
    if (nodeType.prototype._ldMenuHooked) return;
    nodeType.prototype._ldMenuHooked = true;
    // 右键菜单里也放一个入口，作为某些前端版本按钮控件失效时的兜底
    try {
      const original = nodeType.prototype.getExtraMenuOptions;
      nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
        const result = original ? original.apply(this, arguments) : undefined;
        if (options && options.unshift) {
          options.unshift({
            content: "⚙ LocalDream 受控模式设置",
            callback: () => openDialog(),
          });
        }
        return result;
      };
    } catch (e) { /* 忽略，节点按钮仍然可用 */ }
  },
});

export { openDialog as openLocalDreamSettings };
