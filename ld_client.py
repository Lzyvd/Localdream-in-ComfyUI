# -*- coding: utf-8 -*-
"""
Localdream-in-ComfyUI / 受控模式客户端核心
=========================================

只使用 Python 标准库完成与手机端 Local Dream「受控模式」的通信，
numpy / torch / Pillow 只在图像转换时按需导入（ComfyUI 自带这些依赖）。

接口依据（开发前已核对）：
  * Local Dream 官方指南 · HTTP API 参考
    https://ld-guide.chino.icu/zh/features/http-api
  * @kongxiangyiren/ld-api (src/device.ts / src/generation.ts / src/type.ts)
    https://github.com/kongxiangyiren/ld-api
  * 受控模式双端口说明
    https://dshpluginhub.ai/zh/plugins/dsh-localdream

两个端口：
  :8808 控制口  GET /info /models /status, POST /select /stop
  :8081 生成口  POST /generate (SSE), POST /tokenize

注意：:8081 只有在 App 内选定模型后才会开始监听；未选模型时连接会被拒绝。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger("LocaldreamComfy")

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(PLUGIN_DIR, "settings.json")
MODELS_CACHE_FILE = os.path.join(PLUGIN_DIR, "models_cache.json")

AUTO_MODEL = "自动（使用设备已载入模型）"

# 节点下拉框显示的采样器名称，采用 ComfyUI / K 采样器的通用命名。
# Local Dream 后端 /generate 只认另一套短名，因此提交前由 to_backend_scheduler() 映射。
SCHEDULERS: List[str] = [
    "dpmpp_2m", "dpmpp_2m_karras", "dpmpp_sde", "dpmpp_sde_karras",
    "euler_ancestral", "euler_ancestral_karras", "euler", "euler_karras", "lcm",
]

SCHEDULER_TO_BACKEND: Dict[str, str] = {
    # 节点显示名 -> /generate 的 scheduler 值
    "dpmpp_2m": "dpm",
    "dpmpp_2m_karras": "dpm_karras",
    "dpmpp_sde": "dpm_sde",
    "dpmpp_sde_karras": "dpm_sde_karras",
    "euler_ancestral": "euler_a",
    "euler_ancestral_karras": "euler_a_karras",
    "euler": "euler",
    "euler_karras": "euler_karras",
    "lcm": "lcm",
    # 兼容旧工作流里保存的后端原生短名
    "dpm": "dpm", "dpm_karras": "dpm_karras",
    "dpm_sde": "dpm_sde", "dpm_sde_karras": "dpm_sde_karras",
    "euler_a": "euler_a", "eulera": "euler_a",
    "euler_a_karras": "euler_a_karras",
}

# 后端实际接受的 scheduler 合法值（官方文档：未知值静默回退 dpm）
BACKEND_SCHEDULERS: List[str] = [
    "dpm", "dpm_karras", "dpm_sde", "dpm_sde_karras",
    "euler_a", "eulera", "euler_a_karras", "euler", "euler_karras", "lcm",
]


def to_backend_scheduler(name: Any) -> str:
    """把节点上的采样器名称转换成 /generate 的 scheduler 值。"""
    key = str(name or "dpmpp_2m")
    return SCHEDULER_TO_BACKEND.get(key, "dpm")

ASPECT_RATIOS: List[str] = [
    "auto", "1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3",
]

DEFAULTS: Dict[str, Any] = {
    # 连接
    "host": "127.0.0.1",          # 手机局域网 IP / 主机名；也可用 LOCALDREAM_HOST 环境变量
    "control_port": 8808,         # 受控模式控制口
    "gen_port": 8081,             # 受控模式生成口
    "control_host": "",           # 可选：单独覆盖控制口地址
    "gen_host": "",               # 可选：单独覆盖生成口地址
    "connect_timeout": 3.0,       # 控制口短请求超时（秒）
    "timeout": 600.0,             # 生成请求超时（秒）
    "model_load_timeout": 180.0,  # 等待模型载入完成的超时（秒）
    "models_ttl": 20.0,           # 模型列表缓存有效期（秒）
    "disable_proxy": True,        # 绕过系统 HTTP 代理（局域网直连必需）
    # 生成
    "auto_select_model": True,    # 需要时自动 POST /select 载入模型
    "auto_match_resolution": True,  # 自动向模型 resolutions 靠拢，避免尺寸不匹配
    "default_model": AUTO_MODEL,
    "default_negative_prompt": "",
    "default_width": 512,         # 设置面板「载入模型」时使用的分辨率
    "default_height": 512,
    "show_diffusion_process": False,  # 需要手机端同时开启「显示生成过程」
    "preview_stride": 1,
    "preview_in_comfy": True,     # 把中间帧推送到 ComfyUI 原生预览
}

_SETTINGS_LOCK = threading.RLock()
_GEN_LOCK = threading.Lock()   # 设备同一时刻只允许一个生成任务

_settings_cache: Optional[Dict[str, Any]] = None
_settings_mtime: float = 0.0
_models_cache: Optional[List[Dict[str, Any]]] = None
_models_cache_ts: float = 0.0
_models_cache_key: str = ""


class LocalDreamError(RuntimeError):
    """带中文排障提示的受控模式调用错误。"""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return max(low, min(high, v))


def _normalize_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in data:
            out[key] = data[key]
    out["host"] = normalize_host(out["host"])
    out["control_host"] = normalize_host(out["control_host"]) if out["control_host"] else ""
    out["gen_host"] = normalize_host(out["gen_host"]) if out["gen_host"] else ""
    for k in ("control_port", "gen_port"):
        out[k] = int(_clamp(out[k], 1, 65535, DEFAULTS[k]))
    out["connect_timeout"] = _clamp(out["connect_timeout"], 0.5, 60.0, 3.0)
    out["timeout"] = _clamp(out["timeout"], 10.0, 3600.0, 600.0)
    out["model_load_timeout"] = _clamp(out["model_load_timeout"], 5.0, 900.0, 180.0)
    out["models_ttl"] = _clamp(out["models_ttl"], 0.0, 600.0, 20.0)
    out["preview_stride"] = int(_clamp(out["preview_stride"], 1, 20, 1))
    for k in ("auto_select_model", "auto_match_resolution", "disable_proxy",
              "show_diffusion_process", "preview_in_comfy"):
        out[k] = bool(out[k])
    out["default_width"] = int(_round8(_clamp(out["default_width"], 64, 2048, 512)))
    out["default_height"] = int(_round8(_clamp(out["default_height"], 64, 2048, 512)))
    out["default_model"] = str(out["default_model"] or AUTO_MODEL)
    out["default_negative_prompt"] = str(out["default_negative_prompt"] or "")
    return out


def normalize_host(value: Any, fallback: str = "127.0.0.1") -> str:
    """接受 192.168.1.7 / http://192.168.1.7 / 192.168.1.7:8808 等写法。"""
    s = str(value or "").strip()
    if not s:
        return fallback
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()
    if s.startswith("["):        # 已经是 [IPv6]
        return s
    if s.count(":") == 1:        # 去掉误填的端口
        s = s.rsplit(":", 1)[0]
    return s or fallback


def load_settings(force: bool = False) -> Dict[str, Any]:
    """读取 settings.json（带 mtime 缓存，跨进程修改立即可见）。"""
    global _settings_cache, _settings_mtime
    with _SETTINGS_LOCK:
        try:
            mtime = os.path.getmtime(SETTINGS_FILE)
        except OSError:
            mtime = 0.0
        if not force and _settings_cache is not None and mtime == _settings_mtime:
            return dict(_settings_cache)

        data = dict(DEFAULTS)
        env_host = os.environ.get("LOCALDREAM_HOST", "").strip()
        if env_host:
            data["host"] = env_host
        if os.path.isfile(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                    user = json.load(fh)
                if isinstance(user, dict):
                    data.update({k: v for k, v in user.items() if k in DEFAULTS})
            except Exception as exc:      # 配置损坏不应导致节点无法加载
                LOG.warning("[Localdream] 读取设置失败，使用默认值: %s", exc)
        _settings_cache = _normalize_settings(data)
        _settings_mtime = mtime
        return dict(_settings_cache)


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """合并保存配置；未知字段忽略。"""
    global _settings_cache, _settings_mtime
    with _SETTINGS_LOCK:
        merged = load_settings()
        for key, value in (patch or {}).items():
            if key in DEFAULTS:
                merged[key] = value
        merged = _normalize_settings(merged)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
        _settings_cache = merged
        _settings_mtime = os.path.getmtime(SETTINGS_FILE)
        invalidate_models_cache()
        return dict(merged)


def reset_settings() -> Dict[str, Any]:
    return save_settings(dict(DEFAULTS))


def invalidate_models_cache() -> None:
    global _models_cache_ts, _models_cache_key
    with _SETTINGS_LOCK:
        _models_cache_ts = 0.0
        _models_cache_key = ""


# --------------------------------------------------------------------------
# HTTP（禁用系统代理，局域网直连）
# --------------------------------------------------------------------------

def base_url(settings: Dict[str, Any], kind: str = "control") -> str:
    override = str(settings.get(kind + "_host") or "").strip()
    host = normalize_host(override or settings.get("host"))
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    port = int(settings["control_port" if kind == "control" else "gen_port"])
    return "http://{}:{}".format(host, port)


def _opener(settings: Dict[str, Any]):
    if settings.get("disable_proxy", True):
        # urllib 默认读取 http_proxy 环境变量，会把局域网请求塞进本机代理，
        # 导致连不上手机或收到伪造的 502。受控模式必须直连。
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _url(settings: Dict[str, Any], kind: str, path: str) -> str:
    return base_url(settings, kind) + (path if path.startswith("/") else "/" + path)


def _friendly_http_error(exc: Exception, settings: Dict[str, Any], kind: str) -> LocalDreamError:
    url = base_url(settings, kind)
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        try:
            payload = json.loads(detail)
            err = payload.get("error")
            if isinstance(err, dict) and err.get("message"):
                detail = str(err["message"])
            elif isinstance(err, str):
                detail = err
        except Exception:
            pass
        # 502/503/504 常见于模型刚载入、生成口尚未开始监听的空窗期
        return LocalDreamError("受控模式 {} 返回 HTTP {}：{}".format(url, exc.code, detail),
                               retryable=exc.code in (502, 503, 504))
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        hint = ""
        if kind == "gen":
            hint = "（生成口仅在 App 内选定模型后才开始监听；未选模型时连接会被拒绝）"
        return LocalDreamError(
            "无法连接受控模式 {}：{}{}。请确认手机与电脑同网段、已在 App 内「⋮ → 设备互联 → 受控模式 → 进入受控模式」。".format(
                url, reason, hint), retryable=True)
    if isinstance(exc, (TimeoutError, OSError)):
        return LocalDreamError("受控模式 {} 请求超时或连接中断：{}".format(url, exc), retryable=True)
    return LocalDreamError("受控模式 {} 调用失败：{}".format(url, exc))


def request_json(settings: Dict[str, Any], kind: str, path: str,
                 payload: Optional[Dict[str, Any]] = None,
                 timeout: Optional[float] = None) -> Any:
    url = _url(settings, kind, path)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    tmo = timeout if timeout is not None else float(settings["connect_timeout"])
    try:
        with _opener(settings).open(req, timeout=tmo) as resp:
            raw = resp.read()
    except Exception as exc:
        raise _friendly_http_error(exc, settings, kind)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"raw": raw.decode("utf-8", "replace")[:500]}


def get_info(settings): return request_json(settings, "control", "/info")


def get_status(settings): return request_json(settings, "control", "/status")


def get_models_raw(settings, timeout=None):
    data = request_json(settings, "control", "/models", timeout=timeout)
    models = data.get("models", []) if isinstance(data, dict) else []
    return {"use_img2img": bool(data.get("use_img2img")) if isinstance(data, dict) else False,
            "upscalers": data.get("upscalers", []) if isinstance(data, dict) else [],
            "models": [m for m in models if isinstance(m, dict) and m.get("id")]}


def select_model(settings, model_id: str, width: int, height: int):
    return request_json(settings, "control", "/select",
                        {"model_id": model_id, "width": int(width), "height": int(height)},
                        timeout=max(float(settings["connect_timeout"]), 20.0))


def stop_model(settings, model_id: str):
    return request_json(settings, "control", "/stop", {"model_id": model_id})


# --------------------------------------------------------------------------
# 模型列表 / 模型选择
# --------------------------------------------------------------------------

def _persist_models(models: List[Dict[str, Any]]) -> None:
    try:
        with open(MODELS_CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"models": models}, fh, ensure_ascii=False)
    except Exception as exc:
        LOG.debug("[Localdream] 模型缓存写入失败: %s", exc)


def _load_persisted_models() -> List[Dict[str, Any]]:
    try:
        with open(MODELS_CACHE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        models = data.get("models", []) if isinstance(data, dict) else []
        return [m for m in models if isinstance(m, dict) and m.get("id")]
    except Exception:
        return []


def fetch_models(settings: Optional[Dict[str, Any]] = None,
                 force: bool = False,
                 timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """带 TTL 的设备模型列表；设备离线时回退到磁盘缓存。"""
    global _models_cache, _models_cache_ts, _models_cache_key
    settings = settings or load_settings()
    key = base_url(settings, "control")
    with _SETTINGS_LOCK:
        fresh = (_models_cache is not None and _models_cache_key == key
                 and (time.monotonic() - _models_cache_ts) < float(settings["models_ttl"]))
        if fresh and not force:
            return list(_models_cache)
        try:
            models = get_models_raw(settings, timeout=timeout)["models"]
            _models_cache, _models_cache_ts, _models_cache_key = models, time.monotonic(), key
            _persist_models(models)
            return list(models)
        except LocalDreamError as exc:
            LOG.debug("[Localdream] 获取模型列表失败: %s", exc)
            if _models_cache and _models_cache_key == key:
                return list(_models_cache)
            persisted = _load_persisted_models()
            if persisted:
                _models_cache, _models_cache_ts, _models_cache_key = persisted, 0.0, key
                return list(persisted)
            return []


def model_choices(settings: Optional[Dict[str, Any]] = None,
                  force: bool = False,
                  timeout: float = 1.5) -> List[str]:
    """节点 model 下拉框的候选值。始终包含 AUTO 与历史已用模型，避免离线时校验失败。

    INPUT_TYPES 会在 ComfyUI 启动时被频繁调用，这里用较短超时，设备离线时
    立刻回退到磁盘缓存，不拖慢启动。
    """
    settings = settings or load_settings()
    ids: List[str] = [str(m["id"]) for m in fetch_models(settings, force=force, timeout=timeout)]
    for extra in (settings.get("default_model"),):
        if extra and str(extra) != AUTO_MODEL and str(extra) not in ids:
            ids.append(str(extra))
    for m in _load_persisted_models():
        mid = str(m.get("id", ""))
        if mid and mid not in ids:
            ids.append(mid)
    return [AUTO_MODEL] + ids


def _round8(value: int) -> int:
    return max(64, min(2048, int(round(float(value) / 8.0)) * 8))


def aspect_ratio_string(width: int, height: int, override: str = "auto") -> str:
    if override and override not in ("auto", "AUTO", "自动"):
        return override
    g = math.gcd(max(1, int(width)), max(1, int(height))) or 1
    return "{}:{}".format(int(width) // g, int(height) // g)


def sdxl_dimensions(ratio: str) -> Tuple[int, int]:
    """SDXL NPU：长边固定 1024，短边按比例计算并向上取整到 8 的倍数。"""
    try:
        a, b = (int(x) for x in str(ratio).split(":", 1))
    except Exception:
        return 1024, 1024
    if a <= 0 or b <= 0:
        return 1024, 1024
    if a >= b:
        return 1024, int(math.ceil((1024.0 * b / a) / 8.0) * 8)
    return int(math.ceil((1024.0 * a / b) / 8.0) * 8), 1024


def choose_dimensions(model: Dict[str, Any], width: int, height: int,
                      settings: Dict[str, Any], aspect_override: str = "auto") -> Dict[str, Any]:
    """按模型族决定真正生效的尺寸与请求字段。

    SDXL NPU    : width/height 被忽略，实际尺寸由 aspect_ratio 决定
    SD1.5 NPU   : 尺寸必须与 /select 时选定的分辨率一致
    SD1.5 CPU   : 任意 8 的倍数
    DiT / Anima : width/height
    """
    w, h = _round8(width), _round8(height)
    info: Dict[str, Any] = {"is_sdxl": bool(model.get("is_sdxl")),
                            "run_on_cpu": bool(model.get("run_on_cpu")),
                            "aspect_ratio": "auto", "width": w, "height": h}
    if info["is_sdxl"]:
        ratio = aspect_ratio_string(w, h, aspect_override)
        dw, dh = sdxl_dimensions(ratio)
        info.update({"aspect_ratio": ratio, "width": dw, "height": dh,
                     "size_mode": "aspect_ratio"})
        return info

    resolutions = model.get("resolutions") or []
    if resolutions and settings.get("auto_match_resolution", True) and not info["run_on_cpu"]:
        best = None
        for res in resolutions:
            try:
                rw, rh = int(res[0]), int(res[1])
            except Exception:
                continue
            score = abs(rw - w) + abs(rh - h)
            if best is None or score < best[0]:
                best = (score, rw, rh)
        if best:
            info.update({"width": best[1], "height": best[2], "size_mode": "model_resolution"})
            return info
    info["size_mode"] = "width_height"
    return info


def resolve_model(settings: Dict[str, Any], model_value: str,
                  width: int, height: int, aspect_override: str = "auto") -> Dict[str, Any]:
    """把节点上的模型下拉值解析成设备模型 + 目标尺寸。"""
    models = fetch_models(settings)
    chosen = model_value
    if not chosen or chosen == AUTO_MODEL:
        chosen = settings.get("default_model") or AUTO_MODEL
    if chosen == AUTO_MODEL:
        try:
            status = get_status(settings)
            if status.get("state") == "running" and status.get("serving_model_id"):
                chosen = str(status["serving_model_id"])
        except LocalDreamError:
            pass
    if not chosen or chosen == AUTO_MODEL:
        chosen = str(models[0]["id"]) if models else ""
    model = next((m for m in models if str(m.get("id")) == str(chosen)), None)
    if model is None:
        model = {"id": str(chosen), "name": str(chosen), "resolutions": [],
                 "is_sdxl": False, "run_on_cpu": False, "generation_size": 0,
                 "defaults": {}}
    dims = choose_dimensions(model, width, height, settings, aspect_override)
    return {"model": model, "model_id": str(chosen), **dims}


def ensure_model_ready(settings: Dict[str, Any], resolved: Dict[str, Any],
                       cancel_check: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """必要时载入模型并轮询 /status 直到 running。"""
    mid, w, h = resolved["model_id"], resolved["width"], resolved["height"]
    if not mid:
        raise LocalDreamError("设备上没有可用模型。请先在 App 内下载并选择一个模型，或在节点设置里点「测试连接 / 刷新模型」。")

    status = get_status(settings)
    same = (status.get("state") == "running" and str(status.get("serving_model_id") or "") == mid)
    if same and status.get("width") and int(status["width"]) != w:
        same = False
    if same and status.get("height") and int(status["height"]) != h:
        same = False
    if same:
        return status

    if not settings.get("auto_select_model", True):
        raise LocalDreamError("设备当前载入的模型与节点所选不一致，但设置里关闭了「自动载入模型」。请手动切换或开启该选项。")

    LOG.info("[Localdream] 载入模型 %s @ %dx%d ...", mid, w, h)
    reply = select_model(settings, mid, w, h)
    if isinstance(reply, dict) and reply.get("ok") is False:
        raise LocalDreamError("模型选择失败：{}".format(reply))

    deadline = time.monotonic() + float(settings["model_load_timeout"])
    while True:
        if cancel_check:
            cancel_check()
        status = get_status(settings)
        state = status.get("state")
        if state == "error":
            raise LocalDreamError("设备载入模型失败：{}".format(status.get("message") or status.get("error_model_id") or "未知错误"))
        if state == "running" and str(status.get("serving_model_id") or "") == mid:
            # state 变 running 后 8081 仍有数百毫秒空窗期
            time.sleep(0.35)
            return status
        if time.monotonic() > deadline:
            raise LocalDreamError("等待模型载入超时（{} 秒）。请检查手机是否熄屏、App 是否被系统回收。".format(int(settings["model_load_timeout"])))
        time.sleep(0.5)


# --------------------------------------------------------------------------
# /generate —— SSE
# --------------------------------------------------------------------------

def _error_from_body(body: str, status: int) -> str:
    try:
        payload = json.loads(body)
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
    except Exception:
        pass
    return "HTTP {}: {}".format(status, body[:300])


def _stream_generate(settings: Dict[str, Any], payload: Dict[str, Any],
                     on_event: Optional[Callable[[Dict[str, Any]], None]],
                     cancel_check: Optional[Callable[[], None]]) -> Dict[str, Any]:
    url = _url(settings, "gen", "/generate")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        resp = _opener(settings).open(req, timeout=min(30.0, float(settings["timeout"])))
    except Exception as exc:
        raise _friendly_http_error(exc, settings, "gen")

    # 注意：不要给 HTTPResponse 的 socket 设置很短的读超时。socket 超时会让
    # http.client 的内部缓冲进入不可恢复状态（cannot read from timed out object），
    # 大图 / 慢网络反而会失败。这里保持长超时，用看门狗线程处理中断。
    try:
        fp = getattr(resp, "fp", None)
        raw_fp = getattr(fp, "raw", None)
        response_sock = getattr(raw_fp, "_sock", None) if raw_fp is not None else None
    except Exception:
        response_sock = None

    deadline = time.monotonic() + float(settings["timeout"])
    interrupted_by_watchdog = {"v": False}
    stop_watchdog = threading.Event()

    def watchdog():
        # 只读取中断标志（不消费），命中时强制断开 socket，让阻塞读尽快退出；
        # 执行线程随后用 cancel_check() 抛出 ComfyUI 的标准中断异常。
        while not stop_watchdog.wait(0.25):
            try:
                import comfy.model_management as mm
                check = getattr(mm, "processing_interrupted", None)
                interrupted = bool(check()) if check else False
            except Exception:
                interrupted = False
            if not interrupted:
                continue
            interrupted_by_watchdog["v"] = True
            try:
                if response_sock is not None:
                    response_sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                resp.close()
            except Exception:
                pass
            return

    threading.Thread(target=watchdog, daemon=True,
                     name="LocaldreamInterruptWatchdog").start()
    try:
        ctype = (resp.headers.get("Content-Type") or "")
        if "text/event-stream" not in ctype.lower():
            raw = resp.read().decode("utf-8", "replace")
            raise LocalDreamError("生成接口未返回 SSE：{}".format(
                _error_from_body(raw, getattr(resp, "status", 0))))

        data_lines: List[str] = []

        def dispatch(text: str):
            """返回一个 complete 事件，或 None（progress / 忽略）。"""
            text = (text or "").strip()
            if not text or text == "[DONE]":
                return None
            try:
                event = json.loads(text)
            except Exception:
                LOG.debug("[Localdream] 忽略无法解析的 SSE 数据: %s", text[:120])
                return None
            if cancel_check:
                cancel_check()
            kind = event.get("type")
            if kind in ("error", "request_error"):
                raise LocalDreamError("设备生成失败：{}".format(event.get("message", event)))
            if kind == "complete":
                return event
            if kind == "progress" and on_event:
                on_event(event)
            return None

        def handle_line(line: str):
            """处理一行 SSE；返回 complete 事件或 None。"""
            nonlocal data_lines
            line = line.rstrip("\r")
            if line == "":
                # 标准 SSE：空行结束一个事件
                result = dispatch("\n".join(data_lines))
                data_lines = []
                return result
            if line.startswith(":"):        # SSE 注释 / keep-alive
                return None
            if not line.startswith("data:"):
                return None
            content = line[5:].lstrip()
            if not content:
                return None
            # Local Dream 的 native 后端与 ld-api 的解析方式一致：
            # 每个事件就是一行完整的 data: JSON，可能不带空行分隔符。
            # 单行能独立解析就立刻派发，保证进度条实时推进；
            # 否则按标准 SSE 累积多行，等空行再派发。
            try:
                json.loads(content)
            except Exception:
                data_lines.append(content)
                return None
            result = dispatch(content)
            data_lines = []
            return result

        # 分块读取：socket 超时只代表「暂时没数据」，保留 buffer 继续读，
        # 不会像逐行 readline 那样丢掉半行；每次醒来都检查中断与总超时。
        buffer = ""
        while True:
            if cancel_check:
                cancel_check()
            if time.monotonic() > deadline:
                raise LocalDreamError("生成超时（{} 秒）。请检查手机是否熄屏、App 是否被系统回收。".format(
                    int(settings["timeout"])))
            try:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            except (socket.timeout, TimeoutError, OSError) as exc:
                if interrupted_by_watchdog["v"] and cancel_check:
                    cancel_check()
                raise _friendly_http_error(exc, settings, "gen")
            if not chunk:
                break
            buffer += chunk.decode("utf-8", "replace")
            lines = buffer.split("\n")
            buffer = lines.pop() or ""
            for line in lines:
                result = handle_line(line)
                if result is not None:
                    return result
        if buffer.strip():
            result = handle_line(buffer)
            if result is not None:
                return result
        result = dispatch("\n".join(data_lines))
        if result is not None:
            return result
    except LocalDreamError:
        raise
    except Exception as exc:
        raise _friendly_http_error(exc, settings, "gen")
    finally:
        stop_watchdog.set()
        try:
            resp.close()
        except Exception:
            pass
    # 流提前结束（设备断开 / 用户中断）时，先让执行线程抛出标准中断异常
    if cancel_check:
        cancel_check()
    raise LocalDreamError("生成流在没有 complete 事件的情况下结束。请确认 App 仍在受控模式且模型已载入。")


def generate(settings: Dict[str, Any], payload: Dict[str, Any],
             on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
             cancel_check: Optional[Callable[[], None]] = None,
             lock: bool = True) -> Dict[str, Any]:
    """执行一次生成。设备不支持并发，默认加全局锁。"""
    def run():
        last: Optional[LocalDreamError] = None
        for attempt in range(3):
            try:
                return _stream_generate(settings, payload, on_event, cancel_check)
            except LocalDreamError as exc:
                last = exc
                if exc.retryable and attempt < 2:
                    LOG.info("[Localdream] 生成口尚未就绪，%.1fs 后重试 (%d/3)", 0.6 * (attempt + 1), attempt + 1)
                    time.sleep(0.6 * (attempt + 1))
                    continue
                raise
        raise last or LocalDreamError("生成失败")

    if lock:
        with _GEN_LOCK:
            return run()
    return run()


# --------------------------------------------------------------------------
# 图像：原始 RGB <-> ComfyUI IMAGE / PNG
# --------------------------------------------------------------------------

def _is_encoded_image(raw: bytes) -> bool:
    return raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff") or raw[:4] == b"RIFF"


def _infer_raw_size(length: int, width: int, height: int) -> Tuple[int, int]:
    """progress 事件不带尺寸，按字节数反推 w/h（先比对请求尺寸，再按宽高比找因数）。"""
    if length % 3:
        raise LocalDreamError("设备返回的原始像素数据长度异常（{} 字节），无法还原图像。".format(length))
    pixels = length // 3
    if pixels == width * height:
        return width, height
    ratio = float(width) / max(1, height)
    best, best_score = (width, height), float("inf")
    limit = int(math.isqrt(pixels))
    for d in range(1, limit + 1):
        if pixels % d:
            continue
        for cand in ((d, pixels // d), (pixels // d, d)):
            try:
                score = abs(math.log(float(cand[0]) / cand[1] / ratio))
            except (ValueError, ZeroDivisionError):
                continue
            if score < best_score:
                best, best_score = cand, score
    return int(best[0]), int(best[1])


def decode_event_image(event: Dict[str, Any], fallback_w: int, fallback_h: int) -> Tuple[bytes, int, int]:
    """把 complete / progress 的 image 字段统一还原成 RGB24 字节 + 宽高。"""
    b64 = event.get("image") or ""
    if not b64:
        raise LocalDreamError("设备返回的事件中没有图像数据。")
    try:
        raw = base64.b64decode(b64)
    except Exception as exc:
        raise LocalDreamError("设备返回的图像 Base64 无法解码：{}".format(exc))
    if not raw:
        raise LocalDreamError("设备返回的图像为空。")

    width = int(event.get("width") or fallback_w)
    height = int(event.get("height") or fallback_h)
    channels = int(event.get("channels") or 3)

    if _is_encoded_image(raw):     # 兼容少数返回 PNG/JPEG 的后端版本
        from PIL import Image
        import numpy as np
        with Image.open(io.BytesIO(raw)) as img:
            rgb = img.convert("RGB")
            width, height = rgb.size
            return np.asarray(rgb, dtype=np.uint8).tobytes(), width, height

    if channels == 4 and len(raw) == width * height * 4:
        import numpy as np
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
        return arr[:, :, :3].tobytes(), width, height

    if len(raw) != width * height * 3:
        width, height = _infer_raw_size(len(raw), width, height)
    if len(raw) != width * height * 3:
        raise LocalDreamError(
            "设备返回的原始像素与声明尺寸不匹配：{} 字节 vs {}x{}x3。".format(len(raw), width, height))
    return raw, width, height


def raw_rgb_to_image_tensor(raw: bytes, width: int, height: int):
    """RGB24 -> ComfyUI IMAGE: torch.Tensor [1, H, W, 3], float32, 0~1。"""
    import numpy as np
    import torch
    arr = np.frombuffer(raw, dtype=np.uint8, count=width * height * 3)
    arr = arr.reshape((height, width, 3)).astype(np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


def concat_image_tensors(tensors: List[Any]):
    """把多次生成的 [1,H,W,3] 拼成 [B,H,W,3]。"""
    import torch
    if len(tensors) == 1:
        return tensors[0]
    return torch.cat(list(tensors), dim=0)


def raw_rgb_to_png_b64(raw: bytes, width: int, height: int) -> str:
    """RGB24 -> Base64 PNG（设置面板的「测试生成」结果用，不需要 torch）。"""
    import numpy as np
    from PIL import Image
    arr = np.frombuffer(raw, dtype=np.uint8, count=width * height * 3).reshape((height, width, 3))
    img = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _first_image_numpy(image):
    """从 ComfyUI IMAGE (torch.Tensor) 取出第一张，返回 HWC uint8 ndarray。"""
    import numpy as np
    first = image[0] if getattr(image, "shape", [0]) and len(getattr(image, "shape", ())) >= 4 else image
    try:
        arr = first.detach().cpu().numpy()
    except AttributeError:
        arr = np.asarray(first)
    arr = np.clip(np.asarray(arr), 0.0, 1.0)
    if arr.dtype != np.uint8:
        arr = (arr * 255.0).astype(np.uint8)
    if arr.ndim == 3 and arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)
    if arr.ndim == 3 and arr.shape[-1] >= 3:
        arr = arr[:, :, :3]
    return np.ascontiguousarray(arr)


def image_tensor_to_png_b64(image, width: int, height: int) -> str:
    """ComfyUI IMAGE -> 目标尺寸 -> Base64 PNG（/generate 的 image 字段要求编码图片）。"""
    import numpy as np
    from PIL import Image
    arr = _first_image_numpy(image)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise LocalDreamError("输入的 IMAGE 张量形状异常，无法编码为 PNG。")
    img = Image.fromarray(arr[:, :, :3], "RGB")
    if img.size != (width, height):
        img = img.resize((width, height), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def mask_tensor_to_png_b64(mask, width: int, height: int) -> str:
    """ComfyUI MASK [B,H,W] 0~1 -> Base64 PNG（白色为需要重绘的区域）。"""
    import numpy as np
    from PIL import Image
    try:
        arr = mask[0].detach().cpu().numpy()
    except AttributeError:
        arr = np.asarray(mask[0] if getattr(mask, "shape", None) else mask)
    arr = np.clip(np.asarray(arr), 0.0, 1.0)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    img = Image.fromarray((arr * 255.0).astype(np.uint8), "L")
    if img.size != (width, height):
        img = img.resize((width, height), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def make_comfy_preview(event: Dict[str, Any], width: int, height: int):
    """把中间帧转换成 ComfyUI ProgressBar 接受的 ("PNG", PIL.Image, max_size) 元组。"""
    try:
        from PIL import Image
        import numpy as np
        raw, w, h = decode_event_image(event, width, height)
        arr = np.frombuffer(raw, dtype=np.uint8, count=w * h * 3).reshape((h, w, 3))
        img = Image.fromarray(arr, "RGB")
        if max(img.size) > 512:
            scale = 512.0 / max(img.size)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
        return ("PNG", img, 512)
    except Exception as exc:
        LOG.debug("[Localdream] 中间帧预览转换失败: %s", exc)
        return None


# --------------------------------------------------------------------------
# 诊断（设置面板「测试连接」）
# --------------------------------------------------------------------------

def tcp_probe(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def diagnose(settings: Dict[str, Any], include_models: bool = True) -> Dict[str, Any]:
    ctl_host = normalize_host(settings.get("control_host") or settings["host"])
    gen_host = normalize_host(settings.get("gen_host") or settings["host"])
    ctl_ok = tcp_probe(ctl_host, int(settings["control_port"]))
    gen_ok = tcp_probe(gen_host, int(settings["gen_port"]))
    info, status, models = None, None, []
    error = None
    if ctl_ok:
        try:
            info = get_info(settings)
            status = get_status(settings)
            if include_models:
                models = fetch_models(settings, force=True)
        except LocalDreamError as exc:
            error = str(exc)
    if not ctl_ok:
        hint = "控制口 {} 不可达：确认已在 App 内「⋮ → 设备互联 → 受控模式 → 进入受控模式」，手机与电脑处于同一网段。".format(settings["control_port"])
    elif not gen_ok:
        hint = "控制口正常，但生成口 {} 未监听 —— 这是正常状态：受控模式只在选定模型后才启动 native 后端。在设置里选一个模型并点「载入模型」即可。".format(settings["gen_port"])
    else:
        hint = "两个端口均就绪，可以生成。"
    return {
        "ok": bool(ctl_ok and gen_ok),
        "control": {"host": ctl_host, "port": int(settings["control_port"]), "tcp": ctl_ok,
                    "url": base_url(settings, "control")},
        "generation": {"host": gen_host, "port": int(settings["gen_port"]), "tcp": gen_ok,
                       "url": base_url(settings, "gen")},
        "info": info, "status": status,
        "models": [{"id": m.get("id"), "name": m.get("name") or m.get("id"),
                    "is_sdxl": bool(m.get("is_sdxl")), "run_on_cpu": bool(m.get("run_on_cpu")),
                    "resolutions": m.get("resolutions") or [],
                    "defaults": m.get("defaults") or {}} for m in models],
        "error": error, "hint": hint,
    }
