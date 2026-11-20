(() => {
  const AUTH_KEY = "nexusops.auth";
  const auth = (() => {
    try { return JSON.parse(sessionStorage.getItem(AUTH_KEY) || "null"); }
    catch { return null; }
  })();
  if (!auth?.token) {
    window.location.replace("/");
    return;
  }

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { profile: null, incidents: [], currentIncident: null, currentAnalysis: null, dashboard: null, activeView: "overview" };

  const pageMeta = {
    overview: ["OPERATIONS OVERVIEW", "运行总览"],
    incidents: ["INCIDENT MANAGEMENT", "故障事件"],
    alerts: ["MONITORING INTEGRATION", "监控告警"],
    approvals: ["HUMAN-IN-THE-LOOP", "人工审批"],
    tools: ["TOOL GOVERNANCE", "工具与审计"],
    knowledge: ["OPERATIONAL KNOWLEDGE", "运维知识库"],
    system: ["RUNTIME OBSERVABILITY", "Agent 与模型"]
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
  function fmtDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
  }
  function fmtShort(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  }
  function roleNames() { return (state.profile?.roles || []).map(item => item.authority || item); }
  function isAdmin() { return roleNames().includes("ROLE_ADMIN"); }
  function isReviewer() { return isAdmin() || roleNames().includes("ROLE_ONCALL"); }

  // Keep the navigation in sync with the server-verified profile.
  // The CSS [hidden] rule is required because .nav-item uses display:flex.
  function applyRoleNavigation() {
    const reviewer = isReviewer();
    const admin = isAdmin();
    $$(".reviewer-only").forEach(el => { el.hidden = !reviewer; });
    $$(".admin-only").forEach(el => { el.hidden = !admin; });
  }
  function setPill(el, text, tone = "neutral") {
    if (!el) return;
    el.textContent = text;
    el.className = `pill pill-${tone}`;
  }
  function showToast(message, tone = "ok") {
    const toast = $("#toast");
    toast.textContent = message;
    toast.className = `toast show ${tone}`;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => { toast.className = "toast"; }, 3200);
  }
  function showError(message) {
    const box = $("#globalError");
    box.textContent = message;
    box.hidden = false;
  }
  function clearError() { $("#globalError").hidden = true; }

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}), Authorization: `Basic ${auth.token}` };
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        message = body.detail || body.message || JSON.stringify(body);
      } catch {
        const body = await response.text();
        if (body) message = body;
      }
      throw new Error(message);
    }
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? response.json() : response;
  }

  function setActiveView(view) {
    if (!pageMeta[view]) view = "overview";
    if ((view === "approvals" || view === "tools") && !isReviewer()) view = "overview";
    if (view === "knowledge" && !isAdmin()) view = "overview";
    if ($(`.nav-item[data-view="${view}"]`)?.hidden) view = "overview";
    state.activeView = view;
    $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === view));
    $$(".view").forEach(item => item.classList.toggle("active", item.id === `view-${view}`));
    $("#pageEyebrow").textContent = pageMeta[view][0];
    $("#pageTitle").textContent = pageMeta[view][1];
    if (view === "incidents") loadIncidents();
    if (view === "alerts") loadAlerts();
    if (view === "approvals") loadApprovals();
    if (view === "tools") loadTools();
    if (view === "knowledge") loadKnowledge();
    if (view === "system") loadSystem();
  }

  async function loadProfile() {
    state.profile = await api("/api/profile");
    $("#activeUser").textContent = state.profile.displayName || state.profile.username;
    $("#activeRole").textContent = roleNames().join(" · ");
    $("#userAvatar").textContent = (state.profile.displayName || state.profile.username || "N").slice(0, 1).toUpperCase();
    applyRoleNavigation();
  }

  async function loadHealth() {
    try {
      const result = await fetch("/actuator/health").then(r => r.json());
      const ok = result.status === "UP";
      $("#sidebarHealth").textContent = ok ? "服务运行正常" : "服务异常";
    } catch {
      $("#sidebarHealth").textContent = "无法连接";
    }
  }

  async function loadDashboard() {
    try {
      const data = await api("/api/dashboard/summary");
      state.dashboard = data;
      const m = data.metrics || {};
      $("#metricTotal").textContent = m.totalIncidents ?? 0;
      $("#metricActive").textContent = m.activeIncidents ?? 0;
      $("#metricCritical").textContent = m.criticalIncidents ?? 0;
      $("#metricApprovals").textContent = m.pendingApprovals ?? 0;
      $("#metricAlerts").textContent = m.firingAlerts ?? 0;
      $("#metricJobs").textContent = m.pendingToolJobs ?? 0;
      $("#navApprovalCount").textContent = m.pendingApprovals ?? 0;
      $("#navIncidentCount").textContent = m.activeIncidents ?? 0;
      renderTrend(data.dailyTrend || []);
      renderSeverity(data.bySeverity || {});
      renderRecent(data.recentIncidents || []);
    } catch (error) {
      showError(`总览数据加载失败：${error.message}`);
    }
  }

  function renderTrend(items) {
    const root = $("#dailyTrend");
    if (!items.length) { root.innerHTML = '<div class="empty-state">暂无趋势数据</div>'; return; }
    const max = Math.max(1, ...items.map(item => Number(item.count || 0)));
    const total = items.reduce((sum, item) => sum + Number(item.count || 0), 0);
    $("#trendTotal").textContent = `7 日新增 ${total} 起`;
    root.innerHTML = items.map(item => {
      const count = Number(item.count || 0);
      const height = Math.max(4, Math.round(count / max * 100));
      return `<div class="bar-col"><span class="bar-count">${count}</span><div class="bar-track"><i style="height:${height}%"></i></div><span class="bar-label">${escapeHtml(item.date.slice(5))}</span></div>`;
    }).join("");
  }

  function renderSeverity(counts) {
    const root = $("#severityDistribution");
    const items = ["P0", "P1", "P2", "P3"].map(key => ({ key, count: Number(counts[key] || 0) }));
    const total = Math.max(1, items.reduce((sum, item) => sum + item.count, 0));
    root.innerHTML = items.map(item => `<div class="severity-row"><span class="severity-badge ${item.key.toLowerCase()}">${item.key}</span><div class="severity-track"><i class="${item.key.toLowerCase()}" style="width:${Math.round(item.count / total * 100)}%"></i></div><b>${item.count}</b></div>`).join("");
  }

  function statusLabel(status) {
    const map = { NEW: "新建", TRIAGED: "已分诊", ACKNOWLEDGED: "已确认", INVESTIGATING: "调查中", MITIGATED: "已缓解", RESOLVED: "已解决", CLOSED: "已关闭" };
    return map[status] || status || "未知";
  }
  function severityBadge(value) { return `<span class="severity-badge ${(value || "p2").toLowerCase()}">${escapeHtml(value || "P2")}</span>`; }
  function statusBadge(value) { return `<span class="status-badge ${(value || "new").toLowerCase()}">${escapeHtml(statusLabel(value))}</span>`; }
  function renderIncidentTable(items, target = "#incidentTable") {
    const root = $(target);
    if (!items.length) { root.innerHTML = '<div class="empty-state">暂无符合条件的故障事件</div>'; return; }
    root.innerHTML = `<table class="data-table"><thead><tr><th>事件</th><th>服务 / 环境</th><th>优先级</th><th>状态</th><th>负责人</th><th>创建时间</th><th></th></tr></thead><tbody>${items.map(row => `<tr><td><button class="table-link" data-open-incident="${row.id}"><b>${escapeHtml(row.incidentKey)}</b><small>${escapeHtml(row.title)}</small></button></td><td><b>${escapeHtml(row.serviceName)}</b><small>${escapeHtml(row.environment)}</small></td><td>${severityBadge(row.severity)}</td><td>${statusBadge(row.status)}</td><td>${escapeHtml(row.assignee || "unassigned")}</td><td>${fmtDate(row.createdAt)}</td><td><button class="row-open" data-open-incident="${row.id}">详情 →</button></td></tr>`).join("")}</tbody></table>`;
  }
  function renderRecent(items) {
    const root = $("#recentIncidents");
    if (!items.length) { root.innerHTML = '<div class="empty-state">还没有故障事件，创建第一条事件开始使用。</div>'; return; }
    root.innerHTML = `<table class="data-table"><thead><tr><th>事件</th><th>服务</th><th>等级</th><th>状态</th><th>时间</th><th></th></tr></thead><tbody>${items.map(row => `<tr><td><button class="table-link" data-open-incident="${row.id}"><b>${escapeHtml(row.incidentKey)}</b><small>${escapeHtml(row.title)}</small></button></td><td>${escapeHtml(row.serviceName)}</td><td>${severityBadge(row.severity)}</td><td>${statusBadge(row.status)}</td><td>${fmtDate(row.createdAt)}</td><td><button class="row-open" data-open-incident="${row.id}">打开 →</button></td></tr>`).join("")}</tbody></table>`;
  }

  async function loadIncidents() {
    const params = new URLSearchParams({ page: "1", pageSize: "100" });
    const keyword = $("#incidentSearch")?.value.trim();
    const status = $("#incidentStatusFilter")?.value;
    const severity = $("#incidentSeverityFilter")?.value;
    if (keyword) params.set("keyword", keyword);
    if (status) params.set("status", status);
    if (severity) params.set("severity", severity);
    try {
      const data = await api(`/api/incidents?${params.toString()}`);
      state.incidents = data.items || [];
      renderIncidentTable(state.incidents);
      $("#incidentTableFoot").textContent = `共 ${data.total || 0} 条记录 · 第 ${data.page || 1} 页`;
    } catch (error) {
      $("#incidentTable").innerHTML = `<div class="empty-state error-text">加载失败：${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadAlerts() {
    try {
      const rows = await api("/api/alerts?limit=200");
      const root = $("#alertTable");
      if (!rows.length) { root.innerHTML = '<div class="empty-state">暂未接收到监控告警。可按上方地址配置 Alertmanager Webhook。</div>'; return; }
      root.innerHTML = `<table class="data-table"><thead><tr><th>告警</th><th>服务</th><th>等级</th><th>状态</th><th>关联事件</th><th>触发次数</th><th>最近触发</th></tr></thead><tbody>${rows.map(row => `<tr><td><b>${escapeHtml(row.alertName)}</b><small>${escapeHtml(row.source)}</small></td><td>${escapeHtml(row.serviceName)}<small>${escapeHtml(row.environment)}</small></td><td>${severityBadge(row.severity)}</td><td><span class="status-badge ${row.status === "FIRING" ? "investigating" : "resolved"}">${escapeHtml(row.status)}</span></td><td>${row.incidentId ? `<button class="table-link" data-open-incident="${row.incidentId}">INC #${row.incidentId} →</button>` : "—"}</td><td>${row.occurrenceCount}</td><td>${fmtDate(row.lastSeenAt)}</td></tr>`).join("")}</tbody></table>`;
    } catch (error) {
      $("#alertTable").innerHTML = `<div class="empty-state error-text">加载失败：${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadApprovals() {
    if (!isReviewer()) return;
    try {
      const rows = await api("/api/admin/approvals?limit=200");
      const pending = rows.filter(row => row.status === "PENDING").length;
      $("#navApprovalCount").textContent = pending;
      const root = $("#approvalTable");
      if (!rows.length) { root.innerHTML = '<div class="empty-state">暂无审批申请。</div>'; return; }
      root.innerHTML = `<div class="approval-list">${rows.map(row => `<article class="approval-card"><div class="approval-main"><div class="approval-top"><b>${escapeHtml(row.actionKey)}</b><span class="status-badge ${row.status.toLowerCase()}">${escapeHtml(row.status)}</span></div><div class="approval-target">${escapeHtml(row.target)} · Incident #${row.incidentId}</div><p>${escapeHtml(row.justification)}</p><small>申请人：${escapeHtml(row.requestedBy)} · ${fmtDate(row.createdAt)}</small>${row.reviewNote ? `<div class="review-note">审批意见：${escapeHtml(row.reviewNote)}</div>` : ""}</div>${row.status === "PENDING" ? `<div class="approval-actions"><button class="btn btn-soft" data-review-approval="${row.id}" data-approved="false">拒绝</button><button class="btn btn-primary" data-review-approval="${row.id}" data-approved="true">批准记录</button></div>` : ""}</article>`).join("")}</div>`;
    } catch (error) {
      $("#approvalTable").innerHTML = `<div class="empty-state error-text">加载失败：${escapeHtml(error.message)}</div>`;
    }
  }

  async function reviewApproval(id, approved) {
    const reviewNote = window.prompt(approved ? "审批意见（可选）" : "拒绝原因（建议填写）", "");
    if (reviewNote === null) return;
    try {
      await api(`/api/admin/approvals/${id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved, reviewNote }) });
      showToast(approved ? "审批记录已批准；不会自动执行生产变更。" : "审批申请已拒绝。", approved ? "ok" : "warn");
      await Promise.all([loadApprovals(), loadDashboard()]);
    } catch (error) { showToast(`审批失败：${error.message}`, "danger"); }
  }

  async function loadTools() {
    if (!isReviewer()) return;
    try {
      const [jobs, audits, notifications] = await Promise.all([
        api("/api/admin/tool-jobs?limit=200"),
        api("/api/admin/tool-audits?limit=200"),
        api("/api/admin/notifications?limit=100")
      ]);
      renderJobs(jobs);
      renderAudits(audits);
      renderNotifications(notifications);
    } catch (error) {
      $("#toolJobTable").innerHTML = `<div class="empty-state error-text">加载失败：${escapeHtml(error.message)}</div>`;
    }
  }
  function renderJobs(rows) {
    const root = $("#toolJobTable");
    if (!rows.length) { root.innerHTML = '<div class="empty-state">暂无工具任务。</div>'; return; }
    root.innerHTML = `<table class="data-table"><thead><tr><th>任务</th><th>Incident</th><th>状态</th><th>尝试</th><th>创建时间</th><th>错误 / 结果</th><th></th></tr></thead><tbody>${rows.map(row => `<tr><td><b>${escapeHtml(row.toolName)}</b><small>#${row.id}</small></td><td>${row.incidentId}</td><td><span class="status-badge ${row.status.toLowerCase()}">${escapeHtml(row.status)}</span></td><td>${row.attempts}/${row.maxAttempts}</td><td>${fmtDate(row.createdAt)}</td><td><small>${escapeHtml(row.lastError || JSON.stringify(row.result || {}))}</small>${row.result?.downloadUrl ? `<a class="row-open" href="${escapeHtml(row.result.downloadUrl)}" target="_blank" rel="noreferrer">下载台账</a>` : ""}</td><td>${row.status === "DEAD" || row.status === "FAILED" ? `<button class="row-open" data-retry-job="${row.id}">重试</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
  }
  function renderAudits(rows) {
    const root = $("#toolAuditTable");
    if (!rows.length) { root.innerHTML = '<div class="empty-state">暂无审计记录。</div>'; return; }
    root.innerHTML = `<table class="data-table"><thead><tr><th>工具</th><th>决策</th><th>状态</th><th>操作人</th><th>原因</th><th>时间</th></tr></thead><tbody>${rows.map(row => `<tr><td>${escapeHtml(row.toolName)}</td><td>${escapeHtml(row.decision)}</td><td><span class="status-badge ${row.status.toLowerCase()}">${escapeHtml(row.status)}</span></td><td>${escapeHtml(row.actor || "system")}</td><td><small>${escapeHtml(row.reason)}</small></td><td>${fmtDate(row.createdAt)}</td></tr>`).join("")}</tbody></table>`;
  }
  function renderNotifications(rows) {
    const root = $("#notificationTable");
    if (!rows.length) { root.innerHTML = '<div class="empty-state">暂无通知发送记录。</div>'; return; }
    root.innerHTML = `<table class="data-table"><thead><tr><th>事件</th><th>渠道</th><th>接收方</th><th>状态</th><th>主题</th><th>时间</th></tr></thead><tbody>${rows.map(row => `<tr><td>#${row.incidentId}</td><td>${escapeHtml(row.channel)}</td><td>${escapeHtml(row.recipient)}</td><td>${escapeHtml(row.status)}</td><td>${escapeHtml(row.subject)}</td><td>${fmtDate(row.createdAt)}</td></tr>`).join("")}</tbody></table>`;
  }

  async function loadKnowledge() {
    if (!isAdmin()) return;
    try {
      const [status, manifest, skills] = await Promise.all([
        api("/api/admin/knowledge/status"),
        api("/api/admin/ops/knowledge-manifest"),
        api("/api/ops/skills")
      ]);
      const items = [
        ["MySQL 知识分块", status.databaseChunks ?? 0],
        ["Chroma 向量分块", status.vectorChunks ?? "未建立"],
        ["向量索引状态", status.vectorIndexReady ? "READY" : "待构建"],
        ["检索模式", status.vectorAvailable ? "Hybrid RAG" : "BM25 Fallback"]
      ];
      $("#knowledgeStatusGrid").innerHTML = items.map(([label, value]) => `<div class="metric-card compact-metric"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${escapeHtml(value)}</div></div>`).join("");
      $("#knowledgeSourceCount").textContent = `${manifest.length} 份内置 Runbook`;
      const root = $("#knowledgeSources");
      if (!manifest.length) root.innerHTML = '<div class="empty-state">暂无知识来源。</div>';
      else root.innerHTML = `<table class="data-table"><thead><tr><th>知识来源</th><th>来源类型</th><th>状态管理</th><th>版本 Hash</th><th>分块数</th><th>复核时间</th></tr></thead><tbody>${manifest.map(row => `<tr><td><b>${escapeHtml(row.name)}</b><small>${escapeHtml(row.sourceKey)}</small></td><td><span class="tag">${row.origin === "BUILT_IN_RUNBOOK" ? "内置 Runbook" : "上传 / 管理员维护"}</span></td><td><select class="source-status-select" data-source-status="${row.id}"><option value="ACTIVE" ${row.status === "ACTIVE" ? "selected" : ""}>ACTIVE</option><option value="STALE" ${row.status === "STALE" ? "selected" : ""}>STALE</option><option value="ARCHIVED" ${row.status === "ARCHIVED" ? "selected" : ""}>ARCHIVED</option></select></td><td><code>${escapeHtml((row.versionHash || "").slice(0, 12))}…</code></td><td>${row.chunkCount}</td><td>${fmtDate(row.reviewedAt)}</td></tr>`).join("")}</tbody></table>`;
      $("#opsSkills").innerHTML = skills.map(item => `<article class="skill-card"><b>${escapeHtml(item.name)}</b><p>${escapeHtml(item.description)}</p><small>${escapeHtml(item.categories.join(" · "))}</small></article>`).join("");
      if (status.vectorError) $("#knowledgeUploadState").textContent = `向量服务提示：${status.vectorError}`;
    } catch (error) {
      $("#knowledgeSources").innerHTML = `<div class="empty-state error-text">知识库加载失败：${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadSystem() {
    const overviewRoot = $("#systemOverview");
    const agentRoot = $("#agentList");
    const routerRoot = $("#modelRouterDetails");
    try {
      const data = await api("/api/agent/status");
      const router = data.modelRouter || {};
      const provider = data.provider || "unknown";
      const model = data.model || "unknown";
      setPill($("#modelPill"), `${provider.toUpperCase()} · ${model}`, provider === "mock" ? "warn" : "ok");

      const overview = [
        ["Agent Framework", data.agentFramework?.active || "event_driven_multi_agent"],
        ["Runtime Harness", data.runtimeHarness?.name || "NexusOpsIncidentAgentRuntime"],
        ["Model Provider", provider],
        ["Model", model],
        ["Max Agent Rounds", data.loop?.maxSteps ?? "—"],
        ["Fallback Provider", router.fallbackProvider || "mock"]
      ];
      if (overviewRoot) {
        overviewRoot.innerHTML = overview.map(([label, value]) => `<div class="system-card"><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></div>`).join("");
      }

      const agents = Array.isArray(data.agents) ? data.agents : [];
      if (agentRoot) {
        agentRoot.innerHTML = agents.length
          ? agents.map(agent => `<div class="agent-row"><span class="agent-status-dot"></span><div><b>${escapeHtml(agent.name)}</b><small>${escapeHtml(agent.description)}</small></div><span class="pill pill-ok">${escapeHtml(agent.status)}</span></div>`).join("")
          : '<div class="empty-state">当前没有可展示的专业 Agent。</div>';
      }
      if (routerRoot) routerRoot.textContent = JSON.stringify(router, null, 2);
    } catch (error) {
      const message = `运行状态读取失败：${escapeHtml(error.message)}`;
      if (overviewRoot) overviewRoot.innerHTML = `<div class="empty-state error-text">${message}</div>`;
      if (agentRoot) agentRoot.innerHTML = `<div class="empty-state error-text">${message}</div>`;
      if (routerRoot) routerRoot.textContent = message;
    }
  }

  const transitionMap = {
    NEW: ["TRIAGED"], TRIAGED: ["ACKNOWLEDGED", "INVESTIGATING"], ACKNOWLEDGED: ["INVESTIGATING"],
    INVESTIGATING: ["MITIGATED", "RESOLVED"], MITIGATED: ["INVESTIGATING", "RESOLVED"],
    RESOLVED: ["CLOSED", "INVESTIGATING"], CLOSED: []
  };

  async function openIncident(id) {
    try {
      const row = await api(`/api/incidents/${id}`);
      state.currentIncident = row;
      state.currentAnalysis = null;
      try { state.currentAnalysis = await api(`/api/incidents/${id}/analysis/latest`); } catch { /* no analysis yet */ }
      $("#detailEyebrow").textContent = `${row.incidentKey} · ${row.severity}`;
      $("#detailTitle").textContent = row.title;
      $("#detailSub").textContent = `${row.serviceName} · ${row.environment} · 创建于 ${fmtDate(row.createdAt)}`;
      renderIncidentDetail(row, state.currentAnalysis);
      $("#incidentDetailDialog").showModal();
    } catch (error) { showToast(`无法打开事件：${error.message}`, "danger"); }
  }

  function renderIncidentDetail(row, analysis) {
    const transitions = transitionMap[row.status] || [];
    const transitionHtml = transitions.length ? `<form id="detailTransitionForm" class="inline-form"><label>状态流转<select name="status">${transitions.map(value => `<option value="${value}">${statusLabel(value)}</option>`).join("")}</select></label><input name="reason" placeholder="流转原因（可选）"><button class="btn btn-soft" type="submit">更新状态</button></form>` : `<p class="muted">当前状态不可继续流转。</p>`;
    const notes = row.notes || [];
    const history = row.history || [];
    const analysisHtml = analysis ? renderAnalysisBlock(analysis) : '<div class="empty-inline">尚未进行多 Agent 研判。点击“运行智能研判”生成分析记录。</div>';
    $("#incidentDetailContent").innerHTML = `
      <div class="detail-badges">${severityBadge(row.severity)} ${statusBadge(row.status)} <span class="tag">${escapeHtml(row.category)}</span><span class="tag">来源 ${escapeHtml(row.source)}</span></div>
      <div class="detail-description">${escapeHtml(row.description)}</div>
      <div class="detail-facts"><div><small>负责人</small><b>${escapeHtml(row.assignee || "unassigned")}</b></div><div><small>影响范围</small><b>${escapeHtml(row.impactScope || "未填写")}</b></div><div><small>版本号</small><b>v${row.version}</b></div></div>
      <div class="detail-action-row"><button class="btn btn-primary" data-detail-action="analyze">✦ 运行智能研判</button><button class="btn btn-soft" data-detail-action="export">导出故障台账</button><button class="btn btn-soft" data-detail-action="notify">通知值班人员</button></div>
      <section class="detail-section"><div class="detail-section-head"><h3>多 Agent 研判结果</h3><span class="panel-note">${analysis ? fmtDate(analysis.createdAt) : "NOT RUN"}</span></div><div id="analysisBlock">${analysisHtml}</div></section>
      <section class="detail-section"><div class="detail-section-head"><h3>协同处置</h3></div>${transitionHtml}<form id="detailNoteForm" class="note-form"><label>新增处理备注<textarea name="content" rows="2" required placeholder="记录排查结果、观察到的指标或处置进展…"></textarea></label><div class="note-form-foot"><select name="noteType"><option>INVESTIGATION</option><option>COMMENT</option><option>MITIGATION</option><option>RESOLUTION</option></select><button class="btn btn-soft" type="submit">添加备注</button></div></form></section>
      <section class="detail-section"><div class="detail-section-head"><h3>申请生产变更审批</h3><span class="panel-note">MANUAL REVIEW</span></div><form id="detailApprovalForm" class="approval-request-form"><select name="actionKey"><option value="restart_service">重启服务</option><option value="rollback_deployment">回滚部署</option><option value="scale_service">扩缩容</option><option value="change_configuration">修改配置</option><option value="database_failover">数据库切主</option><option value="rotate_secret">轮换密钥</option><option value="other_change">其他变更</option></select><input name="target" value="${escapeHtml(row.serviceName)}" required placeholder="变更目标"><textarea name="justification" rows="2" minlength="8" required placeholder="填写变更原因、风险和验证计划"></textarea><button class="btn btn-soft" type="submit">提交人工审批</button></form><p class="safety-hint">审批通过不会自动执行任何生产命令。</p></section>
      <div class="detail-two-col"><section class="detail-section"><div class="detail-section-head"><h3>处理时间线</h3></div><div class="timeline">${history.length ? history.map(item => `<div class="timeline-item"><span></span><div><b>${escapeHtml(statusLabel(item.toStatus))}</b><small>${escapeHtml(item.actorName)} · ${fmtDate(item.createdAt)}</small><p>${escapeHtml(item.reason || "状态更新")}</p></div></div>`).join("") : '<div class="empty-inline">暂无状态记录</div>'}</div></section><section class="detail-section"><div class="detail-section-head"><h3>处理备注</h3><span class="panel-note">${notes.length} 条</span></div><div class="note-list">${notes.length ? [...notes].reverse().map(item => `<div class="note-item"><b>${escapeHtml(item.actorName)} <small>${escapeHtml(item.noteType)}</small></b><time>${fmtDate(item.createdAt)}</time><p>${escapeHtml(item.content)}</p></div>`).join("") : '<div class="empty-inline">暂无处理备注</div>'}</div></section></div>`;
  }

  function renderAnalysisBlock(data) {
    const hypotheses = data.resolutionPlan?.rootCauseHypotheses || [];
    const actions = data.resolutionPlan?.recommendedActions || [];
    const evidence = data.retrievedContext?.evidence || [];
    const guard = data.operationGuard || {};
    return `<div class="analysis-summary">${escapeHtml(data.summary || "研判完成")}</div><div class="analysis-grid"><div class="analysis-mini"><small>根因假设</small><b>${hypotheses.length}</b></div><div class="analysis-mini"><small>排查建议</small><b>${actions.length}</b></div><div class="analysis-mini"><small>知识证据</small><b>${evidence.length}</b></div><div class="analysis-mini"><small>安全审查</small><b>${escapeHtml(guard.decision || "—")}</b></div></div><div class="analysis-columns"><div><h4>待验证根因假设</h4>${hypotheses.length ? hypotheses.map(item => `<div class="analysis-item"><b>${escapeHtml(item.cause)}</b><p>${escapeHtml(item.rationale)}</p><small>置信度 ${Math.round((item.confidence || 0) * 100)}% · ${escapeHtml(item.validation || "")}</small></div>`).join("") : '<div class="empty-inline">暂无假设</div>'}</div><div><h4>建议排查步骤</h4>${actions.length ? actions.map(item => `<div class="analysis-item"><b>${item.step}. ${escapeHtml(item.action)}</b><small>${escapeHtml(item.priority)} · 风险 ${escapeHtml(item.riskLevel)}</small></div>`).join("") : '<div class="empty-inline">暂无建议</div>'}</div></div><details class="raw-details"><summary>查看完整 Agent 研判数据</summary><pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre></details>`;
  }

  async function analyzeCurrentIncident() {
    const row = state.currentIncident;
    if (!row) return;
    const button = $('[data-detail-action="analyze"]');
    if (button) { button.disabled = true; button.textContent = "研判运行中…"; }
    try {
      const analysis = await api(`/api/incidents/${row.id}/analyze`, { method: "POST" });
      state.currentAnalysis = analysis;
      renderIncidentDetail(state.currentIncident, analysis);
      showToast("多 Agent 故障研判已完成。", "ok");
    } catch (error) { showToast(`研判失败：${error.message}`, "danger"); }
    finally {
      const refreshed = $('[data-detail-action="analyze"]');
      if (refreshed) { refreshed.disabled = false; refreshed.textContent = "✦ 运行智能研判"; }
    }
  }

  async function enqueueTool(toolName, payload = {}) {
    const row = state.currentIncident;
    if (!row) return;
    try {
      await api(`/api/incidents/${row.id}/tools`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ toolName, payload })
      });
      showToast("工具任务已进入异步队列。", "ok");
      if (isReviewer()) loadTools();
      loadDashboard();
    } catch (error) { showToast(`任务提交失败：${error.message}`, "danger"); }
  }

  async function createIncident(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form).entries());
    const stateEl = $("#createIncidentState");
    stateEl.textContent = "正在创建…";
    try {
      const row = await api("/api/incidents", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values)
      });
      form.reset();
      $("#createIncidentDialog").close();
      showToast(`故障事件 ${row.incidentKey} 已创建。`, "ok");
      await Promise.all([loadDashboard(), loadIncidents()]);
      openIncident(row.id);
    } catch (error) { stateEl.textContent = `创建失败：${error.message}`; }
  }

  async function handleDetailSubmit(event) {
    const form = event.target;
    const row = state.currentIncident;
    if (!row) return;
    if (form.id === "detailNoteForm") {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      try {
        await api(`/api/incidents/${row.id}/notes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
        await openIncident(row.id);
        showToast("处理备注已添加。", "ok");
      } catch (error) { showToast(`备注添加失败：${error.message}`, "danger"); }
    } else if (form.id === "detailTransitionForm") {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      try {
        await api(`/api/incidents/${row.id}/transition`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: values.status, reason: values.reason || "", expectedVersion: row.version }) });
        await Promise.all([openIncident(row.id), loadDashboard(), loadIncidents()]);
        showToast("故障状态已更新。", "ok");
      } catch (error) { showToast(`状态更新失败：${error.message}`, "danger"); }
    } else if (form.id === "detailApprovalForm") {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      try {
        await api(`/api/incidents/${row.id}/approvals`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
        await openIncident(row.id);
        showToast("审批申请已提交，等待授权人员审核。", "ok");
        if (isReviewer()) loadApprovals();
      } catch (error) { showToast(`审批申请失败：${error.message}`, "danger"); }
    }
  }

  async function retryJob(id) {
    if (!window.confirm("确认重新排队执行该工具任务？")) return;
    try {
      await api(`/api/admin/tool-jobs/${id}/retry`, { method: "POST" });
      showToast("任务已重新进入队列。", "ok");
      loadTools();
    } catch (error) { showToast(`重试失败：${error.message}`, "danger"); }
  }

  async function uploadKnowledge(event) {
    event.preventDefault();
    const file = $("#knowledgeFile").files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    $("#knowledgeUploadState").textContent = "正在解析并写入知识库…";
    try {
      const result = await api("/api/admin/knowledge/file", { method: "POST", body: form });
      $("#knowledgeUploadState").textContent = `入库成功：${result.source}，新增 ${result.chunks} 个分块。`;
      $("#knowledgeFile").value = "";
      await loadKnowledge();
      showToast("知识文档已入库。", "ok");
    } catch (error) { $("#knowledgeUploadState").textContent = `上传失败：${error.message}`; }
  }

  async function runKnowledgeAction(kind) {
    const endpoint = kind === "rebuild" ? "/api/admin/knowledge/rebuild-vector" : "/api/admin/knowledge/backup";
    const button = kind === "rebuild" ? $("#rebuildVector") : $("#backupVector");
    button.disabled = true;
    try {
      const result = await api(endpoint, { method: "POST" });
      showToast(kind === "rebuild" ? `向量索引重建完成：${result.indexedChunks} 个分块。` : `知识库快照已生成：${result.snapshot}`, "ok");
      loadKnowledge();
    } catch (error) { showToast(`操作失败：${error.message}`, "danger"); }
    finally { button.disabled = false; }
  }

  function logout() {
    sessionStorage.removeItem(AUTH_KEY);
    window.location.replace("/");
  }

  async function refreshAll() {
    clearError();
    await Promise.all([loadDashboard(), loadIncidents(), loadAlerts(), loadHealth()]);
    if (isReviewer()) await loadApprovals();
    if (isAdmin()) await loadKnowledge();
    await loadSystem();
    showToast("工作台数据已刷新。", "ok");
  }

  // Navigation and global event delegation.
  $$(".nav-item").forEach(button => button.addEventListener("click", () => setActiveView(button.dataset.view)));
  $$('[data-go-view]').forEach(button => button.addEventListener("click", () => setActiveView(button.dataset.goView)));
  $("#logoutButton").addEventListener("click", logout);
  $("#refreshAll").addEventListener("click", refreshAll);
  $("#refreshAlerts").addEventListener("click", loadAlerts);
  $("#refreshApprovals")?.addEventListener("click", loadApprovals);
  $("#refreshJobs")?.addEventListener("click", loadTools);
  $("#refreshSystem")?.addEventListener("click", loadSystem);
  $("#applyIncidentFilters").addEventListener("click", loadIncidents);
  $("#incidentSearch").addEventListener("keydown", event => { if (event.key === "Enter") loadIncidents(); });
  $("#createIncidentButton").addEventListener("click", () => $("#createIncidentDialog").showModal());
  $("#quickCreateIncident").addEventListener("click", () => $("#createIncidentDialog").showModal());
  $("#createIncidentForm").addEventListener("submit", createIncident);
  $("#knowledgeUploadForm").addEventListener("submit", uploadKnowledge);
  $("#knowledgeSources").addEventListener("change", async event => {
    const select = event.target.closest("[data-source-status]");
    if (!select) return;
    const sourceId = Number(select.dataset.sourceStatus);
    const status = select.value;
    select.disabled = true;
    try {
      await api(`/api/admin/knowledge-sources/${sourceId}/status`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status })
      });
      showToast(`知识来源状态已更新为 ${status}。`, "ok");
      await loadKnowledge();
    } catch (error) {
      showToast(`知识来源状态更新失败：${error.message}`, "danger");
      await loadKnowledge();
    } finally { select.disabled = false; }
  });
  $("#rebuildVector").addEventListener("click", () => runKnowledgeAction("rebuild"));
  $("#backupVector").addEventListener("click", () => runKnowledgeAction("backup"));
  $("#incidentDetailContent").addEventListener("submit", handleDetailSubmit);
  $("#incidentDetailContent").addEventListener("click", event => {
    const action = event.target.closest("[data-detail-action]")?.dataset.detailAction;
    if (action === "analyze") analyzeCurrentIncident();
    if (action === "export") enqueueTool("export_incident_ledger");
    if (action === "notify") enqueueTool("send_oncall_notification");
  });
  document.body.addEventListener("click", event => {
    const incidentButton = event.target.closest("[data-open-incident]");
    if (incidentButton) openIncident(Number(incidentButton.dataset.openIncident));
    const approvalButton = event.target.closest("[data-review-approval]");
    if (approvalButton) reviewApproval(Number(approvalButton.dataset.reviewApproval), approvalButton.dataset.approved === "true");
    const retryButton = event.target.closest("[data-retry-job]");
    if (retryButton) retryJob(Number(retryButton.dataset.retryJob));
    const closeButton = event.target.closest("[data-close-dialog]");
    if (closeButton) $(`#${closeButton.dataset.closeDialog}`).close();
  });

  (async () => {
    try {
      await loadProfile();
      await loadHealth();
      await Promise.all([loadDashboard(), loadIncidents(), loadAlerts(), loadSystem()]);
      if (isReviewer()) loadApprovals();
      if (isAdmin()) loadKnowledge();
    } catch (error) {
      if (String(error.message).includes("401")) logout();
      else showError(`工作台初始化失败：${error.message}`);
    }
  })();
})();
