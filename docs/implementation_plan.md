# 对话式多智能体系统落地计划

更新时间：2026-05-26

## 1. 开发目标

将当前“项目表单 + 审核台 + 固定 LangGraph 流程”重构为“对话式多智能体工作台”。

目标状态：

- 新建项目等同于新建对话。
- 用户通过自然语言和附件驱动任务。
- DeepSeek-V4-Flash 主 Agent 负责规划和调度。
- 卖点、包装策略、详情页策略、出图、质检等能力拆成分 Agent。
- 每个关键节点用 ReviewGate 卡片人工确认。
- 确认后的结构化结果写入项目记忆，并自动进入下一步。

## 2. 里程碑

### 阶段 1：前端对话式工作台

目标：

先把产品体验从审核台切换成对话式工作台。

任务：

1. 重构 `/app/` 页面布局。
2. 左侧改为会话列表。
3. 中间改为对话流。
4. 右侧改为项目记忆面板。
5. 底部改为文本输入 + 附件上传。
6. 删除旧的项目详情模块式展示。
7. 保留必要的项目创建、素材上传、审核确认入口。

验收标准：

- 点击“新建项目”后创建一场空对话。
- 用户可以输入一段项目描述。
- 用户可以在对话中上传 PPT、PDF、产品图、竞品图、VI、LOGO。
- 页面不再以“素材管理、审核台、输出预览”并列模块作为主体验。

### 阶段 2：会话数据模型和 API

目标：

让后端支持对话消息、工具调用、确认卡片和项目记忆。

建议数据结构：

```text
conversation_sessions
conversation_messages
conversation_tool_runs
conversation_review_gates
project_confirmed_context
```

API 草案：

```text
POST /api/conversations
GET  /api/conversations
GET  /api/conversations/{id}
POST /api/conversations/{id}/messages
POST /api/conversations/{id}/assets
POST /api/conversations/{id}/review-gates/{gate_id}/actions
GET  /api/conversations/{id}/memory
```

验收标准：

- 对话消息可持久化。
- 文件上传后可以关联到当前会话。
- ReviewGate 可以创建、确认、修改、退回。
- 已确认上下文可以独立查询。

### 阶段 3：主 Agent Planner

目标：

使用 DeepSeek-V4-Flash 做主 Agent，让它根据对话和项目状态判断下一步。

任务：

1. 新增 `PlannerAgent` 服务。
2. 新增 Planner 结构化输出 schema。
3. 接入 DeepSeek-V4-Flash。
4. 将当前用户消息、项目记忆、上传资料、待审核卡片作为 Planner 输入。
5. Planner 输出 `intent`、`next_action`、`target_agent`、`required_tools`、`need_human_review`、`state_patch`。

Planner 输出 schema：

```json
{
  "intent": "",
  "next_action": "call_agent",
  "target_agent": "",
  "required_tools": [],
  "need_human_review": true,
  "review_gate_type": "",
  "message_to_user": "",
  "state_patch": {},
  "reason": ""
}
```

验收标准：

- 用户输入“这是一个玩具包装项目...”后，Planner 能判断应进入包装流程。
- 用户输入“先帮我提炼卖点”后，Planner 能只调用卖点 Agent。
- 用户输入“这个卖点不对，重新强调安全性”后，Planner 能判断退回卖点 Agent。

### 阶段 4：分 Agent 能力封装

目标：

把现有 LangGraph 节点包装成可被 Planner 调用的分 Agent。

优先封装：

1. `ParserAgent`
2. `USPAgent`
3. `PackagingStrategyAgent`
4. `DetailPageStrategyAgent`
5. `VIGuardianAgent`
6. `PackagingDesignerAgent`
7. `DetailPageDesignerAgent`
8. `CriticAgent`

每个 Agent 必须有：

- 角色定义。
- 输入 schema。
- 输出 schema。
- 限制条件。
- fallback 策略。
- 结果写入 timeline 和项目记忆的规则。

验收标准：

- Planner 可以通过统一接口调用任意分 Agent。
- 分 Agent 输出都可以生成 ReviewGate。
- 分 Agent 失败时不导致整个会话崩溃，而是给出错误卡片或 fallback 结果。

### 阶段 5：ReviewGate 确认卡片

目标：

将人工审核从页面固定区域改成对话流里的确认卡片。

任务：

1. 新增 ReviewGate 前端组件。
2. 支持结构化展示。
3. 支持表单化编辑。
4. 支持确认、修改确认、退回重做、补充资料。
5. 用户确认后自动触发 Planner 下一轮。

确认动作：

```json
{
  "action": "approve",
  "edited_payload": {},
  "comment": "",
  "reviewer": "user"
}
```

验收标准：

- 卖点输出后，对话中出现“确认卖点”卡片。
- 用户点确认后，卖点写入 `confirmed_usps`。
- 系统自动进入包装策略或详情页策略。
- 用户修改卡片后，后续节点使用修改后的版本。

### 阶段 6：资料上传和自动解析

目标：

用户在对话中上传资料后，系统自动登记文件、判断文件角色、调用解析工具。

任务：

1. 上传文件后写入素材库。
2. 主 Agent 判断文件用途。
3. 产品 PPT/PDF 调用文档解析。
4. 产品图调用图片理解和候选参考图识别。
5. 竞品图调用竞品视觉分析。
6. VI/LOGO 调用 VI 理解。
7. 解析结果形成“资料解析确认卡”。

验收标准：

- 上传产品 PPT 和产品图后，系统能输出解析摘要。
- 用户能确认或修改尺寸、配件、玩法、产品参考图。
- 确认后写入 `confirmed_parsed_metadata` 和 `confirmed_reference_assets`。

### 阶段 7：出图和质检闭环

目标：

把包装出图和详情页出图变成对话中的可控步骤。

任务：

1. 包装策略确认后，提示用户上传或确认品牌资料。
2. 确认产品参考图。
3. 包装出图 Agent 根据四面策略生成正面、左侧、右侧、背面。
4. 详情页出图 Agent 根据五屏策略生成分屏或长图。
5. 质检 Agent 对比参考图和生成图。
6. 不合格时生成修订建议。
7. 支持单面或单屏重跑。

验收标准：

- 出图前能看到使用的产品参考图。
- 生成图能在对话流中预览和下载。
- 质检报告能指出产品一致性和 VI 风险。
- 用户退回后能带着修改意见重新生成。

### 阶段 8：持久化和长期记忆

目标：

让系统具备长期项目记忆和品牌记忆。

任务：

1. PostgreSQL 存会话、项目、审核、输出版本。
2. LangGraph checkpoint 切换到 PostgreSQL。
3. Qdrant 存品牌 VI、历史反馈、竞品洞察、成功案例。
4. Planner 和分 Agent 调用 memory_search 获取历史上下文。

验收标准：

- 服务重启后会话和项目仍可恢复。
- 同品牌新项目能检索历史 VI 和审核偏好。
- 同品类项目能检索过往卖点和策略经验。

## 3. 推荐开发顺序

第一步：前端对话壳。

- 新建会话列表。
- 对话流组件。
- 底部输入区。
- 右侧项目记忆面板。

第二步：会话 API。

- 新建会话。
- 发消息。
- 查消息。
- 上传附件。
- 返回会话详情。

第三步：Planner Agent。

- 用 DeepSeek-V4-Flash 输出结构化决策。
- 先只支持判断包装流程、详情页流程、卖点提取、状态询问。

第四步：接入卖点 Agent。

- 用户输入项目描述后，Planner 调用卖点 Agent。
- 卖点 Agent 输出 ReviewGate。
- 用户确认后写入 `confirmed_usps`。

第五步：接入包装策略和详情页策略 Agent。

- 根据工作流类型自动进入对应策略。
- 策略输出后进入 ReviewGate。

第六步：接入资料解析。

- 上传文件自动解析。
- 对话流显示工具调用状态。
- 输出资料解析确认卡。

第七步：接入包装出图和质检。

- 先支持包装正面真实图生图。
- 其他面继续 mock 或选择性真实生成。
- 接质检报告卡。

第八步：完善长期记忆和部署。

- PostgreSQL。
- Qdrant。
- Harness 环境变量和发布流程。

## 4. 迁移策略

当前代码可以按以下方式迁移：

| 现有模块 | 迁移方式 |
| --- | --- |
| `graph/nodes.py` | 拆成 Agent callable 或保留为 LangGraph 节点 |
| `orchestrator_agent.py` | 重写为 PlannerAgent，不再靠关键词判断 |
| `project_detail.py` | 部分能力迁移到 conversation detail 和 memory panel |
| `web/app.js` | 大幅重构为对话式 UI |
| `reviewPayloadEditor` | 替换为 ReviewGate 内部结构化编辑状态 |
| `assetPanel` | 变成对话附件栏和右侧资料面板 |
| `workflow_engine.py` | 保留，但从固定 start/resume 转向 Planner 驱动 |
| `memory_store.py` | 保留并升级为项目记忆/长期记忆检索层 |

## 5. 数据模型草案

### ConversationSession

```json
{
  "id": "",
  "project_id": "",
  "title": "",
  "workflow_type": "unknown",
  "status": "active",
  "current_stage": "collecting_input",
  "created_at": "",
  "updated_at": ""
}
```

### ConversationMessage

```json
{
  "id": "",
  "session_id": "",
  "role": "user",
  "message_type": "text",
  "content": "",
  "payload": {},
  "created_at": ""
}
```

### ReviewGate

```json
{
  "id": "",
  "session_id": "",
  "type": "usp_review",
  "title": "",
  "payload": {},
  "status": "pending",
  "allowed_actions": ["approve", "edit", "reject"],
  "created_at": "",
  "resolved_at": ""
}
```

### ConfirmedContext

```json
{
  "session_id": "",
  "confirmed_project_brief": {},
  "confirmed_parsed_metadata": {},
  "confirmed_usps": {},
  "confirmed_packaging_strategy": {},
  "confirmed_detail_page_strategy": {},
  "confirmed_vi_profile": {},
  "confirmed_reference_assets": [],
  "confirmed_outputs": {}
}
```

## 6. 验收路径

第一条可验收路径：

```text
新建对话
  ↓
输入：这是一个玩具包装项目...
  ↓
上传产品 PPT 和产品图
  ↓
系统解析资料并弹出确认卡
  ↓
确认资料
  ↓
卖点 Agent 输出核心/次要卖点
  ↓
确认卖点
  ↓
包装策略 Agent 输出四面策略
  ↓
确认策略
  ↓
上传品牌资料
  ↓
包装出图 Agent 生成正面图
  ↓
质检 Agent 输出报告
  ↓
最终确认并归档
```

最小验收标准：

- 对话式界面跑通。
- DeepSeek-V4-Flash Planner 能判断下一步。
- 卖点卡片能确认并自动进入策略。
- 人工确认结果能写入有效上下文。
- 包装策略能使用确认后的卖点。

## 7. 风险和处理

| 风险 | 处理 |
| --- | --- |
| Planner 判断错误 | 引入结构化输出和状态约束，必要时让用户确认下一步 |
| 分 Agent 编造信息 | 所有输出要求证据字段和 source map |
| 用户修改内容丢失 | 修改后的 ReviewGate payload 写入 confirmed context |
| 图生图成本失控 | 真实生图前确认，默认只生成指定面 |
| 产品形变 | 增加产品一致性质检，失败自动进入修订 |
| 页面复杂度过高 | 对话流展示过程，右侧只放已确认记忆 |

## 8. 下一步执行清单

立即执行：

1. 新建 conversation domain schema。
2. 新建 conversation API。
3. 重构前端 `/app/` 为对话工作台。
4. 新增 PlannerAgent，先只接 DeepSeek-V4-Flash dry run。
5. 将 USPAgent 接入对话，输出 ReviewGate。

后续执行：

1. 接包装策略 Agent。
2. 接详情页策略 Agent。
3. 接资料解析确认卡。
4. 接出图 Agent 和质检 Agent。
5. 接 PostgreSQL 和 Qdrant 的生产记忆。
