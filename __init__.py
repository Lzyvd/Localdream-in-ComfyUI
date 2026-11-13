# -*- coding: utf-8 -*-
"""Localdream-in-ComfyUI —— 手机端 Local Dream「受控模式」的 ComfyUI 插件。

安装：把本目录放到 ComfyUI/custom_nodes/ 下，重启 ComfyUI 即可。
不需要任何额外后台程序、额外服务或独立部署：设置界面走 ComfyUI 自带的
aiohttp 路由，生成请求由节点在 ComfyUI 进程内直连手机。
"""

import logging

try:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
    from .web_api import register_routes
except ImportError:
    # 极少数加载方式会把 __init__.py 当作普通模块（不是 package）。
    # 显式按文件路径加载同目录子模块，避免误 import 到 ComfyUI 自带的 nodes 模块。
    import importlib.util as _ilu
    import os as _os
    import sys as _sys

    _dir = _os.path.dirname(_os.path.abspath(__file__))

    def _load(name, filename, alias=None):
        spec = _ilu.spec_from_file_location(name, _os.path.join(_dir, filename))
        module = _ilu.module_from_spec(spec)
        _sys.modules[name] = module
        if alias:
            _sys.modules[alias] = module
        spec.loader.exec_module(module)
        return module

    _load("ld_client", "ld_client.py")
    _nodes = _load("_ld_nodes_standalone", "nodes.py")
    _api = _load("_ld_web_api_standalone", "web_api.py")
    NODE_CLASS_MAPPINGS = _nodes.NODE_CLASS_MAPPINGS
    NODE_DISPLAY_NAME_MAPPINGS = _nodes.NODE_DISPLAY_NAME_MAPPINGS
    register_routes = _api.register_routes

WEB_DIRECTORY = "./js"

try:
    register_routes()
except Exception as exc:                 # 路由失败不应导致节点加载失败
    logging.getLogger("LocaldreamComfy").warning(
        "[Localdream] 注册设置路由失败: %s", exc)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
