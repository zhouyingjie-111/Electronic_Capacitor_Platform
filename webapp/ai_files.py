"""Create real downloadable files from text, without running generated programs."""

# 下载文件由受控格式生成器创建；模型提供的内容不作为 Python、HTML 或模板执行，文件下载绑定对应会话。

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import time
import uuid
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from ai_providers import AIError

VENDOR = str(Path(__file__).resolve().parent / "vendor_public")
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
FORMATS = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".py": "text/plain",
    ".m": "text/plain",
    ".c": "text/plain",
    ".h": "text/plain",
    ".cpp": "text/plain",
    ".hpp": "text/plain",
    ".tex": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
    ".xml": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}


def plain(text):
    return re.sub(
        r"\*\*([^*]+)\*\*|`([^`]+)`", lambda m: m.group(1) or m.group(2), text
    )


def clean_xml(text):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def xml_text(text):
    return escape(clean_xml(text))


def docx(content):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paragraphs = []
    code = False
    for line in content.splitlines():
        if line.startswith("```"):
            code = not code
            continue
        match = re.match(r"^(#{1,6})\s+(.+)", line)
        style = (
            "Heading" + str(min(3, len(match[1]))) if match and not code else "Normal"
        )
        text = match[2] if match and not code else line
        text = text if code else plain(text)
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">{xml_text(text)}</w:t></w:r></w:p>'
        )
    styles = f"""<w:styles xmlns:w="{ns}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="320" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>"""
    for level, size in ((1, 32), (2, 28), (3, 24)):
        styles += f'<w:style w:type="paragraph" w:styleId="Heading{level}"><w:name w:val="heading {level}"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="200"/><w:outlineLvl w:val="{level-1}"/></w:pPr><w:rPr><w:b/><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="微软雅黑"/><w:sz w:val="{size}"/></w:rPr></w:style>'
    styles += "</w:styles>"
    document = (
        f'<w:document xmlns:w="{ns}"><w:body>'
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>'
    )
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/_rels/document.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "word/document.xml": document,
        "word/styles.xml": styles,
    }
    return zip_parts(parts)


def zip_parts(parts):
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(
                name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + text
            )
    return output.getvalue()


def column(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def rows_from(content):
    rows = list(csv.reader(io.StringIO(content)))
    if len(rows) > 10000 or any(len(row) > 100 for row in rows):
        raise AIError("表格最多10000行、100列，请分文件生成。")
    return rows


def spreadsheet(content):
    rows = rows_from(content)
    body = []
    for number, row in enumerate(rows, 1):
        cells = []
        for index, value in enumerate(row, 1):
            ref = column(index) + str(number)
            # Inline strings never become spreadsheet formulas or external links.
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>'
            )
        body.append(f'<row r="{number}">' + "".join(cells) + "</row>")
    return zip_parts(
        {
            "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
            "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
            "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="数据" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"/></sheetViews><cols><col min="1" max="100" width="24" customWidth="1"/></cols><sheetData>'
            + "".join(body)
            + "</sheetData></worksheet>",
        }
    )


PDF_LOCK = __import__("threading").RLock()


def pdf(content):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    with PDF_LOCK:
        if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        style = ParagraphStyle(
            "Body",
            fontName="STSong-Light",
            fontSize=11,
            leading=18,
            spaceAfter=7,
            wordWrap="CJK",
            alignment=TA_LEFT,
            splitLongWords=True,
        )
        heading = ParagraphStyle(
            "Heading",
            parent=style,
            fontSize=15,
            leading=23,
            spaceBefore=10,
            spaceAfter=10,
        )
        story = []
        code = False
        for line in content.splitlines():
            if line.startswith("```"):
                code = not code
                continue
            match = re.match(r"^#{1,6}\s+(.+)", line)
            text = match[1] if match and not code else line
            if not text.strip():
                story.append(Spacer(1, 6))
                continue
            # Paragraph receives escaped text, never model-provided ReportLab markup.
            story.append(
                Paragraph(
                    xml_text(text if code else plain(text)),
                    heading if match and not code else style,
                )
            )
        output = io.BytesIO()
        SimpleDocTemplate(
            output,
            pagesize=A4,
            rightMargin=48,
            leftMargin=48,
            topMargin=48,
            bottomMargin=48,
            title="对话文件",
            author="电力电子平台",
        ).build(story or [Paragraph(" ", style)])
        return output.getvalue()


def create_file(filename, content, thread_id):
    if (
        not isinstance(filename, str)
        or not isinstance(content, str)
        or not content.strip()
        or len(content) > 120000
    ):
        raise AIError("文件内容须为非空文本，且不超过120000字符。")
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")[:120]
    suffix = Path(name).suffix.lower()
    if suffix not in FORMATS:
        raise AIError("支持 TXT、Markdown、CSV、JSON、常见代码、DOCX、XLSX、PDF 文件。")
    note = ""
    if suffix == ".json":
        try:
            content = json.dumps(
                json.loads(content), ensure_ascii=False, indent=2, allow_nan=False
            )
        except (ValueError, TypeError):
            raise AIError("JSON 内容无效，未生成伪装成 JSON 的文本。") from None
    if suffix == ".docx":
        raw = docx(content)
    elif suffix == ".xlsx":
        raw = spreadsheet(content)
        note = "单工作表；单元格按文本保存，不执行公式。"
    elif suffix == ".pdf":
        raw = pdf(content)
    elif suffix == ".csv":
        output = io.StringIO()
        writer = csv.writer(output)
        for row in rows_from(content):
            safe = []
            for cell in row:
                text = cell.lstrip()
                if text[:1] in ("=", "+", "-", "@"):
                    try:
                        numeric = math.isfinite(float(text))
                    except ValueError:
                        numeric = False
                    if not numeric:
                        cell = "'" + cell
                        note = "可能触发电子表格公式的单元格已按文本转义。"
                safe.append(cell)
            writer.writerow(safe)
        raw = output.getvalue().encode("utf-8-sig")
    else:
        raw = content.encode("utf-8")
    if len(raw) > 8 * 1024**2:
        raise AIError("生成文件超过8 MB，请拆分内容。", 413)
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "raw": raw,
        "size": len(raw),
        "mime": FORMATS[suffix],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "created_at": time.time(),
        "thread_id": thread_id,
        "note": note,
    }


def public_file(item):
    return {
        key: item[key] for key in ("id", "name", "size", "mime", "sha256", "note")
    } | {"url": "/api/project/ai/files/" + item["id"] + "/download"}
