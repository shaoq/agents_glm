# Implementation Tasks

> 方案 A（描述入库检索）+ C 存储（原图留存，回传留后续）。
> 数据流：解析层提取原图（`IMAGE` block 携 `image_data`）→ pipeline 图片阶段（存原图 + 生成描述 + 填 block + 清 `image_data`）→ 描述走向量化 / 双索引。
> 视觉模型走 `.env`：`VISION_MODEL`（默认 `glm-4.5v`）。

## 1. models 与 config

- [x] 1.1 `models.py`：`BlockType` 加 `IMAGE`；`Block` 加 `image_ref` / `caption`（str | None）+ `image_data`（bytes | None，临时字段）；新增 `ImageRecord`
- [x] 1.2 `config.py` + `.env.example`：新增 `VISION_MODEL`（默认 `glm-4.5v`）；派生 `images_dir`（`storage/images`）
- [x] 1.3 `ChildChunk.metadata_dict` 在有 `image_ref` 时包含之（chunk 关联原图）

## 2. 原图存储（image_store）

- [ ] 2.1 `indexing/image_store.py`：`ImageStore` 文档维度存储（`put(bytes, doc_id) -> image_ref` / `get` / `delete_by_doc`）+ `ImageRecord`（sqlite 注册表，含 `content_hash` 供增量判定）
- [ ] 2.2 单测：存取 / 文档级删除 / `content_hash` 去重（同图不重存）

## 3. 描述生成（vision_describer）

- [ ] 3.1 `indexing/vision_describer.py`：`ImageDescriptionCache`（sqlite，键 = `content_hash + model`）+ `ZhipuVisionDescriber`（GLM-4.5V，`chat.completions` + `image_url` base64，tenacity 重试，鉴权不重试，失败 caption 兜底）
- [ ] 3.2 单测：缓存命中 / 模型变更不命中 / 限流重试 / 描述失败走 caption（mock client）

## 4. docling_parser 图片处理

- [ ] 4.1 `picture` 移出 `_SKIP_LABELS`；提取原图字节（兼容 try，适配 docling 多版本 API）+ caption → 产出 `IMAGE` block（`image_data`=bytes, `caption`, `text`="", `page`）
- [ ] 4.2 验证：含图 PDF 解析出 `IMAGE` block（需含图 fixture）

## 5. pipeline 图片阶段

- [ ] 5.1 `pipeline/ingest.py`：解析后、向量化前插入「图片处理」阶段——对 `image_data` 非空 block：存原图（`ImageStore`，`content_hash` 增量）→ 生成描述（`ZhipuVisionDescriber`，缓存）→ `block.text=描述` / `image_ref=ref` / 清 `image_data`；失败降级（提取失败跳过、描述失败用 caption）
- [ ] 5.2 `IMAGE` block 描述走向量化 / 双索引（复用现有 embedder / 索引，无需改）

## 6. 测试与端到端

- [ ] 6.1 `cleaning/normalizer.py`：`IMAGE` block 描述文本归一（`image_data` / `image_ref` 不动）
- [ ] 6.2 集成测试：含图文档 ingest → `IMAGE` block 描述入索引 + `image_ref` + `ImageRecord`
- [ ] 6.3 端到端 `agents-rag ingest`（含图 PDF fixture，真实密钥，验证描述生成 + 原图存储）
- [ ] 6.4 覆盖率 ≥ 80%
