"""Bounded in-memory attachment decoding. Uploaded programs are never executed."""

# 附件只作为数据解码，不执行上传的脚本；控制文件大小、ZIP 展开体积与 XML 实体，防止文件输入绕过边界。

import base64
import csv
import io
from pathlib import PurePath
import re
import uuid
import xml.etree.ElementTree as ET
from zipfile import ZipFile, BadZipFile

from ai_providers import AIError

MAX_FILE = 8 * 1024**2
MAX_TEXT = 60000
TEXT_TYPES = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".py",
    ".m",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
    ".ini",
    ".toml",
    ".v",
    ".sv",
    ".tex",
}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def decode_text(data):
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            pass
    raise AIError("文本编码无法读取，请保存为 UTF-8 后上传。")


def safe_zip(raw):
    try:
        archive = ZipFile(io.BytesIO(raw))
    except BadZipFile:
        raise AIError("文件不是有效的文档/模型归档。") from None
    info = archive.infolist()
    if len(info) > 4096 or sum(item.file_size for item in info) > 40 * 1024**2:
        archive.close()
        raise AIError("文档解压体积过大，请拆分后上传。")
    if any(item.file_size > 8 * 1024**2 for item in info):
        archive.close()
        raise AIError("文档内部文件过大。")
    return archive


def xml(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise AIError("不支持含实体声明的文档。")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise AIError("文档结构无法解析。") from None


def extract_docx(data):
    with safe_zip(data) as archive:
        if "word/document.xml" not in archive.namelist():
            raise AIError("DOCX 缺少正文。")
        root = xml(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        return "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns))


def extract_xlsx(data):
    with safe_zip(data) as archive:
        names = archive.namelist()
        strings = []
        if "xl/sharedStrings.xml" in names:
            root = xml(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(n.itertext()) for n in root][:100000]
        texts = []
        sheets = sorted(
            n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
        )[:3]
        for name in sheets:
            texts.append("工作表 " + name.rsplit("/", 1)[-1])
            root = xml(archive.read(name))
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            for row in root.iter(ns + "row"):
                cells = []
                for cell in list(row)[:100]:
                    value = cell.find(ns + "v")
                    value = (
                        value.text if value is not None else "".join(cell.itertext())
                    )
                    if cell.get("t") == "s":
                        try:
                            value = strings[int(value)]
                        except (ValueError, IndexError, TypeError):
                            value = ""
                    cells.append(cell.get("r", "?") + "=" + str(value or ""))
                texts.append("\t".join(cells))
                if sum(map(len, texts)) > MAX_TEXT:
                    break
        return "\n".join(texts) + "\n[只读取缓存值，不执行公式；最多3个工作表。]"


def extract_slx(data):
    with safe_zip(data) as archive:
        chunks = []
        for name in archive.namelist():
            if not name.startswith("simulink/") or not name.endswith(".xml"):
                continue
            if not any(
                part in name
                for part in ("blockdiagram", "systems/", "stateflow/chart_")
            ):
                continue
            root = xml(archive.read(name))
            for node in root.iter():
                if node.tag == "Block":
                    chunks.append(
                        "模块："
                        + node.get("Name", "")
                        + " / "
                        + node.get("BlockType", "")
                    )
                elif node.tag == "P" and node.get("Name") in (
                    "script",
                    "StopTime",
                    "StartTime",
                    "SampleTime",
                    "Value",
                    "Gain",
                ):
                    chunks.append(node.get("Name") + ": " + (node.text or "")[:20000])
            if sum(map(len, chunks)) > MAX_TEXT:
                break
        return "Simulink模型静态结构（未运行，未执行回调）\n" + "\n".join(chunks)


def load_attachment(upload):
    if not upload or not upload.filename:
        raise AIError("请选择文件。")
    name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)[:160]
    suffix = PurePath(name).suffix.lower()
    data = upload.stream.read(MAX_FILE + 1)
    if not data or len(data) > MAX_FILE:
        raise AIError("单个附件须为非空文件，且不超过 8 MB。", 413)
    item = {
        "id": uuid.uuid4().hex,
        "name": name,
        "size": len(data),
        "raw": data,
        "kind": "file",
        "text": "",
        "note": "",
    }
    if suffix in IMAGE_TYPES:
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(io.BytesIO(data)) as picture:
                if (
                    picture.format not in ("PNG", "JPEG", "WEBP", "GIF")
                    or max(picture.size) > 8192
                    or picture.width * picture.height > 32000000
                ):
                    raise AIError("图片格式或尺寸不支持，最长边不超过8192像素。")
                picture.verify()
                mime = Image.MIME[picture.format]
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise AIError("图片文件无法读取。") from None
        item.update(
            kind="image",
            mime=mime,
            data_url="data:" + mime + ";base64," + base64.b64encode(data).decode(),
        )
        if suffix == ".gif":
            item["note"] = "动态图片的识别方式取决于所选模型。"
        return item
    if suffix in TEXT_TYPES:
        text = decode_text(data)
    elif suffix == ".pdf":
        from pypdf import PdfReader

        try:
            pdf = PdfReader(io.BytesIO(data))
            if pdf.is_encrypted:
                raise AIError("请先移除 PDF 密码后上传。")
            texts = []
            for page in pdf.pages[:80]:
                texts.append(page.extract_text() or "")
                if sum(map(len, texts)) > MAX_TEXT:
                    break
            text = "\n".join(texts)
        except AIError:
            raise
        except Exception:
            raise AIError("PDF 文字解析失败，请另存为文本或上传截图。") from None
        if not text.strip():
            raise AIError("PDF 未检测到可提取文字，请上传页面截图。")
        item["note"] = "PDF 仅提取文字；需要分析图表时请同时粘贴截图。"
    elif suffix == ".docx":
        text = extract_docx(data)
    elif suffix == ".xlsx":
        text = extract_xlsx(data)
        item["note"] = "读取缓存值，不执行公式；最多3张工作表。"
    elif suffix == ".slx":
        text = extract_slx(data)
        item["note"] = "只读模型结构，不执行上传模型或回调。"
    else:
        raise AIError(
            "支持图片、PDF、DOCX、XLSX、CSV、文本、代码和 SLX；其他格式请转换后上传。"
        )
    text = text.replace("\x00", "")
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n[内容已截取，请按需拆分文件。]"
        item["note"] += " 已截取前60000字符。"
    item["text"] = text
    return item


def attachment_public(item):
    return {key: item[key] for key in ("id", "name", "size", "kind", "note")} | {
        "characters": len(item["text"])
    }
