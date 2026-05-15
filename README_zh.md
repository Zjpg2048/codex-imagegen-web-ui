# comic

本地图像生成与图生视频工具，提供两种工作流：

1. **推荐：** Web 应用，通过 `codex exec` 生成图片，使用本地 Remotion 项目渲染视频。
2. **备用：** Python CLI，直接调用 OpenAI DALL-E 3（需要 `OPENAI_API_KEY`）。

---

## 推荐工作流：Web 应用

### 环境要求

- Python 3.10+
- `codex` 已安装并完成登录认证
- Node.js + npm（用于本地 Remotion 视频渲染）

如需登录 Codex：

```bash
codex login
```

### 启动 Web 应用

```bash
scripts/start_web.sh --port 8000
```

或直接运行：

```bash
python3 webapp.py --port 8000
```

浏览器打开：

```
http://127.0.0.1:8000
```

### 安装视频渲染依赖

视频渲染依赖 `remotion-video/` 目录下的本地项目：

```bash
cd remotion-video
npm install
```

缺少依赖时，发起视频渲染请求会收到明确的错误提示。

### 功能

#### 图片生成

- 提示词输入
- 生成数量选择（1–8 张）
- 项目本地输出目录选择
- 后台任务 + 进度轮询
- 参考图上传
- 历史记录页面 `/history`
- 任务列表页面 `/tasks`
- 单图导出 / 批量导出

#### 图生视频

- 上传源图片，或复用已生成的项目图片
- 运镜提示词输入
- 视频时长控制（2–12 秒）
- 宽高比选择（`16:9` / `9:16` / `1:1`）
- 运镜预设：
  - Cinematic push-in（默认）
  - Parallax float
  - Pan left to right
  - Zoom out reveal
  - Orbital drift
  - 自定义提示词
- 使用本地 Remotion 模板渲染为 MP4
- 视频任务追踪与导出

#### 图生文

- 上传图片，提取可复用的生成提示词或结构化视觉字段
- **Agent 选择：** Claude（默认，使用本地 `claude` CLI，模型为 Haiku 4.5）或 Codex
- 两种分析模式：反向提示词 / 结构化分析
- 可选附加指令，聚焦分析内容
- 结果内嵌显示在同一卡片内
- "Use as image prompt" 按钮，一键将结果填入生成表单

### 工作原理

#### 图片

1. 浏览器将提示词提交到本地后端。
2. 后端立即创建后台任务。
3. 任务执行 `codex exec`，调用内置 `imagegen` 工具生成图片。
4. 生成结果复制到所选输出目录。

#### 视频

1. 后端校验源图片。
2. 源图片暂存至 `remotion-video/public/input/`。
3. 渲染参数写入本地 JSON 文件。
4. 后端执行本地 Remotion 渲染命令。
5. 最终 MP4 写入所选输出目录。

### 注意事项

- 图片生成**不需要** `OPENAI_API_KEY`，但需要已登录的本地 Codex 会话；Codex 图生文同理。
- 图生文选择 Claude agent 需要已登录的本地 `claude` CLI（`claude login`）。
- 视频渲染依赖本地 `remotion-video/` 工作区。
- 输出目录必须在本项目内部。
- 导出目标目录可在项目外，但必须已存在且为目录。

---

## 备用工作流：Python CLI

仅在需要直接调用 OpenAI API 时使用。

### 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 运行

```bash
export OPENAI_API_KEY=your_key_here
scripts/start.sh --prompt "赛博朋克风格女孩肖像" --count 3
```

或：

```bash
python3 generate.py --prompt "赛博朋克风格女孩肖像" --count 3
```

### 完整参数示例

```bash
scripts/start.sh \
  --prompt "赛博朋克风格女孩肖像" \
  --count 3 \
  --variation-mode pose \
  --out-dir output \
  --size 1024x1024 \
  --quality standard
```

#### `--variation-mode` 可选值

| 值            | 说明         |
|---------------|--------------|
| `pose`        | 姿态/造型差异（默认） |
| `composition` | 构图/景别差异 |
| `color`       | 配色差异     |
| `camera`      | 镜头角度差异 |

### 生成逻辑

- 每张图对应一条独立提示词，在用户主意图基础上追加变体描述词。
- 对 N 张图顺序发起 N 次独立 API 调用，每次请求 1 张。
- 单次调用失败时，标记该结果为错误并继续后续调用，不中断整体任务。
- 每张图落盘为独立文件，结果包含：序号、最终提示词、状态、文件路径或错误信息。

---

## 测试

```bash
pytest -q
```
