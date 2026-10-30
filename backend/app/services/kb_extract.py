"""上传文档正文抽取：docx / pdf / pptx / xlsx / md / txt → 纯文本（小标题统一成 "## " 前缀，供 kb_service 分块）。

- 只处理 OOXML 新格式；.doc/.xls/.ppt 提示先另存为新格式。
- PDF 只取文本层；每页平均不足 30 字视为扫描件，提示暂不支持（OCR 放第二期）。
"""
import io
import re
from pathlib import Path

from app.core.response import BizError, bad_request

MAX_BYTES = 50 * 1024 * 1024
DOC_EXTS = {".docx", ".pdf", ".pptx", ".md", ".txt", ".markdown"}
TABLE_EXTS = {".xlsx", ".xlsm", ".csv"}
LEGACY = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx", ".wps": ".docx", ".et": ".xlsx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}
MAX_TABLE_ROWS = 20000


def validate_archive(data: bytes) -> None:
    """OOXML 本身也是压缩包，解析前限制展开体积，避免小文件占满服务内存。"""
    import zipfile
    if not data.startswith(b"PK"):
        return
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(i.file_size for i in entries) > 200 * 1024 * 1024:
                raise bad_request("文件展开后过大，请拆分文件后上传")
    except zipfile.BadZipFile:
        raise bad_request("文件压缩结构损坏，请重新另存为 Office 新格式") from None


def ext_of(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def _docx(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = Document(io.BytesIO(data))
    out: list[str] = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if style.startswith(("Heading", "标题")) or style == "Title":
                out.append(f"## {text}")
            else:
                out.append(text)
        elif tag == "tbl":
            for row in Table(el, d).rows:
                cells, seen = [], set()
                for c in row.cells:  # 合并单元格会重复出现，去重
                    if id(c._tc) in seen:
                        continue
                    seen.add(id(c._tc))
                    cells.append(c.text.strip().replace("\n", " "))
                if any(cells):
                    out.append(" | ".join(cells))
    return "\n".join(out)


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                raise bad_request("PDF 已加密，请先解除密码保护再上传")
        pages = [(pg.extract_text() or "").strip() for pg in reader.pages]
    except PdfReadError as exc:
        raise bad_request(f"PDF 无法读取：{exc}")
    text_len = sum(len(re.sub(r"\s", "", t)) for t in pages)
    if pages and text_len / len(pages) < 30:
        raise bad_request("这份 PDF 看起来是扫描件或图片，没有可提取的文字；扫描件识别（OCR）将在第二期支持，"
                          "现在可以先上传 Word 版或带文字层的 PDF")
    return "\n".join(t for t in pages if t)


def _pptx(data: bytes) -> str:
    from pptx import Presentation

    out: list[str] = []
    for i, slide in enumerate(Presentation(io.BytesIO(data)).slides, start=1):
        lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                lines.extend(p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip())
            elif getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        lines.append(" | ".join(cells))
        if lines:
            out.append(f"## 第{i}页 {lines[0][:40]}")
            out.extend(lines[1:])
    return "\n".join(out)


def _xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out, total = [], 0
    try:
        for ws in wb.worksheets:
            if ws.max_column > 200 or ws.max_row > MAX_TABLE_ROWS:
                raise bad_request("表格过大，请拆分为每表最多 20000 行、200 列；不会只截取部分内容入库")
            out.append(f"## {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v).strip() for v in row]
                if any(cells):
                    total += 1
                    if total > MAX_TABLE_ROWS:
                        raise bad_request("表格正文超过 20000 行，请拆分后上传；不会只截取部分内容入库")
                    out.append(" | ".join(cells).rstrip(" |"))
    finally:
        wb.close()
    return "\n".join(out)


def _text(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise bad_request("文本文件编码无法识别，请另存为 UTF-8")


def extract_text(data: bytes, filename: str) -> str:
    """返回抽取后的正文；不支持或无文字时抛 bad_request（文案直接给上传人看）。"""
    if len(data) > MAX_BYTES:
        raise bad_request("文件超过 50MB，请拆分后再上传")
    validate_archive(data)
    ext = ext_of(filename)
    if ext in LEGACY:
        raise bad_request(f"暂不支持旧版 {ext} 格式，请用 Office/WPS 另存为 {LEGACY[ext]} 后再上传")
    if ext in IMAGE_EXTS:
        raise bad_request("图片暂不支持文字识别（OCR 将在第二期支持）")
    try:
        if ext == ".docx":
            text = _docx(data)
        elif ext == ".pdf":
            text = _pdf(data)
        elif ext == ".pptx":
            text = _pptx(data)
        elif ext in (".xlsx", ".xlsm"):
            text = _xlsx(data)
        elif ext in (".md", ".markdown", ".txt", ".csv"):
            text = _text(data)
        else:
            raise bad_request(f"暂不支持 {ext or '无扩展名'} 文件；支持 Word(docx)、PDF、PPT(pptx)、Excel(xlsx)、Markdown、TXT")
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"文件解析失败，可能已损坏或格式不符：{type(exc).__name__}")
    text = re.sub(r"[ \t　]+\n", "\n", text or "").strip()
    if len(re.sub(r"\s", "", text)) < 10:
        raise bad_request("没有从文件里读到正文内容")
    return text
