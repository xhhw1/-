# 本地 PostgreSQL 切换说明

本项目的项目、素材、对话、审核卡、审计、后台任务、用户和知识库记录已经支持 PostgreSQL。

## 为什么之前还是 SQLite

代码层已经支持 Postgres，但本地直接运行 API 时会读取 `.env`。如果 `.env` 中仍是：

```env
PROJECT_STORE_BACKEND=sqlite
```

运行态就会继续使用 `LOCAL_DATABASE_URL` 指向的 `data/vision_agent.db`。

## 本地切换步骤

1. 启动 PostgreSQL 16：

```powershell
docker compose up -d postgres
```

2. 迁移现有 SQLite 数据：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py `
  --source sqlite:///data/vision_agent.db `
  --target postgresql+psycopg://vision_agent:vision_agent@localhost:5432/vision_agent
```

3. 修改 `.env`：

```env
PROJECT_STORE_BACKEND=postgres
DATABASE_URL=postgresql+psycopg://vision_agent:vision_agent@localhost:5432/vision_agent
LOCAL_DATABASE_URL=sqlite:///data/vision_agent.db
```

4. 重启 API，检查：

```http
GET http://127.0.0.1:8000/health
```

期望看到：

```json
{
  "project_store_backend": "postgres"
}
```

## 备份

切库前建议保留 `data/vision_agent.db`。本次切换已在 `data/backups/` 下生成 SQLite 备份文件。

## 说明

`GRAPH_CHECKPOINT_BACKEND` 当前仍建议保持 `memory`，除非运行环境已经安装 `langgraph-checkpoint-postgres`。业务数据已经切到 PostgreSQL，不受该项影响。
