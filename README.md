# AI 电商视觉设计多智能体系统

这是一个从零搭建的 MVP 工程骨架，用于承载两条工作流：

- 包装概念 AI 工作流：卖点提炼、包装四面策略、包装设计图生成。
- 详情提案 AI 工作流：卖点提炼、五屏详情策略、详情页设计图生成。

## 技术栈

- FastAPI：后端 API 与审核入口。
- React + Vite + TypeScript：生产版前端工程，构建产物挂载到 `/app-next/`；React 版本复用旧版工作台 CSS 与 class 体系，确保 UI 与 `/app/` 对齐。
- LangGraph：工作流编排、状态机、人工审核暂停与恢复。
- LangChain：工具封装与模型调用接口。
- DeepSeek V4：卖点、策略、文案、质检推理。
- GPT Image 2：包装/详情视觉底图生成和编辑。
- Gemini：竞品视频、多模态素材理解。
- LlamaParse / python-pptx / PaddleOCR / SAM 2：文档解析、OCR、抠图和视觉资产处理。
- PostgreSQL 16：项目、资产、审核、工作流、质检和归档的主数据库。
- Qdrant：Agent 语义记忆库，用于检索品牌 VI、竞品洞察、历史反馈和相似案例。
- MinIO / S3：PPT、PDF、图片、生成图等对象存储。
- Harness：CI/CD、密钥管理、部署发布。

## 产品形态

系统采用“统一项目平台 + 两条工作流模板”的形态。Step 1 和 Step 2 共用，Step 3 按 `workflow_type` 分流：

```text
资料输入
  -> Parser Agent
  -> Competitor Analyst Agent
  -> Marketer Agent
  -> 人工审核卖点
  -> packaging / detail_page 分支
  -> 策略 Agent
  -> 人工审核策略
  -> VI Guardian Agent
  -> Designer Agent
  -> Critic Agent
  -> 人工审核设计图
  -> Archivist Agent
```

## 当前实现范围

- 项目、素材、工作流状态的 Pydantic Schema。
- LangGraph 节点、路由和人工审核 `interrupt()`。
- 包装/详情页双分支。
- FastAPI 项目创建、文件上传、启动工作流、恢复审核接口。
- PPTX/PDF 文本解析。
- Qdrant MemoryStore 接口，默认 mock fallback。
- PostgreSQL ProjectStore 接口，`PROJECT_STORE_BACKEND=postgres` 时启用。
- LangGraph checkpoint 后端可切换，`GRAPH_CHECKPOINT_BACKEND=postgres` 时使用 PostgreSQL 持久化审核中断和恢复状态。
- Docker、docker-compose、K8s 和 Harness Pipeline 模板。

## 本地启动

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn ai_visual_agent.main:app --reload
```

可选重依赖按需安装：

```bash
pip install -r requirements-vision.txt
pip install -r requirements-infra.txt

# 或使用 pyproject extras
pip install -e ".[vision]"
pip install -e ".[infra]"
```

访问：

- `GET /app/`：旧版稳定工作台，用作 UI 和功能对照基准。
- `GET /app-next/`：React/Vite/TypeScript 生产版工作台。仅当 `frontend/dist` 存在时自动挂载，视觉层复用旧版样式体系。
- `GET /health`
- `GET /health/integrations`
- `POST /api/integrations/probe`
- `POST /api/projects`
- `PATCH /api/projects/{project_id}`
- `DELETE /api/projects/{project_id}`
- `GET /api/projects/{project_id}/detail`
- `GET /api/projects/{project_id}/assets/{asset_id}/content`
- `POST /api/projects/{project_id}/assets`
- `PATCH /api/projects/{project_id}/assets/{asset_id}`
- `DELETE /api/projects/{project_id}/assets/{asset_id}`
- `POST /api/projects/{project_id}/assets/{asset_id}/analyze`
- `POST /api/projects/{project_id}/assets/{asset_id}/segment`
- `POST /api/workflows/{project_id}/start`
- `POST /api/workflows/{project_id}/resume`
- `POST /api/memory`
- `POST /api/memory/search`
- `GET /api/knowledge`
- `POST /api/knowledge`
- `PATCH /api/knowledge/{entry_id}`
- `DELETE /api/knowledge/{entry_id}`
- `POST /api/knowledge/search`
- `POST /api/projects/{project_id}/knowledge/preview`
- `GET /api/prompts`
- `GET /api/prompts/{prompt_name}`
- `GET /api/golden/fixtures`
- `POST /api/golden/fixtures/{fixture_name}/run`
- `GET /api/projects/{project_id}/audit`

## 生产版前端

当前旧版稳定入口是 `/app/`，生产版 React 工作台入口是 `/app-next/`。React 版本的目标不是重新设计 UI，而是在 TypeScript、组件化和统一 API client 的架构下复刻旧版工作台的视觉、布局和交互：

```bash
cd frontend
npm install
npm run build
```

构建后重启后端，FastAPI 会自动挂载 `frontend/dist` 到 `/app-next/`。新版前端采用 TypeScript strict mode、TanStack Query、统一 API client 和组件化工作台；视觉层复用 `src/ai_visual_agent/web/styles.css`，以旧版 `/app/` 作为等价验收基准。

## 存储分层

生产版采用三层存储：

```text
PostgreSQL 16
  存项目、资产元数据、工作流状态、人工审核、质检报告、Prompt 版本和归档记录

Qdrant
  存语义记忆：品牌 VI、产品资料切片、竞品洞察、历史人工反馈、成功案例

MinIO / S3
  存原始文件、页面截图、抠图、生成图、最终交付物
```

Qdrant 不替代 PostgreSQL，PostgreSQL 也不承担向量检索。Agent 需要“记忆”时先按 `project_id`、`brand_id`、`category`、`workflow_type` 等 payload 过滤，再做向量检索。

## Agent 知识库

品类包装原则、Badge 规则、出图方法等不写死在 Agent 代码里，而是存放在知识库模块：

- 默认种子：`src/ai_visual_agent/knowledge/defaults/*.json`
- 运行时表：`knowledge_entries`
- 管理入口：前端「知识库」与 `/api/knowledge`

包装策略 Agent 会根据项目品类、用户输入、已确认卖点和工作流类型自动检索知识。命中的知识只作为设计方法和表达原则，不会覆盖产品资料、用户确认内容和 VI 事实。

本地单进程开发默认：

```text
PROJECT_STORE_BACKEND=memory
```

Docker Compose / 生产部署：

```text
PROJECT_STORE_BACKEND=postgres
GRAPH_CHECKPOINT_BACKEND=postgres
LANGGRAPH_STRICT_MSGPACK=true
DATABASE_URL=postgresql+psycopg://vision_agent:vision_agent@postgres:5432/vision_agent
```

`GRAPH_CHECKPOINT_BACKEND=postgres` 依赖 `langgraph-checkpoint-postgres`，第一次启动会调用 `.setup()` 创建 LangGraph checkpoint 表。

OCR 后端：

```text
OCR_BACKEND=mock
OCR_BACKEND=paddle
OCR_LANGUAGE=ch
```

`OCR_BACKEND=paddle` 需要在 OCR worker 或 API 镜像中安装 `paddleocr` 和 `paddlepaddle`。本地开发默认 mock，避免重依赖拖慢启动。

抠图/分割后端：

```text
SEGMENTATION_BACKEND=mock
SEGMENTATION_BACKEND=sam2
SAM2_CHECKPOINT=/models/sam2/checkpoint.pt
SAM2_MODEL_CFG=/models/sam2/config.yaml
```

当前 mock 后端会生成可用的 mask PNG 和 transparent PNG，方便下游设计链路先跑通。`sam2` 后端需要在分割 worker 上安装 SAM 2 并配置模型文件。

## 示例请求

创建包装项目：

```json
{
  "workflow_type": "packaging",
  "brief": {
    "category": "儿童玩具",
    "target_user": "3-6 岁儿童家庭",
    "user_expectations": ["安全", "好玩", "有教育意义"],
    "value_proposition": "通过可视化互动玩法提升亲子陪伴体验",
    "core_product_definition": "带灯光和配件扩展的互动玩具套装"
  },
  "assets": []
}
```

恢复人工审核：

```json
{
  "action": "approve",
  "reviewer": "designer",
  "selected_usps": {
    "core": [],
    "secondary": []
  },
  "comment": "确认通过"
}
```

## 真实工具接入原则

工具层不让多模态模型独自承担事实抽取：

- PPT/PDF：先做原生结构解析和 OCR，再用多模态模型理解页面语义。
- 图片：PaddleOCR 读文字，SAM 2 做主体分割，多模态模型做语义判断。
- 设计图：GPT Image 2 生成底图，LOGO、文案、警示语、尺寸等用程序化排版叠加。
- 质检：规则检测 + OCR + 视觉模型，不只靠单个 LLM 判断。

## Multimodal image understanding

`POST /api/projects/{project_id}/assets/{asset_id}/analyze` now runs:

1. image role classification;
2. image size reading;
3. OCR via `OCR_BACKEND`;
4. visual understanding via `MULTIMODAL_BACKEND`;
5. audit logging plus memory upsert.

Runtime switches:

```text
MULTIMODAL_BACKEND=mock
MULTIMODAL_BACKEND=gemini
MULTIMODAL_BACKEND=openai
MULTIMODAL_BACKEND=openai_compatible
MULTIMODAL_BASE_URL=https://shiyunapi.com/v1
MULTIMODAL_API_KEY=...
MULTIMODAL_MODEL=gemini-2.5-flash
```

The default mock backend keeps local development fast. Production should use Gemini or OpenAI
for image semantics such as product appearance, accessories, play clues, competitor visual hooks,
packaging hierarchy, detail-page sections, and consistency risks.

For OpenAI-compatible gateways such as Shiyun API, use `MULTIMODAL_BACKEND=openai_compatible`.
If `MULTIMODAL_API_KEY` is empty, the provider falls back to `GEMINI_API_KEY` and then
`OPENAI_API_KEY`, which lets image generation and Gemini-style multimodal use different tokens.

During workflow execution, `parse_inputs` can also analyze uploaded image assets automatically.
This keeps manual upload simple while still feeding product images, competitor images, packaging
references, VI images, and logos into downstream agents.

```text
AUTO_ANALYZE_IMAGES=true
AUTO_ANALYZE_MAX_IMAGES=12
```

## Document parsing

Product PPT/PDF and VI documents use a provider-style parser:

```text
DOCUMENT_PARSER_BACKEND=local
DOCUMENT_PARSER_BACKEND=llamaparse
LLAMA_CLOUD_API_KEY=...
LLAMA_PARSE_TIER=fast
LLAMA_PARSE_VERSION=latest
LLAMA_PARSE_TIMEOUT=120
```

`local` uses `python-pptx` for PPTX and `PyMuPDF` for PDF. `llamaparse` calls LlamaCloud
LlamaParse and normalizes the response into the same `pages[]` contract, so LangGraph nodes keep
using one `parsed_product.parsed_pages` shape regardless of parser backend.

## Structured LLM agents

Marketer, Packaging Director, Detail Page Director, and VI Guardian now call a structured LLM
provider with Pydantic output schemas. Local development uses deterministic fallback output:

```text
LLM_BACKEND=mock
LLM_BACKEND=deepseek
LLM_TEMPERATURE=0.2
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL_STRATEGY=deepseek-v4-pro
DEEPSEEK_MODEL_FAST=deepseek-v4-flash
```

When `LLM_BACKEND=deepseek`, LangChain calls DeepSeek through an OpenAI-compatible chat client and
`with_structured_output(...)`. If the provider fails or a key is missing, the workflow falls back to
the deterministic local object and records the failure in AuditStore under `payload.llm`.

## Image generation

Designer Agent can use a real OpenAI-compatible image provider when external tools are enabled:

```text
MOCK_EXTERNAL_TOOLS=false
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://shiyunapi.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2
IMAGE_GENERATION_BACKEND=auto
IMAGE_GENERATION_REAL_NAMES=front
IMAGE_GENERATION_QUALITY=low
```

The provider first tries image editing with the first available product/reference image, then falls
back to text-to-image generation if the gateway does not expose edit support. If the real provider
fails, the workflow keeps running by falling back to deterministic mock images and records
`image_generation_fallback_used` plus `image_generation_error` in each output `layout_spec`.

`IMAGE_GENERATION_REAL_NAMES` controls cost and rollout. Use `front` for the first packaging face,
`screen_1` for the first detail page screen, comma-separated values for a small batch, or `*` for
all generated outputs.

Generated text, logos, compliance marks, and warning labels should still be overlaid by the layout
composer rather than rendered by the image model. This keeps copy reviewable and avoids unreadable
or hallucinated packaging text.

## Prompt versions and replay

Prompt files under `src/ai_visual_agent/prompts/*.md` are versioned by SHA-256 content hash:

```text
marketer@<first-12-hash-chars>
packaging_director@<first-12-hash-chars>
detail_page_director@<first-12-hash-chars>
vi_guardian@<first-12-hash-chars>
```

Each structured Agent call writes an `agent_run` audit record containing prompt version, prompt
hash, backend, model, input context, output object, summaries, fallback flag, and error message.
This makes DeepSeek output replayable and reviewable after human edits.

## Golden regression fixtures

Golden fixtures live in `fixtures/golden/*.json`. A fixture contains:

- a project brief and seed assets;
- default human-review decisions;
- business-level checks such as output counts, required fields, prompt versions, and final status.

Local/Harness regression entry points:

```text
GET /api/golden/fixtures
POST /api/golden/fixtures/packaging_toy/run
POST /api/golden/fixtures/detail_toy/run
pytest tests/test_golden_regression.py
PYTHONPATH=src python scripts/run_golden_regression.py --json-output artifacts/golden-regression.json
```

The checks intentionally validate structure and key business invariants instead of exact long-form
copy, so prompt wording can improve without breaking CI unless the workflow behavior actually drifts.

## Harness CI/CD gates

The Harness pipeline now runs these gates before building the Docker image:

1. install runtime and infra dependencies;
2. `ruff check src tests`;
3. `python -m compileall src tests`;
4. `pytest tests`;
5. `scripts/run_golden_regression.py` for all golden fixtures.

After staging deployment, the pipeline calls `/health`, lists golden fixtures, and runs both
`packaging_toy` and `detail_toy` through the deployed API before production approval.

## Integration health

`GET /health/integrations` returns configuration and runtime status for:

- `llm`: mock or DeepSeek structured output;
- `multimodal`: mock, Gemini, or OpenAI image understanding;
- `image_generation`: mock or OpenAI image API;
- `ocr`: mock or PaddleOCR;
- `segmentation`: mock or SAM 2;
- `persistence`: memory or PostgreSQL;
- `memory`: in-memory or Qdrant.

For `LLM_BACKEND=deepseek`, missing `DEEPSEEK_API_KEY` is reported as `misconfigured`. If a real
LLM call fails and the workflow falls back to deterministic output, the latest error appears under
the `llm` item as `last_error` with status `degraded`.

`POST /api/integrations/probe` provides the same provider matrix for deployment checks and frontend
operations panels. By default it is a dry-run probe and does not spend external API calls:

```json
{
  "target": "all",
  "active": false,
  "allow_external_call": false
}
```

Set `"target": "llm", "active": true` to execute the local mock LLM probe. For real DeepSeek calls,
the request must also set `"allow_external_call": true` so production smoke tests cannot trigger
paid or rate-limited provider calls accidentally.
