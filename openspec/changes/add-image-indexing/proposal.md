## Why

当前 docling 解析把 `picture`（图片）当噪音丢弃（`_SKIP_LABELS`），含关键信息的图表 / 示意图 / 流程图 / 公式截图无法检索——这是纯文本 RAG 的已知信息盲区。企业文档图片常承载核心信息（财务趋势、架构关系、操作步骤），需纳入索引。

## What Changes

- docling `picture` 不再丢弃：**识别 + 提取原图 + 取 caption**
- 原图按**文档维度**存储（`storage/images/<doc_id>/<image_id>.<ext>`）+ `ImageRecord` 注册表（`content_hash` 支撑增量）
- **多模态 LLM 生成图片描述**（GLM-4.5V，`.env` 配置 `VISION_MODEL`，默认 `glm-4.5v`），缓存键 = 图片 `content_hash`（图没变不重算）
- 描述作为 `IMAGE` block 入 section → 走文本向量化 / 双索引（**描述被检索**）；block 带 `image_ref` 关联原图
- 为后续查询侧「描述命中 → 取原图 → 回传 GLM-4.5V 看图」零成本铺路（方案 C 的回传留后续阶段）
- 复用现有管线：描述走 embedding 缓存 / 双索引 / 五态增量；仅新增「图片处理」阶段与视觉模型调用

## Capabilities

### New Capabilities

- `image-indexing`: 图片原图存储（文档维度 + `ImageRecord`）+ 多模态描述生成（GLM-4.5V，缓存 + 重试）+ `image_ref` 关联 + 图片级增量（`content_hash`）

### Modified Capabilities

- `document-parsing`: 「统一文档模型」新增 `IMAGE` block 类型；`picture` 解析不再跳过，产出 `IMAGE` block（`text`=描述、`image_ref`、`caption`、`page`）

## Impact

- **models**：`BlockType.IMAGE`、`Block.image_ref` / `caption`、新增 `ImageRecord`
- **parsing/docling_parser.py**：`picture` 移出 `_SKIP_LABELS`，提取原图 + caption → 产出 `IMAGE` block（描述待 pipeline 填充）
- **新 `indexing/image_store.py`**：原图文档维度存储 + `ImageRecord`（sqlite 注册表，类比 `DocumentRegistry`）
- **新 `indexing/vision_describer.py`**：`ZhipuVisionDescriber`（GLM-4.5V，缓存 + tenacity 重试，类比 `ZhipuEmbedder`）
- **pipeline/ingest.py**：解析后、向量化前插入「图片处理」阶段（存原图 → 生成描述 → 填 `IMAGE` block）
- **config.py / .env.example**：新增 `VISION_MODEL`（默认 `glm-4.5v`）
- **依赖**：智谱视觉 API（`zhipuai` SDK 已有，无需新依赖）
- **非影响**：查询侧回传（C 的完整链路）本轮不做；文本 / 表格解析与索引逻辑不变
