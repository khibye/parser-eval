"""Self-contained HTML report for one comparison."""

import time
from html import escape

from . import compare

CSS = """
:root{--bg:#F3F4F7;--card:#fff;--ink:#151922;--muted:#586070;--line:#DEE2E8;--soft:#EDEFF3;--accent:#2650B8;
--good:#0B6247;--goodbg:#E3F4EC;--bad:#9A3A0B;--badbg:#FCEBDF;--warn:#7A5300;--warnbg:#FFF3D6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Arial,sans-serif}
main{max-width:1200px;margin:0 auto;padding:32px 20px 64px;display:flex;flex-direction:column;gap:24px}
h1{margin:0;font-size:26px}h2{margin:0 0 12px;font-size:17px}.muted{color:var(--muted)}
.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.tile .l{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
.tile .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:18px 20px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--soft);vertical-align:top}
th{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);background:#F7F8FA}
td.n,th.n{text-align:right}.up{color:var(--good)}.down{color:var(--bad)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px}
.p-improved{background:var(--goodbg);color:var(--good)}.p-regressed{background:var(--badbg);color:var(--bad)}
.p-unchanged{background:#E9EBEF}.p-review{background:var(--warnbg);color:var(--warn)}
.file{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}
.diff{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--line);border-radius:6px;overflow:hidden;
font-size:13px;margin-top:8px}.diff div{padding:1px 8px;white-space:pre-wrap;overflow-wrap:anywhere;unicode-bidi:plaintext}
.del{background:var(--badbg)}.add{background:var(--goodbg)}.chg{background:#FFF7E0}.skip{grid-column:1/-1;
text-align:center;color:var(--muted);background:#F7F8FA}
ul{margin:6px 0 0;padding-left:18px}
"""


def _fmt(v, suffix=""):
    return "–" if v is None else f"{v}{suffix}"


def _pct(a, total):
    return f"{a / total * 100:.1f}%" if total else "–"


def _diff_html(rows: list[dict], limit: int = 80) -> str:
    out, shown = [], 0
    for r in rows:
        if shown >= limit:
            out.append('<div class="skip">more changes not shown</div>')
            break
        k = r["kind"]
        if k == "skip":
            out.append(f'<div class="skip">{r["count"]} unchanged lines</div>')
            continue
        cls_a = {"del": "del", "change": "chg"}.get(k, "")
        cls_b = {"add": "add", "change": "chg"}.get(k, "")
        out.append(f'<div class="{cls_a}">{escape(r["a"] or "")}</div><div class="{cls_b}">{escape(r["b"] or "")}</div>')
        shown += 1
    return '<div class="diff">' + "".join(out) + "</div>"


def build(run: dict, a: str, b: str) -> str:
    s = compare.summary(run, a, b)
    names = {p["id"]: p["name"] for p in run["parsers"]}
    na, nb = escape(names.get(a, a)), escape(names.get(b, b))
    c, t, e, j = s["checks"], s["time"], s["errors"], s["judge"]
    rows = s["rows"]
    started = time.strftime("%d %b %Y %H:%M", time.localtime(run.get("started") or run["created"]))
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
             f"content='width=device-width,initial-scale=1'><title>{escape(run['name'])}</title>"
             f"<style>{CSS}</style></head><body><main>",
             f"<header><h1>{escape(run['name'])}</h1><p class='muted mono'>{escape(run['id'])} · "
             f"{escape(run['dataset'])} · {len(run['files'])} files · {started} · VLM {escape(run['vlm_mode'])}"
             f"</p><p>Baseline A: <b class='mono'>{na}</b> · Candidate B: <b class='mono'>{nb}</b></p></header>",
             "<div class='tiles'>",
             f"<div class='tile'><div class='l'>Checks passed</div><div class='v'>{_pct(c['b'], c['total'])}</div>"
             f"<div class='muted'>B {c['b']} / {c['total']} · A {c['a']} ({_pct(c['a'], c['total'])})</div></div>",
             f"<div class='tile'><div class='l'>Files</div><div class='v'><span class='up'>▲ {s['counts']['improved']}"
             f"</span> <span class='down'>▼ {s['counts']['regressed']}</span></div><div class='muted'>"
             f"{s['counts']['unchanged']} unchanged</div></div>",
             f"<div class='tile'><div class='l'>Needs review</div><div class='v'>{s['counts']['needs_review']}</div>"
             f"<div class='muted'>changed &gt;{s['threshold']:g}%, same checks</div></div>",
             f"<div class='tile'><div class='l'>Judge picks</div><div class='v'>B {j['B']} · A {j['A']}</div>"
             f"<div class='muted'>{j['tie']} ties · {j['disagree']} disagree</div></div>",
             f"<div class='tile'><div class='l'>Median time</div><div class='v'>{_fmt(t['b_median'], ' s')}</div>"
             f"<div class='muted'>A {_fmt(t['a_median'], ' s')} · p95 {_fmt(t['b_p95'], ' s')} vs "
             f"{_fmt(t['a_p95'], ' s')}</div></div>",
             f"<div class='tile'><div class='l'>Errors</div><div class='v'>{e['b']}</div>"
             f"<div class='muted'>A had {e['a']}</div></div></div>"]

    parts.append("<section><h2>By file type</h2><div class='scroll'><table><tr><th>Type</th><th class='n'>Files</th>"
                 "<th class='n'>Checks A</th><th class='n'>Checks B</th><th class='n'>Δ</th><th class='n'>▲</th>"
                 "<th class='n'>▼</th><th class='n'>Median time A → B</th><th class='n'>Errors A → B</th></tr>")
    for r in s["by_type"]:
        d = r["b"] - r["a"]
        cls = "up" if d > 0 else ("down" if d < 0 else "muted")
        parts.append(f"<tr><td>{escape(r['type'])}</td><td class='n'>{r['files']}</td><td class='n'>{r['a']} / "
                     f"{r['total']}</td><td class='n'>{r['b']} / {r['total']}</td><td class='n {cls}'>{f'{d:+d}' if d else '0'}</td>"
                     f"<td class='n up'>{r['up'] or '–'}</td><td class='n down'>{r['down'] or '–'}</td>"
                     f"<td class='n'>{_fmt(r['a_median'], ' s')} → {_fmt(r['b_median'], ' s')}</td>"
                     f"<td class='n'>{r['a_err']} → {r['b_err']}</td></tr>")
    parts.append("</table></div></section>")

    def file_section(title: str, items: list[dict], with_diff: bool) -> None:
        if not items:
            return
        parts.append(f"<section><h2>{title} · {len(items)}</h2>")
        for r in items:
            parts.append(f"<div class='file'><b class='mono'>{escape(r['key'])}</b> <span class='muted'>"
                         f"{escape(r['type'])} · checks {r.get('a_passed', '–')}/{r.get('a_eval', '–')} → "
                         f"{r.get('b_passed', '–')}/{r.get('b_eval', '–')} · text changed "
                         f"{_fmt(r.get('change'), '%')}</span>")
            if r.get("judge_label"):
                parts.append(f"<div>Judge: {escape(r['judge_label'])}</div>")
            if r.get("notes"):
                parts.append("<ul>" + "".join(f"<li>{escape(n)}</li>" for n in r["notes"]) + "</ul>")
            if with_diff:
                detail = compare.file_detail(run, a, b, r["fid"], only_changes=True)
                if detail.get("diff"):
                    j_ = detail.get("judge")
                    if j_ and j_.get("reason"):
                        parts.append(f"<p class='muted'>{escape(j_['reason'])}</p>")
                    parts.append(_diff_html(detail["diff"]))
            parts.append("</div>")
        parts.append("</section>")

    file_section("Regressions", [r for r in rows if r["status"] == "regressed"], True)
    file_section("Needs review", [r for r in rows if r.get("needs_review")], True)
    file_section("Improvements", [r for r in rows if r["status"] == "improved"], False)

    parts.append("<section><h2>All files</h2><div class='scroll'><table><tr><th>File</th><th>Type</th>"
                 "<th>Result</th><th class='n'>Checks A → B</th><th class='n'>Text changed</th>"
                 "<th class='n'>Time A → B</th><th>Judge</th></tr>")
    for r in rows:
        status = "review" if r.get("needs_review") else r["status"]
        parts.append(f"<tr><td class='mono'>{escape(r['key'])}</td><td>{escape(r['type'])}</td>"
                     f"<td><span class='pill p-{status}'>{status}</span></td>"
                     f"<td class='n'>{r.get('a_passed', '–')}/{r.get('a_eval', '–')} → "
                     f"{r.get('b_passed', '–')}/{r.get('b_eval', '–')}</td>"
                     f"<td class='n'>{_fmt(r.get('change'), '%')}</td>"
                     f"<td class='n'>{_fmt(r.get('a_time'), ' s')} → {_fmt(r.get('b_time'), ' s')}</td>"
                     f"<td>{escape(r.get('judge_label') or '')}</td></tr>")
    parts.append("</table></div></section>")
    parts.append(f"<p class='muted'>Generated by parser-eval on {time.strftime('%d %b %Y %H:%M')}. "
                 "A file is regressed when B passes fewer of its checks than A, or B failed where A did not.</p>")
    parts.append("</main></body></html>")
    return "".join(parts)
