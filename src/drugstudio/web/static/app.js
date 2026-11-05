/* DrugStudio workbench: scientific data comes from the curated /api/state snapshot. */
(function () {
  "use strict";

  const app = { state: null, events: [], eventSource: null, proteinViewer: null, proteinStyle: "cartoon", ligandViewer: null, ligandKey: null, selectedPocket: null, structureArtifact: null, selectedStage: null, pricing: {}, provenance: null, workflowCanvas: { x: 0, y: 0, scale: 1, ready: false, pointer: null, moved: false }, projectId: ".", fileDirectory: "", filePath: "", terminalCwd: "", terminalSession: null, terminalCursor: 0, terminalPollTimer: null, terminalInputBuffer: "", terminalInputTimer: null, terminal: null, terminalFit: null, chatHistory: [], chatSessions: [], chatId: null, chatBusy: false, chatFilter: "active", modelConfig: null, codeEditor: null, runKey: "",
    boxVisible: false, boxShapes: [], structureAtoms: [],
    parameters: null, parameterEdits: {}, workflowBuilt: false, stagePickedByUser: false, pendingAction: null };
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
  // Every user-facing string goes through t(). Source strings are Chinese,
  // matching the primary audience, and MESSAGES holds the tables for other
  // locales; t() passes a string through when no translation exists. The
  // seam matters more than the table: before this, half the interface was
  // Chinese and half was whatever the original author typed in English,
  // with no single place to see or change it.
  const MESSAGES = {
    en: {
      "开始": "Start", "完成": "Done", "查看日志": "View logs", "查看参数": "Parameters",
      "从此处重跑": "Rerun from here", "接管节点": "Take over", "等待事件": "Waiting for events",
      "模型已连接": "Model connected", "模型未连接": "Model not connected",
    },
  };
  const LOCALE = (document.documentElement.lang || "zh-CN").slice(0, 2);
  const t = (text) => MESSAGES[LOCALE]?.[text] ?? text;

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
  const fmt = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
  // The server keeps no "current project" or "current run": every request
  // carries its own scope, so two tabs can sit on different runs.
  const scoped = (path, extra = {}) => {
    const url = new URL(path, window.location.origin);
    const params = { project_id: app.projectId, run_key: app.runKey, ...extra };
    Object.entries(params).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") url.searchParams.set(key, value);
    });
    return url.pathname + url.search;
  };
  const withScope = (body = {}) => JSON.stringify({
    project_id: app.projectId || undefined, run_key: app.runKey || undefined, ...body
  });
  const postJson = (path, body) => fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: withScope(body)
  });
  const fileUrl = (path) => scoped("/api/file", { path });

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
    } catch (error) { toast(error.message || t("无法连接到工作台"), true); }
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
    const [mode, mime, label] = editorModes[ext] || ["", "null", t("纯文本")];
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
      const response = await fetch(scoped("/api/model-config"), { cache: "no-store" });
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
      const response = await postJson("/api/model-config", {
          base_url: $("model-base-url").value.trim(),
          model: $("chat-model").value.trim(),
          api_key: $("model-api-key").value,
          clear_api_key: $("clear-model-api-key").checked
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

  // 3Dmol keeps a live WebGL context per viewer.  Dropping the reference
  // without clearing it leaks the context, and browsers cap them at ~16, so
  // switching run or structure often enough used to blank the canvas.
  function disposeViewers() {
    [["proteinViewer", "protein-viewer"], ["ligandViewer", "ligand-viewer"]].forEach(([key, id]) => {
      try { app[key]?.clear?.(); } catch (_) { /* viewer already torn down */ }
      app[key] = null;
      const host = $(id);
      if (host) host.innerHTML = "";
    });
    app.ligandKey = null;
    app.boxShapes = [];
    app.structureAtoms = [];
  }

  async function fetchState() {
    try {
      const response = await fetch(scoped("/api/state"), { cache: "no-store" });
      if (!response.ok) throw new Error((await response.json()).error || response.statusText);
      app.state = await response.json();
      if (!app.runKey && app.state.run_key) app.runKey = app.state.run_key;
      if (!app.projectId && app.state.project?.project_id) app.projectId = app.state.project.project_id;
      setConnection(true);
      fetchParameters();
      renderAll();
      connectEvents();
      fetchRuns();
    } catch (error) {
      setConnection(false);
      toast(error.message || t("无法载入运行数据"), true);
    }
  }

  function setConnection(online) {
    $("connection-dot").classList.toggle("offline", !online);
    $("connection-label").textContent = online ? t("已连接") : t("已断开");
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
    app.eventSource = new EventSource(scoped("/api/events", { since: app.events.at(-1)?.event_id || 0 }));
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

  const ATTENTION_STATUSES = ["running", "waiting_approval", "waiting_input", "waiting_execution", "failed"];

  function statusClassFor(status) {
    if (status === "succeeded" || status === "completed") return "success";
    if (status === "failed") return "failed";
    if (status.includes("waiting")) return "waiting";
    if (status === "running") return "running";
    return "pending";
  }

  // Build the graph once. Rebuilding innerHTML on every event -- twice a
  // second during a run -- dropped keyboard focus, restarted the status
  // animations and rebound every listener. Updates now patch text and
  // classes in place, so a node the user is interacting with stays put.
  function buildWorkflowGraph() {
    const world = $("workflow-world");
    const action = (name, iconName, title, stage) =>
      `<button type="button" class="stage-action" data-stage-action="${name}" data-stage="${stage}" title="${esc(title)}" aria-label="${esc(title)}"><i data-lucide="${iconName}"></i></button>`;
    const cards = workflowNodes.map(([stage, label, icon], index) => `${
      index ? `<div class="workflow-arrow" data-arrow-after="${esc(workflowNodes[index - 1][0])}"><i data-lucide="arrow-right"></i></div>` : ""
    }<article class="workflow-stage-node pending" data-workflow-stage="${esc(stage)}" tabindex="0" role="button" aria-pressed="false"><div class="workflow-node-icon"><i data-lucide="${icon}"></i></div><strong>${esc(label)}</strong><span data-stage-status></span><em data-stage-progress hidden></em><div class="workflow-stage-actions">${
      action("logs", "scroll-text", t("查看日志"), stage)
    }${action("params", "sliders-horizontal", t("查看参数"), stage)
    }${action("rerun", "rotate-ccw", t("从此处重跑"), stage)
    }${action("takeover", "hand", t("接管节点"), stage)}</div></article>`).join("");
    world.innerHTML = `<div class="workflow-entry">${t("开始")}</div>${cards}<div class="workflow-entry workflow-end">${t("完成")}</div>`;

    // One delegated listener per interaction, bound once for the life of the
    // page rather than re-attached on every repaint.
    world.addEventListener("click", (event) => {
      const button = event.target.closest("[data-stage-action]");
      if (button) {
        event.stopPropagation();
        handleStageAction(button.dataset.stageAction, button.dataset.stage);
        return;
      }
      const node = event.target.closest("[data-workflow-stage]");
      if (node && !app.workflowCanvas.moved) selectWorkflowStage(node.dataset.workflowStage);
    });
    world.addEventListener("keydown", (event) => {
      const node = event.target.closest("[data-workflow-stage]");
      if (!node || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      selectWorkflowStage(node.dataset.workflowStage);
    });
    app.workflowBuilt = true;
    if (window.lucide) window.lucide.createIcons();
  }

  // A click is a deliberate choice; the automatic follow must not override it.
  function selectWorkflowStage(stage) {
    app.stagePickedByUser = true;
    inspectWorkflowStage(stage);
    updateWorkflowNodes();
  }

  function updateWorkflowNodes() {
    const world = $("workflow-world");
    if (!world || !app.workflowBuilt) return;
    workflowNodes.forEach(([stage]) => {
      const node = world.querySelector(`[data-workflow-stage="${stage}"]`);
      if (!node) return;
      const status = stageStatus(stage);
      const className = statusClassFor(status);
      // Only touch the DOM when the value actually changed, so a running
      // node's animation is not restarted by every unrelated event.
      if (!node.classList.contains(className)) {
        node.classList.remove("pending", "running", "success", "failed", "waiting");
        node.classList.add(className);
      }
      const selected = app.selectedStage === stage;
      node.classList.toggle("selected", selected);
      node.setAttribute("aria-pressed", String(selected));
      const label = node.querySelector("[data-stage-status]");
      const text = stageStatusLabel(status);
      if (label && label.textContent !== text) label.textContent = text;
      const progress = stageProgress(stage, status);
      const slot = node.querySelector("[data-stage-progress]");
      if (slot) {
        if (slot.textContent !== progress) slot.textContent = progress;
        slot.hidden = !progress;
      }
      const arrow = world.querySelector(`[data-arrow-after="${stage}"]`);
      if (arrow) arrow.classList.toggle("success", className === "success");
    });
  }

  function renderWorkflow() {
    if (!app.state || !$("workflow-canvas")) return;
    if (!app.workflowBuilt) buildWorkflowGraph();
    updateWorkflowNodes();
    if (!app.workflowCanvas.ready) fitWorkflowCanvas(); else applyWorkflowCanvas();
    const activeStage = workflowNodes.map(([stage]) => stage)
      .find((stage) => ATTENTION_STATUSES.includes(stageStatus(stage)));
    // Follow the run only until the user picks a node themselves. Otherwise
    // opening one stage's details and having the next event yank you back to
    // whatever is running makes the inspector unusable during a run.
    if (activeStage && !app.stagePickedByUser && app.selectedStage !== activeStage) {
      inspectWorkflowStage(activeStage);
      updateWorkflowNodes();
    }
    const recent = app.events.slice(-1)[0]; $("workflow-updated").textContent = recent ? new Date(recent.timestamp).toLocaleTimeString() : t("等待事件");
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

  // ---- stage parameters --------------------------------------------------
  // Until now "改参数" only raised a toast pointing at a panel that showed
  // no parameters. The form is built from the schema the server serves, so
  // adding a knob in parameters.py is enough to make it editable here.

  async function fetchParameters() {
    try {
      const response = await fetch(scoped("/api/parameters"), { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.parameters = data;
      app.parameterEdits = {};
      renderStageParameters();
    } catch (error) {
      app.parameters = null;
      const fields = $("stage-parameters-fields");
      if (fields) fields.innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
    }
  }

  function parameterValue(key) {
    if (Object.prototype.hasOwnProperty.call(app.parameterEdits, key)) return app.parameterEdits[key];
    return app.parameters?.values?.[key];
  }

  function parameterField(item) {
    const value = parameterValue(item.key);
    const id = `param-${item.key}`;
    const disabled = app.parameters?.editable ? "" : "disabled";
    const changed = app.parameters?.values?.[item.key] !== item.default;
    let control;
    if (item.kind === "bool") {
      control = `<input type="checkbox" id="${id}" data-parameter="${esc(item.key)}" ${value ? "checked" : ""} ${disabled}>`;
    } else if (item.kind === "choice") {
      control = `<select id="${id}" class="input-control" data-parameter="${esc(item.key)}" ${disabled}>${
        item.choices.map((choice) => `<option value="${esc(choice)}" ${choice === value ? "selected" : ""}>${esc(choice)}</option>`).join("")}</select>`;
    } else if (item.kind === "list") {
      control = `<textarea id="${id}" class="input-control" rows="2" data-parameter="${esc(item.key)}" placeholder="每行一个" ${disabled}>${esc((value || []).join("\n"))}</textarea>`;
    } else if (item.kind === "text") {
      control = `<input type="text" id="${id}" class="input-control" data-parameter="${esc(item.key)}" value="${esc(value ?? "")}" ${disabled}>`;
    } else {
      const step = item.kind === "float" ? "0.1" : "1";
      const bounds = `${item.minimum != null ? ` min="${item.minimum}"` : ""}${item.maximum != null ? ` max="${item.maximum}"` : ""}`;
      control = `<input type="number" id="${id}" class="input-control" step="${step}"${bounds} data-parameter="${esc(item.key)}" value="${esc(value ?? "")}" ${disabled}>`;
    }
    return `<div class="parameter-row ${item.kind === "bool" ? "parameter-row-bool" : ""}">
      <label for="${id}">${esc(item.label)}${item.unit ? ` <small>${esc(item.unit)}</small>` : ""}${changed ? `<span class="parameter-changed" title="已偏离默认值">·</span>` : ""}</label>
      ${control}
      ${item.help ? `<p class="parameter-help">${esc(item.help)}</p>` : ""}
    </div>`;
  }

  function renderStageParameters() {
    const section = $("stage-parameters");
    if (!section) return;
    const fields = $("stage-parameters-fields");
    const stage = app.selectedStage;
    const data = app.parameters;
    if (!data) { fields.innerHTML = `<div class="empty-state">${t("参数尚未载入。")}</div>`; return; }
    if (!stage) {
      // "No stage selected" and "this stage has no knobs" are different
      // things; saying the second when nothing is selected reads as a bug.
      fields.innerHTML = `<div class="empty-state">${t("选择一个阶段查看它的参数。")}</div>`;
      $("stage-parameters-actions").hidden = true;
      $("stage-parameters-note").hidden = true;
      return;
    }
    const items = data.parameters.filter((item) => item.stage === stage);
    $("stage-parameters-state").textContent = data.editable ? "" : t("运行中不可修改");
    if (!items.length) {
      // Honest about stages that genuinely expose nothing, rather than
      // showing an empty box that looks broken.
      fields.innerHTML = `<div class="empty-state">${t("这个阶段没有可调参数。")}</div>`;
      $("stage-parameters-actions").hidden = true;
      $("stage-parameters-note").hidden = true;
      return;
    }
    fields.innerHTML = items.map(parameterField).join("");
    fields.querySelectorAll("[data-parameter]").forEach((node) => {
      node.addEventListener("input", () => {
        const key = node.dataset.parameter;
        app.parameterEdits[key] = node.type === "checkbox" ? node.checked : node.value;
        renderParameterNote();
      });
      if (node.tagName === "SELECT") node.addEventListener("change", () => {
        app.parameterEdits[node.dataset.parameter] = node.value;
        renderParameterNote();
      });
    });
    $("stage-parameters-actions").hidden = !data.editable;
    renderParameterNote();
    if (window.lucide) window.lucide.createIcons();
  }

  function pendingParameterKeys() {
    return Object.keys(app.parameterEdits).filter((key) => {
      const current = app.parameters?.values?.[key];
      const edited = app.parameterEdits[key];
      if (typeof current === "boolean") return Boolean(edited) !== current;
      if (Array.isArray(current)) return String(edited).trim() !== current.join("\n");
      if (typeof current === "number") return Number(edited) !== current;
      return String(edited ?? "") !== String(current ?? "");
    });
  }

  function renderParameterNote() {
    const note = $("stage-parameters-note");
    const pending = pendingParameterKeys();
    if (!pending.length) { note.hidden = true; return; }
    // Say what a change costs before it is saved: the stage it invalidates
    // is the one the user will have to rerun from.
    const stages = pending.map((key) => app.parameters.parameters.find((item) => item.key === key)?.stage);
    const order = app.parameters.stage_order || [];
    const earliest = stages.filter(Boolean).sort((a, b) => order.indexOf(a) - order.indexOf(b))[0];
    const label = workflowNodes.find(([name]) => name === earliest)?.[1] || earliest;
    note.hidden = false;
    note.textContent = `保存后需要从「${label}」重跑才会生效。`;
  }

  async function saveParameters() {
    const pending = pendingParameterKeys();
    if (!pending.length) { toast("参数没有改动"); return; }
    const values = { ...app.parameters.values };
    pending.forEach((key) => { values[key] = app.parameterEdits[key]; });
    try {
      const response = await postJson("/api/parameters", { values });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.parameters = data;
      app.parameterEdits = {};
      renderStageParameters();
      const label = workflowNodes.find(([name]) => name === data.affects_from)?.[1];
      toast(label ? `参数已保存，从「${label}」重跑后生效` : "参数已保存");
    } catch (error) { toast(error.message, true); }
  }

  function resetParameters() {
    if (!app.parameters) return;
    const stage = app.selectedStage;
    app.parameters.parameters.filter((item) => item.stage === stage)
      .forEach((item) => { app.parameterEdits[item.key] = item.default; });
    renderStageParameters();
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
      $("stage-parameters")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
    $("workflow-elapsed").textContent = t("耗时") + " " + (start ? duration(start, app.events.at(-1)?.timestamp) : "—");
  }

  function artifactCount(stage) {
    const count = (app.state?.artifacts?.[stage] || []).length;
    return count ? `<em>${count} artifact${count === 1 ? "" : "s"}</em>` : "";
  }

  function duration(start, end) { const seconds = Math.max(0, (new Date(end || Date.now()) - new Date(start)) / 1000); return seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.floor(seconds / 60)}m ${(seconds % 60).toFixed(0)}s`; }

  function inspectWorkflowStage(stage) {
    const changed = app.selectedStage !== stage;
    app.selectedStage = stage;
    if (changed) app.parameterEdits = {};
    const label = workflowNodes.find((item) => item[0] === stage)?.[1] || stage;
    const status = stageStatus(stage); const result = app.state.stage_results?.[stage] || {};
    $("workflow-node-title").textContent = label; $("workflow-node-status").textContent = stageStatusLabel(status);
    $("workflow-node-detail").innerHTML = `<div class="inspector-fact"><span>状态</span><strong>${esc(stageStatusLabel(status))}</strong></div><div class="inspector-fact"><span>产物</span><strong>${(app.state.artifacts?.[stage] || []).length}</strong></div><div class="inspector-fact"><span>进度</span><strong>${esc(stageProgress(stage, status) || "—")}</strong></div><div class="inspector-fact"><span>最近结果</span><strong>${esc(result.error || result.metadata?.profile || result.metadata?.selected_rank || "已记录在运行清单")}</strong></div>`;
    $("rerun-stage").disabled = app.state.server?.workflow_running;
    renderStageParameters();
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
    $("workflow-lineage").innerHTML = artifacts.length ? artifacts.map((item) => `<span class="lineage-item"><i data-lucide="file-output"></i>${esc(item.kind)}<small>${esc(item.path.split("/").pop())}</small></span>`).join(`<i class="lineage-arrow" data-lucide="chevron-right"></i>`) : `<span class="muted">${t("阶段产出后，产物会显示在这里。")}</span>`;
  }

  function renderEventList() {
    const items = app.events.slice(-30).reverse();
    $("event-list").innerHTML = items.length ? items.map((item) => `<div class="event-row"><span class="event-time">${esc(new Date(item.timestamp).toLocaleTimeString())}</span><span class="event-kind ${esc(item.status || "")}">${esc(item.type.replaceAll("_", " "))}</span><span class="event-stage">${esc(item.stage || item.tool || "supervisor")}</span><span class="event-detail">${esc(item.error || item.reason || item.kind || item.model || (item.observation?.status ? `status ${item.observation.status}` : ""))}</span></div>`).join("") : `<div class="empty-state">${t("暂无运行事件。")}</div>`;
  }

  function renderRunFacts() {
    const state = app.state; const facts = [["运行状态", stageStatusLabel(state.status)], ["当前阶段", workflowNodes.find(([stage]) => stage === state.current_node)?.[1] || state.current_node || "空闲"], ["事件", app.events.length], ["产物", Object.values(state.artifacts || {}).flat().length], ["模型调用", app.events.filter((item) => item.type === "model_started").length], ["工具调用", app.events.filter((item) => item.type === "tool_started").length]];
    $("run-facts").innerHTML = facts.map(([label, value]) => `<div class="run-fact"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
  }

  function renderCosts() {
    const costs = app.state.costs || { models: {}, pricing: {} };
    const rows = Object.entries(costs.models || {});
    const total = costs.estimated_usd == null ? t("未设置价格") : `$${Number(costs.estimated_usd).toFixed(6)}`;
    $("cost-summary").innerHTML = `<div class="cost-total"><span>${t("预估模型成本")}</span><strong>${esc(total)}</strong></div><div class="cost-tokens">${Number(costs.input_tokens || 0).toLocaleString()} ${t("输入")} · ${Number(costs.output_tokens || 0).toLocaleString()} ${t("输出 token")}</div>${rows.map(([model, row]) => `<div class="cost-model"><strong>${esc(model)}</strong><span>${Number(row.input_tokens || 0).toLocaleString()} / ${Number(row.output_tokens || 0).toLocaleString()} tokens</span><small>${row.estimated_usd == null ? t("无价格或用量数据") : `$${Number(row.estimated_usd).toFixed(6)}`}</small></div>`).join("")}`;
    const model = app.state.model?.model || rows[0]?.[0] || "";
    const rate = costs.pricing?.[model] || {};
    if (document.activeElement !== $("price-input")) $("price-input").value = rate.input_usd_per_million ?? "";
    if (document.activeElement !== $("price-output")) $("price-output").value = rate.output_usd_per_million ?? "";
    app.pricing.model = model;
  }

  async function fetchRuns() {
    try {
      const response = await fetch(scoped("/api/runs"), { cache: "no-store" }); const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      const runs = data.runs || [];
      $("run-selector").innerHTML = runs.length ? runs.map((run) => {
        const title = `${run.run_key} · ${run.target_id || run.run_id} · ${run.status || "unknown"}`;
        return `<option value="${esc(run.run_key)}" ${run.run_key === app.runKey ? "selected" : ""} title="${esc(title)}">${esc(run.run_key)}</option>`;
      }).join("") : `<option value="">本项目暂无运行</option>`;
      // The run list is scoped to the project, so a run from the project we
      // just left is no longer selectable; fall back to this project's newest.
      if (runs.length && !runs.some((run) => run.run_key === app.runKey)) {
        await selectRun(runs.at(-1).run_key);
      } else if (!runs.length) {
        app.runKey = "";
      }
    } catch (error) { toast(error.message, true); }
  }

  async function fetchProjects() {
    try {
      const response = await fetch(scoped("/api/projects"), { cache: "no-store" }); const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      const selected = data.selected || app.state?.project || {};
      app.projectId = app.projectId || selected.project_id || ".";
      $("project-selector").innerHTML = (data.projects || []).map((project) => `<option value="${esc(project.project_id)}" ${project.project_id === app.projectId ? "selected" : ""}>${esc(project.name)}</option>`).join("");
      $("project-description").textContent = selected.name || app.projectId;
      renderChatStatus();
      await fetchChats();
    } catch (error) { toast(error.message, true); }
  }

  async function selectProject(projectId) {
    try {
      const response = await postJson("/api/select-project", { project_id: projectId });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.projectId = data.project_id;
      app.runKey = "";
      app.eventSource?.close(); app.eventSource = null; app.events = [];
      app.provenance = null; disposeViewers();
      app.fileDirectory = ""; app.filePath = ""; app.terminalCwd = "";
      app.terminalSession = null; app.terminalCursor = 0;
      if (app.terminalPollTimer) window.clearInterval(app.terminalPollTimer); app.terminalPollTimer = null;
      app.terminal?.reset();
      if (app.codeEditor) app.codeEditor.setValue(""); else $("file-editor").value = "";
      $("file-editor").value = ""; $("editor-file-label").textContent = t("未选择文件"); $("editor-language").textContent = "";
      $("editor-empty").hidden = false; $("save-file").disabled = true;
      await fetchProjects();
      await fetchRuns();
      await fetchState();
      toast(`已切换到项目 ${data.name}`);
    } catch (error) { toast(error.message, true); }
  }

  async function refreshProjectFiles() {
    try {
      const response = await fetch(scoped("/api/project/files", { path: app.fileDirectory }), { cache: "no-store" });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      $("file-tree-path").textContent = `/${data.path || ""}`;
      const parent = data.path ? `<button class="file-entry parent-entry" data-file-parent="1"><i data-lucide="corner-left-up"></i><span>..</span></button>` : "";
      const rows = (data.entries || []).map((entry) => {
        const disabled = entry.kind === "file" && entry.previewable === false;
        const ext = entry.name.toLowerCase().split(".").pop();
        const icon = entry.kind === "directory" ? "folder" : disabled ? "file-x-2" : editorModes[ext] ? "file-code-2" : "file-text";
        const title = disabled ? entry.preview_reason || t("无法预览") : entry.kind === "directory" ? t("打开文件夹") : t("打开文件");
        return `<button class="file-entry ${entry.kind} ${disabled ? "unsupported" : ""}" data-file-path="${esc(entry.path)}" data-file-kind="${entry.kind}" ${disabled ? "disabled" : ""} title="${esc(title)}"><i data-lucide="${icon}"></i><span>${esc(entry.name)}</span>${entry.kind === "file" ? `<small>${formatBytes(entry.size)}${disabled ? " · 不可预览" : ""}</small>` : ""}</button>`;
      }).join("");
      $("file-tree").innerHTML = parent + rows || `<div class="empty-state">${t("此文件夹为空")}</div>`;
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
      const response = await fetch(scoped("/api/project/file", { path })); const data = await response.json();
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
      const response = await postJson("/api/project/file", { path: app.filePath, content });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      toast(`${t("已保存")} ${data.path}`); refreshProjectFiles();
    } catch (error) { toast(error.message, true); }
  }

  function createTerminal() {
    if (app.terminal) return true;
    if (!window.Terminal || !window.FitAddon?.FitAddon) {
      $("terminal-screen").textContent = t("终端组件加载失败。");
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
      const response = await postJson("/api/project/terminal/start", { cwd: app.terminalCwd });
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
      await postJson("/api/project/terminal/resize", { session_id: app.terminalSession, columns: app.terminal.cols, rows: app.terminal.rows });
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
        const response = await postJson("/api/project/terminal/input", { session_id: app.terminalSession, input });
        const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      } catch (error) { app.terminal?.writeln(`\r\n\x1b[31m${error.message}\x1b[0m`); }
    }, 25);
  }

  async function pollTerminal() {
    if (!app.terminalSession) return;
    try {
      const response = await fetch(scoped("/api/project/terminal/output", { session_id: app.terminalSession, since: app.terminalCursor }), { cache: "no-store" });
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
      await postJson("/api/project/terminal/stop", { session_id: app.terminalSession });
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
    const response = await postJson("/api/chats", { project_id: app.projectId });
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
    const response = await postJson("/api/chats/save",
      { project_id: app.projectId, chat_id: app.chatId, messages: app.chatHistory });
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
      const response = await postJson("/api/chats/archive",
        { project_id: app.projectId, chat_id: chatId, archived });
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
    $("chat-messages").insertAdjacentHTML("beforeend", pendingActionCard());
    $("approve-action")?.addEventListener("click", () => decideChatAction(true));
    $("reject-action")?.addEventListener("click", () => decideChatAction(false));
    if (window.lucide) window.lucide.createIcons();
    $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
    const current = app.chatSessions.find((item) => item.chat_id === app.chatId);
    $("project-title").textContent = current?.title || t("新对话");
  }

  // A turn can come back suspended: the model asked to run a command or
  // write a file, and nothing has happened yet. The card is the gate, so it
  // lives in the transcript rather than as a toast that can be missed.
  function absorbChatReply(history, data) {
    if (data.reply) history.push({ role: "assistant", content: data.reply, actions: data.actions || [] });
    app.pendingAction = data.pending || null;
    if (data.pending && !data.reply) {
      history.push({ role: "assistant", content: t("需要你确认下面的操作。"), actions: data.actions || [] });
    }
  }

  function pendingActionCard() {
    const pending = app.pendingAction;
    if (!pending) return "";
    const isCommand = pending.tool === "run_command";
    const detail = isCommand
      ? `<pre class="pending-command">${esc(pending.command)}</pre>${pending.cwd ? `<div class="pending-meta">${t("工作目录")}: ${esc(pending.cwd)}</div>` : ""}`
      : `<div class="pending-meta">${t("写入文件")}: <code>${esc(pending.path)}</code> · ${pending.content_bytes} ${t("字节")}</div>${pending.content_preview ? `<pre class="pending-command">${esc(pending.content_preview)}</pre>` : ""}`;
    return `<article class="pending-action" id="pending-action">
      <div class="pending-heading"><i data-lucide="shield-alert"></i><strong>${t(isCommand ? "AI 请求执行命令" : "AI 请求写入文件")}</strong></div>
      ${detail}
      <label class="pending-reason">${t("理由（拒绝时会转达给 AI）")}<input id="pending-reason" class="input-control" type="text" placeholder="${t("可选")}"></label>
      <div class="pending-actions">
        <button class="secondary-button" type="button" id="reject-action">${t("拒绝")}</button>
        <button class="primary-button" type="button" id="approve-action">${t("批准并执行")}</button>
      </div>
    </article>`;
  }

  async function decideChatAction(approved) {
    const pending = app.pendingAction;
    if (!pending || app.chatBusy) return;
    const reason = $("pending-reason")?.value || "";
    app.chatBusy = true; app.pendingAction = null;
    $("chat-status").textContent = approved ? t("正在执行…") : t("已拒绝");
    renderChat();
    const history = app.chatHistory;
    try {
      const response = await postJson("/api/project/chat/decide",
        { action_id: pending.action_id, approved, reason });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      history.push({ role: "user", content: approved ? t("（已批准该操作）") : `${t("（已拒绝该操作）")}${reason ? ` ${reason}` : ""}` });
      absorbChatReply(history, data);
    } catch (error) {
      history.push({ role: "assistant", content: `${t("无法回复")}：${error.message}` });
    }
    app.chatBusy = false;
    renderChatStatus();
    try { await persistChat(); } catch (error) { toast(error.message, true); }
    renderChat();
  }

  async function sendChat(event) {
    event.preventDefault();
    const input = $("chat-input"), content = input.value.trim(); if (!content || app.chatBusy) return;
    const projectId = app.projectId, history = app.chatHistory;
    if (app.pendingAction) {
      // Moving on without answering is itself an answer. Record it as a
      // refusal so the audit trail does not just lose the request, and so
      // the suspended call cannot be resumed later.
      const abandoned = app.pendingAction;
      app.pendingAction = null;
      postJson("/api/project/chat/decide", {
        action_id: abandoned.action_id, approved: false, resume: false,
        reason: t("用户未处理该请求，改为发送了新消息"),
      }).catch(() => { /* the request had already expired */ });
    }
    history.push({ role: "user", content }); input.value = ""; app.chatBusy = true; $("send-chat").disabled = true; renderChat();
    $("chat-status").textContent = t("Agent 正在处理");
    $("chat-status").classList.remove("chat-status-offline");
    try { await persistChat(); } catch (error) { toast(error.message, true); }
    try {
      const response = await postJson("/api/project/chat", {
        messages: history, chat_id: app.chatId,
        model: $("chat-model").value.trim(), temperature: Number($("chat-temperature").value)
      });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      absorbChatReply(history, data);
    } catch (error) { history.push({ role: "assistant", content: `${t("无法回复")}：${error.message}` }); }
    app.chatBusy = false; $("send-chat").disabled = false;
    renderChatStatus();
    if (app.projectId === projectId) {
      try { await persistChat(); } catch (error) { toast(error.message, true); }
    }
    if (app.projectId === projectId) { app.chatHistory = history; renderChat(); }
  }

  async function selectRun(runKey) {
    try {
      const response = await postJson("/api/select-run", { run_key: runKey });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      // Selection is client state now; the server only confirmed the key.
      app.runKey = data.selected || runKey;
      if (data.project_id) app.projectId = data.project_id;
      // A node chosen in the previous run says nothing about this one.
      app.stagePickedByUser = false;
      app.eventSource?.close(); app.eventSource = null; app.events = []; app.provenance = null;
      disposeViewers();
      $("result-provenance")?.setAttribute("hidden", "");
      await fetchState(); toast(`${t("已选择运行")} ${app.runKey}`);
    } catch (error) { toast(error.message, true); await fetchRuns(); }
  }

  async function sendControl(action) {
    try {
      const response = await postJson("/api/control", { action });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      toast(action === "pause" ? t("已请求暂停，将在下一个安全检查点生效") : t("已请求取消，将在下一个安全检查点生效"));
      await fetchState();
    } catch (error) { toast(error.message, true); }
  }

  async function rerunFromSelectedStage() {
    if (!app.selectedStage) return;
    try {
      const response = await postJson("/api/rerun", { stage: app.selectedStage });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.eventSource?.close(); app.eventSource = null; app.events = []; app.provenance = null;
      disposeViewers();
      await selectRun(data.run_key || data.run_id);
      await continueWorkflow();
    } catch (error) { toast(error.message, true); }
  }

  async function savePricing() {
    try {
      if (!$("price-input").value.trim() || !$("price-output").value.trim()) throw new Error(t("请同时填写输入和输出的价格"));
      const payload = { model: app.pricing.model, input_usd_per_million: Number($("price-input").value), output_usd_per_million: Number($("price-output").value) };
      const response = await postJson("/api/pricing", payload);
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.state.costs = data; renderCosts(); toast(t("模型价格已保存到本次运行"));
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
      onBoxInputChanged();
    }
    if (rerender) renderPockets();
  }

  function renderPocketDetail() {
    const pocket = app.selectedPocket;
    if (!pocket) {
      const residues = app.state.target?.binding_site_residues || [];
      $("pocket-detail").innerHTML = residues.length
        ? `<div class="detail-item"><label>REFERENCE SITE</label><strong>Provided in target input</strong></div><div class="detail-item"><label>RESIDUES</label><strong>${residues.length}</strong></div><div class="residue-list">${residues.map((item) => `<span class="residue-chip">${esc(item)}</span>`).join("")}</div>`
        : `<span class="muted">${t("没有预测口袋或参考位点残基。")}</span>`;
      return;
    }
    const center = pocket.box?.center || pocket.center || [];
    const size = pocket.box?.size || [];
    $("pocket-detail").innerHTML = `<div class="detail-item"><label>CENTER X / Y / Z</label><strong class="mono">${center.map((n) => fmt(n, 2)).join(" · ") || "—"}</strong></div><div class="detail-item"><label>BOX Å</label><strong class="mono">${size.map((n) => fmt(n, 1)).join(" · ") || "—"}</strong></div><div class="detail-item"><label>PROBABILITY</label><strong>${fmt(pocket.probability, 3)}</strong></div><div class="detail-item"><label>SURFACE POINTS</label><strong>${esc(pocket.surface_points ?? "—")}</strong></div><div class="residue-list">${(pocket.residue_ids || []).map((item) => `<span class="residue-chip">${esc(item)}</span>`).join("") || `<span class="muted">${t("无残基标签")}</span>`}</div>`;
  }

  async function loadProtein() {
    const artifact = app.structureArtifact;
    if (!artifact) return;
    $("structure-file").textContent = artifact.path;
    // Fetch and parse coordinates before touching the renderer: box coverage
    // is a numeric check and must survive a missing viewer.
    let text;
    try {
      const response = await fetch(fileUrl(artifact.path));
      if (!response.ok) throw new Error(`${t("结构请求失败")} (${response.status})`);
      text = await response.text();
      app.structureAtoms = parseReceptorAtoms(text);
      renderBoxCoverage();
    } catch (error) {
      showViewerMessage("protein-viewer", `${t("无法载入结构")}：${error.message || t("请求失败")}`);
      return;
    }
    if (!window.$3Dmol) { $("protein-viewer").innerHTML = `<div class="viewer-placeholder">${t("3D 查看器组件不可用")}</div>`; return; }
    if (!webglAvailable()) { showViewerMessage("protein-viewer", t("浏览器禁用了 WebGL；开启硬件加速后才能查看 3D 结构。")); return; }
    try {
      $("protein-viewer").innerHTML = "";
      app.proteinViewer = window.$3Dmol.createViewer($("protein-viewer"), { backgroundColor: "#0e1116", antialias: true });
      app.proteinViewer.addModel(text, "pdb");
      setProteinStyle("cartoon");
      await addPoseOverlay();
      app.proteinViewer.zoomTo(); app.proteinViewer.render();
      if (app.selectedPocket) showPocket(app.selectedPocket);
      app.boxShapes = [];
      onBoxInputChanged();
    } catch (error) { showViewerMessage("protein-viewer", `${t("无法载入结构")}：${error.message || String(error) || t("3D 渲染失败")}`); }
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
        $("viewer-hint").textContent = app.selectedPocket ? `${t("预测口袋")} · ${siteSelections.length} ${t("个残基")}` : `${t("参考位点")} · ${siteSelections.length} ${t("个残基")}`;
      } else {
        app.proteinViewer.setStyle(protein, { cartoon: { color: "spectrum" } });
        $("viewer-hint").textContent = t("PDB 坐标系");
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
      $("viewer-hint").textContent = t("PDB 坐标系");
      toast(error.message || "无法显示结合位点。", true);
    }
  }

  // ---- docking box -------------------------------------------------------
  // The box is what docking actually searches, so it has to be visible next
  // to the structure rather than guessed at from six number fields.

  const BOX_AXES = ["x", "y", "z"];

  function boxFromInputs() {
    const read = (id) => Number($(id).value);
    const center = BOX_AXES.map((axis) => read(`center-${axis}`));
    const size = BOX_AXES.map((axis) => read(`size-${axis}`));
    if (![...center, ...size].every(Number.isFinite)) return null;
    if (size.some((value) => value <= 0)) return null;
    return { center, size };
  }

  function boxCorners(center, size) {
    const [cx, cy, cz] = center, [sx, sy, sz] = size;
    const lo = { x: cx - sx / 2, y: cy - sy / 2, z: cz - sz / 2 };
    const hi = { x: cx + sx / 2, y: cy + sy / 2, z: cz + sz / 2 };
    return { lo, hi };
  }

  function drawDockingBox() {
    if (!app.proteinViewer) return;
    app.boxShapes.forEach((shape) => { try { app.proteinViewer.removeShape(shape); } catch (_) { /* gone */ } });
    app.boxShapes = [];
    if (!app.boxVisible) { app.proteinViewer.render(); return; }
    const box = boxFromInputs();
    if (!box) { app.proteinViewer.render(); return; }
    const { lo, hi } = boxCorners(box.center, box.size);
    const at = (ix, iy, iz) => ({ x: ix ? hi.x : lo.x, y: iy ? hi.y : lo.y, z: iz ? hi.z : lo.z });
    // Twelve edges of the cuboid, drawn as lines so the receptor stays visible
    // through it.
    const edges = [];
    [0, 1].forEach((a) => [0, 1].forEach((b) => {
      edges.push([at(0, a, b), at(1, a, b)]);
      edges.push([at(a, 0, b), at(a, 1, b)]);
      edges.push([at(a, b, 0), at(a, b, 1)]);
    }));
    edges.forEach(([start, end]) => {
      app.boxShapes.push(app.proteinViewer.addLine({ start, end, color: "#4d8df6", linewidth: 2 }));
    });
    app.proteinViewer.render();
  }

  // Coordinates are parsed from the PDB text we already fetch, not read back
  // out of 3Dmol. Coverage is a property of the structure and the numbers, so
  // it must still work when the viewer is unavailable -- no WebGL, or the CDN
  // unreachable on an air-gapped cluster node.
  function parseReceptorAtoms(text) {
    const atoms = [];
    for (const line of text.split("\n")) {
      if (!line.startsWith("ATOM") && !line.startsWith("HETATM")) continue;
      const x = Number(line.slice(30, 38)), y = Number(line.slice(38, 46)), z = Number(line.slice(46, 54));
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
      const chain = line.slice(21, 22).trim() || "A";
      const resi = Number(line.slice(22, 26));
      if (!Number.isFinite(resi)) continue;
      atoms.push({ x, y, z, key: `${chain}:${resi}` });
    }
    return atoms;
  }

  function boxCoverage() {
    const box = boxFromInputs();
    if (!box) return null;
    const atoms = app.structureAtoms || [];
    if (!atoms.length) return { unavailable: true };
    const { lo, hi } = boxCorners(box.center, box.size);
    const inside = new Set();
    atoms.forEach((atom) => {
      if (atom.x >= lo.x && atom.x <= hi.x && atom.y >= lo.y && atom.y <= hi.y
          && atom.z >= lo.z && atom.z <= hi.z) inside.add(atom.key);
    });
    // Residues the predictor or the user called out, that the box misses.
    const expected = (app.selectedPocket?.residue_ids?.length
      ? app.selectedPocket.residue_ids
      : app.state?.target?.binding_site_residues || []);
    const missing = expected.filter((label) => {
      const match = String(label).match(/^([A-Za-z0-9]+)[:_ ]?(-?\d+)/);
      return match ? !inside.has(`${match[1]}:${Number(match[2])}`) : false;
    });
    return { residues: inside.size, expected: expected.length, missing };
  }

  function renderBoxCoverage() {
    const readout = $("box-readout"), coverage = $("box-coverage");
    const summary = boxCoverage();
    if (readout) {
      readout.hidden = !app.boxVisible || !summary || Boolean(summary.unavailable);
      if (summary && !summary.unavailable) readout.textContent = `盒内残基 ${summary.residues}`;
    }
    if (!coverage) return;
    if (!summary) {
      coverage.innerHTML = `<span class="muted">填写盒中心和正的盒尺寸后显示覆盖情况。</span>`;
      return;
    }
    if (summary.unavailable) {
      // The numbers are fine; we just have no coordinates to measure against.
      coverage.innerHTML = `<span class="muted">尚未载入结构坐标，暂时无法统计盒内残基。</span>`;
      return;
    }
    // A key residue outside the box is a warning, not a block: the reviewer
    // may be deliberately targeting a sub-pocket.
    const warning = summary.missing.length
      ? `<div class="box-warning"><i data-lucide="triangle-alert"></i>${summary.missing.length} 个关键残基落在盒外：${summary.missing.slice(0, 6).map(esc).join(" · ")}${summary.missing.length > 6 ? " …" : ""}</div>`
      : summary.expected
        ? `<div class="box-ok"><i data-lucide="circle-check"></i>${summary.expected} 个关键残基全部在盒内</div>`
        : "";
    coverage.innerHTML = `<div class="box-stat"><span>盒内残基</span><strong>${summary.residues}</strong></div>${warning}`;
    if (window.lucide) window.lucide.createIcons();
  }

  function onBoxInputChanged() {
    drawDockingBox();
    renderBoxCoverage();
  }

  function setBoxVisible(visible) {
    app.boxVisible = visible;
    const button = $("toggle-box");
    if (button) {
      button.classList.toggle("active", visible);
      button.setAttribute("aria-pressed", String(visible));
    }
    drawDockingBox();
    renderBoxCoverage();
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
    // removeAllShapes above dropped the box; put it back so toggling styles
    // does not silently lose it.
    app.boxShapes = [];
    drawDockingBox();
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
    $("ligand-file").textContent = artifact?.path || t("无 3D 产物");
    renderProperties(reportLigand);
    if (!artifact || !window.$3Dmol) { $("ligand-viewer").innerHTML = `<div class="viewer-placeholder"><i data-lucide="atom"></i><span>${t("无 3D 小分子产物")}</span></div>`; return; }
    if (!webglAvailable()) { showViewerMessage("ligand-viewer", t("浏览器禁用了 WebGL；开启硬件加速后才能查看 3D 分子。")); return; }
    try {
      const response = await fetch(fileUrl(artifact.path));
      if (!response.ok) throw new Error(`${t("结构请求失败")} (${response.status})`);
      const text = await response.text();
      $("ligand-viewer").innerHTML = "";
      app.ligandViewer = window.$3Dmol.createViewer($("ligand-viewer"), { backgroundColor: "#0e1116", antialias: true });
      const format = artifact.path.toLowerCase().endsWith(".sdf") ? "sdf" : "pdb";
      app.ligandViewer.addModel(text, format);
      app.ligandViewer.setStyle({}, { stick: { radius: .19, colorscheme: "Jmol" }, sphere: { scale: .28, colorscheme: "Jmol" } });
      app.ligandViewer.zoomTo(); app.ligandViewer.render();
      app.ligandKey = artifact.path;
      $("ligand-atoms").textContent = reportLigand?.properties?.heavy_atoms ? `${reportLigand.properties.heavy_atoms} heavy atoms` : "3D conformer";
    } catch (error) { showViewerMessage("ligand-viewer", `${t("无法载入小分子")}：${error.message || String(error) || t("3D 渲染失败")}`); }
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
    $("ligand-quality").textContent = props ? (item.status === "succeeded" ? t("质控通过") : String(item.status || "reported").toUpperCase()) : t("无报告");
    if (!props) { $("ligand-properties").innerHTML = `<div class="empty-state">${t("暂无评估报告。")}</div>`; $("ligand-alerts").innerHTML = ""; return; }
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
    $("property-table").innerHTML = ligands.length ? ligands.map((item) => { const p = item.properties || {}; const alerts = (p.alerts?.pains || []).length + (p.alerts?.brenk || []).length; return `<tr><td>${esc(item.ligand_id)}</td><td>${fmt(p.mw)}</td><td>${fmt(p.logp)}</td><td>${fmt(p.tpsa)}</td><td>${fmt(p.qed, 3)}</td><td>${alerts ? `<span style="color:var(--amber)">${alerts}</span>` : `<span style="color:var(--teal)">0</span>`}</td></tr>`; }).join("") : `<tr><td colspan="6" class="muted">${t("暂无分子评估结果。")}</td></tr>`;
    $("limitations-list").innerHTML = (report.limitations || [t("暂无评估报告。")]).map((item) => `<div class="limitation">${esc(item)}</div>`).join("");
    document.querySelectorAll("[data-result-row]").forEach((row) => row.addEventListener("click", () => showView("structure")));
  }

  async function loadProvenance(artifactId) {
    const panel = $("result-provenance");
    if (!panel) return;
    panel.hidden = false;
    panel.innerHTML = `<div class="provenance-heading"><div><div class="eyebrow">PROVENANCE</div><strong>正在读取来源链…</strong></div></div>`;
    try {
      const response = await fetch(scoped("/api/provenance", { artifact_id: artifactId || "" }), { cache: "no-store" });
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

  // The receptor stage records an approval only; the CLI rejects any other
  // action for it, so the form offers what the backend will actually accept
  // rather than letting the reviewer discover it from an error.
  const reviewActions = {
    docking_box: [["approve", "接受当前选择"], ["select", "选择口袋排名"],
                  ["override", "修改对接盒"], ["reject", "否决当前盒子"]],
    receptor: [["approve", "接受受体准备结果"]]
  };

  function renderReviewStage() {
    const stage = $("review-stage").value;
    const actions = reviewActions[stage] || reviewActions.docking_box;
    const previous = $("review-action").value;
    $("review-action").innerHTML = actions
      .map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join("");
    if (actions.some(([value]) => value === previous)) $("review-action").value = previous;
    const boxStage = stage === "docking_box";
    $("box-inputs").hidden = !boxStage;
    $("box-coverage").hidden = !boxStage;
    const note = $("review-stage-note");
    note.hidden = boxStage;
    note.textContent = boxStage ? "" : "受体审核目前只记录「接受」。要否决修复结果，请从受体阶段重跑并调整参数。";
    if (boxStage) renderBoxCoverage();
  }

  async function submitReview() {
    const stage = $("review-stage").value;
    const action = $("review-action").value;
    const payload = { stage, action, actor: $("review-actor").value, reason: $("review-reason").value };
    if (stage === "docking_box") {
      if (action === "select" || action === "approve") payload.pocket_rank = app.selectedPocket?.rank;
      if (action === "override") {
        const box = boxFromInputs();
        if (!box) { toast("请填写有效的盒中心和正的盒尺寸。", true); return; }
        payload.center = box.center;
        payload.size = box.size;
      }
    }
    try {
      const response = await postJson("/api/review", payload);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      toast("人工决策已记录");
      await fetchState();
    } catch (error) { toast(error.message, true); }
  }

  async function continueWorkflow() {
    try { const response = await postJson("/api/continue", { model: $("chat-model").value.trim(), conversation_id: app.chatId }); const data = await response.json(); if (!response.ok) throw new Error(data.error); toast(data.status === "already_running" ? t("工作流已在运行") : data.status === "already_complete" ? t("工作流已完成") : t("工作流已启动")); await fetchState(); } catch (error) { toast(error.message, true); }
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
    $("refresh-files").addEventListener("click", refreshProjectFiles); $("save-file").addEventListener("click", saveProjectFile); $("restart-terminal").addEventListener("click", restartTerminal); $("chat-form").addEventListener("submit", sendChat); $("new-file").addEventListener("click", async () => { const name = window.prompt(t("新文件名（相对项目根目录）")); if (!name) return; const path = [app.fileDirectory, name].filter(Boolean).join("/"); try { const response = await postJson("/api/project/file", { path, content: "" }); const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText); await refreshProjectFiles(); await openProjectFile(data.path); } catch (error) { toast(error.message, true); } });
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
    $("auth-form").addEventListener("submit", async (event) => { event.preventDefault(); const token = $("auth-token").value; const ok = await authenticate(token); if (ok) { $("auth-error").textContent = ""; initializeWorkspace(); } else { $("auth-error").textContent = t("访问令牌与本服务器不匹配。"); } });
    $("file-editor").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveProjectFile(); } });
    $("chat-input").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); $("chat-form").requestSubmit(); } });
    document.querySelectorAll("[data-protein-style]").forEach((button) => button.addEventListener("click", () => setProteinStyle(button.dataset.proteinStyle)));
    $("structure-select").addEventListener("change", async (event) => { const picked = structureCandidates()[Number(event.target.value)]; disposeViewers(); app.structureArtifact = picked; await loadProtein(); });
    $("pose-select").addEventListener("change", async () => { const picked = app.structureArtifact; disposeViewers(); app.structureArtifact = picked; await loadProtein(); });
    $("ligand-select").addEventListener("change", (event) => { try { app.ligandViewer?.clear?.(); } catch (_) { /* already gone */ } app.ligandViewer = null; app.ligandKey = null; loadLigand(event.target.value); }); $("submit-review").addEventListener("click", submitReview);
    $("review-stage").addEventListener("change", renderReviewStage);
    $("save-parameters").addEventListener("click", saveParameters);
    $("reset-parameters").addEventListener("click", resetParameters);
    $("toggle-box").addEventListener("click", () => setBoxVisible(!app.boxVisible));
    ["center", "size"].forEach((kind) => ["x", "y", "z"].forEach((axis) => {
      // Typing a coordinate moves the wireframe immediately, so the box is
      // judged by looking at it rather than by reading six numbers.
      $(`${kind}-${axis}`).addEventListener("input", onBoxInputChanged);
    }));
    renderReviewStage(); $("continue-button").addEventListener("click", continueWorkflow);
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
      // Capture is taken only once a drag actually starts. Capturing on
      // pointerdown retargets the following click to the canvas, which
      // swallowed every click on a stage node and its action buttons.
      app.workflowCanvas.pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, startX: app.workflowCanvas.x, startY: app.workflowCanvas.y, moved: false };
    });
    workflowCanvas.addEventListener("pointermove", (event) => {
      const pointer = app.workflowCanvas.pointer;
      if (!pointer || pointer.id !== event.pointerId) return;
      const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
      if (Math.abs(dx) + Math.abs(dy) > 4 && !pointer.moved) {
        pointer.moved = app.workflowCanvas.moved = true;
        // Now that this is a drag, keep receiving moves even if the cursor
        // leaves the canvas.
        try { workflowCanvas.setPointerCapture(event.pointerId); } catch (_) { /* already gone */ }
      }
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
      if (pointer.moved) {
        try { workflowCanvas.releasePointerCapture(event.pointerId); } catch (_) { /* already released */ }
        window.setTimeout(() => { app.workflowCanvas.moved = false; }, 0);
      }
    };
    workflowCanvas.addEventListener("pointerup", endCanvasPan);
    workflowCanvas.addEventListener("pointercancel", endCanvasPan);
    bootWorkspace();
    window.setInterval(updateWorkflowElapsed, 1000);
  });
})();
