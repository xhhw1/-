from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


WorkflowType = Literal["packaging", "detail_page"]
KnowledgeWorkflowType = Literal["all", "packaging", "detail_page"]
KnowledgeStatus = Literal["active", "inactive", "draft"]
KnowledgeDomain = Literal["packaging", "detail_page", "visual", "general"]
ConversationWorkflowType = Literal["unknown", "packaging", "detail_page"]
ConversationRole = Literal["user", "agent", "system", "tool"]
ConversationMessageType = Literal[
    "text",
    "planner_decision",
    "tool_call",
    "tool_result",
    "review_gate",
    "review_action",
    "status",
]
ConversationReviewAction = Literal["approve", "edit", "reject", "request_more_info"]
GoldenCheckOperator = Literal["exists", "equals", "contains", "startswith", "min_count"]
IntegrationStatus = Literal["ready", "mock", "misconfigured", "degraded", "unknown"]
ReadinessCheckStatus = Literal["ready", "skipped", "failed"]
IntegrationProbeTarget = Literal[
    "all",
    "llm",
    "multimodal",
    "image_generation",
    "document_parser",
    "ocr",
    "segmentation",
    "persistence",
    "memory",
]
AssetKind = Literal[
    "product_ppt",
    "product_pdf",
    "product_image",
    "competitor_video",
    "competitor_image",
    "competitor_packaging",
    "competitor_detail_page",
    "vi_document",
    "logo",
    "mask_image",
    "transparent_product_image",
    "other",
]
ReviewAction = Literal["approve", "edit", "reject"]
AuditRecordType = Literal[
    "conversation",
    "human_review",
    "agent_output",
    "agent_run",
    "qc_report",
    "archive_record",
]
MemoryType = Literal["asset_registry", "brand_vi", "product_doc", "competitor", "feedback", "case", "prompt", "other"]


class AssetRef(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: AssetKind
    filename: str
    uri: str
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetUpdateRequest(BaseModel):
    kind: AssetKind | None = None
    filename: str | None = None
    metadata: dict[str, Any] | None = None


class OCRBlock(BaseModel):
    text: str
    confidence: float = Field(default=0.0, ge=0, le=1)
    bbox: list[float] = Field(default_factory=list)


class OCRResult(BaseModel):
    image_id: str
    image_uri: str
    engine: str
    language: str = "ch"
    blocks: list[OCRBlock] = Field(default_factory=list)
    full_text: str = ""


class ImageUnderstandingResult(BaseModel):
    image_id: str
    image_uri: str
    engine: str
    image_role: str
    summary: str = ""
    product_appearance: list[str] = Field(default_factory=list)
    visible_accessories: list[str] = Field(default_factory=list)
    play_clues: list[str] = Field(default_factory=list)
    competitor_visual_hooks: list[str] = Field(default_factory=list)
    packaging_hierarchy: list[str] = Field(default_factory=list)
    detail_page_sections: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ImageAssetAnalysis(BaseModel):
    asset_id: str
    image_uri: str
    image_role: str
    width: int | None = None
    height: int | None = None
    ocr: OCRResult
    understanding: ImageUnderstandingResult | None = None
    semantic_summary: str = ""
    tags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SegmentationQuality(BaseModel):
    edge_residue: Literal["low", "medium", "high", "unknown"] = "unknown"
    needs_manual_trim: bool = True
    foreground_ratio: float | None = Field(default=None, ge=0, le=1)


class SegmentationResult(BaseModel):
    image_id: str
    image_uri: str
    engine: str
    mode: str = "auto"
    mask_asset: AssetRef
    transparent_asset: AssetRef
    quality: SegmentationQuality = Field(default_factory=SegmentationQuality)


class ProjectBrief(BaseModel):
    category: str = ""
    target_user: str = ""
    user_expectations: list[str] = Field(default_factory=list)
    user_metrics: list[str] = Field(default_factory=list)
    value_proposition: str = ""
    core_product_definition: str = ""
    raw_text: str = ""


class ProjectCreateRequest(BaseModel):
    workflow_type: WorkflowType
    brief: ProjectBrief
    assets: list[AssetRef] = Field(default_factory=list)
    owner_id: str = ""


class ProjectUpdateRequest(BaseModel):
    workflow_type: WorkflowType | None = None
    brief: ProjectBrief | None = None


class ProjectRecord(ProjectCreateRequest):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "created"


class ProjectProgressSummary(BaseModel):
    completed_stages: list[str] = Field(default_factory=list)
    current_stage: str = "created"
    audit_count: int = 0
    human_review_count: int = 0
    agent_run_count: int = 0
    asset_count: int = 0


class ProjectDetailResponse(BaseModel):
    project: ProjectRecord
    progress: ProjectProgressSummary
    pending_review: dict[str, Any] | None = None
    file_memory_context: list[dict[str, Any]] = Field(default_factory=list)
    workflow_requirements: dict[str, Any] = Field(default_factory=dict)
    latest_agent_outputs: dict[str, Any] = Field(default_factory=dict)
    latest_generated_outputs: dict[str, Any] = Field(default_factory=dict)
    latest_qc_report: dict[str, Any] = Field(default_factory=dict)
    latest_archive: dict[str, Any] = Field(default_factory=dict)
    audit_records: list["AuditRecord"] = Field(default_factory=list)


class AgentChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    role: Literal["user", "agent", "system", "tool"] = "agent"
    message_type: Literal["text", "review_gate", "tool_result", "status"] = "text"
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentChatRequest(BaseModel):
    message: str
    reviewer: str = "user"
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentChatResponse(BaseModel):
    project_id: str
    decision: str = ""
    messages: list[AgentChatMessage] = Field(default_factory=list)
    project_detail: ProjectDetailResponse
    workflow_result: "WorkflowResult | None" = None


class PlannerDecision(BaseModel):
    intent: str = "status"
    next_action: Literal[
        "ask_user",
        "call_agent",
        "call_tool",
        "create_review_gate",
        "start_workflow",
        "status",
    ] = "status"
    target_agent: str = ""
    required_tools: list[str] = Field(default_factory=list)
    need_human_review: bool = False
    review_gate_type: str = ""
    message_to_user: str = ""
    state_patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ConversationSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    owner_id: str = ""
    title: str = "未命名对话"
    workflow_type: ConversationWorkflowType = "unknown"
    status: Literal["active", "archived"] = "active"
    current_stage: str = "collecting_input"
    confirmed_context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationCreateRequest(BaseModel):
    title: str = ""
    initial_message: str = ""
    workflow_type: ConversationWorkflowType = "unknown"


class ConversationMessageCreateRequest(BaseModel):
    content: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ConversationMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    role: ConversationRole = "agent"
    message_type: ConversationMessageType = "text"
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationReviewGate(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    type: str
    title: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "approved", "edited", "rejected", "needs_more_info"] = "pending"
    allowed_actions: list[ConversationReviewAction] = Field(
        default_factory=lambda: ["approve", "edit", "reject"]
    )
    next_step_on_approve: str = ""
    created_by_agent: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None


class ConversationReviewActionRequest(BaseModel):
    action: ConversationReviewAction
    edited_payload: dict[str, Any] | None = None
    comment: str = ""
    reviewer: str = "user"


class ConversationBatchDeleteRequest(BaseModel):
    session_ids: list[str] = Field(default_factory=list)


class ConversationBatchDeleteResult(BaseModel):
    requested_count: int = 0
    deleted_count: int = 0
    deleted_project_ids: list[str] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class AuthUser(BaseModel):
    id: str
    email: str
    role: Literal["admin", "member"] = "admin"
    status: Literal["active", "disabled"] = "active"


class AuthUserRecord(AuthUser):
    password_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthUserCreateRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: Literal["admin", "member"] = "member"


class AuthUserUpdateRequest(BaseModel):
    password: str | None = Field(default=None, min_length=8)
    role: Literal["admin", "member"] | None = None
    status: Literal["active", "disabled"] | None = None


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser


class ConversationDetailResponse(BaseModel):
    session: ConversationSession
    project: ProjectRecord
    messages: list[ConversationMessage] = Field(default_factory=list)
    review_gates: list[ConversationReviewGate] = Field(default_factory=list)
    pending_review_gate: ConversationReviewGate | None = None
    confirmed_context: dict[str, Any] = Field(default_factory=dict)
    assets: list[AssetRef] = Field(default_factory=list)


class ParsedPage(BaseModel):
    page_index: int
    title: str | None = None
    text: str = ""
    ocr_text: str = ""
    image_asset_ids: list[str] = Field(default_factory=list)
    semantic_summary: str = ""


class ProductMetadata(BaseModel):
    category: str = ""
    product_name: str = ""
    dimensions: list[str] = Field(default_factory=list)
    accessories: list[str] = Field(default_factory=list)
    play_methods: list[str] = Field(default_factory=list)
    hero_image_asset_id: str | None = None
    visual_features: list[str] = Field(default_factory=list)
    fact_sources: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    parsed_pages: list[ParsedPage] = Field(default_factory=list)


class CompetitorItem(BaseModel):
    name: str = ""
    asset_id: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    visual_hooks: list[str] = Field(default_factory=list)
    price_or_sales_signal: str = ""
    risks_to_avoid: list[str] = Field(default_factory=list)


class CompetitorInsights(BaseModel):
    summary: str = ""
    competitors: list[CompetitorItem] = Field(default_factory=list)
    opportunity_gaps: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class USPUserAlignment(BaseModel):
    parent: str = ""
    child: str = ""


class USPCompetitorComparisonRow(BaseModel):
    dimension: str = ""
    competitor: str = ""
    our_product: str = ""


class USPVisualUsage(BaseModel):
    package_headline: str = ""
    short_tags: list[str] = Field(default_factory=list)
    visual_event: str = ""
    required_visual_elements: list[str] = Field(default_factory=list)
    recommended_package_area: str = ""


class USPItem(BaseModel):
    title: str = ""
    description: str = ""
    aligned_expectations: list[str] = Field(default_factory=list)
    product_evidence: list[str] = Field(default_factory=list)
    competitor_comparison: str = ""
    confidence: float = Field(default=0.7, ge=0, le=1)
    headline: str = ""
    angle: str = ""
    content: str = ""
    user_alignment: USPUserAlignment = Field(default_factory=USPUserAlignment)
    product_visual_evidence: str = ""
    competitor_comparison_rows: list[USPCompetitorComparisonRow] = Field(default_factory=list)
    competitiveness_judgement: str = ""
    visual_usage: USPVisualUsage = Field(default_factory=USPVisualUsage)


class USPCandidates(BaseModel):
    core: list[USPItem] = Field(default_factory=list)
    secondary: list[USPItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PackagingStrategy(BaseModel):
    product_name: str = ""
    box_type: str = ""
    front_ratio: str = ""
    side_ratio: str = ""
    top_ratio: str = ""
    overall_tone: str = ""
    front_layout: str = ""
    left_layout: str = ""
    right_layout: str = ""
    back_layout: str = ""
    required_copy: list[str] = Field(default_factory=list)
    required_icons: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class DetailScreenStrategy(BaseModel):
    screen_index: int
    goal: str
    visual: str
    copy_text: str
    product_angle: str = ""
    proof_points: list[str] = Field(default_factory=list)


class DetailPageStrategy(BaseModel):
    page_theme: str = ""
    screens: list[DetailScreenStrategy] = Field(default_factory=list)
    traffic_platform_notes: str = ""
    risk_notes: list[str] = Field(default_factory=list)


class VIProfile(BaseModel):
    brand_colors: list[str] = Field(default_factory=list)
    logo_asset_id: str | None = None
    typography_notes: str = ""
    layout_rules: list[str] = Field(default_factory=list)
    forbidden_rules: list[str] = Field(default_factory=list)
    source_asset_ids: list[str] = Field(default_factory=list)


class MainImagePromptDraft(BaseModel):
    main_image_prompt: str = ""
    negative_prompt: str = ""
    reference_usage: str = ""
    layout_notes: str = ""
    text_overlay_plan: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class GenerationOutputItem(BaseModel):
    name: str
    asset_id: str
    uri: str
    prompt: str
    layout_spec: dict[str, Any] = Field(default_factory=dict)


class GenerationOutput(BaseModel):
    items: list[GenerationOutputItem] = Field(default_factory=list)
    revision_round: int = 0


class QCIssue(BaseModel):
    severity: Literal["low", "medium", "high", "blocking"]
    category: Literal["product_consistency", "vi", "copy", "layout", "compliance", "asset"]
    message: str
    suggested_fix: str = ""


class QCReport(BaseModel):
    passed: bool = True
    score: float = Field(default=0.9, ge=0, le=1)
    issues: list[QCIssue] = Field(default_factory=list)
    summary: str = ""


class HumanReviewInput(BaseModel):
    action: ReviewAction
    reviewer: str = ""
    comment: str = ""
    selected_usps: USPCandidates | None = None
    packaging_strategy: PackagingStrategy | None = None
    detail_page_strategy: DetailPageStrategy | None = None
    requested_changes: list[str] = Field(default_factory=list)


class WorkflowResult(BaseModel):
    project_id: str
    status: str
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)


class PromptVersion(BaseModel):
    name: str
    version: str
    content_hash: str
    path: str
    content: str = ""


class AgentRunRecord(BaseModel):
    project_id: str
    stage: str
    agent_name: str
    prompt_name: str
    prompt_version: str
    prompt_hash: str
    model_backend: str
    model_name: str
    model_role: str = "strategy"
    output_schema: str
    input_context: dict[str, Any] = Field(default_factory=dict)
    input_summary: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    output_summary: str = ""
    fallback_used: bool = False
    error: str | None = None


class GoldenCheck(BaseModel):
    path: str
    operator: GoldenCheckOperator = "exists"
    expected: Any = None


class GoldenCheckResult(GoldenCheck):
    actual: Any = None
    passed: bool = False
    message: str = ""


class GoldenFixtureSummary(BaseModel):
    name: str
    workflow_type: WorkflowType
    description: str = ""
    check_count: int = 0


class GoldenRunResult(BaseModel):
    fixture_name: str
    project_id: str
    workflow_type: WorkflowType
    status: str
    passed: bool
    checks: list[GoldenCheckResult] = Field(default_factory=list)
    agent_run_count: int = 0
    final_state: dict[str, Any] = Field(default_factory=dict)


class IntegrationHealthItem(BaseModel):
    name: str
    backend: str
    status: IntegrationStatus
    configured: bool = True
    ready: bool = True
    message: str = ""
    required_env: list[str] = Field(default_factory=list)
    missing_env: list[str] = Field(default_factory=list)
    model: str | None = None
    last_checked_at: datetime | None = None
    last_error: str | None = None
    fallback_used: bool = False


class IntegrationHealthReport(BaseModel):
    status: Literal["ok", "degraded", "misconfigured"] = "ok"
    items: list[IntegrationHealthItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReadinessCheck(BaseModel):
    name: str
    backend: str
    status: ReadinessCheckStatus
    required: bool = True
    message: str = ""
    latency_ms: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ReadinessReport(BaseModel):
    status: Literal["ready", "not_ready"] = "ready"
    checks: list[ReadinessCheck] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)


class IntegrationProbeRequest(BaseModel):
    target: IntegrationProbeTarget = "all"
    active: bool = False
    allow_external_call: bool = False


class IntegrationProbeResult(BaseModel):
    target: IntegrationProbeTarget
    status: Literal["ok", "degraded", "misconfigured"] = "ok"
    active: bool = False
    allow_external_call: bool = False
    items: list[IntegrationHealthItem] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)


class MemoryUpsertRequest(BaseModel):
    text: str
    memory_type: MemoryType = "other"
    project_id: str | None = None
    brand_id: str | None = None
    category: str | None = None
    workflow_type: WorkflowType | None = None
    asset_id: str | None = None
    source_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)
    memory_type: MemoryType | None = None
    project_id: str | None = None
    brand_id: str | None = None
    category: str | None = None
    workflow_type: WorkflowType | None = None


class MemorySearchResult(BaseModel):
    id: str
    text: str
    score: float
    payload: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    domain: KnowledgeDomain = "general"
    workflow_type: KnowledgeWorkflowType = "all"
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    status: KnowledgeStatus = "active"
    priority: int = Field(default=50, ge=0, le=100)
    content: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeBaseCreateRequest(BaseModel):
    id: str | None = None
    title: str
    domain: KnowledgeDomain = "general"
    workflow_type: KnowledgeWorkflowType = "all"
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    status: KnowledgeStatus = "active"
    priority: int = Field(default=50, ge=0, le=100)
    content: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"


class KnowledgeBaseUpdateRequest(BaseModel):
    title: str | None = None
    domain: KnowledgeDomain | None = None
    workflow_type: KnowledgeWorkflowType | None = None
    category: str | None = None
    tags: list[str] | None = None
    keywords: list[str] | None = None
    status: KnowledgeStatus | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    content: dict[str, Any] | None = None
    source: str | None = None


class KnowledgeSearchRequest(BaseModel):
    query: str = ""
    workflow_type: KnowledgeWorkflowType | None = None
    domain: KnowledgeDomain | None = None
    category: str | None = None
    status: KnowledgeStatus | None = "active"
    limit: int = Field(default=5, ge=1, le=20)


class KnowledgeSearchResult(BaseModel):
    entry: KnowledgeBaseEntry
    score: float = 0
    matched_keywords: list[str] = Field(default_factory=list)
    reason: str = ""


class ProjectKnowledgePreviewResponse(BaseModel):
    project_id: str
    query: str = ""
    results: list[KnowledgeSearchResult] = Field(default_factory=list)
    injected_context: dict[str, Any] = Field(default_factory=dict)


class AuditRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    record_type: AuditRecordType
    stage: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
