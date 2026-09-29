/* Проверка фронтенда ProjectControl.
   1) Живой сервер + headless Edge/Chrome: страница рендерится без JS-ошибок.
   2) Фикстура с РЕАЛЬНЫМИ данными /api/state: карточки, док, вкладки, поиск, стили строк.
   Запуск: node scripts/ui_check.js */
const { spawn, spawnSync } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

const ROOT = path.join(__dirname, "..");
const STATIC = path.join(ROOT, "projectcontrol", "static");
const PORT = 8811;
const FIXTURE_PORT = 8812;
const BASE = `http://127.0.0.1:${PORT}`;

const BROWSERS = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];

const FAKE_LINES = [
  { stream: "cmd", text: "$ python main.py" },
  { stream: "stdout", text: "Django started, listening on 127.0.0.1:8000" },
  { stream: "stdout", text: "celery@host ready." },
  { stream: "meta", text: "Перезапуск через 10 сек" },
  { stream: "error", text: "Traceback (most recent call last): ModuleNotFoundError" },
];

let failed = 0;
function check(label, cond, extra) {
  if (!cond) failed++;
  console.log((cond ? "  OK  " : " FAIL ") + label + (extra !== undefined && !cond ? "  -> " + extra : ""));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function get(url, tries = 4) {
  return new Promise((resolve, reject) => {
    const attempt = (n) => {
      const req = http.get(url, (res) => {
        let body = "";
        res.on("data", (c) => (body += c));
        res.on("end", () => resolve(body));
      });
      req.on("error", (err) => (n > 1 ? setTimeout(() => attempt(n - 1), 300) : reject(err)));
    };
    attempt(tries);
  });
}

function post(url, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = http.request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
    }, (res) => { let b = ""; res.on("data", (c) => (b += c)); res.on("end", () => resolve(JSON.parse(b || "{}"))); });
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

async function waitFor(url) {
  for (let i = 0; i < 100; i++) {
    try { await get(url, 1); return true; } catch (e) { await sleep(200); }
  }
  return false;
}

function dumpDom(browser, url) {
  // spawnSync блокирует event loop Node, а fixture-сервер живёт именно в нём
  return new Promise((resolve) => {
    const proc = spawn(browser, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
      "--virtual-time-budget=8000", "--dump-dom", url,
    ], { stdio: ["ignore", "pipe", "pipe"] });
    let dom = "";
    let stderr = "";
    const timer = setTimeout(() => proc.kill(), 90000);
    proc.stdout.on("data", (c) => (dom += c));
    proc.stderr.on("data", (c) => (stderr += c));
    proc.on("close", () => { clearTimeout(timer); resolve({ dom, stderr }); });
    proc.on("error", () => { clearTimeout(timer); resolve({ dom: "", stderr: "браузер не запустился" }); });
  });
}

function pageErrors(stderr) {
  return stderr.split("\n").filter((line) =>
    /CONSOLE|Uncaught|SyntaxError|ReferenceError|is not a function|is not defined/i.test(line) &&
    !/chrome\\|task_manager|fallback_task_provider|voice_transcription|Fontconfig|DEPRECATED/i.test(line));
}

function slice(html, id, length) {
  const at = html.indexOf('id="' + id + '"');
  if (at < 0) return "";
  const start = html.lastIndexOf(">", at) + 1;
  return html.slice(start, start + (length || 900));
}

/* ------------------------------------------------------------- фикстура */
function startFixture(state) {
  const stateJson = JSON.stringify(state).replace(/<\//g, "<\\/");
  const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/styles.css"></head><body>
<div id="toasts" class="toasts"></div>
<header class="topbar"><div class="brand"><h1>ProjectControl</h1><p id="subtitle"></p></div>
<div class="topbar-actions"><span id="conn"></span><button id="btn-add"></button>
<button id="btn-templates"></button><button id="btn-settings"></button><button id="btn-stop-all"></button></div></header>
<main><section id="projects" class="projects"></section>
<div id="empty" class="empty hidden"><button data-action="add-project"></button></div></main>
<section id="dock" class="dock collapsed"><div class="dock-resizer" id="dock-resizer"></div>
<header class="dock-head"><div class="dock-tabs" id="dock-tabs"></div>
<div class="dock-tools"><span id="dock-meta"></span><input type="search" id="dock-search">
<label class="check"><input type="checkbox" id="dock-follow" checked></label>
<button id="dock-clear"></button><button id="dock-max"></button><button id="dock-close"></button></div></header>
<div class="dock-note hidden" id="dock-note"></div><div class="dock-body" id="dock-body"></div></section>
<div id="modal-root"></div>
<script>window.__STATE__=${stateJson};window.__QUERY__=new URLSearchParams(location.search).get("q")||"";</script>
<script src="/static/core.js"></script><script src="/static/logs.js"></script>
<script src="/static/cards.js"></script><script src="/static/editor.js"></script><script src="/static/app.js"></script>
<script>
state.projects = window.__STATE__.projects || [];
state.templates = window.__STATE__.templates || [];
state.settings = window.__STATE__.settings || {};
state.history = window.__STATE__.history || [];
renderProjects();
const first = state.projects[0];
const web = first.targets[0];
if (window.__QUERY__ === "__FORM__") {
  openProjectEditor(state.projects[0]);
  document.title = "READY";
} else {
  openDock(tabId(first.id, web.id));
  // ждём, пока лог загрузится с фикстуры, потом проверяем поиск и живую дописку
  setTimeout(() => {
    const buf = getBuf(tabId(first.id, web.id));
    appendEntry(first.id, {seq: 900, target_id: web.id, target_name: web.name,
      stream: "stdout", text: "HOT RELOAD: новая строка в реальном времени"});
    document.getElementById("dock-search").value = window.__QUERY__;
    buf.filterText = window.__QUERY__;
    renderLogBody();
    updateProjectLogBadges();
    document.title = "READY";
  }, 500);
}
</script></body></html>`;

  const firstId = state.projects[0] && state.projects[0].id;
  const webId = state.projects[0] && state.projects[0].targets[0].id;

  return http.createServer((req, res) => {
    const url = req.url.split("?")[0];
    if (url.startsWith("/static/")) {
      const file = path.join(STATIC, url.slice(8));
      if (fs.existsSync(file) && fs.statSync(file).isFile()) {
        const ext = path.extname(file);
        const types = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html" };
        res.writeHead(200, { "Content-Type": types[ext] || "text/plain" });
        res.end(fs.readFileSync(file));
        return;
      }
      res.writeHead(404); res.end(); return;
    }
    if (url.endsWith("/logs") && url.startsWith("/api/projects/")) {
      const entries = FAKE_LINES.map((l, i) => ({
        seq: i + 1, ts: Date.now() / 1000, target_id: webId, target_name: "web",
        stream: l.stream, text: l.text,
      }));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ project_id: firstId, entries, dropped: 3, stats: { kept: entries.length, maxlen: 2000, total: 3, dropped: 3 } }));
      return;
    }
    if (url.startsWith("/api/state")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(state));
      return;
    }
    if (url.startsWith("/api/")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(html);
  }).listen(FIXTURE_PORT);
}

/* ------------------------------------------------------------------ main */
(async function main() {
  const browserPath = BROWSERS.find((p) => fs.existsSync(p));
  if (!browserPath) { console.log("Браузер не найден — проверка фронтенда пропущена"); process.exit(0); }

  console.log("\n== 1. Живая страница (реальный сервер) ==");
  const server = spawn(path.join(ROOT, ".venv", "Scripts", "python.exe"),
    ["run.py", "--no-browser", "--port", String(PORT)], { cwd: ROOT, stdio: "ignore" });
  if (!await waitFor(BASE + "/api/health")) { server.kill(); console.log("Сервер не поднялся"); process.exit(1); }

  await post(BASE + "/api/projects", {
    name: "ConstructorSite", root: ROOT, kind: "django", interpreter: path.join(ROOT, ".venv"),
    targets: [
      { name: "ConstructorSite", kind: "web", command: ["python", "-c", "print('site ok')"] },
      { name: "worker", kind: "worker", command: ["celery", "-A", "src.settings.celery", "worker", "--pool=solo", "--loglevel=info"] },
      { name: "beat", kind: "beat", command: ["celery", "-A", "src.settings.celery", "beat", "--loglevel=info"] },
    ],
  });
  const projects = JSON.parse(await get(BASE + "/api/projects"));
  await post(BASE + "/api/projects/" + projects[0].id + "/start", {});
  await sleep(2500);

  const live = await dumpDom(browserPath, BASE + "/");
  check("страница отрисована", live.dom.length > 2000, live.dom.length);
  check("нет JS-ошибок", pageErrors(live.stderr).length === 0, pageErrors(live.stderr).slice(0, 2).join(" | "));
  const projectsZone = live.dom.slice(0, live.dom.indexOf('id="dock"'));
  check("карточки созданы", (projectsZone.match(/class="card"/g) || []).length >= 1,
    (projectsZone.match(/class="card"/g) || []).length);
  check("путь проекта в карточке", projectsZone.includes(ROOT) && projectsZone.includes("ConstructorSite"));
  check("док присутствует", live.dom.includes('id="dock-tabs"') && live.dom.includes('id="dock-resizer"'));
  check("старых инлайн-консолей нет", !live.dom.includes('class="log-bar"'));

  const css = await get(BASE + "/static/styles.css");
  check("CSS дока отдаётся", css.includes(".dock-tab") && css.includes(".dock-resizer") && css.includes(".dock-body"));
  check("док не даёт второй скролл", /main\s*\{[^}]*overflow-y:\s*auto/.test(css) && /body\s*\{[^}]*overflow:\s*hidden/.test(css));

  console.log("\n== 2. Фикстура с реальными данными ==");
  const state = JSON.parse(await get(BASE + "/api/state"));
  const fixture = startFixture(state);
  await sleep(400);

  const plain = await dumpDom(browserPath, `http://127.0.0.1:${FIXTURE_PORT}/`);
  const dom = plain.dom;
  const body = slice(dom, "dock-body", 4000);
  const meta = slice(dom, "dock-meta", 200);

  check("фикстура без JS-ошибок", pageErrors(plain.stderr).length === 0, pageErrors(plain.stderr).slice(0, 2).join(" | "));
  check("карточки отрисованы", (dom.match(/class="card"/g) || []).length === state.projects.length,
    `${(dom.match(/class="card"/g) || []).length} / ${state.projects.length}`);
  check("док открыт", dom.includes('id="dock" class="dock"'));
  const tabs = (dom.match(/class="dock-tab[ "]/g) || []).length;
  const targetCount = state.projects.reduce((n, p) => n + p.targets.length, 0);
  check("вкладка на каждый процесс", tabs === targetCount, `${tabs} / ${targetCount}`);
  check("название вкладки с проектом", dom.includes("ConstructorSite · worker"));
  check("активная вкладка подсвечена", dom.includes("dock-tab active"));
  check("точка статуса во вкладке", /class="dock-tab[^"]*"[\s\S]{0,160}?dot dot-/.test(slice(dom, "dock-tabs", 2500)));
  check("команда подсвечена как cmd", body.includes('class="log-line cmd"'), body.slice(0, 200));
  check("ошибки подсвечены", body.includes('class="log-line error"'), body.slice(0, 200));
  check("служебные строки отделены", body.includes('class="log-line meta"'), body.slice(0, 200));
  check("строка дописана в реальном времени", body.includes("HOT RELOAD"));
  check("счётчик строк и pid в шапке", /строк/.test(meta) && /pid \d+/.test(meta), meta);
  check("поиск пуст — строки на месте", body.includes("Django started") && body.includes("Traceback"));
  check("подсказка об удалённых строках", dom.includes("старые строки удалены"));

  const searched = await dumpDom(browserPath, `http://127.0.0.1:${FIXTURE_PORT}/?q=celery`);
  const sbody = slice(searched.dom, "dock-body", 4000);
  check("поиск нашёл своё", sbody.includes("celery@host"));
  check("поиск отсёк лишнее", !sbody.includes("Django started") && !sbody.includes("Traceback"), sbody.slice(0, 200));
  check("поиск отражён в статистике", slice(searched.dom, "dock-meta", 200).includes("поиск: celery"));
  const empty = await dumpDom(browserPath, `http://127.0.0.1:${FIXTURE_PORT}/?q=zzzzz`);
  check("пустой результат объяснён", slice(empty.dom, "dock-body", 400).includes("Ничего не найдено"));

  // форма редактора: venv, автозапуск, автоперезапуск
  const formPage = await dumpDom(browserPath, `http://127.0.0.1:${FIXTURE_PORT}/?q=__FORM__`);
  check("форма проекта открывается без ошибок", pageErrors(formPage.stderr).length === 0,
    pageErrors(formPage.stderr).slice(0, 2).join(" | "));
  check("в форме есть поле .venv", formPage.dom.includes('id="f-interpreter"'));
  check("в форме есть галочка автозапуска", formPage.dom.includes('id="f-autostart"'));
  check("в форме есть автоперезапуск и интервал", formPage.dom.includes('id="f-autorestart"') &&
    formPage.dom.includes('id="f-restart-delay"') && formPage.dom.includes('value="10"'));
  check("в форме есть галочки celery", formPage.dom.includes('id="f-worker-on"') && formPage.dom.includes('id="f-beat-on"'));

  const sources = ["core.js", "logs.js", "cards.js", "editor.js", "app.js"]
    .map((f) => fs.readFileSync(path.join(STATIC, f), "utf8")).join("\n");
  const acts = new Set(Array.from(dom.matchAll(/data-act="([a-z-]+)"/g)).map((m) => m[1]));
  for (const act of acts) check("обработчик для " + act, sources.includes('"' + act + '"'));

  for (const f of ["core.js", "logs.js", "cards.js", "editor.js", "app.js"]) {
    const res = spawnSync(process.execPath, ["--check", path.join(STATIC, f)], { encoding: "utf8" });
    check("синтаксис " + f, res.status === 0, (res.stderr || "").split("\n").slice(0, 5).join(" "));
  }

  fixture.close();
  await sleep(200);
  try { await post(BASE + "/api/stop-all", {}); } catch (e) { /* сервер уже мог завершиться */ }
  await sleep(400);
  server.kill();

  console.log("\nИТОГ:", failed === 0 ? "ВСЁ ОК" : failed + " ОШИБОК");
  process.exit(failed === 0 ? 0 : 1);
})().catch((err) => { console.log("ОШИБКА СКРИПТА:", err.message); process.exit(1); });
