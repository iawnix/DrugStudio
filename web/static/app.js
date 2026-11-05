/* DrugStudio workbench: scientific data comes from the curated /api/state snapshot. */
(function () {
  "use strict";

  const app = { state: null, events: [], eventSource: null, proteinViewer: null, proteinStyle: "cartoon", ligandViewer: null, ligandKey: null, selectedPocket: null, structureArtifact: null, selectedStage: null, pricing: {}, provenance: null, workflowCanvas: { x: 0, y: 0, scale: 1, ready: false, pointer: null, moved: false }, projectId: ".", fileDirectory: "", filePath: "", terminalCwd: "", terminalSession: null, terminalCursor: 0, terminalPollTimer: null, terminalInputBuffer: "", terminalInputTimer: null, terminal: null, terminalFit: null, chatHistory: [], chatSessions: [], chatId: null, chatBusy: false, chatFilter: "active", modelConfig: null, codeEditor: null };
  const editorModeLoads = new Map();
  const editorModes = {
    py: ["python", "python", "Python"], pyi: ["python", "python", "Python"],
    js: ["javascript", "javascript", "JavaScript"], mjs: ["javascript", "javascript", "JavaScript"], cjs: ["javascript", "javascript", "JavaScript"],
    jsx: ["jsx", "jsx", "JSX"], ts: ["javascript", "javascript", "TypeScript"], tsx: ["jsx", "jsx", "TSX"],
    json: ["javascript", "application/json", "JSON"], ipynb: ["javascript", "application/json", "Jupyter JSON"],
    html: ["htmlmixed", "htmlmixed", "HTML"], htm: ["htmlmixed", "htmlmixed", "HTML"],
    xml: ["xml", "xml", "XML"], svg: ["xml", "xml", "SVG"], css: ["css", "css", "CSS"], scss: ["css", "css", "SCSS"],
    sh: ["shell", "shell", "Shell"], bash: ["shell", "shell", "Bash"], zsh: ["shell", "shell", "Shell"],
    yml: ["yaml", "yaml", "YAML"], yaml: ["yaml", "yaml", "YAML"], md: ["markdown", "markdown", "Markdown"], markdown: ["markdown", "markdown", "Markdown"],
    sql: ["sql", "text/x-sql", "SQL"], r: ["r", "r", "R"], go: ["go", "go", "Go"], rs: ["rust", "rust", "Rust"],
    c: ["clike", "text/x-csrc", "C"], h: ["clike", "text/x-csrc", "C/C++"], cc: ["clike", "text/x-c++src", "C++"],
    cpp: ["clike", "text/x-c++src", "C++"], cxx: ["clike", "text/x-c++src", "C++"], java: ["clike", "text/x-java", "Java"],
    kt: ["clike", "text/x-kotlin", "Kotlin"], php: ["php", "application/x-httpd-php", "PHP"], rb: ["ruby", "ruby", "Ruby"],
    lua: ["lua", "lua", "Lua"], pl: ["perl", "perl", "Perl"], dockerfile: ["dockerfile", "dockerfile", "Dockerfile"],
    toml: ["toml", "toml", "TOML"], ini: ["properties", "text/x-properties", "INI"], conf: ["properties", "text/x-properties", "Config"]
  };
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
  const fmt = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
  const fileUrl = (path) => `/api/file?path=${encodeURIComponent(path)}`;

  async function bootWorkspace() {
    try {
      const auth = await fetch("/api/auth", { cache: "no-store" }).then((response) => response.json());
      if (auth.required) {
        if (!auth.authorized) {
          $("auth-overlay").hidden = false;
          return;
        }
      }
      initializeWorkspace();
    } catch (error) { toast(error.message || "Unable to connect to workbench", true); }
  }

  async function authenticate(token) {
    const response = await fetch("/api/auth", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
    if (!response.ok) return false;
    $("auth-overlay").hidden = true;
    return true;
  }

  function initializeWorkspace() {
    initializeCodeEditor();
    fetchState();
    fetchProjects();
    fetchModelConfig();
    renderChat();
  }

  function initializeCodeEditor() {
    if (!window.CodeMirror || app.codeEditor) return;
    app.codeEditor = window.CodeMirror.fromTextArea($("file-editor"), {
      lineNumbers: true, lineWrapping: false, indentUnit: 2, tabSize: 2, theme: "default",
      mode: "null", extraKeys: {
        "Ctrl-S": () => saveProjectFile(), "Cmd-S": () => saveProjectFile(),
        Tab: (editor) => editor.replaceSelection("  ")
      }
    });
    app.codeEditor.setSize("100%", "100%");
  }

  function loadEditorModeScript(mode) {
    if (editorModeLoads.has(mode)) return editorModeLoads.get(mode);
    const promise = new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/" + mode + "/" + mode + ".min.js";
      script.onload = () => resolve(true);
      script.onerror = () => resolve(false);
      document.head.appendChild(script);
    });
    editorModeLoads.set(mode, promise);
    return promise;
  }

  async function setEditorMode(path) {
    if (!app.codeEditor) return;
    const ext = path.toLowerCase().split("/").pop().split(".").pop();
    const [mode, mime, label] = editorModes[ext] || ["", "null", "Plain text"];
    $("editor-language").textContent = label;
    if (!mode) {
      app.codeEditor.setOption("mode", "null");
      return;
    }
    const dependencies = mode === "htmlmixed" ? ["xml", "javascript", "css", "htmlmixed"] : mode === "jsx" ? ["javascript", "jsx"] : [mode];
    const loaded = await Promise.all(dependencies.map(loadEditorModeScript));
    if (app.filePath === path) app.codeEditor.setOption("mode", loaded.every(Boolean) ? mime : "null");
  }

  async function fetchModelConfig() {
    try {
      const response = await fetch("/api/model-config", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.modelConfig = data;
      $("model-base-url").value = data.base_url || "";
      if (!localStorage.getItem("dock-agent-chat-model")) $("chat-model").value = data.model || "gpt-4.1-mini";
      setModelConfigStatus(data.api_key_configured, data.base_url);
    } catch (_) {
      setModelConfigStatus(Boolean(app.state?.chat_available), app.state?.model?.base_url || "");
    }
  }

  function setModelConfigStatus(configured, baseUrl = "") {
    $("model-config-dot").classList.toggle("offline", !configured);
    $("model-config-status").textContent = configured
      ? `API Key 已配置${baseUrl ? ` · ${baseUrl}` : ""}`
      : "尚未配置 API Key";
  }

  async function saveModelConfig(event) {
    event.preventDefault();
    const button = $("save-model-settings");
    button.disabled = true;
    try {
      const response = await fetch("/api/model-config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: $("model-base-url").value.trim(),
          model: $("chat-model").value.trim(),
          api_key: $("model-api-key").value,
          clear_api_key: $("clear-model-api-key").checked
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.modelConfig = data;
      $("model-api-key").value = "";
      $("clear-model-api-key").checked = false;
      localStorage.setItem("dock-agent-chat-model", data.model);
      if (app.state) app.state.chat_available = data.api_key_configured;
      setModelConfigStatus(data.api_key_configured, data.base_url);
      renderChatStatus();
      toast("模型连接设置已保存");
      $("model-settings-dialog").close();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function fetchState() {
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (!response.ok) throw new Error((await response.json()).error || response.statusText);
      app.state = await response.json();
      setConnection(true);
      renderAll();
      connectEvents();
      fetchRuns();
    } catch (error) {
      setConnection(false);
      toast(error.message || "Unable to load run data", true);
    }
  }

  function setConnection(online) {
    $("connection-dot").classList.toggle("offline", !online);
    $("connection-label").textContent = online ? "Live" : "Offline";
  }

  function artifactsByKind(kind) {
    if (!app.state) return [];
    return Object.values(app.state.artifacts || {}).flat().filter((item) => item.kind === kind);
  }

  function renderAll() {
    const state = app.state;
    renderChatStatus();
    if (state.model_config) {
      app.modelConfig = state.model_config;
      if (!$("model-settings-dialog").open) $("model-base-url").value = state.model_config.base_url || "";
      setModelConfigStatus(state.model_config.api_key_configured, state.model_config.base_url);
    }
    $("workflow-pause").disabled = !state.server?.workflow_running;
    $("workflow-cancel").disabled = !state.server?.workflow_running;
    $("workflow-continue").disabled = state.server?.workflow_running || ["cancelled", "succeeded"].includes(state.status);
    const runPill = $("run-state-pill");
    runPill.textContent = ({ succeeded: "已完成", failed: "失败", running: "运行中", paused: "已暂停", cancelled: "已取消", waiting_approval: "等待审核", waiting_input: "等待输入", waiting_execution: "等待执行" })[state.status] || (state.run_id ? state.status || "待运行" : "无运行");
    runPill.className = `run-state-pill ${state.status || "idle"}`;
    const modelInput = $("chat-model");
    if (document.activeElement !== modelInput) modelInput.value = localStorage.getItem("dock-agent-chat-model") || app.modelConfig?.model || state.model?.model || "gpt-4.1-mini";
    const temperature = localStorage.getItem("dock-agent-chat-temperature") || "0.2";
    $("chat-temperature").value = temperature;
    $("chat-temperature-value").value = temperature;
    renderWorkflow();
    renderStructureChoices();
    renderPoseChoices();
    renderPockets();
    renderLigandChoices();
    renderResults();
    renderReview();
    if (window.lucide) window.lucide.createIcons();
    if (!app.proteinViewer && document.querySelector("#view-structure.active")) loadProtein();
  }

  const workflowNodes = [
    ["structure", "结构获取", "database"], ["receptor", "受体准备", "shield-check"],
    ["ligand", "小分子准备", "atom"], ["pocket", "口袋发现", "target"],
    ["docking", "分子对接", "flask-conical"], ["assessment", "结果评估", "chart-no-axes-combined"]
  ];
  const toolStages = {
    retrieve_structure: "structure", prepare_receptor: "receptor", prepare_ligands: "ligand",
    identify_binding_sites: "pocket", run_docking: "docking", assess_results: "assessment"
  };
  const toolLabels = {
    inspect_run: "Inspect run", retrieve_structure: "Retrieve structure", prepare_receptor: "Prepare receptor",
    prepare_ligands: "Prepare ligands", identify_binding_sites: "Find binding sites",
    run_docking: "Run docking", assess_results: "Assess results"
  };

  const stageStatusLabels = {
    pending: "待执行", running: "运行中", succeeded: "已完成", completed: "已完成",
    failed: "失败", waiting_approval: "等待审核", waiting_input: "等待输入",
    waiting_execution: "等待执行", stale: "需要重跑"
  };

  function stageStatusLabel(status) { return stageStatusLabels[status] || String(status || "待执行").replaceAll("_", " "); }

  function stageProgress(stage, status) {
    const result = app.state?.stage_results?.[stage] || {};
    const metadata = result.metadata || {};
    const current = result.progress || metadata.progress || {};
    const done = current.completed ?? current.finished ?? current.processed;
    const total = current.total ?? current.count ?? current.total_items;
    if (Number.isFinite(Number(done)) && Number.isFinite(Number(total)) && Number(total) > 0) return `${Number(done)}/${Number(total)}`;
    const artifacts = (app.state?.artifacts?.[stage] || []).length;
    if (artifacts) return `${artifacts} 个产物`;
    return status === "succeeded" ? "已记录" : "";
  }

  function connectEvents() {
    if (app.eventSource) return;
    app.eventSource = new EventSource("/api/events?since=" + (app.events.at(-1)?.event_id || 0));
    app.eventSource.onopen = () => { $("event-live").classList.add("live-indicator"); };
    app.eventSource.onmessage = (message) => receiveEvent(message.data);
    ["run_started", "run_completed", "run_failed", "run_paused", "run_cancelled", "control_requested", "model_started", "model_finished", "tool_started", "tool_finished", "stage_started", "stage_completed", "stage_failed", "stage_progress", "waiting_approval", "artifact_published", "human_decision", "run_created", "run_branched", "pricing_updated"].forEach((type) => app.eventSource.addEventListener(type, (event) => receiveEvent(event.data)));
    app.eventSource.onerror = () => {
      $("event-live").classList.remove("live-indicator");
      app.eventSource.close(); app.eventSource = null;
      fetch("/api/auth", { cache: "no-store" }).then((response) => response.json()).then((auth) => {
        if (auth.required && !auth.authorized) { $("auth-overlay").hidden = false; return; }
        window.setTimeout(connectEvents, 3000);
      }).catch(() => window.setTimeout(connectEvents, 3000));
    };
  }

  function receiveEvent(raw) {
    try {
      const item = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (app.events.some((event) => event.event_id === item.event_id)) return;
      app.events.push(item); app.events = app.events.slice(-300);
      renderWorkflow();
      if (["stage_completed", "stage_failed", "run_completed", "run_paused", "run_cancelled", "human_decision", "pricing_updated"].includes(item.type)) fetchState();
    } catch (_) { /* Ignore malformed partial event lines. */ }
  }

  function stageStatus(stage) {
    const result = app.state?.stage_results?.[stage];
    const event = app.events.slice().reverse().find((item) => item.stage === stage && ["stage_started", "stage_completed", "stage_failed", "waiting_approval", "stage_progress"].includes(item.type));
    return result?.status || (event?.status === "calling_tool" ? "running" : event?.status) || "pending";
  }

  function graphActivity() {
    const lastModel = app.events.slice().reverse().find((item) => ["model_started", "model_finished"].includes(item.type));
    const modelRunning = lastModel?.type === "model_started";
    const lastTool = app.events.slice().reverse().find((item) => ["tool_started", "tool_finished"].includes(item.type));
    const toolRunning = lastTool?.type === "tool_started";
    const toClass = (status) => status === "running" || status === "reasoning" || status === "calling_tool" ? "running" : status === "failed" || status === "error" ? "failed" : status === "succeeded" || status === "completed" ? "success" : "pending";
    let reasonStatus = modelRunning ? "reasoning" : lastModel?.status || "pending";
    let actStatus = toolRunning ? "calling_tool" : lastTool?.status || "pending";
    if (!lastModel && !lastTool) {
      const runStatus = app.state?.status;
      if (runStatus === "succeeded") reasonStatus = actStatus = "succeeded";
      else if (runStatus === "failed") {
        const failedStage = Object.values(app.state?.stage_results || {}).some((result) => result.status === "failed");
        if (failedStage) { reasonStatus = "completed"; actStatus = "failed"; }
        else reasonStatus = "failed";
      }
    }
    return {
      reason: { status: reasonStatus, className: toClass(reasonStatus) },
      act: { status: actStatus, className: toClass(actStatus), activeTool: toolRunning ? lastTool.tool : null },
      modelRunning, toolRunning, activeTool: toolRunning ? lastTool.tool : null,
      lastTool: lastTool?.tool || null
    };
  }

  function renderWorkflow() {
    if (!app.state || !$("workflow-canvas")) return;
    const stageCards = workflowNodes.map(([stage, label, icon], index) => {
      const status = stageStatus(stage);
      const statusClass = status === "succeeded" || status === "completed" ? "success" : status === "failed" ? "failed" : status.includes("waiting") ? "waiting" : status === "running" ? "running" : "pending";
      const progress = stageProgress(stage, status);
      const action = (name, iconName, title) => `<button type="button" class="stage-action" data-stage-action="${name}" data-stage="${stage}" title="${title}" aria-label="${title}"><i data-lucide="${iconName}"></i></button>`;
      return `${index ? `<div class="workflow-arrow ${statusClass === "success" ? "success" : ""}"><i data-lucide="arrow-right"></i></div>` : ""}<article class="workflow-stage-node ${statusClass}" data-workflow-stage="${stage}" tabindex="0"><div class="workflow-node-icon"><i data-lucide="${icon}"></i></div><strong>${label}</strong><span>${stageStatusLabel(status)}</span>${progress ? `<em>${esc(progress)}</em>` : ""}<div class="workflow-stage-actions">${action("logs", "scroll-text", "查看日志")}${action("params", "sliders-horizontal", "查看参数")}${action("rerun", "rotate-ccw", "从此处重跑")}${action("takeover", "hand", "接管节点")}</div></article>`;
    }).join("");
    $("workflow-world").innerHTML = `<div class="workflow-entry">开始</div>${stageCards}<div class="workflow-entry workflow-end">完成</div>`;
    $("workflow-world").querySelectorAll("[data-workflow-stage]").forEach((node) => {
      const activate = () => { if (!app.workflowCanvas.moved) inspectWorkflowStage(node.dataset.workflowStage); };
      node.addEventListener("click", activate);
      node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } });
    });
    $("workflow-world").querySelectorAll("[data-stage-action]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      handleStageAction(button.dataset.stageAction, button.dataset.stage);
    }));
    if (!app.workflowCanvas.ready) fitWorkflowCanvas(); else applyWorkflowCanvas();
    const activeStage = Object.keys(toolStages).map((tool) => toolStages[tool]).find((stage) => ["running", "waiting_approval", "waiting_input", "waiting_execution", "failed"].includes(stageStatus(stage)));
    if (activeStage) inspectWorkflowStage(activeStage);
    const recent = app.events.slice(-1)[0]; $("workflow-updated").textContent = recent ? new Date(recent.timestamp).toLocaleTimeString() : "等待事件";
    const modelEvent = app.events.slice().reverse().find((item) => item.type === "model_started");
    const modelName = modelEvent?.model || app.state.model?.model || "—";
    $("workflow-model").textContent = "模型 " + modelName;
    const start = app.events.find((item) => item.type === "run_started")?.timestamp; $("workflow-elapsed").textContent = "耗时 " + (start ? duration(start, app.events.at(-1)?.timestamp) : "—");
    const completed = workflowNodes.filter(([stage]) => ["succeeded", "completed"].includes(stageStatus(stage))).length;
    const active = workflowNodes.find(([stage]) => ["running", "waiting_approval", "waiting_input", "waiting_execution", "failed"].includes(stageStatus(stage)));
    $("workflow-progress").textContent = `${completed}/${workflowNodes.length}`;
    $("workflow-stage-summary").textContent = active ? `${active[1]} · ${stageStatusLabel(stageStatus(active[0]))}` : app.state.status === "succeeded" ? "全部阶段已完成" : app.state.status === "failed" ? "运行失败，需要处理" : "等待开始";
    renderWorkflowLineage(); renderEventList(); renderRunFacts();
    renderCosts();
    if (window.lucide) window.lucide.createIcons();
  }

  function handleStageAction(action, stage) {
    app.selectedStage = stage;
    if (action === "logs") {
      const details = $("advanced-run-details");
      if (details) details.open = true;
      window.setTimeout(() => $("event-list")?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 40);
      return;
    }
    if (action === "params") {
      inspectWorkflowStage(stage);
      toast("节点参数和产物已显示在右侧详情。 ");
      return;
    }
    if (action === "rerun") {
      rerunFromSelectedStage();
      return;
    }
    if (action === "takeover") {
      inspectWorkflowStage(stage);
      if (app.state?.server?.workflow_running) sendControl("pause");
      else toast("已进入节点详情，可在审核面板接管后续决策。");
    }
  }

  function applyWorkflowCanvas() {
    const canvas = $("workflow-canvas"), world = $("workflow-world");
    if (!canvas || !world) return;
    const { x, y, scale } = app.workflowCanvas;
    world.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    $("workflow-zoom-level").textContent = `${Math.round(scale * 100)}%`;
  }

  function fitWorkflowCanvas() {
    const canvas = $("workflow-canvas"), world = $("workflow-world");
    if (!canvas || !world || !world.children.length || !canvas.clientWidth || !canvas.clientHeight) return;
    const width = world.scrollWidth, height = world.scrollHeight;
    const scale = Math.min(1, (canvas.clientWidth - 72) / width, (canvas.clientHeight - 72) / height);
    app.workflowCanvas.scale = Math.max(.45, scale);
    app.workflowCanvas.x = (canvas.clientWidth - width * app.workflowCanvas.scale) / 2;
    app.workflowCanvas.y = (canvas.clientHeight - height * app.workflowCanvas.scale) / 2;
    app.workflowCanvas.ready = true;
    applyWorkflowCanvas();
  }

  function zoomWorkflowCanvas(delta, anchorX, anchorY) {
    const canvas = $("workflow-canvas"), previous = app.workflowCanvas.scale;
    const next = Math.max(.45, Math.min(1.8, previous + delta));
    const x = anchorX ?? canvas.clientWidth / 2, y = anchorY ?? canvas.clientHeight / 2;
    app.workflowCanvas.x = x - (x - app.workflowCanvas.x) * (next / previous);
    app.workflowCanvas.y = y - (y - app.workflowCanvas.y) * (next / previous);
    app.workflowCanvas.scale = next;
    app.workflowCanvas.ready = true;
    applyWorkflowCanvas();
  }

  function updateWorkflowElapsed() {
    if (!app.state || !$("workflow-elapsed")) return;
    const start = app.events.find((item) => item.type === "run_started")?.timestamp;
    $("workflow-elapsed").textContent = "Elapsed " + (start ? duration(start, app.events.at(-1)?.timestamp) : "—");
  }

  function artifactCount(stage) {
    const count = (app.state?.artifacts?.[stage] || []).length;
    return count ? `<em>${count} artifact${count === 1 ? "" : "s"}</em>` : "";
  }

  function duration(start, end) { const seconds = Math.max(0, (new Date(end || Date.now()) - new Date(start)) / 1000); return seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.floor(seconds / 60)}m ${(seconds % 60).toFixed(0)}s`; }

  function inspectWorkflowStage(stage) {
    app.selectedStage = stage;
    const label = workflowNodes.find((item) => item[0] === stage)?.[1] || stage;
    const status = stageStatus(stage); const result = app.state.stage_results?.[stage] || {};
    $("workflow-node-title").textContent = label; $("workflow-node-status").textContent = stageStatusLabel(status);
    $("workflow-node-detail").innerHTML = `<div class="inspector-fact"><span>状态</span><strong>${esc(stageStatusLabel(status))}</strong></div><div class="inspector-fact"><span>产物</span><strong>${(app.state.artifacts?.[stage] || []).length}</strong></div><div class="inspector-fact"><span>进度</span><strong>${esc(stageProgress(stage, status) || "—")}</strong></div><div class="inspector-fact"><span>最近结果</span><strong>${esc(result.error || result.metadata?.profile || result.metadata?.selected_rank || "已记录在运行清单")}</strong></div>`;
    $("rerun-stage").disabled = app.state.server?.workflow_running;
  }

  function inspectGraphNode(node) {
    const graph = graphActivity();
    const isReason = node === "reason";
    const title = isReason ? "reason · LLM" : "act · tool executor";
    const status = isReason ? graph.reason.status : graph.act.status;
    const event = isReason
      ? app.events.slice().reverse().find((item) => ["model_started", "model_finished"].includes(item.type))
      : app.events.slice().reverse().find((item) => ["tool_started", "tool_finished"].includes(item.type));
    const detail = isReason
      ? [["Node", "LangGraph reason"], ["Action", "Invoke the bound chat model"], ["Next", "tool_calls → act; otherwise END"], ["Latest model", event?.model || app.state.model?.model || "—"]]
      : [["Node", "LangGraph act"], ["Action", "Execute requested tools in serial order"], ["Next", "Continue → reason; wait/failure/control → END"], ["Latest tool", event?.tool || "—"]];
    $("workflow-node-title").textContent = title;
    $("workflow-node-status").textContent = String(status).replaceAll("_", " ");
    $("workflow-node-detail").innerHTML = detail.map(([label, value]) => `<div class="inspector-fact"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
    $("rerun-stage").disabled = true;
  }

  function renderWorkflowLineage() {
    const artifacts = Object.values(app.state.artifacts || {}).flat().slice(-12);
    $("workflow-lineage").innerHTML = artifacts.length ? artifacts.map((item) => `<span class="lineage-item"><i data-lucide="file-output"></i>${esc(item.kind)}<small>${esc(item.path.split("/").pop())}</small></span>`).join(`<i class="lineage-arrow" data-lucide="chevron-right"></i>`) : `<span class="muted">Artifacts will appear here as stages publish outputs.</span>`;
  }

  function renderEventList() {
    const items = app.events.slice(-30).reverse();
    $("event-list").innerHTML = items.length ? items.map((item) => `<div class="event-row"><span class="event-time">${esc(new Date(item.timestamp).toLocaleTimeString())}</span><span class="event-kind ${esc(item.status || "")}">${esc(item.type.replaceAll("_", " "))}</span><span class="event-stage">${esc(item.stage || item.tool || "supervisor")}</span><span class="event-detail">${esc(item.error || item.reason || item.kind || item.model || (item.observation?.status ? `status ${item.observation.status}` : ""))}</span></div>`).join("") : `<div class="empty-state">No events recorded yet.</div>`;
  }

  function renderRunFacts() {
    const state = app.state; const facts = [["运行状态", stageStatusLabel(state.status)], ["当前阶段", workflowNodes.find(([stage]) => stage === state.current_node)?.[1] || state.current_node || "空闲"], ["事件", app.events.length], ["产物", Object.values(state.artifacts || {}).flat().length], ["模型调用", app.events.filter((item) => item.type === "model_started").length], ["工具调用", app.events.filter((item) => item.type === "tool_started").length]];
    $("run-facts").innerHTML = facts.map(([label, value]) => `<div class="run-fact"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
  }

  function renderCosts() {
    const costs = app.state.costs || { models: {}, pricing: {} };
    const rows = Object.entries(costs.models || {});
    const total = costs.estimated_usd == null ? "Cost not priced" : `$${Number(costs.estimated_usd).toFixed(6)}`;
    $("cost-summary").innerHTML = `<div class="cost-total"><span>Estimated model cost</span><strong>${esc(total)}</strong></div><div class="cost-tokens">${Number(costs.input_tokens || 0).toLocaleString()} input · ${Number(costs.output_tokens || 0).toLocaleString()} output tokens</div>${rows.map(([model, row]) => `<div class="cost-model"><strong>${esc(model)}</strong><span>${Number(row.input_tokens || 0).toLocaleString()} / ${Number(row.output_tokens || 0).toLocaleString()} tokens</span><small>${row.estimated_usd == null ? "No rate or usage data" : `$${Number(row.estimated_usd).toFixed(6)}`}</small></div>`).join("")}`;
    const model = app.state.model?.model || rows[0]?.[0] || "";
    const rate = costs.pricing?.[model] || {};
    if (document.activeElement !== $("price-input")) $("price-input").value = rate.input_usd_per_million ?? "";
    if (document.activeElement !== $("price-output")) $("price-output").value = rate.output_usd_per_million ?? "";
    app.pricing.model = model;
  }

  async function fetchRuns() {
    try {
      const response = await fetch("/api/runs", { cache: "no-store" }); const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      $("run-selector").innerHTML = (data.runs || []).map((run) => {
        const title = `${run.run_key} · ${run.target_id || run.run_id} · ${run.status || "unknown"}`;
        return `<option value="${esc(run.run_key)}" ${run.selected ? "selected" : ""} title="${esc(title)}">${esc(run.run_key)}</option>`;
      }).join("");
    } catch (error) { toast(error.message, true); }
  }

  async function fetchProjects() {
    try {
      const response = await fetch("/api/projects", { cache: "no-store" }); const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      const selected = data.selected || app.state?.project || {};
      app.projectId = selected.project_id || ".";
      $("project-selector").innerHTML = (data.projects || []).map((project) => `<option value="${esc(project.project_id)}" ${project.project_id === app.projectId ? "selected" : ""}>${esc(project.name)}</option>`).join("");
      $("project-description").textContent = selected.name || app.projectId;
      renderChatStatus();
      await fetchChats();
    } catch (error) { toast(error.message, true); }
  }

  async function selectProject(projectId) {
    try {
      const response = await fetch("/api/select-project", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: projectId }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.projectId = data.project_id;
      app.fileDirectory = ""; app.filePath = ""; app.terminalCwd = "";
      app.terminalSession = null; app.terminalCursor = 0;
      if (app.terminalPollTimer) window.clearInterval(app.terminalPollTimer); app.terminalPollTimer = null;
      app.terminal?.reset();
      if (app.codeEditor) app.codeEditor.setValue(""); else $("file-editor").value = "";
      $("file-editor").value = ""; $("editor-file-label").textContent = "No file selected"; $("editor-language").textContent = "";
      $("editor-empty").hidden = false; $("save-file").disabled = true;
      await fetchProjects();
      toast(`Project: ${data.name}`);
    } catch (error) { toast(error.message, true); }
  }

  async function refreshProjectFiles() {
    try {
      const response = await fetch(`/api/project/files?path=${encodeURIComponent(app.fileDirectory)}`, { cache: "no-store" });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      $("file-tree-path").textContent = `/${data.path || ""}`;
      const parent = data.path ? `<button class="file-entry parent-entry" data-file-parent="1"><i data-lucide="corner-left-up"></i><span>..</span></button>` : "";
      const rows = (data.entries || []).map((entry) => {
        const disabled = entry.kind === "file" && entry.previewable === false;
        const ext = entry.name.toLowerCase().split(".").pop();
        const icon = entry.kind === "directory" ? "folder" : disabled ? "file-x-2" : editorModes[ext] ? "file-code-2" : "file-text";
        const title = disabled ? entry.preview_reason || "Preview unavailable" : entry.kind === "directory" ? "Open folder" : "Open file";
        return `<button class="file-entry ${entry.kind} ${disabled ? "unsupported" : ""}" data-file-path="${esc(entry.path)}" data-file-kind="${entry.kind}" ${disabled ? "disabled" : ""} title="${esc(title)}"><i data-lucide="${icon}"></i><span>${esc(entry.name)}</span>${entry.kind === "file" ? `<small>${formatBytes(entry.size)}${disabled ? " · 不可预览" : ""}</small>` : ""}</button>`;
      }).join("");
      $("file-tree").innerHTML = parent + rows || `<div class="empty-state">This folder is empty.</div>`;
      if (data.truncated) $("file-tree").insertAdjacentHTML("afterbegin", `<div class="file-list-note">仅显示前 500 项</div>`);
      $("file-tree").querySelectorAll("[data-file-kind]:not(:disabled)").forEach((button) => button.addEventListener("click", () => button.dataset.fileKind === "directory" ? openProjectDirectory(button.dataset.filePath) : openProjectFile(button.dataset.filePath)));
      $("file-tree").querySelector("[data-file-parent]")?.addEventListener("click", () => openProjectDirectory(app.fileDirectory.split("/").slice(0, -1).join("/")));
      if (window.lucide) window.lucide.createIcons();
    } catch (error) { $("file-tree").innerHTML = `<div class="empty-state">${esc(error.message)}</div>`; }
  }

  function formatBytes(value) {
    const size = Number(value || 0);
    return size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;
  }

  async function openProjectDirectory(path) { app.fileDirectory = path; await refreshProjectFiles(); }

  async function openProjectFile(path) {
    try {
      const response = await fetch(`/api/project/file?path=${encodeURIComponent(path)}`); const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.filePath = data.path;
      if (app.codeEditor) app.codeEditor.setValue(data.content); else $("file-editor").value = data.content;
      $("editor-file-label").textContent = data.path; $("editor-empty").hidden = true; $("save-file").disabled = false;
      setEditorMode(data.path);
      requestAnimationFrame(() => app.codeEditor?.refresh());
      document.querySelectorAll(".file-entry").forEach((node) => node.classList.toggle("selected", node.dataset.filePath === path));
      document.querySelector('[data-view="files"]')?.click();
    } catch (error) { toast(error.message, true); }
  }

  async function saveProjectFile() {
    if (!app.filePath) return;
    try {
      const content = app.codeEditor ? app.codeEditor.getValue() : $("file-editor").value;
      const response = await fetch("/api/project/file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: app.filePath, content }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      toast(`Saved ${data.path}`); refreshProjectFiles();
    } catch (error) { toast(error.message, true); }
  }

  function createTerminal() {
    if (app.terminal) return true;
    if (!window.Terminal || !window.FitAddon?.FitAddon) {
      $("terminal-screen").textContent = "Terminal library could not be loaded.";
      return false;
    }
    app.terminal = new window.Terminal({
      allowProposedApi: false, convertEol: true, cursorBlink: true,
      fontFamily: '"DM Mono", monospace', fontSize: 12, scrollback: 3000,
      theme: { background: "#15191e", foreground: "#d9e1eb", cursor: "#79c99b", selectionBackground: "#354252" }
    });
    app.terminalFit = new window.FitAddon.FitAddon();
    app.terminal.loadAddon(app.terminalFit);
    app.terminal.open($("terminal-screen"));
    app.terminal.options.disableStdin = true;
    app.terminal.onData(queueTerminalInput);
    requestAnimationFrame(resizeTerminal);
    return true;
  }

  async function ensureTerminal() {
    if (!createTerminal()) return;
    if (app.terminalSession) {
      app.terminal.options.disableStdin = false;
      requestAnimationFrame(resizeTerminal);
      if (!app.terminalPollTimer) app.terminalPollTimer = window.setInterval(pollTerminal, 350);
      return;
    }
    try {
      const response = await fetch("/api/project/terminal/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cwd: app.terminalCwd }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.terminalSession = data.session_id; app.terminalCwd = data.cwd || ""; app.terminalCursor = 0;
      $("terminal-cwd").textContent = `/${app.projectId}${app.terminalCwd ? `/${app.terminalCwd}` : ""}`;
      app.terminal.options.disableStdin = false;
      await resizeTerminal(); await pollTerminal();
      app.terminalPollTimer = window.setInterval(pollTerminal, 350);
      app.terminal.focus();
    } catch (error) { app.terminal.writeln(`\r\n\x1b[31m${error.message}\x1b[0m`); }
  }

  async function resizeTerminal() {
    if (!app.terminal || !app.terminalFit) return;
    try {
      app.terminalFit.fit();
      if (!app.terminalSession) return;
      await fetch("/api/project/terminal/resize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: app.terminalSession, columns: app.terminal.cols, rows: app.terminal.rows }) });
    } catch (_) { /* Keep the current terminal size while the pane is hidden. */ }
  }

  function queueTerminalInput(value) {
    if (!app.terminalSession) return;
    app.terminalInputBuffer += value;
    if (app.terminalInputTimer) return;
    app.terminalInputTimer = window.setTimeout(async () => {
      const input = app.terminalInputBuffer;
      app.terminalInputBuffer = ""; app.terminalInputTimer = null;
      if (!input || !app.terminalSession) return;
      try {
        const response = await fetch("/api/project/terminal/input", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: app.terminalSession, input }) });
        const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      } catch (error) { app.terminal?.writeln(`\r\n\x1b[31m${error.message}\x1b[0m`); }
    }, 25);
  }

  async function pollTerminal() {
    if (!app.terminalSession) return;
    try {
      const response = await fetch(`/api/project/terminal/output?session_id=${encodeURIComponent(app.terminalSession)}&since=${app.terminalCursor}`, { cache: "no-store" });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      if (data.reset) { app.terminal.clear(); app.terminalCursor = 0; }
      if (data.data) app.terminal.write(data.data);
      app.terminalCursor = data.cursor; app.terminalCwd = data.cwd || "";
      $("terminal-cwd").textContent = `/${app.projectId}${app.terminalCwd ? `/${app.terminalCwd}` : ""}`;
      if (!data.running) {
        app.terminal.writeln("\r\n[ shell exited ]");
        app.terminal.options.disableStdin = true;
        app.terminalSession = null; window.clearInterval(app.terminalPollTimer); app.terminalPollTimer = null;
      }
    } catch (error) { app.terminal.writeln(`\r\n\x1b[31m${error.message}\x1b[0m`); app.terminalSession = null; window.clearInterval(app.terminalPollTimer); app.terminalPollTimer = null; }
  }

  async function restartTerminal() {
    if (app.terminalPollTimer) window.clearInterval(app.terminalPollTimer);
    app.terminalPollTimer = null;
    if (app.terminalSession) {
      await fetch("/api/project/terminal/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: app.terminalSession }) });
      app.terminalSession = null;
    }
    app.terminal?.reset();
    ensureTerminal();
  }

  async function fetchChats() {
    const response = await fetch(`/api/chats?project_id=${encodeURIComponent(app.projectId)}`, { cache: "no-store" });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
    app.chatSessions = data.chats || [];
    const savedId = localStorage.getItem(`dock-agent-chat-id:${app.projectId}`);
    const selected = app.chatSessions.find((item) => item.chat_id === savedId && !item.archived_at)
      || app.chatSessions.find((item) => !item.archived_at);
    if (selected) {
      app.chatId = selected.chat_id; app.chatHistory = selected.messages || [];
    } else {
      let legacy = [];
      try { legacy = JSON.parse(localStorage.getItem(`dock-agent-chat:${app.projectId}`) || "[]"); } catch (_) { legacy = []; }
      const created = await createChatRequest();
      app.chatId = created.chat_id; app.chatHistory = legacy;
      if (legacy.length) await persistChat();
      localStorage.removeItem(`dock-agent-chat:${app.projectId}`);
      await fetchChatsListOnly();
    }
    localStorage.setItem(`dock-agent-chat-id:${app.projectId}`, app.chatId);
    renderChatList(); renderChat();
  }

  async function fetchChatsListOnly() {
    const response = await fetch(`/api/chats?project_id=${encodeURIComponent(app.projectId)}`, { cache: "no-store" });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
    app.chatSessions = data.chats || [];
  }

  async function createChatRequest() {
    const response = await fetch("/api/chats", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: app.projectId }) });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }

  async function newChat() {
    try {
      app.chatFilter = "active";
      const chat = await createChatRequest();
      app.chatId = chat.chat_id; app.chatHistory = [];
      localStorage.setItem(`dock-agent-chat-id:${app.projectId}`, app.chatId);
      await fetchChatsListOnly(); renderChatList(); renderChat(); $("chat-input").focus();
    } catch (error) { toast(error.message, true); }
  }

  async function selectChat(chatId) {
    const chat = app.chatSessions.find((item) => item.chat_id === chatId);
    if (!chat) return;
    if (chat.archived_at) {
      await updateChatArchive(chatId, false);
      app.chatFilter = "active";
    }
    app.chatId = chat.chat_id; app.chatHistory = chat.messages || [];
    localStorage.setItem(`dock-agent-chat-id:${app.projectId}`, app.chatId);
    renderChatList(); renderChat();
  }

  async function persistChat() {
    if (!app.chatId) return;
    const response = await fetch("/api/chats/save", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: app.projectId, chat_id: app.chatId, messages: app.chatHistory }) });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
    const current = app.chatSessions.find((item) => item.chat_id === app.chatId);
    if (current) Object.assign(current, data); else app.chatSessions.unshift(data);
    renderChatList();
  }

  function renderChatList() {
    const list = $("chat-list");
    document.querySelectorAll("[data-chat-filter]").forEach((button) => button.classList.toggle("active", button.dataset.chatFilter === app.chatFilter));
    const rows = app.chatSessions.filter((chat) => app.chatFilter === "archived" ? Boolean(chat.archived_at) : !chat.archived_at);
    list.innerHTML = rows.length ? rows.map((chat) =>
      `<div class="chat-list-row"><button type="button" class="chat-list-item ${chat.chat_id === app.chatId ? "active" : ""}" data-chat-id="${esc(chat.chat_id)}" title="${esc(chat.title)}"><i data-lucide="message-square"></i><span>${esc(chat.title || "新对话")}</span></button><button type="button" class="chat-archive-button" data-chat-archive="${esc(chat.chat_id)}" title="${chat.archived_at ? "恢复对话" : "归档对话"}" aria-label="${chat.archived_at ? "恢复对话" : "归档对话"}"><i data-lucide="${chat.archived_at ? "archive-restore" : "archive"}"></i></button></div>`).join("") :
      `<div class="chat-list-empty">${app.chatFilter === "archived" ? "暂无归档对话" : "暂无活跃对话"}</div>`;
    list.querySelectorAll("[data-chat-id]").forEach((button) => button.addEventListener("click", () => selectChat(button.dataset.chatId)));
    list.querySelectorAll("[data-chat-archive]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      const chat = app.chatSessions.find((item) => item.chat_id === button.dataset.chatArchive);
      updateChatArchive(button.dataset.chatArchive, !chat?.archived_at);
    }));
    if (window.lucide) window.lucide.createIcons();
    const current = app.chatSessions.find((item) => item.chat_id === app.chatId);
    $("project-title").textContent = current?.title || "新对话";
  }

  async function updateChatArchive(chatId, archived) {
    try {
      const response = await fetch("/api/chats/archive", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: app.projectId, chat_id: chatId, archived })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      const chat = app.chatSessions.find((item) => item.chat_id === chatId);
      if (chat) Object.assign(chat, data);
      renderChatList();
      if (archived && app.chatId === chatId) {
        const next = app.chatSessions.find((item) => !item.archived_at);
        if (next) selectChat(next.chat_id);
        else await newChat();
      } else if (!archived && app.chatFilter === "archived") {
        app.chatFilter = "active";
        renderChatList();
      }
    } catch (error) { toast(error.message, true); }
  }

  function openTerminalDock() {
    $("terminal-dock").classList.add("open");
    requestAnimationFrame(ensureTerminal);
  }

  function closeTerminalDock() {
    $("terminal-dock").classList.remove("open");
    if (app.terminalPollTimer) window.clearInterval(app.terminalPollTimer);
    app.terminalPollTimer = null;
  }

  function renderChatStatus() {
    if (!$('chat-status')) return;
    const ready = Boolean(app.state?.chat_available);
    $("chat-status").textContent = ready ? "模型已连接" : "模型未连接";
    $("chat-status").classList.toggle("chat-status-offline", !ready);
  }

  function renderChat() {
    const messages = app.chatHistory || [];
    $("chat-messages").innerHTML = messages.length ? messages.map((item) => `<article class="chat-message ${item.role === "user" ? "user" : "assistant"}"><div class="chat-role">${item.role === "user" ? "你" : "DrugStudio"}</div><div class="chat-content">${esc(item.content).replaceAll("\n", "<br>")}</div>${item.actions?.length ? `<div class="chat-actions">${item.actions.map((action) => `<span><i data-lucide="${action.tool === "run_command" ? "terminal" : "file"}"></i>${esc(action.tool)}${action.path ? ` · ${esc(action.path)}` : ""}</span>`).join("")}</div>` : ""}</article>`).join("") : `<div class="chat-empty"><i data-lucide="messages-square"></i><span>从当前项目开始新对话</span></div>`;
    if (window.lucide) window.lucide.createIcons();
    $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
    const current = app.chatSessions.find((item) => item.chat_id === app.chatId);
    $("project-title").textContent = current?.title || "新对话";
  }

  async function sendChat(event) {
    event.preventDefault();
    const input = $("chat-input"), content = input.value.trim(); if (!content || app.chatBusy) return;
    const projectId = app.projectId, history = app.chatHistory;
    history.push({ role: "user", content }); input.value = ""; app.chatBusy = true; $("send-chat").disabled = true; renderChat();
    $("chat-status").textContent = "Agent 正在处理";
    $("chat-status").classList.remove("chat-status-offline");
    try { await persistChat(); } catch (error) { toast(error.message, true); }
    try {
      const response = await fetch("/api/project/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        messages: history, model: $("chat-model").value.trim(), temperature: Number($("chat-temperature").value)
      }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      history.push({ role: "assistant", content: data.reply, actions: data.actions || [] });
    } catch (error) { history.push({ role: "assistant", content: `Unable to respond: ${error.message}` }); }
    app.chatBusy = false; $("send-chat").disabled = false;
    renderChatStatus();
    if (app.projectId === projectId) {
      try { await persistChat(); } catch (error) { toast(error.message, true); }
    }
    if (app.projectId === projectId) { app.chatHistory = history; renderChat(); }
  }

  async function selectRun(runKey) {
    try {
      const response = await fetch("/api/select-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run_key: runKey }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.eventSource?.close(); app.eventSource = null; app.events = []; app.provenance = null; app.proteinViewer = null; app.ligandViewer = null;
      $("result-provenance")?.setAttribute("hidden", "");
      await fetchState(); toast(`Selected ${runKey}`);
    } catch (error) { toast(error.message, true); await fetchRuns(); }
  }

  async function sendControl(action) {
    try {
      const response = await fetch("/api/control", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      toast(action === "pause" ? "Pause requested; waiting for a safe checkpoint" : "Cancel requested; waiting for a safe checkpoint");
      await fetchState();
    } catch (error) { toast(error.message, true); }
  }

  async function rerunFromSelectedStage() {
    if (!app.selectedStage) return;
    try {
      const response = await fetch("/api/rerun", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stage: app.selectedStage }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.eventSource?.close(); app.eventSource = null; app.events = []; app.provenance = null; app.proteinViewer = null; app.ligandViewer = null;
      await selectRun(data.run_key || data.run_id);
      await continueWorkflow();
    } catch (error) { toast(error.message, true); }
  }

  async function savePricing() {
    try {
      if (!$("price-input").value.trim() || !$("price-output").value.trim()) throw new Error("Enter both input and output token rates");
      const payload = { model: app.pricing.model, input_usd_per_million: Number($("price-input").value), output_usd_per_million: Number($("price-output").value) };
      const response = await fetch("/api/pricing", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.state.costs = data; renderCosts(); toast("Model rates saved for this run");
    } catch (error) { toast(error.message, true); }
  }

  function structureCandidates() {
    const all = Object.values(app.state.artifacts || {}).flat();
    const priority = ["prepared_receptor_pdb", "repaired_receptor_pdb", "supplied_structure", "experimental_structure", "predicted_structure"];
    return priority.flatMap((kind) => all.filter((item) => item.kind === kind));
  }

  function renderStructureChoices() {
    const items = structureCandidates();
    const select = $("structure-select");
    select.innerHTML = items.length ? items.map((item, index) => `<option value="${index}">${esc(item.kind)} · ${esc(item.path.split("/").pop())}</option>`).join("") : `<option value="">No structure artifact</option>`;
    app.structureArtifact = items[0] || null;
    $("structure-file").textContent = app.structureArtifact?.path || "—";
    const quality = app.state.stage_results?.structure?.metadata?.quality || app.state.stage_results?.receptor?.metadata?.structure_quality || app.state.stage_results?.pocket?.metadata?.structure_quality;
    if (quality) {
      $("structure-quality-title").textContent = quality.gate === "pass" || quality.status === "pass" ? "结构质量通过" : "结构需要关注";
      const chips = [["来源", app.structureArtifact?.metadata?.source || app.structureArtifact?.kind || "—"], ["链数", quality.chain_count ?? "—"], ["缺失残基", quality.missing_residues ?? quality.missing_residue_count ?? "—"], ["分辨率", quality.resolution_A != null ? `${fmt(quality.resolution_A, 2)} Å` : "—"]];
      $("structure-quality-chips").innerHTML = chips.map(([label, value]) => `<span class="quality-chip"><small>${esc(label)}</small><strong>${esc(value)}</strong></span>`).join("");
    } else {
      $("structure-quality-title").textContent = items.length ? "已获得结构，等待质量评估" : "等待结构产物";
      $("structure-quality-chips").innerHTML = `<span class="quality-chip muted">质量数据将在结构阶段完成后显示</span>`;
    }
  }

  function poseArtifacts() {
    const all = Object.values(app.state.artifacts || {}).flat();
    return all.filter((item) => item.kind === "docking_output" && String(item.metadata?.relative_output || "").endsWith("/poses.sdf"));
  }

  function renderPoseChoices() {
    const poses = poseArtifacts();
    const select = $("pose-select");
    select.innerHTML = `<option value="">No docked pose overlay</option>` + poses.map((item, index) => `<option value="${index}">${esc(item.metadata?.relative_output || item.path.split("/").pop())}</option>`).join("");
  }

  function currentPockets() {
    return app.state.stage_results?.pocket?.metadata?.candidates || [];
  }

  function renderPockets() {
    const pockets = currentPockets();
    const referenceResidues = app.state.target?.binding_site_residues || [];
    $("pocket-count").textContent = pockets.length;
    const html = pockets.length ? pockets.map((pocket) => {
      const selected = app.selectedPocket?.rank === pocket.rank || (!app.selectedPocket && pocket.selected);
      return `<div class="pocket-card ${selected ? "selected" : ""}" data-pocket-rank="${pocket.rank}">
        <div class="pocket-row"><span class="pocket-name">${esc(pocket.name || `Pocket ${pocket.rank}`)}</span><span class="pocket-score">P2Rank score ${fmt(pocket.p2rank_score ?? pocket.score, 2)}</span></div>
        <div class="pocket-meta"><span>rank ${esc(pocket.rank)}</span><span>predictors ${(pocket.sources || []).join(" + ") || "P2Rank"}</span><span>P2Rank p ${fmt(pocket.p2rank_probability ?? pocket.probability, 2)}</span>${pocket.fpocket_druggability_score != null ? `<span>fpocket druggability ${fmt(pocket.fpocket_druggability_score, 2)}</span>` : ""}<span>${(pocket.residue_ids || []).length} residues</span><span>${esc(String(pocket.evidence_class || "computational_only").replaceAll("_", " "))}</span>${pocket.reference_site_overlap_count ? `<span>${pocket.reference_site_overlap_count} reference-site residues overlap</span>` : ""}${pocket.predictor_center_distance_A != null ? `<span>centers agree within ${fmt(pocket.predictor_center_distance_A, 1)} Å</span>` : ""}${pocket.human_review_recommended ? `<span>review recommended</span>` : ""}</div>
      </div>`;
    }).join("") : referenceResidues.length
      ? `<button class="pocket-card reference-site-card" type="button" data-reference-site><div class="pocket-row"><span class="pocket-name">Reference binding site</span><span class="pocket-score">Input provided</span></div><div class="pocket-meta"><span>${referenceResidues.length} specified residues</span><span>Not an independently predicted pocket</span></div></button>`
      : `<div class="empty-state">暂无口袋预测或参考位点。</div>`;
    const quality = app.state.stage_results?.pocket?.metadata?.structure_quality;
    const qualityHtml = quality ? `<div class="quality-pill">Structure QC: ${esc(quality.gate)} · ${quality.chain_count} chains · median CA B ${fmt(quality.ca_bfactor_median, 1)}${quality.review_flags?.length ? ` · ${quality.review_flags.length} review flags` : ""}</div>` : "";
    $("pocket-list").innerHTML = qualityHtml + html;
    $("review-pocket-list").innerHTML = pockets.length ? pockets.map((pocket) => {
      const selected = app.selectedPocket?.rank === pocket.rank;
      return `<div class="review-pocket-card ${selected ? "selected" : ""}" data-review-rank="${pocket.rank}"><span class="review-rank">${pocket.rank}</span><div><strong>${esc(pocket.name || `Pocket ${pocket.rank}`)}</strong><small>${(pocket.residue_ids || []).slice(0, 5).map(esc).join(" · ") || "No residue labels"} · ${(pocket.sources || ["P2Rank"]).join(" + ")}${pocket.predictor_center_distance_A != null ? ` · centers agree ${fmt(pocket.predictor_center_distance_A, 1)} Å` : ""}${pocket.human_review_recommended ? " · review recommended" : ""}</small></div><span class="pocket-score">${fmt(pocket.p2rank_probability ?? pocket.score, 2)}</span></div>`;
    }).join("") : `<div class="empty-state">暂无候选口袋。</div>`;
    document.querySelectorAll("[data-pocket-rank]").forEach((node) => node.addEventListener("click", () => selectPocket(Number(node.dataset.pocketRank))));
    document.querySelectorAll("[data-review-rank]").forEach((node) => node.addEventListener("click", () => selectPocket(Number(node.dataset.reviewRank))));
    $("pocket-list").querySelector("[data-reference-site]")?.addEventListener("click", () => setProteinStyle("site"));
    if (!app.selectedPocket && pockets.length) selectPocket(Number(pockets.find((item) => item.selected)?.rank || pockets[0].rank), false);
    else if (!app.selectedPocket) renderPocketDetail();
  }

  function selectPocket(rank, rerender = true) {
    app.selectedPocket = currentPockets().find((item) => Number(item.rank) === rank) || null;
    if (app.selectedPocket) {
      const center = app.selectedPocket.box?.center || app.selectedPocket.center || [];
      const size = app.selectedPocket.box?.size || [];
      ["x", "y", "z"].forEach((axis, index) => { $(`center-${axis}`).value = center[index] ?? ""; $(`size-${axis}`).value = size[index] ?? 24; });
      renderPocketDetail();
      if (app.proteinStyle === "site") setProteinStyle("site");
      else showPocket(app.selectedPocket);
    }
    if (rerender) renderPockets();
  }

  function renderPocketDetail() {
    const pocket = app.selectedPocket;
    if (!pocket) {
      const residues = app.state.target?.binding_site_residues || [];
      $("pocket-detail").innerHTML = residues.length
        ? `<div class="detail-item"><label>REFERENCE SITE</label><strong>Provided in target input</strong></div><div class="detail-item"><label>RESIDUES</label><strong>${residues.length}</strong></div><div class="residue-list">${residues.map((item) => `<span class="residue-chip">${esc(item)}</span>`).join("")}</div>`
        : `<span class="muted">No predicted or reference site residues.</span>`;
      return;
    }
    const center = pocket.box?.center || pocket.center || [];
    const size = pocket.box?.size || [];
    $("pocket-detail").innerHTML = `<div class="detail-item"><label>CENTER X / Y / Z</label><strong class="mono">${center.map((n) => fmt(n, 2)).join(" · ") || "—"}</strong></div><div class="detail-item"><label>BOX Å</label><strong class="mono">${size.map((n) => fmt(n, 1)).join(" · ") || "—"}</strong></div><div class="detail-item"><label>PROBABILITY</label><strong>${fmt(pocket.probability, 3)}</strong></div><div class="detail-item"><label>SURFACE POINTS</label><strong>${esc(pocket.surface_points ?? "—")}</strong></div><div class="residue-list">${(pocket.residue_ids || []).map((item) => `<span class="residue-chip">${esc(item)}</span>`).join("") || `<span class="muted">No residue labels</span>`}</div>`;
  }

  async function loadProtein() {
    const artifact = app.structureArtifact;
    if (!artifact) return;
    $("structure-file").textContent = artifact.path;
    if (!window.$3Dmol) { $("protein-viewer").innerHTML = `<div class="viewer-placeholder">3D viewer library unavailable</div>`; return; }
    if (!webglAvailable()) { showViewerMessage("protein-viewer", "WebGL is disabled in this browser. Enable hardware acceleration to view 3D structures."); return; }
    try {
      const response = await fetch(fileUrl(artifact.path));
      if (!response.ok) throw new Error(`Structure request failed (${response.status})`);
      const text = await response.text();
      $("protein-viewer").innerHTML = "";
      app.proteinViewer = window.$3Dmol.createViewer($("protein-viewer"), { backgroundColor: "#0e1116", antialias: true });
      app.proteinViewer.addModel(text, "pdb");
      setProteinStyle("cartoon");
      await addPoseOverlay();
      app.proteinViewer.zoomTo(); app.proteinViewer.render();
      if (app.selectedPocket) showPocket(app.selectedPocket);
    } catch (error) { showViewerMessage("protein-viewer", `Unable to load structure: ${error.message || String(error) || "3D renderer failed"}`); }
  }

  async function addPoseOverlay() {
    const index = Number($("pose-select")?.value);
    const artifact = poseArtifacts()[index];
    if (!artifact || !app.proteinViewer) return;
    const text = await fetch(fileUrl(artifact.path)).then((response) => response.text());
    app.proteinViewer.addModel(text, "sdf");
    app.proteinViewer.setStyle({ model: 1 }, { stick: { radius: .2, colorscheme: "Jmol" }, sphere: { scale: .28, colorscheme: "Jmol" } });
  }

  function siteResidueSelections() {
    const values = app.selectedPocket?.residue_ids?.length
      ? app.selectedPocket.residue_ids
      : app.state?.target?.binding_site_residues || [];
    return values.map((value) => {
      const match = String(value).match(/^([A-Za-z0-9])[: ]?(\d+)/);
      return match ? { chain: match[1], resi: Number(match[2]) } : null;
    }).filter(Boolean);
  }

  function setProteinStyle(style) {
    if (!app.proteinViewer) return;
    const protein = { model: 0 };
    const siteSelections = style === "site" ? siteResidueSelections() : [];
    if (style === "site" && !siteSelections.length) {
      toast("当前没有预测口袋或参考结合位点残基可显示。", true);
      return;
    }
    try {
      app.proteinViewer.removeAllSurfaces?.();
      app.proteinViewer.removeAllShapes();
      app.proteinViewer.setStyle(protein, {});
      if (style === "site") {
        app.proteinViewer.setStyle(protein, { cartoon: { color: "#b8c1c5", opacity: 0.22 } });
        siteSelections.forEach((selection) => {
          app.proteinViewer.setStyle(selection, {
            cartoon: { color: "#e78e3b" },
            stick: { radius: 0.24, colorscheme: "Jmol" },
            sphere: { scale: 0.32, colorscheme: "Jmol" }
          });
        });
        if (app.selectedPocket) {
          const center = app.selectedPocket.box?.center || app.selectedPocket.center;
          if (center?.length === 3) {
            app.proteinViewer.addShape({ sphere: { center: { x: center[0], y: center[1], z: center[2] }, radius: 1.2, color: "#f2b562", alpha: 0.35 } });
            app.proteinViewer.addLabel(`Pocket ${app.selectedPocket.rank}`, { position: { x: center[0], y: center[1], z: center[2] }, backgroundColor: "#fff7e9", fontColor: "#bd7a1f", fontSize: 11, inFront: true });
          }
        }
        const focus = siteSelections.length === 1 ? siteSelections[0] : { or: siteSelections };
        try { app.proteinViewer.zoomTo(focus); }
        catch (_) { app.proteinViewer.zoomTo(); }
        $("viewer-hint").textContent = app.selectedPocket ? `Predicted pocket · ${siteSelections.length} residues` : `Reference site · ${siteSelections.length} residues`;
      } else {
        app.proteinViewer.setStyle(protein, { cartoon: { color: "spectrum" } });
        $("viewer-hint").textContent = "PDB coordinate frame";
        if (app.selectedPocket) showPocket(app.selectedPocket);
      }
      app.proteinStyle = style;
      app.proteinViewer.render();
      document.querySelectorAll("[data-protein-style]").forEach((button) => button.classList.toggle("active", button.dataset.proteinStyle === style));
    } catch (error) {
      app.proteinViewer.removeAllSurfaces?.();
      app.proteinViewer.setStyle(protein, { cartoon: { color: "spectrum" } });
      app.proteinViewer.render();
      app.proteinStyle = "cartoon";
      document.querySelectorAll("[data-protein-style]").forEach((button) => button.classList.toggle("active", button.dataset.proteinStyle === "cartoon"));
      $("viewer-hint").textContent = "PDB coordinate frame";
      toast(error.message || "无法显示结合位点。", true);
    }
  }

  function showPocket(pocket) {
    if (!app.proteinViewer || !pocket) return;
    app.proteinViewer.removeAllShapes();
    const center = pocket.box?.center || pocket.center;
    if (center?.length === 3) {
      app.proteinViewer.addShape({ sphere: { center: { x: center[0], y: center[1], z: center[2] }, radius: 1.6, color: "#f2b562", alpha: .9 } });
      app.proteinViewer.addLabel(`Pocket ${pocket.rank}`, { position: { x: center[0], y: center[1], z: center[2] }, backgroundColor: "#fff7e9", fontColor: "#bd7a1f", fontSize: 11, inFront: true });
    }
    const selections = (pocket.residue_ids || []).map((value) => {
      const match = String(value).match(/^([A-Za-z0-9])[: ]?(\d+)/); return match ? { chain: match[1], resi: Number(match[2]) } : null;
    }).filter(Boolean);
    selections.forEach((selection) => app.proteinViewer.setStyle(selection, { stick: { radius: .18, colorscheme: "Jmol" }, cartoon: { color: "#f2b562" } }));
    app.proteinViewer.render();
  }

  function renderLigandChoices() {
    const ligands = app.state.ligands || [];
    const select = $("ligand-select");
    const selected = select.value;
    select.innerHTML = ligands.length ? ligands.map((ligand, index) => `<option value="${index}">${esc(ligand.ligand_id)}</option>`).join("") : `<option value="">No ligands</option>`;
    if (selected && ligands[Number(selected)]) select.value = selected;
    if (ligands.length && $("view-ligand").classList.contains("active") && !app.ligandViewer) loadLigand(Number(select.value || 0));
  }

  function ligandArtifacts(ligandId) {
    const all = Object.values(app.state.artifacts || {}).flat();
    return all.filter((item) => (item.metadata?.ligand_id === ligandId || item.metadata?.identifier === ligandId || item.path.includes(`/ligand/${ligandId}/`)) && ["prepared_ligand_sdf", "docking_output"].includes(item.kind));
  }

  async function loadLigand(index) {
    const ligand = app.state.ligands?.[Number(index)];
    if (!ligand) return;
    const candidates = ligandArtifacts(ligand.ligand_id);
    const artifact = candidates.find((item) => item.kind === "prepared_ligand_sdf") || candidates.find((item) => item.metadata?.relative_output?.endsWith("/poses.sdf"));
    const reportLigand = (app.state.report?.ligands || []).find((item) => item.ligand_id === ligand.ligand_id);
    $("ligand-identity").textContent = ligand.ligand_id;
    $("ligand-file").textContent = artifact?.path || "No 3D artifact";
    renderProperties(reportLigand);
    if (!artifact || !window.$3Dmol) { $("ligand-viewer").innerHTML = `<div class="viewer-placeholder"><i data-lucide="atom"></i><span>No 3D ligand artifact</span></div>`; return; }
    if (!webglAvailable()) { showViewerMessage("ligand-viewer", "WebGL is disabled in this browser. Enable hardware acceleration to view 3D molecules."); return; }
    try {
      const response = await fetch(fileUrl(artifact.path));
      if (!response.ok) throw new Error(`Structure request failed (${response.status})`);
      const text = await response.text();
      $("ligand-viewer").innerHTML = "";
      app.ligandViewer = window.$3Dmol.createViewer($("ligand-viewer"), { backgroundColor: "#0e1116", antialias: true });
      const format = artifact.path.toLowerCase().endsWith(".sdf") ? "sdf" : "pdb";
      app.ligandViewer.addModel(text, format);
      app.ligandViewer.setStyle({}, { stick: { radius: .19, colorscheme: "Jmol" }, sphere: { scale: .28, colorscheme: "Jmol" } });
      app.ligandViewer.zoomTo(); app.ligandViewer.render();
      app.ligandKey = artifact.path;
      $("ligand-atoms").textContent = reportLigand?.properties?.heavy_atoms ? `${reportLigand.properties.heavy_atoms} heavy atoms` : "3D conformer";
    } catch (error) { showViewerMessage("ligand-viewer", `Unable to load ligand: ${error.message || String(error) || "3D renderer failed"}`); }
  }

  function webglAvailable() {
    try { const canvas = document.createElement("canvas"); return Boolean(canvas.getContext("webgl") || canvas.getContext("experimental-webgl")); }
    catch (_) { return false; }
  }

  function showViewerMessage(id, message) {
    $(id).innerHTML = `<div class="viewer-placeholder viewer-error"><span>${esc(message)}</span></div>`;
  }

  function renderProperties(item) {
    const props = item?.properties;
    $("ligand-quality").textContent = props ? (item.status === "succeeded" ? "QC PASS" : String(item.status || "reported").toUpperCase()) : "NO REPORT";
    if (!props) { $("ligand-properties").innerHTML = `<div class="empty-state">No assessment report.</div>`; $("ligand-alerts").innerHTML = ""; return; }
    const rows = [["分子量", `${fmt(props.mw)} Da`], ["cLogP", fmt(props.logp)], ["TPSA", `${fmt(props.tpsa)} Å²`], ["氢键供体 / 受体", `${props.hbd ?? "—"} / ${props.hba ?? "—"}`], ["QED", fmt(props.qed, 3)], ["Csp3 比例", fmt(props.fraction_csp3, 3)], ["可旋转键", props.rotatable_bonds ?? "—"], ["Lipinski", (props.lipinski_violations || []).length ? `${props.lipinski_violations.length} 项超限` : "通过"]];
    $("ligand-properties").innerHTML = rows.map(([label, value]) => `<div class="property-row"><label>${label}</label><strong>${esc(value)}</strong></div>`).join("");
    const alerts = [...(props.alerts?.pains || []).map((item) => `PAINS · ${item}`), ...(props.alerts?.brenk || []).map((item) => `Brenk · ${item}`)];
    $("ligand-alerts").innerHTML = alerts.length ? alerts.map((item) => `<div class="alert"><i data-lucide="triangle-alert"></i>${esc(item)}</div>`).join("") : `<div class="alert" style="color:var(--teal)"><i data-lucide="circle-check"></i>未发现 PAINS / Brenk 风险</div>`;
  }

  function renderResults() {
    const report = app.state.report || {};
    const tasks = report.tasks || app.state.tasks || [];
    const ligands = report.ligands || [];
    const completed = tasks.filter((task) => task.status === "succeeded").length;
    const pockets = currentPockets();
    const metrics = [["对接任务", `${completed}/${tasks.length}`, "flask-conical"], ["候选口袋", pockets.length, "target"], ["最佳评分", tasks.length ? fmt(Math.min(...tasks.map((task) => Number(task.best_pose?.affinity_kcal_mol ?? task.score ?? NaN)).filter(Number.isFinite)), 2) : "—", "trending-down"], ["小分子", ligands.length || app.state.ligands?.length || 0, "atom"]];
    $("metric-row").innerHTML = metrics.map(([label, value, icon]) => `<div class="metric"><div><label>${label}</label><strong>${esc(value)}</strong></div><span class="metric-icon"><i data-lucide="${icon}"></i></span></div>`).join("");
    const firstTask = tasks.find((task) => task.best_pose || task.score != null) || tasks[0];
    const score = firstTask ? (firstTask.best_pose?.affinity_kcal_mol ?? firstTask.score_kcal_mol ?? firstTask.score) : null;
    const ligandName = firstTask?.state_id || firstTask?.ligand_id || ligands[0]?.ligand_id || "当前配体";
    const engine = firstTask?.engine || firstTask?.engine_version || report.engine || "对接引擎";
    const sourceArtifact = artifactsByKind("assessment_data").at(-1) || artifactsByKind("docking_output").at(-1);
    $("result-explanation").innerHTML = `<div class="explanation-item"><span>它是什么</span><strong>${esc(ligandName)} 的对接姿态${score != null ? `，评分 ${fmt(score, 3)} kcal/mol` : "，等待评分"}</strong></div><div class="explanation-item"><span>它是怎么得到的</span><strong>来自当前项目的结构、口袋和配体产物；${esc(engine)} 的版本与参数记录在运行详情中。</strong><button type="button" class="explanation-link" data-provenance-artifact="${esc(sourceArtifact?.artifact_id || "")}">查看来源链</button></div><div class="explanation-item"><span>下一步可以做什么</span><div class="explanation-actions"><button type="button" class="secondary-button" data-open-view="structure">查看姿态</button><button type="button" class="secondary-button" data-open-view="review">审核口袋</button></div></div>`;
    $("result-explanation").querySelectorAll("[data-open-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.openView)));
    $("result-explanation").querySelectorAll("[data-provenance-artifact]").forEach((button) => button.addEventListener("click", () => loadProvenance(button.dataset.provenanceArtifact)));
    $("result-attempt").textContent = report.docking_attempt ? `第 ${report.docking_attempt} 次运行` : "最近一次报告";
    $("task-table").innerHTML = tasks.length ? tasks.map((task, index) => { const score = task.best_pose?.affinity_kcal_mol ?? task.score_kcal_mol ?? task.score; return `<tr data-result-row="${index}" tabindex="0"><td>${esc(task.state_id || task.ligand_id || "—")}</td><td class="mono-cell">${esc(task.task_id || "—")}</td><td class="score">${fmt(score, 3)} kcal/mol</td><td>${esc(task.qc_pass_count ?? "—")}</td><td><span class="state-tag ${task.status === "succeeded" || task.status === "complete" ? "success" : task.status === "failed" ? "failed" : ""}">${esc(task.status || "—")}</span></td></tr>`; }).join("") : `<tr><td colspan="5" class="muted">暂无对接任务。</td></tr>`;
    $("property-table").innerHTML = ligands.length ? ligands.map((item) => { const p = item.properties || {}; const alerts = (p.alerts?.pains || []).length + (p.alerts?.brenk || []).length; return `<tr><td>${esc(item.ligand_id)}</td><td>${fmt(p.mw)}</td><td>${fmt(p.logp)}</td><td>${fmt(p.tpsa)}</td><td>${fmt(p.qed, 3)}</td><td>${alerts ? `<span style="color:var(--amber)">${alerts}</span>` : `<span style="color:var(--teal)">0</span>`}</td></tr>`; }).join("") : `<tr><td colspan="6" class="muted">No molecular assessment reported.</td></tr>`;
    $("limitations-list").innerHTML = (report.limitations || ["No assessment report is available."]).map((item) => `<div class="limitation">${esc(item)}</div>`).join("");
    document.querySelectorAll("[data-result-row]").forEach((row) => row.addEventListener("click", () => showView("structure")));
  }

  async function loadProvenance(artifactId) {
    const panel = $("result-provenance");
    if (!panel) return;
    panel.hidden = false;
    panel.innerHTML = `<div class="provenance-heading"><div><div class="eyebrow">PROVENANCE</div><strong>正在读取来源链…</strong></div></div>`;
    try {
      const query = artifactId ? `?artifact_id=${encodeURIComponent(artifactId)}` : "";
      const response = await fetch(`/api/provenance${query}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.provenance = data;
      const chain = data.chain || [];
      const stages = data.stages || [];
      panel.innerHTML = `<div class="provenance-heading"><div><div class="eyebrow">PROVENANCE</div><strong>${esc(data.run_id || "当前运行")} · ${chain.length} 个来源产物</strong></div><button type="button" class="icon-button" data-close-provenance title="收起来源链" aria-label="收起来源链"><i data-lucide="x"></i></button></div><div class="provenance-chain">${chain.length ? chain.map((item, index) => `<div class="provenance-item"><span class="provenance-index">${index + 1}</span><div><strong>${esc(item.kind || "artifact")}</strong><small>${esc(item.stage || "—")} · ${esc(item.path || "—")}</small></div><span class="provenance-state ${item.exists ? "ok" : "missing"}">${item.exists ? "存在" : "缺失"}</span></div>`).join("") : `<div class="empty-state">没有找到可追溯产物。</div>`}</div><div class="provenance-stages">${stages.map((item) => `<span>${esc(item.stage)} · ${esc(stageStatusLabel(item.status))}</span>`).join("")}</div>`;
      panel.querySelector("[data-close-provenance]")?.addEventListener("click", () => { panel.hidden = true; });
      if (window.lucide) window.lucide.createIcons();
    } catch (error) {
      panel.innerHTML = `<div class="provenance-heading"><strong>来源链读取失败</strong><button type="button" class="icon-button" data-close-provenance title="收起来源链" aria-label="收起来源链"><i data-lucide="x"></i></button></div><div class="empty-state">${esc(error.message)}</div>`;
      panel.querySelector("[data-close-provenance]")?.addEventListener("click", () => { panel.hidden = true; });
      if (window.lucide) window.lucide.createIcons();
    }
  }

  function renderReview() {
    const pending = ["waiting_approval", "waiting_input"].includes(app.state.status) || app.state.stage_results?.docking?.status === "stale";
    $("review-state").textContent = pending ? "有待审核" : "暂无待审核";
    const history = app.state.human_decisions || [];
    $("decision-history").innerHTML = history.length ? history.slice().reverse().map((item) => `<div class="decision-row"><span class="decision-time">${esc(item.timestamp || "—")}</span><span class="decision-actor">${esc(item.actor || "审核人")} · ${esc(item.action || "决策")}</span><span class="decision-reason">${esc(item.reason || "—")}</span></div>`).join("") : `<div class="empty-state">暂无人工决策记录。</div>`;
  }

  async function submitReview() {
    const action = $("review-action").value;
    const payload = { stage: "docking_box", action, actor: $("review-actor").value, reason: $("review-reason").value };
    if (action === "select" || action === "approve") payload.pocket_rank = app.selectedPocket?.rank;
    if (action === "override") payload.center = ["x", "y", "z"].map((axis) => Number($(`center-${axis}`).value));
    if (action === "override") payload.size = ["x", "y", "z"].map((axis) => Number($(`size-${axis}`).value));
    try { const response = await fetch("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); const data = await response.json(); if (!response.ok) throw new Error(data.error); toast("Human decision recorded"); await fetchState(); } catch (error) { toast(error.message, true); }
  }

  async function continueWorkflow() {
    try { const response = await fetch("/api/continue", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model: $("chat-model").value.trim(), conversation_id: app.chatId }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error); toast(data.status === "already_running" ? "Workflow is already running" : data.status === "already_complete" ? "Workflow is already complete" : "Workflow started"); await fetchState(); } catch (error) { toast(error.message, true); }
  }

  function toast(message, error = false) { const node = $("toast"); node.textContent = message; node.className = `toast show ${error ? "error" : ""}`; window.clearTimeout(node._timer); node._timer = window.setTimeout(() => node.classList.remove("show"), 3500); }

  function showView(viewName) {
    const currentDrawer = document.querySelector(".view.active:not(#view-project)")?.id?.replace("view-", "");
    if (viewName !== "project" && currentDrawer === viewName) viewName = "project";
    const activeTool = viewName === "workflow" ? "project" : viewName;
    document.body.classList.toggle("drawer-open", viewName !== "project");
    document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === activeTool));
    document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${viewName}`));
    if (viewName === "structure") {
      if (app.proteinViewer) window.setTimeout(() => app.proteinViewer.resize(), 40); else loadProtein();
    } else if (viewName === "ligand") {
      if (app.ligandViewer) window.setTimeout(() => app.ligandViewer.resize(), 40); else loadLigand(Number($("ligand-select").value || 0));
    } else if (viewName === "files") {
      refreshProjectFiles();
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
    document.querySelectorAll("[data-open-view]").forEach((button) => button.addEventListener("click", () => {
      document.querySelector(`[data-view="${button.dataset.openView}"]`)?.click();
    }));
    $("new-chat").addEventListener("click", newChat);
    $("context-backdrop")?.addEventListener("click", () => showView("project"));
    document.querySelectorAll("[data-chat-filter]").forEach((button) => button.addEventListener("click", () => {
      app.chatFilter = button.dataset.chatFilter;
      renderChatList();
    }));
    $("open-terminal").addEventListener("click", openTerminalDock);
    $("close-terminal").addEventListener("click", closeTerminalDock);
    $("refresh-button").addEventListener("click", () => { fetchState(); fetchProjects(); }); $("workflow-refresh").addEventListener("click", fetchState); $("workflow-continue").addEventListener("click", continueWorkflow); $("workflow-pause").addEventListener("click", () => sendControl("pause")); $("workflow-cancel").addEventListener("click", () => sendControl("cancel")); $("rerun-stage").addEventListener("click", rerunFromSelectedStage); $("save-pricing").addEventListener("click", savePricing); $("run-selector").addEventListener("change", (event) => selectRun(event.target.value)); $("project-selector").addEventListener("change", (event) => selectProject(event.target.value)); $("reset-protein").addEventListener("click", () => { if (app.proteinViewer) { app.proteinViewer.removeAllShapes(); app.proteinViewer.zoomTo(); app.proteinViewer.render(); } });
    $("refresh-files").addEventListener("click", refreshProjectFiles); $("save-file").addEventListener("click", saveProjectFile); $("restart-terminal").addEventListener("click", restartTerminal); $("chat-form").addEventListener("submit", sendChat); $("new-file").addEventListener("click", async () => { const name = window.prompt("New project-relative file name"); if (!name) return; const path = [app.fileDirectory, name].filter(Boolean).join("/"); try { const response = await fetch("/api/project/file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path, content: "" }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText); await refreshProjectFiles(); await openProjectFile(data.path); } catch (error) { toast(error.message, true); } });
    $("chat-model").addEventListener("change", (event) => localStorage.setItem("dock-agent-chat-model", event.target.value.trim()));
    $("open-model-settings").addEventListener("click", () => {
      $("model-settings-dialog").showModal();
      if (!app.modelConfig) fetchModelConfig();
    });
    $("model-settings-form").addEventListener("submit", saveModelConfig);
    document.querySelectorAll("[data-close-model-settings]").forEach((button) => button.addEventListener("click", () => $("model-settings-dialog").close()));
    $("chat-temperature").addEventListener("input", (event) => {
      $("chat-temperature-value").value = event.target.value;
      localStorage.setItem("dock-agent-chat-temperature", event.target.value);
    });
    $("auth-form").addEventListener("submit", async (event) => { event.preventDefault(); const token = $("auth-token").value; const ok = await authenticate(token); if (ok) { $("auth-error").textContent = ""; initializeWorkspace(); } else { $("auth-error").textContent = "Token did not match this server."; } });
    $("file-editor").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveProjectFile(); } });
    $("chat-input").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); $("chat-form").requestSubmit(); } });
    document.querySelectorAll("[data-protein-style]").forEach((button) => button.addEventListener("click", () => setProteinStyle(button.dataset.proteinStyle)));
    $("structure-select").addEventListener("change", async (event) => { app.structureArtifact = structureCandidates()[Number(event.target.value)]; if (app.proteinViewer) { app.proteinViewer = null; } await loadProtein(); });
    $("pose-select").addEventListener("change", async () => { if (app.proteinViewer) { app.proteinViewer = null; } await loadProtein(); });
    $("ligand-select").addEventListener("change", (event) => { app.ligandViewer = null; app.ligandKey = null; loadLigand(event.target.value); }); $("submit-review").addEventListener("click", submitReview); $("continue-button").addEventListener("click", continueWorkflow);
    $("workflow-zoom-out").addEventListener("click", () => zoomWorkflowCanvas(-.1));
    $("workflow-zoom-in").addEventListener("click", () => zoomWorkflowCanvas(.1));
    $("workflow-fit").addEventListener("click", fitWorkflowCanvas);
    $("conversation-workflow").addEventListener("toggle", () => {
      if ($("conversation-workflow").open) window.setTimeout(() => { fitWorkflowCanvas(); renderWorkflow(); }, 40);
    });
    window.addEventListener("resize", () => { if ($("terminal-dock").classList.contains("open")) resizeTerminal(); });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { window.clearInterval(app.terminalPollTimer); app.terminalPollTimer = null; }
      else if ($("terminal-dock").classList.contains("open")) ensureTerminal();
    });
    const workflowCanvas = $("workflow-canvas");
    workflowCanvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = workflowCanvas.getBoundingClientRect();
      zoomWorkflowCanvas(event.deltaY < 0 ? .06 : -.06, event.clientX - rect.left, event.clientY - rect.top);
    }, { passive: false });
    workflowCanvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      app.workflowCanvas.pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, startX: app.workflowCanvas.x, startY: app.workflowCanvas.y, moved: false };
      workflowCanvas.setPointerCapture(event.pointerId);
    });
    workflowCanvas.addEventListener("pointermove", (event) => {
      const pointer = app.workflowCanvas.pointer;
      if (!pointer || pointer.id !== event.pointerId) return;
      const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) pointer.moved = app.workflowCanvas.moved = true;
      if (pointer.moved) {
        app.workflowCanvas.x = pointer.startX + dx;
        app.workflowCanvas.y = pointer.startY + dy;
        applyWorkflowCanvas();
      }
    });
    const endCanvasPan = (event) => {
      const pointer = app.workflowCanvas.pointer;
      if (!pointer || pointer.id !== event.pointerId) return;
      app.workflowCanvas.pointer = null;
      if (pointer.moved) window.setTimeout(() => { app.workflowCanvas.moved = false; }, 0);
    };
    workflowCanvas.addEventListener("pointerup", endCanvasPan);
    workflowCanvas.addEventListener("pointercancel", endCanvasPan);
    bootWorkspace();
    window.setInterval(updateWorkflowElapsed, 1000);
  });
})();
