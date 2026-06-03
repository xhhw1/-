# 生产健康检查与排障说明

本项目现在区分两类健康检查：

- `/health`：API 存活检查。用于 Docker healthcheck，确认服务进程可响应。
- `/health/ready`：生产就绪检查。用于上线门禁、负载均衡 readiness、排查基础设施连接。

## `/health/ready` 检查项

返回状态：

- `ready`：所有当前运行态必需依赖可用。
- `not_ready`：至少一个硬依赖失败，HTTP 状态码为 `503`。

检查项：

- `database`：当 `PROJECT_STORE_BACKEND=postgres/sqlite` 或 `GRAPH_CHECKPOINT_BACKEND=postgres` 时检查 SQL 连接。
- `redis`：当 `TASK_QUEUE_BACKEND=redis` 或 `RATE_LIMIT_BACKEND=redis` 时检查 Redis 连接和队列长度。
- `worker`：当 `TASK_QUEUE_BACKEND=redis` 时检查 Redis 中是否存在 Worker 心跳。
- `object_storage`：当 `STORAGE_BACKEND=s3/minio` 时检查 bucket 是否可访问。
- `vector_memory`：生产环境或真实外部工具模式下检查 Qdrant 是否可连接。

## Worker 心跳

Redis 队列模式下，Worker 会周期性写入：

```text
${WORKER_HEARTBEAT_KEY_PREFIX}:<hostname>:<pid>
```

默认配置：

```env
WORKER_HEARTBEAT_KEY_PREFIX=ai_visual_agent:workers
WORKER_HEARTBEAT_TTL_SECONDS=30
```

如果 `/health/ready` 返回 `worker failed`，优先检查：

```powershell
docker compose ps
docker compose logs worker --tail 100
docker compose exec redis redis-cli keys "ai_visual_agent:workers:*"
```

## Docker 启动顺序

`docker-compose.yml` 已配置：

- `postgres` 使用 `pg_isready` 健康检查。
- `redis` 使用 `redis-cli ping` 健康检查。
- `minio` 使用 `/minio/health/live` 健康检查。
- `qdrant` 使用 `/healthz` 健康检查。
- `api` 和 `worker` 等待上述服务 `service_healthy` 后启动。

启动后建议确认：

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

## 常见问题

- `database failed`：检查 `DATABASE_URL`、Postgres 容器状态和端口占用。
- `redis failed`：检查 `REDIS_URL`、Redis 容器状态。
- `worker failed`：Redis 可用但没有 Worker 心跳，通常是 Worker 未启动、反复崩溃或连接了不同 Redis。
- `object_storage failed`：检查 MinIO 是否启动、bucket 是否存在、`S3_ACCESS_KEY/S3_SECRET_KEY` 是否一致。
- `vector_memory failed`：检查 Qdrant 容器状态和 `QDRANT_URL`。
