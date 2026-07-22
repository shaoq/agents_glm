## Context

当前 docling 解析把 `picture` 当噪音丢弃（`_SKIP_LABELS`），图表 / 示意图 / 公式截图无法检索。本变更实现笔记 §12.1.1 的**方案 A（描述入库检索）+ 方案 C 的存储部分（原图留存）**：图片经 GLM-4.5V 生成文字描述，描述走文本管线被检索；原图按文档维度存盘并经 `image_ref` 关联，为后续查询侧「描述命中 → 取原图 → 回传 GLM-4.5V 看图」铺路（回传本轮不做）。

智谱视觉模型已核实可用（`glm-4v` / `glm-4v-plus` / `glm-4.5v`，`zhipuai` SDK `chat.completions` + `image_url` base64），无需新依赖。

## Goals / Non-Goals

**Goals:**

- docling `picture` 识别 + 提取原图 + 取 caption
- 原图文档维度存储 + `ImageRecord`（`content_hash` 图片级增量）
- GLM-4.5V 生成描述（缓存键 = `content_hash + vision_model`，tenacity 重试）
- 描述作为 `IMAGE` block 入 section，走文本向量化 / 双索引
- `image_ref` 关联原图（为回传铺路）
- `VISION_MODEL` env 配置（默认 `glm-4.5v`）

**Non-Goals:**

- 查询侧回传（描述命中 → 原图 → GLM-4.5V 看图）——后续阶段
- 多模态 embedding（方案 B，图文同向量空间）
- 视频 / 音频

## Decisions

**1. 数据流：解析层提取原图，pipeline 层生成描述。**
docling_parser 在解析时就能拿到 `PictureItem` 的图片数据，而描述生成是付费 LLM 调用（应带缓存、放 pipeline）。故：
- `docling_parser`：`picture` → 提取原图字节 + caption → 产出 `IMAGE` block，**临时携带 `image_data`（bytes）**，`text` 留空
- `pipeline` 新增「图片处理」阶段（解析后、向量化前）：对带 `image_data` 的 block → 存原图（得 `image_ref`）→ 生成描述（缓存）→ `block.text = 描述`、`block.image_ref = ref`、清掉 `image_data`
- 向量化阶段：描述（`text`）走 embedding / 双索引

`image_data` 是 frozen block 的临时字段，pipeline 处理后用 `model_copy` 置空；不进向量库 `metadata_dict`。

**2. docling 图片提取 API 用兼容 try 适配。**
`PictureItem` → 图片字节的具体 API 跨版本有差异（`item.image` / `doc._pictures` / PIL 转换等）。沿用 `_table_markdown` 的兼容 try 套路，实施时核实并适配；提取失败则跳过该图（记日志，不中断）。

**3. 描述缓存独立于 embedding 缓存。**
新 `ImageDescriptionCache`（sqlite，键 = `hash(image_bytes) + vision_model`，值 = 描述文本）。与 embedding 缓存分离（语义不同）；图片 `content_hash` 未变 → 描述不重算。

**4. 图片级增量。**
`ImageRecord`（类比 `DocumentRecord`）：`image_id` / `doc_id` / `source_path` / `page` / `caption` / `description` / `content_hash` / `created_at`。文档更新时比对该 doc 新旧图 `content_hash`，未变的复用描述 / 不重存原图。

**5. 原图文档维度存储。**
`storage/images/<doc_id>/<image_id>.<ext>`（笔记 §12.1.1：文档是 RAG 管理单元，目录级删除简单）。删文档 = 删整个 `<doc_id>/` 目录（与 `ParentStore` 一致）。

**6. `VISION_MODEL` env 配置。**
`config.Settings` 加 `vision_model: str = "glm-4.5v"`；`.env.example` 加 `VISION_MODEL=glm-4.5v`。`ZhipuVisionDescriber` 用 `settings.vision_model`。

**7. `image_ref` 为回传铺路。**
`IMAGE` block 与 `ChildChunk.metadata` 带 `image_ref`；本轮查询侧未实现，但字段就位，后续回传零成本衔接。

**8. 降级链（健壮性）。**
- 原图提取失败 → 跳过该图（记日志，不中断批量）
- 描述生成失败 → 用 `caption` 兜底（若有）或 `text` 留空（仍入索引，只是无描述）
- 视觉 API 限流（429/5xx）→ tenacity 指数退避；鉴权错不重试（复用 embedder 的区分逻辑）

## Risks / Trade-offs

- **[docling 图片提取 API 版本差异]** → 兼容 try + 实施时核实；失败降级跳过。
- **[GLM-4.5V 描述质量]**（数值 / 趋势准确性）→ prompt 约束「客观描述关键信息，禁评价」；质量不足可升级模型或加多角度描述。
- **[描述生成成本]**（每图一次 LLM 调用）→ `content_hash` 缓存 + 增量复用，调参 / 重跑不重算。
- **[Block 临时携 `image_data` bytes 的内存]** → pipeline 在解析后**立即**处理图片阶段、随即清空，不让 bytes 长留。
- **[大图 base64 传 API]** → 超尺寸图片先缩放（实施时定阈值），避免超 API 限制。
