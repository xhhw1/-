# AI 电商视觉 Agent 本机 Docker 生产化运行说明

## 当前生产化目标

本版本面向本机 Docker / 内部团队试运行：

- 使用 PostgreSQL 16 存项目、会话、资产、审核卡和后台任务。
- 使用 Qdrant 做 Agent 记忆向量检索。
- 使用 MinIO / 本地挂载保存素材和生成图。
- 使用管理员登录保护 API，默认管理员邮箱为 `1173817292@qq.com`。
- 使用数据库 `auth_users` 表管理账号，`.env` 管理员只用于首次启动引导。
- 使用本地缓存 + MinIO/S3 镜像保存素材，解析和生图仍读取本地缓存路径。
- 后台 Agent 和资产解析任务会写入 `background_jobs` 表，并受 `BACKGROUND_WORKER_CONCURRENCY` 控制。

## 启动前必须准备

在 `.env` 中补齐：

```env
AUTH_ENABLED=true
ADMIN_EMAIL=1173817292@qq.com
ADMIN_PASSWORD=请设置一个强密码
JWT_SECRET_KEY=请设置一个至少32位的随机字符串

PROJECT_STORE_BACKEND=postgres
GRAPH_CHECKPOINT_BACKEND=postgres
TASK_QUEUE_BACKEND=thread
BACKGROUND_WORKER_CONCURRENCY=4
BACKGROUND_JOB_RECOVERY_ENABLED=true
STORAGE_BACKEND=s3
S3_BUCKET=vision-agent
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
```

真实模型相关 Key 继续使用现有 `.env` 中的配置：

```env
DEEPSEEK_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
MULTIMODAL_API_KEY=...
LLAMA_CLOUD_API_KEY=...
```

## 启动命令

```powershell
docker compose up --build
```

访问：

- React 生产工作台：`http://127.0.0.1:8000/app-next/`
- 健康检查：`http://127.0.0.1:8000/health`
- MinIO Console：`http://127.0.0.1:9001`
- Qdrant：`http://127.0.0.1:6333`

## 多用户与并发边界

当前已经具备：

- API Bearer Token 登录。
- 项目、会话、资产、审核、下载、记忆查询按 `owner_id` 隔离。
- 管理员用户管理 API：创建成员、禁用账号、重置密码、调整角色。
- 后台任务有 job 记录与并发上限。
- Docker 内 PostgreSQL/Qdrant/Redis/MinIO 独立持久化。

当前是单组织多账号版本。下一步若要开放给多个公司/团队，需要新增 organization/team 表，并把 `owner_id` 扩展为 `organization_id + user_id` 双层隔离。

## 运维检查

后台任务：

```http
GET /api/tasks
GET /api/tasks?project_id=...
```

返回 `queued/running/succeeded/failed`，用于排查“为什么 Agent 还没输出”。

用户管理：

```http
GET /api/auth/users
POST /api/auth/users
PATCH /api/auth/users/{user_id}
```

只有 `admin` 角色可以调用。

如果 `.env` 中 `AUTH_ENABLED=true`，请求业务 API 必须带：

```http
Authorization: Bearer <access_token>
```
