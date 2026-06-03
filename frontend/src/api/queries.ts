import { apiRequest } from "./client";
import type {
  AssetKind,
  AuthLoginRequest,
  AuthTokenResponse,
  AuthUser,
  BackgroundTask,
  ConversationBatchDeleteRequest,
  ConversationBatchDeleteResult,
  ConversationDetail,
  CreateConversationRequest,
  HealthResponse,
  KnowledgeBaseCreateRequest,
  KnowledgeBaseEntry,
  KnowledgeBaseUpdateRequest,
  ProjectKnowledgePreviewResponse,
  ReviewGateActionRequest,
  SendMessageRequest
} from "./types";

export function getHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>("/health", {}, { retries: 2, timeoutMs: 10000 });
}

export function login(payload: AuthLoginRequest): Promise<AuthTokenResponse> {
  return apiRequest<AuthTokenResponse>(
    "/api/auth/login",
    {
      method: "POST",
      body: JSON.stringify(payload)
    },
    { retries: 0, timeoutMs: 30000 }
  );
}

export function getCurrentUser(): Promise<AuthUser> {
  return apiRequest<AuthUser>("/api/auth/me", {}, { retries: 0, timeoutMs: 10000 });
}

export function listBackgroundTasks(projectId?: string): Promise<BackgroundTask[]> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return apiRequest<BackgroundTask[]>(`/api/tasks${query}`, {}, { retries: 1, timeoutMs: 10000 });
}

export function cancelBackgroundTask(jobId: string): Promise<BackgroundTask> {
  return apiRequest<BackgroundTask>(
    `/api/tasks/${jobId}/cancel`,
    { method: "POST" },
    { retries: 0, timeoutMs: 30000 }
  );
}

export function retryBackgroundTask(jobId: string): Promise<BackgroundTask> {
  return apiRequest<BackgroundTask>(
    `/api/tasks/${jobId}/retry`,
    { method: "POST" },
    { retries: 0, timeoutMs: 30000 }
  );
}

export function listConversations(): Promise<ConversationDetail[]> {
  return apiRequest<ConversationDetail[]>("/api/conversations", {}, { retries: 2 });
}

export function getConversationDetail(id: string): Promise<ConversationDetail> {
  return apiRequest<ConversationDetail>(`/api/conversations/${id}`, {}, { retries: 2 });
}

export function createConversation(payload: CreateConversationRequest): Promise<ConversationDetail> {
  return apiRequest<ConversationDetail>(
    "/api/conversations",
    {
      method: "POST",
      body: JSON.stringify(payload)
    },
    { retries: 0, timeoutMs: 120000 }
  );
}

export function deleteConversation(id: string): Promise<null> {
  return apiRequest<null>(`/api/conversations/${id}`, {
    method: "DELETE"
  });
}

export function batchDeleteConversations(
  payload: ConversationBatchDeleteRequest
): Promise<ConversationBatchDeleteResult> {
  return apiRequest<ConversationBatchDeleteResult>(
    "/api/conversations/batch-delete",
    {
      method: "POST",
      body: JSON.stringify(payload)
    },
    { retries: 0, timeoutMs: 120000 }
  );
}

export function sendConversationMessage(id: string, payload: SendMessageRequest): Promise<ConversationDetail> {
  return apiRequest<ConversationDetail>(
    `/api/conversations/${id}/messages`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    },
    { retries: 0, timeoutMs: 120000 }
  );
}

export function uploadConversationAsset(
  id: string,
  kind: AssetKind,
  file: File
): Promise<ConversationDetail> {
  const formData = new FormData();
  formData.append("kind", kind);
  formData.append("file", file);
  return apiRequest<ConversationDetail>(
    `/api/conversations/${id}/assets`,
    {
      method: "POST",
      body: formData
    },
    { retries: 0, timeoutMs: 120000 }
  );
}

export function submitReviewGateAction(
  conversationId: string,
  gateId: string,
  payload: ReviewGateActionRequest
): Promise<ConversationDetail> {
  return apiRequest<ConversationDetail>(
    `/api/conversations/${conversationId}/review-gates/${gateId}/actions`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    },
    { retries: 0, timeoutMs: 120000 }
  );
}

export function listKnowledgeEntries(): Promise<KnowledgeBaseEntry[]> {
  return apiRequest<KnowledgeBaseEntry[]>("/api/knowledge", {}, { retries: 2 });
}

export function createKnowledgeEntry(payload: KnowledgeBaseCreateRequest): Promise<KnowledgeBaseEntry> {
  return apiRequest<KnowledgeBaseEntry>("/api/knowledge", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function updateKnowledgeEntry(id: string, payload: KnowledgeBaseUpdateRequest): Promise<KnowledgeBaseEntry> {
  return apiRequest<KnowledgeBaseEntry>(`/api/knowledge/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function deleteKnowledgeEntry(id: string): Promise<KnowledgeBaseEntry> {
  return apiRequest<KnowledgeBaseEntry>(`/api/knowledge/${id}`, {
    method: "DELETE"
  });
}

export function previewProjectKnowledge(projectId: string): Promise<ProjectKnowledgePreviewResponse> {
  return apiRequest<ProjectKnowledgePreviewResponse>(
    `/api/projects/${projectId}/knowledge/preview`,
    { method: "POST" },
    { timeoutMs: 60000 }
  );
}
