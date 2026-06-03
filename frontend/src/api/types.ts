export type WorkflowType = "packaging" | "detail_page";

export type ConversationWorkflowType = "unknown" | WorkflowType;

export type AssetKind =
  | "product_ppt"
  | "product_pdf"
  | "product_image"
  | "competitor_video"
  | "competitor_image"
  | "competitor_packaging"
  | "competitor_detail_page"
  | "vi_document"
  | "logo"
  | "mask_image"
  | "transparent_product_image"
  | "other";

export type ReviewStatus = "pending" | "approved" | "edited" | "rejected" | "needs_more_info";

export type ReviewAction = "approve" | "edit" | "reject" | "request_more_info";

export interface HealthResponse {
  status: string;
  llm_backend: string;
  deepseek_model_strategy?: string;
  integration_status?: string;
  integration_warnings?: number;
  auth_enabled?: boolean;
  admin_email?: string;
}

export interface AuthLoginRequest {
  email: string;
  password: string;
}

export interface AuthUser {
  id: string;
  email: string;
  role: "admin" | "member";
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: "bearer";
  user: AuthUser;
}

export interface BackgroundTask {
  id: string;
  kind: string;
  project_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | string;
  payload?: Record<string, unknown>;
  error?: string;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  heartbeat_at?: string | null;
}

export interface ConversationSummary {
  id: string;
  project_id: string;
  title: string;
  workflow_type: ConversationWorkflowType;
  current_stage: string;
  updated_at: string;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | "system" | string;
  message_type:
    | "text"
    | "planner_decision"
    | "tool_call"
    | "tool_result"
    | "review_gate"
    | "review_action"
    | "status"
    | string;
  content: string;
  payload?: Record<string, unknown>;
  created_at: string;
}

export interface ReviewGate {
  id: string;
  type: string;
  title: string;
  summary: string;
  payload: Record<string, unknown>;
  status: ReviewStatus;
  allowed_actions?: ReviewAction[];
  next_step_on_approve?: string;
  created_by_agent?: string;
  created_at?: string;
}

export interface AssetRef {
  id: string;
  kind: AssetKind;
  filename: string;
  uri: string;
  mime_type?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ConversationDetail {
  session: ConversationSummary;
  project?: {
    id: string;
    workflow_type: WorkflowType;
    status: string;
  };
  messages: ConversationMessage[];
  review_gates: ReviewGate[];
  pending_review_gate?: ReviewGate | null;
  confirmed_context?: Record<string, unknown>;
  assets: AssetRef[];
}

export interface CreateConversationRequest {
  initial_message: string;
  title?: string;
  workflow_type?: ConversationWorkflowType;
}

export interface SendMessageRequest {
  content: string;
  payload?: Record<string, unknown>;
}

export interface ReviewGateActionRequest {
  action: ReviewAction;
  comment?: string;
  edited_payload?: Record<string, unknown> | null;
  reviewer?: string;
}

export interface ConversationBatchDeleteRequest {
  session_ids: string[];
}

export interface ConversationBatchDeleteResult {
  requested_count: number;
  deleted_count: number;
  deleted_project_ids: string[];
  errors: Array<{ session_id: string; error: string }>;
}

export type KnowledgeDomain = "packaging" | "detail_page" | "visual" | "general";
export type KnowledgeWorkflowType = "all" | "packaging" | "detail_page";
export type KnowledgeStatus = "active" | "inactive" | "draft";

export interface KnowledgeBaseEntry {
  id: string;
  title: string;
  domain: KnowledgeDomain;
  workflow_type: KnowledgeWorkflowType;
  category: string;
  content: Record<string, unknown>;
  tags: string[];
  keywords: string[];
  status: KnowledgeStatus;
  priority: number;
  source?: string;
  created_at?: string;
  updated_at?: string;
}

export type KnowledgeBaseCreateRequest = Omit<KnowledgeBaseEntry, "id" | "created_at" | "updated_at">;

export type KnowledgeBaseUpdateRequest = Partial<KnowledgeBaseCreateRequest>;

export interface ProjectKnowledgePreviewResponse {
  project_id: string;
  context: string;
  results: Array<{
    entry: KnowledgeBaseEntry;
    score: number;
    matched_fields: string[];
  }>;
}
