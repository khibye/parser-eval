"""Stand-ins for docintel and LiteLLM, for trying parser-eval without the real services.

    python tools/mock_services.py docintel --variant a --port 9201 --vlm http://localhost:8081/v1
    python tools/mock_services.py docintel --variant b --port 9202 --vlm http://localhost:8081/v1
    python tools/mock_services.py litellm --port 9300

The mock docintel answers like docling-serve (POST /v1/convert/file, text in document.md_content).
It extracts real text where it can, sends every embedded image to the VLM URL, and wraps the answer
in <image_analysis> blocks. Variant b differs on purpose so a comparison has something to show.
Scanned pages and standalone images have no text layer, so their "OCR" comes from the sample
manifest. This is only a test double, not a parser.
"""

import argparse
import base64
import email
import email.policy
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent
TRUTH: dict[str, list[str]] = {}
for item in json.loads((ROOT / "samples" / "manifest.json").read_text(encoding="utf-8")):
    lines = []
    for c in item["checks"]:
        if c["kind"] in ("contains", "appears_once") and c.get("text"):
            lines.append(c["text"])
        if c["kind"] == "order":
            lines.extend(c["items"])
        if c["kind"] == "table_cell":
            lines.append(f"| {c['row']} | {c['value']} |")
        if c["kind"] == "question":
            lines.append(c["answer"])
    TRUTH[Path(item["path"]).name] = list(dict.fromkeys(lines))


def make_docintel(variant: str, vlm_url: str) -> FastAPI:
    app = FastAPI()

    async def describe(data: bytes, mime: str = "image/png") -> str:
        body = {"model": "mock-vlm", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Describe the image."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}]}]}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(vlm_url.rstrip("/") + "/chat/completions", json=body,
                                      headers={"Authorization": "Bearer mock-key"})
            return r.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            return f"(VLM failed: {exc})"

    async def block(i: int, data: bytes, ocr: str = "") -> str:
        return f'<image_analysis index="{i}">\nOCR: {ocr}\nVLM: {await describe(data)}\n</image_analysis>'

    def truth(name: str, drop_last: bool) -> str:
        lines = TRUTH.get(name, [])
        if drop_last and lines:
            lines = lines[:-1]
        return "\n".join(lines)

    async def pdf_text(name: str, data: bytes) -> str:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        pages = []
        for i in range(len(pdf)):
            tp = pdf[i].get_textpage()
            pages.append(tp.get_text_bounded())
        text = "\n\n".join(pages).strip()
        if not text:  # scanned
            return truth(name, drop_last=(variant == "a"))
        if variant == "b" and "40" in name:
            pass
        return text

    async def docx_text(data: bytes) -> str:
        from docx import Document
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for t in doc.tables:
            for row in t.rows:
                parts.append("| " + " | ".join(c.text for c in row.cells) + " |")
        z = zipfile.ZipFile(io.BytesIO(data))
        imgs = [n for n in z.namelist() if n.startswith("word/media/")]
        for i, n in enumerate(imgs, 1):
            parts.append(await block(i, z.read(n), "מכירות לפי אזור" if i == 1 else ""))
        return "\n\n".join(parts)

    async def pptx_text(data: bytes) -> str:
        from pptx import Presentation
        prs = Presentation(io.BytesIO(data))
        out, idx, seen = [], 0, {}
        ocr_hints = {1: "מכירות לפי אזור · צפון 4.2 · מרכז 6.8", 2: "מחשב נייד 4,800 ₪", 6: "שעות פעילות המוקד"}
        for sno, slide in enumerate(prs.slides, 1):
            if variant == "b" and sno == 2 and len(prs.slides) == 7:
                continue  # the candidate loses slide 2 on purpose
            out.append(f"## Slide {sno}")
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    out.append(shape.text_frame.text)
                if shape.shape_type == 13:  # picture
                    idx += 1
                    blob = shape.image.blob
                    key = hashlib.sha1(blob).hexdigest()
                    if variant == "a" or key not in seen:
                        seen[key] = True
                        out.append(await block(idx, blob, ocr_hints.get(sno, "") if len(prs.slides) == 7 else ""))
        return "\n\n".join(out)

    async def eml_text(data: bytes) -> str:
        msg = email.message_from_bytes(data, policy=email.policy.default)
        out = [f"# {msg['subject']}"]
        body = msg.get_body(preferencelist=("plain",))
        if body:
            out.append(body.get_content())
        i = 0
        for part in msg.iter_attachments():
            payload = part.get_payload(decode=True)
            if part.get_content_type() == "application/pdf":
                out.append(await pdf_text(part.get_filename() or "att.pdf", payload))
            elif part.get_content_maintype() == "image":
                i += 1
                out.append(await block(i, payload, "מכירות לפי אזור" if variant == "b" else ""))
        return "\n\n".join(out)

    @app.get("/health")
    async def health():
        return {"status": "ok", "variant": variant}

    @app.post("/v1/convert/file")
    async def convert(request: Request, files: UploadFile = File(...)):
        form = await request.form()
        params = {k: form.getlist(k) for k in form.keys() if k != "files"}
        name = files.filename or "file"
        data = await files.read()
        ext = Path(name).suffix.lower()
        if ext == ".pdf":
            text = await pdf_text(name, data)
        elif ext == ".docx":
            text = await docx_text(data)
        elif ext == ".pptx":
            text = await pptx_text(data)
        elif ext == ".eml":
            text = await eml_text(data)
        elif ext in (".png", ".jpg", ".jpeg"):
            text = truth(name, drop_last=False) if variant == "b" else ""
        elif ext in (".doc", ".ppt") and variant == "a":
            return JSONResponse({"detail": "Unsupported format"}, status_code=415)
        elif ext == ".doc":
            text = truth(name, drop_last=False)
        elif ext == ".ppt":
            text = truth(name, drop_last=False)
        else:
            return JSONResponse({"detail": f"Unsupported format {ext}"}, status_code=415)
        return {"document": {"filename": name, "md_content": text}, "status": "success",
                "params_seen": params}

    return app


def make_litellm() -> FastAPI:
    app = FastAPI()

    def reply(content: str) -> dict:
        return {"id": "chatcmpl-mock", "object": "chat.completion", "created": 0, "model": "mock",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}

    @app.get("/v1/models")
    async def models():
        return {"data": [{"id": "mock-vlm"}, {"id": "mock-judge"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        msgs = body.get("messages", [])
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        user = msgs[-1]["content"]
        text = user if isinstance(user, str) else " ".join(p.get("text", "") for p in user if p.get("type") == "text")
        images = [] if isinstance(user, str) else [p for p in user if p.get("type") == "image_url"]
        if "You answer questions" in system:
            doc = re.search(r"<document>\n(.*)\n</document>", text, re.S)
            q = text.rsplit("Question:", 1)[-1].strip()
            content = doc.group(1) if doc else ""
            if "יישום" in q and "מנהלת מערכות המידע" in content:
                return reply("מנהלת מערכות המידע")
            if "פרקים" in q and "פרק 40" in content:
                return reply("40")
            if "סכום" in q and "4,956" in content:
                return reply("4,956 ₪")
            if "המעסיק" in q and "חברת דוגמה" in content:
                return reply("חברת דוגמה בע״מ")
            return reply("NOT FOUND")
        if "You grade answers" in system:
            data = json.loads(text)
            ok = data["expected"].replace(" ", "") in data["answer"].replace(" ", "")
            return reply(json.dumps({"correct": ok, "reason": "Same fact." if ok else "The answer does not match."}))
        if "Two parsers extracted text" in system:
            o1 = re.search(r"<output_1>\n(.*)\n</output_1>", text, re.S).group(1)
            o2 = re.search(r"<output_2>\n(.*)\n</output_2>", text, re.S).group(1)
            better = "1" if len(o1) > len(o2) * 1.05 else ("2" if len(o2) > len(o1) * 1.05 else "tie")
            return reply(json.dumps({"better": better, "reason": "Output %s keeps more of the text." % better
                                     if better != "tie" else "Both outputs hold the same content."}))
        if "You write test checks" in system:
            doc = re.search(r"<document>\n(.*)\n</document>", text, re.S)
            lines = [ln.strip() for ln in (doc.group(1) if doc else "").splitlines() if len(ln.strip()) > 8
                     and "<" not in ln][:3]
            return reply(json.dumps([{"kind": "contains", "text": ln} for ln in lines]))
        if "You grade image descriptions" in system:
            score = 3 + (int(hashlib.sha1(json.dumps(images)[:2000].encode()).hexdigest(), 16) % 3)
            return reply(json.dumps({"accurate": score, "complete": score - 1, "text": score, "no_invention": 5,
                                     "overall": score, "note": "Mock score."}))
        if images:
            h = hashlib.sha1(json.dumps(images).encode()).hexdigest()[:8]
            return reply(f"{body.get('model')} description of image {h}")
        return reply("OK")

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("service", choices=["docintel", "litellm"])
    ap.add_argument("--variant", default="a")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--vlm", default="http://localhost:8081/v1")
    args = ap.parse_args()
    app = make_docintel(args.variant, args.vlm) if args.service == "docintel" else make_litellm()
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
