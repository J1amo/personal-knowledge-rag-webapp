const state = {
  sources: [],
  research: {
    projects: [],
    packs: [],
    currentProjectId: "",
    projectSources: [],
    outputTypes: [],
  },
};

const themeStorageKey = "personal-research-os-theme";
const allowedPalettes = new Set(["jade", "amber"]);
const allowedModes = new Set(["light", "dark"]);

function readStoredTheme() {
  const params = new URLSearchParams(window.location.search);
  const urlPalette = params.get("palette");
  const urlMode = params.get("mode");
  if (allowedPalettes.has(urlPalette) || allowedModes.has(urlMode)) {
    return {
      palette: allowedPalettes.has(urlPalette) ? urlPalette : "jade",
      mode: allowedModes.has(urlMode) ? urlMode : "light",
    };
  }
  try {
    const parsed = JSON.parse(localStorage.getItem(themeStorageKey) || "{}");
    return {
      palette: allowedPalettes.has(parsed.palette) ? parsed.palette : "jade",
      mode: allowedModes.has(parsed.mode) ? parsed.mode : "light",
    };
  } catch (_err) {
    return { palette: "jade", mode: "light" };
  }
}

function applyTheme(theme) {
  const palette = allowedPalettes.has(theme.palette) ? theme.palette : "jade";
  const mode = allowedModes.has(theme.mode) ? theme.mode : "light";
  document.body.dataset.palette = palette;
  document.body.dataset.mode = mode;
  document.querySelectorAll("[data-theme-palette]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themePalette === palette));
  });
  document.querySelectorAll("[data-theme-mode]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeMode === mode));
  });
  try {
    localStorage.setItem(themeStorageKey, JSON.stringify({ palette, mode }));
  } catch (_err) {
    // Theme persistence is a convenience; the UI should still work without storage.
  }
}

function initThemeControls() {
  applyTheme(readStoredTheme());
  document.querySelectorAll("[data-theme-palette]").forEach((button) => {
    button.addEventListener("click", () => applyTheme({ palette: button.dataset.themePalette, mode: document.body.dataset.mode }));
  });
  document.querySelectorAll("[data-theme-mode]").forEach((button) => {
    button.addEventListener("click", () => applyTheme({ palette: document.body.dataset.palette, mode: button.dataset.themeMode }));
  });
}

const domainLabel = {
  paper: "论文",
  chat: "对话",
  image: "图片",
  doc: "文档",
  project: "项目",
  note: "笔记",
  misc: "其他",
};

const sensitivityLabel = {
  public: "公开",
  private: "私有",
  confidential: "机密",
};

const sensitivityHint = {
  public: "公开：可进入 API 索引和 API 辅助分析，适合论文、公开网页和可外发资料。",
  private: "私有：默认只走本地检索；如需 API，必须在检索或维护时显式允许。",
  confidential: "机密：按最严格私有资料处理，默认阻止 API 索引、API 检索和 API LLM 分析。",
};

const statusLabel = {
  ok: "正常",
  ready: "就绪",
  partial: "部分完成",
  stopped: "已停止",
  failed: "失败",
  downloaded: "已下载",
  skipped_existing: "已存在",
  blocked_by_rate_limit: "限流阻止",
  blocked_by_access: "访问受限",
  blocked_by_captcha: "需要验证码",
  needs_login: "需要登录",
  imported: "已导入",
  parsed: "已解析",
  chunked: "已切块",
  indexed: "已索引",
  pending: "等待中",
  running: "运行中",
  not_requested: "未请求",
  unknown: "未知",
};

const retrievalModeLabel = {
  all_available: "全部可用索引",
  fast_local: "本地快速",
  private_local_only: "仅本地私有",
  api_only: "仅 API",
  strict_exhaustive: "严格穷尽",
};

const collectIntakePages = new Set(["upload", "literature", "doi"]);

function labelFrom(labels, value) {
  return labels[value] || value || "";
}

const branchLandingPage = {
  dashboard: "dashboard",
  collect: "upload",
  retrieve: "query",
  research: "research",
  publish: "outputs",
  maintain: "maintenance",
};

const pageBranch = {
  dashboard: "dashboard",
  upload: "collect",
  literature: "collect",
  doi: "collect",
  sources: "collect",
  processing: "collect",
  query: "retrieve",
  audits: "retrieve",
  pdf: "retrieve",
  compare: "retrieve",
  research: "research",
  outputs: "publish",
  maintenance: "maintain",
  settings: "maintain",
};

const pageMeta = {
  dashboard: ["00 工作台", "查看库状态、最近导入和失败记录。"],
  upload: ["01 收集 / 本地导入", "上传 PDF 或导入文件夹，进入统一数据层。"],
  literature: ["01 收集 / 文献发现", "按主题、关键词、期刊和年份获取 DOI 与摘要。"],
  doi: ["01 收集 / DOI 下载", "处理你明确提供且有合法访问权限的 DOI。"],
  sources: ["01 收集 / 文档库", "核对文档、资料源、原始路径、文本块和 PDF 入口。"],
  processing: ["01 收集 / 处理状态", "检查已导入、已解析、已切块、已索引和失败记录。"],
  query: ["02 检索 / 问答", "选择检索模式、隐私边界和分析模型，拿到证据。"],
  audits: ["02 检索 / 审计", "复查后端命中、合并去重、引用和修复线索。"],
  pdf: ["02 检索 / PDF 阅读器", "按来源 ID、页码和引用回到原始 PDF。"],
  compare: ["02 检索 / 评估", "比较不同检索模式的命中、重叠和缺口。"],
  research: ["03 Research / 工作台", "管理研究项目、资料范围和研究包输出。"],
  outputs: ["04 输出 / Markdown", "把证据打包成摘要、综述、汇报或 Codex 提示词。"],
  maintenance: ["05 维护 / 维护中心", "重建索引、备份数据库、生成 Codex 修复指导。"],
  settings: ["05 维护 / 设置", "检查本地 LLM / Gemma4 OpenAI-compatible 端点。"],
};

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_err) {
    data = { status: "failed", message: text };
  }
  if (!res.ok) {
    throw new Error(data.message || `HTTP ${res.status}`);
  }
  return data;
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function jsonBox(el, value) {
  el.textContent = JSON.stringify(value, null, 2);
}

function optionList(rows, valueKey, labeler, emptyLabel = "") {
  const empty = emptyLabel ? `<option value="">${esc(emptyLabel)}</option>` : "";
  return (
    empty +
    rows
      .map((row) => `<option value="${esc(row[valueKey])}">${esc(labeler(row))}</option>`)
      .join("")
  );
}

function initFilePickers() {
  document.querySelectorAll(".file-picker").forEach((picker) => {
    const input = picker.querySelector('input[type="file"]');
    const nameEl = picker.querySelector(".file-picker-name");
    if (!input || !nameEl) return;

    const emptyText = nameEl.dataset.empty || "未选择文件";
    const updateName = () => {
      const names = Array.from(input.files || []).map((file) => file.name);
      nameEl.textContent = names.length ? names.join(", ") : emptyText;
      picker.classList.toggle("has-file", names.length > 0);
    };

    input.addEventListener("change", updateName);
    updateName();
  });
}

function updateImportSensitivityHint() {
  const select = document.getElementById("importSensitivity");
  const hint = document.getElementById("importSensitivityHint");
  if (!select || !hint) return;
  hint.textContent = sensitivityHint[select.value] || "";
  hint.classList.toggle("warn", select.value !== "public");
}

function updateImportForm() {
  const form = document.getElementById("importForm");
  if (!form) return;

  const sourceKind = form.source_kind.value || "file";
  form.querySelectorAll("[data-import-source]").forEach((field) => {
    const active = field.dataset.importSource === sourceKind;
    field.hidden = !active;
    field.querySelectorAll("input, select, textarea").forEach((input) => {
      input.disabled = !active;
      input.required = active && (input.name === "file" || input.name === "folder");
    });
  });

  const submit = form.querySelector("[data-import-submit]");
  if (submit) submit.textContent = sourceKind === "folder" ? "导入文件夹" : "导入 PDF";
}

function table(rows, columns) {
  if (!rows.length) return '<div class="muted">暂无记录</div>';
  return `<table><thead><tr>${columns.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr></thead><tbody>${rows
    .map(
      (row) =>
        `<tr>${columns
          .map((c) => `<td>${typeof c.render === "function" ? c.render(row) : esc(row[c.key])}</td>`)
          .join("")}</tr>`,
    )
    .join("")}</tbody></table>`;
}

function updateWorkflowContext(page) {
  const [title, summary] = pageMeta[page] || pageMeta.dashboard;
  const titleEl = document.getElementById("workflowTitle");
  const summaryEl = document.getElementById("workflowSummary");
  if (titleEl) titleEl.textContent = title;
  if (summaryEl) summaryEl.textContent = summary;
}

function resolvePage(target) {
  return branchLandingPage[target] || target;
}

function updateNavigation(page) {
  const branch = pageBranch[page] || "dashboard";
  document.querySelectorAll(".home-button").forEach((btn) => btn.classList.toggle("active", page === "dashboard"));
  document.querySelectorAll(".flow-step").forEach((btn) => btn.classList.toggle("active", btn.dataset.branch === branch));
  document.querySelectorAll(".branch-item").forEach((btn) => {
    const active = btn.dataset.page === page || (btn.dataset.page === "upload" && collectIntakePages.has(page));
    btn.classList.toggle("active", active);
  });
  document.querySelectorAll(".mode-tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.page === page));

  const branchNav = document.querySelector(".branch-nav");
  const activeTools = document.querySelector(`.branch-tools[data-branch="${branch}"]`);
  if (branchNav) branchNav.hidden = !activeTools;
  document.querySelectorAll(".branch-tools").forEach((group) => {
    const active = group.dataset.branch === branch;
    group.classList.toggle("active", active);
    group.hidden = !active;
    group.setAttribute("aria-hidden", String(!active));
  });
}

function switchPage(target) {
  const page = resolvePage(target);
  updateWorkflowContext(page);
  updateNavigation(page);
  document.querySelectorAll(".page").forEach((el) => el.classList.toggle("active", el.id === `page-${page}`));
  if (page === "dashboard") loadDashboard();
  if (page === "sources") loadSources();
  if (page === "doi") loadDoiDownloads();
  if (page === "maintenance") loadCoverage();
  if (page === "processing") loadProcessing();
  if (page === "audits") loadAudits();
  if (page === "outputs") loadOutputs();
  if (page === "research") loadResearch();
  if (page === "settings") loadLocalLlmStatus();
}

function renderMetrics(counts) {
  const items = [
    ["资料源", counts.sources],
    ["文本块", counts.chunks],
    ["论文", counts.papers],
    ["多模态元素", counts.multimodal_elements],
    ["失败", counts.failed_sources],
  ];
  document.getElementById("metrics").innerHTML = items
    .map(([label, value]) => `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`)
    .join("");
}

function renderCoverage(summary) {
  const rows = Object.entries(summary.indexed_by || {}).map(([name, indexed]) => ({
    name,
    indexed,
    missing: summary.missing_by?.[name] ?? 0,
  }));
  document.getElementById("coverageSummary").innerHTML =
    table(rows, [
      { label: "索引", key: "name" },
      { label: "已索引", key: "indexed" },
      { label: "缺失", key: "missing" },
    ]) +
    `<p><span class="pill warn">全部缺失: ${esc(summary.missing_all_indexes)}</span> <span class="pill warn">过期: ${esc(
      summary.stale_indexes,
    )}</span> <span class="pill bad">失败: ${esc(summary.failed_chunks)}</span></p>`;
}

async function loadDashboard() {
  const data = await api("/api/dashboard");
  renderMetrics(data.counts || {});
  renderCoverage(data.coverage || {});
  document.getElementById("backendStatus").innerHTML = table(
    Object.entries(data.backend_status || {}).map(([key, value]) => ({ key, value })),
    [
      { label: "项目", key: "key" },
      { label: "值", render: (row) => `<code>${esc(row.value)}</code>` },
    ],
  );
  document.getElementById("recentIngestions").innerHTML = table(data.recent_ingestions || [], [
    { label: "source_id", render: (row) => `<code>${esc(row.source_id)}</code>` },
    { label: "文件", key: "original_filename" },
    { label: "领域", render: (row) => esc(labelFrom(domainLabel, row.domain)) },
    { label: "主题", key: "topic" },
    { label: "敏感级别", render: (row) => esc(labelFrom(sensitivityLabel, row.sensitivity)) },
    { label: "状态", render: (row) => esc(labelFrom(statusLabel, row.ingestion_status)) },
    { label: "导入时间", key: "ingested_at" },
  ]);
  document.getElementById("failedIngestions").innerHTML = table(data.failed_ingestions || [], [
    { label: "source_id", key: "source_id" },
    { label: "阶段", key: "stage" },
    { label: "信息", key: "message" },
    { label: "创建时间", key: "created_at" },
  ]);
}

async function loadSources() {
  const data = await api("/api/sources");
  const docs = await api("/api/documents");
  state.sources = data.sources || [];
  const docRows = docs.documents || [];
  const docHtml = `<h3>文档</h3>${table(docRows, [
    { label: "document_id", render: (row) => `<code>${esc(row.document_id)}</code>` },
    { label: "标题", key: "title" },
    { label: "source_id", render: (row) => `<code>${esc(row.primary_source_id)}</code>` },
    { label: "状态", render: (row) => esc(labelFrom(statusLabel, row.processing_status)) },
    { label: "文本块", key: "chunk_count" },
  ])}<h3>资料源</h3>`;
  document.getElementById("sourcesTable").innerHTML = table(state.sources, [
    { label: "source_id", render: (row) => `<button class="secondary source-open" data-source="${esc(row.source_id)}">${esc(row.source_id)}</button>` },
    { label: "文件", key: "original_filename" },
    { label: "领域", render: (row) => esc(labelFrom(domainLabel, row.domain)) },
    { label: "主题", key: "topic" },
    { label: "敏感级别", render: (row) => esc(labelFrom(sensitivityLabel, row.sensitivity)) },
    { label: "文本块", key: "chunk_count" },
    { label: "原始文件", render: (row) => `<code>${esc(row.raw_path)}</code> <a href="/viewer?source_id=${encodeURIComponent(row.source_id)}&page=1" target="_blank">PDF</a>` },
    { label: "哈希", render: (row) => `<code>${esc(String(row.file_hash || "").slice(0, 12))}</code>` },
  ]);
  document.getElementById("sourcesTable").insertAdjacentHTML("afterbegin", docHtml);
  document.querySelectorAll(".source-open").forEach((btn) => {
    btn.addEventListener("click", () => loadChunks(btn.dataset.source));
  });
}

async function loadProcessing() {
  const data = await api("/api/processing-status");
  document.getElementById("processingTable").innerHTML = table(data.documents || [], [
    { label: "文档", render: (row) => `<code>${esc(row.document_id)}</code>` },
    { label: "文件", key: "original_filename" },
    { label: "流水线", render: (row) => `<code>${esc(row.stage_status_json)}</code>` },
    { label: "解析器", key: "parser_version" },
    { label: "文本块", key: "chunk_count" },
    { label: "缺失索引", key: "missing_index_count" },
    { label: "最近处理", key: "last_processed_at" },
  ]);
  document.getElementById("processingErrors").innerHTML = table(data.errors || [], [
    { label: "阶段", key: "stage" },
    { label: "资料源", key: "source_id" },
    { label: "信息", key: "error_message" },
    { label: "创建时间", key: "created_at" },
  ]);
}

async function loadAudits() {
  const data = await api("/api/retrieval-audits");
  document.getElementById("auditsTable").innerHTML = table(data.audits || [], [
    { label: "audit_id", render: (row) => `<button class="secondary audit-open" data-audit="${esc(row.audit_id)}">${esc(row.audit_id)}</button>` },
    { label: "查询", key: "query_text" },
    { label: "模式", render: (row) => esc(labelFrom(retrievalModeLabel, row.retrieval_mode)) },
    { label: "后端", render: (row) => `<code>${esc(row.backends_used_json)}</code>` },
    { label: "合并", render: (row) => `<code>${esc(row.dropped_duplicates_json)}</code>` },
    { label: "警告", render: (row) => `<code>${esc(row.warning_flags_json)}</code>` },
    { label: "创建时间", key: "created_at" },
  ]);
  document.querySelectorAll(".audit-open").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.getElementById("auditDetailId").value = btn.dataset.audit;
      await loadAuditDetail(btn.dataset.audit);
    });
  });
}

async function loadAuditDetail(auditId) {
  const id = auditId || document.getElementById("auditDetailId").value.trim();
  if (!id) throw new Error("需要填写 audit_id");
  const data = await api(`/api/retrieval-audit?audit_id=${encodeURIComponent(id)}`);
  jsonBox(document.getElementById("auditDetailBox"), data);
}

async function generateAuditRepair() {
  const auditId = document.getElementById("auditDetailId").value.trim();
  if (!auditId) throw new Error("需要填写 audit_id");
  const result = await api("/api/retrieval-audits/repair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      audit_id: auditId,
      expected_behavior: document.getElementById("auditExpectedBehavior").value,
    }),
  });
  jsonBox(document.getElementById("auditDetailBox"), result);
}

async function loadLocalLlmStatus() {
  const data = await api("/api/local-llm/status");
  jsonBox(document.getElementById("localLlmStatus"), data);
}

async function loadOutputs() {
  const data = await api("/api/outputs");
  document.getElementById("outputsTable").innerHTML = table(data.outputs || [], [
    { label: "output_id", render: (row) => `<code>${esc(row.output_id)}</code>` },
    { label: "类型", key: "output_type" },
    { label: "标题", key: "title" },
    { label: "路径", render: (row) => `<code>${esc(row.file_path)}</code>` },
    { label: "质量检查", render: (row) => `<code>${esc(row.quality_checks_json)}</code>` },
    { label: "创建时间", key: "created_at" },
  ]);
}

function currentResearchProject() {
  return state.research.projects.find((project) => project.project_id === state.research.currentProjectId) || null;
}

function renderResearchSelectors() {
  const packSelect = document.getElementById("researchPackSelect");
  packSelect.innerHTML = optionList(
    state.research.packs,
    "pack_id",
    (pack) => `${pack.name} (${pack.pack_id})`,
    "无 Pack",
  );

  const projectSelect = document.getElementById("researchProjectSelect");
  projectSelect.innerHTML = optionList(
    state.research.projects,
    "project_id",
    (project) => `${project.name} (${project.source_count || 0})`,
    "未选择",
  );
  projectSelect.value = state.research.currentProjectId;

  const sourceSelect = document.getElementById("researchSourceSelect");
  sourceSelect.innerHTML = optionList(
    state.sources,
    "source_id",
    (source) => `${source.original_filename} / ${source.source_id}`,
    "未选择",
  );

  const project = currentResearchProject();
  jsonBox(document.getElementById("researchProjectDetail"), project || { status: "empty" });
}

function renderResearchOutputTypes() {
  const select = document.getElementById("researchOutputType");
  select.innerHTML = optionList(
    state.research.outputTypes,
    "output_type",
    (item) => `${item.title} (${item.source})`,
  );
}

async function loadResearchProjectContext() {
  if (!state.research.currentProjectId) {
    state.research.projectSources = [];
    state.research.outputTypes = [];
    document.getElementById("researchSourcesTable").innerHTML = '<div class="muted">暂无 Project</div>';
    renderResearchOutputTypes();
    return;
  }

  const [sourcesData, outputTypeData] = await Promise.all([
    api(`/api/research/project/sources?project_id=${encodeURIComponent(state.research.currentProjectId)}`),
    api(`/api/research/output-types?project_id=${encodeURIComponent(state.research.currentProjectId)}`),
  ]);
  state.research.projectSources = sourcesData.sources || [];
  state.research.outputTypes = outputTypeData.output_types || [];
  renderResearchOutputTypes();
  document.getElementById("researchSourcesTable").innerHTML = table(state.research.projectSources, [
    { label: "source_id", render: (row) => `<code>${esc(row.source_id)}</code>` },
    { label: "文件", key: "original_filename" },
    { label: "Role", key: "role" },
    { label: "主题", key: "topic" },
    { label: "敏感级别", render: (row) => esc(labelFrom(sensitivityLabel, row.sensitivity)) },
    { label: "文本块", key: "chunk_count" },
    {
      label: "操作",
      render: (row) => `<button class="secondary research-source-remove" data-source="${esc(row.source_id)}">移除</button>`,
    },
  ]);
  document.querySelectorAll(".research-source-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api("/api/research/project/sources/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: state.research.currentProjectId,
          source_id: btn.dataset.source,
        }),
      });
      await loadResearch();
    });
  });
}

async function loadResearch() {
  const [projectsData, packsData, sourcesData] = await Promise.all([
    api("/api/research/projects"),
    api("/api/research/packs"),
    api("/api/sources"),
  ]);
  state.research.projects = projectsData.projects || [];
  state.research.packs = packsData.packs || [];
  state.sources = sourcesData.sources || [];
  if (!state.research.projects.some((project) => project.project_id === state.research.currentProjectId)) {
    state.research.currentProjectId = state.research.projects[0]?.project_id || "";
  }
  renderResearchSelectors();
  await loadResearchProjectContext();
}

function switchResearchPanel(panel) {
  document.querySelectorAll(".research-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.researchPanel === panel);
  });
  document.querySelectorAll(".research-panel").forEach((el) => {
    const active = el.dataset.researchPanelView === panel;
    el.classList.toggle("active", active);
    el.hidden = !active;
  });
}

async function loadDoiDownloads() {
  const [status, downloads] = await Promise.all([api("/api/doi-downloader/status"), api("/api/doi-downloads")]);
  jsonBox(document.getElementById("doiStatus"), status);
  document.getElementById("doiJobsTable").innerHTML = `<h3>任务</h3>${table(downloads.jobs || [], [
    { label: "job_id", render: (row) => `<code>${esc(row.job_id)}</code>` },
    { label: "状态", render: (row) => esc(labelFrom(statusLabel, row.status)) },
    { label: "输入", key: "input_count" },
    { label: "请求", key: "requested_count" },
    { label: "摘要", render: (row) => `<code>${esc(JSON.stringify(row.summary || {}))}</code>` },
    { label: "更新时间", key: "updated_at" },
  ])}`;
  document.getElementById("doiItemsTable").innerHTML = `<h3>条目</h3>${table(downloads.items || [], [
    { label: "doi", key: "doi" },
    { label: "状态", render: (row) => esc(labelFrom(statusLabel, row.status)) },
    { label: "出版方域名", key: "publisher_domain" },
    { label: "保存路径", render: (row) => `<code>${esc(row.saved_path || "")}</code>` },
    { label: "原因", key: "failure_reason" },
    { label: "更新时间", key: "updated_at" },
  ])}`;
}

async function loadChunks(sourceId) {
  const data = await api(`/api/chunks?source_id=${encodeURIComponent(sourceId)}`);
  document.getElementById("chunksTable").innerHTML = table(data.chunks || [], [
    { label: "chunk_id", render: (row) => `<code>${esc(row.chunk_id)}</code>` },
    { label: "页码", key: "page_number" },
    { label: "章节", key: "section_title" },
    { label: "预览", key: "text_preview" },
  ]);
}

async function loadCoverage() {
  const data = await api("/api/coverage");
  const summary = data.summary || {};
  const blocks = [
    `<h3>摘要</h3>${table(
      Object.entries(summary.indexed_by || {}).map(([name, indexed]) => ({ name, indexed, missing: summary.missing_by?.[name] ?? 0 })),
      [
        { label: "索引", key: "name" },
        { label: "已索引", key: "indexed" },
        { label: "缺失", key: "missing" },
      ],
    )}`,
    `<h3>缺失</h3>${table(data.missing || [], [
      { label: "索引", key: "index_name" },
      { label: "文本块", render: (row) => `<code>${esc(row.chunk_id)}</code>` },
      { label: "文件", key: "original_filename" },
      { label: "页码", key: "page_number" },
      { label: "错误", key: "index_error" },
    ])}`,
    `<h3>过期</h3>${table(data.stale || [], [
      { label: "索引", key: "index_name" },
      { label: "文本块", render: (row) => `<code>${esc(row.chunk_id)}</code>` },
      { label: "文件", key: "original_filename" },
      { label: "页码", key: "page_number" },
    ])}`,
    `<h3>失败</h3>${table(data.failed || [], [
      { label: "索引", key: "index_name" },
      { label: "文本块", render: (row) => `<code>${esc(row.chunk_id)}</code>` },
      { label: "文件", key: "original_filename" },
      { label: "错误", key: "index_error" },
    ])}`,
  ];
  document.getElementById("coverageDetail").innerHTML = blocks.join("");
}

function filtersFromForm(form) {
  const domain = form.domain?.value;
  const sensitivity = form.sensitivity?.value;
  return {
    domains: domain ? [domain] : [],
    sensitivities: sensitivity ? [sensitivity] : [],
  };
}

function updatePolicyBox() {
  const retrieval = document.getElementById("retrievalMode").value;
  const analysis = document.getElementById("analysisModel").value;
  const sensitivity = document.getElementById("sensitivityFilter").value;
  const box = document.getElementById("apiPolicy");
  const apiRetrieval = ["api_only", "all_available", "strict_exhaustive"].includes(retrieval);
  const apiAnalysis = ["api_llm", "auto"].includes(analysis);
  box.className = "policy-box";
  if (["private", "confidential"].includes(sensitivity) && (apiRetrieval || analysis === "api_llm")) {
    box.textContent = "私有范围：除非明确允许，否则不会使用 API。";
    box.classList.add("bad");
  } else if (apiRetrieval || apiAnalysis) {
    box.textContent = "如果已配置且策略允许，可能使用 API。";
    box.classList.add("warn");
  } else {
    box.textContent = "仅使用本地检索和分析。";
  }
}

function renderQueryResult(data) {
  const analysis = data.analysis || {};
  const retrieval = data.retrieval || {};
  document.getElementById("answerBox").textContent = [
    `检索模式: ${labelFrom(retrievalModeLabel, data.retrieval?.retrieval_mode)}`,
    `分析后端: ${analysis.analysis_backend}`,
    `使用 API: ${analysis.api_used}`,
    `耗时: ${data.latency_ms} ms`,
    retrieval.errors?.length ? `警告: ${retrieval.errors.join("; ")}` : "",
    "",
    analysis.answer || "",
  ]
    .filter((line) => line !== "")
    .join("\n");
  const evidence = retrieval.evidence || [];
  document.getElementById("evidenceList").innerHTML = evidence.length
    ? evidence
        .map(
          (item) => `<article class="evidence-card">
            <div class="evidence-meta">
              <span class="pill ok">排序 ${esc(item.final_rank)}</span>
              <span class="pill">${esc(item.source_id)}</span>
              <span class="pill">${esc(item.original_filename)}</span>
              <span class="pill">第 ${esc(item.page_number ?? "")} 页</span>
              <span class="pill">来源 ${esc((item.found_by || []).join(", "))}</span>
              <a class="pill" href="/viewer?source_id=${encodeURIComponent(item.source_id)}&page=${encodeURIComponent(item.page_number || 1)}&chunk_id=${encodeURIComponent(item.chunk_id)}" target="_blank">打开 PDF</a>
            </div>
            <div class="evidence-text">${esc(String(item.text || "").slice(0, 900))}</div>
          </article>`,
        )
        .join("")
    : '<div class="muted">暂无证据</div>';
}


function renderLiteratureResults(data) {
  const status = document.getElementById("literatureStatus");
  const warnings = data.warnings?.length ? ` · ${data.warnings.join(" ")}` : "";
  status.className = "policy-box";
  if (data.warnings?.length) status.classList.add("warn");
  status.textContent = `${data.count || 0} 篇候选 · 来源=${data.source || "未知"} · 翻译=${(data.translation?.statuses || []).map((item) => labelFrom(statusLabel, item)).join(", ") || "无"}${warnings}`;

  const results = data.results || [];
  document.getElementById("literatureResults").innerHTML = results.length
    ? results
        .map((item, idx) => {
          const doi = item.doi ? `<span class="pill ok">DOI ${esc(item.doi)}</span>` : '<span class="pill warn">缺少 DOI</span>';
          const authors = (item.authors || []).slice(0, 6).join(", ");
          const links = [
            item.source_url ? `<a class="pill" href="${esc(item.source_url)}" target="_blank" rel="noreferrer">来源</a>` : "",
            item.pdf_url ? `<a class="pill" href="${esc(item.pdf_url)}" target="_blank" rel="noreferrer">PDF</a>` : "",
          ]
            .filter(Boolean)
            .join("");
          return `<article class="evidence-card literature-card">
            <div class="evidence-meta">
              <span class="pill">#${idx + 1}</span>
              ${doi}
              <span class="pill">${esc(item.journal || "期刊未知")}</span>
              <span class="pill">${esc(item.year || "年份未知")}</span>
              <span class="pill">引用 ${esc(item.cited_by_count ?? 0)}</span>
              <span class="pill ${item.translation_status === "ready" ? "ok" : item.translation_status === "not_requested" ? "" : "warn"}">翻译 ${esc(labelFrom(statusLabel, item.translation_status || "unknown"))}</span>
              ${links}
            </div>
            <h4 class="literature-title">${esc(item.title)}</h4>
            <div class="muted literature-authors">${esc(authors)}</div>
            <div class="evidence-text abstract-text">${esc(item.abstract_display || item.abstract_en || "没有返回摘要。")}</div>
          </article>`;
        })
        .join("")
    : '<div class="muted">没有候选文章。可以放宽期刊目录、年份或关键词。</div>';
}

async function runLiteratureDiscovery(form) {
  const payload = {
    query: form.query.value,
    keywords: form.keywords.value,
    journals: form.journals.value,
    year_from: form.year_from.value,
    year_to: form.year_to.value,
    max_results: Number(form.max_results.value || 8),
    language_mode: form.language_mode.value,
    translate: Boolean(form.translate.checked),
  };
  const status = document.getElementById("literatureStatus");
  status.className = "policy-box warn";
  status.textContent = "正在从 OpenAlex 获取论文元数据，并按需要调用本地翻译端点...";
  document.getElementById("literatureResults").innerHTML = "";
  const result = await api("/api/literature/discover", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  renderLiteratureResults(result);
}

async function init() {
  initThemeControls();
  updateWorkflowContext("dashboard");
  updateNavigation("dashboard");
  initFilePickers();
  updateImportForm();
  updateImportSensitivityHint();
  document.querySelectorAll(".nav-item").forEach((btn) => btn.addEventListener("click", () => switchPage(btn.dataset.page)));
  document.getElementById("refreshDashboard").addEventListener("click", loadDashboard);
  document.getElementById("refreshSources").addEventListener("click", loadSources);
  document.getElementById("refreshDoiDownloads").addEventListener("click", loadDoiDownloads);
  document.getElementById("refreshCoverage").addEventListener("click", loadCoverage);
  document.getElementById("refreshProcessing").addEventListener("click", loadProcessing);
  document.getElementById("refreshAudits").addEventListener("click", loadAudits);
  document.getElementById("refreshOutputs").addEventListener("click", loadOutputs);
  document.getElementById("refreshResearch").addEventListener("click", loadResearch);
  document.getElementById("loadAuditDetail").addEventListener("click", () => loadAuditDetail());
  document.getElementById("generateAuditRepair").addEventListener("click", generateAuditRepair);
  document.getElementById("checkLocalLlm").addEventListener("click", loadLocalLlmStatus);
  document.getElementById("retrievalMode").addEventListener("change", updatePolicyBox);
  document.getElementById("analysisModel").addEventListener("change", updatePolicyBox);
  document.getElementById("sensitivityFilter").addEventListener("change", updatePolicyBox);
  document.querySelectorAll('#importForm input[name="source_kind"]').forEach((input) =>
    input.addEventListener("change", updateImportForm),
  );
  document.getElementById("importSensitivity").addEventListener("change", updateImportSensitivityHint);
  document.querySelectorAll(".research-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchResearchPanel(btn.dataset.researchPanel));
  });
  document.getElementById("researchProjectSelect").addEventListener("change", async (event) => {
    state.research.currentProjectId = event.currentTarget.value;
    renderResearchSelectors();
    await loadResearchProjectContext();
  });

  document.getElementById("importForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const sourceKind = form.source_kind.value || "file";
    let result;
    if (sourceKind === "folder") {
      const data = Object.fromEntries(new FormData(form).entries());
      const payload = {
        folder: data.folder,
        domain: data.domain,
        topic: data.topic,
        sensitivity: data.sensitivity,
      };
      result = await api("/api/ingest/folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      const data = new FormData(form);
      data.delete("source_kind");
      result = await api("/api/ingest/upload", { method: "POST", body: data });
    }
    jsonBox(document.getElementById("ingestResult"), result);
    await loadDashboard();
  });

  document.getElementById("researchProjectForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const project = await api("/api/research/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.name.value,
        description: form.description.value,
        pack_id: form.pack_id.value || null,
      }),
    });
    state.research.currentProjectId = project.project_id;
    form.reset();
    await loadResearch();
  });

  document.getElementById("researchSourceForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!state.research.currentProjectId) throw new Error("需要先选择 Project");
    await api("/api/research/project/sources/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: state.research.currentProjectId,
        source_id: form.source_id.value,
        role: form.role.value,
      }),
    });
    await loadResearch();
  });

  document.getElementById("doiForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      doi_text: form.doi_text.value,
      out_dir: form.out_dir.value,
      max_items: Number(form.max_items.value || 10),
      headed: Boolean(form.headed.checked),
      allow_manual_login: Boolean(form.allow_manual_login.checked),
      fast_mode: Boolean(form.fast_mode.checked),
      auto_ingest: Boolean(form.auto_ingest.checked),
    };
    const result = await api("/api/doi-downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    jsonBox(document.getElementById("doiResult"), result);
    await loadDoiDownloads();
    if (payload.auto_ingest) await loadSources();
  });

  document.getElementById("clearDoiProfile").addEventListener("click", async () => {
    const result = await api("/api/doi-downloader/clear-profile", { method: "POST" });
    jsonBox(document.getElementById("doiResult"), result);
    await loadDoiDownloads();
  });

  document.getElementById("literatureForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await runLiteratureDiscovery(event.currentTarget);
    } catch (err) {
      const status = document.getElementById("literatureStatus");
      status.className = "policy-box bad";
      status.textContent = err.message || "文献发现请求失败";
    }
  });

  document.getElementById("queryForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      question: form.question.value,
      retrieval_mode: form.retrieval_mode.value,
      analysis_model: form.analysis_model.value,
      filters: filtersFromForm(form),
      top_k: Number(form.top_k.value || 8),
      allow_private_api: Boolean(form.allow_private_api.checked),
    };
    const result = await api("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderQueryResult(result);
  });

  document.querySelectorAll("[data-rebuild]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const result = await api("/api/index/rebuild", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          index_names: [btn.dataset.rebuild],
          include_private_api: document.getElementById("includePrivateApi").checked,
        }),
      });
      jsonBox(document.getElementById("maintenanceResult"), result);
      await loadCoverage();
    });
  });

  document.getElementById("backupDb").addEventListener("click", async () => {
    const result = await api("/api/backup", { method: "POST" });
    jsonBox(document.getElementById("maintenanceResult"), result);
  });

  document.getElementById("compareForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await api("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: form.question.value,
        filters: filtersFromForm(form),
        top_k: Number(form.top_k.value || 8),
      }),
    });
    jsonBox(document.getElementById("compareResult"), result);
  });

  document.getElementById("pdfForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    window.open(`/viewer?source_id=${encodeURIComponent(form.source_id.value)}&page=${encodeURIComponent(form.page.value || 1)}`, "_blank");
  });

  document.getElementById("outputForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await api("/api/outputs/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: form.question.value,
        output_type: form.output_type.value,
        retrieval_mode: form.retrieval_mode.value,
        top_k: Number(form.top_k.value || 8),
        filters: { domains: ["paper"], sensitivities: ["public"] },
        llm_backend: "gemma4",
      }),
    });
    jsonBox(document.getElementById("outputResult"), {
      输出ID: result.output_id,
      文件路径: result.file_path,
      质量检查: result.quality_checks,
      LLM: result.llm,
    });
    await loadOutputs();
  });

  document.getElementById("researchOutputForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!state.research.currentProjectId) throw new Error("需要先选择 Project");
    const result = await api("/api/research/output/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: state.research.currentProjectId,
        question: form.question.value,
        output_type: form.output_type.value,
        retrieval_mode: form.retrieval_mode.value,
        top_k: Number(form.top_k.value || 8),
        llm_backend: "gemma4",
      }),
    });
    jsonBox(document.getElementById("researchOutputResult"), {
      输出ID: result.output_id,
      Project: result.project_id,
      Pack: result.pack_id,
      文件路径: result.file_path,
      质量检查: result.quality_checks,
    });
    await loadOutputs();
  });

  document.getElementById("maintenanceReport").addEventListener("click", async () => {
    const result = await api("/api/maintenance/report");
    jsonBox(document.getElementById("maintenanceReportBox"), result);
  });

  document.getElementById("codexRepairTask").addEventListener("click", async () => {
    const result = await api("/api/maintenance/codex-task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "用户从维护中心生成修复任务" }),
    });
    jsonBox(document.getElementById("maintenanceReportBox"), result);
  });

  const requestedPage = new URLSearchParams(window.location.search).get("page");
  if (requestedPage && pageBranch[resolvePage(requestedPage)]) {
    switchPage(requestedPage);
  }

  try {
    const health = await api("/api/health");
    document.getElementById("healthText").textContent = labelFrom(statusLabel, health.status);
  } catch (err) {
    document.getElementById("healthText").textContent = err.message;
  }
  updatePolicyBox();
  await loadDashboard();
}

init().catch((err) => {
  document.body.insertAdjacentHTML("afterbegin", `<div class="fatal">${esc(err.message)}</div>`);
});
