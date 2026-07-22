# incremental-ingest Specification

## Purpose
TBD - created by archiving change add-knowledge-base-core. Update Purpose after archive.
## Requirements
### Requirement: 内容指纹
系统 SHALL 以流式 SHA-256 计算文档内容指纹（恒定内存），并先用 `(size, mtime)` 预筛跳过明显未变文件；指纹 MUST 基于内容（非仅 mtime）。

#### Scenario: 流式与一次性 hash 一致
- **WHEN** 对同一文件分别用流式与一次性方式计算 SHA-256
- **THEN** 结果一致

#### Scenario: size/mtime 未变则跳过精确 hash
- **WHEN** 文件的 (size, mtime) 与上次记录一致
- **THEN** 跳过精确 hash 计算

### Requirement: 文档注册表
系统 SHALL 用 sqlite 持久化文档注册表作为真相源，记录每个文档的 `doc_id`（= 指纹 + namespace）、`content_fingerprint`、`source_path`、`chunk_ids`、`parent_chunk_ids`、`version`、`status` 等；注册表 SHALL 跨会话持久。

#### Scenario: 注册表跨会话持久
- **WHEN** 进程重启后再次 ingest
- **THEN** 注册表保留上次索引状态，增量 diff 生效

### Requirement: 五态检测
系统 SHALL 通过扫描结果与注册表的 diff 产出五种动作：新增（new）、更新（update）、删除（delete）、移动（move）、跳过（skip）。

#### Scenario: 新文件判为新增
- **WHEN** 扫描到一个注册表中没有的文件
- **THEN** 产出 new 动作

#### Scenario: 内容变化判为更新
- **WHEN** 注册表中已有该路径对应文档但内容指纹已变
- **THEN** 产出 update 动作

#### Scenario: 源文件消失判为删除
- **WHEN** 注册表中有该文档但源目录已无对应文件
- **THEN** 产出 delete 动作

#### Scenario: 路径变化但内容不变判为移动
- **WHEN** 文档指纹已在注册表但 source_path 变化
- **THEN** 产出 move 动作（仅更新路径，不重索引）

#### Scenario: 指纹相同判为跳过
- **WHEN** 文件指纹与注册表记录完全一致
- **THEN** 产出 skip 动作（不处理）

### Requirement: 两阶段执行与先建后删
系统 SHALL 两阶段执行动作：先执行 new / update（建新），后执行 delete；update SHALL 先建立新版本 chunk 并确认成功，再将旧版本 chunk 标记为 `superseded`（不物理删）。

#### Scenario: update 先建新后标旧
- **WHEN** 执行 update 动作
- **THEN** 新版本 chunk 以 `status=active` 写入，旧版本 chunk 标记 `status=superseded`

#### Scenario: delete 按句柄清理
- **WHEN** 执行 delete 动作
- **THEN** 按注册表的 `chunk_ids` 精确清理向量库 / BM25 / 父块 / 缓存

### Requirement: 多索引协同与幂等
系统 SHALL 使向量库 / BM25 / 父块 / 缓存在每个 new / update 上协同写入；new / update 写入前 SHALL 先清理该 `doc_id` 的残留 chunk；单个动作失败 SHALL 被隔离（记录日志）且 MUST NOT 中断整批；动作 SHALL 幂等（重复执行安全）。

#### Scenario: 单文件失败不中断批量
- **WHEN** 某文档索引过程中出错
- **THEN** 该文档被记录为失败，其余文档继续处理

#### Scenario: 重复 ingest 幂等
- **WHEN** 对未变更文档集再次 ingest
- **THEN** 全部判为 skip，不产生重复索引、不调用 embedding API

### Requirement: ingest CLI
系统 SHALL 提供 `agents-rag ingest <dir>` 命令驱动索引管线，完成后输出五态统计与索引规模。

#### Scenario: ingest 产出索引与报告
- **WHEN** 运行 `agents-rag ingest data/raw`
- **THEN** 产出向量库 / BM25 / 注册表 / 缓存，并打印 new / update / delete / move / skip 计数

#### Scenario: 二次 ingest 跳过未变文件
- **WHEN** 不修改文档再次运行 ingest
- **THEN** 报告显示全部 skip，无重复索引

