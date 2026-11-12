/* DrugStudio workbench: scientific data comes from the curated /api/state snapshot. */
(function () {
  "use strict";

  const app = { state: null, events: [], eventSource: null, proteinViewer: null, proteinStyle: "cartoon", ligandViewer: null, ligandKey: null, selectedPocket: null, structureArtifact: null, selectedStage: null, pricing: {}, provenance: null, workflowCanvas: { x: 0, y: 0, scale: 1, ready: false, pointer: null, moved: false }, projectId: ".", fileDirectory: "", filePath: "", terminalCwd: "", terminalSession: null, terminalStarting: false, terminalCursor: 0, terminalPollTimer: null, terminalInputBuffer: "", terminalInputTimer: null, terminal: null, terminalFit: null, chatHistory: [], chatSessions: [], chatId: null, chatBusy: false, chatFilter: "active", modelConfig: null, moduleConfig: null, codeEditor: null, runKey: "",
    boxVisible: false, boxShapes: [], pocketLabel: null, structureAtoms: [],
    structureRequestId: 0, structureCoordinateKey: null, structureText: null, boxDraftRun: null,
    parameters: null, parameterEdits: {}, parameterRequestId: 0, parameterSaving: false,
    workflowBuilt: false, stagePickedByUser: false, pendingAction: null };
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
  const t = window.DrugStudioUI.t;
  const busy = (id, action) => window.DrugStudioControls.busy(document.getElementById(id), action);

  function refreshAppearance() {
    const palette = getComputedStyle(document.documentElement);
    const background = palette.getPropertyValue("--surface-2").trim();
    [app.proteinViewer, app.ligandViewer, app.fileScene?.viewer].forEach((viewer) => {
      if (viewer) { viewer.setBackgroundColor(background); viewer.render(); }
    });
    if (app.proteinViewer) {
      updatePocketLabel(app.proteinScene?.siteMode ? app.selectedPocket : null);
      drawDockingBox();
    }
    if (app.terminal) app.terminal.options.theme = terminalTheme();
  }

  function terminalTheme() {
    const palette = getComputedStyle(document.documentElement);
    const color = (name) => palette.getPropertyValue(name).trim();
    // xterm defaults are intended for a black terminal. Supply the entire ANSI
    // palette so colored shell output stays legible on both surfaces.
    return { background: color("--surface-2"), foreground: color("--ink"),
      cursor: color("--blue"), cursorAccent: color("--surface-2"),
      selectionBackground: color("--blue") + "44",
      black: color("--ansi-black"), brightBlack: color("--ansi-bright-black"),
      white: color("--ansi-white"), brightWhite: color("--ansi-bright-white"),
      red: color("--coral"), brightRed: color("--coral"),
      green: color("--lime"), brightGreen: color("--lime"),
      yellow: color("--amber"), brightYellow: color("--amber"),
      blue: color("--blue"), brightBlue: color("--blue"),
      magenta: color("--purple"), brightMagenta: color("--purple"),
      cyan: color("--teal"), brightCyan: color("--teal") };
  }

  function updatePocketLabel(pocket) {
    const viewer = app.proteinViewer;
    if (!viewer) return;
    if (app.pocketLabel) viewer.removeLabel(app.pocketLabel);
    app.pocketLabel = null;
    const center = pocket?.box?.center || pocket?.center;
    if (center?.length === 3) {
      const palette = getComputedStyle(document.documentElement);
      app.pocketLabel = viewer.addLabel(`Pocket ${pocket.rank}`, {
        position: { x: center[0], y: center[1], z: center[2] },
        backgroundColor: palette.getPropertyValue("--viewer-label-bg").trim(),
        fontColor: palette.getPropertyValue("--viewer-label-ink").trim(),
        fontSize: 11, inFront: true
      });
    }
  }

  function refreshLanguage() {
    // Preserve unsaved drafts and the user's reading position. No scientific
    // jobs or settings writes are triggered by this preference.
    const scrollTop = $("chat-messages").scrollTop;
    const reason = $("pending-reason")?.value;
    const moduleDraft = [...document.querySelectorAll("[data-module-key]")].map((input) =>
      [input.dataset.moduleKey, input.value, input.checked]);
    const forms = [...document.querySelectorAll("input[id],textarea[id],select[id]")].map((input) =>
      [input.id, input.value, input.checked]);
    renderChatList(); renderChat(); renderReviewStage(); syncTerminalToggle();
    document.querySelectorAll("[data-close-drawer]").forEach((button) => {
      button.title = t("关闭工具面板");
      button.setAttribute("aria-label", button.title);
    });
    if (reason !== undefined && $("pending-reason")) $("pending-reason").value = reason;
    $("chat-messages").scrollTop = scrollTop;
    setConnection(!$("connection-dot").classList.contains("offline"));
    renderChatStatus();
    if (app.state) {
      renderAll();
      if (app.selectedStage) inspectWorkflowStage(app.selectedStage);
    }
    if (app.moduleConfig) {
      renderModuleConfig();
      moduleDraft.forEach(([key, value, checked]) => {
        const input = document.querySelector(`[data-module-key="${key}"]`);
        if (input) { input.value = value; input.checked = checked; }
      });
    }
    forms.forEach(([id, value, checked]) => {
      const input = $(id);
      if (input) { input.value = value; input.checked = checked; }
    });
    if (app.state) {
      renderPocketDetail();
      const ligand = app.state.ligands?.[Number($("ligand-select").value || 0)];
      renderProperties((app.state.report?.ligands || []).find((item) => item.ligand_id === ligand?.ligand_id));
    }
  }

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
  const fmt = (value, digits = 2) => value != null && value !== "" && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
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

  async function initializeWorkspace() {
    initializeCodeEditor();
    await fetchProjects();
    await fetchRuns();
    await fetchState();
    await fetchModelConfig();
    await fetchModuleConfig();
    renderChat();
    document.documentElement.dataset.workspaceReady = 'true';
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

  const moduleSettingLabels = {
    vina_runner: ["Vina runner", "运行 AutoDock Vina 的封装脚本"],
    vina_binary: ["Vina 可执行文件", "用于检查和兜底调用的 Vina 路径"],
    chemistry_runner: ["化学工具 runner", "RDKit / Meeko 的环境封装脚本"],
    receptor_prefix: ["PDBFixer 环境", "包含 PDBFixer / OpenMM 的 Python 环境"],
    xtb_binary: ["xTB 可执行文件", "小分子构象优化使用的 xTB"],
    p2rank_binary: ["P2Rank prank", "口袋发现程序"],
    java_home: ["Java HOME", "P2Rank 使用的 Java 运行时目录"],
    af3_activate: ["AF3 激活脚本", "本地 AlphaFold 3 环境激活脚本"],
    af3_model_dir: ["AF3 模型目录", "AlphaFold 3 权重目录"],
    af3_db_dir: ["AF3 数据库目录", "AlphaFold 3 数据库根目录"],
    af3_queue: ["PBS 队列", "提交本地 AF3 任务的队列"],
    af3_walltime: ["PBS walltime", "例如 12:00:00"],
    af3_cpus: ["CPU 核数", "AF3 PBS 任务申请的 CPU 数"],
    af3_memory: ["内存", "例如 64gb"],
    af3_gpu_count: ["GPU 数量", "AF3 PBS 任务申请的 GPU 数"],
    af3_gpu_id: ["GPU ID", "节点上的物理 GPU 编号；留空则由调度器决定"],
    af3_terms_acknowledged: ["AF3 条款确认", "确认本机 AlphaFold 3 模型条款适用于本次运行"],
    af3_host: ["PBS 计算节点", "PBS 的计算节点资源名；留空则由调度器选择，不是 SSH 主机或 IP"],
    af3_qsub: ["qsub", "PBS 提交命令"],
    af3_qstat: ["qstat", "PBS 状态查询命令"]
  };

  async function fetchModuleConfig() {
    try {
      const response = await fetch(scoped("/api/module-config"), { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.moduleConfig = data;
      renderModuleConfig();
    } catch (error) {
      $("module-config-status").textContent = t("模块配置读取失败：{p0}", { p0: error.message });
      $("module-config-dot").classList.add("offline");
    }
  }

  function renderModuleConfig({ preserveDraft = true } = {}) {
    const config = app.moduleConfig;
    if (!config) return;
    const drafts = preserveDraft ? [...document.querySelectorAll('[data-module-key]')]
      .filter(input => input.type === 'checkbox' ? input.checked !== input.defaultChecked : input.value !== input.defaultValue)
      .map(input => [input.dataset.moduleKey, input.value, input.checked]) : [];
    const checks = config.checks || {};
    const groups = config.groups || {};
    const values = config.values || {};
    const groupHtml = Object.entries(groups).map(([name, group]) => {
      const fields = (group.keys || []).map((key) => {
        const meta = moduleSettingLabels[key] || [key, ""];
        const check = checks[key] || {};
        const isCheckbox = key === "af3_terms_acknowledged";
        const inputType = isCheckbox ? "checkbox" : (["af3_cpus", "af3_gpu_count"].includes(key) ? "number" : "text");
        const status = t(check.ok ? "可用" : (check.detail || "待检查"));
        const checkbox = isCheckbox ? (values[key] === "1" || values[key] === "yes" || values[key] === "true" ? " checked" : "") : "";
        const value = isCheckbox ? "1" : esc(values[key] || "");
        return `<label class="module-setting-field ${isCheckbox ? "checkbox-setting" : ""}"><span><strong>${esc(t(meta[0]))}</strong><small>${esc(t(meta[1]))}</small></span><input class="input-control" data-module-key="${esc(key)}" type="${inputType}" ${inputType === "number" ? "min=\"1\" step=\"1\"" : ""}${checkbox} value="${value}" autocomplete="off"><em class="module-setting-check ${check.ok ? "ok" : "bad"}" data-module-check="${esc(key)}">${esc(status)}</em></label>`;
      }).join("");
      return `<section class="panel module-settings-group"><div class="panel-heading"><div><div class="eyebrow">${t("模块")}</div><h2>${esc(t(group.label || name))}</h2></div><span class="mono muted">${(group.keys || []).length} ${esc(t("项"))}</span></div><div class="module-settings-fields">${fields}</div></section>`;
    }).join("");
    $("module-settings-groups").innerHTML = groupHtml || `<div class="empty-state">${esc(t("没有可配置模块。"))}</div>`;
    drafts.forEach(([key, value, checked]) => {
      const input = document.querySelector(`[data-module-key="${key}"]`);
      if (input) { input.value = value; input.checked = checked; }
    });
    const allChecks = Object.values(checks);
    const healthy = allChecks.length > 0 && allChecks.every((item) => item.ok);
    $("module-config-dot").classList.toggle("offline", !healthy);
    $("module-config-status").textContent = healthy ? t("模块环境检查通过") : t("{p0}/{p1} 项配置可用", { p0: allChecks.filter((item) => item.ok).length, p1: allChecks.length });
    $("module-config-path").textContent = config.config_path || "";
    window.DrugStudioControls.icons();
  }

  async function saveModuleConfig() {
    const button = $("save-module-settings");
    const settings = {};
    document.querySelectorAll("[data-module-key]").forEach((input) => { settings[input.dataset.moduleKey] = input.type === "checkbox" ? (input.checked ? "1" : "") : input.value.trim(); });
    button.disabled = true;
    try {
      const response = await postJson("/api/module-config", { settings });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.moduleConfig = data;
      renderModuleConfig({ preserveDraft: false });
      toast(t("模块设置已保存；新运行将使用最新配置"));
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  }

  function setModelConfigStatus(configured, baseUrl = "") {
    $("model-config-dot").classList.toggle("offline", !configured);
    $("model-config-status").textContent = configured
      ? t("API Key 已配置{p0}", { p0: baseUrl ? ` · ${baseUrl}` : "" })
      : t("尚未配置 API Key");
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
      toast(t("模型连接设置已保存"));
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
    app.proteinScene?.dispose(); app.proteinScene = null; app.ligandScene?.dispose(); app.ligandScene = null;
    [["proteinViewer", "protein-viewer"], ["ligandViewer", "ligand-viewer"]].forEach(([key, id]) => {
      try { app[key]?.clear?.(); } catch (_) { /* viewer already torn down */ }
      app[key] = null;
      const host = $(id);
      if (host) host.innerHTML = "";
    });
    app.fileScene?.dispose(); app.fileScene = null; $("file-molecule-preview")?.remove();
    app.pocketShape = null;
    app.ligandKey = null;
    app.boxShapes = [];
    app.structureAtoms = [];
    app.structureText = null; app.structureCoordinateKey = null;
    app.structureRequestId += 1;
  }

  async function fetchState() {
    const projectId = app.projectId, runKey = app.runKey;
    try {
      const response = await fetch(scoped("/api/state"), { cache: "no-store" });
      if (!response.ok) throw new Error((await response.json()).error || response.statusText);
      const state = await response.json();
      if (projectId !== app.projectId || runKey !== app.runKey) return;
      app.state = state;
      if (state.module_config) {
        app.moduleConfig = state.module_config;
        if (document.querySelector("#view-settings.active")) renderModuleConfig();
      }
      app.runKey = state.run_key || "";
      if (!app.runKey) {
        app.eventSource?.close(); app.eventSource = null; app.events = [];
        app.selectedStage = null; app.selectedPocket = null; app.structureArtifact = null;
        app.provenance = null; disposeViewers();
        $("result-provenance")?.setAttribute("hidden", "");
        $("workflow-node-detail").textContent = t("本项目暂无运行");
      }
      if (!app.projectId && app.state.project?.project_id) app.projectId = app.state.project.project_id;
      setConnection(true);
      fetchParameters();
      renderAll();
      connectEvents();
      fetchRuns();
    } catch (error) {
      if (projectId !== app.projectId || runKey !== app.runKey) return;
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
    const cannotContinue = !app.runKey || state.server?.workflow_running || ["cancelled", "succeeded"].includes(state.status);
    $("workflow-continue").disabled = cannotContinue;
    $("continue-button").disabled = cannotContinue;
    $("submit-review").disabled = !app.runKey;
    $("save-pricing").disabled = !app.runKey;
    $("rerun-stage").disabled = !app.runKey || !app.selectedStage || state.server?.workflow_running;
    const runPill = $("run-state-pill");
    runPill.textContent = ({ succeeded: t("已完成"), failed: t("失败"), running: t("运行中"), paused: t("已暂停"), cancelled: t("已取消"), waiting_approval: t("等待审核"), waiting_input: t("等待输入"), waiting_execution: t("等待执行") })[state.status] || (state.run_id ? state.status || t("待运行") : t("无运行"));
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
    if (app.boxDraftRun !== app.runKey) {
      const saved = state.overrides?.docking_box;
      if (saved?.center && saved?.size) fillBoxInputs(saved);
      app.boxDraftRun = app.runKey;
    }
    renderLigandChoices();
    renderResults();
    renderReview();
    window.DrugStudioControls.icons();
    if (!app.proteinViewer && document.querySelector("#view-structure.active")) loadProtein();
    if (document.querySelector("#view-review.active")) loadStructureCoordinates();
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

  function stageStatusLabel(status) { return t(stageStatusLabels[status] || String(status || t("待执行")).replaceAll("_", " ")); }

  function stageProgress(stage, status) {
    const result = app.state?.stage_results?.[stage] || {};
    const metadata = result.metadata || {};
    const current = result.progress || metadata.progress || {};
    const done = current.completed ?? current.finished ?? current.processed;
    const total = current.total ?? current.count ?? current.total_items;
    if (Number.isFinite(Number(done)) && Number.isFinite(Number(total)) && Number(total) > 0) return `${Number(done)}/${Number(total)}`;
    const artifacts = (app.state?.artifacts?.[stage] || []).length;
    if (artifacts) return t("{p0} 个产物", { p0: artifacts });
    return status === "succeeded" ? t("已记录") : "";
  }

  function connectEvents() {
    if (app.eventSource || !app.runKey) return;
    const source = new EventSource(scoped("/api/events", { since: app.events.at(-1)?.event_id || 0 }));
    app.eventSource = source;
    source.onopen = () => { if (app.eventSource === source) $("event-live").classList.add("live-indicator"); };
    const onEvent = (event) => { if (app.eventSource === source) receiveEvent(event.data); };
    source.onmessage = onEvent;
    ["run_started", "run_completed", "run_failed", "run_paused", "run_cancelled", "control_requested", "model_started", "model_finished", "tool_started", "tool_finished", "stage_started", "stage_completed", "stage_failed", "stage_progress", "waiting_approval", "artifact_published", "human_decision", "run_created", "run_branched", "pricing_updated"].forEach((type) => source.addEventListener(type, onEvent));
    source.onerror = () => {
      if (app.eventSource !== source) return;
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
    }<article class="workflow-stage-node pending" data-workflow-stage="${esc(stage)}" tabindex="0" role="button" aria-pressed="false"><div class="workflow-node-icon"><i data-lucide="${icon}"></i></div><strong>${esc(t(label))}</strong><span data-stage-status></span><em data-stage-progress hidden></em><div class="workflow-stage-actions">${
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
    window.DrugStudioControls.icons();
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
    workflowNodes.forEach(([stage, title]) => {
      const node = world.querySelector(`[data-workflow-stage="${stage}"]`);
      if (!node) return;
      node.querySelector("strong").textContent = t(title);
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
    $("workflow-world").querySelector(".workflow-entry").textContent = t("开始");
    $("workflow-world").querySelector(".workflow-end").textContent = t("完成");
    const actionLabels = { logs: t("查看日志"), params: t("查看参数"), rerun: t("从此处重跑"), takeover: t("接管节点") };
    $("workflow-world").querySelectorAll("[data-stage-action]").forEach((button) => {
      button.title = t(actionLabels[button.dataset.stageAction]);
      button.setAttribute("aria-label", button.title);
    });
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
    $("workflow-model").textContent = t("模型 ") + modelName;
    const start = app.events.find((item) => item.type === "run_started")?.timestamp; $("workflow-elapsed").textContent = t("耗时 ") + (start ? duration(start, app.events.at(-1)?.timestamp) : "—");
    const completed = workflowNodes.filter(([stage]) => ["succeeded", "completed"].includes(stageStatus(stage))).length;
    const active = workflowNodes.find(([stage]) => ["running", "waiting_approval", "waiting_input", "waiting_execution", "failed"].includes(stageStatus(stage)));
    $("workflow-progress").textContent = `${completed}/${workflowNodes.length}`;
    $("workflow-stage-summary").textContent = active ? `${t(active[1])} · ${stageStatusLabel(stageStatus(active[0]))}` : app.state.status === "succeeded" ? t("全部阶段已完成") : app.state.status === "failed" ? t("运行失败，需要处理") : t("等待开始");
    renderWorkflowLineage(); renderEventList(); renderRunFacts();
    renderCosts();
    window.DrugStudioControls.icons();
  }

  // ---- stage parameters --------------------------------------------------
  // Until now "改参数" only raised a toast pointing at a panel that showed
  // no parameters. The form is built from the schema the server serves, so
  // adding a knob in parameters.py is enough to make it editable here.

  function resetParameterContext() {
    app.parameterRequestId += 1;
    app.parameters = null; app.parameterEdits = {}; app.parameterSaving = false;
    renderStageParameters();
  }

  async function fetchParameters() {
    if (!app.runKey) { resetParameterContext(); return; }
    if (app.parameterSaving) return;
    const projectId = app.projectId, runKey = app.runKey;
    const requestId = ++app.parameterRequestId;
    const current = () => requestId === app.parameterRequestId && projectId === app.projectId && runKey === app.runKey;
    try {
      const response = await fetch(scoped("/api/parameters"), { cache: "no-store" });
      const data = await response.json();
      if (!current()) return;
      if (!response.ok) throw new Error(data.error || response.statusText);
      if (data.run_key !== runKey) throw new Error(t("参数所属运行不匹配，请刷新后重试"));
      if (app.parameters?.run_key !== runKey) app.parameterEdits = {};
      app.parameters = data;
      renderStageParameters();
    } catch (error) {
      if (!current()) return;
      app.parameters = null;
      renderStageParameters();
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
    const disabled = app.parameters?.editable && !app.parameterSaving ? "" : "disabled";
    const changed = JSON.stringify(app.parameters?.values?.[item.key]) !== JSON.stringify(item.default);
    let control;
    if (item.kind === "bool") {
      control = `<input type="checkbox" id="${id}" data-parameter="${esc(item.key)}" ${value ? "checked" : ""} ${disabled}>`;
    } else if (item.kind === "choice") {
      control = `<select id="${id}" class="input-control" data-parameter="${esc(item.key)}" ${disabled}>${
        item.choices.map((choice) => `<option value="${esc(choice)}" ${choice === value ? "selected" : ""}>${esc(choice)}</option>`).join("")}</select>`;
    } else if (item.kind === "list") {
      control = `<textarea id="${id}" class="input-control" rows="2" data-parameter="${esc(item.key)}" placeholder="${esc(t("每行一个"))}" ${disabled}>${esc(Array.isArray(value) ? value.join("\n") : (value || ""))}</textarea>`;
    } else if (item.kind === "text") {
      control = `<input type="text" id="${id}" class="input-control" data-parameter="${esc(item.key)}" value="${esc(value ?? "")}" ${disabled}>`;
    } else {
      const step = item.kind === "float" ? "0.1" : "1";
      const bounds = `${item.minimum != null ? ` min="${item.minimum}"` : ""}${item.maximum != null ? ` max="${item.maximum}"` : ""}`;
      control = `<input type="number" id="${id}" class="input-control" step="${step}"${bounds} data-parameter="${esc(item.key)}" value="${esc(value ?? "")}" ${disabled}>`;
    }
    return `<div class="parameter-row ${item.kind === "bool" ? "parameter-row-bool" : ""}">
      <label for="${id}">${esc(t(item.label))}${item.unit ? ` <small>${esc(item.unit)}</small>` : ""}${changed ? `<span class="parameter-changed" title="${esc(t("已偏离默认值"))}">·</span>` : ""}</label>
      ${control}
      ${item.help ? `<p class="parameter-help">${esc(t(item.help))}</p>` : ""}
    </div>`;
  }

  function renderStageParameters() {
    const section = $("stage-parameters");
    if (!section) return;
    const fields = $("stage-parameters-fields");
    const stage = app.selectedStage;
    const data = app.parameters;
    if (!data || data.run_key !== app.runKey) {
      fields.innerHTML = `<div class="empty-state">${app.runKey ? t("参数尚未载入。") : t("本项目暂无运行。")}</div>`;
      $("stage-parameters-actions").hidden = true;
      $("stage-parameters-note").hidden = true;
      $("stage-parameters-state").textContent = "";
      if (!app.runKey || !stage) {
        fields.innerHTML = `<div class="empty-state">${!app.runKey ? t("本项目暂无运行。") : t("选择一个阶段查看它的参数。")}</div>`;
      }
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
    $("save-parameters").disabled = app.parameterSaving;
    $("reset-parameters").disabled = app.parameterSaving;
    renderParameterNote();
    window.DrugStudioControls.icons();
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
    if (!pending.length) {
      const required = app.parameters?.rerun_required;
      const label = workflowNodes.find(([name]) => name === required)?.[1] || required;
      note.hidden = !required;
      note.textContent = required ? t("已保存的参数需要从「{p0}」重跑后生效。", { p0: t(label) }) : "";
      return;
    }
    // Say what a change costs before it is saved: the stage it invalidates
    // is the one the user will have to rerun from.
    const stages = pending.map((key) => app.parameters.parameters.find((item) => item.key === key)?.stage);
    const order = app.parameters.stage_order || [];
    const earliest = stages.filter(Boolean).sort((a, b) => order.indexOf(a) - order.indexOf(b))[0];
    const label = workflowNodes.find(([name]) => name === earliest)?.[1] || earliest;
    note.hidden = false;
    note.textContent = t("保存后需要从「{p0}」重跑才会生效。", { p0: t(label) });
  }

  async function saveParameters() {
    if (!app.runKey || app.parameters?.run_key !== app.runKey || !app.parameters?.editable || app.parameterSaving) return;
    const pending = pendingParameterKeys();
    if (!pending.length) { toast(t("参数没有改动")); return; }
    const projectId = app.projectId, runKey = app.runKey;
    const requestId = ++app.parameterRequestId;
    const current = () => requestId === app.parameterRequestId && projectId === app.projectId && runKey === app.runKey;
    const values = {};
    pending.forEach((key) => { values[key] = app.parameterEdits[key]; });
    app.parameterSaving = true;
    renderStageParameters();
    try {
      const response = await postJson("/api/parameters", { values, run_key: runKey, project_id: projectId });
      const data = await response.json();
      if (!current()) return;
      if (!response.ok) throw new Error(data.error || response.statusText);
      if (data.run_key !== runKey) throw new Error(t("参数所属运行不匹配，请刷新后重试"));
      app.parameters = data;
      app.parameterEdits = {};
      const label = workflowNodes.find(([name]) => name === data.rerun_required)?.[1];
      toast(label ? t("参数已保存，从「{p0}」重跑后生效", { p0: t(label) }) : t("参数已保存，将用于下一次执行"));
    } catch (error) { if (current()) toast(error.message, true); }
    finally {
      if (current()) { app.parameterSaving = false; renderStageParameters(); }
    }
  }

  function resetParameters() {
    if (!app.parameters) return;
    const stage = app.selectedStage;
    app.parameters.parameters.filter((item) => item.stage === stage)
      .forEach((item) => { app.parameterEdits[item.key] = item.default; });
    renderStageParameters();
  }

  function handleStageAction(action, stage) {
    if (app.selectedStage !== stage) app.parameterEdits = {};
    app.selectedStage = stage;
    if (action === "logs") {
      const details = $("advanced-run-details");
      if (details) details.open = true;
      window.setTimeout(() => $("event-list")?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 40);
      return;
    }
    if (action === "params") {
      inspectWorkflowStage(stage);
      $("stage-parameters")?.scrollIntoView({ behavior: "instant", block: "nearest" });
      return;
    }
    if (action === "rerun") {
      rerunFromSelectedStage();
      return;
    }
    if (action === "takeover") {
      inspectWorkflowStage(stage);
      if (app.state?.server?.workflow_running) sendControl("pause");
      else toast(t("已进入节点详情，可在审核面板接管后续决策。"));
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
    $("workflow-node-title").textContent = t(label); $("workflow-node-status").textContent = stageStatusLabel(status);
    $("workflow-node-detail").innerHTML = `<div class="inspector-fact"><span>${esc(t("状态"))}</span><strong>${esc(stageStatusLabel(status))}</strong></div><div class="inspector-fact"><span>${esc(t("产物"))}</span><strong>${(app.state.artifacts?.[stage] || []).length}</strong></div><div class="inspector-fact"><span>${esc(t("进度"))}</span><strong>${esc(stageProgress(stage, status) || "—")}</strong></div><div class="inspector-fact"><span>${esc(t("最近结果"))}</span><strong>${esc(result.error || result.metadata?.profile || result.metadata?.selected_rank || t("已记录在运行清单"))}</strong></div>`;
    $("rerun-stage").disabled = !app.runKey || app.state.server?.workflow_running;
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
    $("workflow-node-title").textContent = t(title);
    $("workflow-node-status").textContent = String(status).replaceAll("_", " ");
    $("workflow-node-detail").innerHTML = detail.map(([label, value]) => `<div class="inspector-fact"><span>${esc(t(label))}</span><strong>${esc(value)}</strong></div>`).join("");
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
    const state = app.state; const facts = [[t("运行状态"), stageStatusLabel(state.status)], [t("当前阶段"), t(workflowNodes.find(([stage]) => stage === state.current_node)?.[1] || state.current_node || "空闲")], [t("事件"), app.events.length], [t("产物"), Object.values(state.artifacts || {}).flat().length], [t("模型调用"), app.events.filter((item) => item.type === "model_started").length], [t("工具调用"), app.events.filter((item) => item.type === "tool_started").length]];
    $("run-facts").innerHTML = facts.map(([label, value]) => `<div class="run-fact"><span>${esc(t(label))}</span><strong>${esc(value)}</strong></div>`).join("");
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
    const projectId = app.projectId, runKey = app.runKey;
    try {
      const response = await fetch(scoped("/api/runs"), { cache: "no-store" }); const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      if (projectId !== app.projectId || runKey !== app.runKey) return;
      const runs = data.runs || [];
      $("run-selector").innerHTML = runs.length ? runs.map((run) => {
        const title = `${run.run_key} · ${run.target_id || run.run_id} · ${run.status || "unknown"}`;
        return `<option value="${esc(run.run_key)}" ${run.run_key === app.runKey ? "selected" : ""} title="${esc(title)}">${esc(run.run_key)}</option>`;
      }).join("") : `<option value="">${esc(t("本项目暂无运行"))}</option>`;
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
      const selectable = (data.projects || []).filter(project => !project.archived || project.project_id === app.projectId);
      $("project-selector").innerHTML = selectable.map((project) => `<option value="${esc(project.project_id)}" ${project.project_id === app.projectId ? "selected" : ""}>${esc(project.name)}${project.archived ? ` · ${esc(t("已归档"))}` : ""}</option>`).join("");
      $("project-description").textContent = selected.name || app.projectId;
      renderProjectManager(data.projects || []);
      renderChatStatus();
      await fetchChats();
    } catch (error) { toast(error.message, true); }
  }

  function renderProjectManager(projects) {
    const list = $("project-management-list");
    if (!list) return;
    list.innerHTML = projects.length ? projects.map(project => {
      const isRoot = project.project_id === ".";
      const archived = Boolean(project.archived);
      return `<div class="project-management-row ${archived ? "archived" : ""}">
        <div class="project-management-info"><strong>${esc(project.name)}</strong><small>${esc(project.project_id)}${project.description ? ` · ${esc(project.description)}` : ""}</small></div>
        <div class="project-management-actions">${!isRoot ? `<button type="button" class="icon-button" data-project-action="rename" data-project-id="${esc(project.project_id)}" title="${esc(t("重命名项目"))}" aria-label="${esc(t("重命名项目"))}"><i data-lucide="pencil"></i></button><button type="button" class="icon-button" data-project-action="${archived ? "restore" : "archive"}" data-project-id="${esc(project.project_id)}" title="${esc(t(archived ? "恢复项目" : "归档项目"))}" aria-label="${esc(t(archived ? "恢复项目" : "归档项目"))}"><i data-lucide="${archived ? "archive-restore" : "archive"}"></i></button>` : `<span class="muted mono">${esc(t("工作区"))}</span>`}</div>
      </div>`;
    }).join("") : `<div class="empty-state">${esc(t("还没有项目"))}</div>`;
    list.querySelectorAll("[data-project-action]").forEach(button => button.addEventListener("click", () => manageProject(button.dataset.projectAction, button.dataset.projectId)));
    window.DrugStudioControls.icons();
  }

  async function manageProject(action, projectId) {
    try {
      if (action === "rename") {
        const current = [...document.querySelectorAll("[data-project-action='rename']")].find(button => button.dataset.projectId === projectId);
        const label = current?.closest(".project-management-row")?.querySelector("strong")?.textContent || projectId;
        const name = window.prompt(t("项目名称"), label);
        if (!name || name.trim() === label) return;
        const response = await postJson("/api/project/manage", { action: "update", project_id: projectId, name: name.trim() });
        const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      } else {
        const response = await postJson("/api/project/manage", { action, project_id: projectId });
        const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
        if (action === "archive" && app.projectId === projectId) await selectProject(".");
      }
      await fetchProjects();
      toast(t(action === "archive" ? "项目已归档" : action === "restore" ? "项目已恢复" : "项目已更新"));
    } catch (error) { toast(error.message, true); }
  }

  async function createProject(event) {
    event.preventDefault();
    const name = $("new-project-name").value.trim();
    if (!name) return;
    try {
      const response = await postJson("/api/projects", { name, project_id: $("new-project-id").value.trim(), description: $("new-project-description").value.trim() });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      $("project-form").reset(); $("project-dialog").close();
      await fetchProjects();
      await selectProject(data.project_id);
      toast(t("项目已创建"));
    } catch (error) { toast(error.message, true); }
  }

  async function selectProject(projectId) {
    try {
      const response = await postJson("/api/select-project", { project_id: projectId });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.projectId = data.project_id;
      app.runKey = "";
      resetRunBox();
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
      toast(t("已切换到项目 {p0}", { p0: data.name }));
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
        return `<button class="file-entry ${entry.kind} ${entry.path === app.filePath ? "selected" : ""} ${disabled ? "unsupported" : ""}" data-file-path="${esc(entry.path)}" data-file-kind="${entry.kind}" ${disabled ? "disabled" : ""} title="${esc(title)}"><i data-lucide="${icon}"></i><span>${esc(entry.name)}</span>${entry.kind === "file" ? `<small>${formatBytes(entry.size)}${disabled ? t(" · 不可预览") : ""}</small>` : ""}</button>`;
      }).join("");
      $("file-tree").innerHTML = parent + rows || `<div class="empty-state">${t("此文件夹为空")}</div>`;
      if (data.truncated) $("file-tree").insertAdjacentHTML("afterbegin", `<div class="file-list-note">${esc(t("仅显示前 500 项"))}</div>`);
      $("file-tree").querySelectorAll("[data-file-kind]:not(:disabled)").forEach((button) => button.addEventListener("click", () => button.dataset.fileKind === "directory" ? openProjectDirectory(button.dataset.filePath) : openProjectFile(button.dataset.filePath)));
      $("file-tree").querySelector("[data-file-parent]")?.addEventListener("click", () => openProjectDirectory(app.fileDirectory.split("/").slice(0, -1).join("/")));
      window.DrugStudioControls.icons();
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
      $("file-molecule-preview")?.remove(); app.fileScene?.dispose(); app.fileScene = null;
      if (/\.(pdb|cif|mmcif|sdf|mol|pdbqt)$/i.test(data.path)) {
        const detail = document.createElement("details"); detail.id = "file-molecule-preview"; detail.className = "molecule-tools";
        const summary = document.createElement("summary"); summary.textContent = t("三维预览"); detail.append(summary);
        const host = document.createElement("div"); host.className = "viewer-canvas"; host.style.height = "320px"; detail.append(host);
        $("editor-file-label").closest(".context-editor")?.append(detail);
        if (!detail.isConnected) $("editor-file-label").parentElement.parentElement.append(detail);
        detail.addEventListener("toggle", () => { if (detail.open && !app.fileScene && window.$3Dmol) { try { app.fileScene = new DrugStudioMolecule.Scene(host); const ext = data.path.split('.').pop().toLowerCase(); app.fileScene.add(data.content, ext === 'mmcif' ? 'cif' : ext, /sdf|mol/.test(ext) ? 'ball' : 'cartoon'); app.fileScene.focus(); } catch (error) { host.textContent = error.message; } } });
      }
      if (app.codeEditor) app.codeEditor.setValue(data.content); else $("file-editor").value = data.content;
      $("editor-file-label").textContent = data.path; $("editor-empty").hidden = true; $("save-file").disabled = false;
      setEditorMode(data.path);
      requestAnimationFrame(() => app.codeEditor?.refresh());
      document.querySelectorAll(".file-entry").forEach((node) => node.classList.toggle("selected", node.dataset.filePath === path));
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
      minimumContrastRatio: 4.5,
      theme: terminalTheme()
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
    if (!$("terminal-dock").classList.contains("open") || document.hidden) return;
    if (!createTerminal()) return;
    if (app.terminalSession) {
      app.terminal.options.disableStdin = false;
      requestAnimationFrame(resizeTerminal);
      if (!app.terminalPollTimer) app.terminalPollTimer = window.setInterval(pollTerminal, 350);
      return;
    }
    if (app.terminalStarting) return;
    app.terminalStarting = true;
    try {
      const response = await postJson("/api/project/terminal/start", { cwd: app.terminalCwd });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.terminalSession = data.session_id; app.terminalCwd = data.cwd || ""; app.terminalCursor = 0;
      $("terminal-cwd").textContent = `/${app.projectId}${app.terminalCwd ? `/${app.terminalCwd}` : ""}`;
      app.terminal.options.disableStdin = false;
      if ($("terminal-dock").classList.contains("open") && !document.hidden) {
        await resizeTerminal(); await pollTerminal();
        if ($("terminal-dock").classList.contains("open") && !document.hidden && app.terminalSession) {
          if (!app.terminalPollTimer) app.terminalPollTimer = window.setInterval(pollTerminal, 350);
          app.terminal.focus();
        }
      }
    } catch (error) { app.terminal.writeln(`\r\n\x1b[31m${error.message}\x1b[0m`); }
    finally { app.terminalStarting = false; }
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

  async function persistChat({ projectId = app.projectId, chatId = app.chatId, history = app.chatHistory } = {}) {
    if (!chatId) return;
    const response = await postJson("/api/chats/save",
      { project_id: projectId, chat_id: chatId, messages: history });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
    if (app.projectId !== projectId) return;
    const current = app.chatSessions.find((item) => item.chat_id === chatId);
    if (current) Object.assign(current, data); else app.chatSessions.unshift(data);
    renderChatList();
  }

  function chatTitle(chat) {
    return !chat?.title || (!chat.messages?.length && ["新对话", "New conversation"].includes(chat.title))
      ? t("新对话") : chat.title;
  }

  function renderChatList() {
    const list = $("chat-list");
    document.querySelectorAll("[data-chat-filter]").forEach((button) => { button.classList.toggle("active", button.dataset.chatFilter === app.chatFilter); button.setAttribute("aria-pressed",String(button.dataset.chatFilter === app.chatFilter)); });
    const rows = app.chatSessions.filter((chat) => app.chatFilter === "archived" ? Boolean(chat.archived_at) : !chat.archived_at);
    list.innerHTML = rows.length ? rows.map((chat) =>
      `<div class="chat-list-row"><button type="button" class="chat-list-item ${chat.chat_id === app.chatId ? "active" : ""}" data-chat-id="${esc(chat.chat_id)}" title="${esc(chatTitle(chat))}"><i data-lucide="message-square"></i><span>${esc(chatTitle(chat))}</span></button><button type="button" class="chat-archive-button" data-chat-archive="${esc(chat.chat_id)}" title="${chat.archived_at ? t("恢复对话") : t("归档对话")}" aria-label="${chat.archived_at ? t("恢复对话") : t("归档对话")}"><i data-lucide="${chat.archived_at ? "archive-restore" : "archive"}"></i></button></div>`).join("") :
      `<div class="chat-list-empty">${app.chatFilter === "archived" ? t("暂无归档对话") : t("暂无活跃对话")}</div>`;
    list.querySelectorAll("[data-chat-id]").forEach((button) => button.addEventListener("click", () => selectChat(button.dataset.chatId)));
    list.querySelectorAll("[data-chat-archive]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      const chat = app.chatSessions.find((item) => item.chat_id === button.dataset.chatArchive);
      updateChatArchive(button.dataset.chatArchive, !chat?.archived_at);
    }));
    window.DrugStudioControls.icons();
    const current = app.chatSessions.find((item) => item.chat_id === app.chatId);
    $("project-title").textContent = chatTitle(current);
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

  function syncTerminalToggle() {
    const open = $("terminal-dock").classList.contains("open");
    const button = $("open-terminal");
    button.classList.toggle("active", open);
    button.setAttribute("aria-expanded", String(open));
    button.title = t(open ? "收起终端" : "打开终端");
    button.setAttribute("aria-label", button.title);
  }

  function toggleTerminalDock() {
    if ($("terminal-dock").classList.contains("open")) closeTerminalDock();
    else openTerminalDock();
  }

  function openTerminalDock() {
    $("terminal-dock").classList.add("open");
    syncTerminalToggle();
    requestAnimationFrame(ensureTerminal);
  }

  function closeTerminalDock() {
    $("terminal-dock").classList.remove("open");
    syncTerminalToggle();
    if (app.terminalPollTimer) window.clearInterval(app.terminalPollTimer);
    app.terminalPollTimer = null;
  }

  function renderChatStatus() {
    if (!$('chat-status')) return;
    const ready = Boolean(app.state?.chat_available);
    $("chat-status").textContent = ready ? t("模型已连接") : t("模型未连接");
    $("chat-status").classList.toggle("chat-status-offline", !ready);
  }

  function messageText(value) {
    const isText = (block) => block && ["text", "output_text", "input_text"].includes(block.type) && typeof block.text === "string";
    if (Array.isArray(value)) return value.map((block) => typeof block === "string" ? block : isText(block) ? block.text : "").join("");
    if (value && typeof value === "object") return isText(value) ? value.text : "";
    return typeof value === "string" ? value : "";
  }

  function chatWelcome() { return `<div class="chat-welcome"><div class="chat-welcome-mark"><i data-lucide="sparkles"></i></div><h2>${esc(t("从一个研究问题开始"))}</h2><p>${esc(t("我可以帮你读取结构、准备受体和配体、运行对接，并解释每一步的证据。"))}</p><div class="chat-suggestions"><button type="button" data-chat-suggestion="${esc(t("检查当前项目的结构和配体输入"))}">${esc(t("检查当前项目的输入"))}</button><button type="button" data-chat-suggestion="${esc(t("解释当前工作流下一步需要什么"))}">${esc(t("解释下一步"))}</button><button type="button" data-chat-suggestion="${esc(t("查看当前项目有哪些结果和风险"))}">${esc(t("查看结果与风险"))}</button></div></div>`; }

  const pendingChats = new Map();
  const chatKey = (projectId = app.projectId, chatId = app.chatId) => JSON.stringify([projectId, chatId]);
  const currentTurnVisible = () => app.chatTurn && chatKey() === app.chatTurn.key;
  const chatAtBottom = () => {
    const node = $("chat-messages");
    return node.scrollHeight - node.scrollTop - node.clientHeight < 80;
  };
  function updateChatJump() { $("chat-jump-latest").hidden = chatAtBottom(); }

  function renderChat({ forceScroll = false } = {}) {
    const container = $("chat-messages"), key = chatKey();
    const changedChat = container.dataset.chatKey !== key;
    const previousTop = container.scrollTop;
    const follow = forceScroll || changedChat || chatAtBottom();
    if (changedChat) { container.replaceChildren(); container.dataset.chatKey = key; }
    app.pendingAction = pendingChats.get(key) || null;
    const messages = [...(app.chatHistory || [])];
    if (currentTurnVisible() && app.chatTurn.preview) messages.push({ role: 'assistant', content: app.chatTurn.preview, streaming: true });
    const locale = document.documentElement.lang;
    let articles = [...container.querySelectorAll(':scope > .chat-message')];
    if (messages.length) {
      container.querySelector('.chat-welcome')?.remove();
      messages.forEach((item, index) => {
        let article = articles[index];
        if (!article) {
          article = document.createElement('article');
          container.insertBefore(article, container.querySelector('.chat-tail'));
        }
        const source = messageText(item.content);
        const signature = JSON.stringify([item.role, source, item.actions, item.streaming, item.error, locale]);
        if (article._signature === signature) return;
        article._signature = signature; article._source = source;
        article.className = `chat-message ${item.role === 'user' ? 'user' : 'assistant'}`;
        article.setAttribute('aria-label', item.role === 'user' ? t('你的消息') : t('助手回复'));
        article.innerHTML = `          <div class="chat-content">${item.role === 'user' ? `<div class="chat-plain">${esc(source)}</div>` : window.DrugStudioChat.render(source)}</div>
          ${item.actions?.length ? `<div class="chat-actions">${item.actions.map(action => `<span>${esc(action.tool)}${action.path ? ` · ${esc(action.path)}` : ''}</span>`).join('')}</div>` : ''}
          ${item.error ? `<div class="chat-error" role="status">${esc(item.error)}</div>` : ''}
          ${item.role !== 'user' && !item.streaming ? `<button type="button" class="chat-message-copy icon-button" data-copy-message title="${esc(t('复制回复'))}" aria-label="${esc(t('复制回复'))}"><i data-lucide="copy"></i></button>` : ''}`;
      });
      articles.slice(messages.length).forEach(node => node.remove());
    } else {
      articles.forEach(node => node.remove());
      let welcome = container.querySelector('.chat-welcome');
      if (!welcome || welcome.dataset.locale !== locale) {
        welcome?.remove();
        container.insertAdjacentHTML('afterbegin', chatWelcome());
        container.querySelector('.chat-welcome').dataset.locale = locale;
      }
    }
    let tail = container.querySelector('.chat-tail');
    if (!tail) { tail = document.createElement('div'); tail.className = 'chat-tail'; container.append(tail); }
    const busy = app.chatBusy && (!app.chatTurn || currentTurnVisible());
    const tailSignature = JSON.stringify([app.pendingAction, busy, locale]);
    if (tail._signature !== tailSignature) {
      const reason = $('pending-reason')?.value || '';
      tail._signature = tailSignature;
      tail.innerHTML = pendingActionCard() + (busy ? `<div class="chat-typing" aria-live="polite"><span class="typing-dots"><i></i><i></i><i></i></span><span>${esc(t('正在处理'))}</span></div>` : '');
      if ($('pending-reason')) $('pending-reason').value = reason;
      $('approve-action')?.addEventListener('click', () => decideChatAction(true));
      $('reject-action')?.addEventListener('click', () => decideChatAction(false));
      window.DrugStudioControls.icons();
    }
    window.DrugStudioControls.icons();
    container.scrollTop = follow ? container.scrollHeight : previousTop;
    updateChatJump();
    const current = app.chatSessions.find(item => item.chat_id === app.chatId);
    $('project-title').textContent = chatTitle(current);
  }

  // A turn can come back suspended: the model asked to run a command or
  // write a file, and nothing has happened yet. The card is the gate, so it
  // lives in the transcript rather than as a toast that can be missed.
  function absorbChatReply(history, data, key = chatKey()) {
    if (data.reply) history.push({ role: "assistant", content: data.reply, actions: data.actions || [] });
    if (data.pending) pendingChats.set(key, data.pending); else pendingChats.delete(key);
    if (key === chatKey()) app.pendingAction = data.pending || null;
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
      <div class="pending-heading"><i data-lucide="shield-alert"></i><strong>${t(isCommand ? t("AI 请求执行命令") : t("AI 请求写入文件"))}</strong></div>
      ${detail}
      <label class="pending-reason">${t("理由（拒绝时会转达给 AI）")}<input id="pending-reason" class="input-control" type="text" placeholder="${t("可选")}"></label>
      <div class="pending-actions">
        <button class="secondary-button" type="button" id="reject-action">${t("拒绝")}</button>
        <button class="primary-button" type="button" id="approve-action">${t("批准并执行")}</button>
      </div>
    </article>`;
  }

  function beginChatTurn() {
    const turn = { key: chatKey(), projectId: app.projectId, runKey: app.runKey,
      chatId: app.chatId, history: app.chatHistory, preview: '', timer: null };
    app.chatTurn = turn; app.chatBusy = true; $('send-chat').disabled = true; $('send-chat').setAttribute('aria-busy','true');
    return turn;
  }

  async function readChatResponse(path, payload, turn) {
    const response = await postJson(path, { ...payload, stream: true,
      project_id: turn.projectId, run_key: turn.runKey, chat_id: turn.chatId });
    if (!response.ok || !response.headers.get('Content-Type')?.includes('application/x-ndjson')) {
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || response.statusText);
      return result;
    }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', result, complete = false;
    const schedule = () => {
      if (turn.timer !== null) return;
      turn.timer = window.setTimeout(() => {
        turn.timer = null;
        if (app.chatTurn === turn && currentTurnVisible()) renderChat();
      }, 80);
    };
    const consume = line => {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.type === 'response_start') { turn.preview = ''; schedule(); }
      else if (event.type === 'delta') { turn.preview += event.text || ''; schedule(); }
      else if (event.type === 'error') throw new Error(event.error || t('无法回复'));
      else if (event.type === 'done') { result = event.result; complete = true; }
    };
    try {
      while (!complete) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        let at;
        while ((at = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, at); buffer = buffer.slice(at + 1); consume(line);
        }
        if (done) { if (buffer.trim()) consume(buffer); break; }
      }
      if (!complete) throw new Error(t('回复中断，已保留收到的内容'));
      return result;
    } finally { await reader.cancel().catch(() => {}); }
  }

  function recordChatError(turn, error) {
    turn.history.push({ role: 'assistant', content: turn.preview
      ? `${turn.preview}\n\n> ${t('回复中断，已保留收到的内容')}：${error.message}`
      : `${t('无法回复')}：${error.message}` });
  }

  async function finishChatTurn(turn) {
    if (turn.timer !== null) window.clearTimeout(turn.timer);
    turn.preview = '';
    if (app.chatTurn === turn) { app.chatTurn = null; app.chatBusy = false; $('send-chat').disabled = false; $('send-chat').removeAttribute('aria-busy'); }
    renderChatStatus();
    try { await persistChat(turn); } catch (error) { toast(error.message, true); }
    if (chatKey() === turn.key) { app.chatHistory = turn.history; renderChat(); }
  }

  async function decideChatAction(approved) {
    const pending = app.pendingAction;
    if (!pending || app.chatBusy) return;
    const reason = $('pending-reason')?.value || '';
    const turn = beginChatTurn();
    pendingChats.delete(turn.key); app.pendingAction = null;
    turn.history.push({ role: 'user', content: approved ? t('（已批准该操作）') : `${t('（已拒绝该操作）')}${reason ? ` ${reason}` : ''}` });
    renderChat({ forceScroll: true });
    try {
      const result = await readChatResponse('/api/project/chat/decide', { action_id: pending.action_id, approved, reason }, turn);
      absorbChatReply(turn.history, result, turn.key);
    } catch (error) { recordChatError(turn, error); }
    await finishChatTurn(turn);
  }

  async function sendChat(event) {
    event.preventDefault();
    const input = $('chat-input'), content = input.value.trim();
    if (!content || app.chatBusy) return;
    const turn = beginChatTurn();
    if (app.pendingAction) {
      const abandoned = app.pendingAction;
      pendingChats.delete(turn.key); app.pendingAction = null;
      postJson('/api/project/chat/decide', {
        action_id: abandoned.action_id, approved: false, resume: false,
        reason: t('用户未处理该请求，改为发送了新消息'),
        project_id: turn.projectId, run_key: turn.runKey
      }).catch(() => {});
    }
    turn.history.push({ role: 'user', content }); input.value = ''; resizeChatInput();
    renderChat({ forceScroll: true });
    $('chat-status').textContent = t('Agent 正在处理');
    $('chat-status').classList.remove('chat-status-offline');
    const payload = { messages: turn.history, model: $('chat-model').value.trim(), temperature: Number($('chat-temperature').value) };
    try { await persistChat(turn); } catch (error) { toast(error.message, true); }
    try {
      const result = await readChatResponse('/api/project/chat', payload, turn);
      absorbChatReply(turn.history, result, turn.key);
    } catch (error) { recordChatError(turn, error); }
    await finishChatTurn(turn);
  }

  function resizeChatInput() {
    const input = $("chat-input");
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(Math.max(input.scrollHeight, 52), 180)}px`;
  }

  async function selectRun(runKey) {
    try {
      const response = await postJson("/api/select-run", { run_key: runKey });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      // Selection is client state now; the server only confirmed the key.
      resetRunBox();
      app.runKey = data.selected || runKey;
      if (data.project_id && data.project_id !== app.projectId) {
        app.projectId = data.project_id;
        app.chatId = null; app.chatHistory = [];
        await fetchProjects();
      }
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
    const previous = app.structureArtifact;
    const select = $("structure-select");
    select.innerHTML = items.length ? items.map((item, index) => `<option value="${index}">${esc(t(({prepared_receptor_pdb:"对接受体",repaired_receptor_pdb:"修复结构",supplied_structure:"输入结构",experimental_structure:"实验结构",predicted_structure:"预测结构"})[item.kind] || item.kind))} · ${esc(item.metadata?.identifier || item.path.split("/").pop())} · ${esc((item.sha256 || "").slice(0,8))}</option>`).join("") : `<option value="">${t("暂无结构")}</option>`;
    app.structureArtifact = items.find(item => item.path === app.structureArtifact?.path) || items[0] || null;
    if (previous && (previous.path !== app.structureArtifact?.path || previous.sha256 !== app.structureArtifact?.sha256)) disposeViewers();
    if (app.structureArtifact) select.value = String(items.indexOf(app.structureArtifact));
    $("structure-file").textContent = app.structureArtifact?.path || "—";
    const quality = app.state.stage_results?.structure?.metadata?.quality || app.state.stage_results?.receptor?.metadata?.structure_quality || app.state.stage_results?.pocket?.metadata?.structure_quality;
    if (quality) {
      $("structure-quality-title").textContent = quality.gate === "pass" || quality.status === "pass" ? t("结构质量通过") : t("结构需要关注");
      const chips = [[t("来源"), app.structureArtifact?.metadata?.source || app.structureArtifact?.kind || "—"], [t("链数"), quality.chain_count ?? "—"], [t("缺失残基"), quality.missing_residues ?? quality.missing_residue_count ?? "—"], [t("分辨率"), quality.resolution_A != null ? `${fmt(quality.resolution_A, 2)} Å` : "—"]];
      $("structure-quality-chips").innerHTML = chips.map(([label, value]) => `<span class="quality-chip"><small>${esc(t(label))}</small><strong>${esc(value)}</strong></span>`).join("");
    } else {
      $("structure-quality-title").textContent = items.length ? t("已获得结构，等待质量评估") : t("等待结构产物");
      $("structure-quality-chips").innerHTML = `<span class="quality-chip muted">${esc(t("质量数据将在结构阶段完成后显示"))}</span>`;
    }
  }

  function poseChoices() {
    return (app.state.results?.tasks || []).filter(task => task.viewable && task.receptor_artifact?.sha256 === app.structureArtifact?.sha256).flatMap(task => task.poses.map(pose => ({task, pose, value:`${task.task_id}:${pose.record_index}`})));
  }

  function renderPoseChoices() {
    const select = $("pose-select"), previous = select.value;
    select.innerHTML = `<option value="">${t("不叠加姿态")}</option>` + poseChoices().map(item => `<option value="${esc(item.value)}">${esc(item.task.state_id || item.task.ligand_id)} · ${t("姿态")} ${esc(item.pose.mode)} · ${fmt(item.pose.affinity_kcal_mol,3)}${item.pose.qc_pass === false ? " · QC failed" : ""}</option>`).join("");
    if (poseChoices().some(item => item.value === previous)) select.value = previous;
  }

  function currentPockets() {
    return app.state.stage_results?.pocket?.metadata?.candidates || [];
  }

  function renderPockets() {
    const pockets = currentPockets();
    const referenceResidues = app.state.target?.binding_site_residues || [];
    $("pocket-count").textContent = pockets.length || (referenceResidues.length ? t("参考位点") : "—");
    const html = pockets.length ? pockets.map((pocket) => {
      const selected = app.selectedPocket?.rank === pocket.rank || (!app.selectedPocket && pocket.selected);
      return `<div class="pocket-card ${selected ? "selected" : ""}" data-pocket-rank="${pocket.rank}">
        <div class="pocket-row"><span class="pocket-name">${esc(pocket.name || `Pocket ${pocket.rank}`)}</span><span class="pocket-score">P2Rank score ${fmt(pocket.p2rank_score ?? pocket.score, 2)}</span></div>
        <div class="pocket-meta"><span>rank ${esc(pocket.rank)}</span><span>predictors ${(pocket.sources || []).join(" + ") || "P2Rank"}</span><span>P2Rank p ${fmt(pocket.p2rank_probability ?? pocket.probability, 2)}</span>${pocket.fpocket_druggability_score != null ? `<span>fpocket druggability ${fmt(pocket.fpocket_druggability_score, 2)}</span>` : ""}<span>${(pocket.residue_ids || []).length} residues</span><span>${esc(String(pocket.evidence_class || "computational_only").replaceAll("_", " "))}</span>${pocket.reference_site_overlap_count ? `<span>${pocket.reference_site_overlap_count} reference-site residues overlap</span>` : ""}${pocket.predictor_center_distance_A != null ? `<span>centers agree within ${fmt(pocket.predictor_center_distance_A, 1)} Å</span>` : ""}${pocket.human_review_recommended ? `<span>${t("建议审核")}</span>` : ""}</div>
      </div>`;
    }).join("") : referenceResidues.length
      ? `<div class="pocket-card reference-site-card"><div class="pocket-row"><span class="pocket-name">${t("参考结合位点")}</span><span class="site-source">${t("来自输入")}</span></div><div class="pocket-meta"><span>${referenceResidues.length} ${t("个残基")}</span><span>${t("并非独立预测的口袋")}</span></div><button type="button" class="secondary-button locate-site" data-reference-site><i data-lucide="crosshair"></i>${t("定位位点")}</button></div>`
      : `<div class="empty-state">${esc(t("暂无口袋预测或参考位点。"))}</div>`;
    const quality = app.state.stage_results?.pocket?.metadata?.structure_quality;
    const qualityHtml = quality ? `<div class="quality-pill">Structure QC: ${esc(quality.gate)} · ${quality.chain_count} chains · median CA B ${fmt(quality.ca_bfactor_median, 1)}${quality.review_flags?.length ? ` · ${quality.review_flags.length} review flags` : ""}</div>` : "";
    $("pocket-list").innerHTML = qualityHtml + html;
    $("review-pocket-list").innerHTML = pockets.length ? pockets.map((pocket) => {
      const selected = app.selectedPocket?.rank === pocket.rank;
      return `<div class="review-pocket-card ${selected ? "selected" : ""}" data-review-rank="${pocket.rank}"><span class="review-rank">${pocket.rank}</span><div><strong>${esc(pocket.name || `Pocket ${pocket.rank}`)}</strong><small>${(pocket.residue_ids || []).slice(0, 5).map(esc).join(" · ") || t("无残基标签")} · ${(pocket.sources || ["P2Rank"]).join(" + ")}${pocket.predictor_center_distance_A != null ? ` · centers agree ${fmt(pocket.predictor_center_distance_A, 1)} Å` : ""}${pocket.human_review_recommended ? ` · ${t("建议审核")}` : ""}</small></div><span class="pocket-score">${fmt(pocket.p2rank_probability ?? pocket.score, 2)}</span></div>`;
    }).join("") : `<div class="empty-state">${esc(t("暂无候选口袋。"))}</div>`;
    document.querySelectorAll("[data-pocket-rank]").forEach((node) => node.addEventListener("click", () => selectPocket(Number(node.dataset.pocketRank))));
    document.querySelectorAll("[data-review-rank]").forEach((node) => node.addEventListener("click", () => selectPocket(Number(node.dataset.reviewRank))));
    $("pocket-list").querySelector("[data-reference-site]")?.addEventListener("click", async () => { if (!app.proteinScene) await loadProtein(); setProteinStyle("site"); $("protein-viewer").scrollIntoView({block:"nearest"}); });
    if (!app.selectedPocket && pockets.length) selectPocket(Number(pockets.find((item) => item.selected)?.rank || pockets[0].rank), false);
    else if (!app.selectedPocket) renderPocketDetail();
  }

  function selectPocket(rank, rerender = true) {
    app.selectedPocket = currentPockets().find((item) => Number(item.rank) === rank) || null;
    if (app.selectedPocket) {
      const center = app.selectedPocket.box?.center || app.selectedPocket.center || [];
      const size = app.selectedPocket.box?.size || [];
      fillBoxInputs({center, size});
      renderPocketDetail();
      if (rerender) setProteinStyle("site");
      onBoxInputChanged();
    }
    if (rerender) renderPockets();
  }

  function renderPocketDetail() {
    const pocket = app.selectedPocket;
    if (!pocket) {
      const residues = app.state.target?.binding_site_residues || [];
      $("pocket-detail").innerHTML = residues.length
        ? `<div class="detail-item"><label>${t("参考位点")}</label><strong>${t("由靶点输入提供")}</strong></div><div class="detail-item"><label>${t("残基")}</label><strong>${residues.length}</strong></div><div class="residue-list">${residues.map((item) => `<span class="residue-chip">${esc(item)}</span>`).join("")}</div>`
        : `<span class="muted">${t("没有预测口袋或参考位点残基。")}</span>`;
      return;
    }
    const center = pocket.box?.center || pocket.center || [];
    const size = pocket.box?.size || [];
    $("pocket-detail").innerHTML = `<div class="detail-item"><label>${t("中心 X / Y / Z")}</label><strong class="mono">${center.map((n) => fmt(n, 2)).join(" · ") || "—"}</strong></div><div class="detail-item"><label>${t("盒尺寸 Å")}</label><strong class="mono">${size.map((n) => fmt(n, 1)).join(" · ") || "—"}</strong></div><div class="detail-item"><label>${t("概率")}</label><strong>${fmt(pocket.probability, 3)}</strong></div><div class="detail-item"><label>${t("表面点")}</label><strong>${esc(pocket.surface_points ?? "—")}</strong></div><div class="residue-list">${(pocket.residue_ids || []).map((item) => `<span class="residue-chip">${esc(item)}</span>`).join("") || `<span class="muted">${t("无残基标签")}</span>`}</div>`;
  }

  function structureCoordinateKey() {
    return JSON.stringify([app.projectId, app.runKey, app.structureArtifact?.path, app.structureArtifact?.sha256]);
  }

  async function loadStructureCoordinates() {
    const artifact = app.structureArtifact;
    if (!artifact) { app.structureAtoms = []; renderBoxCoverage(); return null; }
    const key = structureCoordinateKey();
    if (key === app.structureCoordinateKey) { renderBoxCoverage(); return app.structureText; }
    const request = ++app.structureRequestId;
    app.structureAtoms = []; renderBoxCoverage();
    try {
      const response = await fetch(fileUrl(artifact.path));
      if (!response.ok) throw new Error(`${t("结构请求失败")} (${response.status})`);
      const text = await response.text();
      if (request !== app.structureRequestId || key !== structureCoordinateKey()) return null;
      app.structureAtoms = parseReceptorAtoms(text);
      app.structureCoordinateKey = key; app.structureText = text;
      renderBoxCoverage();
      return text;
    } catch (error) {
      if (request === app.structureRequestId && key === structureCoordinateKey()) {
        app.structureAtoms = []; renderBoxCoverage();
        showViewerMessage("protein-viewer", `${t("无法载入结构")}：${error.message || t("请求失败")}`);
      }
      return null;
    }
  }

  async function loadProtein() {
    const key = structureCoordinateKey();
    const text = await loadStructureCoordinates();
    if (text === null || key !== structureCoordinateKey()) return;
    $("structure-file").textContent = app.structureArtifact.path;
    if (!window.$3Dmol) { $("protein-viewer").innerHTML = `<div class="viewer-placeholder">${t("3D 查看器组件不可用")}</div>`; return; }
    if (!webglAvailable()) { showViewerMessage("protein-viewer", t("浏览器禁用了 WebGL；开启硬件加速后才能查看 3D 结构。")); return; }
    try {
      app.proteinScene?.dispose();
      app.proteinViewer?.clear?.();
      $("protein-viewer").innerHTML = "";
      const scene = new DrugStudioMolecule.Scene($("protein-viewer"));
      app.proteinScene = scene;
      const viewer = scene.viewer;
      app.proteinViewer = viewer; app.boxShapes = []; app.pocketLabel = null;
      scene.add(text, /\.(cif|mmcif)$/i.test(app.structureArtifact.path) ? "cif" : "pdb");
      if (/\.(cif|mmcif)$/i.test(app.structureArtifact.path)) app.structureAtoms = scene.atoms().map(a => ({x:a.x,y:a.y,z:a.z,key:`${a.chain || ""}:${a.resi}${a.icode || ""}`}));
      setProteinStyle("cartoon");
      await addPoseOverlay(viewer);
      if (app.proteinViewer !== viewer || key !== structureCoordinateKey()) return;
      onBoxInputChanged();
      fitProteinView();
      bindStructureSelection();
    } catch (error) {
      if (key === structureCoordinateKey()) showViewerMessage("protein-viewer", `${t("无法载入结构")}：${error.message || t("3D 渲染失败")}`);
    }
  }

  function fitProteinView() {
    if (!app.proteinViewer) return;
    app.proteinViewer.zoomTo();
    // 3Dmol fits the vertical field of view; narrow drawers also need a
    // horizontal margin so the wireframe is not clipped at the sides.
    const host = $("protein-viewer");
    if (host.clientWidth > 0 && host.clientWidth < host.clientHeight) {
      app.proteinViewer.zoom(host.clientWidth / host.clientHeight);
    }
    app.proteinViewer.render();
  }

  async function addPoseOverlay(viewer) {
    const choice = poseChoices().find(item => item.value === $("pose-select").value);
    if (!choice) return;
    const response = await fetch(fileUrl(choice.task.pose_artifact.path));
    if (!response.ok) throw new Error("Unable to load docked pose");
    const text = await response.text();
    if (app.proteinViewer !== viewer) return;
    const records = text.split(/\$\$\$\$\r?\n?/);
    const record = records[choice.pose.record_index];
    if (!record?.trim()) throw new Error("Pose record is missing");
    app.proteinScene.add(record + "$$$$\n", "sdf", "ball");
  }

  function residueSelection(value) {
    const match = String(value).match(/^(.*):(-?\d+)([A-Za-z]?)$/) || String(value).match(/^([A-Za-z]+)(-?\d+)([A-Za-z]?)$/);
    return match ? {model:0, chain:match[1].trim(), resi:Number(match[2]), icode:match[3] || ""} : null;
  }

  function selectionViewKey() { return `drugstudio-view:${structureCoordinateKey()}:${$("pose-select").value}`; }

  function bindStructureSelection() {
    const scene = app.proteinScene;
    if (!scene) return;
    const residues = scene.residues();
    $("structure-source").textContent = JSON.stringify({artifact:app.structureArtifact?.artifact_id,path:app.structureArtifact?.path,sha256:app.structureArtifact?.sha256,model:1},null,2);
    syncProteinControls(); $("molecule-surface").checked = false;
    $("selection-box-summary").textContent = ''; app.selectionDraft = null;
    $("structure-sequence").innerHTML = `<div class="sequence-caption">${t("坐标序列 · 模型 1")} · ${t("仅包含有坐标的残基；编号间隔不代表完整序列。")}</div>` + [...new Set(residues.map(r => r.chain))].map(chain => `<div class="sequence-chain"><span>${t("链")} ${esc(chain || '∅')}</span><div>${residues.map((r,i) => r.chain !== chain ? '' : `<button type="button" data-residue="${i}" title="${esc(`${r.chain || '∅'}:${r.resi}${r.icode} ${r.resn}`)}"><strong>${r.letter}</strong><small>${r.resi}${esc(r.icode)}</small></button>`).join('')}</div></div>`).join('');
    let anchor = null;
    scene.onSelection = atoms => {
      const keys = new Set(atoms.map(DrugStudioMolecule.residueKey));
      $("structure-sequence").querySelectorAll('[data-residue]').forEach(button => { const active = keys.has(residues[Number(button.dataset.residue)].key); button.classList.toggle('selected',active); button.setAttribute('aria-pressed',String(active)); });
      const names = [...new Set(atoms.filter(a => a.model === 0).map(a => `${a.chain || '∅'}:${a.resi}${a.icode || ''}`))];
      $("viewer-hint").textContent = atoms.length ? `${names.slice(0,4).join(', ')}${names.length > 4 ? '…' : ''} · ${t("已选择")} ${keys.size} ${t("个残基")} · ${atoms.length} ${t("原子")}` : `${t("点击序列中的残基以高亮并聚焦")} · ${t("已选择")} 0 ${t("个残基")} · 0 ${t("原子")}`;
      $("selection-message").textContent = '';
      app.selectionDraft = null; $("selection-box-summary").textContent = '';
    };
    $("structure-sequence").querySelectorAll('[data-residue]').forEach(button => button.addEventListener('click', e => {
      const i = Number(button.dataset.residue), residue = residues[i];
      const chosen = e.ctrlKey || e.metaKey ? new Set(scene.selection) : new Set();
      if (e.shiftKey && anchor !== null && residues[anchor].chain === residue.chain) {
        residues.slice(Math.min(anchor,i),Math.max(anchor,i)+1).filter(r => r.chain === residue.chain).forEach(r => r.ids.forEach(id => chosen.add(id)));
      } else { const remove = residue.ids.every(id => chosen.has(id)); residue.ids.forEach(id => remove ? chosen.delete(id) : chosen.add(id)); anchor = i; }
      scene.setSelection(chosen);

    }));
    scene.onSelection(scene.selected());
  }

  function bindMolecularControls() {
    const action = (id, fn) => $(id).addEventListener('click', async () => { try { if (!app.proteinScene) throw new Error(t("请先打开结构")); if (id === 'selection-apply') await busy(id, () => fn(app.proteinScene)); else await fn(app.proteinScene); } catch (error) { $("selection-message").textContent = error.message; } });
    $("selection-form").addEventListener('submit',e => { e.preventDefault(); try { app.proteinScene?.command($("selection-expression").value); } catch (error) { $("selection-message").textContent = error.message; } });
    $("molecule-style").addEventListener('change',e => { if (app.proteinScene) { app.proteinScene.siteMode = false; app.proteinScene.style = e.target.value; app.proteinScene.paint(); syncProteinControls(); } });
    const surface = async () => { const scene = app.proteinScene; if (!scene) return; $("surface-status").textContent = t("正在更新"); try { await scene.surface($("molecule-surface").checked,Number($("surface-opacity").value)); if (scene === app.proteinScene) $("surface-status").textContent = ''; } catch (error) { $("surface-status").textContent = error.message; } };
    $("molecule-surface").addEventListener('change',surface); $("surface-opacity").addEventListener('change',surface);
    action('selection-focus',scene => scene.focus());
    action('selection-clear',scene => scene.setSelection(new Set()));
    action('selection-label',scene => scene.labelSelection());
    action('selection-distance',scene => { $("selection-message").textContent = `${scene.distance().toFixed(2)} Å`; });
    action('selection-save',scene => { localStorage.setItem(selectionViewKey(),JSON.stringify(scene.save())); toast(t("视图已保存在此浏览器")); });
    action('selection-restore',scene => { scene.restore(JSON.parse(localStorage.getItem(selectionViewKey()) || 'null')); syncProteinControls(); });
    action('selection-image',scene => { scene.viewer.render(); const link = document.createElement('a'); link.href = scene.viewer.pngURI(); link.download = 'DrugStudio-structure.png'; link.click(); });
    action('selection-chat',scene => {
      const residues = [...new Set(scene.selected().map(a => `${a.chain || '∅'}:${a.resi}${a.icode || ''}`))];
      if (!residues.length) throw new Error(t("请先选择残基"));
      $("chat-input").value = `${t("请分析所选位点")}\n${app.structureArtifact.artifact_id}\nSHA256: ${app.structureArtifact.sha256}\nModel 1: ${residues.join(', ')}`; resizeChatInput(); $("chat-input").focus();
    });
    action('selection-box',scene => {
      const box = scene.candidateBox(Number($("selection-padding").value));
      app.selectionDraft = { ...box, artifact_id:app.structureArtifact.artifact_id,sha256:app.structureArtifact.sha256,model:1,residues:scene.residues().filter(r => r.ids.some(id => scene.selection.has(id))).map(r => ({chain:r.chain,resi:r.resi,icode:r.icode})), scope:structureCoordinateKey() };
      fillBoxInputs(box); app.boxVisible = true; $("toggle-box").setAttribute('aria-pressed','true'); drawDockingBox();
      $("selection-box-summary").textContent = `${t("草稿")} · ${box.center.map(v => fmt(v,2)).join(', ')} · ${box.size.map(v => fmt(v,1)).join(' × ')} Å`;
    });
    action('selection-apply',async () => {
      const draft = app.selectionDraft, reason = $("selection-reason").value.trim();
      if (!draft || draft.scope !== structureCoordinateKey()) throw new Error(t("请先预览候选盒"));
      if (!reason) throw new Error(t("请填写选择理由"));
      const response = await postJson('/api/review',{stage:'docking_box',action:'override',center:draft.center,size:draft.size,reason,actor:'web-viewer',selection:{artifact_id:draft.artifact_id,sha256:draft.sha256,model:draft.model,residues:draft.residues}});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText);
      app.selectionDraft = null; $("selection-box-summary").textContent = t("已应用，等待重新对接"); await fetchState(); toast(t("位点选择已记录"));
    });
    $("structure-open-file").addEventListener('click',() => showView('files'));
  }

  function siteResidueSelections() {
    const values = app.selectedPocket?.residue_ids?.length
      ? app.selectedPocket.residue_ids
      : app.state?.target?.binding_site_residues || [];
    return values.map(residueSelection).filter(Boolean);
  }

  function syncProteinControls() {
    const scene = app.proteinScene;
    if (!scene) return;
    app.proteinStyle = scene.siteMode ? 'site' : scene.style;
    $("molecule-style").value = scene.style;
    document.querySelectorAll('[data-protein-style]').forEach(button => {
      const active = button.dataset.proteinStyle === app.proteinStyle;
      button.classList.toggle('active', active); button.setAttribute('aria-pressed',String(active));
    });
  }

  function setProteinStyle(style) {
    const scene = app.proteinScene;
    if (!scene) return;
    if (style === 'site') {
      const selections = siteResidueSelections();
      const atoms = scene.atoms().filter(atom => selections.some(sel =>
        atom.model === 0 && atom.chain === sel.chain && atom.resi === sel.resi && atom.icode === sel.icode));
      if (!atoms.length) { toast(t("当前结构未找到位点残基，请检查结构和残基编号。"),true); return; }
      scene.style = 'cartoon'; scene.siteMode = true;
      scene.setSelection(new Set(atoms.map(DrugStudioMolecule.atomKey)));
      const found = new Set(atoms.map(DrugStudioMolecule.residueKey)).size;
      if (found < selections.length) toast(t("部分位点残基未找到坐标：{p0}/{p1}",{p0:found,p1:selections.length}),true);
    } else {
      scene.style = 'cartoon'; scene.siteMode = false; scene.overlays = []; scene.setSelection(new Set()); fitProteinView();
    }
    updatePocketLabel(scene.siteMode ? app.selectedPocket : null);
    syncProteinControls(); drawDockingBox();
  }

  // ---- docking box -------------------------------------------------------
  // The box is what docking actually searches, so it has to be visible next
  // to the structure rather than guessed at from six number fields.

  const BOX_AXES = ["x", "y", "z"];

  function fillBoxInputs(box) {
    BOX_AXES.forEach((axis, index) => {
      $(`center-${axis}`).value = box.center[index] ?? "";
      $(`size-${axis}`).value = box.size[index] ?? 24;
    });
    onBoxInputChanged();
  }

  function resetRunBox() {
    resetParameterContext();
    app.selectedPocket = null; app.structureArtifact = null; app.boxDraftRun = null; app.resultTaskKey = null;
    BOX_AXES.forEach(axis => { $(`center-${axis}`).value = ""; $(`size-${axis}`).value = 24; });
    app.structureAtoms = []; renderBoxCoverage();
  }

  function boxFromInputs() {
    const read = (id) => $(id).value.trim() === "" ? NaN : Number($(id).value);
    const center = BOX_AXES.map((axis) => read(`center-${axis}`));
    const size = BOX_AXES.map((axis) => read(`size-${axis}`));
    if (![...center, ...size].every(Number.isFinite)) return null;
    if (size.some((value) => value <= 0)) return null;
    return { center, size };
  }

  function derivePreviewBox() {
    const scene = app.proteinScene;
    if (!scene) return null;
    // Reference sites may contain residues but no persisted docking-box
    // coordinates.  Use the visible site selection as an explicit preview
    // source so the toggle never silently produces an empty viewer.
    if (!scene.selection?.size) {
      const selections = siteResidueSelections();
      const atoms = scene.atoms().filter(atom => selections.some(sel =>
        atom.model === 0 && atom.chain === sel.chain && atom.resi === sel.resi && atom.icode === sel.icode));
      if (atoms.length) scene.setSelection(new Set(atoms.map(DrugStudioMolecule.atomKey)));
    }
    if (!scene.selection?.size) return null;
    try { return scene.candidateBox(5); } catch (_) { return null; }
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
    const palette = getComputedStyle(document.documentElement);
    // Keep the box visually distinct from the blue selection controls and the
    // element-coloured molecular representation.  A translucent solid gives
    // the volume context while the edges preserve the exact search boundary.
    const color = palette.getPropertyValue("--amber").trim();
    app.boxShapes.push(app.proteinViewer.addBox({
      center: { x: box.center[0], y: box.center[1], z: box.center[2] },
      dimensions: { w: box.size[0], h: box.size[1], d: box.size[2] },
      color, alpha: .07
    }));
    edges.forEach(([start, end]) => {
      app.boxShapes.push(app.proteinViewer.addLine({ start, end, color, linewidth: 3 }));
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
      if (line.startsWith("ENDMDL")) break; // Same first model as the viewer.
      if (!line.startsWith("ATOM  ") && !line.startsWith("HETATM")) continue;
      const fields = [line.slice(30, 38), line.slice(38, 46), line.slice(46, 54), line.slice(22, 26)];
      if (fields.some(value => !value.trim() || !Number.isFinite(Number(value)))) continue;
      const [x, y, z, resi] = fields.map(Number);
      atoms.push({ x, y, z, key: `${line.slice(21, 22).trim()}:${resi}${line.slice(26, 27).trim()}` });
    }
    return atoms;
  }

  function residueKey(label) {
    const value = String(label).trim();
    const match = value.match(/^([^:\s]*):(-?\d+)([A-Za-z]?)$/)
      || value.match(/^([A-Za-z0-9]+)[_ ](-?\d+)([A-Za-z]?)$/)
      || value.match(/^([A-Za-z])(-?\d+)([A-Za-z]?)$/);
    return match ? `${match[1]}:${Number(match[2])}${match[3]}` : null;
  }

  function boxCoverage() {
    const box = boxFromInputs();
    if (!box) return null;
    const atoms = app.structureAtoms || [];
    if (!atoms.length) return { unavailable: true };
    const { lo, hi } = boxCorners(box.center, box.size);
    const observed = new Map();
    atoms.forEach((atom) => {
      const inside = atom.x >= lo.x && atom.x <= hi.x && atom.y >= lo.y && atom.y <= hi.y
          && atom.z >= lo.z && atom.z <= hi.z;
      const counts = observed.get(atom.key) || { inside: 0, outside: 0 };
      counts[inside ? "inside" : "outside"] += 1;
      observed.set(atom.key, counts);
    });
    const expected = new Map();
    [...(app.selectedPocket?.residue_ids || []), ...(app.state?.target?.binding_site_residues || [])]
      .forEach(label => expected.set(residueKey(label) ?? `unparsed:${label}`, label));
    const missing = [], partial = [], unresolved = [];
    expected.forEach((label, key) => {
      const counts = observed.get(key);
      if (!counts) unresolved.push(label);
      else if (!counts.inside) missing.push(label);
      else if (counts.outside) partial.push(label);
    });
    return { residues: [...observed.values()].filter(counts => counts.inside).length,
      expected: expected.size, missing, partial, unresolved };
  }

  function renderBoxCoverage() {
    const readout = $("box-readout"), coverage = $("box-coverage");
    const summary = boxCoverage();
    if (readout) {
      readout.hidden = !app.boxVisible || !summary || Boolean(summary.unavailable);
      if (summary && !summary.unavailable) readout.textContent = t("与盒相交残基 {p0}", { p0: summary.residues });
    }
    if (!coverage) return;
    if (!summary) {
      coverage.innerHTML = `<span class="muted">${esc(t("填写盒中心和正的盒尺寸后显示覆盖情况。"))}</span>`;
      return;
    }
    if (summary.unavailable) {
      // The numbers are fine; we just have no coordinates to measure against.
      coverage.innerHTML = `<span class="muted">${esc(t("尚未载入结构坐标，暂时无法统计盒内残基。"))}</span>`;
      return;
    }
    // A key residue outside the box is a warning, not a block: the reviewer
    // may be deliberately targeting a sub-pocket.
    const warningList = (labels, description) => labels.length
      ? `<div class="box-warning">${labels.length} ${description}：${labels.slice(0, 6).map(esc).join(" · ")}${labels.length > 6 ? " …" : ""}</div>` : "";
    const warning = warningList(summary.missing, t("个关键残基落在盒外"))
      + warningList(summary.partial, t("个关键残基仅部分原子在盒内"))
      + warningList(summary.unresolved, t("个关键残基未找到坐标或标签无法识别"));
    const complete = !warning && summary.expected
      ? `<div class="box-ok">${summary.expected} ${esc(t("个关键残基的已载入原子全部在盒内"))}</div>` : "";
    coverage.innerHTML = `<div class="box-stat"><span>${esc(t("与盒相交残基"))}</span><strong>${summary.residues}</strong></div><small class="muted">${esc(t("按当前结构中已载入的原子统计；相交表示至少一个原子在盒内。"))}</small>${warning}${complete}`;
    window.DrugStudioControls.icons();
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
    if (visible && !boxFromInputs()) {
      const preview = derivePreviewBox();
      if (preview) {
        fillBoxInputs(preview);
        toast(t("当前没有保存的对接盒，已根据结合位点生成 5 Å 边距的预览盒。"));
      }
    }
    drawDockingBox();
    if (visible) fitProteinView();
    renderBoxCoverage();
  }

  function renderLigandChoices() {
    const ligands = app.state.ligands || [];
    const select = $("ligand-select");
    const selected = select.value;
    select.innerHTML = ligands.length ? ligands.map((ligand, index) => `<option value="${index}">${esc(ligand.ligand_id)}</option>`).join("") : `<option value="">${t("暂无配体")}</option>`;
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
      app.ligandScene?.dispose();
      app.ligandScene = new DrugStudioMolecule.Scene($("ligand-viewer"));
      app.ligandViewer = app.ligandScene.viewer;
      const format = artifact.path.toLowerCase().endsWith(".sdf") ? "sdf" : "pdb";
      app.ligandScene.add(text, format, "ball");
      app.ligandViewer.setStyle({}, { stick: { radius: .19, colorscheme: "Jmol" }, sphere: { scale: .28, colorscheme: "Jmol" } });
      app.ligandViewer.zoomTo(); app.ligandViewer.render();
      app.ligandKey = artifact.path;
      $("ligand-atoms").textContent = reportLigand?.properties?.heavy_atoms ? `${reportLigand.properties.heavy_atoms} heavy atoms` : t("3D 构象");
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
    const rows = [[t("分子量"), `${fmt(props.mw)} Da`], ["cLogP", fmt(props.logp)], ["TPSA", `${fmt(props.tpsa)} Å²`], [t("氢键供体 / 受体"), `${props.hbd ?? "—"} / ${props.hba ?? "—"}`], ["QED", fmt(props.qed, 3)], [t("Csp3 比例"), fmt(props.fraction_csp3, 3)], [t("可旋转键"), props.rotatable_bonds ?? "—"], ["Lipinski", (props.lipinski_violations || []).length ? t("{p0} 项超限", { p0: props.lipinski_violations.length }) : t("通过")]];
    $("ligand-properties").innerHTML = rows.map(([label, value]) => `<div class="property-row"><label>${label}</label><strong>${esc(value)}</strong></div>`).join("");
    const alerts = [...(props.alerts?.pains || []).map((item) => `PAINS · ${item}`), ...(props.alerts?.brenk || []).map((item) => `Brenk · ${item}`)];
    $("ligand-alerts").innerHTML = alerts.length ? alerts.map((item) => `<div class="alert"><i data-lucide="triangle-alert"></i>${esc(item)}</div>`).join("") : `<div class="alert" style="color:var(--teal)"><i data-lucide="circle-check"></i>${esc(t("未发现 PAINS / Brenk 风险"))}</div>`;
  }

  function renderResults() {
    const report = app.state.report || {}, results = app.state.results || {tasks:[], summary:{}}, tasks = results.tasks, summary = results.summary;
    const ligands = report.ligands || [], pockets = currentPockets(), references = app.state.target?.binding_site_residues || [];
    const metrics = [[t("对接任务"), `${summary.succeeded || 0}/${summary.total || 0}`], [pockets.length ? t("候选口袋") : t("参考位点"), pockets.length || (references.length ? `${references.length} ${t("个残基")}` : "—")], [t("最佳评分"), summary.groups?.length > 1 ? t("按协议分组") : fmt(summary.best_kcal_mol, 3)], [t("姿态"), summary.poses || 0]];
    $("metric-row").innerHTML = metrics.map(([label,value],index) => `<div class="metric"><div><label>${esc(label)}</label><strong>${esc(value)}</strong></div><span class="metric-icon"><i data-lucide="${["flask-conical","target","trending-down","atom"][index]}"></i></span></div>`).join("");
    $("result-explanation").innerHTML = `<span class="muted">${esc(t(results.stale ? "结果已失效，请重新对接。" : "选择任务查看对应受体、姿态与质控。"))}</span>`;
    $("result-attempt").textContent = `${t("第 {p0} 次运行", {p0:results.attempt || 1})} · ${summary.ligands || 0} ${t("小分子")}`;
    $("task-table").innerHTML = tasks.length ? tasks.map((task,index) => `<tr data-result-row="${index}" tabindex="0"><td>${esc(task.state_id || task.ligand_id || "—")}</td><td class="mono-cell">${esc(task.task_id)}<small> · ${esc(task.engine)}</small></td><td class="score" title="${esc(t("同一受体和对接协议下，QC 通过姿态中的最低 Vina 评分；不是实验亲和力。"))}">${fmt(task.score,3)}${task.score != null ? " kcal/mol" : ""}</td><td>${task.qc_pass_count ?? "—"}/${task.pose_count ?? "—"}</td><td><span class="state-tag ${task.status === 'succeeded' ? 'success' : task.status === 'failed' ? 'failed' : ''}">${esc(task.status)}</span></td></tr>`).join("") : `<tr><td colspan="5">${esc(t("暂无对接任务。"))}</td></tr>`;
    $("property-table").innerHTML = ligands.length ? ligands.map(item => { const p = item.properties || {}; const alerts = p.alerts ? (p.alerts.pains || []).length + (p.alerts.brenk || []).length : null; return `<tr><td>${esc(item.ligand_id)}</td><td>${fmt(p.mw)}</td><td>${fmt(p.logp)}</td><td>${fmt(p.tpsa)}</td><td>${fmt(p.qed,3)}</td><td>${alerts ?? "—"}</td></tr>`; }).join("") : `<tr><td colspan="6">${t("暂无分子评估结果。")}</td></tr>`;
    $("limitations-list").innerHTML = (report.limitations || []).map(item => `<div class="limitation">${esc(item)}</div>`).join("");
    const selected = tasks.find(task => task.task_id === app.resultTaskKey);
    if (selected) showResultDetail(selected);
    $("task-table").querySelectorAll("[data-result-row]").forEach(row => {
      const open = () => showResultDetail(tasks[Number(row.dataset.resultRow)]);
      row.addEventListener("click",open); row.addEventListener("keydown",e => { if (e.key === 'Enter') open(); });
    });
  }

  function showResultDetail(task) {
    const previous = app.resultTaskKey === task.task_id ? $("result-pose-choice")?.value : null;
    app.resultTaskKey = task.task_id;
    const panel = $("result-explanation");
    panel.innerHTML = `<div><strong>${esc(task.state_id || task.ligand_id)} · ${esc(task.receptor_id)} · ${esc(task.engine)}</strong><p>${t("姿态")} ${task.pose_count ?? '—'} · QC ${task.qc_pass_count ?? '—'} · ${fmt(task.score,3)} kcal/mol</p>${task.error ? `<p class="error">${esc(task.error)}</p>` : ''}<select id="result-pose-choice" class="select-control">${task.poses.map(p => `<option value="${p.record_index}">${t("姿态")} ${esc(p.mode)} · ${fmt(p.affinity_kcal_mol,3)} · QC ${p.qc_pass === true ? 'pass' : p.qc_pass === false ? 'fail' : '—'} ${esc(p.qc_reason || '')}</option>`).join('')}</select><button id="result-open-pose" class="secondary-button" ${task.viewable ? '' : 'disabled'}><i data-lucide="scan-eye"></i>${t("查看姿态")}</button>${!task.viewable ? `<p>${t("缺少可核验的受体或姿态来源，无法叠合。")}</p>` : ''}<details><summary>${t("方法与来源")}</summary><pre>${esc(JSON.stringify({attempt:task.attempt,engine:task.engine,engine_version:task.engine_version,protocol:task.protocol,receptor:task.receptor_artifact?.path,poses:task.pose_artifact?.path},null,2))}</pre></details></div>`;
    window.DrugStudioControls.icons();
    if (previous != null && task.poses.some(p => String(p.record_index) === previous)) $("result-pose-choice").value = previous;
    else if (task.best_pose) $("result-pose-choice").value = String(task.best_pose.record_index);
    $("result-open-pose").addEventListener("click", async () => {
      const index = $("result-pose-choice").value;
      disposeViewers(); app.structureArtifact = task.receptor_artifact;
      renderStructureChoices(); renderPoseChoices(); $("pose-select").value = `${task.task_id}:${index}`;
      fillBoxInputs({center:task.protocol.center || [],size:task.protocol.size || []});
      showView("structure");
    });
  }

  async function loadProvenance(artifactId) {
    const panel = $("result-provenance");
    if (!panel) return;
    panel.hidden = false;
    panel.innerHTML = `<div class="provenance-heading"><div><div class="eyebrow">${t("来源链")}</div><strong>${esc(t("正在读取来源链…"))}</strong></div></div>`;
    try {
      const response = await fetch(scoped("/api/provenance", { artifact_id: artifactId || "" }), { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      app.provenance = data;
      const chain = data.chain || [];
      const stages = data.stages || [];
      panel.innerHTML = `<div class="provenance-heading"><div><div class="eyebrow">${t("来源链")}</div><strong>${esc(data.run_id || t("当前运行"))} · ${chain.length} ${esc(t("个来源产物"))}</strong></div><button type="button" class="icon-button" data-close-provenance title="${esc(t("收起来源链"))}" aria-label="${esc(t("收起来源链"))}"><i data-lucide="x"></i></button></div><div class="provenance-chain">${chain.length ? chain.map((item, index) => `<div class="provenance-item"><span class="provenance-index">${index + 1}</span><div><strong>${esc(item.kind || "artifact")}</strong><small>${esc(item.stage || "—")} · ${esc(item.path || "—")}</small></div><span class="provenance-state ${item.exists ? "ok" : "missing"}">${item.exists ? t("存在") : t("缺失")}</span></div>`).join("") : `<div class="empty-state">${esc(t("没有找到可追溯产物。"))}</div>`}</div><div class="provenance-stages">${stages.map((item) => `<span>${esc(item.stage)} · ${esc(stageStatusLabel(item.status))}</span>`).join("")}</div>`;
      panel.querySelector("[data-close-provenance]")?.addEventListener("click", () => { panel.hidden = true; });
      window.DrugStudioControls.icons();
    } catch (error) {
      panel.innerHTML = `<div class="provenance-heading"><strong>${esc(t("来源链读取失败"))}</strong><button type="button" class="icon-button" data-close-provenance title="${esc(t("收起来源链"))}" aria-label="${esc(t("收起来源链"))}"><i data-lucide="x"></i></button></div><div class="empty-state">${esc(error.message)}</div>`;
      panel.querySelector("[data-close-provenance]")?.addEventListener("click", () => { panel.hidden = true; });
      window.DrugStudioControls.icons();
    }
  }

  function renderReview() {
    const pending = ["waiting_approval", "waiting_input"].includes(app.state.status) || app.state.stage_results?.docking?.status === "stale";
    $("review-state").textContent = pending ? t("有待审核") : t("暂无待审核");
    const history = app.state.human_decisions || [];
    $("decision-history").innerHTML = history.length ? history.slice().reverse().map((item) => `<div class="decision-row"><span class="decision-time">${esc(item.timestamp || "—")}</span><span class="decision-actor">${esc(item.actor || t("审核人"))} · ${esc(item.action || t("决策"))}</span><span class="decision-reason">${esc(item.reason || "—")}</span></div>`).join("") : `<div class="empty-state">${esc(t("暂无人工决策记录。"))}</div>`;
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
      .map(([value, label]) => `<option value="${value}">${esc(t(label))}</option>`).join("");
    if (actions.some(([value]) => value === previous)) $("review-action").value = previous;
    const boxStage = stage === "docking_box";
    $("box-inputs").hidden = !boxStage;
    $("box-coverage").hidden = !boxStage;
    const note = $("review-stage-note");
    note.hidden = boxStage;
    note.textContent = boxStage ? "" : t("受体审核目前只记录「接受」。要否决修复结果，请从受体阶段重跑并调整参数。");
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
        if (!box) { toast(t("请填写有效的盒中心和正的盒尺寸。"), true); return; }
        payload.center = box.center;
        payload.size = box.size;
      }
    }
    try {
      const response = await postJson("/api/review", payload);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      toast(t("人工决策已记录"));
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
    const closingFocus = currentDrawer && viewName === "project" && $(`view-${currentDrawer}`).contains(document.activeElement);
    const activeTool = viewName === "workflow" ? "project" : viewName;
    document.body.classList.toggle("drawer-open", viewName !== "project");
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === activeTool);
      button.setAttribute("aria-pressed", String(button.dataset.view === activeTool));
      button.setAttribute("aria-controls", `view-${button.dataset.view}`);
    });
    // The conversation remains mounted and visible underneath tool drawers.
    // Scientific views are contextual inspectors, not alternate destinations;
    // closing one should return to the exact transcript position.
    document.querySelectorAll(".view").forEach((view) => {
      const active = view.id === "view-project" || view.id === `view-${viewName}`;
      view.inert = !active;
      view.classList.toggle("active", active);
    });
    if (closingFocus) document.querySelector(`[data-view="${currentDrawer}"]`)?.focus({preventScroll:true});
    if (viewName === "structure") {
      if (app.proteinViewer) window.setTimeout(() => app.proteinViewer.resize(), 40); else loadProtein();
    } else if (viewName === "ligand") {
      if (app.ligandViewer) window.setTimeout(() => app.ligandViewer.resize(), 40); else loadLigand(Number($("ligand-select").value || 0));
    } else if (viewName === "review") {
      loadStructureCoordinates();
    } else if (viewName === "files") {
      refreshProjectFiles();
    } else if (viewName === "settings") {
      if (!app.moduleConfig) fetchModuleConfig(); else renderModuleConfig();
    }
  }

  function initializeSidebarResize() {
    const root = document.documentElement;
    const sides = ["left", "right", "drawer"];
    const property = (side) => side === "drawer" ? "--drawer-width" : `--${side}-sidebar-width`;
    const key = (side) => `drugstudio-${side}-sidebar-width`;
    const preferred = {};
    sides.forEach((side) => {
      try {
        const value = Number(localStorage.getItem(key(side)));
        if (Number.isFinite(value) && value > 0) preferred[side] = value;
      } catch (_) { /* Preferences are optional when storage is unavailable. */ }
    });
    const handles = [...document.querySelectorAll("[data-sidebar-resizer]")];
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    const widths = {}, ranges = {};
    const defaults = () => ({left: window.innerWidth <= 1120 ? 210 : 248,
      right: window.innerWidth <= 1120 ? 64 : 74, drawer: 680});
    const apply = () => {
      if (window.innerWidth <= 760) return;
      const base = defaults(), available = window.innerWidth;
      widths.left = clamp(preferred.left ?? base.left, 190, Math.min(420, available - 58 - 332));
      widths.right = clamp(preferred.right ?? base.right, 58, Math.min(200, available - widths.left - 332));
      ranges.left = [190, Math.min(420, available - widths.right - 332)];
      ranges.right = [58, Math.min(200, available - widths.left - 332)];
      const drawerMax = Math.min(1200, available - widths.left - widths.right - 40);
      ranges.drawer = [Math.min(320, drawerMax), drawerMax];
      widths.drawer = clamp(preferred.drawer ?? base.drawer, ...ranges.drawer);
      sides.forEach((side) => root.style.setProperty(property(side), `${widths[side]}px`));
      handles.forEach((handle) => {
        const side = handle.dataset.sidebarResizer;
        handle.setAttribute("aria-valuemin", Math.round(ranges[side][0]));
        handle.setAttribute("aria-valuemax", Math.round(ranges[side][1]));
        handle.setAttribute("aria-valuenow", Math.round(widths[side]));
      });
    };
    const setWidth = (side, value) => {
      preferred[side] = clamp(value, ...ranges[side]);
      apply();
    };
    let drag = null;
    const persist = (side) => {
      try { localStorage.setItem(key(side), String(Math.round(widths[side]))); } catch (_) { /* session only */ }
    };
    const finish = () => {
      if (!drag) return;
      const {handle, side} = drag;
      drag = null;
      persist(side); handle.classList.remove("resizing");
      document.body.classList.remove("sidebar-resizing");
    };
    handles.forEach((handle) => {
      const side = handle.dataset.sidebarResizer;
      handle.addEventListener("pointerdown", (event) => {
        if (window.innerWidth <= 760 || event.button !== 0 || !event.isPrimary) return;
        apply();
        drag = {handle, side, pointerId: event.pointerId, startX: event.clientX, startWidth: widths[side]};
        handle.setPointerCapture(event.pointerId);
        handle.classList.add("resizing"); document.body.classList.add("sidebar-resizing");
        event.preventDefault();
      });
      handle.addEventListener("lostpointercapture", finish);
      handle.addEventListener("dblclick", () => {
        delete preferred[side];
        try { localStorage.removeItem(key(side)); } catch (_) { /* session only */ }
        apply();
      });
      handle.addEventListener("keydown", (event) => {
        if (window.innerWidth <= 760 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        apply();
        const direction = (event.key === "ArrowRight" ? 1 : -1) * (side === "left" ? 1 : -1);
        setWidth(side, event.key === "Home" ? ranges[side][0] : event.key === "End" ? ranges[side][1] : widths[side] + direction * (event.shiftKey ? 40 : 12));
        persist(side); event.preventDefault();
      });
    });
    window.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      setWidth(drag.side, drag.startWidth + (event.clientX - drag.startX) * (drag.side === "left" ? 1 : -1));
    });
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    window.addEventListener("blur", finish);
    window.addEventListener("resize", () => { finish(); apply(); });
    // Editors, terminals and WebGL canvases need an explicit resize when only
    // their parent changes size (the browser window itself has not changed).
    let frame;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if ($("view-files").classList.contains("active")) app.codeEditor?.refresh();
        if ($("view-structure").classList.contains("active")) app.proteinViewer?.resize();
        if ($("view-ligand").classList.contains("active")) app.ligandViewer?.resize();
        if ($("terminal-dock").classList.contains("open")) resizeTerminal();
      });
    });
    document.querySelectorAll(".main-content, .viewer-canvas, .context-editor").forEach((node) => observer.observe(node));
    apply();
  }

  document.addEventListener("DOMContentLoaded", () => {
    initializeSidebarResize();
    window.DrugStudioUI.initialize();
    window.addEventListener("drugstudio:locale", refreshLanguage);
    window.addEventListener("drugstudio:theme", refreshAppearance);
    document.querySelectorAll(".view:not(#view-project) .view-header").forEach((header) => {
      if (header.querySelector("[data-close-drawer]")) return;
      const close = document.createElement("button");
      close.type = "button";
      close.className = "icon-button drawer-close";
      close.dataset.closeDrawer = "true";
      close.title = t("关闭工具面板");
      close.setAttribute("aria-label", t("关闭工具面板"));
      close.innerHTML = '<i data-lucide="x"></i>';
      header.appendChild(close);
    });
    document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
    document.querySelectorAll("[data-close-drawer]").forEach((button) => button.addEventListener("click", () => showView("project")));
    document.querySelectorAll("[data-open-view]").forEach((button) => button.addEventListener("click", () => {
      document.querySelector(`[data-view="${button.dataset.openView}"]`)?.click();
    }));
    $("chat-messages").addEventListener("scroll", updateChatJump, { passive: true });
    $("chat-jump-latest").addEventListener("click", () => {
      $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
      updateChatJump();
    });
    $("chat-messages").addEventListener("click", async event => {
      const suggestion = event.target.closest('[data-chat-suggestion]');
      if (suggestion) { $("chat-input").value = suggestion.dataset.chatSuggestion; resizeChatInput(); $("chat-input").focus(); }
      const button = event.target.closest('[data-copy-code],[data-copy-message]');
      if (!button) return;
      const source = button.hasAttribute('data-copy-code') ? button.closest('.chat-code').querySelector('code').textContent : button.closest('.chat-message')._source;
      try {
        await window.DrugStudioChat.copy(source);
        const feedback = copied => {
          const label = t(copied ? '已复制' : button.hasAttribute('data-copy-code') ? '复制代码' : '复制回复');
          button.title = label; button.setAttribute('aria-label',label);
          button.classList.toggle('copied',copied);
          button.innerHTML = `<i data-lucide="${copied ? 'check' : 'copy'}"></i>`;
          window.DrugStudioControls.icons();
        };
        feedback(true); window.clearTimeout(button._copyTimer);
        button._copyTimer = window.setTimeout(() => { if (button.isConnected) feedback(false); },1600);
      } catch (error) { toast(error.message, true); }
    });
    $("new-chat").addEventListener("click", newChat);
    $("manage-projects").addEventListener("click", async () => { await fetchProjects(); $("project-dialog").showModal(); });
    $("project-form").addEventListener("submit", createProject);
    document.querySelectorAll("[data-close-project-dialog]").forEach(button => button.addEventListener("click", () => $("project-dialog").close()));
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !document.querySelector("dialog[open]") && document.body.classList.contains("drawer-open")) { event.preventDefault(); showView("project"); }
    });
    $("context-backdrop")?.addEventListener("click", () => showView("project"));
    document.querySelectorAll("[data-chat-filter]").forEach((button) => button.addEventListener("click", () => {
      app.chatFilter = button.dataset.chatFilter;
      renderChatList();
    }));
    $("open-terminal").addEventListener("click", toggleTerminalDock);
    $("close-terminal").addEventListener("click", closeTerminalDock);
    $("refresh-button").addEventListener("click", () => busy("refresh-button", () => Promise.all([fetchState(), fetchProjects()]))); $("workflow-refresh").addEventListener("click", () => busy("workflow-refresh", fetchState)); $("workflow-continue").addEventListener("click", () => busy("workflow-continue", continueWorkflow)); $("workflow-pause").addEventListener("click", () => sendControl("pause")); $("workflow-cancel").addEventListener("click", () => sendControl("cancel")); $("rerun-stage").addEventListener("click", () => busy("rerun-stage", rerunFromSelectedStage)); $("save-pricing").addEventListener("click", () => busy("save-pricing", savePricing)); $("run-selector").addEventListener("change", (event) => selectRun(event.target.value)); $("project-selector").addEventListener("change", (event) => selectProject(event.target.value)); $("reset-protein").addEventListener("click", () => { if (app.proteinViewer) { drawDockingBox(); fitProteinView(); } });
    $("refresh-files").addEventListener("click", () => busy("refresh-files", refreshProjectFiles)); $("save-file").addEventListener("click", () => busy("save-file", saveProjectFile)); $("restart-terminal").addEventListener("click", restartTerminal); $("chat-form").addEventListener("submit", sendChat); $("new-file").addEventListener("click", async () => { const name = window.prompt(t("新文件名（相对项目根目录）")); if (!name) return; const path = [app.fileDirectory, name].filter(Boolean).join("/"); try { const response = await postJson("/api/project/file", { path, content: "" }); const data = await response.json(); if (!response.ok) throw new Error(data.error || response.statusText); await refreshProjectFiles(); await openProjectFile(data.path); } catch (error) { toast(error.message, true); } });
    $("save-module-settings").addEventListener("click", () => busy("save-module-settings", saveModuleConfig));
    $("refresh-module-settings").addEventListener("click", () => busy("refresh-module-settings", fetchModuleConfig));
    $("open-model-settings-from-modules").addEventListener("click", () => { $("model-settings-dialog").showModal(); if (!app.modelConfig) fetchModelConfig(); });
    $("chat-model").addEventListener("change", (event) => localStorage.setItem("dock-agent-chat-model", event.target.value.trim()));
    $("open-model-settings").addEventListener("click", () => {
      $("model-settings-dialog").showModal();
      if (!app.modelConfig) fetchModelConfig();
    });
    $("model-settings-form").addEventListener("submit", event => { event.preventDefault(); busy("save-model-settings", () => saveModelConfig(event)); });
    document.querySelectorAll("[data-close-model-settings]").forEach((button) => button.addEventListener("click", () => $("model-settings-dialog").close()));
    $("chat-temperature").addEventListener("input", (event) => {
      $("chat-temperature-value").value = event.target.value;
      localStorage.setItem("dock-agent-chat-temperature", event.target.value);
    });
    $("auth-form").addEventListener("submit", async (event) => { event.preventDefault(); const token = $("auth-token").value; const ok = await authenticate(token); if (ok) { $("auth-error").textContent = ""; initializeWorkspace(); } else { $("auth-error").textContent = t("访问令牌与本服务器不匹配。"); } });
    $("file-editor").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveProjectFile(); } });
    $("chat-input").addEventListener("input", resizeChatInput);
    $("chat-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("chat-form").requestSubmit(); } });
    document.querySelectorAll("[data-protein-style]").forEach((button) => button.addEventListener("click", () => setProteinStyle(button.dataset.proteinStyle)));
    bindMolecularControls();
    $("structure-select").addEventListener("change", async (event) => { const picked = structureCandidates()[Number(event.target.value)]; disposeViewers(); app.structureArtifact = picked; renderPoseChoices(); await loadProtein(); });
    $("pose-select").addEventListener("change", async () => { const picked = app.structureArtifact, view = app.proteinViewer?.getView(); disposeViewers(); app.structureArtifact = picked; await loadProtein(); if (view && app.structureArtifact === picked && app.proteinViewer) { app.proteinViewer.setView(view); app.proteinViewer.render(); } });
    $("ligand-select").addEventListener("change", (event) => { try { app.ligandViewer?.clear?.(); } catch (_) { /* already gone */ } app.ligandViewer = null; app.ligandKey = null; loadLigand(event.target.value); }); $("submit-review").addEventListener("click", () => busy("submit-review", submitReview));
    $("review-stage").addEventListener("change", renderReviewStage);
    $("save-parameters").addEventListener("click", () => busy("save-parameters", saveParameters));
    $("reset-parameters").addEventListener("click", () => busy("reset-parameters", resetParameters));
    $("toggle-box").addEventListener("click", () => setBoxVisible(!app.boxVisible));
    ["center", "size"].forEach((kind) => ["x", "y", "z"].forEach((axis) => {
      // Typing a coordinate moves the wireframe immediately, so the box is
      // judged by looking at it rather than by reading six numbers.
      $(`${kind}-${axis}`).addEventListener("input", onBoxInputChanged);
    }));
    renderReviewStage(); $("continue-button").addEventListener("click", () => busy("continue-button", continueWorkflow));
    $("workflow-zoom-out").addEventListener("click", () => zoomWorkflowCanvas(-.1));
    $("workflow-zoom-in").addEventListener("click", () => zoomWorkflowCanvas(.1));
    $("workflow-fit").addEventListener("click", fitWorkflowCanvas);
    $("conversation-workflow").addEventListener("toggle", () => {
      // The toggle event has already changed layout. Deferring this fit can
      // recenter the canvas after the user has begun a drag.
      if ($("conversation-workflow").open) { fitWorkflowCanvas(); renderWorkflow(); }
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
