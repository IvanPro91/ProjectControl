/* ProjectControl — нижняя панель логов: вкладки по процессам, поиск, изменение высоты */

function tabId(pid, tid) { return pid + ":" + tid; }

function projectTabs(project) {
  const targets = project.targets || [];
  const many = targets.length > 1;
  return targets.map((t) => ({
    id: tabId(project.id, t.id),
    pid: project.id,
    tid: t.id,
    name: t.name,
    kind: t.kind,
    label: many ? (project.name || project.root) + " · " + t.name : t.name,
  }));
}

function allTabs() {
  const out = [];
  state.projects.forEach((p) => out.push(...projectTabs(p)));
  return out;
}

function getBuf(id) {
  if (!state.logs.has(id)) {
    state.logs.set(id, {
      lines: [], cap: 2000, follow: true, dropped: 0, unread: 0,
      filterText: "", loaded: false,
    });
  }
  return state.logs.get(id);
}

function logCap(pid) {
  const project = state.projects.find((p) => p.id === pid);
  return (project && project.log_lines) || 2000;
}

function entryNode(entry) {
  const node = document.createElement("div");
  node.className = "log-line " + (entry.stream === "error" ? "error" : entry.stream === "cmd" ? "cmd" :
    entry.stream === "meta" ? "meta" : "");
  const msg = document.createElement("span");
  msg.className = "m";
  msg.textContent = entry.text === null || entry.text === undefined ? "" : entry.text;
  node.appendChild(msg);
  return node;
}

function dockBody() { return $("#dock-body"); }

function activeTab() {
  const tabs = allTabs();
  return tabs.find((t) => t.id === state.activeTab) || tabs[0] || null;
}

/* ------------------------------------------------------------------ вкладки */

function renderTabs() {
  const host = $("#dock-tabs");
  const tabs = allTabs();
  if (!tabs.length) { host.innerHTML = '<span class="muted" style="font-size:12px">Нет процессов</span>'; return; }
  if (!tabs.some((t) => t.id === state.activeTab)) state.activeTab = tabs[0].id;

  host.innerHTML = tabs.map((t) => {
    const project = projectById(t.pid);
    const status = project ? statusOf(project, t.tid) : { state: "stopped" };
    const buf = getBuf(t.id);
    const unread = buf.unread && t.id !== state.activeTab
      ? '<span class="unread">' + (buf.unread > 99 ? "99+" : buf.unread) + "</span>" : "";
    return '<button class="dock-tab' + (t.id === state.activeTab ? " active" : "") +
      '" data-tab="' + t.id + '" title="' + esc(t.label) + '">' +
      '<span class="dot ' + dotClass(status) + '"></span>' +
      esc(t.name) + unread + "</button>";
  }).join("");
}

function selectTab(id) {
  state.activeTab = id;
  const buf = getBuf(id);
  buf.unread = 0;
  $("#dock-search").value = buf.filterText;
  $("#dock-follow").checked = buf.follow;
  renderTabs();
  renderLogBody();
  const tab = allTabs().find((t) => t.id === id);
  if (tab && !getBuf(id).loaded) loadProjectLogs(tab.pid);
}

/* ------------------------------------------------------------------ отрисовка */

function renderLogBody() {
  const body = dockBody();
  const tab = activeTab();
  if (!tab) { body.innerHTML = '<div class="log-empty">Добавь проект и запусти процесс.</div>'; return; }
  const buf = getBuf(tab.id);
  buf.cap = logCap(tab.pid);
  const query = buf.filterText.trim().toLowerCase();
  const lines = query
    ? buf.lines.filter((e) => (e.text || "").toLowerCase().includes(query))
    : buf.lines;

  body.innerHTML = "";
  if (!lines.length) {
    body.innerHTML = '<div class="log-empty">' +
      (buf.lines.length ? "Ничего не найдено по запросу «" + esc(buf.filterText) + "»." : "Лог пуст — запусти процесс, вывод появится здесь.") +
      "</div>";
  } else {
    const frag = document.createDocumentFragment();
    lines.forEach((e) => frag.appendChild(entryNode(e)));
    body.appendChild(frag);
  }

  updateDockMeta();
  if (buf.follow) body.scrollTop = body.scrollHeight;
}

function updateDockMeta() {
  const tab = activeTab();
  const meta = $("#dock-meta");
  if (!tab) { if (meta) meta.textContent = ""; return; }
  const buf = getBuf(tab.id);
  const project = projectById(tab.pid);
  const status = project ? statusOf(project, tab.tid) : null;
  const parts = [];
  parts.push(buf.lines.length + " / " + buf.cap + " строк");
  if (status && status.state === "running") parts.push("pid " + status.pid);
  if (buf.filterText) parts.push("поиск: " + buf.filterText);
  if (meta) meta.textContent = parts.join("  ·  ");

  const note = $("#dock-note");
  const dropped = buf.dropped;
  if (note) {
    note.classList.toggle("hidden", !dropped);
    note.textContent = dropped
      ? "старые строки удалены (" + dropped + "), хранится кольцо последних " + buf.cap
      : "";
  }
}

function appendEntry(pid, entry) {
  const id = tabId(pid, entry.target_id);
  const buf = getBuf(id);
  buf.cap = logCap(pid);
  buf.lines.push(entry);
  if (buf.lines.length > buf.cap) {
    buf.dropped += buf.lines.length - buf.cap;
    buf.lines.splice(0, buf.lines.length - buf.cap);
  }
  const isActive = state.activeTab === id;
  if (!isActive) { buf.unread++; renderTabs(); return; }
  if (!state.dockOpen) { buf.unread++; renderTabs(); return; }
  if (buf.filterText) { scheduleRender(id); return; }

  const body = dockBody();
  if (!body) return;
  if (body.querySelector(".log-empty")) body.innerHTML = "";
  body.appendChild(entryNode(entry));
  while (body.childElementCount > buf.cap) body.removeChild(body.firstElementChild);
  if (buf.follow) body.scrollTop = body.scrollHeight;
  updateDockMeta();
}

const renderTimers = new Map();

function scheduleRender(id) {
  if (renderTimers.has(id)) return;
  renderTimers.set(id, setTimeout(() => { renderTimers.delete(id); renderLogBody(); }, 180));
}

async function loadProjectLogs(pid) {
  const project = projectById(pid);
  if (!project) return;
  try {
    const data = await api("/api/projects/" + pid + "/logs?limit=" + project.log_lines + "&since=0");
    const byTarget = new Map();
    (data.entries || []).forEach((entry) => {
      const id = tabId(pid, entry.target_id);
      if (!byTarget.has(id)) byTarget.set(id, []);
      byTarget.get(id).push(entry);
    });
    (project.targets || []).forEach((t) => {
      const id = tabId(pid, t.id);
      const buf = getBuf(id);
      buf.cap = project.log_lines;
      buf.lines = byTarget.get(id) || [];
      buf.dropped = data.dropped || 0;
      buf.loaded = true;
    });
    if (data.dropped) {
      const buf = getBuf(state.activeTab);
      if (buf && buf.tid && String(buf.tid).startsWith(pid)) buf.dropped = data.dropped;
    }
    renderTabs();
    renderLogBody();
  } catch (err) {
    toast(err.message, "error");
  }
}

/* ------------------------------------------------------------------ высоты */

const DOCK_MIN = 110;
const DOCK_KEY = "pc.dock.height";

function dockHeight() {
  const stored = Number(localStorage.getItem(DOCK_KEY));
  if (stored >= DOCK_MIN) return stored;
  return Math.round(window.innerHeight * 0.35);
}

function setDockHeight(px) {
  const max = Math.max(DOCK_MIN, window.innerHeight - 180);
  const value = Math.min(max, Math.max(DOCK_MIN, Math.round(px)));
  $("#dock").style.height = value + "px";
  localStorage.setItem(DOCK_KEY, String(value));
}

function openDock(tabIdToOpen) {
  const dock = $("#dock");
  dock.classList.remove("collapsed");
  dock.classList.remove("max");
  dock.style.height = dockHeight() + "px";
  state.dockOpen = true;
  if (tabIdToOpen) state.activeTab = tabIdToOpen;
  renderTabs();
  selectTab(state.activeTab);
  markLogButtons();
}

function closeDock() {
  $("#dock").classList.add("collapsed");
  state.dockOpen = false;
  renderTabs();
  markLogButtons();
}

function markLogButtons() {
  $$('[data-act="toggle-logs"]').forEach((btn) => {
    btn.classList.toggle("active", state.dockOpen && activeTab() && btn.dataset.pid === activeTab().pid);
  });
}

/* ------------------------------------------------------------------ события */

function bindDock() {
  $("#dock-tabs").addEventListener("click", (e) => {
    const tab = e.target.closest("[data-tab]");
    if (!tab) return;
    selectTab(tab.dataset.tab);
  });

  let searchTimer = null;
  $("#dock-search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    const value = e.target.value;
    searchTimer = setTimeout(() => {
      const tab = activeTab();
      if (!tab) return;
      const buf = getBuf(tab.id);
      buf.filterText = value.trim().toLowerCase();
      renderLogBody();
    }, 160);
  });

  $("#dock-follow").addEventListener("change", (e) => {
    const tab = activeTab();
    if (tab) getBuf(tab.id).follow = e.target.checked;
  });

  $("#dock-clear").addEventListener("click", () => {
    const tab = activeTab();
    if (!tab) return;
    const buf = getBuf(tab.id);
    buf.lines = [];
    buf.dropped = 0;
    buf.unread = 0;
    renderLogBody();
    renderTabs();
  });

  $("#dock-close").addEventListener("click", closeDock);

  $("#dock-max").addEventListener("click", () => {
    const dock = $("#dock");
    if (dock.classList.toggle("max")) {
      dock.style.height = "";
    } else {
      dock.style.height = dockHeight() + "px";
    }
  });

  // перетаскивание верхней границы
  const resizer = $("#dock-resizer");
  let dragging = false;
  let startY = 0;
  let startH = 0;

  resizer.addEventListener("mousedown", (e) => {
    dragging = true;
    startY = e.clientY;
    startH = $("#dock").getBoundingClientRect().height;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "ns-resize";
    e.preventDefault();
  });
  document.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    setDockHeight(startH + (startY - e.clientY));
  });
  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    renderLogBody();
  });

  // автоскролл отключается, если пользователь пролистал вверх
  $("#dock-body").addEventListener("scroll", (e) => {
    const body = e.target;
    const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
    const tab = activeTab();
    if (!tab) return;
    getBuf(tab.id).follow = nearBottom;
    $("#dock-follow").checked = nearBottom;
  });
}
