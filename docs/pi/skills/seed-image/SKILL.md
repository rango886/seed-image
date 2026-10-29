---
name: seed-image
description: 用 seed-image 生成/编辑图片。包装本地 ComfyUI 工作流（flux2_klein 图片编辑、黑白底图还原透明度）、OpenAI 兼容的 images 接口（主推 gpt-image）与 Gemini 原生图片接口（Nano Banana）。当需要出图、改图、抠图/还原透明通道、批量并发生图时使用。
---

# seed-image

统一入口 `seed-image`（已全局安装）。stdout 只有结果，stderr 是进度，退出码有契约。
给 agent 用时加 `--format json`，stdout 恰好一个 JSON 对象。示例全部为 bash；
POSIX shell 下 `--values` 一律**单引号**（防 `{}` 展开与中文/空格拆分），PowerShell 换双引号。

退出码：0 成功 / 2 用法错 / 3 本地输入错 / 4 上游错 / 5 超时（任务仍在跑）/ 6 执行失败 / 7 被取消 / 8 部分成功 / 130 Ctrl+C。

命令树：`comfyui workflow inspect`、`comfyui job submit|wait|status|cancel|list`、`openai-image generate|edit`、`gemini-image generate|edit`、`config show|path`。

配置优先级：命令行参数 > 环境变量 > `~/.config/seed-image/config.toml`（可用 `SEED_IMAGE_CONFIG` 改路径）> 内置默认值。
`seed-image config show` 列出每项生效值与来源（api_key 打码）。

## 工作流清单

| 工作流文件 | 用途 | 参数 |
| --- | --- | --- |
| V:\software\AI\comfyui-workflows\krea2.json | krea2 生图模型，单次约 10–20s | `85.inputs.value` 提示词<br>`91.inputs.value` 宽<br>`92.inputs.value` 高 |
| `D:\TOOLS\workflow\flux2_klein.json` | flux2_klein 编辑图片，单次约 10–20s | `75:74.inputs.text` 提示词<br>`76.inputs.image` 参考图 |
| `D:\TOOLS\workflow\抠出图片中主体.json` | 自动抠主体（简单边缘） | `2.inputs.image` 输入图片 |
| `D:\TOOLS\workflow\抠出图片中半透明或是不清晰边缘的主体.json` | 抠半透明/模糊边缘主体 | `76.inputs.image` 输入图片<br>`168:165.inputs.text` 提示词1→白底，例：保持主体位置和大小不变，保持光照不变，仅将背景变成白色<br>`75:74.inputs.text` 提示词2→黑底，例：保持主体位置和大小不变，保持光照不变，仅将背景变成黑色 |

## ComfyUI

`--values path=value`：只按第一个 `=` 切分；`.` 分段，节点 id 里的 `:` 不是分隔符，数组用 `a[0]`；
**路径必须已存在**（打错报错并列出同层键）；value 按 JSON 字面量解析（`42`→数字、`true`→布尔、`[1,2]`→数组）；
本地文件写 `@upload{路径}`，提交前自动上传并回填服务端文件名（图片/视频/音频/文本通用）。

常用（sync 会阻塞到完成并下载产物）：

```bash
# 编辑图片
seed-image comfyui job submit --workflow 'D:/TOOLS/workflow/flux2_klein.json' \
  --values '75:74.inputs.text=把背景换成雪山黄昏' \
  --values '76.inputs.image=@upload{./cat.png}' \
  --output-path out --format json

# 抠图（简单边缘）
seed-image comfyui job submit --workflow 'D:/TOOLS/workflow/抠出图片中主体.json' \
  --values '2.inputs.image=@upload{./logo.png}' --output-path out

# 改清单外的参数前先自省，别把整个 json 读进上下文
seed-image comfyui workflow inspect 'D:/TOOLS/workflow/flux2_klein.json' --filter text
seed-image comfyui job submit --workflow wf.json --values '7.inputs.seed=42' --dry-run  # 只看补丁结果
```

并发：async 提交 + `job wait` 一次收网，不要自己写并发循环。

```bash
a=$(seed-image comfyui job submit --workflow wf.json --values '7.inputs.seed=1' --mode async)
b=$(seed-image comfyui job submit --workflow wf.json --values '7.inputs.seed=2' --mode async)
seed-image comfyui job wait "$a" "$b" --output-path out --timeout 600 --format json
```

全量参数：

```bash
seed-image comfyui job submit \
  --workflow wf.json \
  --values '75:74.inputs.text=提示词' --values '7.inputs.seed=42' --values '7.inputs.steps=30' \
  --mode sync \            # sync=等待并下载 / async=只返回 job id
  --output-path out \      # 默认 .
  --timeout 300 \          # sync 最长等待秒数
  --poll 1 \               # 轮询间隔
  --on-timeout keep \      # keep=任务继续跑 / cancel=顺手取消
  --concurrency 4 \        # 上传/下载并发
  --dry-run \              # 只打印打好补丁的 workflow
  --server http://127.0.0.1:8188 \
  --format json

seed-image comfyui job wait ID1 ID2 --output-path out --timeout 300 --poll 1 \
  --on-timeout keep --concurrency 4 --server http://127.0.0.1:8188 --format json
seed-image comfyui job status ID1 ID2 --format json          # 只查状态，不下载
seed-image comfyui job cancel ID1 --format json              # 幂等，已完成返回 cancelled=false
seed-image comfyui job list --status pending,in_progress,completed,failed --limit 20 --offset 0
seed-image comfyui workflow inspect wf.json --filter Sampler --format json
```

服务器地址：`--server` > `COMFYUI_SERVER` > 配置 `comfyui.server` > `http://127.0.0.1:8188`。
超时（码 5）不是失败，任务仍在服务端跑，stdout 给出继续等待的命令。
执行失败时节点级 `execution_error` 完整出现在 `jobs[].error`，那是修工作流的唯一线索。

## OpenAI 兼容接口

地址与密钥取自 `OPENAI_BASE_URL` / `OPENAI_API_KEY`，没设就读配置 `openai.base_url` / `openai.api_key`（已配好中转站）。默认模型 `gpt-image-2.5-flare`，支持透明背景（`--background transparent`，出 PNG 带 alpha）。
落盘 `提示词前20字-序号-uuid.png`；**默认不写参数记录 json**，需要留档时加 `--save-params`。`--background` 原样透传，取值非法由上游报错。

| 模型（`--model`） | 说明 |
| --- | --- |
| `gpt-image-2.5-flare` | 默认，速度快，效果不错 |
| `gpt-image-2.5-sunburst` | 复杂画面/一致性最强，慢且贵 |

常用：

```bash
seed-image openai-image generate --prompt '城市夜景，霓虹倒影' --number 2 --quality low --output-path out --format json

# 直接出透明背景素材（图标/贴纸/去底元素）
seed-image openai-image generate --prompt '一只柯基的贴纸，纯净透明背景' \
  --background transparent --output-path out --format json

seed-image openai-image edit --prompt '把图一的帽子戴到图二的人身上' \
  --input-image a.png --input-image b.png --output-path out --format json

# 已有图去底/还原 alpha（默认走这条，不用 ComfyUI）
seed-image openai-image edit --prompt '只保留主体，完全去除背景，保留边缘细节与半透明' \
  --input-image a.png --background transparent --quality low --output-path out --format json
```

并发（CLI 本身单请求，用 shell 后台任务并行，最多 16 张输入图/次）：

```bash
for i in 1 2 3; do
  seed-image openai-image generate --prompt "城市夜景 变体$i" --output-path out --format json &
done
wait
```

全量参数（generate / edit 除输入图外完全一致）：

```bash
seed-image openai-image generate \
  --prompt '提示词' \
  --model gpt-image-2.5-flare \
  --size auto \            # auto 或 1024x1024 等
  --quality low \          # auto/low/medium/high，默认用 low
  --background auto \      # auto/transparent/opaque，原样透传
  --number 1 \
  --output-path out \
  --no-save-params \      # 默认不写参数记录 json；要留档改 --save-params
  --timeout 300 \
  --max-retries 2 \
  --format json

seed-image openai-image edit --prompt '提示词' \
  --input-image a.png --input-image b.png \
  --model gpt-image-2.5-flare --size auto --quality low --background auto \
  --number 1 --output-path out --save-params --timeout 300 --max-retries 2 --format json
```

## Gemini 原生接口（Nano Banana）

也支持 Gemini 出图，但**不是默认选项**；密钥/地址取自 `GEMINI_API_KEY` / `GOOGLE_GEMINI_BASE_URL` 或配置 `[gemini]`（已配好中转站）。
参数与 openai-image 不同：没有 `--size/--quality/--background`，改用 `--aspect-ratio`（1:1 / 3:4 / 16:9 / 21:9 …）与 `--image-size`（512 / 1K / 2K / 4K，不传为 1K）。
**不支持透明背景**。`--input-image` 最多 14 张，顺序即“第一张/第二张”。`--number N` 由 CLI 并发发请求，不用 shell 并发。

| 模型（`--model`） | 说明 |
| --- | --- |
| `gemini-3.1-flash-lite-image` | 最快最便宜，仅 1K，不适合多参考图 |
| `gemini-3.1-flash-image` | 默认，Nano Banana 2，主力，文字渲染好，支持到 4K |
| `gemini-nano-banana-2.1` | gemini-nano-banana-2.1，复杂画面/一致性最强，慢且贵 |


```bash
seed-image gemini-image generate --prompt '写着「开业大吉」的红色海报' --aspect-ratio 3:4 --output-path out --format json
seed-image gemini-image edit --prompt '把第一张图的帽子戴到第二张图的人头上' \
  --input-image a.png --input-image b.png --output-path out --format json
# 全量：--model --aspect-ratio --image-size --number --concurrency 4 --output-path --save-params --timeout 300 --max-retries 2 --format
```

可调安全类别已在代码里全部关到 `OFF`；仍报 `blocked`（服务端硬性拦截）或 `no_image`（只回了文字）时，原因在 `error.details`，改提示词或换回 openai-image。

## 最佳实践

选路（默认优先 `gpt-image-2.5-flare`，即 `openai-image generate/edit`）：

1. **文生图、多图合成、自然语言改图、要效果好** → `openai-image`，首选，不必碰工作流。
2. **要透明背景的新素材**（图标、贴纸、UI 元素） → `openai-image` 直接加 `--background transparent`，一步到位，不要先出白底再抠。
3. **已有图片要去底/还原 alpha** → 也走 `openai-image edit --background transparent`，不要默认拉 ComfyUI。
4. **只有用户明确要求用 ComfyUI 抠图时** 才走工作流：简单硬边缘用 `抠出图片中主体.json`；半透明、发丝、玻璃、模糊边缘用 `抠出图片中半透明或是不清晰边缘的主体.json`。
5. **从一张图里取出某个元素的透明图** → 一次 `openai-image edit`：提示词说清只保留该元素 + `--background transparent`；用户指定走 ComfyUI 时再改成先提白底图 → 判断边缘类型 → 调对应抠图工作流。
6. **简单快速的图片编辑、批量跑变体、不在意精细质量** → `flux2_klein.json`，10–20s/张，async 并发提交。
7. **用户点名要 Gemini / Nano Banana**，或 gpt-image 效果不理想想换个模型对比 → `gemini-image`；其余情况仍走 `openai-image`。

参数默认：

- `--quality` 默认 `low`（快且省钱）；**只有用户明确写 `--quality high` 时才用 `high`**。
- `--size auto` 让模型跟随输入比例，除非用户指定尺寸。
- 参数记录 json 默认不写；只有用户明确要留档/复现参数时才加 `--save-params`。
- 多张变体优先 `--number N`（一次请求），跨提示词才用 shell 后台并发。

协作习惯：

- 生成后不要自动读图复核，除非用户要求；直接把落盘路径给用户。
- 给 agent/脚本消费时统一加 `--format json`。
- ComfyUI 报码 5 只是超时，任务还在跑，用 stdout 给的 `job wait` 命令续等，别重复提交。
- 改工作流参数前先 `workflow inspect --filter`，不要把整个 workflow json 读进上下文。

直接执行 `seed-image` ，无需检测环境，只有报错的时候才去检测（先跑 `seed-image config show` 看地址/密钥从哪来）
