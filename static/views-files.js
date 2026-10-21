/* Files & tests, and the test editor for one file. */
"use strict";

/* ---------- Files & tests ---------- */

App.views.files = async function (main, params) {
  let data;
  try {
    data = await api(`/files?${qs({ prefix: params.prefix })}`);
  } catch (err) {
    main.innerHTML = `<div class="head"><div class="t"><h1>Files &amp; tests</h1></div></div>
      <div class="note bad">${esc(err.message)} <a href="#/settings?tab=s3">Open settings</a></div>`;
    return;
  }
  let checkFilter = params.checks || "all";
  let typeFilter = params.type || "";
  let search = "";

  const rowsFor = () => data.files.filter((f) => {
    if (checkFilter === "drafts" && !f.drafts) return false;
    if (checkFilter === "none" && (f.approved || f.drafts)) return false;
    if (checkFilter === "changed" && !f.changed) return false;
    if (typeFilter && f.type !== typeFilter) return false;
    if (search && !f.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  function crumbs() {
    const parts = [{ label: (App.settings?.s3?.bucket || "bucket") + (data.root ? "/" + data.root.replace(/\/$/, "") : ""), prefix: data.root }];
    const rest = data.prefix.slice(data.root.length).split("/").filter(Boolean);
    let acc = data.root;
    for (const p of rest) { acc += p + "/"; parts.push({ label: p, prefix: acc }); }
    return parts.map((p, i) => i === parts.length - 1 ? `<b>${esc(p.label)}</b>`
      : `<a href="#/files?${qs({ prefix: p.prefix })}">${esc(p.label)}</a>`).join(` <span class="muted">/</span> `) + ` <span class="muted">/</span>`;
  }

  function rows() {
    const list = rowsFor();
    if (!data.files.length) return `<tr><td colspan="7" class="empty">No files in this folder yet. Drop files here or use Upload files.</td></tr>`;
    if (!list.length) return `<tr><td colspan="7" class="empty">No files match the filters.</td></tr>`;
    return list.map((f) => {
      const chips = [];
      if (f.approved) chips.push(`<span class="tag">${f.approved} approved</span>`);
      if (f.drafts) chips.push(`<span class="tag blue">${f.drafts} drafts</span>`);
      if (!f.approved && !f.drafts) chips.push(`<span class="small muted">No checks yet</span>`);
      if (f.changed) chips.push(`<span class="tag" style="background:var(--warn-bg);color:var(--warn)">File changed · recheck</span>`);
      const last = f.last ? (f.last.ok ? `${f.last.passed} / ${f.last.evaluated} · ${esc(f.last.parser)}` : `<span class="bad">error · ${esc(f.last.parser)}</span>`) : "–";
      const act = f.changed ? "Recheck" : f.drafts ? "Review" : (f.approved ? "Edit checks" : "Add checks");
      return `<tr class="click" data-key="${esc(f.key)}"><td class="key">${esc(f.name)}</td><td class="mono small muted">${esc(f.type)}</td>
        <td class="r small">${fmtBytes(f.size)}</td><td><div class="row">${chips.join("")}</div></td>
        <td class="small">${last}</td><td class="small muted">${fmtWhen(f.modified)}</td>
        <td class="r"><a class="btn sm" href="#/tests?${qs({ key: f.key })}">${act}</a></td></tr>`;
    }).join("");
  }

  function render() {
    const types = [...new Set(data.files.map((f) => f.type))].sort();
    const counts = {
      all: data.files.length, drafts: data.files.filter((f) => f.drafts).length,
      none: data.files.filter((f) => !f.approved && !f.drafts).length, changed: data.files.filter((f) => f.changed).length,
    };
    main.innerHTML = `
      <div class="head"><div class="t"><h1>Files &amp; tests</h1>
        <p>Files live in S3. Each file's checks are saved on the PVC and tied to that version of the file. Drop files anywhere on this page to upload them to the open folder.</p></div>
        <div class="row"><button class="btn" id="samples">Add sample set</button><button class="btn" id="new-folder">New folder</button>
          <label class="btn primary" for="upload-input" style="cursor:pointer">Upload files</label>
          <input id="upload-input" type="file" multiple class="sr"></div></div>
      <div class="row between">
        <div class="row"><nav aria-label="Folder path" class="mono small">${crumbs()}</nav>
          ${data.folders.map((f) => `<a class="btn sm" href="#/files?${qs({ prefix: f.prefix })}"><span aria-hidden="true">▸</span> <span class="mono">${esc(f.name)}</span></a>`).join("")}</div>
        <div class="row"><label class="sr" for="f-search">Search files</label><input id="f-search" type="search" placeholder="Search" style="width:170px">
          <label class="small" for="f-checks">Checks</label><select id="f-checks" style="width:auto">
            ${[["all", "All"], ["drafts", "Drafts to review"], ["none", "No checks"], ["changed", "File changed"]].map(([k, l]) => `<option value="${k}" ${checkFilter === k ? "selected" : ""}>${l} · ${counts[k]}</option>`).join("")}</select>
          <label class="small" for="f-type">Type</label><select id="f-type" style="width:auto"><option value="">All types</option>
            ${types.map((t) => `<option ${t === typeFilter ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div></div>
      <section class="card"><div class="tbl-wrap tall"><table class="tbl"><thead><tr><th>File</th><th>Type</th><th class="r">Size</th>
        <th>Checks</th><th>Last run</th><th>Updated</th><th></th></tr></thead><tbody id="file-rows">${rows()}</tbody></table></div></section>
      <p class="small muted" style="margin:0">s3://${esc(App.settings?.s3?.bucket || "")}/${esc(data.prefix)} · ${data.files.length} files, ${data.folders.length} folders</p>`;
    $("#f-search").oninput = (e) => { search = e.target.value; $("#file-rows").innerHTML = rows(); };
    $("#f-checks").onchange = (e) => { checkFilter = e.target.value; setParam("checks", checkFilter === "all" ? "" : checkFilter); $("#file-rows").innerHTML = rows(); };
    $("#f-type").onchange = (e) => { typeFilter = e.target.value; setParam("type", typeFilter); $("#file-rows").innerHTML = rows(); };
    $("#upload-input").onchange = (e) => upload(e.target.files);
    $("#new-folder").onclick = async () => {
      const name = await modal({ title: "New folder", ok: "Create", input: { label: `Folder inside ${data.prefix || "/"}`, mono: true } });
      if (!name) return;
      try { const r = await api("/files/folder", { method: "POST", body: { prefix: data.prefix, name } }); go(`#/files?${qs({ prefix: r.prefix })}`); }
      catch (err) { fail(err); }
    };
    $("#samples").onclick = async () => {
      const samples = await api("/samples");
      const prefix = await modal({ title: "Add the sample set",
        body: `${samples.length} synthetic files (PDF, scanned PDF, DOCX, DOC, PPTX, PPT, EML and images) with ready-made checks, built around problems seen with Hebrew OCR and image handling. They are uploaded to S3 and their checks are saved.`,
        ok: "Upload samples", input: { label: "Folder", value: data.root + "synthetic/", mono: true } });
      if (!prefix) return;
      try { const r = await api("/samples/import", { method: "POST", body: { prefix } }); toast(`${r.imported} sample files added.`); go(`#/files?${qs({ prefix: r.prefix })}`); }
      catch (err) { fail(err); }
    };
  }

  async function upload(fileList) {
    if (!fileList || !fileList.length) return;
    const form = new FormData();
    form.append("prefix", data.prefix);
    for (const f of fileList) form.append("files", f, f.name);
    toast(`Uploading ${fileList.length} file${fileList.length > 1 ? "s" : ""}…`);
    try {
      await api("/files/upload", { method: "POST", form });
      toast("Uploaded.");
      data = await api(`/files?${qs({ prefix: data.prefix })}`);
      render();
    } catch (err) { fail(err); }
  }

  main.ondragover = (e) => { e.preventDefault(); main.classList.add("dropzone"); };
  main.ondragleave = (e) => { if (e.target === main) main.classList.remove("dropzone"); };
  main.ondrop = (e) => { e.preventDefault(); main.classList.remove("dropzone"); upload(e.dataTransfer.files); };
  main.onclick = (e) => {
    if (e.target.closest("a, button, input, select, label")) return;
    const tr = e.target.closest("tr[data-key]");
    if (tr) go(`#/tests?${qs({ key: tr.dataset.key })}`);
  };
  if (!App.settings) App.settings = await api("/settings");
  render();
};

/* ---------- Test editor ---------- */

const CHECK_KINDS = [
  ["contains", "Contains"], ["not_contains", "Not contains"], ["order", "Order"], ["appears_once", "Appears once"],
  ["image_blocks", "Image blocks"], ["image_contains", "Image contains"], ["table_cell", "Table cell"], ["question", "Question"],
];
const KIND_HELP = {
  contains: "This text must appear in the output.",
  not_contains: "This text must not appear, for example a leftover image placeholder.",
  order: "These parts must appear in this order. One per line.",
  appears_once: "This text must appear exactly once. Catches duplicated text.",
  image_blocks: "How many image analysis blocks the output must have.",
  image_contains: "A given image block must contain this text.",
  table_cell: "One table row (or line) must hold both the label and the value.",
  question: "The judge answers this question from the output only, and checks the answer.",
};

function describeCheck(c) {
  switch (c.kind) {
    case "contains": case "not_contains": case "appears_once": return `“${c.text || ""}”`;
    case "order": return (c.items || []).map((i) => `“${i}”`).join(" → ");
    case "image_blocks": return `${c.op === "min" ? "at least" : "exactly"} ${c.count}`;
    case "image_contains": return `image ${c.index}: “${c.text || ""}”`;
    case "table_cell": return `row “${c.row || ""}”, value “${c.value || ""}”`;
    case "question": return `${c.question || ""} → ${c.answer || ""}`;
    default: return JSON.stringify(c);
  }
}

App.views.tests = async function (main, params) {
  const key = params.key;
  if (!key) { go("#/files"); return; }
  let [data, outputs, parsers] = await Promise.all([api(`/checks?${qs({ key })}`), api(`/file-outputs?${qs({ key })}`), api("/parsers")]);
  let outId = outputs[0]?.id || "";
  let text = "";
  let evals = {};
  let leftTab = params.left || "text";
  let page = 1, pages = 1;
  let editing = null; // {check, isNew}
  const canPreview = data.type === "Images" || key.toLowerCase().endsWith(".pdf");

  async function loadOutput() {
    if (!outId) { text = ""; return; }
    try { text = (await api(`/file-output?${qs({ key, id: outId })}`)).text || ""; } catch (e) { text = ""; }
  }
  async function evaluate() {
    if (!text) { evals = {}; return; }
    try { evals = await api("/checks/evaluate", { method: "POST", body: { text, checks: data.checks } }); } catch (e) { evals = {}; }
  }
  async function save(checks, msg) {
    try {
      const saved = await api("/checks", { method: "PUT", body: { key, checks } });
      data = { ...data, ...saved, changed: false, etag: saved.etag };
      await evaluate();
      if (msg) toast(msg);
    } catch (err) { fail(err); }
    render();
  }

  function leftPane() {
    if (leftTab === "original") {
      if (!canPreview) return `<div class="empty">No preview for ${esc(data.type)} files. <a href="/api/files/raw?${qs({ key })}">Download the file</a> to look at it.</div>`;
      return `<div style="padding:16px;background:#E9EBEF;display:flex;flex-direction:column;align-items:center;gap:10px">
        ${pages > 1 ? `<div class="row"><button class="btn sm" id="pg-prev" ${page <= 1 ? "disabled" : ""}>Previous</button><span class="small">Page ${page} of ${pages}</span>
          <button class="btn sm" id="pg-next" ${page >= pages ? "disabled" : ""}>Next</button></div>` : ""}
        <img id="preview" class="preview-img" alt="Page ${page} of ${esc(basename(key))}" src="/api/files/preview?${qs({ key, page })}"></div>`;
    }
    if (!outputs.length) return `<div class="empty">No output for this file yet. Choose a parser above and press Parse now.</div>`;
    return `<pre class="raw" id="out-text" dir="${textDir(text)}">${esc(text) || `<span class="muted">The output is empty.</span>`}</pre>`;
  }

  function checkRow(c, draft) {
    const ev = evals[c.id];
    const mark = draft ? "" : ev ? checkMark(ev.passed) : `<span class="mark muted">·</span>`;
    return `<div class="chk ${draft ? "draft" : ""}" data-id="${esc(c.id)}">
      ${draft ? "" : mark}
      <div class="body"><span class="kind">${esc(c.kind.replace("_", " "))}${c.match === "exact" ? " · exact" : ""}</span>
        <span class="txt he">${esc(describeCheck(c))}</span>${ev && !draft && ev.passed === false ? `<span class="det">${esc(ev.detail)}</span>` : ""}</div>
      <div class="row" style="flex-wrap:nowrap">
        ${draft ? `<button class="btn sm primary" data-approve="${esc(c.id)}">Approve</button>` : ""}
        <button class="btn sm" data-edit="${esc(c.id)}">Edit</button>
        <button class="btn sm ${draft ? "" : "danger"}" data-del="${esc(c.id)}" aria-label="${draft ? "Discard" : "Delete"} check">${draft ? "Discard" : "Delete"}</button></div></div>`;
  }

  function editForm() {
    const c = editing.check;
    const f = (id, label, value, opts = {}) => `<div class="field ${opts.wide ? "wide" : ""}"><label for="${id}">${label}</label>
      <input id="${id}" type="${opts.type || "text"}" value="${esc(value ?? "")}" dir="auto" ${opts.min !== undefined ? `min="${opts.min}"` : ""}></div>`;
    let fields = "";
    if (["contains", "not_contains", "appears_once", "image_contains"].includes(c.kind)) {
      if (c.kind === "image_contains") fields += f("c-index", "Image number", c.index ?? 1, { type: "number", min: 1 });
      fields += f("c-text", c.kind === "not_contains" ? "Text that must not appear" : "Text", c.text, { wide: true });
      if (c.kind === "contains" || c.kind === "image_contains") fields += `<div class="field"><label for="c-match">Match</label><select id="c-match">
        <option value="fuzzy" ${c.match !== "exact" ? "selected" : ""}>Close match (${App.settings.eval.fuzzy_threshold}% similar)</option>
        <option value="exact" ${c.match === "exact" ? "selected" : ""}>Exact, after normalizing</option></select></div>`;
    } else if (c.kind === "order") {
      fields += `<div class="field wide"><label for="c-items">Parts, one per line, in the order they must appear</label>
        <textarea id="c-items" rows="5" dir="auto" style="font-family:var(--hebrew);font-size:14px">${esc((c.items || []).join("\n"))}</textarea></div>`;
    } else if (c.kind === "image_blocks") {
      fields += `<div class="field"><label for="c-op">Rule</label><select id="c-op"><option value="eq" ${c.op !== "min" ? "selected" : ""}>Exactly</option>
        <option value="min" ${c.op === "min" ? "selected" : ""}>At least</option></select></div>` + f("c-count", "Number of image blocks", c.count ?? 1, { type: "number", min: 0 });
    } else if (c.kind === "table_cell") {
      fields += f("c-row", "Row label", c.row) + f("c-value", "Value in the same row", c.value);
    } else if (c.kind === "question") {
      fields += f("c-question", "Question", c.question, { wide: true }) + f("c-answer", "Expected answer", c.answer, { wide: true });
    }
    return `<div class="stack" style="padding:16px">
      <div class="row" role="radiogroup" aria-label="Check type">${CHECK_KINDS.map(([k, l]) => `<button type="button" class="chip ${c.kind === k ? "on" : ""}"
        role="radio" aria-checked="${c.kind === k}" data-kind="${k}">${l}</button>`).join("")}</div>
      <p class="small muted" style="margin:0">${KIND_HELP[c.kind]}</p>
      <div class="fields">${fields}</div>
      <p class="small muted" style="margin:0">Before comparing, niqqud, direction marks, quotes and extra spaces are normalized.</p>
      <div class="row" style="justify-content:flex-end"><button class="btn" id="c-cancel">Cancel</button>
        <button class="btn primary" id="c-save">${editing.isNew ? "Add check" : "Save check"}</button></div></div>`;
  }

  function readForm() {
    const c = { ...editing.check };
    const v = (id) => $(id) ? $(id).value : undefined;
    if (["contains", "not_contains", "appears_once", "image_contains"].includes(c.kind)) {
      c.text = v("#c-text").trim();
      if ($("#c-match")) c.match = v("#c-match");
      if (c.kind === "image_contains") c.index = Number(v("#c-index")) || 1;
      if (!c.text) throw new Error("Write the text to look for.");
    } else if (c.kind === "order") {
      c.items = v("#c-items").split("\n").map((s) => s.trim()).filter(Boolean);
      if (c.items.length < 2) throw new Error("Write at least two parts, one per line.");
    } else if (c.kind === "image_blocks") {
      c.op = v("#c-op"); c.count = Number(v("#c-count")) || 0;
    } else if (c.kind === "table_cell") {
      c.row = v("#c-row").trim(); c.value = v("#c-value").trim();
      if (!c.row || !c.value) throw new Error("Write both the row label and the value.");
    } else if (c.kind === "question") {
      c.question = v("#c-question").trim(); c.answer = v("#c-answer").trim();
      if (!c.question || !c.answer) throw new Error("Write the question and the expected answer.");
    }
    return c;
  }

  function rightPane() {
    if (editing) return editForm();
    const drafts = data.checks.filter((c) => c.status === "draft");
    const approved = data.checks.filter((c) => c.status !== "draft");
    const passing = approved.filter((c) => evals[c.id]?.passed === true).length;
    return `${data.changed ? `<div class="note warn" style="margin:12px 14px 0">This file changed in S3 after its checks were saved. Look over the checks, then
        <button class="linkbtn" id="mark-current">mark them as current</button>.</div>` : ""}
      ${drafts.length ? `<div class="drafts"><p class="small" style="margin:0 14px 8px;color:var(--accent-ink);font-weight:500">${drafts.length} drafts from the judge.
        They are built from the output, so compare each one with the original before approving.
        <button class="linkbtn" id="approve-all">Approve all</button></p>${drafts.map((c) => checkRow(c, true)).join("")}</div>` : ""}
      ${approved.length ? `${text ? `<p class="small muted" style="margin:0;padding:8px 14px">Against the selected output: ${passing} of ${approved.length} pass.</p>` : ""}
        ${approved.map((c) => checkRow(c, false)).join("")}`
        : `<div class="empty">No approved checks yet. Add one, or let the judge suggest some from the output.</div>`}`;
  }

  function render() {
    const approvedN = data.checks.filter((c) => c.status !== "draft").length;
    const draftsN = data.checks.length - approvedN;
    main.innerHTML = `
      <div class="head"><div class="t"><a class="back" href="#/files?${qs({ prefix: key.slice(0, key.lastIndexOf("/") + 1) })}">← Files &amp; tests</a>
        <h1 class="file">${esc(key)}</h1>
        <p class="small">${esc(data.type)} · ${fmtBytes(data.size)} · ${approvedN} approved checks${draftsN ? `, ${draftsN} drafts` : ""}${data.exists === false ? ` · <span class="bad">not found in S3</span>` : ""}</p></div>
        <div class="row" style="align-items:flex-end">
          <div class="field"><label for="out-src">Output from</label><select id="out-src" class="mono" style="width:auto;max-width:360px">
            ${outputs.length ? outputs.map((o) => `<option value="${esc(o.id)}" ${o.id === outId ? "selected" : ""}>${esc(o.label)}</option>`).join("") : `<option value="">No output yet</option>`}</select></div>
          <div class="field"><label for="parse-with">Parse now with</label><div class="row" style="flex-wrap:nowrap">
            <select id="parse-with" class="mono" style="width:auto">${parsers.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select>
            <button class="btn" id="parse-now" ${parsers.length ? "" : "disabled"}>Parse now</button></div></div>
          <button class="btn danger" id="del-file" title="Delete the file from S3">Delete file</button></div></div>
      <div class="split">
        <section class="card" style="flex:1 1 440px;display:flex;flex-direction:column;overflow:hidden">
          <div class="row between" style="padding-right:12px;border-bottom:1px solid var(--line)">
            <div class="tabs" role="tablist" style="border:0">${tabsHtml([["text", "Extracted text"], ["original", "Original"]], leftTab, "left")}</div>
            <span class="small muted" id="sel-hint">${leftTab === "text" && text ? "Select text to make a check from it" : ""}</span></div>
          <div class="tbl-wrap" style="max-height:calc(100vh - 250px)">${leftPane()}</div></section>
        <section class="card" style="flex:1 1 440px;display:flex;flex-direction:column;overflow:hidden">
          <div class="ch"><h2>${editing ? (editing.isNew ? "Add a check" : "Edit check") : "Checks"}</h2>
            ${editing ? "" : `<div class="row"><button class="btn sm soft" id="suggest" ${text ? "" : "disabled"} title="${text ? "" : "Pick an output first"}">Suggest checks</button>
              <button class="btn sm primary" id="add-check">Add check</button></div>`}</div>
          <div class="tbl-wrap" style="max-height:calc(100vh - 250px)">${rightPane()}</div></section></div>`;
    bind();
  }

  function startEdit(check, isNew) { editing = { check: JSON.parse(JSON.stringify(check)), isNew }; render(); }

  function bind() {
    $("#out-src").onchange = async (e) => { outId = e.target.value; await loadOutput(); await evaluate(); render(); };
    $("#parse-now").onclick = async () => {
      const btn = $("#parse-now"); btn.disabled = true; btn.innerHTML = `<span class="spin"></span> Parsing`;
      try {
        const res = await api("/parse-now", { method: "POST", body: { key, parser_id: $("#parse-with").value } });
        if (!res.ok) toast(`The parser returned an error: ${res.error}`, true);
        outputs = await api(`/file-outputs?${qs({ key })}`); outId = res.id; leftTab = "text";
        await loadOutput(); await evaluate();
      } catch (err) { fail(err); }
      render();
    };
    $("#del-file").onclick = async () => {
      if (!await modal({ title: "Delete this file from S3?", body: `${esc(key)} is removed from the bucket. Its checks stay on the PVC in case you upload it again.`, ok: "Delete file", danger: true })) return;
      try { await api(`/files?${qs({ key })}`, { method: "DELETE" }); toast("File deleted."); go(`#/files?${qs({ prefix: key.slice(0, key.lastIndexOf("/") + 1) })}`); }
      catch (err) { fail(err); }
    };
    $$("[data-left]", main).forEach((t) => t.onclick = () => { leftTab = t.dataset.left; setParam("left", leftTab); render(); });
    if ($("#preview")) $("#preview").onload = async () => {
      if (pages === 1 && key.toLowerCase().endsWith(".pdf")) {
        try { const r = await api(`/files/preview?${qs({ key, page })}`, { raw: true }); const n = Number(r.headers.get("X-Pages")) || 1; if (n !== pages) { pages = n; render(); } } catch (e) { /* ignore */ }
      }
    };
    if ($("#pg-prev")) $("#pg-prev").onclick = () => { page -= 1; render(); };
    if ($("#pg-next")) $("#pg-next").onclick = () => { page += 1; render(); };
    if ($("#out-text")) $("#out-text").onmouseup = () => {
      const sel = String(window.getSelection() || "").trim();
      const hint = $("#sel-hint");
      if (sel && sel.length < 400 && !editing) {
        hint.innerHTML = `<button class="btn sm soft" id="use-sel">Make a “contains” check from the selection</button>`;
        $("#use-sel").onclick = () => startEdit({ kind: "contains", text: sel.replace(/\s+/g, " "), match: "fuzzy", status: "approved" }, true);
      }
    };
    if ($("#suggest")) $("#suggest").onclick = async () => {
      const btn = $("#suggest"); btn.disabled = true; btn.innerHTML = `<span class="spin"></span> Asking the judge`;
      try { const r = await api("/checks/suggest", { method: "POST", body: { key, text } }); data = { ...data, ...r }; toast(`${r.added} drafts added.`); }
      catch (err) { fail(err); }
      render();
    };
    if ($("#add-check")) $("#add-check").onclick = () => startEdit({ kind: "contains", match: "fuzzy", status: "approved" }, true);
    if ($("#mark-current")) $("#mark-current").onclick = () => save(data.checks, "Checks marked as current for this version of the file.");
    if ($("#approve-all")) $("#approve-all").onclick = () => save(data.checks.map((c) => ({ ...c, status: "approved" })), "All drafts approved.");
    $$("[data-approve]", main).forEach((b) => b.onclick = () => save(data.checks.map((c) => c.id === b.dataset.approve ? { ...c, status: "approved" } : c), "Check approved."));
    $$("[data-del]", main).forEach((b) => b.onclick = () => save(data.checks.filter((c) => c.id !== b.dataset.del), "Check removed."));
    $$("[data-edit]", main).forEach((b) => b.onclick = () => startEdit(data.checks.find((c) => c.id === b.dataset.edit), false));
    $$("[data-kind]", main).forEach((b) => b.onclick = () => { editing.check.kind = b.dataset.kind; render(); });
    if ($("#c-cancel")) $("#c-cancel").onclick = () => { editing = null; render(); };
    if ($("#c-save")) $("#c-save").onclick = () => {
      let c;
      try { c = readForm(); } catch (err) { fail(err); return; }
      const wasNew = editing.isNew;
      editing = null;
      const checks = wasNew ? [...data.checks, c] : data.checks.map((x) => x.id === c.id ? c : x);
      save(checks, wasNew ? "Check added." : "Check saved.");
    };
  }

  if (!App.settings) App.settings = await api("/settings");
  await loadOutput();
  await evaluate();
  render();
};
