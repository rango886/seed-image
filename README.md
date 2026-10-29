# seed-image

给 AI agent 用的图像生成 CLI：包装本地 ComfyUI 的 HTTP 接口、OpenAI 兼容的图片生成/编辑接口，以及 Gemini 原生图片接口（Nano Banana）。

设计取向：**stdout 只放结果，stderr 放进度，退出码有契约，失败一定带上游报文。**

```powershell
uv sync
uv run seed-image --help
```

全局安装（装完就是 `seed-image` 命令）：

```powershell
uv tool install --force .
```

## 配置

取值优先级：**命令行参数 > 环境变量 > 配置文件 > 内置默认值**。

配置文件默认在 `~/.config/seed-image/config.toml`（Windows 上是 `%USERPROFILE%\.config\seed-image\config.toml`），
用环境变量 `SEED_IMAGE_CONFIG` 可以指定别的路径。文件不存在不算错；存在但写坏了会报 `invalid_config`（退出码 3）。

环境变量一律用官方 SDK 的名字；走中转站就在配置文件里写 `base_url` / `api_key`。

```toml
[comfyui]
server = "http://127.0.0.1:8188"

[openai]
base_url = "https://api.inferera.com/v1"
api_key  = "sk-..."
model    = "gpt-image-2.5-flare"

[gemini]
base_url = "https://api.inferera.com/gemini"   # 不含 /v1beta，请求时自动接上
api_key  = "sk-..."
model    = "gemini-3.1-flash-image"
```

| 配置项 | 环境变量 | 命令行 | 默认值 |
| --- | --- | --- | --- |
| `comfyui.server` | `COMFYUI_SERVER` | `--server` | `http://127.0.0.1:8188` |
| `openai.base_url` | `OPENAI_BASE_URL` | — | `https://api.openai.com/v1` |
| `openai.api_key` | `OPENAI_API_KEY` | — | 无（必填） |
| `openai.model` | — | `--model` | `gpt-image-2.5-flare` |
| `gemini.base_url` | `GOOGLE_GEMINI_BASE_URL` | — | `https://generativelanguage.googleapis.com` |
| `gemini.api_key` | `GEMINI_API_KEY` | — | 无（必填） |
| `gemini.model` | — | `--model` | `gemini-3.1-flash-image` |

```powershell
seed-image config show              # 每一项最终生效的值和来源（api_key 打码）
seed-image config path --template   # 配置文件路径 + 一份模板
```

## 全局

| 选项 | 说明 |
| --- | --- |
| `--format text\|json` | 默认 `text`。可写在任意一层（root / comfyui / 叶子命令），就近者优先 |

`--format json` 时 stdout 恰好是一个 JSON 对象：成功 `{"ok": true, ...}`，失败 `{"ok": false, "error": {code, message, details}}`。
进度、上传日志、执行错误详情一律走 stderr，不污染解析。

### 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 命令行用法错误（Typer） |
| 3 | 本地输入有问题（文件不存在、路径写错、workflow 格式不对） |
| 4 | 上游接口返回错误 |
| 5 | 等待超时（任务仍在服务端执行，没有被取消） |
| 6 | 任务执行失败 |
| 7 | 任务被取消 |
| 8 | 部分成功（例如 4 张图只落盘 3 张） |
| 130 | 用户 Ctrl+C 中断。已提交的任务不会被取消，仍在服务端继续执行 |

## seed-image comfyui

服务器地址优先级：`--server` > `COMFYUI_SERVER` 环境变量 > 配置 `comfyui.server` > `http://127.0.0.1:8188`。

### job submit

```powershell
# 阻塞直到出图，返回图片路径列表
seed-image comfyui job submit --workflow wf.json `
  --values "75:74.inputs.text=一只柯基穿着西装" `
  --values "7.inputs.seed=42" `
  --values "7.inputs.steps=30" `
  --output-path out --timeout 300

# 本地资源用 @upload{...} 包围：提交前自动上传，再把服务端文件名填回该位置
seed-image comfyui job submit --workflow wf.json `
  --values "10.inputs.image=@upload{./cat.png}" `
  --values "12.inputs.video=@upload{./clip.mp4}"

# 只看打好补丁的 workflow，不上传不提交
seed-image comfyui job submit --workflow wf.json --values "7.inputs.seed=42" --dry-run
```

`--values` 的约定：

- `path=value`，**只按第一个 `=` 切分**（提示词里可以有 `=`）。
- path 用 `.` 分段，节点 id 里的 `:` 不是分隔符；数组用 `a[0]`。
- **路径必须已存在**，打错字直接报错并列出同层可用键（打错字静默生成一张“没改过”的图更糟）。
- value 先按 JSON 字面量解析（`42`→数字、`true`→布尔、`[..]`→数组），解析不了才当字符串。
  ComfyUI 的 `seed`/`steps` 需要真数字，字符串会被节点拒绝。
- 需要上传的本地文件写成 `@upload{./cat.png}`。图片、视频、音频、文本走同一个上传入口
  （`POST /api/upload/image` 对字节内容不做类型限制），服务端按“文件名 + 内容哈希”自动去重。
  标记可以出现在 JSON 数组里：`--values '10.inputs.images=["@upload{a.png}","@upload{b.png}"]'`。

并发上传/下载由 `--concurrency` 控制（默认 4）。

### 长任务与并发

`--mode sync`（默认）= submit + wait 的糖。**超时不等于失败，也不会取消任务**：

```powershell
seed-image comfyui job submit --workflow wf.json --timeout 90
# 超时 → 退出码 5，stdout 给出 timeout.unfinished 与继续等待的命令
```

`--on-timeout cancel` 才会顺手取消。想要并发就 async 提交、批量收网，**不需要写 bash 并发**：

```powershell
$a = seed-image comfyui job submit --workflow wf.json --values "7.inputs.seed=1" --mode async
$b = seed-image comfyui job submit --workflow wf.json --values "7.inputs.seed=2" --mode async
seed-image comfyui job wait $a $b --output-path out --timeout 600
```

`job wait` 一次等待多个 id，并发下载全部产物，输出一个完整 JSON，不会出现输出交错。

### 其余子命令

```powershell
seed-image comfyui job status <job_id> [<job_id> ...]   # 只查状态，不下载
seed-image comfyui job cancel <job_id> [<job_id> ...]   # 幂等
seed-image comfyui job list --status pending,in_progress --limit 20   # 丢了 job id 时找回来
seed-image comfyui workflow inspect wf.json [--filter text]          # 列出可赋值的输入槽与当前值
```

`workflow inspect` 是给 agent 省上下文的：不必把整个 workflow.json 读进对话就知道该改哪个路径。

任务执行失败时，服务端的 `execution_error`（节点 id、异常信息）会完整出现在结果的 `jobs[].error`
里并同时打到 stderr —— 那是修 workflow 的唯一线索，不做截断。

## seed-image openai-image

地址与密钥取自环境变量 `OPENAI_BASE_URL` / `OPENAI_API_KEY`，没设就读配置 `openai.base_url` / `openai.api_key`。

```powershell
seed-image openai-image generate --prompt "生成个城市夜景" --number 2 --output-path out
seed-image openai-image edit --prompt "把图一的帽子戴在图二的人物上，用图三的画风" `
  --input-image a.png --input-image b.png --input-image c.png --output-path out
```

- `--model`（默认取配置 `openai.model`，未配置则 `gpt-image-2.5-flare`）、`--size` / `--quality` / `--background` / `--number` 全部按字符串透传，
  取值合法性由上游判定。
- 输入图片读成 base64 data URL 后走 `images[].image_url` 传入。
- 落盘命名：`提示词前20字符-序号-uuid.png`，例如 `生成个城市夜景-01-1b7a6a49.png`；
  加 `--save-params` 才额外写一份参数记录 `生成个城市夜景-1b7a6a49.json`（含请求参数、覆盖项、usage、
  输出文件名，**不含 api key**）；默认不写。

## seed-image gemini-image

走 Gemini 原生 `POST {base_url}/v1beta/models/{model}:generateContent`，请求头 `x-goog-api-key`。
密钥取自 `GEMINI_API_KEY` 或配置 `gemini.api_key`；地址取自 `GOOGLE_GEMINI_BASE_URL` 或配置 `gemini.base_url`。

```powershell
seed-image gemini-image generate --prompt "写着「开业大吉」的红色海报" --aspect-ratio 3:4 --image-size 2K --output-path out
seed-image gemini-image edit --prompt "把第一张图的帽子戴到第二张图的人头上" `
  --input-image a.png --input-image b.png --output-path out
```

| 模型 | 定位 | 分辨率 |
| --- | --- | --- |
| `gemini-3.1-flash-image`（默认） | Nano Banana 2，主力，文字渲染好，最多 14 张参考图 | 512 / 1K / 2K / 4K |
| `gemini-3-pro-image` | Nano Banana Pro，复杂画面、品牌一致性最强，最慢最贵 | 1K / 2K / 4K |
| `gemini-3.1-flash-lite-image` | 最快最便宜，不适合多参考图 | 仅 1K |
| `gemini-2.5-flash-image` | 旧版 Nano Banana | 1K |

- `--aspect-ratio`（1:1 / 3:4 / 16:9 / 21:9 …）、`--image-size`（512 / 1K / 2K / 4K）不传就不发，交给上游默认
  （跟随输入图比例 / 1K）。输出短边被固定，拿不到精确像素尺寸。**不支持透明背景，也没有 mask。**
- 输入图 base64 内联，**图在前、指令在后**，`--input-image` 的顺序就是“第一张/第二张”；最多 14 张，
  base64 后总量超过 18MB 直接报错（先缩图）。MIME 按文件头判断。
- 一次请求一张图；`--number N` = 并发发 N 次（`--concurrency` 默认 4），部分失败退出码 8。
- 先发 `responseModalities=["IMAGE"]`；被拒自动改 `["TEXT","IMAGE"]` 重试一次。模型顺带的文字进结果的 `texts`，并打到 stderr。
- 请求里写死 `safetySettings`：可调的 5 个类别（骚扰、仇恨、色情、危险、公民诚信）全部 `OFF`，不提供参数修改。
  服务端的硬性拦截（`PROHIBITED_CONTENT`、输出图的 `IMAGE_SAFETY` 等）关不掉，仍可能出现。
- 被安全策略拦截报 `blocked`，只回文字不给图报 `no_image`（都是退出码 4，带上游报文）。
  base_url 写错、拿到 HTML 时报 `unexpected_response` 并提示检查前缀。
- 网络错误 / 429 / 5xx 按 `--max-retries`（默认 2）退避重试。落盘命名与 `--save-params` 同 openai-image。

## 结构

```
src/seed_image/
  errors.py     失败的唯一表示 + 退出码契约
  config.py     用户配置：参数 > 环境变量 > 配置文件 > 默认值
  render.py     输出的唯一出口（Output / emit_error / note）
  core/         纯函数：--values 解析与打补丁、workflow 自省、文件命名、请求体装配（OpenAI / Gemini）
  comfy/        ComfyUI：client(单次调用) / service(上传+补丁+入队) / waiting(轮询) / outputs(产物落盘)
  images/       OpenAI 兼容图片接口：client(调用) / save(落盘与可选 sidecar，Gemini 共用)
  gemini/       Gemini generateContent：client(鉴权、重试、modality 降级、并发)
  cli/          Typer 命令：解析参数 → 调下层 → run() 收尾
```

`cli/context.py` 的 `run()` 是唯一的错误出口，所以不存在“某个子命令忘了处理 --format 或退出码”。

## 测试

```powershell
uv run pytest -q
```

`tests/fake_servers/` 下有三个 stdlib 假服务端，可用来跑端到端验证：

```powershell
python tests\fake_servers\fake_comfy.py   # 127.0.0.1:8199
python tests\fake_servers\fake_hub.py     # 127.0.0.1:8200
python tests\fake_servers\fake_gemini.py  # 127.0.0.1:8201（key=test-key，分支见文件头注释）
```
