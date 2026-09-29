/* ProjectControl — ядро UI: утилиты, состояние, API, модалки */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  projects: [],
  templates: [],
  settings: {},
  history: [],
  logs: new Map(),
  activeTab: "",
  dockOpen: false,
  connected: false,
  menu: null,
};

async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
  if (!res.ok) throw new Error((data && data.error) || res.statusText || "Ошибка запроса");
  return data;
}

function toast(message, kind = "", ttl = 4200) {
  const node = document.createElement("div");
  node.className = "toast " + kind;
  node.textContent = message;
  $("#toasts").appendChild(node);
  setTimeout(() => { node.style.opacity = "0"; setTimeout(() => node.remove(), 250); }, ttl);
}

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtDuration(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return h + "ч " + m + "м";
  if (m) return m + "м " + String(s).padStart(2, "0") + "с";
  return s + "с";
}

function splitShell(line) {
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(line))) out.push(m[1] !== undefined ? m[1] : (m[2] !== undefined ? m[2] : m[3]));
  return out;
}

function parseArgLines(text) {
  return String(text || "").split(/\r?\n/).map((l) => l.trim()).filter(Boolean).flatMap((line) =>
    (/\s/.test(line) && !/^["'].*["']$/.test(line) ? splitShell(line) : [line]));
}

function argvToText(argv) { return (argv || []).join("\n"); }

function kvToText(kv) {
  return Object.entries(kv || {}).map(([k, v]) => k + "=" + v).join("\n");
}

function textToKv(text) {
  const out = {};
  String(text || "").split(/\r?\n/).forEach((line) => {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) return;
    const i = t.indexOf("=");
    const key = t.slice(0, i).trim().replace(/^export\s+/, "");
    if (key) out[key] = t.slice(i + 1).trim().replace(/^["']|["']$/g, "");
  });
  return out;
}

function badgeKind(kind) {
  const map = {
    django: "Django", fastapi: "FastAPI", flask: "Flask", node: "Node",
    celery: "Celery", python: "Python", custom: "Custom",
  };
  return '<span class="badge badge-' + esc(kind) + '">' + esc(map[kind] || kind || "auto") + "</span>";
}

/* ------------------------------------------------------------------ modals
   Модалки складываются в стек: вложенная (обзор папок, подтверждение) не
   уничтожает родительскую, а прячет её и возвращает обратно при закрытии. */

function modalStack() {
  return Array.from(document.querySelectorAll("#modal-root .overlay"));
}

function closeModal() {
  const stack = modalStack();
  const top = stack[stack.length - 1];
  if (!top) return;
  top.remove();
  const below = modalStack()[modalStack().length - 1];
  if (below) below.style.display = "";
}

function closeAllModals() {
  modalStack().forEach((el) => el.remove());
}

/* ------------------------------------------------------------------ меню */

function closeMenu() {
  if (state.menu) { state.menu.remove(); state.menu = null; }
}

function openMenu(anchor, items) {
  closeMenu();
  const rect = anchor.getBoundingClientRect();
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.innerHTML = items.map((it) => {
    if (it.separator) return '<div class="menu-sep"></div>';
    return '<button class="menu-item' + (it.danger ? " danger" : "") + '" data-menu-act="' + esc(it.act) + '"' +
      (it.disabled ? " disabled" : "") + ">" + esc(it.label) + "</button>";
  }).join("");

  document.body.appendChild(menu);
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  menu.style.top = Math.min(rect.bottom + 6, window.innerHeight - height - 10) + "px";
  menu.style.left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)) + "px";

  menu.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-menu-act]");
    if (!btn) return;
    const act = btn.dataset.menuAct;
    closeMenu();
    handleAction(act, { dataset: { pid: anchor.dataset.pid } });
  });
  state.menu = menu;
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }), 0);
}

function modal(opts) {
  const root = $("#modal-root");
  const foot = opts.foot || '<button class="btn" data-close="1">Закрыть</button>';
  const stack = modalStack();
  stack.forEach((el) => { el.style.display = "none"; });

  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.innerHTML =
    '<div class="modal ' + (opts.narrow ? "narrow" : "") + '">' +
      '<div class="modal-head"><h2>' + esc(opts.title) + "</h2>" +
        '<button class="btn btn-sm btn-ghost" data-close="1">Закрыть</button></div>' +
      '<div class="modal-body">' + opts.body + "</div>" +
      '<div class="modal-foot">' + foot + "</div>" +
    "</div>";
  root.appendChild(overlay);

  overlay.querySelectorAll("[data-close]").forEach((el) =>
    el.addEventListener("click", () => closeModal()));
  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) closeModal();
  });
  if (opts.onMount) opts.onMount(overlay);
}

function confirmDialog(title, message, okLabel = "Удалить") {
  return new Promise((resolve) => {
    modal({
      title, narrow: true,
      body: "<p>" + esc(message) + "</p>",
      foot: '<button class="btn" data-no>Отмена</button>' +
            '<button class="btn btn-danger" data-yes>' + esc(okLabel) + "</button>",
      onMount(root) {
        root.querySelector("[data-no]").onclick = () => { closeModal(); resolve(false); };
        root.querySelector("[data-yes]").onclick = () => { closeModal(); resolve(true); };
      },
    });
  });
}

/* ------------------------------------------------------------ folder picker */

function browseFolders(startPath, onPick) {
  let current = startPath || "";
  modal({
    title: "Выбор папки",
    narrow: true,
    body:
      '<div class="field"><div class="inline">' +
        '<input type="text" id="fs-path" placeholder="C:\\Projects\\myapp">' +
        '<button class="btn btn-sm" data-up>Вверх</button>' +
      "</div><div class=\"hint\" id=\"fs-drives\"></div></div>" +
      '<div class="fs-list" id="fs-list"></div>',
    foot: '<button class="btn" data-close="1">Отмена</button>' +
          '<button class="btn btn-primary" data-ok>Выбрать эту папку</button>',
    onMount(root) {
      const listEl = root.querySelector("#fs-list");
      const pathEl = root.querySelector("#fs-path");
      const drivesEl = root.querySelector("#fs-drives");

      api("/api/fs/roots").then((roots) => {
        const all = [roots.home].concat(roots.drives || []);
        drivesEl.innerHTML = all.map((r) =>
          '<button class="btn btn-sm" data-drive="' + esc(r.path) + '">' + esc(r.name) + "</button>").join(" ");
        drivesEl.querySelectorAll("[data-drive]").forEach((b) => {
          b.onclick = () => load(b.dataset.drive);
        });
      }).catch(() => {});

      async function load(path) {
        current = path || "";
        pathEl.value = current;
        listEl.innerHTML = '<div class="log-empty">Загрузка…</div>';
        try {
          const data = await api("/api/fs/list?path=" + encodeURIComponent(current));
          listEl.innerHTML = "";
          const rows = [];
          if (data.parent) {
            rows.push('<div class="fs-row" data-path="' + esc(data.parent) + '">.. <span>наверх</span></div>');
          }
          (data.entries || []).forEach((entry) => {
            if (!entry.is_dir) return;
            rows.push('<div class="fs-row" data-path="' + esc(entry.path) + '">' + esc(entry.name) + " <span>папка</span></div>");
          });
          if (!rows.length) rows.push('<div class="log-empty">Папок нет</div>');
          listEl.innerHTML = rows.join("");
          listEl.querySelectorAll("[data-path]").forEach((row) => {
            row.onclick = () => load(row.dataset.path);
          });
        } catch (err) {
          listEl.innerHTML = '<div class="log-empty">' + esc(err.message) + "</div>";
        }
      }

      pathEl.onkeydown = (e) => { if (e.key === "Enter") load(pathEl.value); };
      root.querySelector("[data-up]").onclick = () => {
        const sep = current.includes("\\") ? "\\" : "/";
        const parts = current.split(sep);
        parts.pop();
        load(parts.join(sep) || sep);
      };
      root.querySelector("[data-ok]").onclick = () => {
        onPick(pathEl.value.trim());
        closeModal();
      };
      load(current);
    },
  });
}

/* --------------------------------------------------------------- templates */

function openTemplates() {
  const cards = state.templates.map((t) => {
    const lines = (t.targets || []).map((x) => x.name + ": " + targetPreview(x)).join("\n");
    return '<div class="tpl" data-id="' + t.id + '">' +
      "<h4>" + esc(t.name) + (t.builtin ? ' <span class="pill">встроенный</span>' : "") + "</h4>" +
      badgeKind(t.kind) +
      "<pre>" + esc(lines) + "</pre>" +
      (t.notes ? '<div class="muted" style="font-size:11px">' + esc(t.notes) + "</div>" : "") +
      '<div class="row-actions">' +
        '<button class="btn btn-sm" data-use>Применить к новому проекту</button>' +
        (t.builtin ? "" : '<button class="btn btn-sm btn-danger" data-del>Удалить</button>') +
      "</div></div>";
  }).join("");

  modal({
    title: "Шаблоны запуска",
    body: '<p class="muted">Шаблон — набор процессов (web + celery worker + beat и т.д.), который применяется к любому проекту в один клик. ' +
          "Все настройки проектов сохраняются автоматически, ничего вводить заново не нужно.</p>" +
          '<div class="tpl-list">' + cards + "</div>",
    onMount(root) {
      root.querySelectorAll("[data-use]").forEach((btn) => {
        btn.onclick = () => {
          const id = btn.closest("[data-id]").dataset.id;
          const tpl = state.templates.find((t) => t.id === id);
          closeModal();
          openProjectEditor(null, tpl);
        };
      });
      root.querySelectorAll("[data-del]").forEach((btn) => {
        btn.onclick = async () => {
          const id = btn.closest("[data-id]").dataset.id;
          await api("/api/templates/" + id, { method: "DELETE" });
          toast("Шаблон удалён", "ok");
          await refresh();
          openTemplates();
        };
      });
    },
  });
}

/* ---------------------------------------------------------------- settings */

function openSettings() {
  const s = state.settings || {};
  modal({
    title: "Настройки ProjectControl",
    narrow: true,
    body:
      '<div class="field"><label>Окружение (.venv) для всех проектов</label>' +
        '<div class="inline">' +
          '<input type="text" id="s-venv" class="mono" value="' + esc(s.default_venv || "") +
            '" placeholder="C:\\Projects\\myapp\\.venv" style="flex:1">' +
          '<button class="btn" id="s-venv-browse" type="button">Обзор…</button>' +
          '<button class="btn" id="s-venv-check" type="button">Проверить</button>' +
        "</div>" +
        '<div class="hint" id="s-venv-hint">Укажи папку .venv (или путь к python.exe). Процессы будут запускаться ' +
          "внутри неё: подхватываются её celery/uvicorn и все пакеты. Пусто — окружение не активируется, " +
          "используется python из PATH.</div>" +
        '<div class="hint" id="s-venv-result"></div>' +
      "</div>" +
      '<div class="field"><label>Автозапуск проектов</label>' +
        '<label class="check"><input type="checkbox" id="s-autostart"' +
          (s.autostart_saved ? " checked" : "") + "> При старте панели запускать проекты с галочкой «авто»</label>" +
        '<div class="hint">Относится ко всем проектам сразу.</div></div>' +
      '<div class="row">' +
        '<div class="field"><label>Хост интерфейса</label><input type="text" id="s-host" value="' + esc(s.host || "127.0.0.1") + '"></div>' +
        '<div class="field"><label>Порт интерфейса</label><input type="number" id="s-port" value="' + esc(s.port || 8770) + '"></div>' +
      "</div>" +
      '<div class="row">' +
        '<div class="field"><label>Строк лога по умолчанию</label><input type="number" id="s-log" value="' + esc(s.default_log_lines || 2000) + '">' +
          '<div class="hint">Хранится только кольцо последних строк — память не растёт.</div></div>' +
        '<div class="field"><label>История папок</label><input type="number" id="s-hist" value="' + esc(s.max_history || 50) + '"></div>' +
      "</div>" +
      '<label class="check"><input type="checkbox" id="s-browser"' + (s.open_browser ? " checked" : "") + "> Открывать браузер при запуске</label>" +
      '<div class="field"><label>Недавние папки</label><div class="muted mono" style="font-size:11px;white-space:pre-wrap">' +
        esc((state.history || []).join("\n") || "—") + "</div></div>",
    foot: '<button class="btn" data-close="1">Отмена</button><button class="btn btn-primary" data-save>Сохранить</button>',
    onMount(root) {
      const venvInput = root.querySelector("#s-venv");
      const result = root.querySelector("#s-venv-result");
      const hint = root.querySelector("#s-venv-hint");

      root.querySelector("#s-venv-browse").onclick = () => {
        browseFolders(venvInput.value, (path) => { venvInput.value = path; });
      };
      venvInput.addEventListener("input", () => { result.textContent = ""; });

      async function verify() {
        const value = venvInput.value.trim();
        if (!value) { result.textContent = "Окружение не задано — будет использован python из PATH."; return; }
        result.textContent = "Проверяю…";
        try {
          const info = await api("/api/env/check", { method: "POST", body: { url: value } });
          if (!info.ok) { result.textContent = "✗ " + (info.error || "не получилось"); return; }
          result.textContent = "✓ Python " + (info.version || "?") + " · пакетов: " + info.packages +
            (info.interesting.length ? " · " + info.interesting.join(", ") : "");
          hint.textContent = info.venv
            ? "Окружение активируется: " + info.venv
            : info.note;
        } catch (err) {
          result.textContent = "✗ " + err.message;
        }
      }
      root.querySelector("#s-venv-check").onclick = verify;

      root.querySelector("[data-save]").onclick = async () => {
        const body = {
          host: root.querySelector("#s-host").value.trim() || "127.0.0.1",
          port: Number(root.querySelector("#s-port").value) || 8770,
          default_log_lines: Number(root.querySelector("#s-log").value) || 2000,
          max_history: Number(root.querySelector("#s-hist").value) || 50,
          open_browser: root.querySelector("#s-browser").checked,
          autostart_saved: root.querySelector("#s-autostart").checked,
          default_venv: venvInput.value.trim(),
        };
        try {
          state.settings = await api("/api/settings", { method: "PUT", body });
          await refresh();
          toast("Настройки сохранены", "ok");
          closeModal();
        } catch (err) { toast(err.message, "error"); }
      };
    },
  });
}
