# 示例工作流（ComfyUI `/prompt` API 格式）

这两个 JSON 是 ComfyUI 的 API prompt 格式，不是界面里「打开工作流」用的 UI 格式。
它们主要用于验证节点注册与执行；日常使用直接在界面里拖拽节点即可。

用法：

```bash
curl -X POST http://127.0.0.1:8188/prompt \
  -H "Content-Type: application/json" \
  -d "{\"prompt\": $(cat api_prompt_txt2img.json), \"client_id\": \"localdream-test\"}"
```

| 文件 | 说明 |
|---|---|
| `api_prompt_txt2img.json` | `LocalDreamTextToImage` → `PreviewImage` |
| `api_prompt_both_nodes.json` | `LocalDreamTextToImage` → `LocalDreamImageToImage` → `PreviewImage`，无需准备外部图片 |

使用前请先在节点设置里填好设备 IP 并载入模型。`model` 填 `自动（使用设备已载入模型）`
表示沿用手机当前已载入的模型；也可以换成设置界面里列出的具体模型 id。

在界面里搭工作流时：

1. 添加节点 → `LocalDream` → `LocalDream 文生图 (提示词 → 图像)`
2. 添加 `PreviewImage`（或 `SaveImage`），把节点输出 `IMAGE` 连到它
3. 需要图生图时再添加 `LocalDream 图生图 (图像 → 图像)`，把上游 `IMAGE` 连到它的 `image` 输入
