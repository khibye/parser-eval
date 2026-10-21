/* parser-eval UI: shared helpers, sign-in, layout and routing. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const qs = (obj) => Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== "")
  .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");

const App = { poll: null, views: {}, settings: null, storageInfo: null };

class ApiError extends Error {}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", credentials: "same-origin", headers: {} };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  if (opts.form) init.body = opts.form;
  const resp = await fetch("/api" + path, init);
  if (resp.status === 401 && !opts.noAuthRedirect) { showLogin(); throw new ApiError("Sign in to continue."); }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try { const j = await resp.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { /* not json */ }
    throw new ApiError(detail);
  }
  if (opts.raw) return resp;
  const type = resp.headers.get("content-type") || "";
  return type.includes("application/json") ? resp.json() : resp.text();
}

function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), bad ? 7000 : 3500);
}

function fail(err) { toast(err && err.message ? err.message : String(err), true); }

function modal({ title, body = "", ok = "OK", cancel = "Cancel", danger = false, input = null }) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="m-title">
      <h3 id="m-title">${esc(title)}</h3>
      ${body ? `<div class="muted">${body}</div>` : ""}
      ${input ? `<div class="field"><label for="m-input">${esc(input.label)}</label>
        <input id="m-input" type="text" value="${esc(input.value || "")}" class="${input.mono ? "mono" : ""}"></div>` : ""}
      <div class="row" style="justify-content:flex-end">
        ${cancel ? `<button class="btn" data-a="no">${esc(cancel)}</button>` : ""}
        <button class="btn ${danger ? "danger" : "primary"}" data-a="yes">${esc(ok)}</button></div></div>`;
    const done = (v) => { back.remove(); resolve(v); };
    back.addEventListener("click", (e) => {
      if (e.target === back) done(input ? null : false);
      const a = e.target.closest("[data-a]");
      if (!a) return;
      if (a.dataset.a === "yes") done(input ? $("#m-input", back).value : true);
      else done(input ? null : false);
    });
    back.addEventListener("keydown", (e) => {
      if (e.key === "Escape") done(input ? null : false);
      if (e.key === "Enter" && input) done($("#m-input", back).value);
    });
    document.body.appendChild(back);
    ($("#m-input", back) || $("[data-a=yes]", back)).focus();
  });
}

/* ---------- formatting ---------- */

function fmtWhen(ts) {
  if (!ts) return "–";
  const d = new Date(ts * 1000), now = new Date();
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((day - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (diff === 0) return `Today, ${time}`;
  if (diff === 1) return `Yesterday, ${time}`;
  return `${d.getDate()} ${d.toLocaleString("en-GB", { month: "short" })}, ${time}`;
}
function fmtDur(s) {
  if (s === null || s === undefined || isNaN(s)) return "–";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  if (m < 60) return `${m}m ${String(r).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
function fmtSec(s) { return s === null || s === undefined ? "–" : `${Number(s).toFixed(s < 10 ? 1 : 0)} s`; }
function fmtBytes(n) {
  if (n === null || n === undefined) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}
function pct(a, total) { return total ? `${(a / total * 100).toFixed(1)}%` : "–"; }
function basename(key) { return String(key || "").split("/").pop(); }
function signed(n) { return n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : "0"; }

/* ---------- routing ---------- */

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/runs";
  const [path, query] = raw.split("?");
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  return { parts: path.split("/").filter(Boolean), params, path };
}

function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

function setParam(key, value) {
  const { path, params } = parseHash();
  if (value === null || value === undefined || value === "") delete params[key]; else params[key] = value;
  const q = qs(params);
  history.replaceState(null, "", "#" + path + (q ? "?" + q : ""));
}

async function route() {
  if (App.poll) { clearInterval(App.poll); App.poll = null; }
  const { parts, params } = parseHash();
  const main = $("main.view");
  if (!main) return;
  const section = parts[0] || "runs";
  $$(".side nav a").forEach((a) => a.classList.toggle("on", a.dataset.section === (section === "tests" ? "files" : section)));
  main.onclick = main.ondragover = main.ondragleave = main.ondrop = main.onmouseup = null;
  main.innerHTML = `<div class="empty"><span class="spin"></span> Loading</div>`;
  window.scrollTo(0, 0);
  try {
    if (section === "runs" && parts[1] && parts[2] === "file") await App.views.fileDetail(main, parts[1], parts[3], params);
    else if (section === "runs" && parts[1]) await App.views.run(main, parts[1], params);
    else if (section === "runs") await App.views.runs(main, params);
    else if (section === "new") await App.views.newRun(main, params);
    else if (section === "files") await App.views.files(main, params);
    else if (section === "tests") await App.views.tests(main, params);
    else if (section === "parsers") await App.views.parsers(main, params);
    else if (section === "vlm") await App.views.vlm(main, params);
    else if (section === "settings") await App.views.settings(main, params);
    else main.innerHTML = `<div class="empty">This page does not exist. <a href="#/runs">Go to runs</a>.</div>`;
  } catch (err) {
    if (err instanceof ApiError && err.message === "Sign in to continue.") return;
    main.innerHTML = `<div class="note bad">${esc(err.message || err)}</div>`;
  }
}

function startPoll(fn, ms = 2500) {
  if (App.poll) clearInterval(App.poll);
  App.poll = setInterval(() => { fn().catch(() => {}); }, ms);
}

/* ---------- shell ---------- */

const NAV = [
  ["runs", "Runs"], ["new", "New run"], ["files", "Files & tests"], ["parsers", "Parsers"],
  ["vlm", "VLM quality"], ["settings", "Settings"],
];

async function refreshFoot() {
  try {
    if (!App.settings) App.settings = await api("/settings");
    const st = await api("/storage");
    App.storageInfo = st;
    const bucket = App.settings.s3.bucket || "not set";
    $("#foot-s3").textContent = `S3 · ${bucket}`;
    $("#foot-pvc").textContent = `PVC /data · ${fmtBytes(st.used)} used`;
  } catch (e) { /* the footer is informational */ }
}

function renderShell() {
  $("#root").innerHTML = `<div class="app">
    <aside class="side">
      <div class="brand"><b>parser-eval</b><span>docintel test bench</span></div>
      <nav aria-label="Main">${NAV.map(([k, l]) => `<a href="#/${k}" data-section="${k}">${l}</a>`).join("")}</nav>
      <div class="foot"><div class="stack" style="gap:4px"><span class="mono" id="foot-s3">S3</span>
        <span class="mono" id="foot-pvc">PVC /data</span></div>
        <button type="button" id="signout">Sign out</button></div>
    </aside>
    <main class="view"></main></div>`;
  $("#signout").onclick = async () => { await api("/logout", { method: "POST" }); showLogin(); };
  refreshFoot();
}

function showLogin() {
  if (App.poll) { clearInterval(App.poll); App.poll = null; }
  $("#root").innerHTML = `<div class="login"><form id="login-form">
    <div class="brand"><b style="color:var(--ink);font-size:18px">parser-eval</b><span class="muted">docintel test bench</span></div>
    <div class="field"><label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" required></div>
    <label class="inline"><input id="remember" type="checkbox" checked> Keep me signed in</label>
    <button class="btn primary" type="submit" style="justify-content:center">Sign in</button>
    <div id="login-msg" class="note bad hidden"></div>
    <p class="small muted" style="margin:0">One shared password for this tool. Change it in Settings, under Access.</p>
  </form></div>`;
  $("#pw").focus();
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const btn = $("#login-form button[type=submit]");
    btn.disabled = true;
    try {
      await api("/login", { method: "POST", body: { password: $("#pw").value, remember: $("#remember").checked }, noAuthRedirect: true });
      App.settings = null;
      renderShell();
      route();
    } catch (err) {
      $("#login-msg").textContent = err.message;
      $("#login-msg").classList.remove("hidden");
      btn.disabled = false;
    }
  };
}

async function boot() {
  const st = await api("/status", { noAuthRedirect: true });
  if (!st.signed_in) { showLogin(); return; }
  renderShell();
  route();
}

window.addEventListener("hashchange", () => { if ($("main.view")) route(); });
document.addEventListener("DOMContentLoaded", () => boot().catch(fail));

/* ---------- small shared pieces ---------- */

function tabsHtml(items, current, attr = "tab") {
  return items.map(([k, l]) => `<button type="button" class="tab ${k === current ? "on" : ""}" role="tab"
    aria-selected="${k === current}" data-${attr}="${esc(k)}">${l}</button>`).join("");
}

function statusPill(run) {
  const s = run.status;
  if (s === "running") {
    const p = run.progress || {};
    const share = p.total ? Math.round(p.done / p.total * 100) : 0;
    const label = p.phase === "judging" ? `Judging · ${p.judge_done}/${p.judge_total}` : `Running · ${share}% · ${p.done} of ${p.total}`;
    return `<div class="stack" style="gap:6px"><span class="small" style="color:var(--accent);font-weight:500">${label}</span>
      <div class="bar"><i style="width:${p.phase === "judging" && p.judge_total ? Math.round(p.judge_done / p.judge_total * 100) : share}%"></i></div></div>`;
  }
  if (s === "queued") return `<span class="pill blue">Queued</span>`;
  if (s === "completed") return `<span class="pill">Completed</span>`;
  if (s === "interrupted") return `<span class="pill warn">Interrupted at ${run.progress?.done ?? 0} of ${run.progress?.total ?? 0}</span>`;
  if (s === "cancelled") return `<span class="pill warn">Cancelled</span>`;
  if (s === "failed") return `<span class="pill bad" title="${esc(run.error || "")}">Failed</span>`;
  return `<span class="pill">${esc(s)}</span>`;
}

function parserNames(run) {
  const ps = run.parsers || [];
  if (!ps.length) return "";
  return esc(ps[0].name) + (ps.length > 1 ? " → " + ps.slice(1).map((p) => esc(p.name)).join(", ") : "");
}

function headlineHtml(run) {
  const h = run.headline;
  if (!h) return `<span class="muted">–</span>`;
  if (h.improved !== undefined) return `<span class="tag" style="background:var(--good-bg);color:var(--good)" title="Files improved">▲ ${h.improved}</span>
    <span class="tag" style="background:var(--bad-bg);color:var(--bad)" title="Files regressed">▼ ${h.regressed}</span>`;
  return `<span class="small">${h.passed} / ${h.evaluated} checks</span>`;
}

function checkMark(passed) {
  if (passed === true) return `<span class="mark good" aria-label="pass" title="pass">✓</span>`;
  if (passed === false) return `<span class="mark bad" aria-label="fail" title="fail">✕</span>`;
  return `<span class="mark muted" aria-label="not evaluated" title="not evaluated">–</span>`;
}

function textDir(text) {
  return /[֐-׿؀-ۿ]/.test(String(text || "").slice(0, 400)) ? "rtl" : "ltr";
}
