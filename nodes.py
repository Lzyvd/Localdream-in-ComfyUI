# -*- coding: utf-8 -*-
"""
Localdream-in-ComfyUI 的两个节点
================================

LocalDreamTextToImage   提示词 (STRING) -> 图像 (IMAGE)
LocalDreamImageToImage  图像 (IMAGE)   -> 图像 (IMAGE)

两个节点都提供类 K 采样器的基础采样参数：模型、步数、CFG、采样器 /
去噪方式、去噪强度、种子、尺寸，并把设备 SSE 返回的 progress 事件
逐帧映射到 comfy.utils.ProgressBar —— 也就是 ComfyUI 顶部那条原生进度条。

接口约束（见 README「接口依据」）：
  /generate 的 complete.image 是 base64 编码的原始 RGB24 字节，不是 PNG；
  seed 省略才是随机，-1 会被当作固定种子 4294967295，因此始终显式发送；
  NPU 模式下尺寸必须与 /select 时选定的分辨率一致；
  SDXL NPU 忽略 width/height，由 aspect_ratio 决定尺寸；
  同一时刻只允许一个生成任务（全局锁）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from . import ld_client as ld
except ImportError:
    import ld_client as ld

LOG = logging.getLogger("LocaldreamComfy")

CATEGORY = "LocalDream"
DEFAULT_PROMPT = "masterpiece, best quality, 1girl, cinematic lighting, detailed"


class _NullProgressBar:
    """脱离 ComfyUI 运行（例如离线测试）时的空实现。"""

    def __init__(self, total, node_id=None):
        self.total = total
        self.current = 0

    def update(self, value):
        self.current += value

    def update_absolute(self, value, total=None, preview=None):
        if total is not None:
            self.total = total
        self.current = min(value, self.total)


def _progress_bar(total: int, node_id=None):
    try:
        import comfy.utils
        try:
            return comfy.utils.ProgressBar(total, node_id=node_id)
        except TypeError:                    # 旧版 ProgressBar 没有 node_id 参数
            return comfy.utils.ProgressBar(total)
    except Exception:
        return _NullProgressBar(total, node_id)


def _cancel_check():
    """让 ComfyUI 的「取消/中断」按钮能在等待设备时立即生效。"""
    try:
        import comfy.model_management as mm
        mm.throw_exception_if_processing_interrupted()
    except ImportError:
        pass


def _common_input_types(include_image: bool, denoise_default: float) -> Dict[str, Any]:
    required: Dict[str, Any] = {
        "prompt": ("STRING", {
            "multiline": True, "default": DEFAULT_PROMPT,
            "placeholder": "正向提示词（/generate 必填，可留空字符串）",
            "tooltip": "提示词会原样发送给 Local Dream；CLIP 上限 77 token。",
        }),
        "model": (ld.model_choices(), {
            "default": ld.AUTO_MODEL,
            "tooltip": "设备 /models 返回的模型 id；「自动」沿用设备当前已载入的模型。",
        }),
        "steps": ("INT", {
            "default": 20, "min": 1, "max": 100, "step": 1,
            "tooltip": "采样步数，对应 /generate 的 steps。",
        }),
        "cfg": ("FLOAT", {
            "default": 7.5, "min": 0.0, "max": 30.0, "step": 0.5, "round": 0.1,
            "tooltip": "CFG 系数；NPU 上 cfg=1.0 会跳过无条件 UNet 计算（反向词失效但速度约翻倍）。",
        }),
        "scheduler": (list(ld.SCHEDULERS), {
            "default": "dpm",
            "tooltip": "采样 / 去噪方式。后端合法值：dpm, dpm_karras, dpm_sde, "
                       "dpm_sde_karras, euler_a, euler_a_karras, euler, euler_karras, lcm。",
        }),
        "seed": ("INT", {
            "default": 0, "min": 0, "max": 0xffffffff, "step": 1,
            "control_after_generate": True,
            "tooltip": "uint32 种子。始终显式发送；省略才是随机，-1 会被当成固定种子 4294967295。",
        }),
        "denoise": ("FLOAT", {
            "default": denoise_default, "min": 0.0, "max": 1.0, "step": 0.01, "round": 0.01,
            "tooltip": "去噪强度。图生图节点直接传给 denoise_strength；"
                       "文生图接口没有该字段，插件以等效步数 steps*denoise 近似实现。",
        }),
        "width": ("INT", {
            "default": 512, "min": 64, "max": 2048, "step": 8,
            "tooltip": "输出宽度（8 的倍数）。NPU 模式会自动向模型 resolutions 靠拢；SDXL 由宽高比换算。",
        }),
        "height": ("INT", {
            "default": 512, "min": 64, "max": 2048, "step": 8,
            "tooltip": "输出高度（8 的倍数）。",
        }),
    }
    if include_image:
        required["image"] = ("IMAGE", {
            "tooltip": "底图。会按目标尺寸缩放并编码成 PNG 后作为 /generate 的 image 字段上传。",
        })
    optional: Dict[str, Any] = {
        "negative_prompt": ("STRING", {
            "multiline": True, "default": "",
            "placeholder": "反向提示词；留空则使用设置里的默认反向词",
        }),
        "aspect_ratio": (list(ld.ASPECT_RATIOS), {
            "default": "auto",
            "tooltip": "仅 SDXL NPU 生效；auto 表示由 width/height 自动换算，长边固定 1024。",
        }),
        "use_opencl": ("BOOLEAN", {
            "default": False,
            "tooltip": "仅 SD1.5 CPU 模式有效，切换 MNN 后端到 GPU OpenCL。",
        }),
        "batch_count": ("INT", {
            "default": 1, "min": 1, "max": 8, "step": 1,
            "tooltip": "生成张数。设备不支持并发，插件会顺序执行并在同一条进度条上累计。",
        }),
    }
    if include_image:
        optional["mask"] = ("MASK", {
            "forceInput": True,
            "tooltip": "可选。连接后切换为局部重绘（inpaint），白色区域为需要重绘的区域。",
        })
    return {"required": required, "optional": optional,
            "hidden": {"unique_id": "UNIQUE_ID"}}


class _LocalDreamSamplerBase:
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("IMAGE",)
    CATEGORY = CATEGORY
    FUNCTION = "sample"
    OUTPUT_NODE = False

    @classmethod
    def VALIDATE_INPUTS(cls, model, scheduler):
        # 声明 model / scheduler 会让 ComfyUI 跳过这两个输入的默认 combo 校验，
        # 这样设备离线或换了手机后旧工作流仍可提交。其余输入照常走内置校验。
        # 后端对未知 scheduler 会自动回退到 dpm，这里保持宽容。
        return True

    # ------------------------------------------------------------------
    def _prepare(self, model, steps, cfg, scheduler, denoise, width, height,
                 aspect_ratio, negative_prompt, use_opencl, batch_count,
                 image=None, mask=None):
        settings = ld.load_settings()
        resolved = ld.resolve_model(settings, model, width, height, aspect_ratio)
        batch_size = 1
        if image is not None:
            try:
                batch_size = max(1, int(image.shape[0]))
            except Exception:
                batch_size = 1
        count = max(1, int(batch_count)) if batch_size <= 1 else batch_size
        effective_steps = max(1, int(round(steps * denoise))) if image is None else int(steps)
        total = max(1, effective_steps * count)
        base_payload: Dict[str, Any] = {
            "prompt": "",                     # 由调用方覆盖
            "negative_prompt": negative_prompt or settings.get("default_negative_prompt", ""),
            "steps": effective_steps,
            "cfg": float(cfg),
            "scheduler": scheduler if scheduler in ld.SCHEDULERS else "dpm",
            "width": int(resolved["width"]),
            "height": int(resolved["height"]),
        }
        if resolved["size_mode"] == "aspect_ratio":
            base_payload["aspect_ratio"] = resolved["aspect_ratio"]
        if use_opencl:
            base_payload["use_opencl"] = True
        if settings.get("show_diffusion_process"):
            base_payload["show_diffusion_process"] = True
            base_payload["show_diffusion_stride"] = int(settings.get("preview_stride", 1))
        return settings, resolved, count, effective_steps, total, base_payload

    def _run(self, *, prompt, model, steps, cfg, scheduler, seed, denoise,
             width, height, negative_prompt="", aspect_ratio="auto",
             use_opencl=False, batch_count=1, image=None, mask=None,
             unique_id=None):
        (settings, resolved, count, effective_steps, total,
         base_payload) = self._prepare(model, steps, cfg, scheduler, denoise,
                                       width, height, aspect_ratio,
                                       negative_prompt, use_opencl, batch_count,
                                       image, mask)
        target_w, target_h = int(resolved["width"]), int(resolved["height"])
        show_preview = bool(settings.get("show_diffusion_process") and settings.get("preview_in_comfy"))

        pbar = _progress_bar(total, unique_id)
        pbar.update_absolute(0, total)       # 立即点亮进度条，模型载入阶段显示 0%

        # 载入模型（必要时），期间同样响应 ComfyUI 的中断
        ld.ensure_model_ready(settings, resolved, _cancel_check)

        results: List[Any] = []
        image_b64: Optional[str] = None
        mask_b64: Optional[str] = None
        if image is not None:
            for i in range(count):
                index = i % max(1, int(getattr(image, "shape", [1])[0]))
                slice_image = image[index:index + 1]
                image_b64 = ld.image_tensor_to_png_b64(slice_image, target_w, target_h)
                if mask is not None:
                    slice_mask = mask[index:index + 1] if int(getattr(mask, "shape", [1])[0]) > index else mask
                    mask_b64 = ld.mask_tensor_to_png_b64(slice_mask, target_w, target_h)
                results.append(self._generate_one(settings, base_payload, pbar,
                                                  i, count, effective_steps, total,
                                                  target_w, target_h, show_preview,
                                                  prompt, seed, denoise,
                                                  image_b64, mask_b64))
        else:
            for i in range(count):
                results.append(self._generate_one(settings, base_payload, pbar,
                                                  i, count, effective_steps, total,
                                                  target_w, target_h, show_preview,
                                                  prompt, seed, denoise, None, None))
        return (ld.concat_image_tensors(results),)

    # ------------------------------------------------------------------
    def _generate_one(self, settings, base_payload, pbar,
                      index, count, effective_steps, total,
                      target_w, target_h, show_preview,
                      prompt, seed, denoise, image_b64, mask_b64):
        payload = dict(base_payload)
        payload["prompt"] = prompt if prompt is not None else ""
        payload["seed"] = (int(seed) + index) & 0xffffffff
        if image_b64 is not None:
            payload["image"] = image_b64
            payload["denoise_strength"] = float(denoise)
            if mask_b64 is not None:
                payload["mask"] = mask_b64

        base_steps = index * effective_steps
        progress_seen = {"n": 0}

        def on_event(event):
            step = int(event.get("step") or 0)
            value = min(total, base_steps + step)
            progress_seen["n"] += 1
            preview = None
            if show_preview and event.get("image"):
                preview = ld.make_comfy_preview(event, target_w, target_h)
            pbar.update_absolute(value, total, preview)
            _cancel_check()

        LOG.info("[Localdream] 生成 %d/%d | %dx%d | steps=%d cfg=%s scheduler=%s seed=%s",
                 index + 1, count, target_w, target_h, effective_steps,
                 payload["cfg"], payload["scheduler"], payload["seed"])
        complete = ld.generate(settings, payload, on_event, _cancel_check, lock=True)
        raw, w, h = ld.decode_event_image(complete, target_w, target_h)
        # 无论设备是否推送了每一步，都把这一批推进到 100%，保证进度条正确收尾
        pbar.update_absolute(min(total, base_steps + effective_steps), total)
        LOG.info("[Localdream] 完成 %d/%d | seed=%s | %dx%d | %s ms",
                 index + 1, count, complete.get("seed"), w, h,
                 complete.get("generation_time_ms"))
        return ld.raw_rgb_to_image_tensor(raw, w, h)


class LocalDreamTextToImage(_LocalDreamSamplerBase):
    """提示词 -> 图像（txt2img）。"""

    DESCRIPTION = ("通过手机端 Local Dream 受控模式文生图。"
                   "所有采样在手机 NPU/CPU 上完成，ComfyUI 只负责编排与显示。")

    @classmethod
    def INPUT_TYPES(cls):
        return _common_input_types(include_image=False, denoise_default=1.0)

    def sample(self, prompt, model, steps, cfg, scheduler, seed, denoise,
               width, height, negative_prompt="", aspect_ratio="auto",
               use_opencl=False, batch_count=1, unique_id=None, **kwargs):
        return self._run(prompt=prompt, model=model, steps=steps, cfg=cfg,
                         scheduler=scheduler, seed=seed, denoise=denoise,
                         width=width, height=height,
                         negative_prompt=negative_prompt,
                         aspect_ratio=aspect_ratio, use_opencl=use_opencl,
                         batch_count=batch_count, image=None, mask=None,
                         unique_id=unique_id)


class LocalDreamImageToImage(_LocalDreamSamplerBase):
    """图像 -> 图像（img2img / 可选 inpaint）。"""

    DESCRIPTION = ("接收 ComfyUI IMAGE，按目标尺寸缩放后作为 /generate 的 image 字段"
                   "进行图生图重绘；连接 mask 输入时切换为局部重绘。")

    @classmethod
    def INPUT_TYPES(cls):
        return _common_input_types(include_image=True, denoise_default=0.6)

    def sample(self, image, prompt, model, steps, cfg, scheduler, seed, denoise,
               width, height, negative_prompt="", aspect_ratio="auto",
               use_opencl=False, batch_count=1, mask=None, unique_id=None, **kwargs):
        return self._run(prompt=prompt, model=model, steps=steps, cfg=cfg,
                         scheduler=scheduler, seed=seed, denoise=denoise,
                         width=width, height=height,
                         negative_prompt=negative_prompt,
                         aspect_ratio=aspect_ratio, use_opencl=use_opencl,
                         batch_count=batch_count, image=image, mask=mask,
                         unique_id=unique_id)


NODE_CLASS_MAPPINGS = {
    "LocalDreamTextToImage": LocalDreamTextToImage,
    "LocalDreamImageToImage": LocalDreamImageToImage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LocalDreamTextToImage": "LocalDream 文生图 (提示词 → 图像)",
    "LocalDreamImageToImage": "LocalDream 图生图 (图像 → 图像)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
