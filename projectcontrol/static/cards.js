/* ProjectControl — карточки проектов и действия */

function projectById(pid) { return state.projects.find((p) => p.id === pid); }

function statusOf(project, tid) {
  return (project.statuses || {})[tid] || { state: "stopped", uptime: 0, pid: null };
}

function stateText(status) {
  if (status.state === "running") return fmtDuration(status.uptime) + " · " + status.pid;
  if (status.state === "stopping") return "остановка…";
  return "остановлен";
}

function dotClass(status) {
  if (status.state === "running") return "dot-running";
  if (status.state === "stopping") return "dot-starting";
  return "dot-stopped";
}

function targetRow(project, target) {
  const status = statusOf(project, target.id);
  const running = status.state === "running";
  const argv = target.argv_preview || [];
  const cmd = argv.join(" ");
  return (
    '<div class="target ' + (target.enabled ? "" : "off") + '" data-pid="' + project.id + '" data-tid="' + target.id + '">' +
      '<div class="target-head">' +
        '<span class="dot ' + dotClass(status) + '" data-role="dot"></span>' +
        '<button class="target-name" data-act="focus-log" data-pid="' + project.id + '" data-tid="' + target.id +
          '" title="Показать лог процесса">' + esc(target.name) + "</button>" +
        '<span class="badge badge-' + esc(target.kind) + '">' + esc(target.kind) + "</span>" +
        '<span class="target-cmd" title="' + esc(cmd) + '">' + esc(cmd) + "</span>" +
        '<span class="target-state" data-role="state">' + esc(stateText(status)) + "</span>" +
        '<span class="target-actions">' +
          (running ? "" : '<button class="btn btn-sm btn-icon" data-act="target-restart" data-pid="' + project.id +
            '" data-tid="' + target.id + '" title="Перезапустить">⟳</button>') +
          '<button class="btn btn-sm btn-icon" data-act="' + (running ? "target-stop" : "target-start") +
            '" data-pid="' + project.id + '" data-tid="' + target.id +
            '" title="' + (running ? "Остановить" : "Запустить") + '">' + (running ? "■" : "▶") + "</button>" +
        "</span>" +
      "</div>" +
    "</div>"
  );
}

function projectCard(project) {
  const statuses = Object.values(project.statuses || {});
  const running = statuses.some((s) => s.state === "running");
  const hasUrl = !!project.start_url;
  const hasAdmin = !!project.admin_url || project.kind === "django";
  const targets = project.targets || [];
  const auto = targets.some((t) => t.autostart);
  const venv = effectiveEnv(project);

  return (
    '<article class="card" data-card="' + project.id + '">' +
      '<div class="card-head">' +
        '<span class="dot ' + (running ? "dot-running" : "dot-stopped") + '" data-role="card-dot"></span>' +
        '<div class="card-title">' +
          "<h3>" + esc(project.name || project.root) + " " + badgeKind(project.kind) +
            (venv ? ' <span class="pill pill-venv" title="' + esc(venv) + '">venv</span>' : "") +
            (auto ? ' <span class="pill">авто</span>' : "") + "</h3>" +
          '<p class="path" data-act="open-folder" data-pid="' + project.id + '" title="Открыть папку проекта">' +
            esc(project.root) + "</p>" +
        "</div>" +
        '<div class="head-buttons">' +
          '<button class="btn btn-sm" data-act="toggle-logs" data-pid="' + project.id + '" title="Показать логи">Логи' +
            '<span class="unread hidden" data-role="unread" data-pid="' + project.id + '"></span></button>' +
          '<button class="btn btn-sm btn-icon" data-act="card-menu" data-pid="' + project.id + '" title="Ещё">⋯</button>' +
        "</div>" +
      "</div>" +

      '<div class="targets">' +
        (targets.length
          ? targets.map((t) => targetRow(project, t)).join("")
          : '<div class="muted" style="padding:8px 10px;font-size:12px">Нет процессов — нажми «Настроить».</div>') +
      "</div>" +

      '<div class="card-actions">' +
        '<button class="btn btn-sm btn-primary" data-act="project-start" data-pid="' + project.id + '">▶ Запустить всё</button>' +
        '<button class="btn btn-sm btn-icon" data-act="project-restart" data-pid="' + project.id + '"' +
          (running ? "" : " disabled") + ' title="Перезапустить все процессы">⟳</button>' +
        '<button class="btn btn-sm btn-icon" data-act="project-stop" data-pid="' + project.id + '"' +
          (running ? "" : " disabled") + ' title="Остановить все процессы">■</button>' +
        '<span class="sep"></span>' +
        (hasUrl
          ? '<button class="btn btn-sm" data-act="open-url" data-pid="' + project.id + '" title="' +
            esc(project.start_url) + '">Открыть сайт' +
            (project.url_alive ? '<span class="dot dot-running" style="display:inline-block"></span>' : "") + "</button>"
          : "") +
        (hasAdmin ? '<button class="btn btn-sm" data-act="open-admin" data-pid="' + project.id +
          '" title="' + esc(project.admin_url || project.start_url + "/admin/") + '">Админка</button>' : "") +
        '<button class="btn btn-sm" data-act="open-ide" data-pid="' + project.id + '" title="' +
          esc(project.open_with || "не задано в настройках проекта") + '">В IDE</button>' +
      "</div>" +
    "</article>"
  );
}

function renderProjects() {
  const host = $("#projects");
  host.innerHTML = state.projects.map(projectCard).join("");
  $("#empty").classList.toggle("hidden", state.projects.length > 0);
  if (state.dockOpen) renderTabs();
  updateProjectLogBadges();
}

function updateProjectLogBadges() {
  state.projects.forEach((p) => {
    const unread = (p.targets || []).reduce((sum, t) => sum + getBuf(tabId(p.id, t.id)).unread, 0);
    $$('[data-role="unread"][data-pid="' + p.id + '"]').forEach((badge) => {
      badge.classList.toggle("hidden", !unread);
      badge.textContent = unread > 999 ? "999+" : String(unread);
    });
  });
}

function effectiveEnv(project) {
  const raw = (project.interpreter || (state.settings && state.settings.default_venv) || "").trim();
  if (!raw) return "";
  const normalized = raw.replace(/\//g, "\\");
  const parts = normalized.split("\\").filter(Boolean);
  const last = (parts[parts.length - 1] || "").toLowerCase();
  const parent = (parts[parts.length - 2] || "").toLowerCase();
  if (parent === "scripts" || parent === "bin") return parts.slice(0, -2).join("\\");
  if (!last.startsWith("python")) return "";
  return normalized;
}

function applyStatus(pid, tid, status) {
  const project = projectById(pid);
  if (project) {
    project.statuses = project.statuses || {};
    project.statuses[tid] = status;
  }
  const row = $('.target[data-pid="' + pid + '"][data-tid="' + tid + '"]');
  if (row) {
    const dot = $('[data-role="dot"]', row);
    const text = $('[data-role="state"]', row);
    if (dot) dot.className = "dot " + dotClass(status);
    if (text) text.textContent = stateText(status);
  }
  if (!project) return;

  const running = Object.values(project.statuses).some((s) => s.state === "running");
  const cardDot = $('.card[data-card="' + pid + '"] [data-role="card-dot"]');
  if (cardDot) cardDot.className = "dot " + (running ? "dot-running" : "dot-stopped");

  const card = $('.card[data-card="' + pid + '"]');
  if (card) {
    const restart = $('[data-act="project-restart"]', card);
    const stop = $('[data-act="project-stop"]', card);
    if (restart) restart.disabled = !running;
    if (stop) stop.disabled = !running;
  }
}

/* ----------------------------------------------------------------- actions */

async function handleAction(act, el) {
  const pid = el.dataset.pid;
  const tid = el.dataset.tid;
  const base = "/api/projects/" + pid;

  try {
    if (act === "toggle-logs") {
      if (state.dockOpen && activeTab() && activeTab().pid === pid) closeDock();
      else openDock(tabId(pid, firstTargetId(pid)));
      return;
    }
    if (act === "focus-log") return openDock(tabId(pid, tid));
    if (act === "card-menu") return openCardMenu(el, pid);
    if (act === "project-edit") return openProjectEditor(projectById(pid));
    if (act === "project-delete") {
      const project = projectById(pid);
      const ok = await confirmDialog("Удалить проект?",
        "Проект «" + (project.name || project.root) + "» и его процессы будут удалены из ProjectControl. Файлы на диске не тронуты.",
        "Удалить");
      if (!ok) return;
      await api(base, { method: "DELETE" });
      state.logs.delete(pid);
      await refresh();
      toast("Проект удалён", "ok");
      return;
    }
    if (act === "target-start") {
      const res = await api(base + "/targets/" + tid + "/start", { method: "POST" });
      return reportResult(res.result);
    }
    if (act === "target-restart") {
      const res = await api(base + "/targets/" + tid + "/restart", { method: "POST" });
      return reportResult(res.result);
    }
    if (act === "target-stop") {
      const res = await api(base + "/targets/" + tid + "/stop", { method: "POST" });
      return reportResult(res.result);
    }
    if (act === "project-start" || act === "project-restart") {
      const res = await api(base + "/" + (act === "project-start" ? "start" : "restart"), { method: "POST" });
      (res.results || []).forEach((r) => reportResult(r, r.target));
      return;
    }
    if (act === "project-stop") {
      await api(base + "/stop", { method: "POST" });
      toast("Остановлено", "ok");
      return;
    }
    if (act === "open-url") return reportResult(await api(base + "/open-url", { method: "POST" }));
    if (act === "open-admin") return reportResult(await api(base + "/open-admin", { method: "POST" }));
    if (act === "open-folder") return reportResult(await api(base + "/open-folder", { method: "POST" }));
    if (act === "open-ide") return reportResult(await api(base + "/open-ide", { method: "POST" }));
    if (act === "save-template") return openSaveTemplate(pid);
  } catch (err) {
    toast(err.message, "error", 7000);
  }
}

function openCardMenu(anchor, pid) {
  const project = projectById(pid);
  if (!project) return;
  openMenu(anchor, [
    { act: "project-edit", label: "Настроить проект" },
    { act: "focus-log", label: "Показать логи", disabled: !(project.targets || []).length },
    { act: "open-folder", label: "Открыть папку" },
    { act: "open-ide", label: "Открыть в IDE", disabled: !project.open_with },
    { act: "save-template", label: "Сохранить как шаблон" },
    { separator: true },
    { act: "project-delete", label: "Удалить проект", danger: true },
  ]);
}

function firstTargetId(pid) {
  const project = projectById(pid);
  const targets = (project && project.targets) || [];
  return targets.length ? targets[0].id : "";
}

function reportResult(result, label) {
  if (!result) return;
  if (result.ok === false) {
    toast((label ? "[" + label + "] " : "") + (result.error || "Не удалось"), "error", 8000);
  } else if (result.already_running) {
    toast((label ? "[" + label + "] " : "") + "уже запущен", "warn", 2500);
  }
}
