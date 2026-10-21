/* Runs list, new run, run results and file detail. */
"use strict";

/* ---------- Runs ---------- */

App.views.runs = async function (main, params) {
  let filter = params.status || "all";
  let search = params.q || "";
  let data = await api("/runs");

  const groups = (runs) => ({
    all: runs.length,
    running: runs.filter((r) => ["running", "queued"].includes(r.status)).length,
    completed: runs.filter((r) => r.status === "completed").length,
    interrupted: runs.filter((r) => ["interrupted", "cancelled", "failed"].includes(r.status)).length,
  });
  const matches = (r) => {
    if (filter === "running" && !["running", "queued"].includes(r.status)) return false;
    if (filter === "completed" && r.status !== "completed") return false;
    if (filter === "interrupted" && !["interrupted", "cancelled", "failed"].includes(r.status)) return false;
    if (search) {
      const hay = [r.name, r.id, r.dataset, ...(r.parsers || []).map((p) => p.name)].join(" ").toLowerCase();
      if (!hay.includes(search.toLowerCase())) return false;
    }
    return true;
  };
  const action = (r) => ({ running: "View", queued: "View", interrupted: "Resume", cancelled: "Resume", failed: "Open" }[r.status]
    || ((r.parsers || []).length > 1 ? "Compare" : "Open"));

  function rows() {
    const runs = data.runs.filter(matches);
    if (!data.runs.length) return `<tr><td colspan="9" class="empty">No runs yet. <a href="#/new">Start the first run</a>.</td></tr>`;
    if (!runs.length) return `<tr><td colspan="9" class="empty">No runs match.</td></tr>`;
    return runs.map((r) => `<tr class="click" data-id="${esc(r.id)}">
      <td><div style="font-weight:500">${esc(r.name)}</div><div class="mono small muted">${esc(r.id)}</div></td>
      <td class="mono small">${esc(r.dataset)}</td>
      <td class="mono small">${parserNames(r)}</td>
      <td class="r">${r.file_count ?? "–"}</td>
      <td style="min-width:180px">${statusPill(r)}</td>
      <td>${headlineHtml(r)}</td>
      <td class="small">${fmtWhen(r.started || r.created)}</td>
      <td class="r small">${r.finished && r.started ? fmtDur(r.finished - r.started) : "–"}</td>
      <td class="r"><button class="btn sm" data-act="${esc(r.id)}" data-kind="${action(r)}">${action(r)}</button></td></tr>`).join("");
  }

  function render() {
    const g = groups(data.runs);
    main.innerHTML = `
      <div class="head"><div class="t"><h1>Runs</h1>
        <p>Each run sends every file in a dataset to the selected parsers and keeps all outputs on the PVC, so any run can be compared again later.</p></div>
        <a class="btn primary" href="#/new">＋ New run</a></div>
      <div class="row between">
        <div class="row"><label class="sr" for="run-search">Search runs</label>
          <input id="run-search" type="search" placeholder="Search by run, parser or dataset" value="${esc(search)}" style="width:320px;max-width:100%">
          <div class="row" role="group" aria-label="Filter by status">
            ${[["all", "All"], ["running", "Running"], ["completed", "Completed"], ["interrupted", "Stopped"]]
              .map(([k, l]) => `<button class="chip ${filter === k ? "on" : ""}" data-filter="${k}" aria-pressed="${filter === k}">${l} · ${g[k]}</button>`).join("")}
          </div></div>
        ${data.queued ? `<span class="small muted">${data.queued} queued</span>` : ""}</div>
      <section class="card"><div class="tbl-wrap tall"><table class="tbl">
        <thead><tr><th>Run</th><th>Dataset</th><th>Parsers · baseline → candidates</th><th class="r">Files</th><th>Status</th>
        <th>Result</th><th>Started</th><th class="r">Duration</th><th></th></tr></thead>
        <tbody id="run-rows">${rows()}</tbody></table></div></section>
      <p class="small muted" style="margin:0">▲ files where the candidate passed more checks than the baseline · ▼ files where it passed fewer.</p>`;
    $("#run-search").oninput = (e) => { search = e.target.value; setParam("q", search); $("#run-rows").innerHTML = rows(); };
    $$("[data-filter]", main).forEach((b) => b.onclick = () => { filter = b.dataset.filter; setParam("status", filter === "all" ? "" : filter); render(); });
  }

  main.onclick = async (e) => {
    const act = e.target.closest("[data-act]");
    if (act) {
      e.stopPropagation();
      if (act.dataset.kind === "Resume") {
        try { await api(`/runs/${act.dataset.act}/resume`, { method: "POST" }); toast("Run queued again."); data = await api("/runs"); render(); }
        catch (err) { fail(err); }
      } else go(`#/runs/${act.dataset.act}`);
      return;
    }
    const tr = e.target.closest("tr[data-id]");
    if (tr) go(`#/runs/${tr.dataset.id}`);
  };
  render();
  startPoll(async () => {
    if (!data.runs.some((r) => ["running", "queued"].includes(r.status))) return;
    data = await api("/runs");
    if ($("#run-rows")) $("#run-rows").innerHTML = rows();
  }, 3000);
};

/* ---------- New run ---------- */

App.views.newRun = async function (main, params) {
  const [datasets, parsers, presets, settings] = await Promise.all([
    api("/datasets").catch((e) => ({ error: e.message })), api("/parsers"), api("/presets"), api("/settings")]);
  App.settings = settings;
  if (datasets.error) {
    main.innerHTML = `<div class="head"><div class="t"><h1>New run</h1></div></div>
      <div class="note bad">${esc(datasets.error)} <a href="#/settings?tab=s3">Open settings</a></div>`;
    return;
  }
  if (!parsers.length) {
    main.innerHTML = `<div class="head"><div class="t"><h1>New run</h1></div></div>
      <div class="note info">Add a parser first. <a href="#/parsers?id=new">Add parser</a></div>`;
    return;
  }
  const cfg = {
    name: "", dataset: datasets[0]?.prefix || "", types: [], only_tested: false,
    baseline: parsers[0].id, candidates: parsers.slice(1, 2).map((p) => p.id),
    vlm_mode: "replay", judge: { qa: true, pairwise: true, threshold: settings.eval.change_threshold, images: false },
    concurrency: 8, timeout: 600, repeat: 1,
  };
  if (params.from) {
    try {
      const r = await api(`/runs/${params.from}`);
      Object.assign(cfg, { name: r.name + " (again)", dataset: r.dataset, types: r.types, only_tested: r.only_tested,
        baseline: r.parsers[0].id, candidates: r.parsers.slice(1).map((p) => p.id), vlm_mode: r.vlm_mode, judge: r.judge,
        concurrency: r.concurrency, timeout: r.timeout, repeat: r.repeat });
    } catch (e) { /* run gone; use defaults */ }
  }
  let health = {};
  let estimate = null;
  let estTimer = null;

  const dsInfo = () => datasets.find((d) => d.prefix === cfg.dataset) || datasets[0];
  const typeList = () => Object.entries(dsInfo()?.types || {}).sort((a, b) => b[1] - a[1]);

  function parserRows() {
    return parsers.map((p) => {
      const h = health[p.id];
      const hs = !h ? `<span class="small muted">checking…</span>` : h.ok ? `<span class="small good dot">${h.ms} ms</span>`
        : `<span class="small bad dot" title="${esc(h.error || "")}">Not reachable</span>`;
      return `<tr><td class="c"><input type="radio" name="baseline" value="${esc(p.id)}" ${cfg.baseline === p.id ? "checked" : ""} aria-label="Use ${esc(p.name)} as baseline" style="accent-color:var(--accent);width:17px;height:17px"></td>
        <td class="c"><input type="checkbox" data-cand="${esc(p.id)}" ${cfg.candidates.includes(p.id) && cfg.baseline !== p.id ? "checked" : ""} ${cfg.baseline === p.id ? "disabled" : ""} aria-label="Compare ${esc(p.name)}" style="accent-color:var(--accent);width:17px;height:17px"></td>
        <td><div class="mono" style="font-weight:500;font-size:13px">${esc(p.name)}</div><div class="small muted">${esc(p.description || p.url)}</div></td>
        <td class="r">${hs}</td></tr>`;
    }).join("");
  }

  function estimateHtml() {
    const down = parsers.filter((p) => (p.id === cfg.baseline || cfg.candidates.includes(p.id)) && health[p.id] && !health[p.id].ok);
    if (!estimate) return `<span class="muted"><span class="spin"></span> Counting files</span>`;
    return `<div class="kv num">
      <span><span class="muted">Files</span> <b>${estimate.files}</b></span>
      <span><span class="muted">Parsers</span> <b>${estimate.parsers}</b></span>
      <span><span class="muted">Requests</span> <b>${estimate.requests * cfg.repeat}</b></span>
      <span><span class="muted">Checks</span> <b>${estimate.checks}</b> <span class="muted">on ${estimate.files_with_checks} files</span></span>
      ${estimate.last_similar_seconds ? `<span><span class="muted">Last run on this folder</span> <b>${fmtDur(estimate.last_similar_seconds)}</b></span>` : ""}
      ${down.length ? `<span class="warn">${down.map((p) => esc(p.name)).join(", ")} not reachable</span>` : ""}</div>`;
  }

  function scheduleEstimate() {
    clearTimeout(estTimer);
    estTimer = setTimeout(async () => {
      try { estimate = await api("/runs/estimate", { method: "POST", body: cfg }); } catch (e) { estimate = null; }
      if ($("#estimate")) $("#estimate").innerHTML = estimateHtml();
    }, 350);
  }

  function render() {
    const vision = App.settings.judge.vision;
    main.innerHTML = `
      <div class="head"><div class="t"><a class="back" href="#/runs">← Runs</a><h1>New run</h1></div>
        <div class="row" style="align-items:flex-end">
          ${presets.length ? `<div class="field"><label for="preset">Preset</label><select id="preset"><option value="">Choose a preset</option>
            ${presets.map((p) => `<option>${esc(p.name)}</option>`).join("")}</select></div>` : ""}
          <div class="field" style="width:340px;max-width:100%"><label for="run-name">Run name</label>
          <input id="run-name" type="text" value="${esc(cfg.name)}" placeholder="for example: xberg 1.3.10 vs 1.0.14"></div></div></div>
      <div class="grid2">
        <div class="stack" style="gap:16px">
          <section class="card pad"><div class="row between"><h2>Files</h2><a class="small" href="#/files">Manage files</a></div>
            <div class="field"><label for="dataset">Dataset (S3 folder)</label><select id="dataset" class="mono">
              ${datasets.map((d) => `<option value="${esc(d.prefix)}" ${d.prefix === cfg.dataset ? "selected" : ""}>s3://${esc(App.settings.s3.bucket)}/${esc(d.prefix)} · ${d.count} files</option>`).join("")}
            </select></div>
            <div class="row" role="group" aria-label="File types">
              ${typeList().map(([t, n]) => `<label class="check-chip"><input type="checkbox" data-type="${esc(t)}" ${!cfg.types.length || cfg.types.includes(t) ? "checked" : ""}>
                <span class="mono small">${esc(t)}</span><span class="small muted">${n}</span></label>`).join("") || `<span class="muted small">This folder is empty.</span>`}
            </div>
            <label class="inline small"><input id="only-tested" type="checkbox" ${cfg.only_tested ? "checked" : ""}> Only files that have approved checks</label>
          </section>
          <section class="card"><div class="ch"><h2>Parsers</h2><a class="small" href="#/parsers">Edit parsers</a></div>
            <table class="tbl"><thead><tr><th class="c">Baseline</th><th class="c">Compare</th><th>Parser</th><th class="r">Health</th></tr></thead>
            <tbody id="parser-rows">${parserRows()}</tbody></table></section>
        </div>
        <div class="stack" style="gap:16px">
          <section class="card pad"><h2>VLM answers</h2>
            <div class="radio-list" role="radiogroup" aria-label="VLM answers">
              ${[["replay", "Replay from cache", "same answer for the same image, so only the parser differs"],
                 ["live", "Live", "every image goes to the VLM, answers can differ between runs"],
                 ["refresh", "Refresh cache", "call the VLM again and replace the stored answers"]].map(([k, l, d]) =>
                `<label><input type="radio" name="vlm" value="${k}" ${cfg.vlm_mode === k ? "checked" : ""}><span><b style="font-weight:500">${l}</b> <span class="small muted">· ${d}</span></span></label>`).join("")}
            </div>
            <p class="small muted" style="margin:0">Works for parsers whose docintel sends its VLM calls to <span class="mono">${esc(App.settings.proxy_url)}</span>.</p>
          </section>
          <section class="card pad"><div class="row between"><h2>Judge</h2><span class="mono small muted">${esc(App.settings.judge.model || "not set")}</span></div>
            <label class="inline"><input id="j-qa" type="checkbox" ${cfg.judge.qa ? "checked" : ""}> Answer question checks</label>
            <div class="row"><input id="j-pair" type="checkbox" ${cfg.judge.pairwise ? "checked" : ""} style="margin:0;accent-color:var(--accent)">
              <label for="j-pair">Pick the better output when the text changed by more than</label>
              <label class="sr" for="j-thr">Threshold in percent</label>
              <input id="j-thr" type="number" min="0" max="100" value="${cfg.judge.threshold}" style="width:70px"> %</div>
            <label class="inline"><input id="j-img" type="checkbox" ${cfg.judge.images ? "checked" : ""} ${vision ? "" : "disabled"}>
              Show the judge the original pages <span class="small muted">· ${vision ? "PDFs and images, slower" : "the judge model cannot see images"}</span></label>
          </section>
          <section class="card pad"><h2>Speed</h2><div class="fields" style="grid-template-columns:repeat(3,minmax(0,1fr))">
            <div class="field"><label for="conc">Parallel per parser</label><input id="conc" type="number" min="1" value="${cfg.concurrency}"></div>
            <div class="field"><label for="timeout">Timeout (s)</label><input id="timeout" type="number" min="5" value="${cfg.timeout}"></div>
            <div class="field"><label for="repeat">Repeat each file</label><input id="repeat" type="number" min="1" max="5" value="${cfg.repeat}"></div></div>
            <p class="small muted" style="margin:0">Each parser also has its own limit, set on the Parsers page. Repeating gives steadier timings.</p>
          </section>
        </div>
      </div>
      <div class="sticky-foot"><div id="estimate">${estimateHtml()}</div>
        <div class="row"><button class="btn" id="save-preset">Save as preset</button><button class="btn primary" id="start">Start run</button></div></div>`;
    bind();
  }

  function readTypes() {
    const boxes = $$("[data-type]", main);
    const on = boxes.filter((b) => b.checked).map((b) => b.dataset.type);
    cfg.types = on.length === boxes.length ? [] : on;
  }

  function bind() {
    $("#run-name").oninput = (e) => { cfg.name = e.target.value; };
    $("#dataset").onchange = (e) => { cfg.dataset = e.target.value; cfg.types = []; render(); scheduleEstimate(); };
    $$("[data-type]", main).forEach((b) => b.onchange = () => { readTypes(); scheduleEstimate(); });
    $("#only-tested").onchange = (e) => { cfg.only_tested = e.target.checked; scheduleEstimate(); };
    bindParsers();
    $$("input[name=vlm]", main).forEach((r) => r.onchange = () => { cfg.vlm_mode = r.value; });
    $("#j-qa").onchange = (e) => { cfg.judge.qa = e.target.checked; };
    $("#j-pair").onchange = (e) => { cfg.judge.pairwise = e.target.checked; };
    $("#j-thr").oninput = (e) => { cfg.judge.threshold = Number(e.target.value) || 0; };
    $("#j-img").onchange = (e) => { cfg.judge.images = e.target.checked; };
    $("#conc").oninput = (e) => { cfg.concurrency = Number(e.target.value) || 1; };
    $("#timeout").oninput = (e) => { cfg.timeout = Number(e.target.value) || 600; };
    $("#repeat").oninput = (e) => { cfg.repeat = Math.max(1, Math.min(5, Number(e.target.value) || 1)); $("#estimate").innerHTML = estimateHtml(); };
    if ($("#preset")) $("#preset").onchange = (e) => {
      const p = presets.find((x) => x.name === e.target.value);
      if (p) { Object.assign(cfg, JSON.parse(JSON.stringify(p.config))); render(); scheduleEstimate(); }
    };
    $("#save-preset").onclick = async () => {
      const name = await modal({ title: "Save as preset", ok: "Save", input: { label: "Preset name", value: cfg.name || "" } });
      if (!name) return;
      try { const items = await api("/presets", { method: "POST", body: { name, config: cfg } }); presets.splice(0, presets.length, ...items); toast("Preset saved."); render(); }
      catch (err) { fail(err); }
    };
    $("#start").onclick = async () => {
      const btn = $("#start");
      btn.disabled = true;
      try {
        const res = await api("/runs", { method: "POST", body: cfg });
        go(`#/runs/${res.id}`);
      } catch (err) { fail(err); btn.disabled = false; }
    };
  }
  function bindParsers() {
    $$("[data-cand]", main).forEach((c) => c.onchange = () => {
      const id = c.dataset.cand;
      cfg.candidates = c.checked ? [...cfg.candidates.filter((x) => x !== id), id] : cfg.candidates.filter((x) => x !== id);
      scheduleEstimate();
    });
    $$("input[name=baseline]", main).forEach((r) => r.onchange = () => {
      cfg.baseline = r.value; cfg.candidates = cfg.candidates.filter((c) => c !== r.value);
      $("#parser-rows").innerHTML = parserRows(); bindParsers(); scheduleEstimate();
    });
  }

  render();
  scheduleEstimate();
  api("/parsers/health").then((h) => {
    health = h;
    if ($("#parser-rows")) { $("#parser-rows").innerHTML = parserRows(); bindParsers(); }
    if ($("#estimate")) $("#estimate").innerHTML = estimateHtml();
  }).catch(() => {});
};

/* ---------- One run: progress and comparison ---------- */

App.views.run = async function (main, runId, params) {
  let run = await api(`/runs/${runId}`);
  const pids = run.parsers.map((p) => p.id);
  const names = Object.fromEntries(run.parsers.map((p) => [p.id, p.name]));
  let a = pids.includes(params.a) ? params.a : pids[0];
  let b = pids.includes(params.b) ? params.b : pids[1];
  let tab = params.tab || "types";
  let search = "";
  let data = null;

  async function load() {
    run = await api(`/runs/${runId}`);
    if (b) data = await api(`/runs/${runId}/compare?${qs({ a, b })}`);
    else data = await api(`/runs/${runId}/single?${qs({ pid: a })}`);
  }

  function progressCard() {
    if (!["running", "queued", "interrupted", "cancelled", "failed"].includes(run.status)) return "";
    const p = run.progress;
    const share = p.total ? Math.round(p.done / p.total * 100) : 0;
    let text = "";
    if (run.status === "queued") text = "Waiting for the run before it to finish.";
    else if (run.status === "running" && p.phase === "judging") text = `All files parsed. The judge is comparing changed files: ${p.judge_done} of ${p.judge_total}.`;
    else if (run.status === "running") text = `Parsing: ${p.done} of ${p.total} requests done.`;
    else if (run.status === "interrupted") text = `Stopped at ${p.done} of ${p.total} when the service restarted. Resume continues from there.`;
    else if (run.status === "cancelled") text = `Cancelled at ${p.done} of ${p.total}. Resume continues from there.`;
    else text = `The run failed: ${esc(run.error || "unknown error")}`;
    const active = ["running", "queued"].includes(run.status);
    return `<section class="card pad" style="gap:10px"><div class="row between"><span>${text}</span>
      <div class="row">${active ? `<button class="btn sm danger" id="cancel">Cancel run</button>` : `<button class="btn sm primary" id="resume">Resume</button>`}</div></div>
      ${active ? `<div class="bar"><i style="width:${p.phase === "judging" && p.judge_total ? Math.round(p.judge_done / p.judge_total * 100) : share}%"></i></div>` : ""}</section>`;
  }

  function header() {
    const vs = run.vlm_stats;
    const vlmText = vs && vs.calls ? `VLM ${Math.round(vs.hits / vs.calls * 100)}% from cache` : `VLM ${run.vlm_mode}`;
    const opt = (sel) => pids.map((id) => `<option value="${esc(id)}" ${id === sel ? "selected" : ""}>${esc(names[id])}</option>`).join("");
    return `<div class="head"><div class="t"><a class="back" href="#/runs">← Runs</a><h1>${esc(run.name)}</h1>
      <div class="row">
        ${b ? `<label class="sr" for="sel-a">Baseline A</label><span class="tag">A <select id="sel-a" class="mono" style="width:auto;padding:2px 6px;border:0;background:transparent">${opt(a)}</select></span>
        <label class="sr" for="sel-b">Candidate B</label><span class="tag blue">B <select id="sel-b" class="mono" style="width:auto;padding:2px 6px;border:0;background:transparent">${opt(b)}</select></span>`
        : `<span class="tag mono">${esc(names[a])}</span>`}
        <span class="small muted mono">${esc(run.id)} · ${esc(run.dataset)} · ${run.files.length} files · ${fmtWhen(run.started || run.created)} · ${vlmText}</span></div></div>
      <div class="row"><button class="btn danger" id="del-run">Delete</button><a class="btn" href="#/new?from=${esc(run.id)}">Run again</a>
        ${b ? `<a class="btn primary" href="/api/runs/${esc(run.id)}/report?${qs({ a, b })}" download>Download report</a>` : ""}</div></div>`;
  }

  function tilesCompare() {
    const c = data.checks, n = data.counts, t = data.time, e = data.errors, j = data.judge;
    const d = c.b - c.a;
    return `<section class="tiles" aria-label="Summary">
      <div class="tile"><span class="l">Checks passed</span><span class="v">${pct(c.b, c.total)}</span>
        <span class="s ${d > 0 ? "good" : d < 0 ? "bad" : ""}">${d > 0 ? "▲" : d < 0 ? "▼" : ""} ${signed(d)} · A had ${pct(c.a, c.total)}</span></div>
      <div class="tile"><span class="l">Files</span><span class="v"><span class="good">▲ ${n.improved}</span> <span class="bad">▼ ${n.regressed}</span></span>
        <span class="s">${n.unchanged} unchanged${n.pending ? ` · ${n.pending} pending` : ""}</span></div>
      <div class="tile warn"><span class="l">Needs review</span><span class="v">${n.needs_review}</span><span class="s">changed &gt;${data.threshold}%, same checks</span></div>
      <div class="tile"><span class="l">Judge picks</span><span class="v">B ${j.B} · A ${j.A}</span><span class="s">${j.tie} ties${j.disagree ? ` · ${j.disagree} disagree` : ""} · ${j.total} judged</span></div>
      <div class="tile"><span class="l">Median time</span><span class="v">${fmtSec(t.b_median)}</span><span class="s">A ${fmtSec(t.a_median)} · p95 ${fmtSec(t.b_p95)} vs ${fmtSec(t.a_p95)}</span></div>
      <div class="tile"><span class="l">Errors</span><span class="v">${e.b}</span><span class="s">A had ${e.a}</span></div></section>`;
  }

  function typesTable() {
    if (!data.by_type.length) return `<div class="empty">No results yet.</div>`;
    return `<table class="tbl"><thead><tr><th>Type</th><th class="r">Files</th><th class="r">Checks A</th><th class="r">Checks B</th><th class="r">Δ</th>
      <th class="r">▲</th><th class="r">▼</th><th class="r">Median time A → B</th><th class="r">Errors A → B</th></tr></thead><tbody>
      ${data.by_type.map((r) => { const d = r.b - r.a; return `<tr><td style="font-weight:500">${esc(r.type)}</td><td class="r">${r.files}</td>
        <td class="r">${r.a} / ${r.total}</td><td class="r">${r.b} / ${r.total}</td>
        <td class="r ${d > 0 ? "good" : d < 0 ? "bad" : "muted"}" style="font-weight:500">${signed(d)}</td>
        <td class="r good">${r.up || "–"}</td><td class="r bad">${r.down || "–"}</td>
        <td class="r">${fmtSec(r.a_median)} → ${fmtSec(r.b_median)}</td><td class="r">${r.a_err} → ${r.b_err}</td></tr>`; }).join("")}</tbody></table>`;
  }

  function listRows(kind) {
    let rows = data.rows;
    if (kind === "reg") rows = rows.filter((r) => r.status === "regressed");
    if (kind === "review") rows = rows.filter((r) => r.needs_review);
    if (kind === "imp") rows = rows.filter((r) => r.status === "improved");
    if (search) rows = rows.filter((r) => r.key.toLowerCase().includes(search.toLowerCase()));
    if (!rows.length) return `<tr><td colspan="6" class="empty">Nothing here.</td></tr>`;
    return rows.map((r) => {
      const st = r.missing ? `<span class="pill">pending</span>` : r.needs_review ? `<span class="pill warn">review</span>`
        : r.status === "improved" ? `<span class="pill good">▲ improved</span>` : r.status === "regressed" ? `<span class="pill bad">▼ regressed</span>` : `<span class="pill">same</span>`;
      return `<tr class="click" data-fid="${esc(r.fid)}"><td><div class="key">${esc(r.key)}</div><div class="row" style="margin-top:4px">${st}<span class="small muted mono">${esc(r.type)}</span></div></td>
        <td class="r">${r.missing ? "–" : `${r.a_passed}/${r.a_eval} → <b>${r.b_passed}/${r.b_eval}</b>`}</td>
        <td class="r">${r.change === null || r.change === undefined ? "–" : r.change + "%"}</td>
        <td class="r small">${r.missing ? "–" : `${fmtSec(r.a_time)} → ${fmtSec(r.b_time)}`}</td>
        <td class="small">${esc(r.judge_label || "")}</td>
        <td class="small">${(r.notes || []).map((n) => `<div class="he">${esc(n)}</div>`).join("")}</td></tr>`;
    }).join("");
  }

  function listTable(kind) {
    return `<table class="tbl"><thead><tr><th>File</th><th class="r">Checks A → B</th><th class="r">Text changed</th><th class="r">Time A → B</th>
      <th>Judge</th><th>Details</th></tr></thead><tbody id="list-rows">${listRows(kind)}</tbody></table>`;
  }

  function singleView() {
    const s = data.summary;
    return `<section class="tiles"><div class="tile"><span class="l">Checks passed</span><span class="v">${pct(s.passed, s.evaluated)}</span><span class="s">${s.passed} / ${s.evaluated}</span></div>
      <div class="tile"><span class="l">Median time</span><span class="v">${fmtSec(s.median)}</span></div>
      <div class="tile"><span class="l">Errors</span><span class="v">${s.errors}</span></div></section>
      <section class="card"><div class="tbl-wrap tall"><table class="tbl"><thead><tr><th>File</th><th>Type</th><th class="r">Checks</th>
      <th class="r">Image blocks</th><th class="r">Characters</th><th class="r">Time</th><th>Error</th></tr></thead><tbody>
      ${data.rows.map((r) => `<tr class="click" data-fid="${esc(r.fid)}"><td class="key">${esc(r.key)}</td><td class="small mono">${esc(r.type)}</td>
        <td class="r">${r.pending ? "–" : `${r.passed}/${r.evaluated}`}</td><td class="r">${r.image_blocks ?? "–"}</td><td class="r">${r.chars ?? "–"}</td>
        <td class="r">${fmtSec(r.elapsed)}</td><td class="small bad">${esc(r.error || "")}</td></tr>`).join("")}</tbody></table></div></section>`;
  }

  function render() {
    if (!b) {
      main.innerHTML = header() + progressCard() + singleView();
    } else {
      const n = data.counts;
      main.innerHTML = header() + progressCard() + tilesCompare() + `
        <section class="card" style="display:flex;flex-direction:column">
          <div class="row between" style="padding-right:12px;border-bottom:1px solid var(--line)">
            <div class="tabs" role="tablist" style="border:0">${tabsHtml([["types", "By file type"], ["reg", `Regressions · ${n.regressed}`],
              ["review", `Needs review · ${n.needs_review}`], ["imp", `Improvements · ${n.improved}`], ["all", `All files · ${data.rows.length}`]], tab)}</div>
            ${tab !== "types" ? `<label class="sr" for="file-search">Search files</label><input id="file-search" type="search" placeholder="Search files" value="${esc(search)}" style="width:220px">` : ""}
          </div>
          <div class="tbl-wrap tall">${tab === "types" ? typesTable() : listTable(tab)}</div>
          <p class="small muted" style="margin:0;padding:10px 16px;border-top:1px solid var(--line)">${tab === "review"
            ? "Text changed a lot but the checks did not notice. These files are good candidates for new checks."
            : "A file is regressed when B passes fewer of its checks than A, or B failed where A did not. Click a file to see the text side by side."}</p>
        </section>`;
    }
    bind();
  }

  function bind() {
    if ($("#sel-a")) $("#sel-a").onchange = async (e) => { a = e.target.value; if (a === b) b = pids.find((x) => x !== a); setParam("a", a); setParam("b", b); await load(); render(); };
    if ($("#sel-b")) $("#sel-b").onchange = async (e) => { b = e.target.value; if (a === b) a = pids.find((x) => x !== b); setParam("a", a); setParam("b", b); await load(); render(); };
    $$("[data-tab]", main).forEach((t) => t.onclick = () => { tab = t.dataset.tab; setParam("tab", tab); render(); });
    if ($("#file-search")) $("#file-search").oninput = (e) => { search = e.target.value; $("#list-rows").innerHTML = listRows(tab); };
    if ($("#cancel")) $("#cancel").onclick = async () => { await api(`/runs/${runId}/cancel`, { method: "POST" }); toast("Cancelling. Files already sent will finish."); };
    if ($("#resume")) $("#resume").onclick = async () => { try { await api(`/runs/${runId}/resume`, { method: "POST" }); await load(); render(); watch(); } catch (err) { fail(err); } };
    $("#del-run").onclick = async () => {
      if (!await modal({ title: "Delete this run?", body: "Its outputs and results are removed from the PVC. This cannot be undone.", ok: "Delete run", danger: true })) return;
      await api(`/runs/${runId}`, { method: "DELETE" }); toast("Run deleted."); go("#/runs");
    };
    main.onclick = (e) => {
      const tr = e.target.closest("tr[data-fid]");
      if (tr) go(`#/runs/${runId}/file/${tr.dataset.fid}?${qs({ a, b })}`);
    };
  }

  function watch() {
    if (!["running", "queued"].includes(run.status)) return;
    startPoll(async () => {
      await load();
      const scroll = $(".tbl-wrap.tall")?.scrollTop || 0;
      render();
      if ($(".tbl-wrap.tall")) $(".tbl-wrap.tall").scrollTop = scroll;
      if (!["running", "queued"].includes(run.status)) { clearInterval(App.poll); App.poll = null; refreshFoot(); }
    }, 3000);
  }

  await load();
  render();
  watch();
};

/* ---------- One file inside a run ---------- */

App.views.fileDetail = async function (main, runId, fid, params) {
  const run = await api(`/runs/${runId}`);
  const names = Object.fromEntries(run.parsers.map((p) => [p.id, p.name]));
  const a = params.a || run.parsers[0].id;
  const b = params.b || (run.parsers[1] ? run.parsers[1].id : "");
  let onlyChanges = params.all !== "1";
  let view = params.view || (b ? "side" : "rawa");
  let side = params.side || "checks";
  let d = await api(`/runs/${runId}/files/${fid}?${qs({ a, b, only_changes: onlyChanges })}`);

  function diffSide() {
    // One grid row per diff row, so both sides stay aligned even when a line wraps.
    const cell = (n, t, cls, right) => t === null
      ? `<div class="dl blank ${right ? "rc" : ""}"><span class="n"></span><span class="x"> </span></div>`
      : `<div class="dl ${cls} ${right ? "rc" : ""}"><span class="n">${n}</span><span class="x">${esc(t) || " "}</span></div>`;
    const rows = d.diff.map((r) => {
      if (r.kind === "skip") return `<div class="dskip" style="grid-column:1/-1">${r.count} unchanged lines</div>`;
      const cls = { same: "", del: "del", add: "add", change: "chg" }[r.kind];
      return cell(r.an, r.a, r.kind === "add" ? "" : cls, false) + cell(r.bn, r.b, r.kind === "del" ? "" : cls, true);
    });
    return `<div class="dgrid"><div class="hd">A · ${esc(names[a])}</div><div class="hd rc" style="color:var(--accent)">B · ${esc(names[b])}</div>${rows.join("")}</div>`;
  }

  function diffInline() {
    return d.diff.map((r) => {
      if (r.kind === "skip") return `<div class="dskip">${r.count} unchanged lines</div>`;
      if (r.kind === "same") return `<div class="dl"><span class="n">${r.an}</span><span class="x">${esc(r.a) || " "}</span></div>`;
      let out = "";
      if (r.a !== null) out += `<div class="dl del"><span class="n">−${r.an}</span><span class="x">${esc(r.a) || " "}</span></div>`;
      if (r.b !== null) out += `<div class="dl add"><span class="n">+${r.bn}</span><span class="x">${esc(r.b) || " "}</span></div>`;
      return out;
    }).join("");
  }

  function outputPane() {
    if (view === "rawa" || !d.b) return d.a ? (d.a.ok ? `<pre class="raw">${esc(d.a.text)}</pre>` : `<div class="note bad" style="margin:14px">${esc(d.a.error)}</div>`) : `<div class="empty">Not parsed yet.</div>`;
    if (view === "rawb") return d.b.ok ? `<pre class="raw">${esc(d.b.text)}</pre>` : `<div class="note bad" style="margin:14px">${esc(d.b.error)}</div>`;
    if (!d.a.ok || !d.b.ok) return `<div class="stack" style="padding:14px">${!d.a.ok ? `<div class="note bad">A failed: ${esc(d.a.error)}</div>` : ""}${!d.b.ok ? `<div class="note bad">B failed: ${esc(d.b.error)}</div>` : ""}</div>`;
    if (!d.diff.some((r) => r.kind !== "same" && r.kind !== "skip")) return `<div class="empty">Both outputs are the same.</div>`;
    return view === "inline" ? `<div style="padding-block:6px">${diffInline()}</div>` : diffSide();
  }

  function checksPane() {
    const ca = d.a ? d.a.checks : [];
    const cb = d.b ? Object.fromEntries(d.b.checks.map((c) => [c.id, c])) : {};
    if (!ca.length) return `<div class="empty">This file had no approved checks when the run started. <a href="#/tests?${qs({ key: d.file.key })}">Add checks</a></div>`;
    return `<table class="tbl"><thead><tr><th>Check</th><th class="c">A</th>${d.b ? `<th class="c">B</th>` : ""}</tr></thead><tbody>
      ${ca.map((c) => { const x = cb[c.id]; return `<tr><td><div class="chk" style="padding:0;border:0"><div class="body"><span class="kind">${esc(c.kind.replace("_", " "))}</span>
        <span class="txt he">${esc(c.desc)}</span>
        <span class="det">A: ${esc(c.detail || "")}${c.answer ? ` · answered “${esc(c.answer)}”` : ""}${x ? `<br>B: ${esc(x.detail || "")}${x.answer ? ` · answered “${esc(x.answer)}”` : ""}` : ""}</span></div></div></td>
        <td class="c">${checkMark(c.passed)}</td>${d.b ? `<td class="c">${checkMark(x ? x.passed : null)}</td>` : ""}</tr>`; }).join("")}</tbody></table>`;
  }

  function judgePane() {
    const j = d.judge;
    if (!j) return `<div class="stack" style="padding:16px"><p class="muted" style="margin:0">${d.change !== null && d.change !== undefined && d.change <= run.judge.threshold
      ? `The text changed by ${d.change}%, under the ${run.judge.threshold}% threshold, so the judge was not asked.` : "The judge was not asked about this file."}</p></div>`;
    const label = j.verdict === "error" ? "Judge error" : j.verdict === "disagree" ? "The two orders disagree" : j.verdict === "tie" ? "Tie" : `${j.verdict} is better`;
    return `<div class="stack" style="padding:16px"><span class="small muted mono">${esc(App.settings?.judge?.model || "judge")} · ${j.with_images ? "saw the pages" : "text only"} · asked in both orders</span>
      <span class="tag" style="align-self:flex-start;font-weight:600;font-size:13px">${esc(label)}${j.agree ? " · both orders agree" : ""}</span>
      <p style="margin:0">${esc(j.reason || "")}</p>
      ${j.reason_swapped ? `<p class="small muted" style="margin:0"><b>Swapped order:</b> ${esc(j.reason_swapped)}</p>` : ""}</div>`;
  }

  function render() {
    const row = d.row || {};
    const st = !b ? "" : row.needs_review ? `<span class="pill warn">Needs review</span>` : row.status === "improved" ? `<span class="pill good">▲ Improved</span>`
      : row.status === "regressed" ? `<span class="pill bad">▼ Regressed</span>` : `<span class="pill">Same checks</span>`;
    const m = (label, va, vb, fmt = (x) => x) => `<span><span class="muted">${label}</span> ${fmt(va)}${b ? ` → <b>${fmt(vb)}</b>` : ""}</span>`;
    const A = d.a || {}, B = d.b || {};
    main.innerHTML = `
      <div class="head"><div class="t"><a class="back" href="#/runs/${esc(runId)}?${qs({ a, b })}">← ${esc(run.name)}</a>
        <div class="row"><h1 class="file">${esc(basename(d.file.key))}</h1>${st}</div>
        <div class="kv small num"><span class="mono muted">${esc(d.file.type)} · ${fmtBytes(d.file.size)}</span>
          ${m("Checks", `${A.passed ?? "–"}/${A.evaluated ?? "–"}`, `${B.passed ?? "–"}/${B.evaluated ?? "–"}`)}
          ${m("Image blocks", A.image_blocks ?? "–", B.image_blocks ?? "–")}
          ${m("Time", A.elapsed, B.elapsed, fmtSec)}
          ${b && d.change !== undefined && d.change !== null ? `<span><span class="muted">Text changed</span> ${d.change}%</span>` : ""}</div></div>
        <div class="row"><a class="btn" href="/api/files/raw?${qs({ key: d.file.key })}">Download original</a>
          <a class="btn primary" href="#/tests?${qs({ key: d.file.key })}">Edit checks</a></div></div>
      <div class="split">
        <section class="card" style="flex:3 1 560px;display:flex;flex-direction:column;overflow:hidden">
          <div class="ch"><div class="seg" role="tablist">${(b ? [["side", "Side by side"], ["inline", "Inline diff"], ["rawa", "Raw A"], ["rawb", "Raw B"]] : [["rawa", "Output"]])
            .map(([k, l]) => `<button type="button" data-view="${k}" class="${view === k ? "on" : ""}">${l}</button>`).join("")}</div>
            ${b && (view === "side" || view === "inline") ? `<label class="inline small"><input id="only-changes" type="checkbox" ${onlyChanges ? "checked" : ""}> Only changed parts</label>` : ""}</div>
          <div class="tbl-wrap" style="max-height:calc(100vh - 260px)" dir="auto">${outputPane()}</div></section>
        <aside class="card" style="flex:2 1 340px;display:flex;flex-direction:column;overflow:hidden">
          <div class="tabs" role="tablist">${tabsHtml(b ? [["checks", `Checks · ${B.passed ?? "–"}/${B.evaluated ?? "–"}`], ["judge", "Judge"]] : [["checks", "Checks"]], side, "side")}</div>
          <div class="tbl-wrap" style="max-height:calc(100vh - 260px)">${side === "judge" ? judgePane() : checksPane()}</div></aside></div>`;
    $$("[data-view]", main).forEach((x) => x.onclick = () => { view = x.dataset.view; setParam("view", view); render(); });
    $$("[data-side]", main).forEach((x) => x.onclick = () => { side = x.dataset.side; setParam("side", side); render(); });
    if ($("#only-changes")) $("#only-changes").onchange = async (e) => {
      onlyChanges = e.target.checked; setParam("all", onlyChanges ? "" : "1");
      d = await api(`/runs/${runId}/files/${fid}?${qs({ a, b, only_changes: onlyChanges })}`); render();
    };
  }
  if (!App.settings) App.settings = await api("/settings");
  render();
};
