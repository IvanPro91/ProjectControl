/* ProjectControl — загрузка состояния, WebSocket, события */

async function refresh() {
  const data = await api("/api/state");
  state.projects = data.projects || [];
  state.templates = data.templates || [];
  state.settings = data.settings || {};
  state.history = data.history || [];
  state.projects.forEach((p) => { getBuf(p.id).cap = p.log_lines; });
  renderProjects();
}

function setConn(online) {
  state.connected = online;
  const pill = $("#conn");
  pill.className = "pill " + (online ? "pill-on" : "pill-off");
  pill.textContent = online ? "на связи" : "нет связи";
}

let ws = null;
let wsTimer = null;
let backoff = 1000;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(proto + "://" + location.host + "/ws");

  ws.onopen = () => {
    setConn(true);
    backoff = 1000;
    $("#subtitle").textContent = "панель управления проектами";
  };

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (e) { return; }
    handleEvent(msg);
  };

  ws.onclose = () => {
    setConn(false);
    clearTimeout(wsTimer);
    wsTimer = setTimeout(connect, backoff);
    backoff = Math.min(backoff * 1.7, 15000);
  };

  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

function handleEvent(msg) {
  if (msg.type === "hello") {
    state.projects = msg.projects || [];
    state.templates = msg.templates || [];
    state.settings = msg.settings || {};
    state.history = msg.history || [];
    renderProjects();
    const running = state.projects.filter((p) =>
      Object.values(p.statuses || {}).some((s) => s.state === "running"));
    if (running.length) {
      const first = running[0];
      const target = (first.targets || []).find((t) =>
        (first.statuses[t.id] || {}).state === "running") || (first.targets || [])[0];
      if (target) openDock(tabId(first.id, target.id));
    }
    return;
  }
  if (msg.type === "log") {
    if (!state.projects.find((p) => p.id === msg.project_id)) return;
    appendEntry(msg.project_id, msg.entry);
    updateProjectLogBadges();
    return;
  }
  if (msg.type === "status") {
    applyStatus(msg.project_id, msg.target_id, msg.status);
    renderTabs();
    return;
  }
  if (msg.type === "logs_cleared") {
    (state.projects.find((p) => p.id === msg.project_id)?.targets || []).forEach((t) => {
      const buf = getBuf(tabId(msg.project_id, t.id));
      buf.lines = [];
      buf.dropped = 0;
      buf.unread = 0;
    });
    renderTabs();
    renderLogBody();
    return;
  }
  if (msg.type === "projects") {
    refresh().catch(() => {});
  }
}

function tickUptime() {
  $$(".target").forEach((row) => {
    const project = projectById(row.dataset.pid);
    if (!project) return;
    const status = statusOf(project, row.dataset.tid);
    const text = $('[data-role="state"]', row);
    if (text && status.state === "running") text.textContent = stateText(status);
  });
}

function bindUi() {
  const host = $("#projects");

  host.addEventListener("click", (e) => {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const act = el.dataset.act;
    if (act === "open-folder") e.preventDefault();
    handleAction(act, el);
  });

  bindDock();

  $("#btn-add").addEventListener("click", () => openProjectEditor(null));
  $("#btn-settings").addEventListener("click", openSettings);
  $("#btn-templates").addEventListener("click", openTemplates);
  $("#empty").addEventListener("click", (e) => {
    if (e.target.closest('[data-action="add-project"]')) openProjectEditor(null);
  });

  $("#btn-stop-all").addEventListener("click", async () => {
    const running = state.projects.filter((p) =>
      Object.values(p.statuses || {}).some((s) => s.state === "running"));
    if (!running.length) { toast("Ничего не запущено", "warn"); return; }
    const ok = await confirmDialog("Остановить всё?",
      "Будут остановлены процессы " + running.length + " проект(ов).", "Остановить");
    if (!ok) return;
    await api("/api/stop-all", { method: "POST" });
    toast("Все процессы остановлены", "ok");
  });

  document.addEventListener("mousedown", (e) => {
    if (state.menu && !state.menu.contains(e.target)) closeMenu();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (state.menu) closeMenu();
      else closeModal();
      return;
    }
    if (e.ctrlKey && (e.key === "`" || e.key === "ё")) {
      e.preventDefault();
      if (state.dockOpen) closeDock();
      else if (activeTab()) openDock(activeTab().id);
    }
  });
  setInterval(tickUptime, 1000);
}

bindUi();
refresh().catch((err) => toast("Не удалось загрузить состояние: " + err.message, "error"));
connect();
