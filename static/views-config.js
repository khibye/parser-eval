/* Parsers, VLM quality and Settings. */
"use strict";

/* ---------- Parsers ---------- */

App.views.parsers = async function (main, params) {
  let [list, settings] = await Promise.all([api("/parsers"), api("/settings")]);
  App.settings = settings;
  let health = {};
  let selected = params.id || (list[0] ? list[0].id : "new");
  let testResult = null;

  const blank = { id: "new", name: "", description: "", url: "", auth_type: "none", header_name: "Authorization", token: { set: false },
    params: { to_formats: ["md"], image_export_mode: "placeholder", do_ocr: true }, text_field: "document.md_content",
    list_mode: "repeat", timeout: 600, parallel: 8 };

  function current() { return selected === "new" ? blank : (list.find((p) => p.id === selected) || blank); }

  function listHtml() {
    if (!list.length) return `<div class="empty">No parsers yet.</div>`;
    return list.map((p) => {
      const h = health[p.id];
      const hs = !h ? `<span class="small muted">…</span>` : h.ok ? `<span class="small good dot">Reachable</span>` : `<span class="small bad dot">Not reachable</span>`;
      return `<a class="list-item ${p.id === selected ? "on" : ""}" href="#/parsers?id=${esc(p.id)}" data-pick="${esc(p.id)}">
        <span class="row between" style="flex-wrap:nowrap"><span class="mono" style="font-weight:600;font-size:13px">${esc(p.name)}</span>${hs}</span>
        <span class="small" style="color:var(--ink2)">${esc(p.description || p.url)}</span></a>`;
    }).join("");
  }

  function formHtml() {
    const p = current();
    return `<form id="pform" class="card pad" style="flex:3 1 600px" autocomplete="off">
      <div class="row between"><h2 class="mono" style="font-size:17px">${p.id === "new" ? "New parser" : esc(p.name)}</h2>
        <span class="small muted">Saved in /data/config/parsers.json</span></div>
      <div class="fields">
        <div class="field"><label for="p-name">Name</label><input id="p-name" class="mono" type="text" value="${esc(p.name)}" required placeholder="xberg-1.3.10"></div>
        <div class="field"><label for="p-desc">Description</label><input id="p-desc" type="text" value="${esc(p.description)}" placeholder="docintel with Xberg 1.3.10"></div>
        <div class="field wide"><label for="p-url">docintel URL</label>
          <div class="row" style="flex-wrap:nowrap;gap:0"><input id="p-url" class="mono" type="url" value="${esc(p.url)}" placeholder="http://docintel-x1310.eval.svc:8080" style="border-radius:6px 0 0 6px">
          <span class="mono small" style="padding:9px 11px;background:var(--bg);border:1px solid var(--input);border-left:0;border-radius:0 6px 6px 0;color:var(--muted)">/v1/convert/file</span></div></div>
        <div class="field"><label for="p-auth">Authentication</label><select id="p-auth">
          ${[["none", "None"], ["bearer", "Bearer token"], ["header", "Custom header"]].map(([k, l]) => `<option value="${k}" ${p.auth_type === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="field ${p.auth_type === "header" ? "" : "hidden"}" id="hdr-field"><label for="p-hdr">Header name</label><input id="p-hdr" class="mono" type="text" value="${esc(p.header_name)}"></div>
        <div class="field ${p.auth_type === "none" ? "hidden" : ""}" id="tok-field"><label for="p-token">Token</label>
          <input id="p-token" type="password" placeholder="${p.token?.set ? "Saved · type to replace" : "Paste the token"}"></div>
      </div>
      <div class="fields" style="grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr))">
        <div class="field"><label for="p-params">Docling parameters (JSON)</label>
          <textarea id="p-params" rows="7" spellcheck="false">${esc(JSON.stringify(p.params || {}, null, 2))}</textarea>
          <span class="hint">The same value you use for DOCLING_PARAMS in Open WebUI. Each key is sent as a form field.</span></div>
        <div class="stack">
          <div class="fields" style="grid-template-columns:repeat(2,minmax(0,1fr))">
            <div class="field"><label for="p-field">Text in the response</label><input id="p-field" class="mono" type="text" value="${esc(p.text_field)}"></div>
            <div class="field"><label for="p-lists">Send lists as</label><select id="p-lists">
              <option value="repeat" ${p.list_mode !== "json" ? "selected" : ""}>Repeated fields</option><option value="json" ${p.list_mode === "json" ? "selected" : ""}>JSON text</option></select></div>
            <div class="field"><label for="p-timeout">Timeout (s)</label><input id="p-timeout" type="number" min="5" value="${p.timeout}"></div>
            <div class="field"><label for="p-par">Max parallel requests</label><input id="p-par" type="number" min="1" value="${p.parallel}"></div></div>
          <div class="note plain stack" style="gap:4px"><b style="font-weight:500">VLM caching</b>
            <span class="small">Set this docintel's VLM URL to the address below and keep its LiteLLM key. The tool forwards each call to LiteLLM and stores the answer.</span>
            <span class="mono small" style="overflow-wrap:anywhere">${esc(settings.proxy_url)}</span></div></div></div>
      ${testResult ? `<div class="note ${testResult.ok ? "good" : "bad"}">${testResult.ok
        ? `Test passed · ${testResult.status} · sample PDF parsed in ${fmtSec(testResult.elapsed)} · ${testResult.chars} characters`
        : `Test failed · ${esc(testResult.error || "")}`}</div>` : ""}
      <div class="formfoot"><div class="row">${p.id !== "new" ? `<button type="button" class="btn danger" id="p-del">Delete</button>
        <button type="button" class="btn" id="p-dup">Duplicate</button>` : ""}</div>
        <div class="row">${p.id !== "new" ? `<button type="button" class="btn" id="p-test">Test connection</button>` : ""}
          <button type="submit" class="btn primary">${p.id === "new" ? "Add parser" : "Save"}</button></div></div></form>`;
  }

  function render() {
    main.innerHTML = `<div class="head"><div class="t"><h1>Parsers</h1>
        <p>Each parser is one docintel instance. The tool calls it the way Open WebUI calls Docling: a multipart POST to <span class="mono">/v1/convert/file</span>, with the text read from <span class="mono">document.md_content</span>.</p></div>
        <a class="btn primary" href="#/parsers?id=new">＋ Add parser</a></div>
      <div class="split" style="align-items:flex-start">
        <section class="card" style="flex:1 1 280px;overflow:auto;max-height:calc(100vh - 200px)">${listHtml()}</section>
        ${formHtml()}</div>`;
    bind();
  }

  function bind() {
    $$("[data-pick]", main).forEach((a) => a.onclick = (e) => { e.preventDefault(); selected = a.dataset.pick; testResult = null; setParam("id", selected); render(); });
    $("#p-auth").onchange = (e) => {
      $("#hdr-field").classList.toggle("hidden", e.target.value !== "header");
      $("#tok-field").classList.toggle("hidden", e.target.value === "none");
    };
    $("#pform").onsubmit = async (e) => {
      e.preventDefault();
      let paramsObj;
      try { paramsObj = JSON.parse($("#p-params").value || "{}"); } catch (err) { fail(new Error("The Docling parameters are not valid JSON.")); return; }
      const body = { name: $("#p-name").value, description: $("#p-desc").value, url: $("#p-url").value, auth_type: $("#p-auth").value,
        header_name: $("#p-hdr").value, params: paramsObj, text_field: $("#p-field").value, list_mode: $("#p-lists").value,
        timeout: Number($("#p-timeout").value), parallel: Number($("#p-par").value) };
      if ($("#p-token").value) body.token = $("#p-token").value;
      try {
        const saved = selected === "new" ? await api("/parsers", { method: "POST", body }) : await api(`/parsers/${selected}`, { method: "PUT", body });
        list = await api("/parsers"); selected = saved.id; setParam("id", selected); testResult = null;
        toast("Parser saved."); render(); loadHealth();
      } catch (err) { fail(err); }
    };
    if ($("#p-test")) $("#p-test").onclick = async () => {
      const btn = $("#p-test"); btn.disabled = true; btn.innerHTML = `<span class="spin"></span> Testing`;
      try { testResult = await api(`/parsers/${selected}/test`, { method: "POST" }); } catch (err) { testResult = { ok: false, error: err.message }; }
      render();
    };
    if ($("#p-dup")) $("#p-dup").onclick = async () => {
      try { const c = await api(`/parsers/${selected}/duplicate`, { method: "POST" }); list = await api("/parsers"); selected = c.id; setParam("id", selected); toast("Copy created. Rename it and change the URL."); render(); }
      catch (err) { fail(err); }
    };
    if ($("#p-del")) $("#p-del").onclick = async () => {
      if (!await modal({ title: `Delete ${current().name}?`, body: "Past runs keep their results. New runs cannot use this parser.", ok: "Delete parser", danger: true })) return;
      await api(`/parsers/${selected}`, { method: "DELETE" }); list = await api("/parsers"); selected = list[0]?.id || "new"; setParam("id", selected); render();
    };
  }

  async function loadHealth() {
    try { health = await api("/parsers/health"); const box = $(".split > section.card"); if (box) { box.innerHTML = listHtml(); bind(); } } catch (e) { /* ignore */ }
  }

  render();
  loadHealth();
};

/* ---------- VLM quality ---------- */

App.views.vlm = async function (main, params) {
  let runs = await api("/vlm-runs");
  let runId = params.run || (runs[0] ? runs[0].id : "");
  let tab = params.tab || (runs.length ? "overview" : "new");
  let detail = null, index = Number(params.i || 0), filter = "all";
  let datasets = null;
  if (!App.settings) App.settings = await api("/settings");

  async function load() {
    detail = runId ? await api(`/vlm-runs/${runId}`) : null;
  }

  function overview() {
    const s = detail.summary, setups = s.setups;
    const crit = [["accurate", "Accurate"], ["complete", "Complete"], ["text", "Text read right"], ["no_invention", "Nothing invented"], ["overall", "Overall"]];
    const colors = ["#8A92A0", "#2650B8", "#B7791F", "#0B6247", "#7E3AAE"];
    const best = (key) => Math.max(...setups.map((x) => x.scores[key] ?? -1));
    return `<div class="grid2" style="padding:18px 20px">
      <div class="stack"><div class="row between"><h2 style="font-size:15px;margin:0">Average scores</h2>
        <span class="small">${s.head_to_head.map((h) => `<b style="color:var(--accent)">${esc(h.setup)} better on ${h.better}</b> · ${esc(setups[0].name)} on ${h.worse} · ${h.tie} ties`).join("<br>")}</span></div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Criterion</th>${setups.map((x, i) => `<th class="r"><span style="color:${colors[i % colors.length]}">■</span> ${esc(x.name)}</th>`).join("")}</tr></thead><tbody>
          ${crit.map(([k, l]) => `<tr><td>${l}</td>${setups.map((x) => `<td class="r" style="${x.scores[k] === best(k) && setups.length > 1 ? "font-weight:600" : ""}">${x.scores[k] ?? "–"}</td>`).join("")}</tr>`).join("")}
          <tr><td>Median time</td>${setups.map((x) => `<td class="r">${fmtSec(x.median_time)}</td>`).join("")}</tr>
          <tr><td>Failed calls</td>${setups.map((x) => `<td class="r">${x.failed}</td>`).join("")}</tr></tbody></table></div>
        <p class="small muted" style="margin:0">Scores run from 1 to 5. The judge sees one description at a time and does not know which setup wrote it.</p></div>
      <div class="stack"><h2 style="font-size:15px;margin:0">Overall score by image kind</h2>
        <p class="small muted" style="margin:0">Kinds are the subfolders of the image folder.</p>
        ${s.kinds.map((k) => `<div style="display:grid;grid-template-columns:minmax(110px,160px) minmax(0,1fr) 90px;gap:12px;align-items:center" class="num">
          <span class="small">${esc(k.kind)} <span class="muted">· ${k.count}</span></span>
          <div class="bars">${setups.map((x, i) => `<div class="bar"><i style="width:${((x.kinds[k.kind] || 0) / 5 * 100).toFixed(0)}%;background:${colors[i % colors.length]}"></i></div>`).join("")}</div>
          <span class="small r" style="text-align:right">${setups.map((x) => x.kinds[k.kind] ?? "–").join(" · ")}</span></div>`).join("")}</div></div>`;
  }

  function filteredIndexes() {
    const o = detail.summary.overall_by_image;
    const all = o.map((_, i) => i);
    if (filter === "worse") return all.filter((i) => o[i].length > 1 && o[i][1] !== null && o[i][0] !== null && o[i][1] < o[i][0]);
    if (filter === "spread") return all.filter((i) => o[i].filter((x) => x !== null).length > 1)
      .sort((x, y) => (Math.max(...o[y]) - Math.min(...o[y])) - (Math.max(...o[x]) - Math.min(...o[x]))).slice(0, 50);
    if (filter === "low") return all.filter((i) => o[i].some((x) => x !== null && x <= 2));
    return all;
  }

  async function imageView() {
    const idxs = filteredIndexes();
    if (!idxs.length) return `<div class="empty">No images match this filter.</div>`;
    if (!idxs.includes(index)) index = idxs[0];
    const pos = idxs.indexOf(index);
    const im = await api(`/vlm-runs/${runId}/images/${index}`);
    return `<div class="row between" style="padding:10px 20px;border-bottom:1px solid var(--soft)">
        <div class="row"><b>Image ${pos + 1} of ${idxs.length}</b><span class="mono small muted">${esc(im.image.key)}</span><span class="tag">${esc(im.image.kind)}</span></div>
        <div class="row"><label class="small" for="v-filter">Show</label><select id="v-filter" style="width:auto">
          ${[["all", "All images"], ["worse", "Where the second setup is worse"], ["spread", "Where setups disagree most"], ["low", "Any score of 2 or less"]]
            .map(([k, l]) => `<option value="${k}" ${filter === k ? "selected" : ""}>${l}</option>`).join("")}</select>
          <button class="btn sm" id="v-prev" ${pos <= 0 ? "disabled" : ""}>Previous</button><button class="btn sm" id="v-next" ${pos >= idxs.length - 1 ? "disabled" : ""}>Next</button></div></div>
      <div class="split" style="gap:0">
        <div style="flex:1 1 300px;padding:18px;background:var(--bg);display:flex;align-items:center;justify-content:center">
          <img class="preview-img" style="max-height:460px" alt="${esc(im.name)}" src="/api/vlm-runs/${esc(runId)}/images/${index}/bytes"></div>
        ${im.rows.map((r, i) => `<div style="flex:1 1 260px;padding:16px 18px;border-left:1px solid var(--line);display:flex;flex-direction:column;gap:10px;${i === 1 ? "background:#F7F9FD" : ""}">
          <span class="mono small" style="color:${i === 1 ? "var(--accent)" : "var(--muted)"}">${esc(r.setup)} · ${r.out ? fmtSec(r.out.elapsed) : "–"}</span>
          ${r.out?.error ? `<div class="note bad">${esc(r.out.error)}</div>` : `<p class="he" dir="auto" style="margin:0;line-height:1.7;white-space:pre-wrap">${esc(r.out?.description || "Not done yet.")}</p>`}
          ${r.scores && !r.scores.error ? `<div class="row small num">${[["accurate", "Accurate"], ["complete", "Complete"], ["text", "Text"], ["no_invention", "Nothing invented"], ["overall", "Overall"]]
            .map(([k, l]) => `<span class="tag" style="${r.scores[k] <= 2 ? "background:var(--bad-bg);color:var(--bad)" : ""}">${l} ${r.scores[k] ?? "–"}</span>`).join("")}</div>
            <p class="small muted" style="margin:0">${esc(r.scores.note || "")}</p>` : r.scores?.error ? `<div class="note bad small">${esc(r.scores.error)}</div>` : ""}</div>`).join("")}</div>`;
  }

  async function newForm() {
    if (!datasets) { try { datasets = await api("/datasets"); } catch (e) { datasets = []; } }
    const prompt = (await api("/vlm-default-prompt")).prompt;
    const imgFolders = datasets.filter((d) => d.types.Images);
    return `<form id="vform" class="stack" style="padding:18px 20px">
      <div class="fields">
        <div class="field"><label for="v-name">Name</label><input id="v-name" type="text" placeholder="for example: current model vs candidate"></div>
        <div class="field"><label for="v-prefix">Image folder (S3)</label><select id="v-prefix" class="mono">
          ${imgFolders.length ? imgFolders.map((d) => `<option value="${esc(d.prefix)}">${esc(d.prefix)} · ${d.types.Images} images</option>`).join("") : `<option value="">No folders with images</option>`}</select>
          <span class="hint">Subfolders become image kinds in the results, for example charts/ or scans/.</span></div>
        <div class="field"><label for="v-conc">Parallel calls per setup</label><input id="v-conc" type="number" min="1" value="4"></div>
        <div class="field" style="justify-content:flex-end"><label class="inline"><input id="v-judge" type="checkbox" ${App.settings.judge.vision ? "checked" : "disabled"}> The judge looks at each image and scores the descriptions</label></div></div>
      <div id="setups" class="grid2">${[0, 1].map((i) => setupHtml(i, prompt)).join("")}</div>
      <div class="row between"><button type="button" class="btn" id="add-setup">＋ Add setup</button>
        <div class="row"><span class="small muted">Calls go straight to LiteLLM at ${esc(App.settings.vlm.upstream || "(not set)")}</span><button type="submit" class="btn primary">Start VLM run</button></div></div></form>`;
  }

  function setupHtml(i, prompt) {
    return `<section class="card pad" data-setup="${i}"><div class="row between"><h2>Setup ${String.fromCharCode(65 + i)}</h2>
      ${i > 0 ? `<button type="button" class="linkbtn small" data-rm="${i}">Remove</button>` : ""}</div>
      <div class="fields"><div class="field"><label>Name</label><input type="text" data-f="name" placeholder="${i ? "candidate" : "current"}"></div>
        <div class="field"><label>Model (LiteLLM name)</label><input type="text" class="mono" data-f="model" required></div>
        <div class="field wide"><label>Prompt</label><textarea rows="4" data-f="prompt" style="font-family:var(--sans);font-size:13px">${esc(prompt)}</textarea></div>
        <div class="field"><label>Long side limit (px)</label><input type="number" data-f="max_side" value="1600"></div>
        <div class="field" style="justify-content:flex-end"><label class="inline"><input type="checkbox" data-f="flatten" checked> White under transparency</label></div></div></section>`;
  }

  async function render() {
    const r = detail?.run;
    const running = r && ["running", "queued"].includes(r.status);
    let body;
    if (tab === "new") body = await newForm();
    else if (!detail) body = `<div class="empty">No VLM runs yet.</div>`;
    else if (tab === "images") body = await imageView();
    else body = overview();
    main.innerHTML = `<div class="head"><div class="t"><h1>VLM quality</h1>
        <p>Send the same images to two or more VLM setups. The judge looks at each image and scores every description on the same rubric.</p></div>
        <div class="row">${runs.length ? `<label class="sr" for="v-run">VLM run</label><select id="v-run" class="mono" style="width:auto">
          ${runs.map((x) => `<option value="${esc(x.id)}" ${x.id === runId ? "selected" : ""}>${esc(x.name)} · ${fmtWhen(x.created)}</option>`).join("")}</select>` : ""}
          ${r ? `<button class="btn danger" id="v-del">Delete</button>` : ""}</div></div>
      ${r && tab !== "new" ? `<div class="row small">${r.setups.map((s, i) => `<span class="tag ${i === 1 ? "blue" : ""}">${String.fromCharCode(65 + i)} · <span class="mono">${esc(s.model)}</span></span>`).join("")}
        <span class="tag">Images · <span class="mono">${esc(r.prefix)}</span> · ${r.image_count}</span>
        <span class="tag">Judge · ${r.judge ? "looks at images" : "off"}</span>
        ${running ? `<span class="pill blue">${r.status === "queued" ? "Queued" : `Running · ${r.progress.done}/${r.progress.total} described · ${r.progress.scored} scored`}</span>
          <button class="btn sm danger" id="v-cancel">Cancel</button>` : r.status !== "completed" ? `<span class="pill warn">${esc(r.status)}</span>` : ""}</div>` : ""}
      <section class="card"><div class="tabs" role="tablist">${tabsHtml([["overview", "Overview"], ["images", "Image by image"], ["new", "＋ New VLM run"]], tab)}</div>
        <div id="v-body">${body}</div></section>`;
    bind();
  }

  function bind() {
    $$("[data-tab]", main).forEach((t) => t.onclick = async () => { tab = t.dataset.tab; setParam("tab", tab); await render(); });
    if ($("#v-run")) $("#v-run").onchange = async (e) => { runId = e.target.value; setParam("run", runId); index = 0; await load(); if (tab === "new") tab = "overview"; await render(); };
    if ($("#v-filter")) $("#v-filter").onchange = async (e) => { filter = e.target.value; index = -1; await render(); };
    if ($("#v-prev")) $("#v-prev").onclick = async () => { const ids = filteredIndexes(); index = ids[ids.indexOf(index) - 1]; setParam("i", index); await render(); };
    if ($("#v-next")) $("#v-next").onclick = async () => { const ids = filteredIndexes(); index = ids[ids.indexOf(index) + 1]; setParam("i", index); await render(); };
    if ($("#v-cancel")) $("#v-cancel").onclick = async () => { await api(`/vlm-runs/${runId}/cancel`, { method: "POST" }); toast("Cancelling."); };
    if ($("#v-del")) $("#v-del").onclick = async () => {
      if (!await modal({ title: "Delete this VLM run?", body: "Its descriptions and scores are removed.", ok: "Delete", danger: true })) return;
      await api(`/vlm-runs/${runId}`, { method: "DELETE" }); runs = await api("/vlm-runs"); runId = runs[0]?.id || ""; await load(); tab = runId ? "overview" : "new"; await render();
    };
    if ($("#add-setup")) $("#add-setup").onclick = async () => {
      const n = $$("[data-setup]", main).length;
      const prompt = $("[data-setup='0'] [data-f=prompt]").value;
      $("#setups").insertAdjacentHTML("beforeend", setupHtml(n, prompt)); bindRemove();
    };
    bindRemove();
    if ($("#vform")) $("#vform").onsubmit = async (e) => {
      e.preventDefault();
      const setups = $$("[data-setup]", main).map((s) => ({
        name: $("[data-f=name]", s).value, model: $("[data-f=model]", s).value.trim(), prompt: $("[data-f=prompt]", s).value,
        max_side: Number($("[data-f=max_side]", s).value) || 1600, flatten: $("[data-f=flatten]", s).checked }));
      try {
        const res = await api("/vlm-runs", { method: "POST", body: { name: $("#v-name").value, prefix: $("#v-prefix").value, setups,
          concurrency: Number($("#v-conc").value) || 4, judge: $("#v-judge").checked } });
        runs = await api("/vlm-runs"); runId = res.id; tab = "overview"; setParam("run", runId); setParam("tab", tab);
        await load(); await render(); watch();
      } catch (err) { fail(err); }
    };
  }
  function bindRemove() { $$("[data-rm]", main).forEach((b) => b.onclick = () => b.closest("[data-setup]").remove()); }

  function watch() {
    startPoll(async () => {
      if (!detail || !["running", "queued"].includes(detail.run.status) || tab === "new") return;
      await load();
      if (tab === "overview") await render();
      else if ($(".tabs")) { /* keep the image view steady while scores arrive */ }
    }, 4000);
  }

  await load();
  await render();
  watch();
};

/* ---------- Settings ---------- */

App.views.settings = async function (main, params) {
  let s = await api("/settings");
  App.settings = s;
  let tab = params.tab || "s3";
  let note = null;

  const secret = (id, label, value, hint = "") => `<div class="field"><label for="${id}">${label}</label>
    <input id="${id}" type="password" autocomplete="new-password" placeholder="${value?.set ? "Saved · type to replace" : "Not set"}">${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
  const text = (id, label, value, opts = {}) => `<div class="field ${opts.wide ? "wide" : ""}"><label for="${id}">${label}</label>
    <input id="${id}" type="${opts.type || "text"}" class="${opts.mono ? "mono" : ""}" value="${esc(value ?? "")}" ${opts.placeholder ? `placeholder="${esc(opts.placeholder)}"` : ""}>
    ${opts.hint ? `<span class="hint">${opts.hint}</span>` : ""}</div>`;
  const noteHtml = () => note ? `<span class="small ${note.ok ? "good" : "bad"}">${note.ok ? "● " : ""}${esc(note.text)}</span>` : "<span></span>";

  async function panel() {
    if (tab === "access") {
      const me = await api("/me");
      return `<h2>Access</h2><p class="muted" style="margin:0">One shared password protects the whole tool.${me.password_source === "initial" ? " You are still using the password from the installation. Change it here." : ""}</p>
        <div class="fields">${[["a-cur", "Current password", "current-password"], ["a-new", "New password", "new-password"], ["a-rep", "Repeat new password", "new-password"]]
          .map(([id, l, ac]) => `<div class="field"><label for="${id}">${l}</label><input id="${id}" type="password" autocomplete="${ac}"></div>`).join("")}</div>
        <div class="row" style="justify-content:flex-end"><button class="btn primary" id="save-pw">Change password</button></div>
        <div class="formfoot"><div class="field"><label for="a-days">Stay signed in for</label><select id="a-days" style="width:auto">
          ${[1, 7, 30].map((d) => `<option value="${d}" ${me.session_days === d ? "selected" : ""}>${d === 1 ? "1 day" : d + " days"}</option>`).join("")}</select></div>
          <button class="btn" id="signout-all">Sign out all browsers</button></div>`;
    }
    if (tab === "s3") {
      const c = s.s3;
      return `<h2>File storage (S3)</h2><p class="muted" style="margin:0">Where test files are read from and uploaded to. Works with any S3-compatible storage.</p>
        <div class="fields">${text("s3-endpoint", "Endpoint URL", c.endpoint, { mono: true, wide: true, placeholder: "https://s3.internal.example" })}
          ${text("s3-bucket", "Bucket", c.bucket, { mono: true })}${text("s3-root", "Root folder", c.root, { mono: true, hint: "Optional. Only files under it are used." })}
          ${text("s3-key", "Access key ID", c.access_key, { mono: true })}${secret("s3-secret", "Secret access key", c.secret_key)}
          ${text("s3-region", "Region", c.region, { mono: true })}
          <div class="field"><label for="s3-style">Addressing</label><select id="s3-style"><option value="path" ${c.addressing === "path" ? "selected" : ""}>Path style</option>
            <option value="virtual" ${c.addressing !== "path" ? "selected" : ""}>Virtual host</option></select><span class="hint">Most internal S3 services need path style.</span></div></div>
        <label class="inline"><input id="s3-verify" type="checkbox" ${c.verify_tls ? "checked" : ""}> Verify the TLS certificate</label>
        <div class="field"><label for="s3-ca">Internal CA certificate (PEM)</label><textarea id="s3-ca" rows="3" placeholder="-----BEGIN CERTIFICATE-----">${esc(c.ca_pem)}</textarea>
          <span class="hint">Also used for docintel, LiteLLM and the judge. <label class="linkbtn" for="ca-file" style="cursor:pointer">Load from a file</label><input id="ca-file" type="file" class="sr" accept=".pem,.crt,.cer"></span></div>
        <div class="formfoot">${noteHtml()}<div class="row"><button class="btn" id="test">Test connection</button><button class="btn primary" id="save">Save storage</button></div></div>`;
    }
    if (tab === "judge") {
      const c = s.judge;
      return `<h2>Judge model</h2><p class="muted" style="margin:0">Answers question checks, compares outputs, suggests checks and scores VLM descriptions.</p>
        <div class="fields">${text("j-url", "LiteLLM URL", c.url, { mono: true, wide: true, placeholder: "http://litellm.ai.svc:4000/v1" })}
          ${text("j-model", "Model", c.model, { mono: true })}${secret("j-key", "API key", c.api_key)}
          ${text("j-ctx", "Context limit (tokens)", c.context_tokens, { type: "number" })}${text("j-par", "Parallel judge calls", c.parallel, { type: "number" })}
          ${text("j-temp", "Temperature", c.temperature, { type: "number" })}${text("j-timeout", "Timeout per call (s)", c.timeout, { type: "number" })}</div>
        <label class="inline"><input id="j-vision" type="checkbox" ${c.vision ? "checked" : ""}> This model can see images</label>
        <div class="formfoot">${noteHtml()}<div class="row"><button class="btn" id="test">Test judge</button><button class="btn primary" id="save">Save judge</button></div></div>`;
    }
    if (tab === "vlm") {
      const c = s.vlm, st = await api("/vlm-proxy/status");
      return `<h2>VLM cache</h2><p class="muted" style="margin:0">docintel sends its VLM calls here. The tool forwards them to LiteLLM and stores each answer, matched by image pixels, prompt and model.</p>
        <div class="field"><span class="lbl">Address to set as docintel's VLM URL</span><div class="row" style="flex-wrap:nowrap">
          <span class="mono small" style="flex:1;padding:9px 12px;background:var(--bg);border-radius:6px;overflow-wrap:anywhere" id="proxy-url">${esc(s.proxy_url)}</span>
          <button class="btn sm" id="copy">Copy</button></div><span class="hint">Reachable inside the cluster only. The Route does not expose it.</span></div>
        <div class="fields">${text("v-up", "Forward to (LiteLLM URL)", c.upstream, { mono: true, placeholder: "http://litellm.ai.svc:4000/v1" })}
          <div class="field"><label for="v-mode">API key for forwarded calls</label><select id="v-mode"><option value="passthrough" ${c.key_mode !== "saved" ? "selected" : ""}>Use the key docintel sends</option>
            <option value="saved" ${c.key_mode === "saved" ? "selected" : ""}>Use the key saved here</option></select></div>
          ${secret("v-key", "Saved API key", c.api_key, "Also used by VLM quality runs. If empty, they use the judge key.")}
          ${text("v-timeout", "Timeout per call (s)", c.timeout, { type: "number" })}</div>
        <div class="kv num small"><span><span class="muted">Stored answers</span> <b>${st.answers}</b></span><span><span class="muted">Size</span> <b>${fmtBytes(st.bytes)}</b></span>
          <span><span class="muted">Calls since start</span> <b>${st.calls}</b> (${st.hits} from cache)</span><span><span class="muted">Last call</span> <b>${fmtWhen(st.last_call)}</b></span></div>
        <div class="formfoot"><button class="btn danger" id="clear">Clear cache</button><div class="row">${noteHtml()}<button class="btn primary" id="save">Save VLM cache</button></div></div>`;
    }
    if (tab === "eval") {
      const c = s.eval;
      return `<h2>Evaluation</h2><p class="muted" style="margin:0">How outputs are read when checks run.</p>
        <div class="fields">${text("e-tag", "Image block tag", c.image_tag, { mono: true, hint: "docintel wraps each image analysis in this tag, for example &lt;image_analysis&gt;." })}
          ${text("e-thr", "Default change threshold (%)", c.change_threshold, { type: "number", hint: "Files whose text changed more than this go to the judge and to Needs review." })}
          ${text("e-fuzzy", "Close match threshold (%)", c.fuzzy_threshold, { type: "number", hint: "How similar text must be for a close-match check to pass." })}</div>
        <div class="formfoot">${noteHtml()}<button class="btn primary" id="save">Save evaluation</button></div>`;
    }
    const st = await api("/storage");
    const keep = s.storage.keep_days;
    return `<h2>Results storage (PVC)</h2><p class="muted" style="margin:0">Run outputs, checks, settings and reports, all under /data.</p>
      <div class="stack" style="gap:6px"><div class="row between num"><span>/data</span><span>${fmtBytes(st.used)} of ${fmtBytes(st.total)}</span></div>
        <div class="bar" style="height:8px"><i style="width:${(st.used / st.total * 100).toFixed(1)}%"></i></div></div>
      <div class="tiles">${[["Run outputs", st.parts.runs, `${st.runs} runs`], ["VLM quality runs", st.parts.vlm_runs], ["VLM cache", st.parts.vlm_cache],
        ["Checks and settings", st.parts.checks_settings], ["Parse-now outputs", st.parts.outputs]]
        .map(([l, v, sub]) => `<div class="tile"><span class="l">${l}</span><span class="v" style="font-size:18px">${fmtBytes(v)}</span>${sub ? `<span class="s">${sub}</span>` : ""}</div>`).join("")}</div>
      <div class="formfoot"><div class="row" style="align-items:flex-end"><div class="field"><label for="keep">Keep run outputs</label><select id="keep" style="width:auto">
        ${[[0, "Until I delete them"], [90, "90 days"], [30, "30 days"]].map(([v, l]) => `<option value="${v}" ${keep === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <button class="btn" id="cleanup">Delete older runs now</button></div>${noteHtml()}<button class="btn primary" id="save">Save storage settings</button></div>`;
  }

  async function render() {
    main.innerHTML = `<div class="head"><div class="t"><h1>Settings</h1>
        <p>Everything here is saved on the PVC and survives restarts. Passwords and keys are stored encrypted.</p></div></div>
      <div class="tabs bare" role="tablist" aria-label="Settings sections">${tabsHtml([["access", "Access"], ["s3", "File storage"], ["judge", "Judge"],
        ["vlm", "VLM cache"], ["eval", "Evaluation"], ["storage", "Results storage"]], tab)}</div>
      <section class="card pad" style="max-width:860px;gap:16px">${await panel()}</section>`;
    bind();
  }

  async function saveSection(section, values, msg) {
    try { s[section] = await api(`/settings/${section}`, { method: "PUT", body: values }); App.settings = s; note = { ok: true, text: msg }; refreshFoot(); }
    catch (err) { note = { ok: false, text: err.message }; }
    await render();
  }
  const val = (id) => $(id).value;
  const num = (id) => Number($(id).value);
  const sec = (id, obj, field) => { if ($(id).value) obj[field] = $(id).value; return obj; };

  function bind() {
    $$("[data-tab]", main).forEach((t) => t.onclick = async () => { tab = t.dataset.tab; note = null; setParam("tab", tab); await render(); });
    if (tab === "access") {
      $("#save-pw").onclick = async () => {
        if (val("#a-new") !== val("#a-rep")) { fail(new Error("The two new passwords are not the same.")); return; }
        try { await api("/auth/password", { method: "POST", body: { current: val("#a-cur"), new: val("#a-new") } }); toast("Password changed."); await render(); }
        catch (err) { fail(err); }
      };
      $("#a-days").onchange = async (e) => { try { await api("/auth/session", { method: "POST", body: { days: Number(e.target.value) } }); toast("Saved. It applies from the next sign-in."); } catch (err) { fail(err); } };
      $("#signout-all").onclick = async () => {
        if (!await modal({ title: "Sign out all browsers?", body: "Everyone, including you, has to sign in again.", ok: "Sign out all" })) return;
        await api("/auth/signout-all", { method: "POST" }); showLogin();
      };
      return;
    }
    if (tab === "s3") {
      $("#ca-file").onchange = async (e) => { const f = e.target.files[0]; if (f) $("#s3-ca").value = await f.text(); };
      const values = () => sec("#s3-secret", { endpoint: val("#s3-endpoint").trim(), bucket: val("#s3-bucket").trim(), root: val("#s3-root").trim(),
        access_key: val("#s3-key").trim(), region: val("#s3-region").trim(), addressing: val("#s3-style"), verify_tls: $("#s3-verify").checked, ca_pem: val("#s3-ca") }, "secret_key");
      $("#save").onclick = () => saveSection("s3", values(), "Saved.");
      $("#test").onclick = async () => {
        await api("/settings/s3", { method: "PUT", body: values() }).then((r) => { s.s3 = r; });
        try { const r = await api("/settings/test/s3", { method: "POST" }); note = { ok: true, text: `Saved and tested · listed ${r.files} files · wrote and deleted a test object` }; refreshFoot(); }
        catch (err) { note = { ok: false, text: err.message }; }
        await render();
      };
    }
    if (tab === "judge") {
      const values = () => sec("#j-key", { url: val("#j-url").trim(), model: val("#j-model").trim(), context_tokens: num("#j-ctx"), parallel: num("#j-par"),
        temperature: num("#j-temp"), timeout: num("#j-timeout"), vision: $("#j-vision").checked }, "api_key");
      $("#save").onclick = () => saveSection("judge", values(), "Saved.");
      $("#test").onclick = async () => {
        await api("/settings/judge", { method: "PUT", body: values() }).then((r) => { s.judge = r; });
        try {
          const r = await api("/settings/test/judge", { method: "POST" });
          note = { ok: r.vision !== false, text: `Saved and tested · answered in ${fmtSec(r.elapsed)}${r.vision === true ? " · image input accepted" : r.vision === false ? ` · image input failed: ${r.vision_error}` : ""}` };
        } catch (err) { note = { ok: false, text: err.message }; }
        await render();
      };
    }
    if (tab === "vlm") {
      $("#copy").onclick = async () => { try { await navigator.clipboard.writeText(s.proxy_url); toast("Copied."); } catch (e) { window.getSelection().selectAllChildren($("#proxy-url")); } };
      $("#save").onclick = () => saveSection("vlm", sec("#v-key", { upstream: val("#v-up").trim(), key_mode: val("#v-mode"), timeout: num("#v-timeout") }, "api_key"), "Saved.");
      $("#clear").onclick = async () => {
        if (!await modal({ title: "Clear the VLM cache?", body: "The next run sends every image to the VLM again.", ok: "Clear cache", danger: true })) return;
        const r = await api("/vlm-cache/clear", { method: "POST" }); note = { ok: true, text: `Removed ${r.removed} stored answers.` }; await render();
      };
    }
    if (tab === "eval") {
      $("#save").onclick = () => saveSection("eval", { image_tag: val("#e-tag").trim(), change_threshold: num("#e-thr"), fuzzy_threshold: num("#e-fuzzy") }, "Saved.");
    }
    if (tab === "storage") {
      $("#save").onclick = () => saveSection("storage", { keep_days: num("#keep") }, "Saved.");
      $("#cleanup").onclick = async () => {
        await api("/settings/storage", { method: "PUT", body: { keep_days: num("#keep") } });
        const r = await api("/storage/cleanup", { method: "POST" }); note = { ok: true, text: `Removed ${r.removed} old runs.` }; await render();
      };
    }
  }

  await render();
};
