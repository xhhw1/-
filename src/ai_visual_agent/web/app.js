const state = {
  conversations: [],
  selectedConversationId: null,
  detail: null,
  busy: false,
  pendingAssetFile: null,
  pendingAssetFiles: [],
  uploadIntent: "product_ppt",
  editDrafts: new Map(),
  editingGates: new Set(),
  memoryCollapsed: new Set(),
  selectedConcepts: new Map(),
  selectedConversationIds: new Set(),
  composerDrafts: new Map(),
  projectManageMode: false,
  refreshTimer: null,
  lastDetailSignature: "",
  draftConversation: false,
  activeView: "chat",
  knowledgeEntries: [],
  selectedKnowledgeId: null,
  knowledgeFilters: {
    query: "",
    status: "all",
    domain: "all",
  },
};

const els = {
  systemStatus: document.querySelector("#systemStatus"),
  refreshBtn: document.querySelector("#refreshBtn"),
  newConversationBtn: document.querySelector("#newConversationBtn"),
  manageProjectsBtn: document.querySelector("#manageProjectsBtn"),
  knowledgeBtn: document.querySelector("#knowledgeBtn"),
  conversationCount: document.querySelector("#conversationCount"),
  conversationList: document.querySelector("#conversationList"),
  emptyState: document.querySelector("#emptyState"),
  chatSurface: document.querySelector("#chatSurface"),
  knowledgeSurface: document.querySelector("#knowledgeSurface"),
  conversationTitleTop: document.querySelector("#conversationTitleTop"),
  conversationStage: document.querySelector("#conversationStage"),
  conversationTitle: document.querySelector("#conversationTitle"),
  conversationProgressBadge: document.querySelector("#conversationProgressBadge"),
  workflowBadge: document.querySelector("#workflowBadge"),
  deleteConversationBtn: document.querySelector("#deleteConversationBtn"),
  cleanupAssetsBtn: document.querySelector("#cleanupAssetsBtn"),
  selectionBar: document.querySelector("#selectionBar"),
  selectedCount: document.querySelector("#selectedCount"),
  manageProjectsLabel: document.querySelector("#manageProjectsLabel"),
  selectAllBtn: document.querySelector("#selectAllBtn"),
  batchDeleteBtn: document.querySelector("#batchDeleteBtn"),
  clearSelectionBtn: document.querySelector("#clearSelectionBtn"),
  messageStream: document.querySelector("#messageStream"),
  composerForm: document.querySelector("#composerForm"),
  messageInput: document.querySelector("#messageInput"),
  sendBtn: document.querySelector("#sendBtn"),
  assetKindInput: document.querySelector("#assetKindInput"),
  assetFileInput: document.querySelector("#assetFileInput"),
  attachmentDropzone: document.querySelector("#attachmentDropzone"),
  selectedAssetPreview: document.querySelector("#selectedAssetPreview"),
  assetFileName: document.querySelector("#assetFileName"),
  assetFileMeta: document.querySelector("#assetFileMeta"),
  assetUploadHint: document.querySelector("#assetUploadHint"),
  clearAssetBtn: document.querySelector("#clearAssetBtn"),
  uploadFileBtn: document.querySelector("#uploadFileBtn"),
  fileChipsArea: document.querySelector("#fileChipsArea"),
  composerDivider: document.querySelector("#composerDivider"),
  assetMentionMenu: document.querySelector("#assetMentionMenu"),
  toast: document.querySelector("#toast"),
  confirmModal: document.querySelector("#confirmModal"),
  confirmModalTitle: document.querySelector("#confirmModalTitle"),
  confirmModalSubtitle: document.querySelector("#confirmModalSubtitle"),
  confirmModalBody: document.querySelector("#confirmModalBody"),
  confirmModalCancel: document.querySelector("#confirmModalCancel"),
  confirmModalConfirm: document.querySelector("#confirmModalConfirm"),
  confirmModalClose: document.querySelector("#confirmModalClose"),
  knowledgeList: document.querySelector("#knowledgeList"),
  knowledgeForm: document.querySelector("#knowledgeForm"),
  knowledgeId: document.querySelector("#knowledgeId"),
  knowledgeTitle: document.querySelector("#knowledgeTitle"),
  knowledgeDomain: document.querySelector("#knowledgeDomain"),
  knowledgeWorkflow: document.querySelector("#knowledgeWorkflow"),
  knowledgeStatus: document.querySelector("#knowledgeStatus"),
  knowledgeCategory: document.querySelector("#knowledgeCategory"),
  knowledgePriority: document.querySelector("#knowledgePriority"),
  knowledgeTags: document.querySelector("#knowledgeTags"),
  knowledgeKeywords: document.querySelector("#knowledgeKeywords"),
  knowledgeContent: document.querySelector("#knowledgeContent"),
  knowledgePreview: document.querySelector("#knowledgePreview"),
  knowledgePreviewBtn: document.querySelector("#knowledgePreviewBtn"),
  newKnowledgeBtn: document.querySelector("#newKnowledgeBtn"),
  deleteKnowledgeBtn: document.querySelector("#deleteKnowledgeBtn"),
  knowledgeSummary: document.querySelector("#knowledgeSummary"),
  knowledgeSearch: document.querySelector("#knowledgeSearch"),
  knowledgeStatusFilter: document.querySelector("#knowledgeStatusFilter"),
  knowledgeDomainFilter: document.querySelector("#knowledgeDomainFilter"),
  knowledgeListCount: document.querySelector("#knowledgeListCount"),
  knowledgeEditorHint: document.querySelector("#knowledgeEditorHint"),
};

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const response = await fetch(path, {
    headers: isForm ? options.headers || {} : { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function loadHealth() {
  const health = await api("/health");
  const model = health.deepseek_model_strategy ? ` · ${health.deepseek_model_strategy}` : "";
  els.systemStatus.textContent = `API ${health.status} · ${health.llm_backend}${model}`;
}

async function loadConversations() {
  state.conversations = await api("/api/conversations");
  const validIds = new Set(state.conversations.map((item) => item.session.id));
  state.selectedConversationIds = new Set([...state.selectedConversationIds].filter((id) => validIds.has(id)));
  if (state.draftConversation) {
    renderConversationList();
    renderConversationDetail();
    applyActiveView();
    return;
  }
  if (
    state.selectedConversationId &&
    !state.conversations.some((item) => item.session.id === state.selectedConversationId)
  ) {
    state.selectedConversationId = null;
    state.detail = null;
  }
  if (!state.selectedConversationId && state.conversations.length) {
    state.selectedConversationId = state.conversations[0].session.id;
  }
  renderConversationList();
  if (state.selectedConversationId) {
    await loadConversation(state.selectedConversationId);
  } else {
    renderEmpty();
  }
  applyActiveView();
}

async function loadConversation(sessionId) {
  if (state.draftConversation || (state.selectedConversationId && state.selectedConversationId !== sessionId)) {
    saveComposerDraft();
  }
  state.draftConversation = false;
  state.selectedConversationId = sessionId;
  state.detail = await api(`/api/conversations/${sessionId}`);
  state.lastDetailSignature = detailSignature(state.detail);
  restoreComposerDraft(sessionId);
  renderConversationList();
  renderConversationDetail();
  updateComposerState();
  applyActiveView();
}

async function refreshActiveConversation({ force = false } = {}) {
  if (state.draftConversation) return;
  if (!state.selectedConversationId) return;
  const detail = await api(`/api/conversations/${state.selectedConversationId}`);
  const signature = detailSignature(detail);
  if (!force && signature === state.lastDetailSignature) return;
  state.detail = detail;
  state.lastDetailSignature = signature;
  const index = state.conversations.findIndex((item) => item.session.id === detail.session.id);
  if (index >= 0) {
    state.conversations[index] = detail;
  } else {
    state.conversations.unshift(detail);
  }
  renderConversationList();
  renderConversationDetail({ preferReviewGate: Boolean(detail.pending_review_gate) });
  applyActiveView();
}

function switchView(view) {
  state.activeView = view;
  if (view === "knowledge") {
    loadKnowledgeEntries().catch(showError);
  }
  applyActiveView();
}

function applyActiveView() {
  const knowledgeActive = state.activeView === "knowledge";
  els.knowledgeSurface?.classList.toggle("hidden", !knowledgeActive);
  els.chatSurface?.classList.toggle("hidden", knowledgeActive || !state.detail);
  els.emptyState?.classList.toggle("hidden", knowledgeActive || Boolean(state.detail));
  els.knowledgeBtn?.classList.toggle("active", knowledgeActive);
  if (knowledgeActive) {
    els.conversationTitleTop.textContent = "Agent 知识库";
    els.workflowBadge.textContent = "Knowledge";
    els.workflowBadge.className = "mn-top-model-badge";
    renderConversationProgressBadge(null);
    els.deleteConversationBtn.classList.add("hidden");
  } else {
    els.deleteConversationBtn.classList.remove("hidden");
    if (state.detail) {
      const session = state.detail.session;
      els.conversationTitleTop.textContent = displayConversationTitle(state.detail);
      els.workflowBadge.textContent = workflowLabel(session.workflow_type);
      els.workflowBadge.className = `tag ${session.workflow_type === "unknown" ? "draft" : "review"}`;
      renderConversationProgressBadge(state.detail);
    } else {
      els.conversationTitleTop.textContent = "PackVision 1.0";
      els.workflowBadge.textContent = "Agent";
      els.workflowBadge.className = "mn-top-model-badge";
      renderConversationProgressBadge(null);
    }
  }
}

async function loadKnowledgeEntries() {
  state.knowledgeEntries = await api("/api/knowledge");
  if (!state.selectedKnowledgeId && state.knowledgeEntries.length) {
    state.selectedKnowledgeId = state.knowledgeEntries[0].id;
  }
  if (state.selectedKnowledgeId && !state.knowledgeEntries.some((entry) => entry.id === state.selectedKnowledgeId)) {
    state.selectedKnowledgeId = state.knowledgeEntries[0]?.id || null;
  }
  renderKnowledgeSurface();
}

function renderKnowledgeSurface() {
  renderKnowledgeSummary();
  renderKnowledgeList();
  const entry = state.knowledgeEntries.find((item) => item.id === state.selectedKnowledgeId) || null;
  fillKnowledgeForm(entry);
}

function filteredKnowledgeEntries() {
  const query = state.knowledgeFilters.query.trim().toLowerCase();
  return state.knowledgeEntries.filter((entry) => {
    if (state.knowledgeFilters.status !== "all" && entry.status !== state.knowledgeFilters.status) return false;
    if (state.knowledgeFilters.domain !== "all" && entry.domain !== state.knowledgeFilters.domain) return false;
    if (!query) return true;
    const haystack = [
      entry.title,
      entry.category,
      entry.domain,
      entry.workflow_type,
      ...(entry.tags || []),
      ...(entry.keywords || []),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

function renderKnowledgeSummary() {
  if (!els.knowledgeSummary) return;
  const total = state.knowledgeEntries.length;
  const active = state.knowledgeEntries.filter((entry) => entry.status === "active").length;
  const draft = state.knowledgeEntries.filter((entry) => entry.status === "draft").length;
  const packaging = state.knowledgeEntries.filter((entry) => entry.domain === "packaging").length;
  const selected = state.knowledgeEntries.find((entry) => entry.id === state.selectedKnowledgeId);
  els.knowledgeSummary.replaceChildren(
    knowledgeStatCard("总知识", total, "可被后台维护的规则条目"),
    knowledgeStatCard("启用中", active, "会进入 Agent 检索范围"),
    knowledgeStatCard("草稿", draft, "仅保存，不建议用于生产"),
    knowledgeStatCard("包装知识", packaging, selected ? `当前：${selected.title}` : "包装策略优先调用")
  );
}

function knowledgeStatCard(label, value, caption) {
  const card = document.createElement("div");
  card.className = "knowledge-stat";
  const top = document.createElement("div");
  top.className = "knowledge-stat-label";
  top.textContent = label;
  const number = document.createElement("div");
  number.className = "knowledge-stat-value";
  number.textContent = String(value);
  const text = document.createElement("div");
  text.className = "knowledge-stat-caption";
  text.textContent = caption;
  card.append(top, number, text);
  return card;
}

function renderKnowledgeList() {
  if (!els.knowledgeList) return;
  els.knowledgeList.replaceChildren();
  const entries = filteredKnowledgeEntries();
  if (els.knowledgeListCount) {
    els.knowledgeListCount.textContent = `${entries.length}/${state.knowledgeEntries.length}`;
  }
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "knowledge-empty";
    empty.textContent = state.knowledgeEntries.length ? "没有符合筛选条件的知识条目" : "暂无知识条目";
    els.knowledgeList.append(empty);
    return;
  }
  for (const entry of entries) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `knowledge-item${entry.id === state.selectedKnowledgeId ? " active" : ""}`;
    const title = document.createElement("div");
    title.className = "knowledge-item-title";
    title.textContent = entry.title;
    const meta = document.createElement("div");
    meta.className = "knowledge-item-meta";
    meta.textContent = `${knowledgeWorkflowLabel(entry.workflow_type)} · ${knowledgeDomainLabel(entry.domain)} · ${entry.category || "未设品类"} · P${entry.priority}`;
    const tags = document.createElement("div");
    tags.className = "knowledge-item-tags";
    for (const tag of (entry.tags || []).slice(0, 4)) {
      const chip = document.createElement("span");
      chip.textContent = tag;
      tags.append(chip);
    }
    const status = document.createElement("span");
    status.className = `knowledge-status ${entry.status}`;
    status.textContent = knowledgeStatusLabel(entry.status);
    button.append(title, meta, tags, status);
    button.addEventListener("click", () => {
      state.selectedKnowledgeId = entry.id;
      renderKnowledgeSurface();
    });
    els.knowledgeList.append(button);
  }
}

function fillKnowledgeForm(entry) {
  if (!els.knowledgeForm) return;
  const safeEntry = entry || {
    id: "",
    title: "",
    domain: "packaging",
    workflow_type: "packaging",
    category: "",
    status: "active",
    priority: 50,
    tags: [],
    keywords: [],
    content: {
      principles: [],
      preferred_design_styles: [],
      badge_design: {},
      risk_notes: [],
    },
  };
  els.knowledgeId.value = safeEntry.id || "";
  els.knowledgeTitle.value = safeEntry.title || "";
  els.knowledgeDomain.value = safeEntry.domain || "packaging";
  els.knowledgeWorkflow.value = safeEntry.workflow_type || "packaging";
  els.knowledgeStatus.value = safeEntry.status || "active";
  els.knowledgeCategory.value = safeEntry.category || "";
  els.knowledgePriority.value = String(safeEntry.priority ?? 50);
  els.knowledgeTags.value = (safeEntry.tags || []).join(", ");
  els.knowledgeKeywords.value = (safeEntry.keywords || []).join(", ");
  els.knowledgeContent.value = JSON.stringify(safeEntry.content || {}, null, 2);
  els.deleteKnowledgeBtn.disabled = !safeEntry.id;
  if (els.knowledgeEditorHint) {
    els.knowledgeEditorHint.textContent = safeEntry.id ? `${knowledgeStatusLabel(safeEntry.status)} · ${safeEntry.source || "manual"}` : "新条目";
  }
}

function newKnowledgeDraft() {
  state.selectedKnowledgeId = null;
  fillKnowledgeForm(null);
  renderKnowledgeSummary();
  renderKnowledgeList();
  els.knowledgeTitle?.focus();
}

async function saveKnowledgeEntry(event) {
  event.preventDefault();
  const payload = knowledgeFormPayload();
  if (!payload.title.trim()) {
    showToast("请填写知识标题");
    return;
  }
  const entryId = els.knowledgeId.value.trim();
  const saved = entryId
    ? await api(`/api/knowledge/${entryId}`, { method: "PATCH", body: JSON.stringify(payload) })
    : await api("/api/knowledge", { method: "POST", body: JSON.stringify(payload) });
  state.selectedKnowledgeId = saved.id;
  await loadKnowledgeEntries();
  showToast("知识已保存");
}

function knowledgeFormPayload() {
  let content = {};
  try {
    content = JSON.parse(els.knowledgeContent.value || "{}");
  } catch (_error) {
    throw new Error("知识内容 JSON 格式不正确");
  }
  return {
    title: els.knowledgeTitle.value.trim(),
    domain: els.knowledgeDomain.value,
    workflow_type: els.knowledgeWorkflow.value,
    category: els.knowledgeCategory.value.trim(),
    status: els.knowledgeStatus.value,
    priority: Number(els.knowledgePriority.value || 50),
    tags: splitListInput(els.knowledgeTags.value),
    keywords: splitListInput(els.knowledgeKeywords.value),
    content,
    source: "manual",
  };
}

async function deleteSelectedKnowledgeEntry() {
  const entryId = els.knowledgeId.value.trim();
  if (!entryId) return;
  const entry = state.knowledgeEntries.find((item) => item.id === entryId);
  const ok = await appConfirm({
    title: "删除知识条目？",
    subtitle: "此操作不可撤销",
    body: "删除后 Agent 将不再调用该知识。",
    items: [entry?.title || entryId],
    confirmText: "确认删除",
    danger: true,
  });
  if (!ok) return;
  await api(`/api/knowledge/${entryId}`, { method: "DELETE" });
  state.selectedKnowledgeId = null;
  await loadKnowledgeEntries();
  showToast("知识已删除");
}

async function previewCurrentProjectKnowledge() {
  if (!state.detail?.project?.id) {
    els.knowledgePreview.textContent = "请先选择一个项目，再预览知识调用。";
    return;
  }
  const preview = await api(`/api/projects/${state.detail.project.id}/knowledge/preview`, { method: "POST" });
  renderKnowledgePreview(preview);
}

function renderKnowledgePreview(preview) {
  els.knowledgePreview.replaceChildren();
  if (!preview.results?.length) {
    els.knowledgePreview.textContent = "当前项目没有命中特定知识，Agent 会使用通用包装策略方法。";
    return;
  }
  const summary = document.createElement("div");
  summary.className = "knowledge-preview-summary";
  summary.textContent = preview.injected_context?.instruction || "已命中知识库。";
  els.knowledgePreview.append(summary);
  for (const result of preview.results) {
    const card = document.createElement("div");
    card.className = "knowledge-preview-card";
    const title = document.createElement("div");
    title.className = "knowledge-preview-title";
    title.textContent = result.entry.title;
    const meta = document.createElement("div");
    meta.className = "knowledge-preview-meta";
    meta.textContent = `score ${result.score} · ${result.matched_keywords?.join("、") || "语义匹配"}`;
    card.append(title, meta);
    els.knowledgePreview.append(card);
  }
}

function splitListInput(value) {
  return String(value || "")
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function knowledgeWorkflowLabel(value) {
  if (value === "packaging") return "包装";
  if (value === "detail_page") return "详情页";
  return "全部";
}

function knowledgeStatusLabel(value) {
  if (value === "active") return "启用";
  if (value === "inactive") return "停用";
  return "草稿";
}

function knowledgeDomainLabel(value) {
  if (value === "packaging") return "包装";
  if (value === "detail_page") return "详情页";
  if (value === "visual") return "视觉通用";
  return "通用";
}

function detailSignature(detail) {
  if (!detail) return "";
  const session = detail.session || {};
  const pending = detail.pending_review_gate || {};
  const latest = detail.messages?.[detail.messages.length - 1] || {};
  return [
    session.id,
    session.updated_at,
    session.current_stage,
    detail.messages?.length || 0,
    latest.id || "",
    pending.id || "",
    pending.status || "",
    pending.updated_at || pending.resolved_at || "",
    Object.keys(detail.confirmed_context || {}).join(","),
    detail.assets?.length || 0,
    (detail.assets || [])
      .map((asset) => `${asset.id}:${asset.metadata?.processing?.status || ""}:${asset.metadata?.processing?.progress || 0}:${(asset.metadata?.role_bindings || []).length}`)
      .join(","),
  ].join("|");
}

function startAutoRefresh() {
  if (state.refreshTimer) window.clearInterval(state.refreshTimer);
  state.refreshTimer = window.setInterval(() => {
    refreshActiveConversation().catch(showError);
  }, 2200);
}

function currentComposerDraftKey() {
  if (state.draftConversation) return "draft";
  return state.selectedConversationId || "";
}

function saveComposerDraft() {
  const key = currentComposerDraftKey();
  if (!key || !els.messageInput) return;
  state.composerDrafts.set(key, {
    text: els.messageInput.value || "",
    files: [...getSelectedAssetFiles()],
  });
}

function restoreComposerDraft(key = currentComposerDraftKey()) {
  const draft = state.composerDrafts.get(key) || { text: "", files: [] };
  els.messageInput.value = draft.text || "";
  state.pendingAssetFiles = [...(draft.files || [])];
  state.pendingAssetFile = state.pendingAssetFiles[0] || null;
  els.assetFileInput.value = "";
  resizeTextarea();
  updateAttachmentPreview(state.pendingAssetFile);
  hideAssetMentionMenu();
}

function clearComposerDraft(key = currentComposerDraftKey()) {
  if (key) state.composerDrafts.delete(key);
}

async function createBlankConversation() {
  saveComposerDraft();
  state.composerDrafts.delete("draft");
  state.draftConversation = true;
  state.selectedConversationId = null;
  state.detail = draftConversationDetail();
  state.lastDetailSignature = detailSignature(state.detail);
  restoreComposerDraft("draft");
  renderConversationList();
  renderConversationDetail();
  showToast("已新建草稿，发送后才会创建项目");
}

function draftConversationDetail() {
  const now = new Date().toISOString();
  return {
    session: {
      id: "draft",
      project_id: "draft-project",
      title: "新项目对话",
      workflow_type: "unknown",
      status: "active",
      current_stage: "collecting_input",
      confirmed_context: {},
      created_at: now,
      updated_at: now,
    },
    project: {
      id: "draft-project",
      workflow_type: "packaging",
      brief: {
        category: "",
        target_user: "",
        user_expectations: [],
        user_metrics: [],
        value_proposition: "",
        core_product_definition: "",
        raw_text: "",
      },
      assets: [],
      status: "draft",
      created_at: now,
      updated_at: now,
    },
    messages: [],
    review_gates: [],
    pending_review_gate: null,
    confirmed_context: {},
    assets: [],
  };
}

async function ensureConversationForAssetUpload() {
  if (state.selectedConversationId) return;
  const detail = await api("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "新项目对话", workflow_type: "unknown" }),
  });
  state.draftConversation = false;
  state.selectedConversationId = detail.session.id;
  state.detail = detail;
  state.lastDetailSignature = detailSignature(detail);
  state.conversations.unshift(detail);
  renderConversationList();
  renderConversationDetail();
}

async function deleteSelectedConversation() {
  if (state.draftConversation) {
    state.draftConversation = false;
    state.selectedConversationId = null;
    state.detail = null;
    renderEmpty();
    showToast("草稿已关闭");
    return;
  }
  if (!state.selectedConversationId || !state.detail) return;
  const title = state.detail.session.title || "当前对话";
  const ok = await appConfirm({
    title: "删除当前项目？",
    subtitle: "此操作不可撤销",
    items: [title],
    body: "删除后将同步清理该项目相关的项目记录、素材文件、会话消息、人工确认卡、审计记录和项目记忆。",
    confirmText: "删除项目",
    danger: true,
  });
  if (!ok) return;
  setBusy(true);
  try {
    await api(`/api/conversations/${state.selectedConversationId}`, { method: "DELETE" });
    state.selectedConversationId = null;
    state.detail = null;
    await loadConversations();
    showToast("对话已删除");
  } finally {
    setBusy(false);
  }
}

async function batchDeleteSelectedConversations() {
  const ids = [...state.selectedConversationIds];
  if (!ids.length) {
    showToast("请先勾选要删除的项目");
    return;
  }
  const titles = state.conversations
    .filter((item) => state.selectedConversationIds.has(item.session.id))
    .map((item) => item.session.title || "未命名项目")
    .slice(0, 5);
  const moreText = ids.length > titles.length ? `另有 ${ids.length - titles.length} 个项目未展开显示` : "";
  const ok = await appConfirm({
    title: `删除 ${ids.length} 个项目？`,
    subtitle: "此操作不可撤销",
    items: titles,
    body: "删除会清理后端该项目相关的项目记录、素材文件、会话消息、人工确认卡、审计记录和项目记忆。",
    note: moreText,
    confirmText: "批量删除",
    danger: true,
  });
  if (!ok) return;
  setBusy(true);
  try {
    const result = await api("/api/conversations/batch-delete", {
      method: "POST",
      body: JSON.stringify({ session_ids: ids }),
    });
    if (state.selectedConversationId && ids.includes(state.selectedConversationId)) {
      state.selectedConversationId = null;
      state.detail = null;
    }
    state.selectedConversationIds.clear();
    state.projectManageMode = false;
    await loadConversations();
    const failed = result.errors?.length || 0;
    showToast(failed ? `已删除 ${result.deleted_count || 0} 个，失败 ${failed} 个` : `已删除 ${result.deleted_count || 0} 个项目`);
  } finally {
    setBusy(false);
  }
}

async function cleanupOrphanAssets() {
  const summary = await api("/api/assets/orphans");
  if (!summary.orphan_count) {
    showToast("没有发现无项目素材目录");
    return;
  }
  const size = formatBytes(summary.size_bytes || 0);
  const ok = await appConfirm({
    title: "清理无项目素材？",
    subtitle: "仅清理未绑定项目的素材目录",
    body: `发现 ${summary.orphan_count} 个无项目素材目录，共 ${summary.file_count || 0} 个文件，约 ${size}。`,
    confirmText: "确认清理",
    danger: true,
  });
  if (!ok) return;
  setBusy(true);
  try {
    const result = await api("/api/assets/orphans", { method: "DELETE" });
    showToast(`已清理 ${result.removed_count || 0} 个目录，${result.removed_file_count || 0} 个文件`);
  } finally {
    setBusy(false);
  }
}

async function submitMessage(event) {
  event.preventDefault();
  const content = els.messageInput.value.trim();
  const pendingFiles = getSelectedAssetFiles();
  if (!content && !pendingFiles.length) {
    showToast("请输入项目描述、修改意见或上传文件");
    return;
  }
  setBusy(true);
  try {
    const draftKeyBeforeSubmit = currentComposerDraftKey();
    const hadPendingFiles = pendingFiles.length > 0;
    let attachedAssets = [];
    if (hadPendingFiles) {
      await ensureConversationForAssetUpload();
      attachedAssets = (await uploadAsset({ clearAfterUpload: false, refreshAfterUpload: false, showUploadToast: false })) || [];
    }
    if (!content) {
      clearAttachmentPreview();
      clearComposerDraft(draftKeyBeforeSubmit);
      clearComposerDraft(currentComposerDraftKey());
      await loadConversations();
      showToast("文件已加入项目，可 @ 引用");
      return;
    }
    let detail;
    if (!state.selectedConversationId) {
      detail = await api("/api/conversations", {
        method: "POST",
        body: JSON.stringify({ initial_message: content, workflow_type: "unknown" }),
      });
      state.selectedConversationId = detail.session.id;
      state.draftConversation = false;
    } else {
      appendOptimisticUserMessage(content);
      const payload = buildMessagePayload(content, attachedAssets);
      detail = await api(`/api/conversations/${state.selectedConversationId}/messages`, {
        method: "POST",
        body: JSON.stringify({ content, payload }),
      });
    }
    els.messageInput.value = "";
    resizeTextarea();
    if (hadPendingFiles) clearAttachmentPreview();
    clearComposerDraft(draftKeyBeforeSubmit);
    clearComposerDraft(currentComposerDraftKey());
    state.detail = detail;
    await loadConversations();
  } finally {
    setBusy(false);
  }
}

function handleMessageInputKeydown(event) {
  if (event.isComposing) return;
  if (event.key !== "Enter" || event.shiftKey) return;
  event.preventDefault();
  const firstMention = els.assetMentionMenu && !els.assetMentionMenu.classList.contains("hidden")
    ? els.assetMentionMenu.querySelector(".asset-mention-option")
    : null;
  if (firstMention) {
    firstMention.click();
    return;
  }
  if (state.busy || els.messageInput.disabled) return;
  els.composerForm.requestSubmit();
}

function appendOptimisticUserMessage(content) {
  if (!state.detail) return;
  state.detail = deepClone(state.detail);
  state.detail.messages.push({
    id: `optimistic-${Date.now()}`,
    session_id: state.selectedConversationId,
    role: "user",
    message_type: "text",
    content,
    payload: {},
    created_at: new Date().toISOString(),
  });
  renderConversationDetail();
}

async function uploadAsset({ clearAfterUpload = true, refreshAfterUpload = true, showUploadToast = true } = {}) {
  await ensureConversationForAssetUpload();
  const kind = "other";
  els.assetKindInput.value = kind;
  const files = getSelectedAssetFiles();
  if (!files.length) {
    showToast("请选择要上传的资料");
    return;
  }
  if (showUploadToast) showToast(files.length > 1 ? `正在上传 ${files.length} 个资料...` : "正在上传资料...");
  const uploadedAssets = [];
  for (const file of files) {
    const beforeIds = new Set((state.detail?.assets || []).map((asset) => asset.id));
    const formData = new FormData();
    formData.append("kind", kind);
    formData.append("file", file);
    state.detail = await api(`/api/conversations/${state.selectedConversationId}/assets`, {
      method: "POST",
      body: formData,
    });
    uploadedAssets.push(...(state.detail.assets || []).filter((asset) => !beforeIds.has(asset.id)));
  }
  if (clearAfterUpload) clearAttachmentPreview();
  if (refreshAfterUpload) await loadConversations();
  renderFileChips();
  if (showUploadToast) showToast(files.length > 1 ? `${files.length} 个文件已加入项目，可 @ 引用` : "文件已加入项目，可 @ 引用");
  return uploadedAssets;
}

function buildMessagePayload(content, attachedAssets = []) {
  const mentions = buildAssetMentions(content);
  const mentionedIds = new Set(mentions.map((mention) => mention.asset_id));
  for (const asset of attachedAssets) {
    if (!asset?.id || mentionedIds.has(asset.id)) continue;
    mentions.push({
      placeholder: `@${asset.filename || asset.name || asset.id}`,
      asset_id: asset.id,
      role_as: inferAttachmentRole(content, asset),
    });
    mentionedIds.add(asset.id);
  }
  return mentions.length ? { mentions } : {};
}

function buildAssetMentions(content) {
  const assets = state.detail?.assets || [];
  const mentions = [];
  for (const asset of assets) {
    const match = findAssetMention(content, asset);
    if (!match) continue;
    mentions.push({
      placeholder: match.placeholder,
      asset_id: asset.id,
      role_as: inferMentionRole(content, match.index),
    });
  }
  return mentions;
}

function activeMentionRange() {
  const input = els.messageInput;
  const cursor = input.selectionStart ?? input.value.length;
  const beforeCursor = input.value.slice(0, cursor);
  const match = beforeCursor.match(/[@＠]([^\s@＠]*)$/);
  if (!match) return null;
  return {
    query: (match[1] || "").toLowerCase(),
    start: cursor - match[0].length,
    end: cursor,
  };
}

function renderAssetMentionMenu() {
  const range = activeMentionRange();
  const hasMentionableAssets = Boolean(state.detail?.assets?.length || getSelectedAssetFiles().length);
  if (!range || !hasMentionableAssets) {
    hideAssetMentionMenu();
    return;
  }
  const candidates = mentionCandidates(range.query);
  if (!candidates.length) {
    els.assetMentionMenu.innerHTML = `<div class="asset-mention-empty">没有匹配的项目文件</div>`;
    els.assetMentionMenu.classList.remove("hidden");
    return;
  }
  els.assetMentionMenu.innerHTML = "";
  const header = document.createElement("div");
  header.className = "asset-mention-header";
  const total = mentionCandidatePool().length;
  header.textContent = range.query
    ? `匹配 ${candidates.length} / ${total} 个素材`
    : `${total} 个素材，输入文件名或类型继续筛选`;
  els.assetMentionMenu.appendChild(header);
  for (const asset of candidates) {
    const button = document.createElement("button");
    button.type = "button";
    const isImage = isImageFileLike(asset);
    button.className = isImage ? "asset-mention-option asset-mention-image" : "asset-mention-option asset-mention-file";
    if (isImage) {
      button.append(fileThumb(asset));
      button.title = asset.filename || asset.name || "图片";
    } else {
      const icon = fileThumb(asset);
      const main = document.createElement("span");
      main.className = "asset-mention-main";
      const name = document.createElement("span");
      name.className = "asset-mention-name";
      name.textContent = asset.filename || asset.name || "未命名文件";
      const meta = document.createElement("span");
      meta.className = "asset-mention-meta";
      meta.textContent = assetMentionMeta(asset);
      main.append(name, meta);
      button.append(icon, main);
    }
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => insertAssetMention(asset));
    els.assetMentionMenu.appendChild(button);
  }
  els.assetMentionMenu.classList.remove("hidden");
}

function mentionCandidates(query) {
  const normalizedQuery = normalizeMentionSearch(query);
  return mentionCandidatePool()
    .map((asset, index) => ({
      asset,
      index,
      score: mentionCandidateScore(asset, normalizedQuery),
      time: assetSortTime(asset),
    }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score || b.time - a.time || a.index - b.index)
    .slice(0, 48)
    .map((item) => item.asset);
}

function mentionCandidatePool() {
  const pendingAssets = getSelectedAssetFiles().map((file) => ({
    filename: file.name,
    name: file.name,
    mime_type: file.type,
    type: file.type,
    file,
    pending: true,
  }));
  return [...pendingAssets, ...(state.detail?.assets || [])];
}

function mentionCandidateScore(asset, normalizedQuery) {
  if (!normalizedQuery) return asset.pending ? 30 : 10;
  const fields = assetMentionSearchFields(asset).map(normalizeMentionSearch).filter(Boolean);
  let best = 0;
  for (const field of fields) {
    if (field === normalizedQuery) best = Math.max(best, 120);
    else if (field.startsWith(normalizedQuery)) best = Math.max(best, 90);
    else if (field.includes(normalizedQuery)) best = Math.max(best, 60);
  }
  if (!best) return 0;
  return best + (asset.pending ? 20 : 0);
}

function assetMentionSearchFields(asset) {
  const filename = asset.filename || asset.name || "";
  const roleBindings = Array.isArray(asset.metadata?.role_bindings)
    ? asset.metadata.role_bindings.map((item) => item?.role_as || item?.role || "").filter(Boolean)
    : [];
  return [
    filename,
    filenameStem(filename),
    asset.metadata?.display_name,
    asset.metadata?.original_filename,
    asset.kind,
    assetKindLabel(asset.kind),
    assetChipStatus(asset),
    fileExtensionLabel(filename),
    asset.id,
    ...roleBindings,
    asset.pending ? "本次上传 待发送 pending upload" : "",
  ];
}

function normalizeMentionSearch(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[（）()[\]【】{}_\-—–·.。,，\s]+/g, "");
}

function assetSortTime(asset) {
  if (asset.pending) return Number.MAX_SAFE_INTEGER;
  const time = Date.parse(asset.updated_at || asset.created_at || asset.metadata?.updated_at || asset.metadata?.created_at || "");
  return Number.isFinite(time) ? time : 0;
}

function assetMentionMeta(asset) {
  const filename = asset.filename || asset.name || "";
  const parts = [
    asset.pending ? "本次上传" : assetKindLabel(asset.kind),
    fileExtensionLabel(filename),
    asset.pending ? "待发送" : assetChipStatus(asset),
  ].filter(Boolean);
  return parts.join(" · ");
}

function insertAssetMention(asset) {
  const range = activeMentionRange();
  if (!range) {
    insertAssetMentionByAsset(asset);
    return;
  }
  const input = els.messageInput;
  const token = `@${asset.filename} `;
  input.value = `${input.value.slice(0, range.start)}${token}${input.value.slice(range.end)}`;
  const cursor = range.start + token.length;
  input.focus();
  input.setSelectionRange(cursor, cursor);
  hideAssetMentionMenu();
  resizeTextarea();
}

function insertAssetMentionByAsset(asset) {
  const input = els.messageInput;
  const cursor = input.selectionStart ?? input.value.length;
  const prefix = input.value && cursor > 0 && !/\s$/.test(input.value.slice(0, cursor)) ? " " : "";
  const token = `${prefix}@${asset.filename} `;
  input.value = `${input.value.slice(0, cursor)}${token}${input.value.slice(cursor)}`;
  const nextCursor = cursor + token.length;
  input.focus();
  input.setSelectionRange(nextCursor, nextCursor);
  hideAssetMentionMenu();
  resizeTextarea();
}

function hideAssetMentionMenu() {
  els.assetMentionMenu.classList.add("hidden");
  els.assetMentionMenu.innerHTML = "";
}

function assetIcon(asset) {
  const mime = (asset.mime_type || "").toLowerCase();
  const name = (asset.filename || "").toLowerCase();
  if (mime.startsWith("image/")) return "IMG";
  if (name.endsWith(".ppt") || name.endsWith(".pptx")) return "PPT";
  if (name.endsWith(".pdf")) return "PDF";
  if (name.endsWith(".xlsx") || name.endsWith(".xls") || name.endsWith(".csv")) return "XLS";
  if (name.endsWith(".doc") || name.endsWith(".docx")) return "DOC";
  return "FILE";
}

function findAssetMention(content, asset) {
  const candidates = new Set([
    asset.filename,
    asset.metadata?.display_name,
    asset.metadata?.original_filename,
    filenameStem(asset.filename),
  ]);
  for (const candidate of candidates) {
    if (!candidate) continue;
    const placeholder = `@${candidate}`;
    const index = content.indexOf(placeholder);
    if (index >= 0) return { placeholder, index };
  }
  return null;
}

function filenameStem(filename = "") {
  return String(filename).replace(/\.[A-Za-z0-9]+$/, "");
}

function inferMentionRole(content, mentionIndex) {
  return nearestRoleLabel(content.toLowerCase(), mentionIndex);
}

function nearestRoleLabel(content, mentionIndex) {
  const roleTerms = [
    ["logo", ["logo", "标志", "商标"]],
    ["vi_reference", ["vi", "品牌规范", "视觉规范", "品牌参考"]],
    ["competitor_info", ["竞品", "对手", "爆款"]],
    ["product_image", ["产品图", "产品图片", "参考图", "主图", "效果图"]],
    ["product_intro", ["产品介绍", "产品资料", "产品ppt", "产品 pdf", "产品文档", "介绍"]],
  ];
  const before = content.slice(Math.max(0, mentionIndex - 40), mentionIndex);
  let best = null;
  for (const [role, terms] of roleTerms) {
    for (const term of terms) {
      const index = before.lastIndexOf(term);
      if (index >= 0 && (!best || index > best.index)) best = { index, role };
    }
  }
  if (best) return best.role;
  const after = content.slice(mentionIndex, mentionIndex + 36);
  for (const [role, terms] of roleTerms) {
    if (terms.some((term) => after.includes(term))) return role;
  }
  return "";
}

function inferAttachmentRole(content, asset) {
  const text = content.toLowerCase();
  const filename = String(asset?.filename || asset?.name || "").toLowerCase();
  const image = isImageFileLike(asset);
  if (["logo", "标志", "商标"].some((term) => text.includes(term) || filename.includes(term))) return "logo";
  if (["vi", "品牌规范", "视觉规范", "品牌参考"].some((term) => text.includes(term) || filename.includes(term))) return "vi_reference";
  if (["竞品", "对手", "爆款"].some((term) => text.includes(term) || filename.includes(term))) return "competitor_info";
  if (image && ["产品图", "产品图片", "参考图", "主图", "效果图", "产品"].some((term) => text.includes(term) || filename.includes(term))) return "product_image";
  if (!image && ["产品介绍", "产品资料", "产品ppt", "产品 pdf", "产品文档", "介绍", "ppt"].some((term) => text.includes(term) || filename.includes(term))) return "product_intro";
  return "";
}

async function submitReviewGate(gateId, action, editedPayload = null, options = {}) {
  if (!state.selectedConversationId) return;
  let comment = options.comment || "";
  if ((action === "reject" || action === "request_more_info") && !options.skipPrompt) {
    comment = window.prompt("请输入原因或补充要求", "") || "";
  }
  setBusy(true);
  try {
    const body = { action, comment, reviewer: "review-console" };
    if (editedPayload) body.edited_payload = editedPayload;
    state.detail = await api(`/api/conversations/${state.selectedConversationId}/review-gates/${gateId}/actions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.editDrafts.delete(gateId);
    state.editingGates.delete(gateId);
    await loadConversations();
    showToast(action === "approve" || action === "edit" ? "已确认，结果已保留在对话" : "已提交处理");
  } finally {
    setBusy(false);
  }
}

function renderConversationList() {
  const visibleCount = state.conversations.length + (state.draftConversation ? 1 : 0);
  els.conversationCount.textContent = String(visibleCount);
  els.conversationList.replaceChildren();
  renderSelectionBar();
  if (state.draftConversation && state.detail) {
    els.conversationList.append(renderDraftConversationRow());
  }
  if (!visibleCount) {
    els.conversationList.append(emptyBlock("暂无项目对话"));
    return;
  }
  for (const detail of state.conversations) {
    const session = detail.session;
    const row = document.createElement("div");
    row.className = `project-row${state.selectedConversationIds.has(session.id) ? " selected" : ""}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "project-check";
    checkbox.checked = state.selectedConversationIds.has(session.id);
    const displayTitle = displayConversationTitle(detail);
    checkbox.setAttribute("aria-label", `选择 ${displayTitle}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedConversationIds.add(session.id);
      else state.selectedConversationIds.delete(session.id);
      renderConversationList();
    });
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item${session.id === state.selectedConversationId ? " active" : ""}`;
    button.addEventListener("click", () => {
      switchView("chat");
      loadConversation(session.id).catch(showError);
    });

    const title = document.createElement("div");
    title.className = "project-name";
    title.textContent = displayTitle;

    const meta = document.createElement("div");
    meta.className = "project-meta";
    const tag = document.createElement("span");
    tag.className = `tag ${stageTagClass(session.current_stage, detail.pending_review_gate)}`;
    tag.textContent = stageShortLabel(session.current_stage, detail.pending_review_gate);
    const latest = document.createElement("span");
    latest.textContent = lastMessagePreview(detail.messages);
    meta.append(tag, latest);

    button.append(title, meta);
    if (state.projectManageMode) row.append(checkbox);
    row.append(button);
    els.conversationList.append(row);
  }
}

function renderDraftConversationRow() {
  const row = document.createElement("div");
  row.className = "project-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "project-item active draft-item";
  button.addEventListener("click", () => {
    switchView("chat");
    renderConversationDetail();
  });

  const title = document.createElement("div");
  title.className = "project-name";
  title.textContent = "新项目对话";

  const meta = document.createElement("div");
  meta.className = "project-meta";
  const tag = document.createElement("span");
  tag.className = "tag draft";
  tag.textContent = "草稿";
  const latest = document.createElement("span");
  latest.textContent = "发送第一条消息后创建";
  meta.append(tag, latest);

  button.append(title, meta);
  row.append(button);
  return row;
}

function renderSelectionBar() {
  const selected = state.selectedConversationIds.size;
  els.selectionBar.classList.toggle("hidden", !state.projectManageMode);
  if (els.manageProjectsLabel) {
    els.manageProjectsLabel.textContent = state.projectManageMode ? "完成" : "管理";
  }
  els.manageProjectsBtn.classList.toggle("active", state.projectManageMode);
  els.selectedCount.textContent = `已选 ${selected} 个`;
  els.batchDeleteBtn.disabled = state.busy || selected === 0;
  els.clearSelectionBtn.disabled = state.busy || selected === 0;
  els.selectAllBtn.disabled = state.busy || !state.conversations.length;
  els.selectAllBtn.textContent = selected === state.conversations.length && selected > 0 ? "取消全选" : "全选";
}

function toggleSelectAllConversations() {
  if (state.selectedConversationIds.size === state.conversations.length) {
    state.selectedConversationIds.clear();
  } else {
    state.selectedConversationIds = new Set(state.conversations.map((item) => item.session.id));
  }
  renderConversationList();
}

function clearConversationSelection() {
  state.selectedConversationIds.clear();
  renderConversationList();
}

function toggleProjectManageMode() {
  state.projectManageMode = !state.projectManageMode;
  if (!state.projectManageMode) {
    state.selectedConversationIds.clear();
  }
  renderConversationList();
}

function renderEmpty() {
  els.emptyState.classList.remove("hidden");
  els.chatSurface.classList.add("hidden");
  els.conversationTitleTop.textContent = "PackVision 1.0";
  els.messageInput.value = "";
  clearAttachmentPreview();
  resizeTextarea();
  renderConversationProgressBadge(null);
  updateComposerState();
}

function renderConversationDetail(options = {}) {
  const detail = state.detail;
  if (!detail) {
    renderEmpty();
    return;
  }
  const session = detail.session;
  els.emptyState.classList.add("hidden");
  els.chatSurface.classList.remove("hidden");
  els.conversationStage.textContent = `${stageLabel(session.current_stage)} · ${agentActivityText(detail)}`;
  const displayTitle = displayConversationTitle(detail);
  els.conversationTitle.textContent = displayTitle;
  els.conversationTitleTop.textContent = displayTitle;
  els.workflowBadge.textContent = workflowLabel(session.workflow_type);
  els.workflowBadge.className = `tag ${session.workflow_type === "unknown" ? "draft" : "review"}`;
  renderConversationProgressBadge(detail);
  renderMessages(detail, options);
  renderFileChips();
  updateComposerState();
}

function renderConversationProgressBadge(detail) {
  if (!els.conversationProgressBadge) return;
  if (!detail) {
    els.conversationProgressBadge.classList.add("hidden");
    els.conversationProgressBadge.textContent = "";
    return;
  }
  const sections = memorySections(detail);
  const confirmed = sections.filter((section) => section.status === "confirmed").length;
  const total = sections.length || 1;
  els.conversationProgressBadge.textContent = `${confirmed}/${total} 已确认`;
  els.conversationProgressBadge.classList.remove("hidden");
}

function renderFileChips() {
  const pendingFiles = getSelectedAssetFiles();
  const hasChips = pendingFiles.length;
  els.fileChipsArea.classList.toggle("hidden", !hasChips);
  els.composerDivider.classList.toggle("hidden", !hasChips);
  els.fileChipsArea.replaceChildren();
  for (const file of pendingFiles) {
    els.fileChipsArea.append(renderPendingFileChip(file));
  }
}

function renderPendingFileChip(file) {
  const chip = document.createElement("div");
  const isImage = isImageFileLike(file);
  chip.className = isImage ? "file-chip image-only-chip" : "file-chip";
  chip.append(fileThumb({ filename: file.name, mime_type: file.type, file }));
  if (!isImage) {
    const body = document.createElement("div");
    body.className = "chip-body";
    const name = document.createElement("span");
    name.className = "chip-name";
    name.textContent = file.name;
    const status = document.createElement("span");
    status.className = "chip-status plain";
    status.textContent = fileExtensionLabel(file.name);
    body.append(name, status);
    chip.append(body);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "chip-remove";
  remove.textContent = "×";
  remove.addEventListener("click", () => removePendingFile(file));
  chip.append(remove);
  return chip;
}

function renderAssetFileChip(asset) {
  const chip = document.createElement("div");
  const isImage = isImageFileLike(asset);
  chip.className = isImage ? "file-chip image-only-chip" : "file-chip";
  chip.title = "点击插入 @ 引用";
  chip.append(fileThumb(asset));
  if (!isImage) {
    const body = document.createElement("div");
    body.className = "chip-body";
    const name = document.createElement("span");
    name.className = "chip-name";
    name.textContent = asset.filename || "未命名文件";
    const status = document.createElement("span");
    status.className = "chip-status plain";
    status.textContent = fileExtensionLabel(asset.filename || "");
    body.append(name, status);
    body.addEventListener("click", () => insertAssetMentionByAsset(asset));
    chip.append(body);
  } else {
    chip.addEventListener("click", () => insertAssetMentionByAsset(asset));
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "chip-remove";
  remove.textContent = "×";
  remove.title = "删除文件";
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteAssetFromComposer(asset).catch(showError);
  });
  chip.append(remove);
  return chip;
}

function fileThumb(fileLike) {
  const thumb = document.createElement("div");
  const mime = (fileLike.mime_type || fileLike.type || "").toLowerCase();
  const filename = String(fileLike.filename || fileLike.name || "");
  if (isImageFileLike(fileLike) && fileLike.file instanceof File) {
    thumb.className = "chip-thumb image-thumb";
    const img = document.createElement("img");
    img.alt = "";
    const objectUrl = URL.createObjectURL(fileLike.file);
    img.src = objectUrl;
    img.addEventListener("load", () => URL.revokeObjectURL(objectUrl), { once: true });
    thumb.append(img);
    return thumb;
  }
  if (isImageFileLike(fileLike) && fileLike.id && state.detail?.project?.id) {
    thumb.className = "chip-thumb image-thumb";
    const img = document.createElement("img");
    img.alt = "";
    img.src = assetContentUrl(fileLike.id);
    thumb.append(img);
    return thumb;
  }
  const label = assetIcon({ filename, mime_type: mime });
  thumb.className = `ft-file-thumb ft-${label.toLowerCase()}-thumb`;
  thumb.textContent = label;
  return thumb;
}

function isImageFileLike(fileLike = {}) {
  const mime = (fileLike.mime_type || fileLike.type || "").toLowerCase();
  const filename = String(fileLike.filename || fileLike.name || "");
  return mime.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|svg|avif)$/i.test(filename);
}

function assetChipStatus(asset) {
  const status = asset?.metadata?.processing?.status || "uploaded";
  if (status === "completed") return "已解析";
  if (status === "running") return "解析中";
  if (status === "queued") return "排队中";
  if (status === "failed") return "解析失败";
  if (status === "cancelled") return "已取消";
  return "待解析";
}

function assetChipStatusClass(asset) {
  const status = asset?.metadata?.processing?.status || "uploaded";
  if (status === "completed") return "cs-done";
  if (status === "failed" || status === "cancelled") return "cs-failed";
  return "cs-pending";
}

function fileExtensionLabel(filename = "") {
  const extension = String(filename).split(".").pop()?.toUpperCase() || "FILE";
  if (extension === filename.toUpperCase()) return "FILE";
  return extension;
}

async function deleteAssetFromComposer(asset) {
  if (!state.detail?.project?.id || !asset?.id) return;
  await api(`/api/projects/${state.detail.project.id}/assets/${asset.id}`, { method: "DELETE" });
  await refreshActiveConversation({ force: true });
  showToast("文件已删除");
}

function renderMessages(detail, options = {}) {
  els.messageStream.replaceChildren();
  els.messageStream.append(renderContextProgressPanel(detail));

  const divider = document.createElement("div");
  divider.className = "date-divider";
  divider.textContent = "今天";
  els.messageStream.append(divider);

  if (!detail.messages.length && !detail.pending_review_gate) {
    els.messageStream.append(emptyBlock("从一句项目描述开始。主 Agent 会判断任务类型，并调用对应工具和子 Agent。"));
    scheduleMessageScroll();
    return;
  }

  const renderedGateIds = new Set();
  for (const message of detail.messages) {
    els.messageStream.append(renderMessage(message, { detail, renderedGateIds }));
  }
  for (const gate of confirmedReviewGates(detail)) {
    if (renderedGateIds.has(gate.id)) continue;
    els.messageStream.append(renderConfirmedReviewGate(gate));
  }
  if (detail.pending_review_gate && !renderedGateIds.has(detail.pending_review_gate.id)) {
    els.messageStream.append(renderReviewGate(detail.pending_review_gate));
  }
  const reviewCards = els.messageStream.querySelectorAll(".confirm-card");
  const reviewNode = reviewCards[reviewCards.length - 1] || null;
  scheduleMessageScroll({ preferReviewGate: options.preferReviewGate || Boolean(detail.pending_review_gate), reviewNode });
}

function scheduleMessageScroll({ preferReviewGate = false, reviewNode = null } = {}) {
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      if (preferReviewGate && reviewNode) {
        reviewNode.scrollIntoView({ block: "end", behavior: "smooth" });
        reviewNode.classList.add("confirm-card-attention");
        window.setTimeout(() => reviewNode.classList.remove("confirm-card-attention"), 1800);
        return;
      }
      els.messageStream.scrollTo({ top: els.messageStream.scrollHeight, behavior: "smooth" });
    });
  });
}

function renderMessage(message, options = {}) {
  if (message.role === "user") {
    return renderUserMessage(message);
  }
  if (message.role === "tool" || message.message_type === "tool_result") {
    return renderToolResult(message);
  }
  return renderAgentMessage(message, options);
}

function renderUserMessage(message) {
  const row = document.createElement("div");
  row.className = "msg-user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = message.content || "";
  const avatar = document.createElement("div");
  avatar.className = "msg-avatar user";
  avatar.textContent = "我";
  row.append(bubble, avatar);
  return row;
}

function renderContextProgressPanel(detail) {
  const sections = memorySections(detail);
  const confirmed = sections.filter((section) => section.status === "confirmed").length;
  const total = sections.length || 1;
  const percent = Math.round((confirmed / total) * 100);
  const panel = document.createElement("section");
  panel.className = "context-progress";

  const top = document.createElement("div");
  top.className = "context-progress-top";
  const title = document.createElement("div");
  title.className = "context-progress-title";
  title.textContent = "对话结果";
  const count = document.createElement("div");
  count.className = "context-progress-count";
  count.textContent = `${confirmed}/${total} 已确认`;
  top.append(title, count);

  const bar = document.createElement("div");
  bar.className = "context-progress-bar";
  const fill = document.createElement("div");
  fill.className = "context-progress-fill";
  fill.style.width = `${percent}%`;
  bar.append(fill);

  const chips = document.createElement("div");
  chips.className = "context-progress-chips";
  for (const section of sections) {
    const chip = document.createElement("span");
    chip.className = `context-chip ${section.status}`;
    chip.textContent = section.title;
    chips.append(chip);
  }

  panel.append(top, bar, chips);
  return panel;
}

function confirmedReviewGates(detail) {
  const gates = Array.isArray(detail?.review_gates) ? detail.review_gates : [];
  return gates.filter((gate) => ["approved", "edited"].includes(gate.status));
}

function renderConfirmedReviewGate(gate) {
  const fragment = document.createDocumentFragment();
  const marker = document.createElement("div");
  marker.className = "confirmed-marker";
  marker.textContent = `${reviewStatusLabel(gate.status)} · ${resultTitle(gate)}`;
  fragment.append(marker);
  const card = renderStructuredResultCard(gate);
  card.classList.add("confirmed-result-card");
  fragment.append(card);
  const concept = renderConceptSelection(gate, { readonly: true });
  if (concept) fragment.append(concept);
  return fragment;
}

function renderAgentMessage(message, options = {}) {
  const fragment = document.createDocumentFragment();
  const row = document.createElement("div");
  row.className = "msg-agent";
  const avatar = document.createElement("div");
  avatar.className = "msg-avatar agent";
  avatar.textContent = agentInitial(message);
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const label = document.createElement("div");
  label.className = "agent-label";
  label.textContent = messageAgentLabel(message);
  const content = document.createElement("div");
  content.textContent = message.content || "";
  bubble.append(label, content);
  row.append(avatar, bubble);
  fragment.append(row);

  if (message.payload?.planner_decision) {
    fragment.append(renderPlannerToolStatus(message.payload.planner_decision, state.detail));
  }
  const gate = reviewGateFromMessage(message, options.detail || state.detail);
  if (gate?.id && !options.renderedGateIds?.has(gate.id)) {
    options.renderedGateIds?.add(gate.id);
    fragment.append(renderInlineReviewGate(gate));
  }
  return fragment;
}

function reviewGateFromMessage(message, detail) {
  const payloadGate = message?.payload?.review_gate;
  if (!payloadGate?.id || !detail) return null;
  if (detail.pending_review_gate?.id === payloadGate.id) return detail.pending_review_gate;
  const gates = Array.isArray(detail.review_gates) ? detail.review_gates : [];
  return gates.find((gate) => gate.id === payloadGate.id) || payloadGate;
}

function renderInlineReviewGate(gate) {
  if (gate.status === "pending") return renderReviewGate(gate);
  return renderConfirmedReviewGate(gate);
}

function renderToolResult(message) {
  const bar = document.createElement("div");
  bar.className = "tool-bar compact";
  bar.style.flexShrink = "0";
  bar.append(
    renderToolRow({
      icon: "✓",
      tone: "green",
      name: "资料上传工具",
      description: message.content || "资料已写入项目素材库",
      status: "done",
    }),
  );
  return bar;
}

function renderPlannerToolStatus(decision, detail) {
  const bar = document.createElement("div");
  bar.className = "tool-bar";
  bar.style.flexShrink = "0";
  const title = document.createElement("div");
  title.className = "tool-bar-title";
  title.textContent = "子任务进度";
  bar.append(title);
  bar.append(
    renderToolRow({
      icon: "P",
      tone: "blue",
      name: "主 Agent",
      description: decision.intent || "识别用户意图并规划下一步",
      status: "done",
    }),
  );
  const workflowPlan = workflowAgentPlan(detail);
  if (workflowPlan.length) {
    for (const item of workflowPlan) {
      const itemStatus = workflowPlanItemStatus(item, detail);
      bar.append(
        renderToolRow({
          icon: agentInitialByName(item.agent),
          tone: item.tone,
          name: agentLabel(item.agent),
          description: itemStatus.description || item.description,
          status: itemStatus.status,
        }),
      );
    }
    return bar;
  }
  if (decision.target_agent) {
    const agentStatus = plannerTargetStatus(decision, detail);
    bar.append(
      renderToolRow({
        icon: agentInitialByName(decision.target_agent),
        tone: "purple",
        name: agentLabel(decision.target_agent),
        description: decision.reason || "等待执行对应子任务",
        status: agentStatus.status,
      }),
    );
  }
  for (const tool of decision.required_tools || []) {
    bar.append(
      renderToolRow({
        icon: "T",
        tone: "orange",
        name: tool,
        description: "由主 Agent 按需调用",
        status: "waiting",
      }),
    );
  }
  return bar;
}

function plannerTargetStatus(decision, detail) {
  const pending = detail?.pending_review_gate;
  const reviewTypes = agentReviewTypes(decision.target_agent, decision.review_gate_type);
  const producedPendingGate =
    pending &&
    (pending.created_by_agent === decision.target_agent ||
      pending.type === decision.review_gate_type ||
      reviewTypes.includes(pending.type) ||
      (decision.target_agent === "strategy_agent" && String(pending.type || "").includes("strategy")));
  if (producedPendingGate) {
    decision.reason = "\u5df2\u751f\u6210\u5f85\u4f60\u786e\u8ba4\u7684\u5ba1\u6838\u5361";
    return { status: "done", description: "\u5df2\u751f\u6210\u5f85\u4f60\u786e\u8ba4\u7684\u5ba1\u6838\u5361" };
  }
  if (hasConfirmedAgentOutput(decision.target_agent, detail, reviewTypes)) {
    decision.reason = "\u5df2\u786e\u8ba4\u5e76\u5199\u5165\u9879\u76ee\u8bb0\u5fc6";
    return { status: "done", description: "\u5df2\u786e\u8ba4\u5e76\u5199\u5165\u9879\u76ee\u8bb0\u5fc6" };
  }
  if (decision.next_action === "call_agent") {
    return { status: "running", description: decision.reason || "\u6b63\u5728\u6267\u884c\u5bf9\u5e94\u5b50\u4efb\u52a1" };
  }
  return { status: "waiting", description: decision.reason || "\u7b49\u5f85\u4e0a\u6e38\u4fe1\u606f" };
}

function workflowAgentPlan(detail) {
  if (!detail?.session) return [];
  const workflow = detail.session.workflow_type === "detail_page" ? "detail_page" : "packaging";
  if (workflow === "detail_page") {
    return [
      workflowPlanItem("usp_agent", "usp_review", "confirmed_usps", "\u63d0\u70bc\u6838\u5fc3/\u6b21\u8981\u5356\u70b9", "purple"),
      workflowPlanItem("vi_understanding_agent", "vi_review", "confirmed_vi_profile", "\u7406\u89e3\u54c1\u724c\u8272\u3001LOGO\u3001\u7248\u5f0f\u7ea6\u675f", "green"),
      workflowPlanItem("detail_page_strategy_agent", "detail_strategy_review", "confirmed_detail_page_strategy", "\u5236\u5b9a\u4e94\u5c4f\u8be6\u60c5\u9875\u7b56\u7565", "blue"),
      workflowPlanItem("detail_designer_agent", "final_design_review", "confirmed_outputs", "\u751f\u6210\u8be6\u60c5\u9875\u8bbe\u8ba1\u56fe", "orange"),
    ];
  }
  return [
    workflowPlanItem("usp_agent", "usp_review", "confirmed_usps", "\u63d0\u70bc\u6838\u5fc3/\u6b21\u8981\u5356\u70b9", "purple"),
    workflowPlanItem("vi_understanding_agent", "vi_review", "confirmed_vi_profile", "\u7406\u89e3\u54c1\u724c\u8272\u3001LOGO\u3001\u7248\u5f0f\u7ea6\u675f", "green"),
    workflowPlanItem("packaging_strategy_agent", "packaging_strategy_review", "confirmed_packaging_strategy", "\u5236\u5b9a\u5305\u88c5\u56db\u9762\u7b56\u7565", "blue"),
    workflowPlanItem("packaging_image_prompt_agent", "image_prompt_review", "confirmed_image_prompt", "生成并确认包装主图生图提示词", "orange"),
    workflowPlanItem("packaging_designer_agent", "final_design_review", "confirmed_outputs", "基于确认提示词生成包装主图", "orange"),
  ];
}

function workflowPlanItem(agent, gateType, confirmedKey, description, tone) {
  return { agent, gateType, confirmedKey, description, tone };
}

function workflowPlanItemStatus(item, detail) {
  const context = detail?.confirmed_context || {};
  if (context[item.confirmedKey]) {
    return { status: "done", description: "\u5df2\u786e\u8ba4\u5e76\u5199\u5165\u9879\u76ee\u8bb0\u5fc6" };
  }
  const pending = detail?.pending_review_gate;
  if (pending?.type === item.gateType) {
    return { status: "review", description: "\u5df2\u8f93\u51fa\uff0c\u7b49\u5f85\u4eba\u5de5\u786e\u8ba4" };
  }
  const gates = Array.isArray(detail?.review_gates) ? detail.review_gates : [];
  if (gates.some((gate) => gate.type === item.gateType && ["approved", "edited"].includes(gate.status))) {
    return { status: "done", description: "\u5df2\u786e\u8ba4\u5e76\u5199\u5165\u9879\u76ee\u8bb0\u5fc6" };
  }
  return { status: "waiting", description: item.description };
}

function agentReviewTypes(agentName, fallbackType = "") {
  const types = [];
  if (fallbackType) types.push(fallbackType);
  if (agentName?.includes("usp")) types.push("usp_review");
  if (agentName?.includes("packaging_strategy") || agentName === "strategy_agent") types.push("packaging_strategy_review");
  if (agentName?.includes("image_prompt")) types.push("image_prompt_review");
  if (agentName?.includes("detail_page_strategy")) types.push("detail_strategy_review");
  if (agentName?.includes("designer")) types.push("final_design_review");
  return [...new Set(types.filter(Boolean))];
}

function hasConfirmedAgentOutput(agentName, detail, reviewTypes) {
  const context = detail?.confirmed_context || {};
  const keysByType = {
    usp_review: "confirmed_usps",
    packaging_strategy_review: "confirmed_packaging_strategy",
    image_prompt_review: "confirmed_image_prompt",
    detail_strategy_review: "confirmed_detail_page_strategy",
    vi_review: "confirmed_vi_profile",
    final_design_review: "confirmed_outputs",
  };
  if (reviewTypes.some((type) => Boolean(context[keysByType[type]]))) return true;
  const gates = Array.isArray(detail?.review_gates) ? detail.review_gates : [];
  return gates.some(
    (gate) =>
      (gate.created_by_agent === agentName || reviewTypes.includes(gate.type)) &&
      ["approved", "edited"].includes(gate.status),
  );
}

function renderToolRow({ icon, tone, name, description, status }) {
  const row = document.createElement("div");
  row.className = "tool-row";
  const info = document.createElement("div");
  info.className = "tool-info";
  const toolIcon = document.createElement("div");
  toolIcon.className = `tool-icon ${tone || "blue"}`;
  toolIcon.textContent = icon;
  const text = document.createElement("div");
  const title = document.createElement("div");
  title.className = "tool-name";
  title.textContent = name;
  const sub = document.createElement("div");
  sub.className = "tool-sub";
  sub.textContent = description;
  text.append(title, sub);
  info.append(toolIcon, text);
  const badge = document.createElement("div");
  badge.className = `badge ${status}`;
  badge.textContent = statusLabel(status);
  row.append(info, badge);
  return row;
}

function renderReviewGate(gate) {
  const fragment = document.createDocumentFragment();
  fragment.append(renderStructuredResultCard(gate));
  const concept = renderConceptSelection(gate);
  if (concept) fragment.append(concept);
  const isEditing = state.editingGates.has(gate.id);

  const card = document.createElement("section");
  card.className = `confirm-card${isEditing ? " editing" : ""}`;
  card.style.flexShrink = "0";
  const header = document.createElement("div");
  header.className = "confirm-header";
  const icon = document.createElement("div");
  icon.className = "confirm-icon";
  icon.textContent = "✓";
  const titleWrap = document.createElement("div");
  const title = document.createElement("div");
  title.className = "confirm-title";
  title.textContent = gate.title || "请确认该节点结果";
  const sub = document.createElement("div");
  sub.className = "confirm-sub";
  const generationRunning = gate.type === "final_design_review" && gate.payload?.generation_status === "running";
  sub.textContent = isEditing
    ? "正在编辑结构化结果，保存后才会保留在对话结果中"
    : generationRunning ? "出图仍在进行，完成或部分失败后再确认" : "确认后将保留在对话结果中，供后续 Agent 使用";
  titleWrap.append(title, sub);
  header.append(icon, titleWrap);

  const body = document.createElement("div");
  body.className = "confirm-body";
  body.textContent = isEditing
    ? "请直接修改上方结构化字段。保存后，修改后的内容会作为下一步 Agent 的有效上下文。"
    : gate.summary || "请确认该结构化结果是否可以作为后续有效上下文。";

  const actions = document.createElement("div");
  actions.className = "confirm-actions";
  const confirm = document.createElement("button");
  confirm.className = "btn-confirm";
  confirm.type = "button";
  confirm.textContent = isEditing ? "保存修改并继续" : "确认并继续";
  confirm.disabled = generationRunning;
  confirm.addEventListener("click", () => {
    if (isEditing) {
      submitReviewGate(gate.id, "edit", draftForGate(gate));
      return;
    }
    submitReviewGate(gate.id, "approve");
  });
  const edit = document.createElement("button");
  edit.className = "btn-edit";
  edit.type = "button";
  edit.textContent = isEditing ? "取消编辑" : "编辑结构化结果";
  edit.disabled = generationRunning;
  edit.addEventListener("click", () => {
    if (isEditing) {
      state.editingGates.delete(gate.id);
      state.editDrafts.delete(gate.id);
    } else {
      state.editDrafts.set(gate.id, deepClone(gate.payload || {}));
      state.editingGates.add(gate.id);
      showToast("已进入编辑模式，修改上方字段后保存");
    }
    renderConversationDetail({ preferReviewGate: true });
  });
  const revert = document.createElement("button");
  revert.className = "btn-revert";
  revert.type = "button";
  revert.textContent = "回退重新分析";
  revert.addEventListener("click", () => {
    const quickRetry = gate.type === "final_design_review";
    submitReviewGate(
      gate.id,
      "reject",
      null,
      quickRetry ? { skipPrompt: true, comment: "重新生成出图" } : {},
    );
  });
  actions.append(confirm, edit, revert);
  card.append(header, body, actions);
  fragment.append(card);
  return fragment;
}

function renderStructuredResultCard(gate) {
  const card = document.createElement("section");
  const isEditing = gate.status === "pending" && state.editingGates.has(gate.id);
  card.className = `result-card${isEditing ? " editing" : ""}`;
  card.style.flexShrink = "0";
  const header = document.createElement("div");
  header.className = "result-card-header";
  const title = document.createElement("div");
  title.className = "result-card-title";
  title.textContent = resultTitle(gate);
  const badge = document.createElement("div");
  badge.className = `${gate.status === "pending" ? "pending" : "confirmed"}-badge`;
  badge.textContent = reviewStatusLabel(gate.status);
  header.append(title, badge);

  const draft = isEditing ? draftForGate(gate) : deepClone(gate.payload || {});
  if (gate.type === "packaging_strategy_review" && !isEditing) {
    card.append(header, renderPackagingStrategyReport(draft));
    return card;
  }

  const rows = document.createElement("div");
  rows.className = "result-rows";
  const fields = resultFieldsForGate(gate, draft);
  if (!fields.length) {
    rows.append(emptyResultRow("内容", "暂无结构化内容"));
  } else {
    for (const field of fields) {
      rows.append(renderResultRow(field, draft, isEditing && field.editable !== false));
    }
  }
  card.append(header, rows);
  return card;
}

function renderPackagingStrategyReport(payload = {}) {
  const report = document.createElement("div");
  report.className = "strategy-report";

  const hero = document.createElement("div");
  hero.className = "strategy-report-hero";
  const title = document.createElement("div");
  title.className = "strategy-report-product";
  title.textContent = payload.product_name || "包装主图设计方案";
  const meta = document.createElement("div");
  meta.className = "strategy-report-meta";
  [
    payload.box_type ? `盒型：${payload.box_type}` : "",
    payload.front_ratio ? `正面：${payload.front_ratio}` : "",
    payload.side_ratio ? `侧面：${payload.side_ratio}` : "",
    payload.top_ratio ? `顶面：${payload.top_ratio}` : "",
  ]
    .filter(Boolean)
    .slice(0, 4)
    .forEach((item) => meta.append(renderStrategyPill(item)));
  const tone = document.createElement("p");
  tone.className = "strategy-report-tone";
  tone.textContent = payload.overall_tone || "等待包装策略 Agent 输出整体影调和用户感受。";
  hero.append(title, meta, tone);
  report.append(hero);

  report.append(renderStrategySection("正面主图设计方案", payload.front_layout || "暂无正面构图详解。", true));

  const copyItems = Array.isArray(payload.required_copy) ? payload.required_copy : [];
  const iconItems = Array.isArray(payload.required_icons) ? payload.required_icons : [];
  if (copyItems.length || iconItems.length) {
    const panels = document.createElement("div");
    panels.className = "strategy-report-panels";
    panels.append(renderStrategyChipPanel("文案层级", copyItems, "主标题、副标题、卖点徽章和功能利益点会进入后续主图提示词。"));
    panels.append(renderStrategyChipPanel("标识与图标", iconItems, "LOGO、年龄、玩法、功能和系列标签按证据使用。"));
    report.append(panels);
  }

  const surfaceGrid = document.createElement("div");
  surfaceGrid.className = "strategy-surface-grid";
  [
    ["左侧信息分工", payload.left_layout || "暂无左侧策略。"],
    ["右侧信息分工", payload.right_layout || "暂无右侧策略。"],
    ["背面信息分工", payload.back_layout || "暂无背面策略。"],
  ].forEach(([sectionTitle, text]) => surfaceGrid.append(renderStrategyMiniSection(sectionTitle, text)));
  report.append(surfaceGrid);

  const risks = Array.isArray(payload.risk_notes) ? payload.risk_notes.filter(Boolean) : [];
  if (risks.length) {
    report.append(renderStrategyChipPanel("风险与人工确认点", risks, "这些内容不会自动覆盖，需要在确认后进入项目有效记忆。", "risk"));
  }

  return report;
}

function renderStrategyPill(text) {
  const pill = document.createElement("span");
  pill.className = "strategy-pill";
  pill.textContent = formatValue(text);
  return pill;
}

function renderStrategySection(title, text, featured = false) {
  const section = document.createElement("section");
  section.className = `strategy-section${featured ? " featured" : ""}`;
  const heading = document.createElement("h4");
  heading.textContent = title;
  const body = document.createElement("div");
  body.className = "strategy-section-body";
  appendStrategyText(body, text);
  section.append(heading, body);
  return section;
}

function renderStrategyMiniSection(title, text) {
  const section = document.createElement("section");
  section.className = "strategy-mini-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const body = document.createElement("p");
  body.textContent = formatValue(text);
  section.append(heading, body);
  return section;
}

function renderStrategyChipPanel(title, items, hint = "", tone = "") {
  const panel = document.createElement("section");
  panel.className = `strategy-chip-panel${tone ? ` ${tone}` : ""}`;
  const heading = document.createElement("h4");
  heading.textContent = title;
  const tags = document.createElement("div");
  tags.className = "strategy-chip-list";
  const normalized = Array.isArray(items) ? items.filter((item) => String(item || "").trim()) : [];
  if (normalized.length) {
    normalized.slice(0, 10).forEach((item) => tags.append(renderMiniTag(item)));
  } else {
    const empty = document.createElement("span");
    empty.className = "strategy-empty";
    empty.textContent = "暂无";
    tags.append(empty);
  }
  panel.append(heading, tags);
  if (hint) {
    const description = document.createElement("p");
    description.textContent = hint;
    panel.append(description);
  }
  return panel;
}

function appendStrategyText(container, text) {
  const raw = formatValue(text).trim();
  if (!raw) {
    const empty = document.createElement("p");
    empty.textContent = "暂无内容。";
    container.append(empty);
    return;
  }
  const paragraphs = raw.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean);
  for (const paragraph of paragraphs) {
    const match = paragraph.match(/^([^：:]{2,22})[：:]\s*([\s\S]*)$/);
    if (match) {
      const block = document.createElement("div");
      block.className = "strategy-paragraph-block";
      const subhead = document.createElement("h5");
      subhead.textContent = match[1];
      const body = document.createElement("p");
      body.textContent = match[2] || "";
      block.append(subhead, body);
      container.append(block);
    } else {
      const body = document.createElement("p");
      body.textContent = paragraph;
      container.append(body);
    }
  }
}

function resultFieldsForGate(gate, draft) {
  if (gate.type === "usp_review") {
    return uspSummaryFields(draft);
  }
  if (gate.type === "final_design_review") {
    return finalDesignSummaryFields(draft);
  }
  if (gate.type === "image_prompt_review") {
    return imagePromptSummaryFields(draft);
  }
  if (gate.type === "vi_review") {
    return viSummaryFields(draft);
  }
  return flattenPayload(draft).slice(0, 18);
}

function imagePromptSummaryFields(payload) {
  return [
    { path: ["main_image_prompt"], label: "主图提示词", value: payload.main_image_prompt || "", editable: true },
    { path: ["negative_prompt"], label: "负向约束", value: payload.negative_prompt || "", editable: true },
    { path: ["reference_usage"], label: "参考图使用", value: payload.reference_usage || "", editable: true },
    { path: ["layout_notes"], label: "构图说明", value: payload.layout_notes || "", editable: true },
    { path: ["text_overlay_plan"], label: "后续叠加", value: payload.text_overlay_plan || [], editable: true },
    { path: ["risk_notes"], label: "风险", value: payload.risk_notes || [], editable: true },
  ];
}

function finalDesignSummaryFields(payload) {
  if (payload?.generation_blocked) {
    return [
      { path: ["reason"], label: "阻塞原因", value: payload.reason || "缺少必要出图资料", editable: false },
      { path: ["required_assets"], label: "需上传", value: payload.required_assets || ["product_image"], editable: false },
    ];
  }
  const items = payload?.generated_outputs?.items || [];
  const first = Array.isArray(items) ? items[0] : null;
  const progress = payload?.generation_progress || {};
  const errors = Array.isArray(payload?.generation_errors) ? payload.generation_errors : [];
  return [
    { path: ["generation_status"], label: "状态", value: generationStatusLabel(payload?.generation_status), editable: false },
    { path: ["generation_progress"], label: "进度", value: `${progress.completed || 0}/${progress.total || (Array.isArray(items) ? items.length : 0)}`, editable: false },
    { path: ["generated_outputs", "items"], label: "输出图", value: Array.isArray(items) ? items.map((item) => item.name) : [], editable: false },
    { path: ["generated_outputs", "items", 0, "layout_spec", "reference_asset_ids"], label: "参考图", value: first?.layout_spec?.reference_asset_ids || [], editable: false },
    { path: ["generated_outputs", "items", 0, "layout_spec", "image_engine"], label: "出图引擎", value: first?.layout_spec?.image_engine || "", editable: false },
    { path: ["generation_errors"], label: "异常", value: errors.length ? errors.map((item) => `${item.name || "unknown"}: ${item.error || ""}`) : (first?.layout_spec?.image_generation_error || "无"), editable: false },
    { path: ["generated_outputs", "items", 0, "layout_spec", "full_image_prompt"], label: "提示词", value: first?.layout_spec?.full_image_prompt || first?.prompt || "", editable: false },
  ];
}

function generationStatusLabel(status) {
  return {
    running: "生成中",
    completed: "已完成",
    partial_failed: "部分完成",
    failed: "失败",
  }[status] || "待生成";
}

function viSummaryFields(payload) {
  return [
    { path: ["brand_colors"], label: "品牌色", value: payload.brand_colors || [], editable: true },
    { path: ["logo_asset_id"], label: "LOGO", value: payload.logo_asset_id || "未提供，不虚构LOGO", editable: false },
    { path: ["typography_notes"], label: "字体", value: payload.typography_notes || "", editable: true },
    { path: ["layout_rules"], label: "版式", value: payload.layout_rules || [], editable: true },
    { path: ["forbidden_rules"], label: "禁用", value: payload.forbidden_rules || [], editable: true },
    { path: ["source_asset_ids"], label: "来源", value: payload.source_asset_ids || [], editable: false },
  ];
}

function uspSummaryFields(payload) {
  const fields = [];
  const diagnostics = payload?.agent_diagnostics || {};
  const evidence = payload?.evidence_summary || {};
  if (diagnostics.backend || diagnostics.model || diagnostics.status) {
    fields.push({
      path: ["agent_diagnostics"],
      label: "\u6a21\u578b",
      value: `${diagnostics.backend || "unknown"} / ${diagnostics.model || "unknown"} · ${diagnostics.fallback_used ? "\u5df2\u542f\u7528\u4fdd\u5e95" : "\u771f\u5b9e\u8fd4\u56de"}`,
      editable: false,
    });
  }
  const parsedDocs = Array.isArray(evidence.parsed_documents) ? evidence.parsed_documents : [];
  const analyzedImages = Array.isArray(evidence.analyzed_images) ? evidence.analyzed_images : [];
  if (parsedDocs.length || analyzedImages.length) {
    fields.push({
      path: ["evidence_summary"],
      label: "\u8d44\u6599",
      value: [
        ...parsedDocs.map((item) => `${item.filename || "\u6587\u6863"} ${item.page_count || 0}\u9875`),
        ...analyzedImages.map((item) => `${item.filename || "\u56fe\u7247"} ${item.engine || ""}`.trim()),
      ].slice(0, 6),
      editable: false,
    });
  }
  const core = Array.isArray(payload?.core) ? payload.core.slice(0, 3) : [];
  const secondary = Array.isArray(payload?.secondary) ? payload.secondary.slice(0, 3) : [];
  core.forEach((item, index) => {
    const headline = item?.headline || item?.title || "";
    const angle = item?.angle || "";
    const title = item?.title || (headline ? `「${headline}」${angle ? `——${angle}` : ""}` : "");
    fields.push({
      path: ["core", index, "title"],
      label: `\u6838\u5fc3${index + 1}`,
      value: title,
    });
    if (item?.content || item?.description) {
      fields.push({
        path: ["core", index, "content"],
        label: "\u5356\u70b9\u5185\u5bb9",
        value: item?.content || item?.description || "",
      });
    }
    if (item?.user_alignment?.parent) {
      fields.push({
        path: ["core", index, "user_alignment", "parent"],
        label: "\u7236\u6bcd\u671f\u5f85",
        value: item.user_alignment.parent,
      });
    }
    if (item?.user_alignment?.child) {
      fields.push({
        path: ["core", index, "user_alignment", "child"],
        label: "\u5b69\u5b50\u671f\u5f85",
        value: item.user_alignment.child,
      });
    }
    fields.push({
      path: ["core", index, "description"],
      label: "\u8bf4\u660e",
      value: item?.description || "",
    });
    if (Array.isArray(item?.aligned_expectations) && item.aligned_expectations.length) {
      fields.push({
        path: ["core", index, "aligned_expectations"],
        label: "\u5bf9\u9f50",
        value: item.aligned_expectations,
      });
    }
    if (Array.isArray(item?.product_evidence) && item.product_evidence.length) {
      fields.push({
        path: ["core", index, "product_evidence"],
        label: "\u8bc1\u636e",
        value: item.product_evidence.slice(0, 4),
      });
    }
    if (item?.product_visual_evidence) {
      fields.push({
        path: ["core", index, "product_visual_evidence"],
        label: "\u89c6\u89c9\u4f53\u73b0",
        value: item.product_visual_evidence,
      });
    }
    const comparisonRows = Array.isArray(item?.competitor_comparison_rows) ? item.competitor_comparison_rows : [];
    if (comparisonRows.length) {
      fields.push({
        path: ["core", index, "competitor_comparison_rows"],
        label: "\u7ade\u54c1\u8868",
        value: comparisonRows,
        render: "comparison_table",
        editable: false,
      });
    }
    if (item?.competitor_comparison) {
      fields.push({
        path: ["core", index, "competitor_comparison"],
        label: "\u7ade\u54c1",
        value: item.competitor_comparison,
      });
    }
    if (item?.competitiveness_judgement) {
      fields.push({
        path: ["core", index, "competitiveness_judgement"],
        label: "\u7ade\u4e89\u5224\u65ad",
        value: item.competitiveness_judgement,
      });
    }
    if (item?.visual_usage?.visual_event) {
      fields.push({
        path: ["core", index, "visual_usage", "visual_event"],
        label: "\u753b\u9762\u4e8b\u4ef6",
        value: item.visual_usage.visual_event,
      });
    }
    if (Array.isArray(item?.visual_usage?.short_tags) && item.visual_usage.short_tags.length) {
      fields.push({
        path: ["core", index, "visual_usage", "short_tags"],
        label: "\u5305\u88c5\u6807\u7b7e",
        value: item.visual_usage.short_tags,
      });
    }
  });
  secondary.forEach((item, index) => {
    const headline = item?.headline || item?.title || "";
    const angle = item?.angle || "";
    const title = item?.title || (headline ? `「${headline}」${angle ? `——${angle}` : ""}` : "");
    fields.push({
      path: ["secondary", index, "title"],
      label: `\u6b21\u8981${index + 1}`,
      value: title,
    });
    fields.push({
      path: ["secondary", index, "content"],
      label: "\u8bf4\u660e",
      value: item?.content || item?.description || "",
    });
    if (Array.isArray(item?.product_evidence) && item.product_evidence.length) {
      fields.push({
        path: ["secondary", index, "product_evidence"],
        label: "\u8f85\u8bc1",
        value: item.product_evidence.slice(0, 3),
      });
    }
  });
  const missing = Array.isArray(evidence.missing_or_failed_assets) ? evidence.missing_or_failed_assets : [];
  if (missing.length) {
    fields.push({
      path: ["evidence_summary", "missing_or_failed_assets"],
      label: "\u672a\u5b8c\u6210",
      value: missing.map((item) => `${item.filename || "\u8d44\u6599"}: ${item.reason || ""}`).slice(0, 4),
      editable: false,
    });
  }
  if (Array.isArray(payload?.notes) && payload.notes.length) {
    fields.push({ path: ["notes"], label: "\u5907\u6ce8", value: payload.notes.slice(0, 2), editable: false });
  }
  return fields;
}

function renderResultRow(field, draft, editable) {
  const row = document.createElement("div");
  row.className = "result-row";
  const key = document.createElement("div");
  key.className = "result-key";
  key.textContent = field.label;
  const val = document.createElement("div");
  val.className = "result-val";
  if (editable) {
    const editor = document.createElement("textarea");
    editor.className = "result-editor";
    editor.rows = Array.isArray(field.value) ? Math.min(Math.max(field.value.length, 2), 5) : 2;
    editor.value = editableValue(field.value);
    editor.placeholder = Array.isArray(field.value) ? "每行一条，可新增或删除" : "输入修改后的内容";
    editor.addEventListener("input", () => {
      setByPath(draft, field.path, parseEditableValue(editor.value, field.value));
    });
    val.append(editor);
    if (isColorField(field)) {
      const preview = document.createElement("div");
      preview.className = "color-preview-row";
      const renderPreview = () => {
        preview.replaceChildren();
        const values = parseEditableValue(editor.value, field.value);
        for (const item of Array.isArray(values) ? values : []) {
          preview.append(renderColorTag(item));
        }
      };
      editor.addEventListener("input", renderPreview);
      renderPreview();
      val.append(preview);
    }
  } else if (field.render === "comparison_table" && Array.isArray(field.value)) {
    val.append(renderComparisonTable(field.value));
  } else if (Array.isArray(field.value)) {
    const tags = document.createElement("div");
    tags.className = "tag-group";
    for (const item of field.value) {
      tags.append(isColorField(field) ? renderColorTag(item) : renderMiniTag(item));
    }
    val.append(tags);
  } else {
    val.textContent = formatValue(field.value);
  }
  row.append(key, val);
  return row;
}

function renderComparisonTable(rows) {
  const table = document.createElement("div");
  table.className = "comparison-table";
  const header = document.createElement("div");
  header.className = "comparison-row comparison-head";
  ["维度", "竞品", "本品"].forEach((text) => {
    const cell = document.createElement("div");
    cell.textContent = text;
    header.append(cell);
  });
  table.append(header);
  for (const item of rows.slice(0, 6)) {
    const row = document.createElement("div");
    row.className = "comparison-row";
    [item?.dimension, item?.competitor, item?.our_product].forEach((text) => {
      const cell = document.createElement("div");
      cell.textContent = formatValue(text || "");
      row.append(cell);
    });
    table.append(row);
  }
  return table;
}

function renderMiniTag(item) {
  const tag = document.createElement("span");
  tag.className = "mini-tag";
  tag.textContent = formatValue(item);
  return tag;
}

function isColorField(field) {
  return field?.path?.includes("brand_colors") || /品牌色|颜色|色彩/i.test(String(field?.label || ""));
}

function renderColorTag(item) {
  const tag = document.createElement("span");
  tag.className = "mini-tag color-tag";
  const color = normalizeColorValue(item);
  const swatch = document.createElement("span");
  swatch.className = "color-swatch";
  if (color) {
    swatch.style.background = color;
    swatch.title = color;
  } else {
    swatch.classList.add("unknown");
  }
  const text = document.createElement("span");
  text.textContent = formatValue(item);
  tag.append(swatch, text);
  return tag;
}

function normalizeColorValue(value) {
  const text = String(value || "").trim();
  const hexMatch = text.match(/#?[0-9a-fA-F]{6}\b/);
  if (hexMatch) return hexMatch[0].startsWith("#") ? hexMatch[0] : `#${hexMatch[0]}`;
  const rgbMatch = text.match(/rgba?\([^)]+\)/i);
  if (rgbMatch) return rgbMatch[0];
  const namedColors = {
    red: "#e53935",
    blue: "#1e88e5",
    green: "#43a047",
    purple: "#7e57c2",
    pink: "#ec407a",
    yellow: "#fdd835",
    orange: "#fb8c00",
    black: "#111111",
    white: "#ffffff",
    gray: "#9e9e9e",
    grey: "#9e9e9e",
    红: "#e53935",
    蓝: "#1e88e5",
    绿: "#43a047",
    紫: "#7e57c2",
    粉: "#ec407a",
    黄: "#fdd835",
    橙: "#fb8c00",
    黑: "#111111",
    白: "#ffffff",
    灰: "#9e9e9e",
  };
  const lower = text.toLowerCase();
  for (const [key, color] of Object.entries(namedColors)) {
    if (lower.includes(key)) return color;
  }
  return "";
}

function editableValue(value) {
  if (Array.isArray(value)) return value.map((item) => formatValue(item)).join("\n");
  if (value === undefined || value === null) return "";
  if (isObjectLike(value)) return JSON.stringify(value, null, 2);
  return String(value);
}

function parseEditableValue(value, originalValue) {
  const text = value.trim();
  if (Array.isArray(originalValue)) {
    return text
      .split(/\n|,|，|、/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  if (typeof originalValue === "number") {
    const numeric = Number(text);
    return Number.isFinite(numeric) ? numeric : originalValue;
  }
  if (isObjectLike(originalValue)) {
    try {
      return JSON.parse(text);
    } catch (_error) {
      return originalValue;
    }
  }
  return text;
}

function renderConceptSelection(gate, options = {}) {
  const items = conceptItemsFromPayload(gate.payload);
  if (!items.length) return null;
  const readonly = Boolean(options.readonly);
  const selectedId = state.selectedConcepts.get(gate.id) || items[0].id;
  state.selectedConcepts.set(gate.id, selectedId);

  const wrap = document.createElement("section");
  wrap.className = "concept-grid";
  wrap.style.flexShrink = "0";
  const label = document.createElement("div");
  label.className = "concept-label";
  label.textContent = readonly ? "图像生成 Agent · 已确认输出" : "图像生成 Agent · 请选择概念图";
  const cards = document.createElement("div");
  cards.className = "concept-cards";
  const action = readonly ? null : document.createElement("button");
  if (action) {
    action.className = "btn-confirm concept-action";
    action.type = "button";
    action.textContent = "进入细化";
  }

  const renderCards = () => {
    cards.replaceChildren();
    const current = state.selectedConcepts.get(gate.id);
    for (const item of items) {
      const card = document.createElement("div");
      if (!readonly) {
        card.tabIndex = 0;
        card.setAttribute("role", "button");
      }
      card.className = `concept-item${item.id === current ? " selected" : ""}${readonly ? " readonly" : ""}`;
      const selectItem = () => {
        state.selectedConcepts.set(gate.id, item.id);
        renderCards();
      };
      if (!readonly) {
        card.addEventListener("click", selectItem);
        card.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            selectItem();
          }
        });
      }
      const thumb = document.createElement("div");
      thumb.className = "concept-img";
      if (item.url) {
        const img = document.createElement("img");
        img.src = item.url;
        img.alt = item.title;
        img.addEventListener("error", () => {
          thumb.classList.add("missing");
          thumb.replaceChildren();
          const missing = document.createElement("div");
          missing.className = "concept-missing";
          missing.textContent = "图片文件缺失，请重新生成";
          thumb.append(missing);
          if (item.id === state.selectedConcepts.get(gate.id)) {
            const check = document.createElement("div");
            check.className = "concept-check";
            check.textContent = "✓";
            thumb.append(check);
          }
        });
        thumb.append(img);
        const download = document.createElement("a");
        download.className = "concept-download";
        download.href = item.downloadUrl || item.url;
        download.download = `${item.title || item.id || "generated-image"}.png`;
        download.title = "下载图片";
        download.setAttribute("aria-label", "下载图片");
        download.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3v12m0 0 5-5m-5 5-5-5M5 21h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        download.addEventListener("click", (event) => event.stopPropagation());
        thumb.append(download);
      } else {
        thumb.textContent = item.fallback;
      }
      if (item.id === current) {
        const check = document.createElement("div");
        check.className = "concept-check";
        check.textContent = "✓";
        thumb.append(check);
      }
      const info = document.createElement("div");
      info.className = "concept-info";
      const title = document.createElement("div");
      title.className = "concept-dir";
      title.textContent = item.title;
      const desc = document.createElement("div");
      desc.className = "concept-desc";
      desc.textContent = item.description;
      info.append(title, desc);
      card.append(thumb, info);
      cards.append(card);
    }
    const currentItem = items.find((item) => item.id === state.selectedConcepts.get(gate.id)) || items[0];
    if (action) action.textContent = `已选「${currentItem.title}」· 进入细化`;
  };
  renderCards();
  wrap.append(label, cards);
  if (action) wrap.append(action);
  return wrap;
}

function memorySections(detail) {
  const context = detail.confirmed_context || {};
  const pendingType = detail.pending_review_gate?.type || "";
  const project = detail.project || {};
  const brief = project.brief || {};
  const sections = [
    {
      key: "project",
      title: "项目定义",
      status: "confirmed",
      statusDot: "green",
      content: {
        category: brief.category || workflowLabel(detail.session.workflow_type),
        target_user: brief.target_user || "待补充",
        value_proposition: brief.value_proposition || brief.raw_text || "来自项目对话输入",
      },
    },
    {
      key: "assets",
      title: "资料解析",
      status: detail.assets.length ? "confirmed" : "locked",
      statusDot: detail.assets.length ? "green" : "gray",
      content: detail.assets.length
        ? {
            uploaded_count: `${detail.assets.length} 个文件`,
            asset_types: [...new Set(detail.assets.map((asset) => assetKindLabel(asset.kind)))],
            latest_file: detail.assets[detail.assets.length - 1]?.filename,
            processing: detail.assets
              .slice(-6)
              .map((asset) => `${asset.filename}: ${assetProcessingLabel(asset)}`),
          }
        : {},
    },
    memoryNode("usps", "核心卖点", context.confirmed_usps, pendingType === "usp_review", detail.pending_review_gate?.payload),
    memoryNode("vi", "VI 规范", context.confirmed_vi_profile, pendingType === "vi_review", detail.pending_review_gate?.payload),
  ];
  const workflow = progressWorkflowType(detail);
  if (workflow === "packaging") {
    sections.push(
      memoryNode(
        "packaging",
        "包装策略",
        context.confirmed_packaging_strategy,
        pendingType === "packaging_strategy_review",
        detail.pending_review_gate?.payload,
      ),
      memoryNode(
        "imagePrompt",
        "主图提示词",
        context.confirmed_image_prompt,
        pendingType === "image_prompt_review",
        detail.pending_review_gate?.payload,
      ),
      memoryNode("outputs", "生成图与质检", context.confirmed_outputs, pendingType === "final_design_review", detail.pending_review_gate?.payload),
    );
  } else if (workflow === "detail_page") {
    sections.push(
      memoryNode(
        "detail",
        "详情策略",
        context.confirmed_detail_page_strategy,
        pendingType === "detail_strategy_review",
        detail.pending_review_gate?.payload,
      ),
      memoryNode("outputs", "生成图与质检", context.confirmed_outputs, pendingType === "final_design_review", detail.pending_review_gate?.payload),
    );
  }
  return sections;
}

function progressWorkflowType(detail) {
  const workflow = detail?.session?.workflow_type;
  if (workflow === "packaging" || workflow === "detail_page") return workflow;
  const pendingType = detail?.pending_review_gate?.type || "";
  const context = detail?.confirmed_context || {};
  if (pendingType === "detail_strategy_review" || context.confirmed_detail_page_strategy) return "detail_page";
  if (
    pendingType === "packaging_strategy_review" ||
    pendingType === "image_prompt_review" ||
    context.confirmed_packaging_strategy ||
    context.confirmed_image_prompt
  ) {
    return "packaging";
  }
  return "unknown";
}

function memoryNode(key, title, confirmedContent, isPending, pendingContent) {
  if (confirmedContent) {
    return { key, title, status: "confirmed", statusDot: "green", content: confirmedContent };
  }
  if (isPending) {
    return { key, title, status: "pending", statusDot: "orange", content: pendingContent || {} };
  }
  return { key, title, status: "locked", statusDot: "gray", content: {} };
}

function emptyBlock(text) {
  const block = document.createElement("div");
  block.className = "empty-block";
  block.textContent = text;
  return block;
}

function emptyResultRow(label, value) {
  return renderResultRow({ label, value, path: [] }, {}, false);
}

function getSelectedAssetFile() {
  return getSelectedAssetFiles()[0] || null;
}

function getSelectedAssetFiles() {
  if (state.pendingAssetFiles?.length) return state.pendingAssetFiles;
  if (state.pendingAssetFile) return [state.pendingAssetFile];
  return Array.from(els.assetFileInput.files || []);
}

function appendPendingFiles(files) {
  const incoming = Array.from(files || []).filter(Boolean);
  if (!incoming.length) return;
  const merged = [];
  const seen = new Set();
  for (const file of [...getSelectedAssetFiles(), ...incoming]) {
    const key = `${file.name || ""}:${file.size || 0}:${file.lastModified || 0}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(file);
  }
  state.pendingAssetFiles = merged;
  state.pendingAssetFile = merged[0] || null;
  els.assetFileInput.value = "";
  updateAttachmentPreview(state.pendingAssetFile);
  saveComposerDraft();
}

function chooseUpload() {
  if (!state.selectedConversationId && !state.draftConversation) {
    showToast("请先新建或选择一个项目对话");
    return;
  }
  state.uploadIntent = "other";
  els.assetKindInput.value = "other";
  els.assetFileInput.accept = "";
  els.assetFileInput.click();
}

function updateAttachmentPreview(file = getSelectedAssetFile()) {
  const files = getSelectedAssetFiles();
  state.pendingAssetFile = file || null;
  state.pendingAssetFiles = files;
  if (files.length) {
    const totalSize = files.reduce((sum, item) => sum + item.size, 0);
    els.selectedAssetPreview.classList.add("hidden");
    els.assetFileName.textContent = files.length > 1 ? `${files.length} 个文件待上传` : files[0].name;
    els.assetFileMeta.textContent = `项目文件 · ${formatBytes(totalSize)}`;
    els.assetUploadHint.textContent = "上传后输入 @ 可在对话中引用";
  } else {
    els.selectedAssetPreview.classList.add("hidden");
    els.assetFileName.textContent = "未选择文件";
    els.assetFileMeta.textContent = "";
    els.assetUploadHint.textContent = state.selectedConversationId ? "上传后在输入框输入 @ 选择文件" : "先新建项目后再上传资料";
  }
  renderFileChips();
}

function clearAttachmentPreview() {
  state.pendingAssetFile = null;
  state.pendingAssetFiles = [];
  els.assetFileInput.value = "";
  updateAttachmentPreview(null);
}

function removePendingFile(fileToRemove) {
  const files = getSelectedAssetFiles().filter((file) => file !== fileToRemove);
  state.pendingAssetFiles = files;
  state.pendingAssetFile = files[0] || null;
  els.assetFileInput.value = "";
  updateAttachmentPreview(files[0] || null);
  saveComposerDraft();
}

function handleAssetDrop(event) {
  event.preventDefault();
  event.stopPropagation();
  els.attachmentDropzone.classList.remove("drag-over");
  const files = Array.from(event.dataTransfer?.files || []);
  if (!files.length) return;
  state.uploadIntent = "other";
  els.assetKindInput.value = "other";
  appendPendingFiles(files);
}

function draftForGate(gate) {
  if (!state.editDrafts.has(gate.id)) {
    state.editDrafts.set(gate.id, deepClone(gate.payload || {}));
  }
  return state.editDrafts.get(gate.id);
}

function flattenPayload(value, path = [], labels = []) {
  if (!isObjectLike(value)) {
    return [{ path, label: labels.length ? labels.join(" ") : "内容", value }];
  }
  if (Array.isArray(value)) {
    if (!value.length || value.every((item) => !isObjectLike(item))) {
      return [{ path, label: labels.join(" ") || "列表", value }];
    }
    return value.flatMap((item, index) => flattenPayload(item, [...path, index], [...labels, `${index + 1}`]));
  }
  return Object.entries(value).flatMap(([key, item]) =>
    flattenPayload(item, [...path, key], [...labels, fieldLabel(key)]),
  );
}

function getByPath(root, path) {
  return path.reduce((current, key) => (current == null ? undefined : current[key]), root);
}

function setByPath(root, path, value) {
  if (!path.length) return;
  let current = root;
  for (let index = 0; index < path.length - 1; index += 1) {
    current = current[path[index]];
    if (current == null) return;
  }
  current[path[path.length - 1]] = value;
}

function conceptItemsFromPayload(payload) {
  const rawItems = payload?.generated_outputs?.items || payload?.items || payload?.concepts || [];
  if (!Array.isArray(rawItems)) return [];
  return rawItems.slice(0, 6).map((item, index) => {
    const displayAssetId = generatedDisplayAssetId(item);
    return {
    id: item.id || displayAssetId || item.asset_id || `concept-${index}`,
    title: item.title || item.name || `方向 ${String.fromCharCode(65 + index)}`,
    description: item.description || item.prompt || item.side || "点击选择后进入细化流程",
    url: imageUrlForGeneratedItem(item),
    downloadUrl: imageDownloadUrlForGeneratedItem(item),
    fallback: ["A", "B", "C", "D", "E", "F"][index] || "图",
  };
  });
}

function imageUrlForGeneratedItem(item) {
  const displayAssetId = generatedDisplayAssetId(item);
  if (displayAssetId) return assetContentUrl(displayAssetId);
  const candidate = item?.url || item?.output_url || item?.image_url || item?.uri || "";
  if (candidate.startsWith("http://") || candidate.startsWith("https://") || candidate.startsWith("data:") || candidate.startsWith("/")) {
    return candidate;
  }
  return "";
}

function imageDownloadUrlForGeneratedItem(item) {
  const displayAssetId = generatedDisplayAssetId(item);
  if (displayAssetId) return assetContentUrl(displayAssetId, { download: true });
  return imageUrlForGeneratedItem(item);
}

function generatedDisplayAssetId(item) {
  const layoutSpec = item?.layout_spec && typeof item.layout_spec === "object" ? item.layout_spec : {};
  return (
    layoutSpec.base_asset_id ||
    item?.asset_id ||
    layoutSpec.composed_asset_id ||
    item?.composed_asset_id ||
    ""
  );
}

function displayConversationTitle(detail) {
  const context = detail?.confirmed_context || {};
  const brief = detail?.project?.brief || {};
  const candidates = [
    context.confirmed_packaging_strategy?.product_name,
    context.confirmed_detail_page_strategy?.product_name,
    brief.core_product_definition,
    brief.category,
    detail?.session?.title,
  ];
  for (const candidate of candidates) {
    const clean = cleanProductTitle(candidate);
    if (clean && !["新项目对话", "未命名对话", "未命名项目", "包装概念项目", "详情提案项目"].includes(clean)) return clean;
  }
  return detail?.session?.title || "未命名项目";
}

function cleanProductTitle(value = "") {
  let clean = String(value || "").replace(/\s+/g, " ").trim().replace(/^[ _：:，,。]+|[ _：:，,。]+$/g, "");
  if (!clean) return "";
  clean = clean.split(/\s*(?:请|我已|我上传|上传|@)/, 1)[0].replace(/^[ _：:，,。]+|[ _：:，,。]+$/g, "");
  clean = clean.replace(/^(?:这是)?(?:一个|一款|一套)/, "").replace(/[ _：:，,。]+$/g, "");
  clean = clean.replace(/(?:品类的项目|品类项目|包装概念方案|包装提案|详情提案|详情页|包装|项目)$/g, "").replace(/[ _：:，,。]+$/g, "");
  const possessive = clean.match(/^(.+?)的([^的，。,；;]{2,24})$/);
  if (possessive) {
    const modifier = possessive[1];
    const name = possessive[2].trim().replace(/^[ _：:，,。"“”]+|[ _：:，,。"“”]+$/g, "");
    if (/[“”"、，,]/.test(modifier) || modifier.length >= 8) clean = name;
  }
  return clean.replace(/^[ _：:，,。"“”]+|[ _：:，,。"“”]+$/g, "").slice(0, 40);
}

function assetContentUrl(assetId, options = {}) {
  if (!assetId || !state.detail?.project?.id) return "";
  const projectId = encodeURIComponent(state.detail.project.id);
  const encodedAssetId = encodeURIComponent(assetId);
  const suffix = options.download ? "?download=true" : "";
  return `/api/projects/${projectId}/assets/${encodedAssetId}/content${suffix}`;
}

function lastMessagePreview(messages) {
  const latest = messages[messages.length - 1];
  const content = latest?.content || "等待输入";
  return content.length > 24 ? `${content.slice(0, 24)}...` : content;
}

function workflowLabel(value) {
  if (value === "packaging") return "包装概念";
  if (value === "detail_page") return "详情提案";
  return "未判断";
}

function stageLabel(value) {
  const labels = {
    collecting_input: "收集输入",
    usp_review: "卖点确认",
    vi_review: "VI 确认",
    packaging_strategy_review: "包装策略确认",
    image_prompt_review: "主图提示词确认",
    detail_strategy_review: "详情策略确认",
    final_design_review: "设计图确认",
  };
  return labels[value] || value || "收集输入";
}

function stageShortLabel(stage, pendingGate) {
  if (pendingGate) return "待确认";
  if (stage === "collecting_input") return "进行中";
  return "运行中";
}

function stageTagClass(stage, pendingGate) {
  if (pendingGate) return "review";
  if (stage === "collecting_input") return "running";
  return "running";
}

function agentActivityText(detail) {
  if (detail.pending_review_gate) return "等待人工确认";
  const latest = detail.messages[detail.messages.length - 1];
  if (latest?.role === "tool") return "工具已完成";
  return "主 Agent 待命";
}

function roleLabel(role, type) {
  if (role === "tool") return "工具";
  if (role === "system") return "系统";
  if (type === "planner_decision") return "主 Agent";
  if (type === "status") return "系统状态";
  return "Agent";
}

function messageAgentLabel(message) {
  const reviewAgent = message.payload?.review_gate?.created_by_agent;
  if (reviewAgent) return agentLabel(reviewAgent);
  if (message.payload?.planner_decision) return "\u4e3b Agent";
  if (message.message_type === "status") return "\u6d41\u7a0b\u72b6\u6001";
  return roleLabel(message.role, message.message_type);
}

function agentLabel(value) {
  const labels = {
    usp_agent: "卖点提炼 Agent",
    packaging_strategy_agent: "包装策略 Agent",
    detail_page_strategy_agent: "详情策略 Agent",
    vi_understanding_agent: "VI \u7406\u89e3 Agent",
    strategy_agent: "策略 Agent",
    packaging_image_prompt_agent: "主图提示词 Agent",
    packaging_designer_agent: "包装出图 Agent",
    detail_designer_agent: "详情页出图 Agent",
    critic_agent: "质检 Agent",
  };
  return labels[value] || value || "子 Agent";
}

function agentInitial(message) {
  const reviewAgent = message.payload?.review_gate?.created_by_agent;
  if (reviewAgent) return agentInitialByName(reviewAgent);
  if (message.message_type === "planner_decision") return "P";
  if (message.message_type === "status") return "S";
  return "A";
}

function agentInitialByName(name) {
  if (name?.includes("usp")) return "U";
  if (name?.includes("vi")) return "VI";
  if (name?.includes("image_prompt")) return "IP";
  if (name?.includes("packaging")) return "P";
  if (name?.includes("detail")) return "D";
  if (name?.includes("critic")) return "Q";
  return "A";
}

function statusLabel(status) {
  const labels = {
    done: "完成",
    running: "运行中",
    waiting: "等待中",
    review: "\u5f85\u786e\u8ba4",
    error: "失败",
  };
  return labels[status] || status;
}

function reviewStatusLabel(status) {
  const labels = {
    pending: "待确认",
    approved: "已确认",
    edited: "已修改",
    rejected: "已退回",
    needs_more_info: "待补充",
  };
  return labels[status] || "待确认";
}

function memoryStatusLabel(status) {
  if (status === "confirmed") return "已确认";
  if (status === "pending") return "待确认";
  return "未解锁";
}

function resultTitle(gate) {
  const labels = {
    usp_review: "卖点提炼结果",
    packaging_strategy_review: "包装策略结果",
    image_prompt_review: "主图生图提示词",
    detail_strategy_review: "详情页策略结果",
    vi_review: "VI 规范结果",
    final_design_review: "概念图结果",
  };
  return labels[gate.type] || gate.title || "结构化结果";
}

function fieldLabel(key) {
  const labels = {
    core: "核心卖点",
    secondary: "次要卖点",
    notes: "备注",
    title: "标题",
    description: "说明",
    aligned_expectations: "对齐指标",
    product_evidence: "产品证据",
    competitor_comparison: "竞品对比",
    confidence: "置信度",
    product_name: "产品品名",
    box_type: "盒型方式",
    front_ratio: "正面比例",
    side_ratio: "侧面比例",
    top_ratio: "顶面比例",
    overall_tone: "整体影调",
    front_layout: "正面构图",
    left_layout: "左侧构图",
    right_layout: "右侧构图",
    back_layout: "背面构图",
    required_copy: "核心文案",
    required_icons: "标识图标",
    risk_notes: "风险备注",
    main_image_prompt: "主图提示词",
    negative_prompt: "负向约束",
    reference_usage: "参考图使用",
    layout_notes: "构图说明",
    text_overlay_plan: "后续叠加",
    page_theme: "详情页主题",
    screens: "详情页屏幕",
    screen_index: "屏幕",
    goal: "目标",
    visual: "视觉表达",
    copy_text: "页面文案",
    product_angle: "产品角度",
    proof_points: "证明点",
    category: "品类",
    target_user: "人群",
    value_proposition: "差异主张",
    uploaded_count: "已解析",
    asset_types: "资料类型",
    latest_file: "最新文件",
    processing: "解析状态",
  };
  return labels[key] || String(key).replaceAll("_", " ");
}

function assetKindLabel(kind) {
  const labels = {
    product_ppt: "产品 PPT",
    product_pdf: "产品 PDF",
    product_image: "产品图",
    competitor_image: "竞品图",
    competitor_packaging: "竞品包装",
    competitor_detail_page: "竞品详情页",
    vi_document: "VI 规范",
    logo: "LOGO",
    other: "其他",
  };
  return labels[kind] || kind || "素材";
}

function assetProcessingLabel(asset) {
  const processing = asset?.metadata?.processing || {};
  const status = processing.status || "uploaded";
  const progress = Number(processing.progress || 0);
  const labels = {
    uploaded: "已上传，待按需解析",
    queued: "排队解析",
    running: `解析中 ${progress}%`,
    completed: processing.cache_hit ? "已解析（缓存）" : "已解析",
    failed: "解析失败",
    cancelled: "已取消",
  };
  return labels[status] || status;
}

function inferAssetKind(file) {
  const type = file.type || "";
  const name = file.name.toLowerCase();
  if (type.startsWith("image/")) return "product_image";
  if (type.includes("pdf") || name.endsWith(".pdf")) return "product_pdf";
  if (name.endsWith(".ppt") || name.endsWith(".pptx")) return "product_ppt";
  return state.uploadIntent || "other";
}

function formatValue(value) {
  if (value === undefined || value === null || value === "") return "暂无";
  if (Array.isArray(value)) return value.map((item) => formatValue(item)).join("、");
  if (isObjectLike(value)) return JSON.stringify(value);
  return String(value);
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value <= 0) return "未知大小";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function approxTokens(detail) {
  const total = detail.messages.reduce((sum, message) => sum + (message.content || "").length, 0);
  return Math.max(1, Math.round(total / 1.6));
}

function isObjectLike(value) {
  return value !== null && typeof value === "object";
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setBusy(value) {
  state.busy = value;
  document.body.classList.toggle("is-busy", value);
  updateComposerState();
}

function updateComposerState() {
  const pendingReview = Boolean(state.detail?.pending_review_gate);
  const noConversation = !state.selectedConversationId && !state.draftConversation;
  const uploadDisabled = state.busy || noConversation;
  els.messageInput.disabled = state.busy;
  els.sendBtn.disabled = state.busy;
  els.uploadFileBtn.disabled = uploadDisabled;
  els.clearAssetBtn.disabled = state.busy || !state.pendingAssetFile;
  const sendLabel = els.sendBtn.querySelector(".send-label");
  if (sendLabel) sendLabel.textContent = state.busy ? "停止" : "发送";
  els.sendBtn.classList.toggle("is-stopping", state.busy);
  els.messageInput.placeholder = pendingReview
    ? "对当前结果不满意，可以直接输入修改意见；也可以输入“确认”继续..."
    : "追加说明、补充资料，或直接回复 Agent 的问题...";
  if (state.busy) {
    els.assetUploadHint.textContent = "Agent \u8fd0\u884c\u4e2d\uff0c\u6b63\u5728\u751f\u6210\u4e0b\u4e00\u6b65...";
  } else if (pendingReview) {
    els.assetUploadHint.textContent = "待确认状态下也可发送修正意见，Agent 会重新输出当前节点";
  } else if (!state.pendingAssetFile) {
    els.assetUploadHint.textContent = state.selectedConversationId
      ? "上传后在输入框输入 @ 选择文件"
      : "\u5148\u65b0\u5efa\u9879\u76ee\u540e\u518d\u4e0a\u4f20\u8d44\u6599";
  }
}

function resizeTextarea() {
  els.messageInput.style.height = "auto";
  els.messageInput.style.height = `${Math.min(Math.max(els.messageInput.scrollHeight, 36), 120)}px`;
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.remove("hidden");
  window.setTimeout(() => els.toast.classList.add("hidden"), 2400);
}

function showError(error) {
  showToast(error.message || String(error));
}

function appConfirm({ title, subtitle = "", body = "", items = [], note = "", confirmText = "确认", danger = false } = {}) {
  if (!els.confirmModal) return Promise.resolve(false);
  els.confirmModalTitle.textContent = title || "确认操作";
  els.confirmModalSubtitle.textContent = subtitle || "";
  els.confirmModalSubtitle.classList.toggle("hidden", !subtitle);
  els.confirmModalBody.replaceChildren();
  if (body) {
    const text = document.createElement("p");
    text.className = "modal-copy";
    text.textContent = body;
    els.confirmModalBody.append(text);
  }
  if (Array.isArray(items) && items.length) {
    const list = document.createElement("div");
    list.className = "modal-item-list";
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "modal-item";
      row.textContent = item;
      list.append(row);
    }
    els.confirmModalBody.append(list);
  }
  if (note) {
    const noteNode = document.createElement("div");
    noteNode.className = "modal-note";
    noteNode.textContent = note;
    els.confirmModalBody.append(noteNode);
  }
  els.confirmModalConfirm.textContent = confirmText;
  els.confirmModalConfirm.classList.toggle("danger", Boolean(danger));
  els.confirmModal.classList.remove("hidden");
  document.body.classList.add("modal-open");
  els.confirmModalConfirm.focus();
  return new Promise((resolve) => {
    const done = (value) => {
      els.confirmModal.classList.add("hidden");
      document.body.classList.remove("modal-open");
      els.confirmModalCancel.removeEventListener("click", onCancel);
      els.confirmModalClose.removeEventListener("click", onCancel);
      els.confirmModalConfirm.removeEventListener("click", onConfirm);
      els.confirmModal.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKeyDown);
      resolve(value);
    };
    const onCancel = () => done(false);
    const onConfirm = () => done(true);
    const onBackdrop = (event) => {
      if (event.target === els.confirmModal) done(false);
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") done(false);
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) done(true);
    };
    els.confirmModalCancel.addEventListener("click", onCancel);
    els.confirmModalClose.addEventListener("click", onCancel);
    els.confirmModalConfirm.addEventListener("click", onConfirm);
    els.confirmModal.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKeyDown);
  });
}

els.refreshBtn.addEventListener("click", () => loadConversations().catch(showError));
els.newConversationBtn.addEventListener("click", () => {
  switchView("chat");
  createBlankConversation().catch(showError);
});
els.manageProjectsBtn.addEventListener("click", toggleProjectManageMode);
els.knowledgeBtn.addEventListener("click", () => switchView(state.activeView === "knowledge" ? "chat" : "knowledge"));
els.deleteConversationBtn.addEventListener("click", () => deleteSelectedConversation().catch(showError));
els.cleanupAssetsBtn.addEventListener("click", () => cleanupOrphanAssets().catch(showError));
els.selectAllBtn.addEventListener("click", toggleSelectAllConversations);
els.clearSelectionBtn.addEventListener("click", clearConversationSelection);
els.batchDeleteBtn.addEventListener("click", () => batchDeleteSelectedConversations().catch(showError));
els.composerForm.addEventListener("submit", (event) => submitMessage(event).catch(showError));
els.knowledgeForm?.addEventListener("submit", (event) => saveKnowledgeEntry(event).catch(showError));
els.newKnowledgeBtn?.addEventListener("click", newKnowledgeDraft);
els.deleteKnowledgeBtn?.addEventListener("click", () => deleteSelectedKnowledgeEntry().catch(showError));
els.knowledgePreviewBtn?.addEventListener("click", () => previewCurrentProjectKnowledge().catch(showError));
els.knowledgeSearch?.addEventListener("input", () => {
  state.knowledgeFilters.query = els.knowledgeSearch.value || "";
  renderKnowledgeList();
});
els.knowledgeStatusFilter?.addEventListener("change", () => {
  state.knowledgeFilters.status = els.knowledgeStatusFilter.value || "all";
  renderKnowledgeList();
});
els.knowledgeDomainFilter?.addEventListener("change", () => {
  state.knowledgeFilters.domain = els.knowledgeDomainFilter.value || "all";
  renderKnowledgeList();
});
els.uploadFileBtn.addEventListener("click", () => chooseUpload());
els.clearAssetBtn.addEventListener("click", clearAttachmentPreview);
els.assetFileInput.addEventListener("change", () => {
  const files = Array.from(els.assetFileInput.files || []);
  appendPendingFiles(files);
});
els.messageInput.addEventListener("input", () => {
  resizeTextarea();
  saveComposerDraft();
  renderAssetMentionMenu();
});
els.messageInput.addEventListener("keydown", handleMessageInputKeydown);
els.messageInput.addEventListener("click", renderAssetMentionMenu);
els.messageInput.addEventListener("keyup", (event) => {
  if (event.key === "Escape") hideAssetMentionMenu();
  else renderAssetMentionMenu();
});
document.addEventListener("click", (event) => {
  if (!els.assetMentionMenu.contains(event.target) && event.target !== els.messageInput) {
    hideAssetMentionMenu();
  }
});
["dragenter", "dragover"].forEach((eventName) => {
  els.attachmentDropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    event.stopPropagation();
    els.attachmentDropzone.classList.add("drag-over");
  });
});
["dragleave", "dragend"].forEach((eventName) => {
  els.attachmentDropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    event.stopPropagation();
    els.attachmentDropzone.classList.remove("drag-over");
  });
});
els.attachmentDropzone.addEventListener("drop", handleAssetDrop);

updateAttachmentPreview();
updateComposerState();
startAutoRefresh();
Promise.all([loadHealth(), loadConversations()]).catch(showError);
