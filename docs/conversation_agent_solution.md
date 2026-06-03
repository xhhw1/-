# 对话式多智能体工作台方案

更新时间：2026-05-26

## 1. 产品形态

当前产品形态重构为“对话式多智能体工作台”。

新建项目不再是填写一组固定表单，而是新建一场项目对话。用户在对话中输入项目描述、上传产品资料、补充竞品资料和品牌资料；系统由主 Agent 判断当前任务，选择工具和分 Agent 执行，并在关键节点弹出人工确认卡片。

核心目标：

- 让用户以自然语言启动包装概念或详情页提案流程。
- 让主 Agent 决定下一步，而不是由页面固定按钮驱动。
- 让每个分 Agent 的输出都结构化、可审核、可修改、可回退。
- 让人工确认后的结果成为后续节点唯一可信上下文。
- 让资料、卖点、策略、VI、生成图、质检报告沉淀为项目记忆。

## 2. 前端交互

页面重构为三块：

| 区域 | 作用 |
| --- | --- |
| 左侧会话列表 | 展示项目对话，支持新建、切换、归档 |
| 中间对话流 | 展示用户消息、Agent 回复、工具调用状态、结构化结果卡片、确认卡片、生成图卡片 |
| 右侧项目记忆 | 展示已确认项目定义、资料解析结果、核心卖点、包装策略、详情页策略、VI 规范、产品参考图 |

底部输入区支持：

- 文本输入。
- 文件上传。
- 当前节点快捷动作，例如“确认进入下一步”“退回重做”“补充品牌资料”。

对话流里的关键卡片：

- 资料解析确认卡。
- 卖点确认卡。
- 包装策略确认卡。
- 详情页策略确认卡。
- VI 规范确认卡。
- 出图结果确认卡。
- 质检报告卡。

## 3. 总体流程

```text
新建对话
  ↓
用户输入项目描述 + 上传资料
  ↓
主 Agent 判断任务类型和当前缺口
  ↓
调用工具解析资料
  ↓
资料解析确认卡
  ↓
卖点提取 Agent
  ↓
卖点确认卡
  ↓
包装策略 Agent 或详情页策略 Agent
  ↓
策略确认卡
  ↓
VI 理解 Agent + 品牌资料解析
  ↓
包装出图 Agent 或详情页出图 Agent
  ↓
质检 Agent
  ↓
最终确认卡
  ↓
归档输出
```

流程不是死工作流。主 Agent 可以根据用户输入决定只做卖点、只做包装策略、只做详情页策略、补充解析资料、重新生成某一面包装图，或者回退某个节点。

## 4. 主 Agent

模型：DeepSeek-V4-Flash。

角色：

主 Agent 是项目总控、任务规划者和状态调度者。它不直接完成所有专业任务，而是判断当前应该调用哪个分 Agent 或工具。

输入上下文：

- 用户当前消息。
- 当前会话状态。
- 已上传文件摘要。
- 已确认项目记忆。
- 当前待审核卡片。
- 最近工具调用结果。
- 历史人工修改和退回意见。

主 Agent 输出结构：

```json
{
  "intent": "start_packaging_workflow",
  "next_action": "call_agent",
  "target_agent": "usp_agent",
  "required_tools": ["document_parser", "image_understanding"],
  "need_human_review": true,
  "review_gate_type": "usp_review",
  "message_to_user": "我会先解析资料并提炼卖点，完成后请你确认。",
  "state_patch": {},
  "reason": "用户已提供包装项目描述和产品资料，下一步应提炼卖点。"
}
```

主 Agent 限制：

- 不编造资料中不存在的产品功能、认证和参数。
- 不跳过人工确认进入下一关键节点。
- 不直接覆盖人工确认过的信息，除非用户明确要求修改。
- 调用真实图像生成前必须确认可用产品参考图和品牌资料。
- 当资料不足时，优先提出缺口，而不是强行生成。

## 5. 分 Agent 定义

### 5.1 资料解析 Agent

职责：

- 解析产品 PPT/PDF。
- 解析产品图片。
- 解析竞品图片、竞品包装、竞品详情页截图。
- 接入 OCR、图片理解、抠图、视频关键帧分析等工具。

输入：

- 项目描述。
- 上传文件。
- 文件类型和用户标注。

限制：

- 每个结论必须尽量保留来源文件和页码/图片 ID。
- 对不确定字段标记为 `unknown` 或进入 `missing_fields`。
- 不将竞品信息误认为本品信息。

输出：

```json
{
  "product_metadata": {
    "category": "",
    "product_name": "",
    "dimensions": [],
    "accessories": [],
    "play_methods": [],
    "visual_features": [],
    "missing_fields": []
  },
  "competitor_insights": {},
  "vi_candidates": {},
  "source_map": []
}
```

### 5.2 卖点提取 Agent

职责：

基于项目描述、用户关注指标、产品资料和竞品资料，提炼有竞争力的核心卖点和次要卖点。

内部角色：

你是电商产品卖点策略专家，负责从产品资料、竞品资料、用户关注指标中提炼能支撑包装和详情页视觉表达的卖点。

限制：

- 核心卖点必须 1-3 条。
- 次要卖点必须 1-3 条。
- 核心卖点必须对齐用户关注指标或品类期待。
- 必须说明是否体现产品核心功能或视觉。
- 必须说明和爆款竞品相比的竞争力。
- 不允许使用无证据的夸张词、认证、参数。
- 不允许只输出泛泛营销口号。

输出：

```json
{
  "core": [
    {
      "title": "",
      "description": "",
      "aligned_expectations": [],
      "product_evidence": [],
      "competitor_comparison": "",
      "confidence": 0.0
    }
  ],
  "secondary": [
    {
      "title": "",
      "description": "",
      "aligned_expectations": [],
      "product_evidence": [],
      "competitor_comparison": "",
      "confidence": 0.0
    }
  ],
  "notes": []
}
```

### 5.3 包装策略 Agent

职责：

基于确认后的卖点、产品资料、竞品资料和 VI 信息，输出包装四面策略。

限制：

- 必须覆盖正面、左侧、右侧、背面。
- 必须说明产品展示形态、画面布局、背景、文案、标识、LOGO 和品名位置。
- 必须避免产品外观、配件数量、颜色和结构不一致。
- 文案、LOGO、警告语默认建议程序化叠加，不依赖生图模型直接生成可读文字。

输出：

```json
{
  "product_name": "",
  "box_type": "",
  "front_ratio": "",
  "side_ratio": "",
  "top_ratio": "",
  "overall_tone": "",
  "front_layout": "",
  "left_layout": "",
  "right_layout": "",
  "back_layout": "",
  "required_copy": [],
  "required_icons": [],
  "risk_notes": []
}
```

### 5.4 详情页策略 Agent

职责：

基于确认后的卖点、产品资料、竞品详情页和目标平台，输出详情页五屏策略。

限制：

- 必须输出第一屏到第五屏。
- 每屏必须有目标、视觉表达、文案、产品角度和证明点。
- 首屏服务点击和第一眼吸引，后续屏服务理解、信任和转化。

输出：

```json
{
  "page_theme": "",
  "screens": [
    {
      "screen_index": 1,
      "goal": "",
      "visual": "",
      "copy_text": "",
      "product_angle": "",
      "proof_points": []
    }
  ],
  "traffic_platform_notes": "",
  "risk_notes": []
}
```

### 5.5 VI 理解 Agent

职责：

解析品牌色、LOGO、版式、字体、禁用规则和品牌视觉倾向。

限制：

- 未解析到的 VI 规则必须标记缺失。
- 不允许凭空创建品牌规范。
- 输出必须供出图 Agent 和质检 Agent 使用。

输出：

```json
{
  "brand_colors": [],
  "logo_asset_id": "",
  "typography_notes": "",
  "layout_rules": [],
  "forbidden_rules": [],
  "source_asset_ids": []
}
```

### 5.6 包装出图 Agent

职责：

根据确认后的包装策略、产品参考图、VI 信息进行包装四面图生图。

限制：

- 必须使用人工确认或系统优先级最高的产品参考图。
- 不允许改变产品主体结构、颜色、核心配件和关键视觉。
- 图像模型只负责视觉底图和产品氛围，文字、LOGO、安规信息后续由程序化排版叠加。
- 每次真实生图必须记录模型、prompt、参考图、失败原因和 fallback 状态。

输出：

```json
{
  "items": [
    {
      "name": "front",
      "asset_id": "",
      "uri": "",
      "prompt": "",
      "layout_spec": {}
    }
  ],
  "revision_round": 0
}
```

### 5.7 详情页出图 Agent

职责：

根据详情页五屏策略、产品参考图和 VI 信息生成详情页视觉图。

限制：

- 支持单屏生成和长图拼接。
- 文字默认不交给图像模型生成。
- 必须保留每屏对应的策略来源。

输出与包装出图 Agent 一致，`name` 使用 `screen_1` 到 `screen_5`。

### 5.8 质检 Agent

职责：

检查生成图是否满足产品一致性、VI 规范、策略完整性和基础合规要求。

限制：

- 必须对比原产品参考图和生成图。
- 必须指出产品形变、配件错乱、颜色偏差、文字风险、LOGO 风险。
- 不合格时必须输出可执行的修改建议。

输出：

```json
{
  "passed": false,
  "score": 0.0,
  "issues": [
    {
      "severity": "high",
      "category": "product_consistency",
      "message": "",
      "suggested_fix": ""
    }
  ],
  "summary": ""
}
```

## 6. 工具层

工具不属于某个固定页面模块，而是由主 Agent 或分 Agent 根据需要调用。

| 工具 | 用途 |
| --- | --- |
| document_parser | PPT/PDF 解析，优先 LlamaParse，local fallback |
| image_understanding | 产品图、竞品图、包装图、详情页截图理解 |
| ocr | 识别图片中文字、包装文案、详情页文案 |
| segmentation | 产品主体抠图，后续接 SAM 2 |
| video_analysis | 竞品视频抽帧、视觉钩子和卖点节奏分析 |
| memory_search | 检索项目记忆、品牌记忆、历史反馈、成功案例 |
| image_generation | GPT-Image-2 或 OpenAI-compatible 图生图 |
| visual_qc | 产品一致性、VI 合规、OCR 文案和布局质检 |

## 7. 人工确认机制

所有关键节点通过统一 ReviewGate 卡片处理。

ReviewGate 数据结构：

```json
{
  "id": "",
  "type": "usp_review",
  "title": "",
  "summary": "",
  "payload": {},
  "allowed_actions": ["approve", "edit", "reject", "request_more_info"],
  "next_step_on_approve": "packaging_strategy",
  "created_by_agent": "usp_agent"
}
```

用户动作：

- `approve`：确认无误，写入有效项目记忆，自动进入下一步。
- `edit`：用户修改结构化内容，修改后的内容成为有效版本。
- `reject`：退回对应 Agent，携带用户意见重新生成。
- `request_more_info`：暂停流程，等待用户补充资料。

确认后的信息写入：

- `confirmed_project_brief`
- `confirmed_parsed_metadata`
- `confirmed_usps`
- `confirmed_packaging_strategy`
- `confirmed_detail_page_strategy`
- `confirmed_vi_profile`
- `confirmed_reference_assets`
- `confirmed_outputs`

## 8. 状态与记忆

### 8.1 会话状态

会话状态保存当前项目正在做什么。

```json
{
  "session_id": "",
  "project_id": "",
  "workflow_type": "packaging",
  "current_stage": "usp_review",
  "confirmed_context": {},
  "pending_review_gate": {},
  "uploaded_assets": [],
  "tool_results": [],
  "revision_round": 0
}
```

### 8.2 对话时间线

所有用户消息、Agent 消息、工具调用、审核动作都写入 timeline。

消息类型：

- `user_message`
- `agent_message`
- `tool_call`
- `tool_result`
- `review_gate`
- `review_action`
- `generated_output`
- `qc_report`
- `archive_record`

### 8.3 长期记忆

长期记忆使用 Qdrant，存储：

- 品牌 VI 规范。
- 历史人工审核反馈。
- 竞品洞察。
- 同品类卖点案例。
- 成功包装策略。
- 质检失败原因和修正建议。

## 9. LangGraph 重构方向

LangGraph 不再只做线性固定流程，而是作为多智能体执行内核。

建议图结构：

```text
conversation_input
  ↓
planner_agent
  ↓
route_by_planner_decision
  ├─ tool_executor
  ├─ parser_agent
  ├─ usp_agent
  ├─ packaging_strategy_agent
  ├─ detail_strategy_agent
  ├─ vi_agent
  ├─ packaging_designer_agent
  ├─ detail_designer_agent
  ├─ critic_agent
  └─ review_gate
        ↓
      state_commit
        ↓
      planner_agent 或 archive
```

主 Agent 每轮都可以重新判断下一步，分 Agent 负责专业输出，ReviewGate 负责人工确认和阻断。

## 10. 当前项目迁移原则

保留：

- 现有 FastAPI 服务。
- 现有 LangGraph 节点逻辑。
- 现有 DeepSeek 结构化输出能力。
- 现有 LlamaParse、多模态、图像生成、项目存储、素材存储。
- 现有人工审核、归档、输出版本能力。

重构：

- 前端从审核台重构为对话式工作台。
- 固定按钮驱动改为主 Agent 规划驱动。
- 项目详情页状态改为右侧项目记忆。
- 审核表单改为对话中的确认卡片。
- 分 Agent 由固定节点变成可被 Planner 调用的能力单元。
