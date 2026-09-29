/* ProjectControl — редактор проекта: корневая папка + строка команды + галочки celery */

const IDE_HINTS = ["code {root}", "pycharm64 {root}", "idea64 {root}", "devenv {root}", "subl {root}", "notepad {root}"];
const TARGET_KINDS = ["web", "worker", "beat", "task", "custom"];

function expandTokens(text, project, target) {
  const values = {
    root: project.root, name: project.name || "", host: project.host || "127.0.0.1",
    port: String(project.port || 8000), app: (target && target.name) || "app",
    python: (target && target.interpreter) || project.interpreter || "python",
    interpreter: (target && target.interpreter) || project.interpreter || "python",
  };
  return String(text).replace(/\{(root|name|host|port|app|python|interpreter)\}/g, (m, k) =>
    (k in values ? values[k] : m));
}

function previewArgv(project, target) {
  if ((target.command || []).length) return target.command.map((t) => expandTokens(t, project, target));
  const interp = target.interpreter || project.interpreter || "python";
  let argv;
  if (target.script) argv = [interp, target.script];
  else if (target.module) argv = [interp, "-m", target.module];
  else argv = [interp];
  return argv.concat(target.args || []).map((t) => expandTokens(t, project, target));
}

function targetPreview(t) {
  if ((t.command || []).length) return t.command.join(" ");
  const parts = [];
  if (t.script) parts.push(t.script);
  if (t.module) parts.push("-m", t.module);
  (t.args || []).forEach((a) => parts.push(a));
  return parts.join(" ");
}

function splitCommand(text) {
  return parseArgLines(String(text || "").replace(/\r?\n/g, " "));
}

function commandText(target) {
  return (target.command || []).join(" ");
}

function globalVenv() {
  return ((state.settings && state.settings.default_venv) || "").trim();
}

function venvHint() {
  const own = "Своё окружение проекта приоритетнее общего.";
  const global = globalVenv();
  if (!global) return "Пусто — используется общий .venv из настроек (он не задан, значит python из PATH). " + own;
  return "Пусто — используется общий .venv: " + global + ". " + own;
}

/* ------------------------------------------------------------------ форма */

function openProjectEditor(project, template) {
  const isNew = !project;
  const defaults = {
    name: "", root: "", kind: "auto", interpreter: "",
    command: "", celery_app: "", worker: "", beat: "", worker_on: false, beat_on: false,
    env: {}, env_file: ".env", load_env_file: true, cwd: "",
    host: "127.0.0.1", port: 8000, start_url: "", admin_url: "", open_with: "", notes: "",
    stop_timeout: 5, log_lines: (state.settings && state.settings.default_log_lines) || 2000,
    autostart: false, autorestart: true, restart_delay: 10, extra: [],
  };
  const draft = project ? draftFromProject(project) : Object.assign({}, defaults);

  if (template) {
    const web = (template.targets || []).find((t) => t.kind === "web");
    const worker = (template.targets || []).find((t) => t.kind === "worker");
    const beat = (template.targets || []).find((t) => t.kind === "beat");
    if (web) draft.command = commandText(web);
    if (worker) {
      draft.worker = commandText(worker);
      draft.worker_on = true;
      const m = /-A\s+([\w.]+)/.exec(draft.worker);
      if (m) draft.celery_app = m[1];
    }
    if (beat) {
      draft.beat = commandText(beat);
      draft.beat_on = true;
    }
    draft.extra = (template.targets || [])
      .filter((t) => !["web", "worker", "beat"].includes(t.kind))
      .map((t) => ({ name: t.name, kind: t.kind, command: commandText(t) }));
    if (template.kind) draft.kind = template.kind;
    if (template.start_url) draft.start_url = template.start_url;
    if (template.admin_url) draft.admin_url = template.admin_url;
    if (template.open_with) draft.open_with = template.open_with;
    if (template.env_file) draft.env_file = template.env_file;
    if (template.env) draft.env = Object.assign({}, draft.env, template.env);
  }

  const history = (state.history || []).map((h) => '<option value="' + esc(h) + '">' + esc(h) + "</option>").join("");
  const tplOptions = state.templates.map((t) =>
    '<option value="' + t.id + '">' + esc(t.name) + " (" + (t.targets || []).length + " проц.)</option>").join("");

  const body =
    '<div class="row">' +
      '<div class="field"><label>Название</label><input type="text" id="f-name" value="' + esc(draft.name) + '" placeholder="my-project"></div>' +
      '<div class="field"><label>Тип проекта</label><select id="f-kind">' +
        ["auto", "django", "fastapi", "flask", "node", "celery", "python", "custom"].map((k) =>
          '<option value="' + k + '"' + (draft.kind === k ? " selected" : "") + ">" + k + "</option>").join("") +
      "</select></div>" +
    "</div>" +

    '<div class="field"><label>Корневая папка проекта</label>' +
      '<div class="inline">' +
        '<input type="text" id="f-root" value="' + esc(draft.root) + '" placeholder="C:\\Projects\\myapp" list="f-history" style="flex:1">' +
        '<datalist id="f-history">' + history + "</datalist>" +
        '<button class="btn" id="f-browse" type="button">Обзор…</button>' +
        '<button class="btn" id="f-detect" type="button">Анализ</button>' +
      "</div>" +
      '<div class="hint" id="f-notes"></div>' +
      '<div class="hint">Все процессы запускаются из этой папки.</div>' +
    "</div>" +

    '<div class="field"><label>Команда запуска</label>' +
      '<input type="text" id="f-command" class="mono" value="' + esc(draft.command) + '" placeholder="python app.py">' +
      '<div class="hint">Одна строка, как в терминале. Подстановки: {python}, {root}, {port}.</div>' +
      '<div class="ed-preview" id="f-preview"></div>' +
    "</div>" +

    '<div class="field"><label>Окружение этого проекта (.venv)</label>' +
      '<div class="inline">' +
        '<input type="text" id="f-interpreter" class="mono" value="' + esc(draft.interpreter) + '" placeholder="C:\\Projects\\myapp\\.venv" style="flex:1">' +
        '<button class="btn" id="f-venv-browse" type="button">Обзор…</button>' +
        '<button class="btn" id="f-venv-check" type="button">Проверить</button>' +
      "</div>" +
      '<div class="hint" id="f-venv-hint">' + esc(venvHint()) + "</div>" +
      '<div class="hint" id="f-venv-result"></div>' +
    "</div>" +

    '<div class="field"><label>Поведение</label>' +
      '<label class="check"><input type="checkbox" id="f-autostart"' + (draft.autostart ? " checked" : "") + "> Запускать этот проект при старте ProjectControl</label>" +
      '<label class="check"><input type="checkbox" id="f-autorestart"' + (draft.autorestart !== false ? " checked" : "") + "> Автоперезапуск при ошибке</label>" +
      '<div class="field" style="margin:0"><label>Повторять через, секунд</label>' +
        '<input type="number" id="f-restart-delay" value="' + esc(draft.restart_delay || 10) + '" min="1" step="1" style="max-width:140px"></div>' +
    "</div>" +

    '<fieldset><legend>Celery</legend>' +
      '<div class="field"><label>Путь к приложению celery (то, что после -A)</label>' +
        '<input type="text" id="f-celery-app" class="mono" value="' + esc(draft.celery_app) + '" placeholder="src.settings.celery">' +
        '<div class="hint" id="f-celery-hint"></div></div>' +
      '<label class="check"><input type="checkbox" id="f-worker-on"' + (draft.worker_on ? " checked" : "") + "> <b>celery worker</b></label>" +
      '<input type="text" id="f-worker" class="mono" value="' + esc(draft.worker) + '" placeholder="celery -A src.settings.celery worker --pool=solo --loglevel=info"' + (draft.worker_on ? "" : " disabled") + ">" +
      '<label class="check" style="margin-top:12px"><input type="checkbox" id="f-beat-on"' + (draft.beat_on ? " checked" : "") + "> <b>celery beat</b></label>" +
      '<input type="text" id="f-beat" class="mono" value="' + esc(draft.beat) + '" placeholder="celery -A src.settings.celery beat --loglevel=info"' + (draft.beat_on ? "" : " disabled") + ">" +
    "</fieldset>" +

    '<fieldset><legend>Свои процессы</legend>' +
      '<div id="f-extra"></div>' +
      '<button class="btn btn-sm" type="button" id="f-add-extra">+ добавить процесс</button>' +
      '<div class="hint">Например: flower, миграции, отдельный скрипт.</div>' +
    "</fieldset>" +

    '<details class="target-editor"><summary>Дополнительно: .env, адреса, IDE</summary>' +
      '<div class="body">' +
        '<label class="check"><input type="checkbox" id="f-load-env"' + (draft.load_env_file ? " checked" : "") + "> Читать переменные из файла .env</label>" +
        '<div class="field"><label>Файл с переменными</label><input type="text" id="f-env-file" value="' + esc(draft.env_file || ".env") + '"></div>' +
        '<div class="field"><label>Свои переменные (KEY=VALUE)</label><textarea id="f-env" rows="3">' + esc(kvToText(draft.env)) + "</textarea></div>" +
        '<div class="row">' +
          '<div class="field"><label>Хост</label><input type="text" id="f-host" value="' + esc(draft.host) + '"></div>' +
          '<div class="field"><label>Порт</label><input type="number" id="f-port" value="' + esc(draft.port || "") + '"></div>' +
          '<div class="field"><label>Лимит строк в логе</label><input type="number" id="f-log-lines" value="' + esc(draft.log_lines) + '" min="100" step="100"></div>' +
        "</div>" +
        '<div class="field"><label>Адрес проекта (кнопка «Открыть сайт»)</label>' +
          '<input type="text" id="f-start-url" value="' + esc(draft.start_url || "") + '" placeholder="http://127.0.0.1:8000"></div>' +
        '<div class="field"><label>Адрес админки Django (кнопка «Админка»)</label>' +
          '<input type="text" id="f-admin-url" value="' + esc(draft.admin_url || "") + '" placeholder="http://127.0.0.1:8000/admin/"></div>' +
        '<div class="field"><label>Открывать в IDE</label>' +
          '<input type="text" id="f-open-with" value="' + esc(draft.open_with || "") + '" placeholder="code {root}" list="f-ide">' +
          '<datalist id="f-ide">' + IDE_HINTS.map((h) => "<option>" + h + "</option>").join("") + "</datalist></div>" +
        '<div class="field"><label>Таймаут остановки, с</label><input type="number" id="f-stop-timeout" value="' + esc(draft.stop_timeout) + '" min="0" step="0.5"></div>' +
        '<div class="field"><label>Заметки</label><textarea id="f-notes-field" rows="2">' + esc(draft.notes || "") + "</textarea></div>" +
      "</div>" +
    "</details>" +

    '<div class="field"><label>Применить шаблон</label><div class="inline">' +
      '<select id="f-template" style="flex:1"><option value="">— выбрать шаблон —</option>' + tplOptions + "</select>" +
      '<button class="btn" type="button" id="f-apply-tpl">Применить</button></div></div>';

  modal({
    title: isNew ? "Новый проект" : "Настройка проекта",
    body,
    foot: '<button class="btn" data-close="1">Отмена</button>' +
          '<button class="btn btn-primary" id="f-save">Сохранить</button>',
    onMount(overlay) {
      overlay.dataset.pid = project ? project.id : "";
      mountEditor(overlay, draft, isNew);
    },
  });
}

function draftFromProject(project) {
  const targets = project.targets || [];
  const web = targets.find((t) => t.kind === "web") || targets[0];
  const worker = targets.find((t) => t.kind === "worker");
  const beat = targets.find((t) => t.kind === "beat");
  const extra = targets.filter((t) => t !== web && t !== worker && t !== beat);
  const workerText = worker ? commandText(worker) : "";
  const appMatch = /(?:-A\s+|--app[= ])([\w.]+)/.exec(workerText);
  return {
    id: project.id,
    name: project.name || "",
    root: project.root,
    kind: project.kind,
    interpreter: project.interpreter || "",
    command: web ? commandText(web) : "",
    celery_app: appMatch ? appMatch[1] : "",
    worker: workerText,
    beat: beat ? commandText(beat) : "",
    worker_on: !!worker,
    beat_on: !!beat,
    env: project.env || {},
    env_file: project.env_file || ".env",
    load_env_file: project.load_env_file !== false,
    cwd: project.cwd || "",
    host: project.host || "127.0.0.1",
    port: project.port || 8000,
    start_url: project.start_url || "",
    admin_url: project.admin_url || "",
    open_with: project.open_with || "",
    notes: project.notes || "",
    stop_timeout: project.stop_timeout || 5,
    log_lines: project.log_lines || 2000,
    autostart: !!project.autostart_all || targets.some((t) => t.autostart),
    autorestart: project.autorestart !== false,
    restart_delay: project.restart_delay || 10,
    extra: extra.map((t) => ({ name: t.name, kind: t.kind, command: commandText(t) })),
  };
}

/* ------------------------------------------------------------------ логика */

function mountEditor(root, draft, isNew) {
  const extraBox = root.querySelector("#f-extra");
  const preview = root.querySelector("#f-preview");
  const touched = { worker: false, beat: false };

  const el = (sel) => root.querySelector(sel);
  const val = (sel) => (el(sel) ? el(sel).value.trim() : "");

  el("#f-venv-browse").onclick = () => {
    browseFolders(val("#f-interpreter"), (path) => {
      el("#f-interpreter").value = path;
      el("#f-venv-result").textContent = "";
      syncCeleryInputs();
      refreshPreview();
    });
  };
  el("#f-venv-check").onclick = async () => {
    const value = val("#f-interpreter");
    const out = el("#f-venv-result");
    if (!value) { out.textContent = "Пусто — берётся общий .venv или python из PATH."; return; }
    out.textContent = "Проверяю…";
    try {
      const info = await api("/api/env/check", { method: "POST", body: { url: value } });
      if (!info.ok) { out.textContent = "✗ " + (info.error || "не получилось"); return; }
      out.textContent = "✓ Python " + (info.version || "?") + " · пакетов: " + info.packages +
        (info.interesting.length ? " · " + info.interesting.join(", ") : "");
      el("#f-venv-hint").textContent = info.venv ? "Окружение активируется: " + info.venv : info.note;
    } catch (err) {
      out.textContent = "✗ " + err.message;
    }
  };
  el("#f-interpreter").addEventListener("input", () => {
    el("#f-venv-result").textContent = "";
    el("#f-venv-hint").textContent = venvHint();
    syncCeleryInputs();
    refreshPreview();
  });

  function projectStub() {
    return {
      root: val("#f-root"), name: val("#f-name"), interpreter: val("#f-interpreter"),
      host: val("#f-host") || "127.0.0.1", port: Number(val("#f-port")) || 8000,
    };
  }

  function celeryCommand(kind) {
    const app = val("#f-celery-app") || "config.celery";
    // {python} подставится на сервере: и папка .venv, и полный путь к python.exe
    const head = val("#f-interpreter") ? ["{python}", "-m", "celery", "-A", app] : ["celery", "-A", app];
    const tail = kind === "worker" ? ["worker", "--pool=solo", "--loglevel=info"] : ["beat", "--loglevel=info"];
    return head.concat(tail).join(" ");
  }

  function syncCeleryInputs() {
    if (!touched.worker && el("#f-worker-on").checked) el("#f-worker").value = celeryCommand("worker");
    if (!touched.beat && el("#f-beat-on").checked) el("#f-beat").value = celeryCommand("beat");
    el("#f-worker").disabled = !el("#f-worker-on").checked;
    el("#f-beat").disabled = !el("#f-beat-on").checked;
  }

  function refreshPreview() {
    const argv = splitCommand(val("#f-command"));
    if (!argv.length) {
      preview.textContent = "⚠ Укажи команду, например: python app.py";
      preview.classList.add("warn");
      return;
    }
    preview.classList.remove("warn");
    preview.textContent = previewArgv(projectStub(), { name: "app", command: argv }).join("  ") +
      "     (cwd: " + (val("#f-root") || "?") + ")";
  }

  function paintExtra() {
    extraBox.innerHTML = draft.extra.map((x, i) =>
      '<div class="row" data-extra="' + i + '">' +
        '<div class="field"><label>Имя</label><input type="text" data-x="name" value="' + esc(x.name) + '"></div>' +
        '<div class="field"><label>Тип</label><select data-x="kind">' +
          TARGET_KINDS.map((k) => '<option value="' + k + '"' + (x.kind === k ? " selected" : "") + ">" + k + "</option>").join("") +
        "</select></div>" +
        '<div class="field" style="grid-column:1/-1"><label>Команда</label>' +
          '<input type="text" class="mono" data-x="command" value="' + esc(x.command) + '"></div>' +
        '<div class="field" style="grid-column:1/-1"><button class="btn btn-sm btn-danger" type="button" data-x-del="' + i + '">Удалить</button></div>' +
      "</div>").join("");
  }

  function readExtra() {
    return $$("[data-extra]", extraBox).map((block) => ({
      name: $('[data-x="name"]', block).value.trim() || "task",
      kind: $('[data-x="kind"]', block).value,
      command: $('[data-x="command"]', block).value,
    }));
  }

  extraBox.addEventListener("input", () => { draft.extra = readExtra(); });
  extraBox.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-x-del]");
    if (!btn) return;
    draft.extra = readExtra();
    draft.extra.splice(Number(btn.dataset.xDel), 1);
    paintExtra();
  });
  el("#f-add-extra").onclick = () => {
    draft.extra = readExtra();
    draft.extra.push({ name: "task", kind: "task", command: "" });
    paintExtra();
  };

  ["#f-worker-on", "#f-beat-on", "#f-celery-app", "#f-interpreter"].forEach((sel) => {
    el(sel).addEventListener("input", () => { syncCeleryInputs(); refreshPreview(); });
  });
  el("#f-worker").addEventListener("input", () => { touched.worker = true; });
  el("#f-beat").addEventListener("input", () => { touched.beat = true; });
  el("#f-command").addEventListener("input", refreshPreview);
  root.addEventListener("input", refreshPreview);

  el("#f-browse").onclick = () => {
    browseFolders(el("#f-root").value, (path) => {
      el("#f-root").value = path;
      runDetect();
    });
  };
  el("#f-detect").onclick = () => runDetect();

  let detectTimer = null;
  el("#f-root").addEventListener("input", () => {
    clearTimeout(detectTimer);
    detectTimer = setTimeout(() => runDetect(true), 600);
  });

  async function runDetect(quiet) {
    const path = el("#f-root").value.trim();
    const notes = el("#f-notes");
    const hint = el("#f-celery-hint");
    if (!path) return;
    notes.textContent = "Анализирую папку…";
    try {
      const info = await api("/api/detect?root=" + encodeURIComponent(path));
      if (info.kind === "missing") {
        notes.textContent = "Папка не найдена";
        hint.textContent = "";
        return;
      }
      notes.textContent = (info.notes || []).join(" · ");
      if (info.kind !== "unknown") el("#f-kind").value = info.kind;
      if (info.interpreter && !el("#f-interpreter").value.trim()) el("#f-interpreter").value = info.interpreter;
      if (info.env_file) el("#f-env-file").value = info.env_file;

      if (info.celery) {
        hint.textContent = "Celery найден в проекте.";
        if (!el("#f-celery-app").value.trim()) {
          const worker = (info.targets || []).find((t) => t.kind === "worker");
          const args = (worker && worker.args) || [];
          const idx = args.indexOf("-A");
          el("#f-celery-app").value = idx >= 0 ? args[idx + 1] : "";
          touched.worker = false;
          touched.beat = false;
        }
      } else {
        hint.textContent = "Celery не найден. Если он есть — укажи путь вручную и поставь галочки.";
      }

      const web = (info.targets || []).find((t) => t.kind === "web");
      if (web && (!val("#f-command") || !quiet)) {
        if ((web.command || []).length) el("#f-command").value = web.command.join(" ");
        else if (web.module) el("#f-command").value = ["{python}", "-m", web.module].concat(web.args || []).join(" ");
        else el("#f-command").value = ["{python}", web.script].concat(web.args || []).join(" ");
      }

      if (!val("#f-admin-url") && info.admin_url) el("#f-admin-url").value = info.admin_url;
      if (!val("#f-start-url") && info.start_url) el("#f-start-url").value = info.start_url;
      const port = /:(\d{4,5})/.exec(info.start_url || "");
      if (port) el("#f-port").value = port[1];
      if (!val("#f-name")) {
        const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
        el("#f-name").value = parts[parts.length - 1];
      }

      syncCeleryInputs();
      refreshPreview();
    } catch (err) {
      notes.textContent = "Ошибка анализа: " + err.message;
    }
  }

  el("#f-apply-tpl").onclick = () => {
    const tpl = state.templates.find((t) => t.id === val("#f-template"));
    if (!tpl) return;
    const web = (tpl.targets || []).find((t) => t.kind === "web");
    const worker = (tpl.targets || []).find((t) => t.kind === "worker");
    const beat = (tpl.targets || []).find((t) => t.kind === "beat");
    if (web) {
      if ((web.command || []).length) el("#f-command").value = web.command.join(" ");
      else if (web.module) el("#f-command").value = ["{python}", "-m", web.module].concat(web.args || []).join(" ");
      else el("#f-command").value = ["{python}", web.script].concat(web.args || []).join(" ");
    }
    if (worker) {
      el("#f-worker-on").checked = true;
      touched.worker = false;
      el("#f-worker").value = commandText(worker);
      const m = /-A\s+([\w.]+)/.exec(el("#f-worker").value);
      if (m) el("#f-celery-app").value = m[1];
    }
    if (beat) {
      el("#f-beat-on").checked = true;
      touched.beat = false;
      el("#f-beat").value = commandText(beat);
    }
    if (tpl.kind) el("#f-kind").value = tpl.kind;
    if (tpl.start_url) el("#f-start-url").value = tpl.start_url;
    if (tpl.admin_url) el("#f-admin-url").value = tpl.admin_url;
    if (tpl.open_with) el("#f-open-with").value = tpl.open_with;
    if (tpl.notes) el("#f-notes-field").value = tpl.notes;
    const envBox = el("#f-env");
    envBox.value = kvToText(Object.assign({}, textToKv(envBox.value), tpl.env || {}));
    syncCeleryInputs();
    refreshPreview();
    toast("Шаблон «" + tpl.name + "» применён", "ok");
  };

  el("#f-save").onclick = () => saveEditor(root, isNew);

  paintExtra();
  syncCeleryInputs();
  refreshPreview();
  if (isNew && !draft.root && state.history && state.history.length) {
    el("#f-root").value = state.history[0];
    runDetect(true);
  }
}

/* ------------------------------------------------------------------ сохранение */

function buildTargets(root) {
  const name = root.querySelector("#f-name").value.trim();
  const autostart = root.querySelector("#f-autostart").checked;
  const autorestart = root.querySelector("#f-autorestart").checked;
  const timeout = Number(root.querySelector("#f-stop-timeout").value) || 5;
  const delay = Math.max(1, Number(root.querySelector("#f-restart-delay").value) || 10);
  const targets = [];

  const base = { autorestart, autostart, stop_timeout: timeout, restart_delay: delay };
  const main = splitCommand(root.querySelector("#f-command").value);
  if (main.length) {
    targets.push(Object.assign({ name: name || "app", kind: "web", command: main }, base));
  }
  const worker = root.querySelector("#f-worker");
  if (root.querySelector("#f-worker-on").checked && worker.value.trim()) {
    targets.push(Object.assign({ name: "worker", kind: "worker", command: splitCommand(worker.value) }, base));
  }
  const beat = root.querySelector("#f-beat");
  if (root.querySelector("#f-beat-on").checked && beat.value.trim()) {
    targets.push(Object.assign({ name: "beat", kind: "beat", command: splitCommand(beat.value) }, base));
  }
  $$("[data-extra]", root.querySelector("#f-extra")).forEach((block) => {
    const command = splitCommand($('[data-x="command"]', block).value);
    if (!command.length) return;
    targets.push(Object.assign({
      name: $('[data-x="name"]', block).value.trim() || "task",
      kind: $('[data-x="kind"]', block).value,
      command,
      autostart: false,
    }, base));
  });
  return targets;
}

async function saveEditor(root, isNew) {
  const val = (sel) => { const e = root.querySelector(sel); return e ? e.value.trim() : ""; };
  const bool = (sel) => { const e = root.querySelector(sel); return e ? e.checked : false; };

  const rootDir = val("#f-root");
  if (!rootDir) { toast("Укажи корневую папку проекта", "error"); return; }
  const targets = buildTargets(root);
  if (!targets.length) { toast("Укажи команду запуска", "error"); return; }

  const name = val("#f-name") || rootDir.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
  const autostart = bool("#f-autostart");
  const restartDelay = Math.max(1, Number(val("#f-restart-delay")) || 10);
  const body = {
    name,
    root: rootDir,
    kind: val("#f-kind"),
    interpreter: val("#f-interpreter"),
    env: textToKv(root.querySelector("#f-env").value),
    env_file: val("#f-env-file") || ".env",
    load_env_file: bool("#f-load-env"),
    cwd: "",
    host: val("#f-host") || "127.0.0.1",
    port: Number(val("#f-port")) || 0,
    start_url: val("#f-start-url"),
    admin_url: val("#f-admin-url"),
    open_with: val("#f-open-with"),
    notes: root.querySelector("#f-notes-field").value,
    autostart_all: autostart,
    autorestart: bool("#f-autorestart"),
    restart_delay: restartDelay,
    stop_timeout: Number(val("#f-stop-timeout")) || 5,
    log_lines: Number(val("#f-log-lines")) || 2000,
    targets,
  };
  (body.targets || []).forEach((t) => { t.autostart = t.autostart && autostart; });

  const btn = root.querySelector("#f-save");
  btn.disabled = true;
  try {
    const saved = await api(isNew ? "/api/projects" : "/api/projects/" + root.dataset.pid, {
      method: isNew ? "POST" : "PATCH",
      body,
    });
    closeModal();
    await refresh();
    state.openLogs.add(saved.id);
    toast("Сохранено: " + saved.name, "ok");
  } catch (err) {
    toast(err.message, "error", 8000);
  } finally {
    btn.disabled = false;
  }
}

function openSaveTemplate(pid) {
  const project = projectById(pid);
  if (!project) return;
  modal({
    title: "Сохранить как шаблон",
    narrow: true,
    body: '<div class="field"><label>Название шаблона</label><input type="text" id="t-name" value="' +
      esc(project.name + " (" + (project.targets || []).length + " проц.)") + '"></div>' +
      '<div class="hint">Шаблон сохранит все процессы этого проекта — потом применишь к любой папке одним кликом.</div>',
    foot: '<button class="btn" data-close="1">Отмена</button><button class="btn btn-primary" id="t-save">Сохранить</button>',
    onMount(overlay) {
      overlay.querySelector("#t-save").onclick = async () => {
        const name = overlay.querySelector("#t-name").value.trim();
        if (!name) return;
        try {
          await api("/api/templates", {
            method: "POST",
            body: {
              name,
              kind: project.kind,
              targets: project.targets,
              interpreter: project.interpreter,
              env: project.env,
              env_file: project.env_file,
              load_env_file: project.load_env_file,
              start_url: project.start_url,
              admin_url: project.admin_url,
              open_with: project.open_with,
              notes: project.notes,
            },
          });
          closeModal();
          await refresh();
          toast("Шаблон сохранён", "ok");
        } catch (err) { toast(err.message, "error"); }
      };
    },
  });
}
