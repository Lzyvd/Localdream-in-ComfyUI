# -*- coding: utf-8 -*-
"""
ComfyUI 内置 HTTP 服务上的插件路由（无需任何额外后台程序 / 独立服务）

所有路由都挂在 PromptServer.instance.routes 上，由 ComfyUI 自己的 aiohttp
服务器提供；设置界面通过 api.fetchApi("/localdream/...") 同源调用。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List

try:
    from . import ld_client as ld
except ImportError:                       # 便于离线测试按文件路径导入
    import ld_client as ld

try:
    from aiohttp import web
except ImportError:                       # 仅在脱离 ComfyUI 环境时被使用
    web = None                            # type: ignore

try:
    from server import PromptServer
except ImportError:
    PromptServer = None                   # type: ignore

LOG = logging.getLogger("LocaldreamComfy")


def _ok(**kwargs):
    data = {"ok": True}
    data.update(kwargs)
    return _json(data)


def _fail(message: str, status: int = 200, **kwargs):
    data = {"ok": False, "error": {"message": message}}
    data.update(kwargs)
    return _json(data, status=status)


def _json(data: Any, status: int = 200):
    if web is None:
        return {"status": status, "body": data}
    return web.json_response(data, status=status)


async def _body(request) -> Dict[str, Any]:
    try:
        value = await request.json()
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# 路由处理函数（同步逻辑放进线程池，避免阻塞 ComfyUI 的事件循环）
# --------------------------------------------------------------------------

async def handle_get_settings(request):
    settings = await asyncio.to_thread(ld.load_settings, True)
    return _ok(settings=settings, defaults=ld.DEFAULTS,
               schedulers=ld.SCHEDULERS, aspect_ratios=ld.ASPECT_RATIOS,
               auto_model=ld.AUTO_MODEL)


async def handle_post_settings(request):
    patch = await _body(request)
    try:
        settings = await asyncio.to_thread(ld.save_settings, patch)
        return _ok(settings=settings)
    except Exception as exc:
        LOG.exception("[Localdream] 保存设置失败")
        return _fail("保存设置失败：{}".format(exc))


async def handle_reset_settings(request):
    settings = await asyncio.to_thread(ld.reset_settings)
    return _ok(settings=settings)


async def handle_get_models(request):
    """返回纯字符串数组；也可作为新版 ComfyUI 动态 COMBO 的 remote 数据源。"""
    try:
        choices = await asyncio.to_thread(ld.model_choices, None, False)
        return _json(choices)
    except Exception as exc:
        return _json([ld.AUTO_MODEL])


async def handle_refresh_models(request):
    settings = await asyncio.to_thread(ld.load_settings, True)
    await asyncio.to_thread(ld.invalidate_models_cache)
    try:
        models = await asyncio.to_thread(ld.fetch_models, settings, True)
        choices = await asyncio.to_thread(ld.model_choices, settings, False)
        return _ok(models=[_public_model(m) for m in models], choices=choices,
                   count=len(models))
    except Exception as exc:
        return _fail("刷新模型列表失败：{}".format(exc))


async def handle_device(request):
    settings = await asyncio.to_thread(ld.load_settings, True)
    try:
        report = await asyncio.to_thread(ld.diagnose, settings, True)
        return _ok(**report)
    except Exception as exc:
        return _fail("诊断失败：{}".format(exc))


async def handle_diagnose(request):
    settings = await asyncio.to_thread(ld.load_settings, True)
    try:
        report = await asyncio.to_thread(ld.diagnose, settings, True)
        return _report(report)
    except Exception as exc:
        return _fail("诊断失败：{}".format(exc))


def _report(report: Dict[str, Any]):
    return _ok(control=report["control"], generation=report["generation"],
               info=report["info"], status=report["status"],
               models=report["models"], error=report["error"],
               hint=report["hint"], reachable=report["ok"])


def _public_model(model: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": model.get("id"),
        "name": model.get("name") or model.get("id"),
        "description": model.get("description", ""),
        "is_sdxl": bool(model.get("is_sdxl")),
        "is_anima": bool(model.get("is_anima")),
        "dit_kind": model.get("dit_kind", ""),
        "run_on_cpu": bool(model.get("run_on_cpu")),
        "generation_size": model.get("generation_size", 0),
        "resolutions": model.get("resolutions") or [],
        "defaults": model.get("defaults") or {},
    }


async def handle_select_model(request):
    data = await _body(request)
    settings = await asyncio.to_thread(ld.load_settings, True)
    model_id = str(data.get("model_id") or ld.AUTO_MODEL)
    width = int(data.get("width") or settings["default_width"])
    height = int(data.get("height") or settings["default_height"])
    try:
        resolved = await asyncio.to_thread(ld.resolve_model, settings, model_id, width, height, "auto")
        status = await asyncio.to_thread(ld.ensure_model_ready, settings, resolved, None)
        return _ok(status=status, model_id=resolved["model_id"],
                   width=resolved["width"], height=resolved["height"])
    except Exception as exc:
        return _fail("载入模型失败：{}".format(exc))


async def handle_tokenize(request):
    data = await _body(request)
    settings = await asyncio.to_thread(ld.load_settings, True)
    try:
        result = await asyncio.to_thread(ld.request_json, settings, "gen", "/tokenize",
                                         {"prompt": str(data.get("prompt") or "")})
        return _ok(result=result)
    except Exception as exc:
        return _fail("token 计数失败：{}".format(exc))


async def handle_test_generate(request):
    """设置面板的「测试生成」：走与节点完全相同的链路，但只出一张小图。"""
    data = await _body(request)
    settings = await asyncio.to_thread(ld.load_settings, True)
    prompt = str(data.get("prompt") or "a cute cat, masterpiece")
    model_value = str(data.get("model") or ld.AUTO_MODEL)
    width = int(data.get("width") or 512)
    height = int(data.get("height") or 512)
    steps = max(1, min(20, int(data.get("steps") or 6)))
    started = time.monotonic()
    try:
        resolved = await asyncio.to_thread(ld.resolve_model, settings, model_value, width, height, "auto")
        await asyncio.to_thread(ld.ensure_model_ready, settings, resolved, None)
        payload = {
            "prompt": prompt,
            "negative_prompt": str(data.get("negative_prompt") or settings["default_negative_prompt"]),
            "steps": steps,
            "cfg": float(data.get("cfg") or 7.5),
            "scheduler": ld.to_backend_scheduler(data.get("scheduler") or "dpmpp_2m"),
            "width": resolved["width"],
            "height": resolved["height"],
        }
        if resolved["size_mode"] == "aspect_ratio":
            payload["aspect_ratio"] = resolved["aspect_ratio"]
        events = []

        def on_event(event):
            events.append({"step": event.get("step"), "total_steps": event.get("total_steps")})

        complete = await asyncio.to_thread(ld.generate, settings, payload, on_event, None, True)
        raw, w, h = await asyncio.to_thread(ld.decode_event_image, complete,
                                            resolved["width"], resolved["height"])
        png = await asyncio.to_thread(ld.raw_rgb_to_png_b64, raw, w, h)
        return _ok(image="data:image/png;base64," + png, seed=complete.get("seed"),
                   width=w, height=h, progress_events=events,
                   generation_time_ms=complete.get("generation_time_ms"),
                   first_step_time_ms=complete.get("first_step_time_ms"),
                   elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as exc:
        LOG.exception("[Localdream] 测试生成失败")
        return _fail("测试生成失败：{}".format(exc))


# --------------------------------------------------------------------------
# 注册
# --------------------------------------------------------------------------

ROUTES = (
    ("get",    "/localdream/settings",          handle_get_settings),
    ("post",   "/localdream/settings",          handle_post_settings),
    ("post",   "/localdream/settings/reset",    handle_reset_settings),
    ("get",    "/localdream/models",            handle_get_models),
    ("post",   "/localdream/models/refresh",    handle_refresh_models),
    ("get",    "/localdream/device",            handle_device),
    ("get",    "/localdream/diagnose",          handle_diagnose),
    ("post",   "/localdream/model/select",      handle_select_model),
    ("post",   "/localdream/tokenize",          handle_tokenize),
    ("post",   "/localdream/test-generate",     handle_test_generate),
)


def register_routes(routes=None) -> int:
    """把上述路径注册到 ComfyUI 的 RouteTableDef。返回成功注册数量。"""
    if routes is None:
        if PromptServer is None or getattr(PromptServer, "instance", None) is None:
            LOG.warning("[Localdream] 未找到 PromptServer，设置界面路由未注册（脱离 ComfyUI 运行时属正常）。")
            return 0
        routes = PromptServer.instance.routes
    # 不同版本的 ComfyUI 前端 api.fetchApi 可能给路径加 /api 前缀，
    # 因此两种写法都注册；前端另有 404 回退到同源 fetch 的兜底。
    paths: List[tuple] = []
    for method, path, handler in ROUTES:
        paths.append((method, path, handler))
        paths.append((method, "/api" + path, handler))
    count = 0
    for method, path, handler in paths:
        try:
            decorator = getattr(routes, method)(path)
            decorator(handler)
            count += 1
        except Exception as exc:
            LOG.warning("[Localdream] 注册路由 %s %s 失败: %s", method.upper(), path, exc)
    LOG.info("[Localdream] 已注册 %d 个设置/状态路由", count)
    return count


__all__ = ["register_routes", "ROUTES"]
