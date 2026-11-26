---
name: seed-image
description: 用 seed-image 命令行生成/编辑图片。后端四选一：OpenAI 兼容 images 接口（gpt-image，默认首选）、Gemini 原生图片接口（Nano Banana）、AIHubMix 原生图片协议（豆包 Seedream 5.5、Grok Imagine 2.0 等聚合模型）、本地 ComfyUI 工作流（krea2 文生图、flux3 klein 改图、Qwen 单图/多图参考改图、抠图/去背景、SeedVR2 放大）。出图、改图、多图合成、透明背景素材、抠图、放大、批量生成变体时使用。
---

# seed-image

直接执行 `seed-image`，不用先检查环境；只有报错时才跑 `seed-image config show`，看地址和密钥从哪来。地址和密钥都已配好。

## 1. 先选路

| 需求 | 用什么 |
| --- | --- |
| 文生图、自然语言改图、多图合成，要效果好 | `openai-image generate`（**默认**；加 `--input-image` 即改图） |
| 要透明背景的新素材（图标、贴纸、UI 元素） | `openai-image generate --background transparent` |
| 已有图去底 / 还原 alpha | `openai-image generate --input-image a.png --background transparent` |
| 用户**明确要求用 ComfyUI** 抠图 | 硬边缘 → `bg-remove-auto.json`；发丝、玻璃、半透明 → `qwen-edit-turbo.json` |
| 用户**明确要求用 ComfyUI** 本地多图参考改图要质量、要快（约 10s/张），不想走 API | `flux3-klein-edit.json`（Flux3 Klein，本地最好的一档） |
| 放大图片，增加图片像素 | `seedvr2-upscale.json` |
| 用户点名要 Gemini / Nano Banana，或想换个模型对比 | `gemini-image generate` |
| 用户点名要豆包 Seedream、Grok Imagine 或其他 AIHubMix 模型 | `aihubmix generate --model ...` |

默认值规则（用户没另说就照这个来）：

- `openai-image` 的模型用 `gpt-image-2.6-flare`。只有用户点名时才用 `gpt-image-2.6-sunburst`。
- `openai-image` **必须显式传 `--quality low`**（CLI 自己的默认是 `auto`）。只有用户明确要求时才用 `high`。
- `--size auto`，跟随输入比例，除非用户指定尺寸。
- 不加 `--save-params`，除非用户要留档或复现参数。

## 2. 通用约定

- 一律加 `--format json`。stdout 恰好是一个 JSON 对象，进度信息走 stderr。
- **生成的图片路径在 JSON 的 `images` 数组里**，所有命令都一样。把这些路径交给用户就行，不要自动读图复核，除非用户要求。
- **文件名一律用 CLI 返回的名字，不要自己拼**。各后端的命名规则不一样，按提示词或任务 id 猜出来的名字基本是错的，贴给用户就是个打不开的链接。
- 退出码：0 成功 / 2 用法错 / 3 本地输入错（文件不存在、路径写错） / 4 上游接口错 / 5 超时（任务仍在跑） / 6 执行失败 / 7 被取消 / 8 部分成功 / 130 Ctrl+C。出错时细节在 `error.details`。
- 引号：bash 和 PowerShell 都用**单引号**包住 `--values` / `--multi`，防止 `{}`、`$`、中文、空格被 shell 处理。只有要往里塞变量时才用双引号，比如 PowerShell 里写 `"470.inputs.image=@upload{$p}"`。
- 下文示例都是 bash 写法，续行用 `\`；PowerShell 里续行用反引号 `` ` ``，或者写成一行。

## 3. openai-image（默认）

```bash
seed-image openai-image generate --prompt '城市夜景，霓虹倒影' --quality low --output-path out --format json

seed-image openai-image generate --prompt '一只柯基的贴纸，纯净透明背景' \
  --background transparent --quality low --output-path out --format json

seed-image openai-image generate --prompt '把图一的帽子戴到图二的人身上' \
  --input-image a.png --input-image b.png --quality low --output-path out --format json

seed-image openai-image generate --prompt '只保留主体，完全去除背景，保留边缘细节与半透明' \
  --input-image a.png --background transparent --quality low --output-path out --format json
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--prompt` | 必填 | |
| `--input-image` | 可选 | 不传为文生图，传了走编辑（最多 16 张；提示词里用"图一/图二"按顺序指代） |
| `--model` | `gpt-image-2.6-flare` | `gpt-image-2.6-sunburst`：复杂画面、一致性最强，慢且贵 |
| `--quality` | CLI 默认 `auto` | auto/low/medium/high，**agent 传 `low`** |
| `--size` | `auto` | size = "auto" 或 "WIDTHxHEIGHT" ，WIDTH、HEIGHT 都必须是 16 的倍数，1/3 ≤ WIDTH / HEIGHT ≤ 3，每边最大长度 3840 像素 |
| `--complexity` | 默认 `high` | low 适合 局部定点编辑、只换背景 / 改颜色 / 改招牌文字、只改一小块，要求原图大部分保持原样/medium 适合普通单主体插画、产品图、简单海报，元素不多、关系不复杂，兼顾速度和构图稳定 /high 适合多物体、有空间遮挡、大量文字排版、严格布局、多角色互动、流程图 / 海报、元素之间有明确位置关系，**agent 默认不传,除非用户明确要求** |
| `--background` | `auto` | auto/transparent/opaque；transparent 出带 alpha 的 PNG |
| `--number` | 1 | 同一提示词多张变体用它（一次请求） |
| `--output-path` | `.` | 落盘名为 `提示词前20字-序号-uuid.png`（标点、空白换成下划线） |
| `--save-params` | 不写 | 额外写一份参数记录 json |
| `--timeout` / `--max-retries` | 300 / 2 | |

多张变体优先用 `--number N`。提示词不同的多次请求，用 shell 后台并发：

```bash
for i in 1 2 3; do
  seed-image openai-image generate --prompt "城市夜景 变体$i" --quality low --output-path out --format json &
done
wait
```

## 4. gemini-image（Nano Banana，非默认）

和 openai-image 的区别：

- 没有 `--size`、`--quality`、`--background`，**不支持透明背景**。
- 改用 `--aspect-ratio`（1:1 / 3:4 / 16:9 / 21:9 …）和 `--image-size`（512 / 1K / 2K / 4K，默认 1K）控制画面。
- `--input-image` 最多 14 张，顺序就是提示词里的"第一张/第二张"。
- `--number N` 由 CLI 自己并发请求（`--concurrency` 默认 4），不用 shell 并发。

| 模型（`--model`） | 说明 |
| --- | --- |
| `gemini-3.1-flash-lite-image` | 默认 最快最便宜，只有 1K |
| `gemini-3.1-flash-image` | 略慢，支持到 4K |
| `gemini-nano-banana-2.1` | 最新一代gemini图像模型，复杂画面、一致性最强，慢且贵，支持到 4K |

```bash
seed-image gemini-image generate --prompt '写着「开业大吉」的红色海报' --aspect-ratio 3:4 --output-path out --format json
seed-image gemini-image generate --prompt '把第一张图的帽子戴到第二张图的人头上' \
  --input-image a.png --input-image b.png --output-path out --format json
```

安全类别已在代码里全部关成 `OFF`。如果仍然报 `blocked`（服务端硬拦截）或 `no_image`（只回了文字），原因在 `error.details`；改提示词，或者换回 openai-image。

## 5. aihubmix（多模型原生接口）

统一入口都是 `seed-image aihubmix generate`（不传 `--input-image` 为文生图，传了就是参考/编辑）。
只把显式传入的选项放进请求体，避免触发不支持字段的 `schema_violation`。

### 常用模型速查

| 模型（`--model`） | 尺寸参数 | 参考图（`--input-image`） | 其它支持参数 / 特色 |
| --- | --- | --- | --- |
| `doubao-seedream-5-5` | `--size`：默认 `2048x2048`；可传 `1K` / `1.5K` / `2K` 或 `WIDTHxHEIGHT`（总像素 `921600`–`4624220`，比例 `1:16`–`16:1`）。**不支持 `--aspect-ratio`** | 支持，最多 10 张（本地路径或 URL） | `--background`（`auto`/`transparent`/`opaque`，`transparent` 仅限 png）<br>`--output-format`（`png`/`jpeg`）<br>`--number` 固定 `1`（不支持一次多张）<br>`--extra watermark=false`（去右下角 AI 水印，默认 true）<br>`--extra optimize_prompt_options.mode=standard` |
| `grok-imagine-image-2.0` | `--aspect-ratio`（`1:1`/`16:9`/`9:16`/`4:3`/`3:4`/`3:2`/`2:3`/`2:1`/`1:2`/`19.5:9`/`9:19.5`/`20:9`/`9:20`/`21:9`/`5:2`/`auto`）+ `--resolution`（`1K`/`1.5K`/`2K`）。**不支持 `--size`** | 支持，最多 5 张（本地路径或 URL） | `--number` 支持 `1`–`10`<br>无 `--background` / `--output-format` / `extra` |

```bash
# 豆包 Seedream 5.5 文生图（去水印）
seed-image aihubmix generate --model doubao-seedream-5-5 \
  --prompt '阳光洒进花店木窗，胶片质感' --size 2048x2048 --extra watermark=false \
  --output-path out --format json

# 豆包 Seedream 5.5 参考图编辑 / 透明背景素材
seed-image aihubmix generate --model doubao-seedream-5-5 \
  --prompt '把背景换成雪山黄昏，主体不变' --input-image a.png \
  --extra watermark=false --output-path out --format json

# Grok Imagine 2.0（用 aspect-ratio + resolution）
seed-image aihubmix generate --model grok-imagine-image-2.0 \
  --prompt '赛博朋克雨夜街道，霓虹倒影' --aspect-ratio 16:9 --resolution 2K \
  --output-path out --format json
```

### 其他模型与通用参数

- 上表之外的模型，先查参数再调用，不要盲猜字段：
  `seed-image aihubmix model list --format json`
  `seed-image aihubmix model inspect <model_id> --format json`
- 扩展参数：`--extra key=value` 写入请求体 `extra`（支持 `a.b=value` 和 JSON 字面量），`--param key=value` 写入顶层；调试可用 `--dry-run`。
- 输入图字段：`--image-field auto` 默认 1 张放 `image`、多张放 `images`；遇到只认其中一种的模型可显式指定 `--image-field image` 或 `images`。总请求体上限 32 MiB。
- 异步长任务：`--mode poll`（异步提交并轮询直到下载）或 `--mode async`（立即返回 `task_id`），批量收网用 `seed-image aihubmix task wait ID1 ID2 --output-path out --timeout 600 --format json`；sync 超时丢响应可用 `seed-image aihubmix task list --format json` 找回。

## 6. ComfyUI 工作流

工作流目录：`~/.config/seed-image/comfyui-workflows`（下文记作 `$WF`）。服务器地址已配好。

| 文件 | 干什么 | 耗时 | 常用参数路径 |
| --- | --- | --- | --- |
| `krea2.json` | Krea2 文生图 | 10–20s | 提示词 `85.inputs.value`<br>宽 `91.inputs.value` / 高 `92.inputs.value`<br>种子 `30:3.inputs.seed` |
| `flux3-klein-edit.json` | Flux3 Klein 改图，本地质量最好（**改图首选本地档**） | ~10s | 图 `11.inputs.image`<br>提示词 `54.inputs.value`<br>种子 `7.inputs.seed` |
| `qwen-edit-turbo.json` | Qwen-Image 2.1 Turbo 改图（快档） | ~15s | 图 `470.inputs.image`<br>提示词 `509.inputs.value`<br>种子 `459:504.inputs.noise_seed` |
| `qwen-edit.json` | Qwen-Image 2.1 改图（质量档） | 30–60s | 图 `470.inputs.image`<br>提示词 `503.inputs.value`<br>种子 `459:458.inputs.seed` |
| `bg-remove-auto.json` | 纯抠图，不跑扩散，硬边缘 | 1–3s | 图 `23.inputs.image`<br>模型 `22:14.inputs.bg_removal_name` |
| `seedvr2-upscale.json` | SeedVR2 放大并补细节 | 看倍数 | 图 `1.inputs.image`<br>倍数 `77.inputs.value`（1–4，默认 4） |
| `z_image_turbo.json` | z image turbo 生图模型| 5-10s | 提示词 `62.inputs.value`<br>宽 `63.inputs.value` / 高 `64.inputs.value`<br>种子 `65.inputs.seed` |

**表里的参数都测过，基本够用，不用去改别的。** 实在要改，先用 `workflow inspect --filter 关键字` 查路径，不要把整个 workflow json 读进上下文。

各工作流要注意的点：

- **种子是写死在文件里的固定值**。同一提示词、同一种子，出的图一模一样。跑多张变体时，每次都要传不同的种子。
- **所有 LoadImage 的 `image` 都清空成了 `""`**。除 krea2 外都必须传图，否则会报找不到文件。
- **Qwen 两档**结构一样，只是模型和采样不同。两档都能当抠图模型用：在提示词里写"透明背景"，就会输出带 alpha 的图，工作流里没有单独的透明开关。
- **Qwen 和 flux3-klein 的输出尺寸跟随第一张图**。多图时，要被修改的那张底图必须放第一张（`<image1>`），其余图作为素材来源。
- **Qwen 的"优化提示词"开关**（turbo 是 `510.inputs.value`，质量档是 `504.inputs.value`）默认关闭，**agent 不要打开**。打开后会先用一个 9B 模型把提示词扩写一遍，多花几十秒、多占很多显存。这是给人用的，agent 直接写清楚提示词就行。
- **`bg-remove-auto`**：`bg_removal_name` 只有两个可选值。`lucida.safetensors` 是默认，细节多、发丝好、支持半透明；`birefnet.safetensors` 边缘稳，但没有透明度。
- **`seedvr2-upscale`**：显存吃紧时把倍数降到 2；采样参数别动。

### 赋值语法

`--values 'path=value'`：

- 只按第一个 `=` 切分。路径用 `.` 分段，节点 id 里的 `:` 不算分隔符，数组下标写成 `a[0]`。
- **路径必须已存在**。写错会报 `path_not_found`，并列出同一层可用的键。
- 值按 JSON 字面量解析：`42` → 数字，`true` → 布尔，`[1,2]` → 数组；解析不了就当字符串。
- 本地文件写成 `@upload{路径}`，提交前会自动上传，再把服务端文件名填回去。

`--multi 'path=value'`（多图）：

- 同一路径写几次，就是几张图。第 1 次填原节点，之后每多 1 次，就把节点复制一份（`470_2`、`470_3`…），接到下游多图口的下一个空位。
- 命令行里的顺序就是图的编号，提示词里**必须用 `<image1>`、`<image2>`… 指代各张图**。
- Qwen 和 flux3-klein 工作流都支持，最多 16 张。工作流本来只吃一张图时会报 multi_no_slot。

### 示例

```bash
WF=~/.config/seed-image/comfyui-workflows

# Flux3 Klein 改图（本地质量最好，必须传图）
seed-image comfyui job submit --workflow $WF/flux3-klein-edit.json \
  --values '11.inputs.image=@upload{./cat.png}' \
  --values '54.inputs.value=把背景换成雪山黄昏，主体不变' \
  --output-path out --format json

# Flux3 Klein 多图改图 + 出更大的图
seed-image comfyui job submit --workflow $WF/flux3-klein-edit.json \
  --multi '11.inputs.image=@upload{./person.png}' \
  --multi '11.inputs.image=@upload{./hat.png}' \
  --values '54.inputs.value=让<image1>中的人戴上<image2>中的帽子' \
  --values '17.inputs.resolution.megapixels=2' \
  --output-path out --format json

# 快档改图（Qwen Turbo）
seed-image comfyui job submit --workflow $WF/qwen-edit-turbo.json \
  --values '470.inputs.image=@upload{./cat.png}' \
  --values '509.inputs.value=把背景换成雪山黄昏' \
  --output-path out --format json

# 多图改图：底图放第一张
seed-image comfyui job submit --workflow $WF/qwen-edit-turbo.json \
  --multi '470.inputs.image=@upload{./person.png}' \
  --multi '470.inputs.image=@upload{./hat.png}' \
  --values '509.inputs.value=让<image1>中的人戴上<image2>中的帽子，保持人物和背景不变' \
  --output-path out --format json

# 按提示词抠图，保留发丝/半透明
seed-image comfyui job submit --workflow $WF/qwen-edit-turbo.json \
  --values '470.inputs.image=@upload{./photo.png}' \
  --values '509.inputs.value=把相机抠出来，透明背景' \
  --output-path out --format json

# 硬边缘抠图，秒回
seed-image comfyui job submit --workflow $WF/bg-remove-auto.json \
  --values '23.inputs.image=@upload{./logo.png}' --output-path out --format json

# 文生图，指定尺寸和种子
seed-image comfyui job submit --workflow $WF/krea2.json \
  --values '85.inputs.value=一只柯基穿着西装' \
  --values '91.inputs.value=768' --values '92.inputs.value=1024' \
  --values '30:3.inputs.seed=42' --output-path out --format json

# 放大 2 倍
seed-image comfyui job submit --workflow $WF/seedvr2-upscale.json \
  --values '1.inputs.image=@upload{./small.png}' --values '77.inputs.value=2' \
  --output-path out --format json
```

### 并发：async 提交，再用 job wait 一次收齐

不要自己写轮询循环。注意每个任务都用不同的种子：

```bash
a=$(seed-image comfyui job submit --workflow $WF/krea2.json \
  --values '85.inputs.value=一只柯基' --values '30:3.inputs.seed=1' --mode async)
b=$(seed-image comfyui job submit --workflow $WF/krea2.json \
  --values '85.inputs.value=一只柯基' --values '30:3.inputs.seed=2' --mode async)
seed-image comfyui job wait "$a" "$b" --output-path out --timeout 600 --format json
```

### submit 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--workflow` | 必填 | API 格式 workflow 文件 |
| `--values` / `--multi` | | 见上面的赋值语法，可重复 |
| `--mode` | `sync` | sync 会等待并下载产物；async 立刻返回 job id |
| `--output-path` | `.` | 产物落盘目录 |
| `--timeout` | 300 | sync 模式最长等待秒数 |
| `--on-timeout` | `keep` | keep：任务继续跑；cancel：顺手取消 |
| `--dry-run` | | 只输出打好补丁的 workflow，不上传也不提交；`--multi` 复制了哪些节点看 `expanded` 字段 |
| `--poll` / `--concurrency` | 1 / 4 | 轮询间隔秒数 / 上传下载并发数 |
| `--server` | 已配置 | 优先级：`--server` > `COMFYUI_SERVER` > 配置文件 |

### 其他命令

```bash
seed-image comfyui workflow inspect $WF/qwen-edit-turbo.json --filter seed --format json
seed-image comfyui job wait ID1 ID2 --output-path out --timeout 600 --format json
seed-image comfyui job status ID1 ID2 --format json
seed-image comfyui job cancel ID1 --format json
seed-image comfyui job list --status pending,in_progress --limit 20 --format json
```

- `workflow inspect`：列出可改的参数路径和当前值，用 `--filter` 过滤。
- `job wait`：等任务结束并下载产物。
- `job status`：只查状态，不下载。
- `job cancel`：幂等，任务已完成时返回 `cancelled=false`。
- `job list`：丢了 job id 时，用它把任务找回来。

### 出错时

- **退出码 5 只是超时**，任务还在服务端跑。用 stdout 给出的 `job wait` 命令继续等，**不要重复提交**。
- **退出码 6 是执行失败**，节点级的报错完整放在 `jobs[].error` 里，原样转给用户，那是排查的唯一线索。
- **`path_not_found`**：路径写错了，对照报错里的 `available` 字段，或者用 `workflow inspect` 查正确的路径。
