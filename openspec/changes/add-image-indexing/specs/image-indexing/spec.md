## ADDED Requirements

### Requirement: 原图文档维度存储
系统 SHALL 将图片原图按文档维度存储到 `storage/images/<doc_id>/<image_id>.<ext>`，并维护 `ImageRecord` 注册表（`image_id` / `doc_id` / `source_path` / `page` / `caption` / `description` / `content_hash` / `created_at`）；删除文档 SHALL 删除其整个 `<doc_id>/` 图片目录与对应 `ImageRecord`。

#### Scenario: 原图存储与文档级清理
- **WHEN** 解析含图片的文档并索引
- **THEN** 原图写入 `storage/images/<doc_id>/`，`ImageRecord` 记录其元数据
- **WHEN** 删除该文档
- **THEN** 其 `<doc_id>/` 图片目录与 `ImageRecord` 记录一并清除

### Requirement: 多模态描述生成
系统 SHALL 用智谱视觉模型（`VISION_MODEL`，默认 `glm-4.5v`）为每张图片生成文字描述；描述缓存以 `content_hash + vision_model` 为键，图片与模型均不变时复用缓存、不调用 API；可重试错误（429 / 5xx）SHALL 指数退避重试，鉴权错误不重试。

#### Scenario: 描述生成与缓存命中
- **WHEN** 首次处理一张图片
- **THEN** 调用视觉模型生成描述并缓存
- **WHEN** 同一图片（`content_hash` 相同）再次处理
- **THEN** 命中缓存、不调用视觉 API

#### Scenario: 模型变更不命中旧缓存
- **WHEN** `VISION_MODEL` 变更
- **THEN** 旧描述缓存不命中、重新生成

### Requirement: 描述入索引
系统 SHALL 将图片描述作为 `IMAGE` block 的 `text`，与段落 / 表格一同进入向量化与双索引（可被文本检索）。

#### Scenario: 图片描述可被检索
- **WHEN** 图片描述入索引后
- **THEN** 向量与 BM25 检索可基于描述文本召回该图片对应的 chunk

### Requirement: image_ref 关联原图
系统 SHALL 在 `IMAGE` block 与对应子块 metadata 中携带 `image_ref`，关联到存储的原图，为后续「描述命中 → 取原图 → 回传视觉模型」提供句柄。

#### Scenario: chunk 携带 image_ref
- **WHEN** 图片描述被分块并索引
- **THEN** 子块 metadata 含 `image_ref`，可据其取回原图

### Requirement: 图片级增量
系统 SHALL 以图片 `content_hash` 为身份；文档更新时，未变更的图片（`content_hash` 相同）SHALL 复用其原图与描述，不重存 / 不重算。

#### Scenario: 未变图片复用描述
- **WHEN** 文档更新但其某图片 `content_hash` 未变
- **THEN** 该图片复用既有原图与描述，不调用视觉 API、不重存原图

### Requirement: 图片处理降级
系统 SHALL 对图片处理失败降级：原图提取失败 SHALL 跳过该图并记日志（MUST NOT 中断批量）；描述生成失败 SHALL 用 `caption` 兜底（若有）或留空描述（`IMAGE` block 仍入索引）。

#### Scenario: 提取失败跳过不中断
- **WHEN** 某图片原图提取失败
- **THEN** 跳过该图、记录日志，其余文档内容继续索引

#### Scenario: 描述失败用 caption 兜底
- **WHEN** 图片描述生成失败但有 caption
- **THEN** `IMAGE` block 的 `text` 使用 caption，仍入索引
