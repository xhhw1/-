import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Paperclip } from "@phosphor-icons/react";
import {
  batchDeleteConversations,
  cancelBackgroundTask,
  createConversation,
  createKnowledgeEntry,
  createUser,
  deleteConversation,
  deleteKnowledgeEntry,
  getCurrentUser,
  getConversationDetail,
  getHealth,
  listConversations,
  listBackgroundTasks,
  listKnowledgeEntries,
  listUsers,
  login,
  logout,
  previewProjectKnowledge,
  retryBackgroundTask,
  sendConversationMessage,
  submitReviewGateAction,
  updateUser,
  updateKnowledgeEntry,
  uploadConversationAsset
} from "./api/queries";
import { getStoredAuthToken, setStoredAuthToken } from "./api/client";
import type {
  AssetKind,
  AssetRef,
  AuthUser,
  AuthUserCreateRequest,
  AuthUserUpdateRequest,
  BackgroundTask,
  ConversationDetail,
  ConversationMessage,
  KnowledgeBaseCreateRequest,
  KnowledgeBaseEntry,
  KnowledgeDomain,
  KnowledgeStatus,
  KnowledgeWorkflowType,
  ReviewAction,
  ReviewGate
} from "./api/types";

type ViewMode = "chat" | "knowledge" | "users";
type ReviewSubmit = {
  gate: ReviewGate;
  action: ReviewAction;
  comment?: string;
  editedPayload?: Record<string, unknown>;
};
type ResultField = {
  path?: Array<string | number>;
  label: string;
  value: unknown;
  editable?: boolean;
  render?: "comparison_table";
};
type PendingUpload = {
  id: string;
  name: string;
  kind: AssetKind;
  file: File;
  previewUrl?: string;
};
type SendSnapshot = {
  content: string;
  assets: AssetRef[];
  uploads: PendingUpload[];
  clientMessageId: string;
};
type TimelineItem =
  | { kind: "message"; id: string; createdAt: string; message: ConversationMessage }
  | { kind: "gate"; id: string; createdAt: string; gate: ReviewGate };

const emptyKnowledgeDraft: KnowledgeBaseCreateRequest = {
  title: "",
  domain: "packaging",
  workflow_type: "packaging",
  category: "",
  content: {},
  tags: [],
  keywords: [],
  status: "active",
  priority: 50,
  source: "manual"
};

export function App() {
  const queryClient = useQueryClient();
  const [activeView, setActiveView] = useState<ViewMode>("chat");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [composerAssets, setComposerAssets] = useState<AssetRef[]>([]);
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [manageMode, setManageMode] = useState(false);
  const [selectedConversationIds, setSelectedConversationIds] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState("");
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [selectedKnowledgeId, setSelectedKnowledgeId] = useState<string | null>(null);
  const [knowledgeDraft, setKnowledgeDraft] = useState<KnowledgeFormState>(knowledgeFormState(emptyKnowledgeDraft));
  const [knowledgeFilters, setKnowledgeFilters] = useState({ query: "", status: "all", domain: "all" });
  const [knowledgePreview, setKnowledgePreview] = useState("");
  const [authToken, setAuthToken] = useState(() => getStoredAuthToken());
  const pendingUploadsRef = useRef<PendingUpload[]>([]);
  const sendInFlightRef = useRef(false);

  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 15000 });
  const authRequired = Boolean(health.data?.auth_enabled);
  const apiReady = !authRequired || Boolean(authToken);
  const currentUser = useQuery({
    queryKey: ["auth", "me", authToken],
    queryFn: getCurrentUser,
    enabled: authRequired && Boolean(authToken),
    retry: false
  });
  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
    enabled: apiReady,
    refetchInterval: apiReady ? 5000 : false
  });
  const detail = useQuery({
    queryKey: ["conversation", selectedId],
    queryFn: () => getConversationDetail(selectedId as string),
    enabled: apiReady && Boolean(selectedId),
    refetchInterval: apiReady && selectedId ? 1200 : false
  });
  const knowledge = useQuery({
    queryKey: ["knowledge"],
    queryFn: listKnowledgeEntries,
    enabled: apiReady && activeView === "knowledge"
  });
  const canManageUsers = currentUser.data?.role === "admin";
  const users = useQuery({
    queryKey: ["auth", "users"],
    queryFn: listUsers,
    enabled: apiReady && activeView === "users" && canManageUsers
  });

  const conversationsData = conversations.data ?? [];
  const selectedFromList = selectedId ? conversationsData.find((item) => item.session.id === selectedId) ?? null : null;
  const currentDetail = detail.data ?? selectedFromList;
  const pendingGate = currentDetail?.pending_review_gate ?? null;
  const knowledgeEntries = knowledge.data ?? [];
  const userEntries = users.data ?? [];
  const currentProjectId = currentDetail ? projectId(currentDetail) : "";
  const tasks = useQuery({
    queryKey: ["tasks", currentProjectId],
    queryFn: () => listBackgroundTasks(currentProjectId),
    enabled: apiReady && Boolean(currentProjectId),
    refetchInterval: apiReady && currentProjectId ? 2000 : false
  });

  const cancelTaskMutation = useMutation({
    mutationFn: cancelBackgroundTask,
    onSuccess: () => {
      showToast("任务已取消");
      if (currentProjectId) queryClient.invalidateQueries({ queryKey: ["tasks", currentProjectId] });
      if (selectedId) queryClient.invalidateQueries({ queryKey: ["conversation", selectedId] });
    },
    onError: showError
  });

  const retryTaskMutation = useMutation({
    mutationFn: retryBackgroundTask,
    onSuccess: () => {
      showToast("任务已重新排队");
      if (currentProjectId) queryClient.invalidateQueries({ queryKey: ["tasks", currentProjectId] });
      if (selectedId) queryClient.invalidateQueries({ queryKey: ["conversation", selectedId] });
    },
    onError: showError
  });

  useEffect(() => {
    if (!conversationsData.length) {
      if (selectedId) setSelectedId(null);
      return;
    }
    const selectedExistsInList = conversationsData.some((item) => item.session.id === selectedId);
    const selectedExistsInDetail = Boolean(selectedId && detail.data?.session.id === selectedId);
    if (!selectedId || (!selectedExistsInList && !selectedExistsInDetail)) {
      setSelectedId(conversationsData[0].session.id);
    }
  }, [conversationsData, detail.data?.session.id, selectedId]);

  useEffect(() => {
    if (!detail.isError || !selectedId || !conversationsData.length) return;
    const fallback = conversationsData.find((item) => item.session.id !== selectedId) ?? conversationsData[0];
    if (fallback) setSelectedId(fallback.session.id);
  }, [conversationsData, detail.isError, selectedId]);

  useEffect(() => {
    if (!knowledgeEntries.length) {
      setSelectedKnowledgeId(null);
      setKnowledgeDraft(knowledgeFormState(emptyKnowledgeDraft));
      return;
    }
    const selected = knowledgeEntries.find((item) => item.id === selectedKnowledgeId) ?? knowledgeEntries[0];
    if (selected.id !== selectedKnowledgeId) setSelectedKnowledgeId(selected.id);
    setKnowledgeDraft(knowledgeFormState(selected));
  }, [knowledgeEntries, selectedKnowledgeId]);

  useEffect(() => {
    pendingUploadsRef.current = pendingUploads;
  }, [pendingUploads]);

  useEffect(() => () => {
    revokePendingUploads(pendingUploadsRef.current);
  }, []);

  useEffect(() => {
    if (!authRequired || !authToken || !currentUser.isError) return;
    setStoredAuthToken("");
    setAuthToken("");
    setSelectedId(null);
    queryClient.removeQueries({ queryKey: ["conversations"] });
  }, [authRequired, authToken, currentUser.isError, queryClient]);

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: (data) => {
      setStoredAuthToken(data.access_token);
      setAuthToken(data.access_token);
      queryClient.invalidateQueries({ queryKey: ["health"] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      showToast(`已登录：${data.user.email}`);
    },
    onError: showError
  });

  const logoutMutation = useMutation({
    mutationFn: logout,
    onSettled: () => handleLocalLogout()
  });

  const createMutation = useMutation({
    mutationFn: createConversation,
    onSuccess: (data) => {
      upsertConversationCache(queryClient, data);
      setSelectedId(data.session.id);
      setActiveView("chat");
      setDraft("");
      setComposerAssets([]);
      clearPendingUploads();
      queryClient.setQueryData(["conversation", data.session.id], data);
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: showError
  });

  const sendMutation = useMutation({
    mutationFn: async ({ content, assets, uploads, clientMessageId }: SendSnapshot) => {
      if (!selectedId && !uploads.length && !assets.length) {
        return createConversation({ initial_message: content });
      }

      let sessionId = selectedId;
      if (!sessionId) {
        const created = await createConversation({ initial_message: "", title: "新项目对话" });
        sessionId = created.session.id;
        upsertConversationCache(queryClient, created);
        queryClient.setQueryData(["conversation", sessionId], created);
      }

      let uploadedAssets: AssetRef[] = [];
      let knownIds = new Set((queryClient.getQueryData<ConversationDetail>(["conversation", sessionId])?.assets ?? []).map((asset) => asset.id));
      for (const upload of uploads) {
        const data = await uploadConversationAsset(sessionId, upload.kind, upload.file);
        const createdAssets = data.assets.filter((asset) => !knownIds.has(asset.id));
        const visibleAssets = createdAssets.length ? createdAssets : uploadedAssetFallback(data.assets, upload.file);
        uploadedAssets = uniqueAssets([...uploadedAssets, ...visibleAssets]);
        knownIds = new Set(data.assets.map((asset) => asset.id));
        upsertConversationCache(queryClient, data);
        queryClient.setQueryData(["conversation", data.session.id], data);
      }

      const referencedAssets = uniqueAssets([...assets, ...uploadedAssets]);
      const messageContent =
        content ||
        `我上传并引用了 ${referencedAssets.map((asset) => `@${asset.filename}`).join(" ")}，请结合当前流程继续。`;
      const payload: Record<string, unknown> = {
        client_message_id: clientMessageId
      };
      if (referencedAssets.length) {
        payload.mentions = referencedAssets.map((asset) => ({
          placeholder: `@${asset.filename}`,
          asset_id: asset.id,
          role_as: roleFromMessageContext(messageContent, asset) || roleFromAsset(asset)
        }));
      }
      return sendConversationMessage(sessionId, { content: messageContent, payload });
    },
    onSuccess: (data) => {
      upsertConversationCache(queryClient, data);
      setSelectedId(data.session.id);
      setDraft("");
      setComposerAssets([]);
      clearPendingUploads();
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.setQueryData(["conversation", data.session.id], data);
    },
    onError: showError,
    onSettled: () => setUploadingCount(0)
  });

  const reviewMutation = useMutation({
    mutationFn: ({ gate, action, comment, editedPayload }: ReviewSubmit) => {
      if (!selectedId) throw new Error("未选中项目");
      return submitReviewGateAction(selectedId, gate.id, {
        action,
        comment: comment || reviewActionCopy(action),
        edited_payload: action === "edit" ? editedPayload ?? gate.payload : null,
        reviewer: "user"
      });
    },
    onSuccess: (data) => {
      showToast(reviewStatus(data.review_gates.at(-1)?.status ?? "approved"));
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.setQueryData(["conversation", data.session.id], data);
    },
    onError: showError
  });

  const deleteMutation = useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => {
      setSelectedId(null);
      setSelectedConversationIds(new Set());
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      showToast("项目已删除");
    },
    onError: showError
  });

  const batchDeleteMutation = useMutation({
    mutationFn: batchDeleteConversations,
    onSuccess: (result) => {
      setSelectedId(null);
      setSelectedConversationIds(new Set());
      setManageMode(false);
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      showToast(`已删除 ${result.deleted_count} 个项目`);
    },
    onError: showError
  });

  const saveKnowledgeMutation = useMutation({
    mutationFn: (payload: KnowledgeBaseCreateRequest & { id?: string }) =>
      payload.id ? updateKnowledgeEntry(payload.id, payload) : createKnowledgeEntry(payload),
    onSuccess: (entry) => {
      setSelectedKnowledgeId(entry.id);
      queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      showToast("知识已保存");
    },
    onError: showError
  });

  const deleteKnowledgeMutation = useMutation({
    mutationFn: deleteKnowledgeEntry,
    onSuccess: () => {
      setSelectedKnowledgeId(null);
      queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      showToast("知识已删除");
    },
    onError: showError
  });

  const previewKnowledgeMutation = useMutation({
    mutationFn: (projectId: string) => previewProjectKnowledge(projectId),
    onSuccess: (preview) => {
      if (!preview.results.length) {
        setKnowledgePreview("当前项目没有命中特定知识，Agent 会使用通用包装策略方法。");
        return;
      }
      setKnowledgePreview(
        [`命中 ${preview.results.length} 条知识。`, ...preview.results.map((item) => `${item.entry.title} · score ${item.score.toFixed(2)}`)].join("\n")
      );
    },
    onError: showError
  });

  const createUserMutation = useMutation({
    mutationFn: createUser,
    onSuccess: (user) => {
      queryClient.invalidateQueries({ queryKey: ["auth", "users"] });
      showToast(`用户已创建：${user.email}`);
    },
    onError: showError
  });

  const updateUserMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: { role?: "admin" | "member"; status?: "active" | "disabled"; password?: string } }) =>
      updateUser(id, payload),
    onSuccess: (user) => {
      queryClient.invalidateQueries({ queryKey: ["auth", "users"] });
      if (currentUser.data?.id === user.id) queryClient.invalidateQueries({ queryKey: ["auth", "me", authToken] });
      showToast(`用户已更新：${user.email}`);
    },
    onError: showError
  });

  function showToast(message: string) {
    setToast(message);
    window.setTimeout(() => setToast(""), 2200);
  }

  function showError(error: unknown) {
    showToast(errorMessage(error));
  }

  function clearPendingUploads() {
    setPendingUploads((current) => {
      revokePendingUploads(current);
      return [];
    });
  }

  function handleLocalLogout() {
    setStoredAuthToken("");
    setAuthToken("");
    setSelectedId(null);
    setActiveView("chat");
    setDraft("");
    setComposerAssets([]);
    clearPendingUploads();
    queryClient.removeQueries({ queryKey: ["auth"] });
    queryClient.removeQueries({ queryKey: ["conversations"] });
    queryClient.removeQueries({ queryKey: ["conversation"] });
    queryClient.removeQueries({ queryKey: ["knowledge"] });
    queryClient.removeQueries({ queryKey: ["tasks"] });
    showToast("已退出登录");
  }

  function removePendingUpload(uploadId: string) {
    setPendingUploads((current) => {
      const removed = current.find((upload) => upload.id === uploadId);
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return current.filter((upload) => upload.id !== uploadId);
    });
  }

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    const queuedFiles = Array.from(files);
    const pending = queuedFiles.map((file, index) => ({
      id: `${file.name}-${file.size}-${file.lastModified}-${index}`,
      name: file.name,
      kind: inferAssetKind(file),
      file,
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined
    }));
    setPendingUploads((current) => uniquePendingUploads([...current, ...pending]));
  }

  function submitMessage() {
    const trimmed = draft.trim();
    if (!trimmed && !composerAssets.length && !pendingUploads.length) return;
    if (sendInFlightRef.current) return;
    const snapshot = {
      content: trimmed,
      assets: composerAssets,
      uploads: pendingUploads,
      clientMessageId: createClientMessageId()
    };
    sendInFlightRef.current = true;
    setDraft("");
    setComposerAssets([]);
      setPendingUploads([]);
      setUploadingCount(snapshot.uploads.length);
      sendMutation.mutate(snapshot, {
      onError: async () => {
        const recovered = await recoverSubmittedMessage(snapshot.clientMessageId);
        if (recovered) return;
        setDraft(snapshot.content);
        setComposerAssets(snapshot.assets);
        setPendingUploads(snapshot.uploads.map((upload) => ({
          ...upload,
          previewUrl: upload.file.type.startsWith("image/") ? URL.createObjectURL(upload.file) : undefined
        })));
      },
      onSettled: () => {
        revokePendingUploads(snapshot.uploads);
        sendInFlightRef.current = false;
      }
    });
  }

  async function recoverSubmittedMessage(clientMessageId: string) {
    try {
      const details = await listConversations();
      const matched = details.find((item) =>
        item.messages.some((message) => message.role === "user" && messagePayloadClientId(message) === clientMessageId)
      );
      if (!matched) return false;
      upsertConversationCache(queryClient, matched);
      queryClient.setQueryData(["conversation", matched.session.id], matched);
      setSelectedId(matched.session.id);
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      showToast("消息已提交，已恢复当前项目状态。");
      return true;
    } catch {
      return false;
    }
  }

  const selectedCount = selectedConversationIds.size;
  const busy = createMutation.isPending || sendMutation.isPending || reviewMutation.isPending || uploadingCount > 0 || sendInFlightRef.current;

  if (authRequired && !authToken) {
    return (
      <LoginScreen
        adminEmail={health.data?.admin_email ?? "1173817292@qq.com"}
        loading={loginMutation.isPending}
        onSubmit={(email, password) => loginMutation.mutate({ email, password })}
      />
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="项目对话">
        <div className="sb-brand">
          <div className="logo-icon">PV</div>
          <span className="sb-brand-name">PackVision</span>
        </div>
        {currentUser.data ? (
          <div className="sb-account-card">
            <div className="sb-account-avatar">{currentUser.data.email.slice(0, 1).toUpperCase()}</div>
            <div className="sb-account-main">
              <strong>{currentUser.data.email}</strong>
              <span>{currentUser.data.role === "admin" ? "管理员" : "成员"} · {currentUser.data.status === "active" ? "启用中" : "已禁用"}</span>
            </div>
            <button className="sb-account-logout" type="button" disabled={logoutMutation.isPending} onClick={() => logoutMutation.mutate()}>
              退出
            </button>
          </div>
        ) : null}
        <div className="sb-nav">
          <button className="sb-nav-item" type="button" onClick={() => createMutation.mutate({ initial_message: "", title: "新项目对话" })}>
            <span className="sb-nav-icon">＋</span>
            <span>新建项目</span>
          </button>
          <button className={`sb-nav-item${manageMode ? " active" : ""}`} type="button" onClick={() => setManageMode((value) => !value)}>
            <span className="sb-nav-icon">◎</span>
            <span>{manageMode ? "完成" : "管理"}</span>
            <span className="sb-nav-badge">{conversationsData.length}</span>
          </button>
          <button className={`sb-nav-item${activeView === "knowledge" ? " active" : ""}`} type="button" onClick={() => setActiveView(activeView === "knowledge" ? "chat" : "knowledge")}>
            <span className="sb-nav-icon">◇</span>
            <span>知识库</span>
          </button>
          {canManageUsers ? (
            <button className={`sb-nav-item${activeView === "users" ? " active" : ""}`} type="button" onClick={() => setActiveView(activeView === "users" ? "chat" : "users")}>
              <span className="sb-nav-icon">◇</span>
              <span>用户管理</span>
              <span className="sb-nav-badge">{userEntries.length}</span>
            </button>
          ) : null}
        </div>
        <div className="sidebar-search">
          <span className="search-icon">⌕</span>
          <span>搜索项目...</span>
        </div>
        <SelectionBar
          hidden={!manageMode}
          selectedCount={selectedCount}
          allSelected={selectedCount === conversationsData.length && conversationsData.length > 0}
          onSelectAll={() => setSelectedConversationIds(new Set(conversationsData.map((item) => item.session.id)))}
          onClear={() => setSelectedConversationIds(new Set())}
          onDelete={() => {
            if (!selectedCount) return;
            setConfirmState({
              title: `删除 ${selectedCount} 个项目？`,
              subtitle: "此操作不可撤销",
              body: "删除会清理后端该项目相关的项目记录、素材文件、会话消息、人工确认卡、审计记录和项目记忆。",
              confirmText: "批量删除",
              danger: true,
              onConfirm: () => batchDeleteMutation.mutate({ session_ids: Array.from(selectedConversationIds) })
            });
          }}
        />
        <div className="sb-sec">
          <span>所有项目</span>
          <button className="sb-sec-action" type="button" title="清理无项目素材">⌁</button>
        </div>
        <ConversationList
          items={conversationsData}
          activeId={selectedId}
          manageMode={manageMode}
          selectedIds={selectedConversationIds}
          onToggleSelect={(id) => {
            setSelectedConversationIds((current) => {
              const next = new Set(current);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            });
          }}
          onSelect={(id) => {
            setActiveView("chat");
            setSelectedId(id);
            setDraft("");
            setComposerAssets([]);
            clearPendingUploads();
          }}
        />
      </aside>

      <main className="chat-area" aria-live="polite">
        <TopBar
          detail={currentDetail}
          healthLabel={healthLabel(health.data)}
          tasks={tasks.data ?? []}
          taskActionBusy={cancelTaskMutation.isPending || retryTaskMutation.isPending}
          onRefresh={() => {
            queryClient.invalidateQueries({ queryKey: ["health"] });
            queryClient.invalidateQueries({ queryKey: ["conversations"] });
            if (currentProjectId) queryClient.invalidateQueries({ queryKey: ["tasks", currentProjectId] });
            if (selectedId) queryClient.invalidateQueries({ queryKey: ["conversation", selectedId] });
          }}
          onDelete={() => {
            if (!selectedId || !currentDetail) return;
            setConfirmState({
              title: "删除 1 个项目？",
              subtitle: "此操作不可撤销",
              body: "删除会清理后端该项目相关的项目记录、素材文件、会话消息、人工确认卡、审计记录和项目记忆。",
              items: [currentDetail.session.title],
              confirmText: "确认删除",
              danger: true,
              onConfirm: () => deleteMutation.mutate(selectedId)
            });
          }}
          onCancelTask={(jobId) => cancelTaskMutation.mutate(jobId)}
          onRetryTask={(jobId) => retryTaskMutation.mutate(jobId)}
        />

        {activeView === "users" && canManageUsers ? (
          <UserManagementSurface
            users={userEntries}
            currentUser={currentUser.data}
            loading={users.isLoading || createUserMutation.isPending || updateUserMutation.isPending}
            onCreate={(payload) => createUserMutation.mutate(payload)}
            onUpdate={(id, payload) => updateUserMutation.mutate({ id, payload })}
            onRefresh={() => queryClient.invalidateQueries({ queryKey: ["auth", "users"] })}
          />
        ) : activeView === "knowledge" ? (
          <KnowledgeSurface
            entries={knowledgeEntries}
            filters={knowledgeFilters}
            selectedId={selectedKnowledgeId}
            draft={knowledgeDraft}
            preview={knowledgePreview}
            currentProjectId={currentDetail?.project?.id}
            onFilter={setKnowledgeFilters}
            onSelect={(entry) => {
              setSelectedKnowledgeId(entry.id);
              setKnowledgeDraft(knowledgeFormState(entry));
            }}
            onNew={() => {
              setSelectedKnowledgeId(null);
              setKnowledgeDraft(knowledgeFormState(emptyKnowledgeDraft));
            }}
            onDraft={setKnowledgeDraft}
            onSave={(payload) => saveKnowledgeMutation.mutate(payload)}
            onDelete={(id) => {
              setConfirmState({
                title: "删除知识条目？",
                subtitle: "删除后 Agent 不会再检索该条知识",
                items: [knowledgeDraft.title],
                confirmText: "删除",
                danger: true,
                onConfirm: () => deleteKnowledgeMutation.mutate(id)
              });
            }}
            onPreview={() => {
              if (!currentDetail?.project?.id) {
                setKnowledgePreview("请先选择一个项目，再预览知识调用。");
                return;
              }
              previewKnowledgeMutation.mutate(currentDetail.project.id);
            }}
          />
        ) : (
          <>
            {!currentDetail ? <EmptyState /> : null}
            {currentDetail ? (
              <ChatSurface
                detail={currentDetail}
                pendingGate={pendingGate}
                busy={reviewMutation.isPending}
                onReview={(input) => reviewMutation.mutate(input)}
              />
            ) : null}
          </>
        )}

        {activeView === "chat" && currentDetail ? (
          <Composer
            value={draft}
            assets={currentDetail.assets}
            selectedAssets={composerAssets}
            pendingUploads={pendingUploads}
            projectId={projectId(currentDetail)}
            disabled={busy}
            pendingGate={Boolean(pendingGate)}
            onFiles={handleFiles}
            onChange={setDraft}
            onAttachAsset={(asset) => setComposerAssets((current) => uniqueAssets([...current, asset]))}
            onRemoveAsset={(assetId) => setComposerAssets((current) => current.filter((asset) => asset.id !== assetId))}
            onRemovePendingUpload={removePendingUpload}
            onSubmit={submitMessage}
          />
        ) : null}
      </main>

      <Toast message={toast} />
      <ConfirmModal state={confirmState} onClose={() => setConfirmState(null)} />
    </div>
  );
}

function LoginScreen({
  adminEmail,
  loading,
  onSubmit
}: {
  adminEmail: string;
  loading: boolean;
  onSubmit: (email: string, password: string) => void;
}) {
  const [email, setEmail] = useState(adminEmail);
  const [password, setPassword] = useState("");

  return (
    <main className="login-shell">
      <form
        className="login-card"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(email.trim(), password);
        }}
      >
        <div className="login-mark">PV</div>
        <div className="login-title">
          <span>PackVision Admin</span>
          <strong>登录生产控制台</strong>
        </div>
        <label>
          <span>管理员邮箱</span>
          <input value={email} autoComplete="email" onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label>
          <span>密码</span>
          <input
            value={password}
            type="password"
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <button type="submit" disabled={loading || !email.trim() || !password}>
          {loading ? "登录中..." : "登录"}
        </button>
      </form>
    </main>
  );
}

function ConversationList({
  items,
  activeId,
  manageMode,
  selectedIds,
  onSelect,
  onToggleSelect
}: {
  items: ConversationDetail[];
  activeId: string | null;
  manageMode: boolean;
  selectedIds: Set<string>;
  onSelect: (id: string) => void;
  onToggleSelect: (id: string) => void;
}) {
  return (
    <div className="project-list">
      {items.map((item) => (
        <div key={item.session.id} className="project-row">
          {manageMode ? (
            <input
              className="project-check"
              type="checkbox"
              checked={selectedIds.has(item.session.id)}
              onChange={() => onToggleSelect(item.session.id)}
            />
          ) : null}
          <button className={`project-item${activeId === item.session.id ? " active" : ""}`} type="button" onClick={() => onSelect(item.session.id)}>
            <div className="project-name">{projectTitle(item.session.title)}</div>
            <div className="project-meta">
              <span className={`tag ${item.pending_review_gate ? "review" : item.session.current_stage === "completed" ? "done" : "running"}`}>
                {item.pending_review_gate ? "待确认" : stageShortLabel(item.session.current_stage)}
              </span>
              <span>{lastMessagePreview(item)}</span>
            </div>
          </button>
        </div>
      ))}
    </div>
  );
}

function SelectionBar({
  hidden,
  selectedCount,
  allSelected,
  onSelectAll,
  onClear,
  onDelete
}: {
  hidden: boolean;
  selectedCount: number;
  allSelected: boolean;
  onSelectAll: () => void;
  onClear: () => void;
  onDelete: () => void;
}) {
  return (
    <div className={`selection-bar${hidden ? " hidden" : ""}`}>
      <span>已选 {selectedCount} 个</span>
      <button type="button" onClick={onSelectAll}>
        {allSelected ? "已全选" : "全选"}
      </button>
      <button className="danger" type="button" onClick={onDelete}>
        删除
      </button>
      <button type="button" onClick={onClear}>
        取消
      </button>
    </div>
  );
}

function TopBar({
  detail,
  healthLabel,
  tasks,
  taskActionBusy,
  onRefresh,
  onDelete,
  onCancelTask,
  onRetryTask
}: {
  detail: ConversationDetail | null | undefined;
  healthLabel: string;
  tasks: BackgroundTask[];
  taskActionBusy: boolean;
  onRefresh: () => void;
  onDelete: () => void;
  onCancelTask: (jobId: string) => void;
  onRetryTask: (jobId: string) => void;
}) {
  const progress = detail ? confirmedProgress(detail) : null;
  const runningTasks = tasks.filter((task) => task.status === "queued" || task.status === "running").length;
  const failedTasks = tasks.filter((task) => task.status === "failed").length;
  return (
    <div className="mn-top">
      <div className="mn-top-l">
        <div className="mn-top-model">
          <span id="conversationTitleTop">{detail ? projectTitle(detail.session.title) : "PackVision 1.0"}</span>
          <span className="mn-top-model-badge">{detail ? workflowLabel(detail.session.workflow_type) : "Agent"}</span>
        </div>
      </div>
      <div className="mn-top-r">
        {runningTasks || failedTasks ? (
          <div className={`mn-top-badge${failedTasks ? " warn" : ""}`}>
            {runningTasks ? `任务运行中 ${runningTasks}` : `任务失败 ${failedTasks}`}
          </div>
        ) : null}
        {progress ? <div className="mn-top-badge">{progress.confirmed}/{progress.total} 已确认</div> : null}
        <button className="mn-top-icon-btn" type="button" title="刷新" onClick={onRefresh}>
          ↻
        </button>
        <div className="nav-status">
          <span className="status-dot"></span>
          <span>{healthLabel}</span>
        </div>
        <button className="mn-top-icon-btn danger" type="button" title="删除当前任务" onClick={onDelete}>
          删
        </button>
      </div>
      <TaskStatusStrip
        tasks={tasks}
        busy={taskActionBusy}
        onCancelTask={onCancelTask}
        onRetryTask={onRetryTask}
      />
    </div>
  );
}

function TaskStatusStrip({
  tasks,
  busy,
  onCancelTask,
  onRetryTask
}: {
  tasks: BackgroundTask[];
  busy: boolean;
  onCancelTask: (jobId: string) => void;
  onRetryTask: (jobId: string) => void;
}) {
  const visibleTasks = tasks.filter((task) => task.status !== "succeeded").slice(0, 3);
  if (!visibleTasks.length) return null;
  return (
    <div className="task-strip" aria-label="后台任务状态">
      {visibleTasks.map((task) => {
        const terminal = task.status === "failed" || task.status === "cancelled";
        const active = task.status === "queued" || task.status === "running";
        return (
          <div className={`task-pill ${task.status}`} key={task.id}>
            <span className="task-dot"></span>
            <strong>{taskKindLabel(task.kind)}</strong>
            <span>{taskStatusLabel(task.status)}</span>
            {task.error ? <em title={task.error}>{task.error}</em> : null}
            {terminal ? (
              <button type="button" disabled={busy} onClick={() => onRetryTask(task.id)}>
                重试
              </button>
            ) : null}
            {active ? (
              <button type="button" disabled={busy} onClick={() => onCancelTask(task.id)}>
                取消
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-mark">PV</div>
      <h2>新建一场视觉任务</h2>
      <p>从一句项目描述开始，上传产品资料、竞品资料和品牌素材。主 Agent 会判断任务类型，并调度对应子 Agent。</p>
    </div>
  );
}

function ChatSurface({
  detail,
  pendingGate,
  busy,
  onReview
}: {
  detail: ConversationDetail;
  pendingGate: ReviewGate | null;
  busy: boolean;
  onReview: (input: ReviewSubmit) => void;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const timeline = useMemo(() => buildTimeline(detail), [detail]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [timeline.length, pendingGate?.id]);

  return (
    <div className="chat-surface">
      <div className="chat-header">
        <div className="agent-pill">
          <div className="agent-dot"></div>
          <span>{stageLabel(detail.session.current_stage)}</span>
        </div>
        <div className="chat-title-wrap">
          <div className="chat-title">{projectTitle(detail.session.title)}</div>
        </div>
      </div>
      <div className="messages">
        <ContextProgressPanel detail={detail} />
        {timeline.map((item) =>
          item.kind === "message" ? (
            <MessageNode key={item.id} message={item.message} />
          ) : (
            <ReviewGateNode key={item.id} gate={item.gate} detail={detail} busy={busy} onReview={onReview} />
          )
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function ContextProgressPanel({ detail }: { detail: ConversationDetail }) {
  const progress = confirmedProgress(detail);
  const nodes = [
    ["项目定义", "project_definition"],
    ["资料解析", "asset_analysis"],
    ["核心卖点", "selling_points"],
    ["VI 规范", "vi_rules"],
    ["包装策略", "packaging_strategy"],
    ["主图提示词", "image_prompt"],
    ["生成图与质检", "confirmed_outputs"]
  ];
  return (
    <div className="context-progress">
      <div className="context-progress-top">
        <div className="context-progress-title">对话结果</div>
        <div className="context-progress-count">{progress.confirmed}/{progress.total} 已确认</div>
      </div>
      <div className="context-progress-bar">
        <div className="context-progress-fill" style={{ width: `${progress.total ? (progress.confirmed / progress.total) * 100 : 0}%` }} />
      </div>
      <div className="context-progress-chips">
        {nodes.map(([label, key]) => {
          const status = detail.confirmed_context?.[key] ? "confirmed" : detail.pending_review_gate?.next_step_on_approve === key ? "pending" : "locked";
          return (
            <span key={key} className={`context-chip ${status}`}>
              {label}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function MessageNode({ message }: { message: ConversationMessage }) {
  if (message.role === "user") {
    return (
      <div className="msg-user">
        <div className="bubble">{message.content}</div>
        <div className="msg-avatar user">你</div>
      </div>
    );
  }
  if (message.message_type === "tool_call" || message.message_type === "tool_result" || message.message_type === "status") {
    const status = messageRunStatus(message);
    const iconClass = status === "failed" ? "orange" : status === "done" ? "green" : "orange";
    return (
      <div className="tool-bar compact">
        <div className="tool-row">
          <div className="tool-info">
            <div className={`tool-icon ${iconClass}`}>{status === "done" ? "✓" : status === "failed" ? "!" : "T"}</div>
            <div className="tool-sub">{message.content || messageTypeLabel(message.message_type)}</div>
          </div>
          <span className={`badge ${statusBadgeClass(status)}`}>{statusLabel(status)}</span>
        </div>
      </div>
    );
  }
  return (
    <div className="msg-agent">
      <div className="msg-avatar agent">A</div>
      <div>
        <div className="agent-label">AI Agent</div>
        <div className="bubble">{message.content || "已完成一项处理。"}</div>
      </div>
    </div>
  );
}

function ReviewGateNode({
  gate,
  detail,
  busy,
  onReview
}: {
  gate: ReviewGate;
  detail: ConversationDetail;
  busy: boolean;
  onReview: (input: ReviewSubmit) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [jsonDraft, setJsonDraft] = useState(() => JSON.stringify(gate.payload, null, 2));
  const [revision, setRevision] = useState("");
  const isPending = gate.status === "pending";
  const conceptItems = conceptItemsFromPayload(gate.payload, detail.assets);

  useEffect(() => {
    setEditing(false);
    setJsonDraft(JSON.stringify(gate.payload, null, 2));
    setRevision("");
  }, [gate.id, gate.payload]);

  if (!isPending) {
    return (
      <>
        <div className="confirmed-marker">已确认 · {gate.title}</div>
        <StructuredResultCard gate={gate} detail={detail} readonly />
        {conceptItems.length ? <ConceptGrid items={conceptItems} readonly projectId={projectId(detail)} /> : null}
      </>
    );
  }

  return (
    <>
      <StructuredResultCard gate={gate} detail={detail} editing={editing} jsonDraft={jsonDraft} onJsonDraft={setJsonDraft} />
      {conceptItems.length ? <ConceptGrid items={conceptItems} projectId={projectId(detail)} /> : null}
      <div className={`confirm-card${editing ? " editing" : ""}`}>
        <div className="confirm-header">
          <div className="confirm-icon">✓</div>
          <div>
            <div className="confirm-title">{gate.title}</div>
            <div className="confirm-sub">确认后将保留在对话结果中，供后续 Agent 使用</div>
          </div>
        </div>
        <div className="confirm-body">
          <p>{gate.summary || "请确认该结构化结果是否可以作为后续有效上下文。"}</p>
          <textarea
            className={editing ? "result-editor" : "hidden"}
            value={jsonDraft}
            onChange={(event) => setJsonDraft(event.target.value)}
            rows={10}
          />
          <textarea
            className="result-editor"
            value={revision}
            onChange={(event) => setRevision(event.target.value)}
            placeholder="不满意时可输入修改意见，点击回退重做；编辑模式下这段话会作为人工说明。"
            rows={2}
          />
        </div>
        <div className="confirm-actions">
          <button
            className="btn-confirm"
            type="button"
            disabled={busy}
            onClick={() => {
              if (editing) {
                try {
                  onReview({ gate, action: "edit", editedPayload: JSON.parse(jsonDraft), comment: revision || "已人工修改并确认" });
                } catch {
                  window.alert("JSON 格式不正确");
                }
              } else {
                onReview({ gate, action: "approve" });
              }
            }}
          >
            {editing ? "保存修改并继续" : "确认并继续"}
          </button>
          <button className="btn-edit" type="button" disabled={busy} onClick={() => setEditing((value) => !value)}>
            {editing ? "取消编辑" : "编辑结构化结果"}
          </button>
          <button className="btn-revert" type="button" disabled={busy} onClick={() => onReview({ gate, action: "reject", comment: revision || "退回重新分析" })}>
            回退重新分析
          </button>
        </div>
      </div>
    </>
  );
}

function StructuredResultCard({
  gate,
  detail,
  readonly = false,
  editing = false,
  jsonDraft = "",
  onJsonDraft
}: {
  gate: ReviewGate;
  detail: ConversationDetail;
  readonly?: boolean;
  editing?: boolean;
  jsonDraft?: string;
  onJsonDraft?: (value: string) => void;
}) {
  const draftPayload = editing ? safeJsonObject(jsonDraft, gate.payload) : gate.payload;
  const fields = resultFieldsForGate(gate, draftPayload);
  return (
    <div className={`result-card${readonly ? " confirmed-result-card" : ""}`}>
      <div className="result-card-header">
        <div className="result-card-title">{resultTitle(gate)}</div>
        <div className={readonly ? "confirmed-badge" : "pending-badge"}>{reviewStatus(gate.status)}</div>
      </div>
      {gate.type === "packaging_strategy_review" && !editing ? (
        <PackagingStrategyReport payload={draftPayload} />
      ) : (
        <div className="result-rows">
          {fields.length ? (
            fields.map((field, index) => (
              <ResultRow
                key={`${field.path?.join(".") || field.label}-${index}`}
                field={field}
                detail={detail}
                editable={editing && field.editable !== false && Boolean(field.path)}
                onChange={(value) => {
                  if (!field.path) return;
                  const next = setByPathImmutable(draftPayload, field.path, value);
                  onJsonDraft?.(JSON.stringify(next, null, 2));
                }}
              />
            ))
          ) : (
            <div className="result-row">
              <div className="result-key">内容</div>
              <div className="result-val">暂无结构化内容</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PackagingStrategyReport({ payload }: { payload: Record<string, unknown> }) {
  const copyItems = arrayOfValues(payload.required_copy);
  const iconItems = arrayOfValues(payload.required_icons);
  const riskItems = arrayOfValues(payload.risk_notes);
  return (
    <div className="strategy-report">
      <div className="strategy-report-hero">
        <div className="strategy-report-product">{stringValue(payload.product_name) || "包装主图设计方案"}</div>
        <div className="strategy-report-meta">
          {[
            stringValue(payload.box_type) ? `盒型：${stringValue(payload.box_type)}` : "",
            stringValue(payload.front_ratio) ? `正面：${stringValue(payload.front_ratio)}` : "",
            stringValue(payload.side_ratio) ? `侧面：${stringValue(payload.side_ratio)}` : "",
            stringValue(payload.top_ratio) ? `顶面：${stringValue(payload.top_ratio)}` : ""
          ]
            .filter(Boolean)
            .slice(0, 4)
            .map((item) => (
              <span className="strategy-pill" key={item}>{item}</span>
            ))}
        </div>
        <p className="strategy-report-tone">{stringValue(payload.overall_tone) || "等待包装策略 Agent 输出整体影调和用户感受。"}</p>
      </div>

      <StrategySection title="正面主图设计方案" text={payload.front_layout} featured />

      {copyItems.length || iconItems.length ? (
        <div className="strategy-report-panels">
          <StrategyChipPanel title="文案层级" items={copyItems} hint="主标题、副标题、卖点徽章和功能利益点会进入后续主图提示词。" />
          <StrategyChipPanel title="标识与图标" items={iconItems} hint="LOGO、年龄、玩法、功能和系列标签按证据使用。" />
        </div>
      ) : null}

      <div className="strategy-surface-grid">
        <StrategyMiniSection title="左侧信息分工" text={payload.left_layout} />
        <StrategyMiniSection title="右侧信息分工" text={payload.right_layout} />
        <StrategyMiniSection title="背面信息分工" text={payload.back_layout} />
      </div>

      {riskItems.length ? <StrategyChipPanel title="风险与人工确认点" items={riskItems} hint="这些内容不会自动覆盖，需要在确认后进入项目有效记忆。" tone="risk" /> : null}
    </div>
  );
}

function StrategySection({ title, text, featured = false }: { title: string; text: unknown; featured?: boolean }) {
  return (
    <section className={`strategy-section${featured ? " featured" : ""}`}>
      <h4>{title}</h4>
      <div className="strategy-section-body">{strategyParagraphs(text)}</div>
    </section>
  );
}

function StrategyMiniSection({ title, text }: { title: string; text: unknown }) {
  return (
    <section className="strategy-mini-section">
      <h4>{title}</h4>
      <p>{stringValue(text) || "暂无策略。"}</p>
    </section>
  );
}

function StrategyChipPanel({ title, items, hint = "", tone = "" }: { title: string; items: unknown[]; hint?: string; tone?: string }) {
  const normalized = items.map(formatPreview).filter(Boolean).slice(0, 10);
  return (
    <section className={`strategy-chip-panel${tone ? ` ${tone}` : ""}`}>
      <h4>{title}</h4>
      <div className="strategy-chip-list">
        {normalized.length ? normalized.map((item) => <span key={item} className="mini-tag">{item}</span>) : <span className="strategy-empty">暂无</span>}
      </div>
      {hint ? <p>{hint}</p> : null}
    </section>
  );
}

function strategyParagraphs(text: unknown) {
  const raw = stringValue(text).trim();
  if (!raw) return <p>暂无内容。</p>;
  return raw.split(/\n{2,}/).map((paragraph: string, index: number) => {
    const clean = paragraph.trim();
    const match = clean.match(/^([^：:]{2,22})[：:]\s*([\s\S]*)$/);
    if (match) {
      return (
        <div className="strategy-paragraph-block" key={`${match[1]}-${index}`}>
          <h5>{match[1]}</h5>
          <p>{match[2]}</p>
        </div>
      );
    }
    return <p key={`${clean.slice(0, 24)}-${index}`}>{clean}</p>;
  });
}

function ResultRow({
  field,
  detail,
  editable,
  onChange
}: {
  field: ResultField;
  detail: ConversationDetail;
  editable: boolean;
  onChange: (value: unknown) => void;
}) {
  return (
    <div className="result-row">
      <div className="result-key">{field.label}</div>
      <div className="result-val">
        {editable ? (
          <>
            <textarea
              className="result-editor"
              rows={Array.isArray(field.value) ? Math.min(Math.max(field.value.length, 2), 5) : 2}
              value={editableValue(field.value)}
              placeholder={Array.isArray(field.value) ? "每行一条，可新增或删除" : "输入修改后的内容"}
              onChange={(event) => onChange(parseEditableValue(event.target.value, field.value))}
            />
            {isColorField(field) ? <ColorPreview value={field.value} /> : null}
          </>
        ) : (
          renderValue(field.label, field.value, detail, field.render)
        )}
      </div>
    </div>
  );
}

function renderValue(key: string, value: unknown, detail: ConversationDetail, render?: ResultField["render"]) {
  if (render === "comparison_table" && Array.isArray(value)) {
    return <ComparisonTable rows={value} />;
  }
  const colors = extractColors(value);
  if (colors.length && /color|颜色|色|brand_colors/.test(key)) {
    return (
      <div>
        {formatPreview(value)}
        <ColorPreview value={value} />
      </div>
    );
  }
  const imageRefs = collectReferencedImages(value, detail.assets);
  if (imageRefs.length) {
    return (
      <div className="reference-thumb-list">
        {imageRefs.map((asset) => (
          <a key={asset.id} className="reference-thumb" href={assetContentUrl(projectId(detail), asset.id, true)} download title={asset.filename}>
            <img src={assetContentUrl(projectId(detail), asset.id)} alt={asset.filename} />
            <span>↓</span>
          </a>
        ))}
      </div>
    );
  }
  if (Array.isArray(value)) {
    return (
      <div className="tag-group">
        {value.slice(0, 16).map((item, index) => (
          <span key={`${index}-${formatPreview(item).slice(0, 20)}`} className="mini-tag">
            {formatPreview(item)}
          </span>
        ))}
      </div>
    );
  }
  return <span>{formatPreview(value)}</span>;
}

function ColorPreview({ value }: { value: unknown }) {
  const colors = extractColors(value);
  if (!colors.length) return null;
  return (
    <div className="color-preview-row">
      {colors.map((color) => (
        <span key={color} className="mini-tag color-tag">
          <span className="color-swatch" style={{ backgroundColor: color }} />
          {color}
        </span>
      ))}
    </div>
  );
}

function ComparisonTable({ rows }: { rows: unknown[] }) {
  return (
    <div className="comparison-table">
      <div className="comparison-row comparison-head">
        <div>维度</div>
        <div>竞品</div>
        <div>本品</div>
      </div>
      {rows.slice(0, 6).map((item, index) => {
        const record = asRecord(item);
        return (
          <div className="comparison-row" key={`${stringValue(record.dimension)}-${index}`}>
            <div>{stringValue(record.dimension)}</div>
            <div>{stringValue(record.competitor)}</div>
            <div>{stringValue(record.our_product)}</div>
          </div>
        );
      })}
    </div>
  );
}

function ConceptGrid({ items, projectId, readonly = false }: { items: ConceptItem[]; projectId: string; readonly?: boolean }) {
  const [selected, setSelected] = useState(items[0]?.id ?? "");
  return (
    <div className="concept-grid">
      <div className="concept-label">{readonly ? "图像生成 Agent · 已确认输出" : "图像生成 Agent · 请选择概念图"}</div>
      <div className="concept-cards">
        {items.map((item) => (
          <div
            key={item.id}
            className={`concept-item${selected === item.id ? " selected" : ""}${readonly ? " readonly" : ""}`}
            role={readonly ? undefined : "button"}
            tabIndex={readonly ? undefined : 0}
            onClick={readonly ? undefined : () => setSelected(item.id)}
            onKeyDown={(event) => {
              if (readonly) return;
              if (event.key === "Enter" || event.key === " ") setSelected(item.id);
            }}
          >
            <div className="concept-img">
              {item.assetId ? <img src={assetContentUrl(projectId, item.assetId)} alt={item.title} /> : <span className="concept-missing">暂无图片</span>}
              {selected === item.id ? <span className="concept-check">✓</span> : null}
              {item.assetId ? (
                <a className="concept-download" href={assetContentUrl(projectId, item.assetId, true)} download onClick={(event) => event.stopPropagation()}>
                  ↓
                </a>
              ) : null}
            </div>
            <div className="concept-info">
              <div className="concept-dir">{item.title}</div>
              <div className="concept-desc">{item.description}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Composer({
  value,
  assets,
  selectedAssets,
  pendingUploads,
  projectId,
  disabled,
  pendingGate,
  onFiles,
  onChange,
  onAttachAsset,
  onRemoveAsset,
  onRemovePendingUpload,
  onSubmit
}: {
  value: string;
  assets: AssetRef[];
  selectedAssets: AssetRef[];
  pendingUploads: PendingUpload[];
  projectId: string;
  disabled: boolean;
  pendingGate: boolean;
  onFiles: (files: FileList | null) => void;
  onChange: (value: string) => void;
  onAttachAsset: (asset: AssetRef) => void;
  onRemoveAsset: (assetId: string) => void;
  onRemovePendingUpload: (uploadId: string) => void;
  onSubmit: () => void;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [closedMentionValue, setClosedMentionValue] = useState("");
  const rawMentionQuery = mentionSearchQuery(value);
  const exactMentionMatch =
    rawMentionQuery !== null &&
    [...pendingUploads.map((upload) => upload.name), ...assets.map((asset) => asset.filename)].some((filename) => filename === rawMentionQuery);
  const mentionQuery =
    rawMentionQuery !== null &&
    value !== closedMentionValue &&
    !exactMentionMatch &&
    isActiveMentionQuery(rawMentionQuery)
      ? rawMentionQuery
      : null;
  const mentionOpen = mentionQuery !== null;
  const normalizedMentionQuery = (mentionQuery ?? "").toLowerCase();
  const mentionPendingUploads =
    mentionQuery === null
      ? []
      : pendingUploads.filter((upload) => upload.name.toLowerCase().includes(normalizedMentionQuery)).slice(0, 8);
  const mentionAssets =
    mentionQuery === null
      ? []
      : assets.filter((asset) => asset.filename.toLowerCase().includes(normalizedMentionQuery)).slice(0, 18);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 36), 132)}px`;
  }, [value]);

  return (
    <form className="chat-input-wrap" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <input type="hidden" value="other" readOnly />
      <div className="chat-input-box">
        <div className={`file-chips-area${selectedAssets.length || pendingUploads.length ? "" : " hidden"}`}>
          {pendingUploads.map((upload) => (
            <PendingUploadChip key={upload.id} upload={upload} onRemove={() => onRemovePendingUpload(upload.id)} />
          ))}
          {selectedAssets.map((asset) => (
            <FileChip key={asset.id} asset={asset} projectId={projectId} onRemove={() => onRemoveAsset(asset.id)} />
          ))}
        </div>
        <div className={`composer-divider${selectedAssets.length || pendingUploads.length ? "" : " hidden"}`} />
        <div className={`asset-mention-menu${mentionOpen ? "" : " hidden"}`}>
          <div className="asset-mention-header">引用附件或项目素材</div>
          {mentionPendingUploads.length ? (
            <>
              <div className="asset-mention-section">待发送附件</div>
              {mentionPendingUploads.map((upload) => (
                <button
                  key={upload.id}
                  className={`asset-mention-option ${upload.previewUrl ? "asset-mention-image" : "asset-mention-file"} pending`}
                  type="button"
                  onClick={() => {
                    const nextValue = replaceTrailingMention(value, upload.name);
                    onChange(nextValue);
                    setClosedMentionValue(nextValue);
                  }}
                >
                  {upload.previewUrl ? (
                    <span className="chip-thumb image-thumb">
                      <img src={upload.previewUrl} alt={upload.name} />
                    </span>
                  ) : (
                    <>
                      <span className="ft-file-thumb">{kindShort(upload.kind)}</span>
                      <span className="asset-mention-main">
                        <span className="asset-mention-name">{upload.name}</span>
                        <span className="asset-mention-meta">待发送，发送后写入项目素材</span>
                      </span>
                    </>
                  )}
                </button>
              ))}
            </>
          ) : null}
          {mentionAssets.length ? (
            <>
              <div className="asset-mention-section">项目素材</div>
              {mentionAssets.map((asset) => (
              <button
                key={asset.id}
                className={`asset-mention-option ${isImageAsset(asset) ? "asset-mention-image" : "asset-mention-file"}`}
                type="button"
                onClick={() => {
                  onAttachAsset(asset);
                  const nextValue = replaceTrailingMention(value, asset.filename);
                  onChange(nextValue);
                  setClosedMentionValue(nextValue);
                }}
              >
                {isImageAsset(asset) ? (
                  <span className="chip-thumb image-thumb">
                    <img src={assetContentUrl(projectId, asset.id)} alt={asset.filename} />
                  </span>
                ) : (
                  <>
                    <span className="ft-file-thumb">{kindShort(asset.kind)}</span>
                    <span className="asset-mention-main">
                      <span className="asset-mention-name">{asset.filename}</span>
                      <span className="asset-mention-meta">{kindShort(asset.kind)}</span>
                    </span>
                  </>
                )}
              </button>
              ))}
            </>
          ) : (
            null
          )}
          {!mentionPendingUploads.length && !mentionAssets.length ? (
            <div className="asset-mention-empty">当前没有匹配素材。可先点上传选择文件，文件会进入“待发送附件”。</div>
          ) : null}
        </div>
        <div className="input-row">
          <div className="left-actions">
            <div className={`icon-btn upload-btn${disabled ? " disabled" : ""}`} title="上传文件">
              <Paperclip size={17} weight="regular" aria-hidden="true" />
              <input
                ref={fileRef}
                className="file-input-native"
                type="file"
                multiple
                disabled={disabled}
                onChange={(event) => {
                  onFiles(event.target.files);
                  event.target.value = "";
                }}
              />
            </div>
          </div>
          <div className="textarea-wrap">
            <textarea
              ref={textareaRef}
              className="chat-textarea"
              rows={1}
              value={value}
              disabled={disabled}
              placeholder={pendingGate ? "对当前结果不满意，可以直接输入修改意见；也可以输入“确认”继续..." : "向 PackVision 发送消息..."}
              onChange={(event) => onChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  onSubmit();
                }
              }}
            />
            <span className="attachment-hint">{pendingGate ? "待确认状态下也可发送修正意见，Agent 会重新输出当前节点" : "上传后输入 @ 选择文件"}</span>
          </div>
          <button className="send-btn" type="submit" disabled={disabled || (!value.trim() && !selectedAssets.length && !pendingUploads.length)}>
            <span className="send-icon">➤</span>
          </button>
        </div>
      </div>
    </form>
  );
}

function FileChip({ asset, projectId, onRemove }: { asset: AssetRef; projectId: string; onRemove: () => void }) {
  const isImage = isImageAsset(asset);
  return (
    <div className={isImage ? "file-chip image-only-chip" : "file-chip"}>
      {isImage ? (
        <div className="chip-thumb image-thumb">
          <img src={assetContentUrl(projectId, asset.id)} alt={asset.filename} />
        </div>
      ) : (
        <div className="ft-file-thumb">{kindShort(asset.kind)}</div>
      )}
      {!isImage ? (
        <div className="chip-body">
          <span className="chip-name">{asset.filename}</span>
          <span className="chip-status cs-pending">已上传</span>
        </div>
      ) : null}
      <button className="chip-remove" type="button" onClick={onRemove}>×</button>
    </div>
  );
}

function PendingUploadChip({ upload, onRemove }: { upload: PendingUpload; onRemove: () => void }) {
  if (upload.previewUrl) {
    return (
      <div className="file-chip image-only-chip pending-upload-chip">
        <div className="chip-thumb image-thumb">
          <img src={upload.previewUrl} alt={upload.name} />
        </div>
        <button className="chip-remove" type="button" onClick={onRemove}>×</button>
      </div>
    );
  }
  return (
    <div className="file-chip pending-upload-chip">
      <div className="ft-file-thumb">{kindShort(upload.kind)}</div>
      <div className="chip-body">
        <span className="chip-name">{upload.name}</span>
        <span className="chip-status cs-pending">待发送</span>
      </div>
      <button className="chip-remove" type="button" onClick={onRemove}>×</button>
    </div>
  );
}

function UserManagementSurface({
  users,
  currentUser,
  loading,
  onCreate,
  onUpdate,
  onRefresh
}: {
  users: AuthUser[];
  currentUser?: AuthUser;
  loading: boolean;
  onCreate: (payload: AuthUserCreateRequest) => void;
  onUpdate: (id: string, payload: AuthUserUpdateRequest) => void;
  onRefresh: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [passwordDrafts, setPasswordDrafts] = useState<Record<string, string>>({});
  const sortedUsers = useMemo(
    () => [...users].sort((a, b) => Number(b.role === "admin") - Number(a.role === "admin") || a.email.localeCompare(b.email)),
    [users]
  );
  const canSubmit = email.trim().includes("@") && password.length >= 8;

  return (
    <div className="user-surface">
      <div className="user-head">
        <div>
          <div className="user-eyebrow">Access Control</div>
          <h2>用户管理</h2>
          <p>管理员在这里给新用户创建登录账号。成员登录后只能看到自己名下的项目、素材和任务。</p>
        </div>
        <button className="btn-user secondary" type="button" onClick={onRefresh} disabled={loading}>刷新</button>
      </div>

      <div className="user-grid">
        <section className="user-create-panel">
          <div className="user-panel-title">新增用户</div>
          <form
            className="user-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (!canSubmit) return;
              onCreate({ email: email.trim(), password, role });
              setEmail("");
              setPassword("");
              setRole("member");
            }}
          >
            <label>
              <span>邮箱</span>
              <input value={email} type="email" autoComplete="off" placeholder="name@example.com" onChange={(event) => setEmail(event.target.value)} />
            </label>
            <label>
              <span>初始密码</span>
              <input value={password} type="password" autoComplete="new-password" placeholder="至少 8 位" onChange={(event) => setPassword(event.target.value)} />
            </label>
            <label>
              <span>角色</span>
              <select value={role} onChange={(event) => setRole(event.target.value as "admin" | "member")}>
                <option value="member">成员</option>
                <option value="admin">管理员</option>
              </select>
            </label>
            <button className="btn-user" type="submit" disabled={loading || !canSubmit}>创建账号</button>
            <p className="user-form-note">创建后把邮箱和初始密码给对方，对方直接打开 /app-next/ 登录。</p>
          </form>
        </section>

        <section className="user-list-panel">
          <div className="user-panel-title user-panel-title-split">
            <span>账号列表</span>
            <span>{sortedUsers.length} 个用户</span>
          </div>
          <div className="user-list">
            {sortedUsers.map((user) => {
              const isSelf = currentUser?.id === user.id;
              const passwordDraft = passwordDrafts[user.id] ?? "";
              return (
                <div className={`user-row${user.status === "disabled" ? " disabled" : ""}`} key={user.id}>
                  <div className="user-avatar">{user.email.slice(0, 1).toUpperCase()}</div>
                  <div className="user-info">
                    <strong>{user.email}</strong>
                    <span>{user.id}</span>
                  </div>
                  <select
                    value={user.role}
                    disabled={loading || isSelf}
                    onChange={(event) => onUpdate(user.id, { role: event.target.value as "admin" | "member" })}
                  >
                    <option value="member">成员</option>
                    <option value="admin">管理员</option>
                  </select>
                  <button
                    className={`user-status ${user.status}`}
                    type="button"
                    disabled={loading || isSelf}
                    onClick={() => onUpdate(user.id, { status: user.status === "active" ? "disabled" : "active" })}
                  >
                    {user.status === "active" ? "启用" : "禁用"}
                  </button>
                  <div className="user-password-reset">
                    <input
                      value={passwordDraft}
                      type="password"
                      placeholder="新密码"
                      disabled={loading}
                      onChange={(event) => setPasswordDrafts((current) => ({ ...current, [user.id]: event.target.value }))}
                    />
                    <button
                      className="btn-user secondary"
                      type="button"
                      disabled={loading || passwordDraft.length < 8}
                      onClick={() => {
                        onUpdate(user.id, { password: passwordDraft });
                        setPasswordDrafts((current) => ({ ...current, [user.id]: "" }));
                      }}
                    >
                      重置
                    </button>
                  </div>
                  {isSelf ? <span className="user-self-badge">当前账号</span> : null}
                </div>
              );
            })}
            {!sortedUsers.length ? <div className="user-empty">暂无用户</div> : null}
          </div>
        </section>
      </div>
    </div>
  );
}

type KnowledgeFormState = {
  id?: string;
  title: string;
  domain: KnowledgeDomain;
  workflow_type: KnowledgeWorkflowType;
  status: KnowledgeStatus;
  category: string;
  priority: string;
  tags: string;
  keywords: string;
  content: string;
  source?: string;
};

function KnowledgeSurface({
  entries,
  filters,
  selectedId,
  draft,
  preview,
  currentProjectId,
  onFilter,
  onSelect,
  onNew,
  onDraft,
  onSave,
  onDelete,
  onPreview
}: {
  entries: KnowledgeBaseEntry[];
  filters: { query: string; status: string; domain: string };
  selectedId: string | null;
  draft: KnowledgeFormState;
  preview: string;
  currentProjectId?: string;
  onFilter: (filters: { query: string; status: string; domain: string }) => void;
  onSelect: (entry: KnowledgeBaseEntry) => void;
  onNew: () => void;
  onDraft: (draft: KnowledgeFormState) => void;
  onSave: (payload: KnowledgeBaseCreateRequest & { id?: string }) => void;
  onDelete: (id: string) => void;
  onPreview: () => void;
}) {
  const filtered = entries.filter((entry) => {
    const query = filters.query.trim().toLowerCase();
    if (filters.status !== "all" && entry.status !== filters.status) return false;
    if (filters.domain !== "all" && entry.domain !== filters.domain) return false;
    if (!query) return true;
    return [entry.title, entry.category, ...entry.tags, ...entry.keywords].join(" ").toLowerCase().includes(query);
  });
  return (
    <div className="knowledge-surface">
      <div className="knowledge-head">
        <div>
          <div className="knowledge-eyebrow">Agent Knowledge</div>
          <h2>Agent 知识库</h2>
          <p>把品类原则、包装方法、Badge 规则和出图边界沉淀为可检索知识，Agent 会在执行前自主判断是否调用。</p>
        </div>
        <div className="knowledge-actions">
          <button className="btn-knowledge secondary" type="button" onClick={onPreview} disabled={!currentProjectId}>预览当前项目命中</button>
          <button className="btn-knowledge" type="button" onClick={onNew}>新增知识</button>
        </div>
      </div>
      <div className="knowledge-summary">
        <KnowledgeStat label="总知识" value={entries.length} caption="可被后台维护的规则条目" />
        <KnowledgeStat label="启用中" value={entries.filter((entry) => entry.status === "active").length} caption="会进入 Agent 检索范围" />
        <KnowledgeStat label="草稿" value={entries.filter((entry) => entry.status === "draft").length} caption="仅保存，不建议用于生产" />
        <KnowledgeStat label="包装知识" value={entries.filter((entry) => entry.domain === "packaging").length} caption="包装策略优先调用" />
      </div>
      <div className="knowledge-grid">
        <section className="knowledge-list-panel">
          <div className="knowledge-panel-title knowledge-panel-title-split">
            <span>知识条目</span>
            <span className="knowledge-count">{filtered.length}/{entries.length}</span>
          </div>
          <div className="knowledge-toolbar">
            <input value={filters.query} type="search" placeholder="搜索标题、品类、标签、关键词..." onChange={(event) => onFilter({ ...filters, query: event.target.value })} />
            <div className="knowledge-filter-row">
              <select value={filters.status} onChange={(event) => onFilter({ ...filters, status: event.target.value })}>
                <option value="all">全部状态</option>
                <option value="active">启用</option>
                <option value="draft">草稿</option>
                <option value="inactive">停用</option>
              </select>
              <select value={filters.domain} onChange={(event) => onFilter({ ...filters, domain: event.target.value })}>
                <option value="all">全部领域</option>
                <option value="packaging">包装</option>
                <option value="detail_page">详情页</option>
                <option value="visual">视觉通用</option>
                <option value="general">通用</option>
              </select>
            </div>
          </div>
          <div className="knowledge-list">
            {filtered.map((entry) => (
              <button key={entry.id} className={`knowledge-item${entry.id === selectedId ? " active" : ""}`} type="button" onClick={() => onSelect(entry)}>
                <div className="knowledge-item-title">{entry.title}</div>
                <div className="knowledge-item-meta">{knowledgeWorkflowLabel(entry.workflow_type)} · {knowledgeDomainLabel(entry.domain)} · {entry.category || "未设品类"} · P{entry.priority}</div>
                <div className="knowledge-item-tags">{entry.tags.slice(0, 4).map((tag) => <span key={tag}>{tag}</span>)}</div>
                <span className={`knowledge-status ${entry.status}`}>{knowledgeStatusLabel(entry.status)}</span>
              </button>
            ))}
            {!filtered.length ? <div className="knowledge-empty">暂无知识条目</div> : null}
          </div>
        </section>
        <section className="knowledge-editor-panel">
          <div className="knowledge-panel-title knowledge-panel-title-split">
            <span>知识编辑</span>
            <span className="knowledge-editor-hint">{draft.id ? "编辑条目" : "新条目"}</span>
          </div>
          <form
            className="knowledge-form"
            onSubmit={(event) => {
              event.preventDefault();
              onSave(knowledgePayload(draft));
            }}
          >
            <div className="knowledge-editor-callout">
              <strong>调用边界</strong>
              <span>保存后的知识会进入 Agent 检索，但只能作为方法和原则，不会覆盖产品资料、人工确认结果和 VI 事实。</span>
            </div>
            <label><span>标题</span><input value={draft.title} onChange={(event) => onDraft({ ...draft, title: event.target.value })} /></label>
            <div className="knowledge-form-row">
              <label><span>领域</span><select value={draft.domain} onChange={(event) => onDraft({ ...draft, domain: event.target.value as KnowledgeDomain })}><option value="packaging">包装</option><option value="detail_page">详情页</option><option value="visual">视觉通用</option><option value="general">通用</option></select></label>
              <label><span>流程</span><select value={draft.workflow_type} onChange={(event) => onDraft({ ...draft, workflow_type: event.target.value as KnowledgeWorkflowType })}><option value="packaging">包装</option><option value="detail_page">详情页</option><option value="all">全部</option></select></label>
              <label><span>状态</span><select value={draft.status} onChange={(event) => onDraft({ ...draft, status: event.target.value as KnowledgeStatus })}><option value="active">启用</option><option value="draft">草稿</option><option value="inactive">停用</option></select></label>
            </div>
            <div className="knowledge-form-row">
              <label><span>品类</span><input value={draft.category} onChange={(event) => onDraft({ ...draft, category: event.target.value })} /></label>
              <label><span>优先级</span><input type="number" value={draft.priority} onChange={(event) => onDraft({ ...draft, priority: event.target.value })} /></label>
            </div>
            <label><span>标签</span><input value={draft.tags} onChange={(event) => onDraft({ ...draft, tags: event.target.value })} /></label>
            <label><span>关键词</span><input value={draft.keywords} onChange={(event) => onDraft({ ...draft, keywords: event.target.value })} /></label>
            <label><span>结构内容 JSON</span><em>建议包含 principles、strategy_execution_method、preferred_design_styles、badge_design、risk_notes。</em><textarea value={draft.content} rows={14} spellCheck={false} onChange={(event) => onDraft({ ...draft, content: event.target.value })} /></label>
            <div className="knowledge-form-actions">
              <button className="btn-knowledge" type="submit">保存知识</button>
              <button className="btn-knowledge danger" type="button" disabled={!draft.id} onClick={() => draft.id && onDelete(draft.id)}>删除</button>
            </div>
          </form>
        </section>
        <section className="knowledge-preview-panel">
          <div className="knowledge-panel-title">调用预览</div>
          <div className="knowledge-rule-card">
            <div className="knowledge-rule-title">Agent 调用顺序</div>
            <ol><li>读取项目定义、卖点、VI 和当前工作流</li><li>检索启用状态的相关知识条目</li><li>只注入适用原则，不覆盖事实</li><li>冲突内容进入风险备注</li></ol>
          </div>
          <div className={preview ? "knowledge-preview-card" : "knowledge-preview-empty"}>{preview || "选择当前项目后，可预览 Agent 会命中的知识条目。"}</div>
        </section>
      </div>
    </div>
  );
}

function KnowledgeStat({ label, value, caption }: { label: string; value: number; caption: string }) {
  return <div className="knowledge-stat"><div className="knowledge-stat-label">{label}</div><div className="knowledge-stat-value">{value}</div><div className="knowledge-stat-caption">{caption}</div></div>;
}

type ConfirmState = {
  title: string;
  subtitle?: string;
  body?: string;
  items?: string[];
  confirmText: string;
  danger?: boolean;
  onConfirm: () => void;
};

function ConfirmModal({ state, onClose }: { state: ConfirmState | null; onClose: () => void }) {
  if (!state) return null;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="app-modal">
        <div className="app-modal-head">
          <div><div className="app-modal-title">{state.title}</div><div className="app-modal-subtitle">{state.subtitle}</div></div>
          <button className="modal-close" type="button" onClick={onClose}>×</button>
        </div>
        <div className="app-modal-body">
          {state.body ? <p>{state.body}</p> : null}
          {state.items?.length ? <ul>{state.items.map((item) => <li key={item}>{item}</li>)}</ul> : null}
        </div>
        <div className="app-modal-actions">
          <button className="btn-modal secondary" type="button" onClick={onClose}>取消</button>
          <button className={`btn-modal${state.danger ? " danger" : ""}`} type="button" onClick={() => { state.onConfirm(); onClose(); }}>{state.confirmText}</button>
        </div>
      </div>
    </div>
  );
}

function Toast({ message }: { message: string }) {
  return <div className={`toast${message ? "" : " hidden"}`}>{message}</div>;
}

function buildTimeline(detail: ConversationDetail): TimelineItem[] {
  const items: TimelineItem[] = [
    ...detail.messages.map((message) => ({ kind: "message" as const, id: message.id, createdAt: message.created_at, message })),
    ...detail.review_gates.map((gate) => ({ kind: "gate" as const, id: gate.id, createdAt: gate.created_at ?? "", gate }))
  ];
  return items.sort((a, b) => new Date(a.createdAt || 0).getTime() - new Date(b.createdAt || 0).getTime());
}

function upsertConversationCache(queryClient: QueryClient, detail: ConversationDetail) {
  queryClient.setQueryData<ConversationDetail[]>(["conversations"], (current = []) => {
    const withoutCurrent = current.filter((item) => item.session.id !== detail.session.id);
    return [detail, ...withoutCurrent];
  });
}

function confirmedProgress(detail: ConversationDetail) {
  const total = 8;
  const confirmed = Object.keys(detail.confirmed_context ?? {}).length;
  return { total, confirmed: Math.min(total, confirmed) };
}

function lastMessagePreview(detail: ConversationDetail) {
  return shortText(detail.messages.at(-1)?.content || "等待输入", 24);
}

function resultTitle(gate: ReviewGate) {
  if (gate.type.includes("usp")) return "卖点提炼结果";
  if (gate.type.includes("vi")) return "VI 理解结果";
  if (gate.type.includes("image_prompt")) return "主图提示词结果";
  if (gate.type.includes("strategy")) return "包装策略结果";
  if (gate.type.includes("design")) return "概念图结果";
  return gate.title || "结构化结果";
}

function resultFieldsForGate(gate: ReviewGate, payload: Record<string, unknown>): ResultField[] {
  if (gate.type === "usp_review") return uspSummaryFields(payload);
  if (gate.type === "vi_review") return viSummaryFields(payload);
  if (gate.type === "packaging_strategy_review") return packagingStrategyFields(payload);
  if (gate.type === "image_prompt_review") return imagePromptSummaryFields(payload);
  if (gate.type === "final_design_review") return finalDesignSummaryFields(payload);
  return flattenPayload(payload).slice(0, 18);
}

function packagingStrategyFields(payload: Record<string, unknown>): ResultField[] {
  return [
    { path: ["product_name"], label: "产品品名", value: payload.product_name || "", editable: true },
    { path: ["box_type"], label: "盒型方式", value: payload.box_type || "", editable: true },
    { path: ["front_ratio"], label: "正面比例", value: payload.front_ratio || payload.aspect_ratio || "", editable: true },
    { path: ["side_ratio"], label: "侧面比例", value: payload.side_ratio || "", editable: true },
    { path: ["top_ratio"], label: "顶面比例", value: payload.top_ratio || "", editable: true },
    { path: ["overall_tone"], label: "整体影调", value: payload.overall_tone || "", editable: true },
    { path: ["front_layout"], label: "正面构图", value: payload.front_layout || "", editable: true },
    { path: ["required_copy"], label: "文案层级", value: payload.required_copy || [], editable: true },
    { path: ["required_icons"], label: "标识图标", value: payload.required_icons || [], editable: true },
    { path: ["risk_notes"], label: "风险备注", value: payload.risk_notes || [], editable: true }
  ].filter((field) => field.value !== "");
}

function imagePromptSummaryFields(payload: Record<string, unknown>): ResultField[] {
  return [
    { path: ["main_image_prompt"], label: "主图提示词", value: payload.main_image_prompt || payload.positive_prompt || "", editable: true },
    { path: ["negative_prompt"], label: "负向约束", value: payload.negative_prompt || "", editable: true },
    { path: ["reference_usage"], label: "参考图使用", value: payload.reference_usage || "", editable: true },
    { path: ["layout_notes"], label: "构图说明", value: payload.layout_notes || "", editable: true },
    { path: ["text_overlay_plan"], label: "文字计划", value: payload.text_overlay_plan || [], editable: true },
    { path: ["risk_notes"], label: "风险", value: payload.risk_notes || [], editable: true }
  ].filter((field) => Boolean(field.value) || Array.isArray(field.value));
}

function finalDesignSummaryFields(payload: Record<string, unknown>): ResultField[] {
  if (payload.generation_blocked) {
    return [
      { path: ["reason"], label: "阻塞原因", value: payload.reason || "缺少必要出图资料", editable: false },
      { path: ["required_assets"], label: "需上传", value: payload.required_assets || ["product_image"], editable: false }
    ];
  }
  const outputs = asRecord(payload.generated_outputs);
  const items = Array.isArray(outputs.items) ? outputs.items.map((item) => asRecord(item)) : [];
  const first = items[0] || {};
  const layout = asRecord(first.layout_spec);
  const progress = asRecord(payload.generation_progress);
  const errors = Array.isArray(payload.generation_errors) ? payload.generation_errors.map((item) => asRecord(item)) : [];
  const actualReferenceIds = referenceIdsFromPayload(payload, layout, "actual_reference_asset_ids");
  const plannedReferenceIds = referenceIdsFromPayload(payload, layout, "reference_asset_ids");
  const generationStatus = stringValue(payload.generation_status);
  const shouldShowPlannedReferences = generationStatus === "running" || generationStatus === "" || !items.length;
  const referenceFieldValue = actualReferenceIds.length
    ? actualReferenceIds
    : shouldShowPlannedReferences
      ? plannedReferenceIds
      : "历史结果未记录实际参考图，请重新生成后查看。";
  return [
    { path: ["generation_status"], label: "状态", value: generationStatusLabel(generationStatus), editable: false },
    { path: ["generation_progress"], label: "进度", value: `${Number(progress.completed || 0)}/${Number(progress.total || items.length || 0)}`, editable: false },
    { path: ["generated_outputs", "items"], label: "输出图", value: items.map((item) => item.name || item.direction || item.view || item.asset_id).filter(Boolean), editable: false },
    {
      path: actualReferenceIds.length ? ["actual_reference_asset_ids"] : ["reference_asset_ids"],
      label: actualReferenceIds.length ? "实际参考图" : shouldShowPlannedReferences ? "待用参考图" : "实际参考图",
      value: referenceFieldValue,
      editable: false
    },
    { path: ["generated_outputs", "items", 0, "layout_spec", "image_engine"], label: "出图引擎", value: layout.image_engine || "", editable: false },
    {
      path: ["generation_errors"],
      label: "异常",
      value: errors.length ? errors.map((item) => `${item.name || "unknown"}: ${item.error || ""}`) : layout.image_generation_error || "无",
      editable: false
    },
    { path: ["generated_outputs", "items", 0, "layout_spec", "full_image_prompt"], label: "提示词", value: layout.full_image_prompt || first.prompt || "", editable: false }
  ];
}

function referenceIdsFromPayload(payload: Record<string, unknown>, layout: Record<string, unknown>, key: string) {
  const fromPayload = Array.isArray(payload[key]) ? payload[key] : [];
  const fromLayout = Array.isArray(layout[key]) ? layout[key] : [];
  const ids = [...fromPayload, ...fromLayout]
    .map((item) => String(item || ""))
    .filter(Boolean);
  return Array.from(new Set(ids));
}

function generationStatusLabel(status: string) {
  return ({ running: "生成中", completed: "已完成", partial_failed: "部分完成", failed: "失败" }[status] ?? "待生成");
}

function viSummaryFields(payload: Record<string, unknown>): ResultField[] {
  return [
    { path: ["brand_colors"], label: "品牌色", value: payload.brand_colors || [], editable: true },
    { path: ["logo_asset_id"], label: "LOGO", value: payload.logo_asset_id || "未提供，不虚构 LOGO", editable: false },
    { path: ["typography_notes"], label: "字体", value: payload.typography_notes || "", editable: true },
    { path: ["layout_rules"], label: "版式", value: payload.layout_rules || [], editable: true },
    { path: ["forbidden_rules"], label: "禁用", value: payload.forbidden_rules || [], editable: true },
    { path: ["source_asset_ids"], label: "来源", value: payload.source_asset_ids || [], editable: false }
  ].filter((field) => Boolean(field.value) || Array.isArray(field.value));
}

function uspSummaryFields(payload: Record<string, unknown>): ResultField[] {
  const fields: ResultField[] = [];
  const diagnostics = asRecord(payload.agent_diagnostics);
  const evidence = asRecord(payload.evidence_summary);
  if (diagnostics.backend || diagnostics.model || diagnostics.status) {
    fields.push({
      path: ["agent_diagnostics"],
      label: "模型",
      value: `${diagnostics.backend || "unknown"} / ${diagnostics.model || "unknown"} · ${diagnostics.fallback_used ? "已启用保底" : "真实返回"}`,
      editable: false
    });
  }
  const parsedDocs = Array.isArray(evidence.parsed_documents) ? evidence.parsed_documents.map((item) => asRecord(item)) : [];
  const analyzedImages = Array.isArray(evidence.analyzed_images) ? evidence.analyzed_images.map((item) => asRecord(item)) : [];
  if (parsedDocs.length || analyzedImages.length) {
    fields.push({
      path: ["evidence_summary"],
      label: "资料",
      value: [
        ...parsedDocs.map((item) => `${item.filename || "文档"} ${item.page_count || 0}页`),
        ...analyzedImages.map((item) => `${item.filename || "图片"} ${item.engine || ""}`.trim())
      ].slice(0, 6),
      editable: false
    });
  }

  const core = Array.isArray(payload.core) ? payload.core.map((item) => asRecord(item)).slice(0, 3) : [];
  const secondary = Array.isArray(payload.secondary) ? payload.secondary.map((item) => asRecord(item)).slice(0, 3) : [];

  core.forEach((item, index) => {
    const headline = stringValue(item.headline || item.title);
    const angle = stringValue(item.angle);
    const title = stringValue(item.title) || (headline ? `「${headline}」${angle ? `——${angle}` : ""}` : "");
    fields.push({ path: ["core", index, "title"], label: `核心${index + 1}`, value: title, editable: true });
    fields.push({ path: ["core", index, "content"], label: "卖点内容", value: item.content || item.description || "", editable: true });

    const alignment = asRecord(item.user_alignment);
    if (alignment.parent) fields.push({ path: ["core", index, "user_alignment", "parent"], label: "父母期待", value: alignment.parent, editable: true });
    if (alignment.child) fields.push({ path: ["core", index, "user_alignment", "child"], label: "孩子期待", value: alignment.child, editable: true });
    if (Array.isArray(item.aligned_expectations) && item.aligned_expectations.length) {
      fields.push({ path: ["core", index, "aligned_expectations"], label: "对齐", value: item.aligned_expectations, editable: true });
    }
    if (Array.isArray(item.product_evidence) && item.product_evidence.length) {
      fields.push({ path: ["core", index, "product_evidence"], label: "证据", value: item.product_evidence.slice(0, 4), editable: true });
    }
    if (item.product_visual_evidence) {
      fields.push({ path: ["core", index, "product_visual_evidence"], label: "视觉体现", value: item.product_visual_evidence, editable: true });
    }
    const comparisonRows = Array.isArray(item.competitor_comparison_rows) ? item.competitor_comparison_rows : [];
    if (comparisonRows.length) {
      fields.push({ path: ["core", index, "competitor_comparison_rows"], label: "竞品表", value: comparisonRows, render: "comparison_table", editable: false });
    }
    if (item.competitor_comparison) {
      fields.push({ path: ["core", index, "competitor_comparison"], label: "竞品", value: item.competitor_comparison, editable: true });
    }
    if (item.competitiveness_judgement) {
      fields.push({ path: ["core", index, "competitiveness_judgement"], label: "竞争判断", value: item.competitiveness_judgement, editable: true });
    }
  });

  secondary.forEach((item, index) => {
    const headline = stringValue(item.headline || item.title);
    const angle = stringValue(item.angle);
    const title = stringValue(item.title) || (headline ? `「${headline}」${angle ? `——${angle}` : ""}` : "");
    fields.push({ path: ["secondary", index, "title"], label: `次要${index + 1}`, value: title, editable: true });
    fields.push({ path: ["secondary", index, "content"], label: "说明", value: item.content || item.description || "", editable: true });
    if (Array.isArray(item.product_evidence) && item.product_evidence.length) {
      fields.push({ path: ["secondary", index, "product_evidence"], label: "辅证", value: item.product_evidence.slice(0, 3), editable: true });
    }
  });

  const missing = Array.isArray(evidence.missing_or_failed_assets) ? evidence.missing_or_failed_assets.map((item) => asRecord(item)) : [];
  if (missing.length) {
    fields.push({
      path: ["evidence_summary", "missing_or_failed_assets"],
      label: "未完成",
      value: missing.map((item) => `${item.filename || "资料"}: ${item.reason || ""}`).slice(0, 4),
      editable: false
    });
  }
  if (Array.isArray(payload.notes) && payload.notes.length) {
    fields.push({ path: ["notes"], label: "备注", value: payload.notes.slice(0, 2), editable: false });
  }
  return fields.filter((field) => Boolean(field.value) || Array.isArray(field.value));
}

function conceptItemsFromPayload(payload: Record<string, unknown>, assets: AssetRef[]): ConceptItem[] {
  const raw = getNestedArray(payload, ["generated_outputs", "items"]) || getNestedArray(payload, ["items"]) || getNestedArray(payload, ["concepts"]) || [];
  return raw.map((item, index) => {
    const record = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
    const assetId = String(record.asset_id || record.display_asset_id || record.image_asset_id || "");
    const asset = assets.find((candidate) => candidate.id === assetId);
    return {
      id: String(record.id || assetId || `concept-${index}`),
      assetId: asset?.id || assetId,
      title: String(record.direction || record.title || record.view || asset?.filename || `方案 ${index + 1}`),
      description: String(record.description || record.prompt || "")
    };
  });
}

type ConceptItem = { id: string; assetId: string; title: string; description: string };

function getNestedArray(object: Record<string, unknown>, path: string[]) {
  let cursor: unknown = object;
  for (const key of path) {
    if (!cursor || typeof cursor !== "object") return null;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return Array.isArray(cursor) ? cursor : null;
}

function knowledgeFormState(entry: KnowledgeBaseEntry | KnowledgeBaseCreateRequest): KnowledgeFormState {
  return {
    id: "id" in entry ? entry.id : undefined,
    title: entry.title,
    domain: entry.domain,
    workflow_type: entry.workflow_type,
    status: entry.status,
    category: entry.category,
    priority: String(entry.priority ?? 50),
    tags: entry.tags.join(", "),
    keywords: entry.keywords.join(", "),
    content: JSON.stringify(entry.content || {}, null, 2),
    source: entry.source
  };
}

function knowledgePayload(draft: KnowledgeFormState): KnowledgeBaseCreateRequest & { id?: string } {
  let content: Record<string, unknown> = {};
  try {
    content = JSON.parse(draft.content || "{}") as Record<string, unknown>;
  } catch {
    throw new Error("知识内容 JSON 格式不正确");
  }
  return {
    id: draft.id,
    title: draft.title.trim(),
    domain: draft.domain,
    workflow_type: draft.workflow_type,
    category: draft.category.trim(),
    content,
    tags: splitList(draft.tags),
    keywords: splitList(draft.keywords),
    status: draft.status,
    priority: Number(draft.priority || 50),
    source: draft.source || "manual"
  };
}

function splitList(value: string) {
  return value.split(/[,，、]/).map((item) => item.trim()).filter(Boolean);
}

function healthLabel(health?: { status?: string; llm_backend?: string; deepseek_model_strategy?: string }) {
  if (!health) return "连接中";
  return `${health.status || "ok"} · ${health.llm_backend || "llm"} · ${health.deepseek_model_strategy || "model"}`;
}

function projectTitle(value: string) {
  return value || "新项目对话";
}

function workflowLabel(type: string) {
  if (type === "detail_page") return "详情提案";
  if (type === "packaging") return "包装概念";
  return "Agent";
}

function stageLabel(stage: string) {
  return ({
    collecting_input: "收集输入",
    usp_review: "卖点确认",
    vi_review: "VI 确认",
    packaging_strategy_review: "包装策略确认",
    image_prompt_review: "主图提示词确认",
    detail_strategy_review: "详情策略确认",
    final_design_review: "设计图确认",
    completed: "已完成"
  }[stage] ?? stage.replaceAll("_", " "));
}

function stageShortLabel(stage: string) {
  if (stage === "completed") return "已完成";
  if (stage === "collecting_input") return "进行中";
  return "运行中";
}

function reviewStatus(status: string) {
  return ({ pending: "待确认", approved: "已确认", edited: "已修改", rejected: "已回退", needs_more_info: "需补充" }[status] ?? status);
}

function reviewActionCopy(action: ReviewAction) {
  return ({ approve: "确认无误，进入下一步", edit: "已人工修改并确认", reject: "退回重做", request_more_info: "需要补充资料" }[action] ?? "已处理审核卡片");
}

function safeJsonObject(value: string, fallback: Record<string, unknown>) {
  try {
    const parsed = JSON.parse(value) as unknown;
    return asRecord(parsed, fallback);
  } catch {
    return fallback;
  }
}

function asRecord(value: unknown, fallback: Record<string, unknown> = {}): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : fallback;
}

function arrayOfValues(value: unknown) {
  if (Array.isArray(value)) return value.filter((item) => item !== null && item !== undefined && item !== "");
  if (value === null || value === undefined || value === "") return [];
  return [value];
}

function stringValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(stringValue).filter(Boolean).join("；");
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value ?? "");
}

function editableValue(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => (isPrimitive(item) ? stringValue(item) : JSON.stringify(item))).join("\n");
  if (value === undefined || value === null) return "";
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function parseEditableValue(value: string, originalValue: unknown) {
  const text = value.trim();
  if (Array.isArray(originalValue)) {
    return text
      .split(/\n|,|，|、/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  if (originalValue && typeof originalValue === "object") {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return text;
    }
  }
  return value;
}

function isPrimitive(value: unknown) {
  return value === null || ["string", "number", "boolean", "undefined"].includes(typeof value);
}

function setByPathImmutable(source: Record<string, unknown>, path: Array<string | number>, value: unknown): Record<string, unknown> {
  return setPathValue(source, path, 0, value) as Record<string, unknown>;
}

function setPathValue(source: unknown, path: Array<string | number>, index: number, value: unknown): unknown {
  if (index >= path.length) return value;
  const key = path[index];
  const nextSource = Array.isArray(source) ? source[Number(key)] : asRecord(source)[String(key)];
  const nextValue = setPathValue(nextSource, path, index + 1, value);
  if (Array.isArray(source)) {
    const clone = [...source];
    clone[Number(key)] = nextValue;
    return clone;
  }
  const clone = { ...asRecord(source) };
  clone[String(key)] = nextValue;
  return clone;
}

function isColorField(field: ResultField) {
  return field.path?.includes("brand_colors") || /品牌色|颜色|色彩|color/i.test(field.label);
}

function flattenPayload(payload: Record<string, unknown>, prefix: string[] = []): ResultField[] {
  const fields: ResultField[] = [];
  for (const [key, value] of Object.entries(payload)) {
    if (value === null || value === undefined || value === "") continue;
    const path = [...prefix, key];
    if (value && typeof value === "object" && !Array.isArray(value) && fields.length < 18) {
      const nested = flattenPayload(value as Record<string, unknown>, path);
      if (nested.length) fields.push(...nested);
      else fields.push({ path, label: fieldName(key), value, editable: true });
    } else {
      fields.push({ path, label: fieldName(key), value, editable: true });
    }
    if (fields.length >= 18) break;
  }
  return fields;
}

function fieldName(key: string) {
  return ({
    product_name: "产品品名",
    box_type: "盒型方式",
    aspect_ratio: "画面比例",
    front_layout: "正面构图",
    overall_tone: "整体影调",
    required_copy: "文案",
    required_icons: "图标/标识",
    main_image_prompt: "主图提示词",
    positive_prompt: "正向提示词",
    negative_prompt: "负向约束",
    brand_colors: "品牌色",
    core: "核心卖点",
    secondary: "次要卖点",
    risk_notes: "风险备注",
    generated_images: "生成图",
    image_assets: "图片资产"
  }[key] ?? key);
}

function formatPreview(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => formatPreview(item)).join("；");
  if (value && typeof value === "object") return JSON.stringify(value, null, 2).slice(0, 900);
  return String(value ?? "");
}

function createClientMessageId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function messagePayloadClientId(message: ConversationMessage) {
  const payload = message.payload ?? {};
  return String(payload.client_message_id || payload.clientMessageId || "");
}

function inferAssetKind(file: File): AssetKind {
  const name = file.name.toLowerCase();
  const type = file.type.toLowerCase();
  if (hasAnyTerm(name, ["logo", "标志", "商标", "品牌logo"])) return "logo";
  if (hasAnyTerm(name, ["vi", "视觉规范", "品牌规范", "品牌参考"])) return "vi_document";
  if (hasAnyTerm(name, ["竞品", "爆款", "对手", "竞对", "竞争", "competitor"])) {
    if (type.startsWith("video/") || /\.(mp4|mov|webm|avi|mkv)$/i.test(name)) return "competitor_video";
    if (type.startsWith("image/")) return "competitor_image";
    return "competitor_detail_page";
  }
  if (type.startsWith("image/")) return "product_image";
  if (name.endsWith(".ppt") || name.endsWith(".pptx")) return "product_ppt";
  if (name.endsWith(".pdf")) return "product_pdf";
  return "other";
}

function roleFromAsset(asset: AssetRef) {
  const roleByKind: Partial<Record<AssetKind, string>> = {
    product_image: "product_image",
    product_ppt: "product_intro",
    product_pdf: "product_intro",
    competitor_video: "competitor_info",
    competitor_image: "competitor_info",
    competitor_packaging: "competitor_info",
    competitor_detail_page: "competitor_info",
    vi_document: "vi_reference",
    logo: "logo"
  };
  return roleByKind[asset.kind] ?? "other";
}

function roleFromMessageContext(content: string, asset: AssetRef) {
  const lowered = content.toLowerCase();
  for (const position of assetMentionPositions(content, asset)) {
    const role = nearestRoleLabel(lowered, position);
    if (role) return role;
  }
  return "";
}

function assetMentionPositions(content: string, asset: AssetRef) {
  const candidates = new Set([
    asset.filename,
    String(asset.metadata?.display_name ?? ""),
    String(asset.metadata?.original_filename ?? ""),
    asset.filename.replace(/\.[A-Za-z0-9]+$/, "")
  ].filter(Boolean));
  const positions: number[] = [];
  for (const candidate of candidates) {
    const marker = `@${candidate}`;
    let index = content.indexOf(marker);
    while (index >= 0) {
      positions.push(index);
      index = content.indexOf(marker, index + marker.length);
    }
  }
  return Array.from(new Set(positions)).sort((a, b) => a - b);
}

function nearestRoleLabel(loweredContent: string, mentionPosition: number) {
  const roleTerms: Array<[string, string[]]> = [
    ["logo", ["logo", "标志", "商标", "品牌logo"]],
    ["vi_reference", ["vi", "品牌规范", "视觉规范", "品牌参考"]],
    ["competitor_info", ["竞品图", "竞品资料", "竞品", "爆款", "对手", "竞对", "竞争", "competitor"]],
    [
      "product_image",
      [
        "产品拼装图",
        "产品收藏系列",
        "产品系列",
        "产品参考图",
        "产品图",
        "产品图片",
        "参考图",
        "主图",
        "效果图",
        "产品参考",
        "拼装图",
        "系列图",
        "收藏系列",
        "产品外观"
      ]
    ],
    ["product_intro", ["产品介绍ppt", "产品介绍", "产品资料", "产品ppt", "产品 pdf", "产品文档", "介绍", "说明书"]]
  ];
  const before = loweredContent.slice(Math.max(0, mentionPosition - 48), mentionPosition);
  let best: { index: number; role: string } | null = null;
  for (const [role, terms] of roleTerms) {
    for (const term of terms) {
      const index = before.lastIndexOf(term);
      if (index >= 0 && (!best || index > best.index)) best = { index, role };
    }
  }
  if (best) return best.role;
  const after = loweredContent.slice(mentionPosition, mentionPosition + 40);
  for (const [role, terms] of roleTerms) {
    if (terms.some((term) => after.includes(term))) return role;
  }
  return "";
}

function hasAnyTerm(value: string, terms: string[]) {
  return terms.some((term) => value.includes(term));
}

function kindShort(kind: AssetKind) {
  const labels: Partial<Record<AssetKind, string>> = {
    product_ppt: "PPT",
    product_pdf: "PDF",
    product_image: "IMG",
    competitor_video: "VID",
    competitor_image: "IMG",
    competitor_packaging: "PKG",
    competitor_detail_page: "DTL",
    vi_document: "VI",
    logo: "LG",
    mask_image: "MASK",
    transparent_product_image: "PNG",
    other: "FILE"
  };
  return labels[kind] ?? "FILE";
}

function uniqueAssets(assets: AssetRef[]) {
  const map = new Map<string, AssetRef>();
  for (const asset of assets) map.set(asset.id, asset);
  return Array.from(map.values());
}

function uniquePendingUploads(uploads: PendingUpload[]) {
  const map = new Map<string, PendingUpload>();
  for (const upload of uploads) {
    if (map.has(upload.id)) {
      if (upload.previewUrl) URL.revokeObjectURL(upload.previewUrl);
      continue;
    }
    map.set(upload.id, upload);
  }
  return Array.from(map.values());
}

function revokePendingUploads(uploads: PendingUpload[]) {
  for (const upload of uploads) {
    if (upload.previewUrl) URL.revokeObjectURL(upload.previewUrl);
  }
}

function uploadedAssetFallback(assets: AssetRef[], file: File) {
  const exact = assets.filter((asset) => asset.filename === file.name);
  if (exact.length) return exact.slice(-1);
  const basename = file.name.toLowerCase();
  const fuzzy = assets.filter((asset) => asset.filename.toLowerCase().includes(basename) || basename.includes(asset.filename.toLowerCase()));
  if (fuzzy.length) return fuzzy.slice(-1);
  return assets.slice(-1);
}

function isImageAsset(asset: AssetRef) {
  return Boolean(asset.mime_type?.startsWith("image/")) || /\.(png|jpe?g|webp|gif)$/i.test(asset.filename);
}

function assetContentUrl(projectId: string, assetId: string, download = false) {
  return `/api/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}/content${download ? "?download=true" : ""}`;
}

function projectId(detail: ConversationDetail) {
  return detail.project?.id || detail.session.project_id || "";
}

function collectReferencedImages(value: unknown, assets: AssetRef[]) {
  const text = JSON.stringify(value) || "";
  return assets.filter((asset) => isImageAsset(asset) && text.includes(asset.id)).slice(0, 6);
}

function extractColors(value: unknown) {
  const text = Array.isArray(value) ? value.join(" ") : String(value ?? "");
  return Array.from(new Set(text.match(/#[0-9a-fA-F]{6}\b/g) ?? []));
}

function mentionSearchQuery(value: string) {
  const match = value.match(/@([^\s@]*)$/);
  return match ? match[1] : null;
}

function isActiveMentionQuery(query: string) {
  if (query.length > 24) return false;
  if (/\.(png|jpe?g|webp|gif|pptx?|pdf|docx?|xlsx?|xls)$/i.test(query)) return false;
  return true;
}

function replaceTrailingMention(value: string, filename: string) {
  const token = `@${filename} `;
  return /@([^\s@]*)$/.test(value)
    ? value.replace(/@([^\s@]*)$/, token)
    : `${value}${value ? " " : ""}${token}`;
}

function shortText(value: string, length: number) {
  const clean = value.replace(/\s+/g, " ").trim();
  return clean.length > length ? `${clean.slice(0, length)}...` : clean;
}

function messageTypeLabel(type: string) {
  return ({ status: "状态", tool_call: "工具调用", tool_result: "工具结果", review_action: "人工操作", planner_decision: "调度决策" }[type] ?? type);
}

function taskKindLabel(kind: string) {
  const labels: Record<string, string> = {
    agent_run: "Agent 任务",
    asset_processing: "素材解析",
    image_generation: "生图任务"
  };
  return labels[kind] ?? kind;
}

function taskStatusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "运行中",
    failed: "失败",
    cancelled: "已取消",
    succeeded: "完成"
  };
  return labels[status] ?? status;
}

function messageRunStatus(message: ConversationMessage) {
  const payload = message.payload ?? {};
  const rawStatus = String(payload.status || payload.processing_status || "");
  if (payload.error || payload.generation_error || rawStatus === "failed") return "failed";
  if (rawStatus === "running" || payload.background || payload.queued_asset_ids) return "running";
  if (payload.waiting_assets || rawStatus === "queued") return "waiting";
  if (message.message_type === "tool_result" || rawStatus === "completed") return "done";
  return message.message_type === "status" ? "done" : "running";
}

function statusBadgeClass(status: string) {
  return ({ running: "running", waiting: "waiting", failed: "error", done: "done" }[status] ?? "done");
}

function statusLabel(status: string) {
  return ({ running: "运行中", waiting: "等待中", failed: "失败", done: "完成" }[status] ?? "完成");
}

function knowledgeWorkflowLabel(value: string) {
  return ({ all: "全部", packaging: "包装", detail_page: "详情页" }[value] ?? value);
}

function knowledgeStatusLabel(value: string) {
  return ({ active: "启用", draft: "草稿", inactive: "停用" }[value] ?? value);
}

function knowledgeDomainLabel(value: string) {
  return ({ packaging: "包装", detail_page: "详情页", visual: "视觉", general: "通用" }[value] ?? value);
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return "操作失败，请稍后重试。";
}
