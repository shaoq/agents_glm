# agents_glm 工作区

多项目工作区根目录。每个子目录是一个独立的 Python 工程，统一使用 conda 环境 `agents_glm`（Python 3.12.13）。

## 环境约定

所有子项目共用同一个 conda 环境：

```bash
conda activate agents_glm
```

- Python 版本：3.12.13
- 环境路径：`/Users/jie.hua/miniconda3/envs/agents_glm`

## 子项目

| 项目 | 说明 | 状态 |
|------|------|------|
| agents_rag | RAG 检索增强生成工程 | 规划中 |

## 添加新子项目

1. 在根目录创建 `agents_<功能>/` 子目录
2. 使用 conda 环境 `agents_glm`
3. 子目录内放置各自的 README 说明
4. 在上方「子项目」表格中登记一行

## 目录结构

```
agents_glm/
├── README.md
├── .gitignore
└── agents_rag/
```
