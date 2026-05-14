# Plan for comic

## Project Goal
目前 chatgpt image2 只能基于一次提示词生成 1 幅图。目标是：当用户描述完意图后，系统能够自动生成多张彼此独立的图片（例如 6 张不同造型的图），而不是生成 1 张包含 6 个造型的拼图式结果。

## Scope
### In Scope
- 明确"多图生成"能力的产品定义：一次用户意图输入，产出 N 张独立图片。
- 设计提示词拆分/变体生成策略，让每张图共享主意图但在造型、构图或细节上可控地区分。
- 明确生成流程：输入意图 → 扩展为多条子提示词或多次生成任务 → 返回多张独立结果。
- 定义最小可行实现（MVP）所需的参数：图片数量、变体策略、风格一致性要求。
- 定义验证方式，确保结果确实是"N 张独立文件"，不是"1 张多角色合成图"。

### Out of Scope
- 与当前目标无关的 UI 大改。
- 复杂的任务编排基础设施重构。
- 训练新模型或修改底层图像模型能力。
- 先行支持视频、编辑、局部重绘等非核心能力。
- 自动化图片内容检测（MVP 阶段失败判定为人工审查）。

## Assumptions
- 用户核心诉求是"同一意图下的多样化独立结果"，不是单张图内的多角色排版。
- 底层接口只支持单次调用返回 1 张图，多图通过循环调用实现。
- MVP 阶段 `count` 上限为 **6 张**，超出此范围不在本次范围内。
- 提示词变体由**固定模板规则**生成（不是再次调用 LLM），保持实现简单可控。
- 第一版优先解决"能稳定生成多张独立图"，再考虑高级控制项。
- V1 实现入口已确定为 **CLI script**。
- V1 底层生成接口已确定为 **OpenAI DALL-E 3（openai SDK）**。
- V1 需要一次性交付 4 个 `variation_mode`：`pose` / `composition` / `color` / `camera`。

## Proposed Approach

### 1. 输入层定义
- 接收用户的主意图描述（`prompt`）。
- 必填参数 `count`：整数，MVP 范围 1–6。
- CLI 入口最小参数约定：
  - `--prompt "<用户意图>"`
  - `--count <1-6>`
  - `--variation-mode <pose|composition|color|camera>`
  - `--out-dir <输出目录>`（默认写入本地目录）
- 可选参数 `variation_mode`：枚举值，MVP 支持：
  - `pose`（造型/姿态差异，默认值）
  - `composition`（构图/景别差异）
  - `color`（配色差异）
  - `camera`（镜头角度差异）

### 2. 提示词生成策略
- 基础提示词 = 用户 `prompt` + 固定风格锁定后缀（`style_lock`，见下）。
- 对每个 index `i` in `[1..count]`，追加该 `variation_mode` 对应的变体描述词（预定义词表，见下）。
- 每条子提示词末尾追加**自然语言约束**，明确要求：`single subject, one pose only, not a character sheet, not a collage, not a grid, not a multi-view sheet`。
- **决策**：MVP 不依赖独立 `negative_prompt` 字段或 `--no` 语法，而是将约束直接写入 prompt 文本，降低接口耦合。
- **`style_lock` 定义**：固定前缀字符串，锁定角色/风格一致性，例如 `"same character, consistent art style, single subject"`. MVP 阶段为硬编码字符串，不暴露给用户。

**变体词表示例（`variation_mode=pose`）：**
```
1: "standing upright, front view"
2: "sitting, three-quarter view"
3: "running, side view"
4: "crouching, dynamic angle"
5: "jumping, low angle shot"
6: "resting, back view"
```
其他 mode 需在实现前同样定义完整词表（此为实现前置条件）。

### 3. 生成执行策略
- 对 N 条子提示词**顺序**发起 N 次独立调用（MVP 不并发，避免速率限制未知风险）。
- 每次调用只请求 1 张图，并落盘为独立文件。
- 汇总返回结果为图片列表（数组），每个元素至少包含：`index`、`final_prompt`、`status`、`file_path | error`。
- 若某次调用失败（网络/API 错误），**不重试，标记该 slot 为 error 并继续后续调用**，最终返回带 error 标记的部分结果。

### 4. 结果约束与失败判定（MVP 阶段：人工审查）
- 每个结果是独立图片对象，有独立文件路径，文件数应与成功调用数一致。
- 失败判定标准（人工审查时使用）：
  - **不合格**：图片内出现格子布局、多个角色同框、角色设定板、九宫格拼图。
  - **合格**：图片内只有单一主体，符合 `variation_mode` 对应描述。
- MVP **不实现自动检测**，人工审查即可。

## MVP Deliverables
按 BLOCKER 解除顺序分层：

**设计层（可立即产出，不依赖 BLOCKER）：**
- 完整参数定义文档（`prompt`, `count`, `variation_mode`, `style_lock`）。
- 各 `variation_mode` 的变体词表。
- 数据流图：用户意图 → 子提示词列表 → N 次调用 → 图片列表。

**实现层（依赖 BLOCKER #1 和 #2 解除后）：**
**实现层（已可直接推进）：**
- CLI 入口脚本（解析参数、校验输入、输出结果摘要）。
- 调用封装模块（循环调用 OpenAI DALL-E 3，返回图片列表）。
- 提示词组装函数（基础提示词 + style_lock + 变体词 + 自然语言约束）。
- 本地文件落盘逻辑（按序号输出独立图片文件）。

## Success Criteria
- 输入 `count=6`，系统发起恰好 6 次独立 API 调用，每次请求 1 张图。
- 最终返回 6 个独立图片资源，且文件路径均不同。
- 人工审查：6 张图中至少 5 张为单主体图片，无拼图/角色设定板/多姿势合图。
- 6 张图的核心角色/风格与用户 `prompt` 一致（人工判断）。
- 失败场景可被识别：出现格子布局或多角色同框，判定为不合格并记录。

> **注意**："至少 5/6" 是 MVP 通过线，不是理想目标。理想目标是 6/6，但允许 1 张偏差以避免过拟合负向约束。

## Testing Intent
### Functional Checks（可自动化）
- 输入 `count=6`，断言最终图片列表长度 = 6。
- 断言每次 API 调用参数中 `n=1`（或等价的单图参数）。
- 断言返回结构为数组，每个元素有独立文件路径或 error。
- 断言子提示词数组长度 = `count`，且每条包含单主体/非拼图约束关键词。
- 断言 CLI 参数校验生效：`count<1`、`count>6`、非法 `variation_mode` 时直接报错退出。
- 断言输出目录下成功生成的文件数量与成功结果数量一致。

### Unit Tests（优先实现，使用 mock）
- `buildPrompts(prompt, count, variationMode)`：
  - 返回数量正确。
  - 公共基础提示词一致。
  - 各条仅在对应变体词上不同。
- `runGenerationBatch()`：
  - 顺序调用 SDK 共 `count` 次。
  - 单次失败不会中断后续调用。
  - 聚合结果保留 `index / prompt / status / file_path | error`。
- `parseCliArgs()`：
  - 必填参数缺失时报错。
  - 非法枚举值时报错。
  - 默认值（如 `variation_mode=pose`）正确。

### Prompt Quality Checks（人工审查）
- 用相同主意图测试 3 次，检查是否每次都避免生成角色设定板/拼图。
  - **通过标准**：3 次中至少 2 次结果满足 Success Criteria。
- 检查 N 条子提示词差异是否仅在 `variation_mode` 维度，基础部分完全一致。

### Failure Checks
- 人为构造会触发"多姿势合图"的 `prompt`，验证负向约束是否能纠正。
  - **通过标准**：加负向约束后，6 次调用中出现多姿势合图的比例低于未加约束时。
- 模拟 1 次 API 调用失败，验证系统返回 5 张成功 + 1 个 error 标记，而不是全部中止。

## Risks 与缓解措施
| 风险 | 缓解措施 |
|------|----------|
| 底层模型将"多造型"理解为角色设定表 | 子提示词中加强自然语言约束；`style_lock` 中强调 `single subject` |
| DALL-E 3 对否定约束执行不稳定 | 将"单主体、单姿势、非拼图"写成明确自然语言；人工抽检 3 轮 |
| 变体词表覆盖不全，差异不明显 | 4 个 mode 都先提供 1–6 的固定词表；优先手工验证最容易混淆的项 |
| 接口仅支持单次调用，需循环 | 已假设为循环实现，风险已纳入设计（顺序调用） |
| 并发调用触发速率限制 | MVP 强制顺序调用，规避此风险 |

## Implementation Sequence
1. 定义 CLI 参数契约与结果输出结构。
2. 先写测试：参数校验、提示词生成、顺序调用、单次失败不中断。
3. 定义 4 个 `variation_mode` 的完整 1–6 词表。
4. 实现提示词组装函数，并将单主体/非拼图约束直接写入 prompt。
5. 实现 OpenAI DALL-E 3 顺序调用与结果聚合模块。
6. 实现本地文件落盘与 CLI 输出摘要。
7. 按 Testing Intent 完成 mock 验证与真实 API 手工抽检，记录不合格样例。

## Open Questions / Blockers
- None

## User Input after Round 2
Selected option: 1. CLI script; 1. OpenAI DALL-E 3 (openai SDK); 3. All 4 modes (pose/composition/color/camera)
Question 1: Which entry point for v1 implementation?
Selected options: 1. CLI script

Question 2: Which image API/SDK is in use, and what constraint parameters does it expose?
Selected options: 1. OpenAI DALL-E 3 (openai SDK)
User details: codex cli imagegen

Question 3: How many variation_mode values must v1 ship?
Selected options: 3. All 4 modes (pose/composition/color/camera)
