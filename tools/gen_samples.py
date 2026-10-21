"""Builds the bundled synthetic test set (samples/files) and its checks (samples/manifest.json).

Needs: Pillow with raqm, python-docx, python-pptx, reportlab, LibreOffice (soffice) and a font with Hebrew.
Run from the repository root:  python tools/gen_samples.py
"""

import io
import json
import random
import shutil
import subprocess
import tempfile
from email.message import EmailMessage
from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pptx import Presentation
from pptx.util import Inches, Pt as PPt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "files"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
random.seed(7)
MANIFEST: list[dict] = []


def font(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, size)


def is_rtl(text):
    return any("֐" <= ch <= "׿" for ch in text)


def draw_text(draw, xy, text, size, fill="black", bold=False, anchor="ra"):
    """Draws a line. Hebrew lines are shaped right to left and anchored at their right edge."""
    kwargs = {"direction": "rtl", "language": "he"} if is_rtl(text) else {}
    if not is_rtl(text) and anchor == "ra":
        anchor = "la"
    draw.text(xy, text, font=font(size, bold), fill=fill, anchor=anchor, **kwargs)


def png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def add(path, description, checks):
    MANIFEST.append({"path": path, "description": description, "checks": checks})


def C(kind, **kw):
    return {"kind": kind, **kw}


# ---------- images ----------

def chart_sales(w=900, h=560):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    draw_text(d, (w - 40, 30), "מכירות לפי אזור (מיליוני ₪)", 30, bold=True)
    data = [("צפון", 4.2), ("מרכז", 6.8), ("דרום", 3.1)]
    base, left, top = h - 90, 120, 110
    d.line((left, top, left, base), fill="#555", width=2)
    d.line((left, base, w - 60, base), fill="#555", width=2)
    bw = 150
    for i, (label, v) in enumerate(data):
        x = left + 60 + i * 230
        y = base - v / 7.0 * (base - top)
        d.rectangle((x, y, x + bw, base), fill="#5B7FC7")
        d.text((x + bw / 2, y - 8), f"{v}", font=font(26), fill="black", anchor="mb")
        d.text((x + bw / 2, base + 14), label, font=font(26), fill="black", anchor="ma", direction="rtl", language="he")
    return img


def chart_pie(w=760, h=560):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    draw_text(d, (w - 40, 30), "תקציב 2026 לפי סעיף", 30, bold=True)
    parts = [("כוח אדם", 55, "#2650B8"), ("תשתיות", 25, "#E07B39"), ("הדרכה", 12, "#3BA272"), ("אחר", 8, "#A0A7B4")]
    start = -90
    box = (60, 110, 420, 470)
    for label, v, col in parts:
        end = start + v * 3.6
        d.pieslice(box, start, end, fill=col)
        start = end
    y = 150
    for label, v, col in parts:
        d.rectangle((w - 80, y, w - 56, y + 24), fill=col)
        draw_text(d, (w - 70 - 30, y), f"{label} {v}%", 26)
        y += 60
    return img


def white_on_black(w=1000, h=380):
    img = Image.new("RGB", (w, h), "black")
    d = ImageDraw.Draw(img)
    draw_text(d, (w - 50, 60), "שעות פעילות המוקד", 44, fill="white", bold=True)
    draw_text(d, (w - 50, 150), "ימים א׳–ה׳ 08:00–17:00", 38, fill="white")
    draw_text(d, (w - 50, 230), "טלפון: 03-5551234", 38, fill="white")
    return img


def screenshot_mixed(w=1100, h=420):
    img = Image.new("RGB", (w, h), "#FAFAFA")
    d = ImageDraw.Draw(img)
    lines = ["הודעת מערכת: העדכון לגרסה 1.3.10 הושלם בהצלחה.",
             "Server: docintel-x1310 · Status: OK",
             "מספר המסמכים שעובדו היום: 1,284",
             "לתמיכה פנו לצוות המערכות בכתובת support@example.org"]
    y = 50
    for line in lines:
        if is_rtl(line):
            draw_text(d, (w - 50, y), line, 30)
        else:
            d.text((50, y), line, font=font(30), fill="black")
        y += 80
    return img


def table_image(w=1000, h=420):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    rows = [("סעיף", "כמות", "מחיר"), ("מחשב נייד", "12", "4,800 ₪"), ("מסך", "20", "950 ₪"), ("מקלדת", "35", "120 ₪")]
    cols = [w - 40, w - 400, w - 640]
    y = 40
    for r, row in enumerate(rows):
        d.line((40, y - 10, w - 30, y - 10), fill="#888", width=2)
        for c, cell in enumerate(row):
            draw_text(d, (cols[c], y + 10), cell, 30, bold=(r == 0))
        y += 85
    d.line((40, y - 10, w - 30, y - 10), fill="#888", width=2)
    return img


def photo_like(w=900, h=600):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line((0, y, w, y), fill=(int(120 + 100 * t), int(170 + 50 * t), int(230 - 60 * t)))
    d.polygon([(0, h), (250, 300), (480, h)], fill="#4A6B3A")
    d.polygon([(300, h), (620, 250), (900, h)], fill="#3B5A2E")
    d.ellipse((680, 70, 790, 180), fill="#FFE08A")
    return img.filter(ImageFilter.GaussianBlur(1.2))


def diagram(w=1000, h=460):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    boxes = [("Open WebUI", 40), ("docintel", 370), ("Xberg", 700)]
    for label, x in boxes:
        d.rounded_rectangle((x, 160, x + 260, 280), radius=16, outline="#2650B8", width=4, fill="#EEF2FB")
        d.text((x + 130, 220), label, font=font(32, True), fill="#151922", anchor="mm")
    for x in (300, 630):
        d.line((x + 5, 220, x + 65, 220), fill="#151922", width=4)
        d.polygon([(x + 65, 210), (x + 80, 220), (x + 65, 230)], fill="#151922")
    draw_text(d, (w - 40, 40), "זרימת קובץ במערכת", 32, bold=True)
    return img


def stamp(w=520, h=520):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((30, 30, w - 30, h - 30), outline=(190, 30, 40, 255), width=14)
    d.text((w / 2, h / 2 - 30), "מאושר", font=font(80, True), fill=(190, 30, 40, 255), anchor="mm", direction="rtl", language="he")
    d.text((w / 2, h / 2 + 60), "21.10.2026", font=font(40), fill=(190, 30, 40, 255), anchor="mm")
    return img


def logo(w=300, h=120, text="RChat"):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, w - 6, h - 6), radius=20, fill="#2650B8")
    d.text((w / 2, h / 2), text, font=font(48, True), fill="white", anchor="mm")
    return img


def icon(size=24):
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((3, 3, size - 3, size - 3), fill="#2650B8")
    return img


def blank(w=600, h=400):
    return Image.new("RGB", (w, h), "white")


# ---------- office documents ----------

def rtl_paragraph(p):
    ppr = p._p.get_or_add_pPr()
    bidi = OxmlElement("w:bidi")
    ppr.append(bidi)
    for run in p.runs:
        rpr = run._r.get_or_add_rPr()
        rtl = OxmlElement("w:rtl")
        rpr.append(rtl)
        fonts = OxmlElement("w:rFonts")
        fonts.set(qn("w:cs"), "DejaVu Sans")
        fonts.set(qn("w:ascii"), "DejaVu Sans")
        fonts.set(qn("w:hAnsi"), "DejaVu Sans")
        rpr.append(fonts)
    return p


def para(doc, text, style=None, size=None):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    if size:
        run.font.size = Pt(size)
    return rtl_paragraph(p) if is_rtl(text) else p


def heading(doc, text, level=1):
    p = doc.add_heading(level=level)
    p.add_run(text)
    return rtl_paragraph(p)


def policy_docx(path: Path):
    doc = Document()
    sec = doc.sections[0]
    hp = sec.header.paragraphs[0]
    hp.add_run("מסמך פנימי · עותק מבוקר")
    rtl_paragraph(hp)
    fp = sec.footer.paragraphs[0]
    fp.add_run("מחלקת מערכות מידע · נוהל 14")
    rtl_paragraph(fp)
    heading(doc, "נוהל עבודה עם מסמכים 2026", 0)
    heading(doc, "1. מטרת הנוהל", 1)
    para(doc, "נוהל זה מגדיר את אופן הטיפול במסמכים סרוקים ובקבצים מצורפים במערכת RChat.")
    heading(doc, "2. תחולה", 1)
    para(doc, "הנוהל חל על כל העובדים המשתמשים ב-Open WebUI גרסה 0.12 ומעלה.")
    table = doc.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    rows = [("סוג קובץ", "זמן טיפול", "אחראי"), ("PDF סרוק", "עד 2 ימים", "צוות OCR"),
            ("מצגת", "יום אחד", "צוות תוכן"), ("דואר אלקטרוני", "מיידי", "מוקד")]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            p = table.cell(r, 2 - c).paragraphs[0]
            p.add_run(cell).bold = r == 0
            rtl_paragraph(p)
    para(doc, "")
    doc.add_picture(io.BytesIO(png(chart_sales())), width=Cm(13))
    para(doc, "תרשים 1: מכירות לפי אזור")
    heading(doc, "3. אחריות", 1)
    para(doc, "האחריות על יישום הנוהל היא של מנהלת מערכות המידע.")
    para(doc, "סוף הנוהל.")
    doc.save(path)


def mixed_docx(path: Path):
    doc = Document()
    heading(doc, "שורות בכיוונים מעורבים", 0)
    for line in ["גרסה 1.3.10 של Xberg שוחררה ב-16 באוקטובר 2026.",
                 "מספר הזמנה: PO-4471-B, סכום: 12,500 ₪",
                 "הפגישה עם ACME Ltd. תתקיים בחדר 3 בשעה 14:30."]:
        para(doc, line)
    para(doc, "This paragraph is in English and must stay in one piece, without Hebrew words mixed into it.")
    para(doc, "השורה האחרונה בעברית בלבד.")
    doc.save(path)


def long_docx(path: Path, chapters=40):
    doc = Document()
    heading(doc, "הדוח השנתי של מערך המסמכים", 0)
    for i in range(1, chapters + 1):
        heading(doc, f"פרק {i}", 1)
        for j in range(4):
            para(doc, f"בפרק {i} מתוארת עבודת הצוות ברבעון. סעיף {j + 1} עוסק בקליטת מסמכים, בבדיקת איכות ובתיעוד התהליך. "
                      f"המספר המזהה של הסעיף הוא {i}-{j + 1}.")
        if i < chapters:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    para(doc, "סוף הדוח השנתי.")
    doc.save(path)


def agenda_docx(path: Path):
    doc = Document()
    heading(doc, "סדר יום לפגישה", 0)
    for item in ["1. סקירת מצב הפרויקט", "2. תוצאות בדיקת המפענחים", "3. החלטה על שדרוג Xberg"]:
        para(doc, item)
    doc.save(path)


def soffice(src: Path, fmt: str, outdir: Path) -> Path:
    subprocess.run(["soffice", "--headless", "--convert-to", fmt, "--outdir", str(outdir), str(src)],
                   check=True, capture_output=True, timeout=180)
    return outdir / (src.stem + "." + fmt.split(":")[0])


# ---------- scans ----------

def scan_page(lines, w=1654, h=2339, rotate=0.0, noise=True, margin_lines=None):
    """An A4 page at 200 DPI with text lines, as a scanner would produce it."""
    img = Image.new("L", (w, h), 250)
    d = ImageDraw.Draw(img)
    y = 230
    for text, size, bold in lines:
        if text:
            draw_text(d, (w - 170, y), text, size, fill=20, bold=bold)
        y += int(size * 2.0)
    if margin_lines:
        top, bottom = margin_lines
        draw_text(d, (w - 70, 40), top, 26, fill=40)
        draw_text(d, (w - 70, h - 80), bottom, 26, fill=40)
    if rotate:
        img = img.rotate(rotate, expand=False, fillcolor=250, resample=Image.BICUBIC)
    if noise:
        px = img.load()
        for _ in range(int(w * h * 0.002)):
            x, yy = random.randrange(w), random.randrange(h)
            px[x, yy] = random.choice((60, 120, 200))
        img = img.filter(ImageFilter.GaussianBlur(0.6))
    return img.convert("RGB")


def save_scan_pdf(pages, path: Path):
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=200)


def probe_pdf(path: Path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 18)
    c.drawString(72, 760, "parser-eval probe page")
    c.setFont("Helvetica", 12)
    c.drawString(72, 730, "This file checks that the parser answers. It has one line of text.")
    c.save()


# ---------- slides ----------

def deck(path: Path, slides):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for title, body, image in slides:
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = title
        s.shapes.title.text_frame.paragraphs[0].font.size = PPt(36)
        if body:
            tb = s.shapes.add_textbox(Inches(7.2), Inches(1.8), Inches(5.6), Inches(4))
            tb.text_frame.word_wrap = True
            tb.text_frame.text = body
            tb.text_frame.paragraphs[0].font.size = PPt(22)
        if image is not None:
            s.shapes.add_picture(io.BytesIO(png(image)), Inches(0.6), Inches(1.8), width=Inches(6.2))
    prs.save(path)


# ---------- email ----------

def email_with_attachments(path: Path, agenda_pdf: Path):
    msg = EmailMessage()
    msg["Subject"] = "סיכום פגישה – פרויקט RChat"
    msg["From"] = "Project Office <office@example.org>"
    msg["To"] = "team@example.org"
    msg["Date"] = "Tue, 20 Oct 2026 10:15:00 +0300"
    text = ("שלום לכולם,\n\nבפגישה הוחלט לבצע בדיקת השוואה בין גרסאות המפענח לפני השדרוג.\n"
            "הבדיקה הבאה תתקיים ביום חמישי.\n\nבברכה,\nמשרד הפרויקט")
    msg.set_content(text)
    msg.add_alternative("<html><body dir='rtl'><p>שלום לכולם,</p><p>בפגישה הוחלט לבצע <b>בדיקת השוואה</b> בין גרסאות "
                        "המפענח לפני השדרוג.</p><ul><li>הבדיקה הבאה תתקיים ביום חמישי.</li></ul><p>בברכה,<br>משרד הפרויקט"
                        "</p></body></html>", subtype="html")
    msg.add_attachment(agenda_pdf.read_bytes(), maintype="application", subtype="pdf", filename="agenda.pdf")
    msg.add_attachment(png(chart_sales()), maintype="image", subtype="png", filename="chart.png")
    path.write_bytes(bytes(msg))


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ("pdf", "scans", "office", "slides", "email", "images/charts", "images/text", "images/photos",
                "images/icons", "images/stamps", "images/diagrams"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())

    # Word, legacy Word and digital PDF of the same policy.
    policy_checks = [
        C("contains", text="נוהל עבודה עם מסמכים 2026"),
        C("contains", text="נוהל זה מגדיר את אופן הטיפול במסמכים סרוקים ובקבצים מצורפים"),
        C("order", items=["מטרת הנוהל", "תחולה", "אחריות"]),
        C("table_cell", row="PDF סרוק", value="עד 2 ימים"),
        C("table_cell", row="דואר אלקטרוני", value="מיידי"),
        C("appears_once", text="סוף הנוהל"),
        C("image_blocks", count=1),
        C("image_contains", index=1, text="מכירות לפי אזור"),
        C("not_contains", text="<!-- image -->"),
        C("question", question="מי אחראית על יישום הנוהל?", answer="מנהלת מערכות המידע"),
    ]
    policy_docx(OUT / "office" / "policy_he.docx")
    add("office/policy_he.docx", "Hebrew policy: right-to-left text, a table, one chart image.", policy_checks)
    shutil.copy(soffice(OUT / "office" / "policy_he.docx", "doc", tmp), OUT / "office" / "policy_he.doc")
    add("office/policy_he.doc", "The same policy saved as a legacy .doc file.", policy_checks)
    shutil.copy(soffice(OUT / "office" / "policy_he.docx", "pdf", tmp), OUT / "pdf" / "policy_he.pdf")
    add("pdf/policy_he.pdf", "The same policy as a digital PDF, with header and footer text in the margins.",
        policy_checks + [C("contains", text="מסמך פנימי · עותק מבוקר"), C("contains", text="מחלקת מערכות מידע · נוהל 14")])

    mixed_docx(tmp / "mixed_direction.docx")
    shutil.copy(soffice(tmp / "mixed_direction.docx", "pdf", tmp), OUT / "pdf" / "mixed_direction.pdf")
    add("pdf/mixed_direction.pdf", "Lines that mix Hebrew, English, numbers and currency. Tests reading order.", [
        C("contains", text="גרסה 1.3.10 של Xberg שוחררה ב-16 באוקטובר 2026"),
        C("contains", text="מספר הזמנה: PO-4471-B, סכום: 12,500 ₪"),
        C("contains", text="הפגישה עם ACME Ltd. תתקיים בחדר 3 בשעה 14:30"),
        C("contains", text="This paragraph is in English and must stay in one piece", match="exact"),
        C("order", items=["גרסה 1.3.10", "מספר הזמנה", "ACME Ltd", "This paragraph is in English", "השורה האחרונה"]),
    ])

    long_docx(tmp / "long_report.docx")
    shutil.copy(soffice(tmp / "long_report.docx", "pdf", tmp), OUT / "pdf" / "long_report_40p.pdf")
    add("pdf/long_report_40p.pdf", "A 40-page digital report. Watch the time per file and timeouts.", [
        C("contains", text="הדוח השנתי של מערך המסמכים"),
        C("order", items=["פרק 1", "פרק 20", "פרק 40"]),
        C("appears_once", text="הדוח השנתי של מערך המסמכים"),
        C("contains", text="המספר המזהה של הסעיף הוא 40-4"),
        C("contains", text="סוף הדוח השנתי"),
        C("question", question="כמה פרקים יש בדוח?", answer="40"),
    ])

    # Scans
    invoice = [("חשבונית מס 2026/0457", 54, True), ("", 20, False), ("לכבוד: חברת דוגמה בע״מ", 36, False),
               ("תאריך: 14.10.2026", 36, False), ("", 20, False), ("שירותי ייעוץ   4,200 ₪", 36, False),
               ("מע״מ 18%   756 ₪", 36, False), ("סה״כ לתשלום: 4,956 ₪", 40, True), ("", 20, False),
               ("התשלום יבוצע תוך 30 ימים.", 34, False)]
    save_scan_pdf([scan_page(invoice)], OUT / "scans" / "invoice_scan_he.pdf")
    add("scans/invoice_scan_he.pdf", "One scanned page in Hebrew. Needs OCR with Hebrew.", [
        C("contains", text="חשבונית מס 2026/0457"),
        C("contains", text="לכבוד: חברת דוגמה בע״מ"),
        C("contains", text="סה״כ לתשלום: 4,956 ₪"),
        C("order", items=["חשבונית מס", "שירותי ייעוץ", "סה״כ לתשלום"]),
        C("question", question="מה הסכום הכולל לתשלום?", answer="4,956 ₪"),
    ])
    form1 = [("טופס 101 · כרטיס עובד", 52, True), ("שנת מס 2026", 36, False), ("", 20, False),
             ("א. פרטי המעסיק", 40, True), ("שם המעסיק: חברת דוגמה בע״מ", 34, False), ("מספר תיק ניכויים: 912345678", 34, False),
             ("", 20, False), ("ב. פרטי העובד", 40, True), ("שם משפחה: כהן", 34, False), ("שם פרטי: דנה", 34, False)]
    form2 = [("ח. הצהרת העובד", 40, True), ("אני מצהיר כי הפרטים שמסרתי בטופס זה מלאים ונכונים.", 34, False),
             ("", 20, False), ("חתימה: ____________", 34, False)]
    save_scan_pdf([scan_page(form1), scan_page(form2)], OUT / "scans" / "form_101_scan.pdf")
    add("scans/form_101_scan.pdf", "Two scanned form pages with key-value fields.", [
        C("contains", text="טופס 101 · כרטיס עובד"),
        C("contains", text="שנת מס 2026"),
        C("order", items=["פרטי המעסיק", "פרטי העובד", "הצהרת העובד"]),
        C("table_cell", row="שם משפחה", value="כהן"),
        C("appears_once", text="הצהרת העובד"),
        C("question", question="מה שם המעסיק?", answer="חברת דוגמה בע״מ"),
    ])
    rotated = [("דוח ביקורת שנתי", 50, True), ("", 20, False), ("נמצאו שלושה ליקויים בתהליך הסריקה.", 36, False),
               ("כל הליקויים תוקנו עד סוף חודש ספטמבר.", 36, False), ("הביקורת הבאה תיערך בינואר 2027.", 36, False)]
    save_scan_pdf([scan_page(rotated, rotate=2.5)], OUT / "scans" / "audit_scan_rotated.pdf")
    add("scans/audit_scan_rotated.pdf", "A scan tilted by 2.5 degrees, with noise.", [
        C("contains", text="דוח ביקורת שנתי"),
        C("contains", text="נמצאו שלושה ליקויים בתהליך הסריקה"),
        C("contains", text="הביקורת הבאה תיערך בינואר 2027"),
    ])
    margins = [("מכתב לספק", 50, True), ("", 20, False), ("אנו מאשרים את קבלת ההצעה מיום 3 באוקטובר.", 36, False),
               ("ההזמנה תצא לאחר אישור התקציב.", 36, False)]
    save_scan_pdf([scan_page(margins, margin_lines=("סימוכין: 2026-118", "עמוד 1 מתוך 1"))], OUT / "scans" / "letter_margins.pdf")
    add("scans/letter_margins.pdf", "A scan with small text at the very top and bottom margins.", [
        C("contains", text="אנו מאשרים את קבלת ההצעה מיום 3 באוקטובר"),
        C("contains", text="סימוכין: 2026-118"),
        C("contains", text="עמוד 1 מתוך 1"),
    ])

    # Slides
    seven = [
        ("מכירות ברבעון השלישי", "המרכז הוביל את המכירות ברבעון.", chart_sales()),
        ("מחירון ציוד", "מחירי הציוד המעודכנים לשנה הבאה.", table_image()),
        ("הודעת מערכת", "צילום מסך מהמערכת לאחר העדכון.", screenshot_mixed()),
        ("אתר הנופש", "תמונה מאתר הכנס השנתי.", photo_like()),
        ("ארכיטקטורה", "הקובץ עובר מ-Open WebUI ל-docintel ומשם ל-Xberg.", diagram()),
        ("שעות פעילות", "שעות הפעילות של המוקד.", white_on_black()),
        ("תודה רבה", "שאלות?", stamp()),
    ]
    deck(OUT / "slides" / "deck_7_images.pptx", seven)
    deck_checks = [
        C("order", items=["מכירות ברבעון השלישי", "מחירון ציוד", "ארכיטקטורה", "תודה רבה"]),
        C("contains", text="המרכז הוביל את המכירות ברבעון"),
        C("appears_once", text="תודה רבה"),
        C("image_blocks", count=7),
        C("image_contains", index=1, text="מכירות לפי אזור"),
        C("image_contains", index=2, text="מחשב נייד"),
        C("image_contains", index=6, text="שעות פעילות המוקד"),
        C("not_contains", text="<!-- image -->"),
    ]
    add("slides/deck_7_images.pptx", "Seven slides, seven different images: chart, table, screenshot, photo, diagram, "
        "white text on black, a transparent stamp. Tests image order and placeholder matching.", deck_checks)
    shutil.copy(soffice(OUT / "slides" / "deck_7_images.pptx", "ppt", tmp), OUT / "slides" / "deck_7_images.ppt")
    add("slides/deck_7_images.ppt", "The same deck saved as a legacy .ppt file.", deck_checks)
    rep = [(f"שקופית {i}", f"תוכן השקופית מספר {i} ברבעון.", logo()) for i in range(1, 5)] + [("סיכום הרבעון", "כל היעדים הושגו.", logo())]
    deck(OUT / "slides" / "repeated_logo.pptx", rep)
    add("slides/repeated_logo.pptx", "The same logo on every slide. Tests duplicate images.", [
        C("order", items=["שקופית 1", "שקופית 4", "סיכום הרבעון"]),
        C("appears_once", text="סיכום הרבעון"),
        C("contains", text="כל היעדים הושגו"),
        C("image_blocks", count=1, op="min"),
    ])

    # Email
    agenda_docx(tmp / "agenda.docx")
    email_with_attachments(OUT / "email" / "meeting_summary.eml", soffice(tmp / "agenda.docx", "pdf", tmp))
    add("email/meeting_summary.eml", "An email in Hebrew (text and HTML) with a PDF and an image attached.", [
        C("contains", text="סיכום פגישה – פרויקט RChat"),
        C("contains", text="בפגישה הוחלט לבצע בדיקת השוואה בין גרסאות המפענח לפני השדרוג"),
        C("appears_once", text="הבדיקה הבאה תתקיים ביום חמישי"),
        C("contains", text="סדר יום לפגישה"),
        C("contains", text="מכירות לפי אזור"),
    ])

    # Standalone images
    images = [
        ("images/charts/sales_by_region.png", chart_sales(), "Bar chart with Hebrew labels and values.",
         [C("contains", text="מכירות לפי אזור"), C("contains", text="6.8"), C("contains", text="דרום")]),
        ("images/charts/budget_pie.png", chart_pie(), "Pie chart with a Hebrew legend.",
         [C("contains", text="תקציב 2026 לפי סעיף"), C("contains", text="כוח אדם 55%")]),
        ("images/text/white_on_black.png", white_on_black(), "White Hebrew text on a black background.",
         [C("contains", text="שעות פעילות המוקד"), C("contains", text="03-5551234")]),
        ("images/text/screenshot_mixed.png", screenshot_mixed(), "Screenshot with Hebrew and English lines.",
         [C("contains", text="העדכון לגרסה 1.3.10 הושלם בהצלחה"), C("contains", text="Server: docintel-x1310"),
          C("contains", text="support@example.org")]),
        ("images/text/price_table.png", table_image(), "A table as an image.",
         [C("contains", text="מחשב נייד"), C("table_cell", row="מסך", value="950")]),
        ("images/photos/landscape.jpg", photo_like(), "A photo-like image without text.", []),
        ("images/diagrams/flow.png", diagram(), "A simple flow diagram with English box labels.",
         [C("contains", text="docintel"), C("contains", text="זרימת קובץ במערכת")]),
        ("images/stamps/approved_stamp.png", stamp(), "A transparent stamp. Shows whether transparency turns black.",
         [C("contains", text="מאושר")]),
        ("images/icons/icon_24.png", icon(), "A 24-pixel icon. Should be skipped, not described.", []),
        ("images/icons/blank.png", blank(), "A blank white image. Should be skipped.", []),
    ]
    for path, img, desc, checks in images:
        target = OUT / path
        if path.endswith(".jpg"):
            img.convert("RGB").save(target, "JPEG", quality=88)
        else:
            img.save(target, "PNG")
        add(path, desc, checks)

    probe_pdf(ROOT / "samples" / "probe.pdf")
    (ROOT / "samples" / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(MANIFEST)} files written to {OUT}")


if __name__ == "__main__":
    main()
