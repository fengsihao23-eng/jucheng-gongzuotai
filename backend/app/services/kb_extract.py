"""上传文档正文抽取：docx / pdf / pptx / xlsx / md / txt / 图片 → 纯文本（小标题统一成 "## " 前缀，供 kb_service 分块）。

- 只处理 OOXML 新格式；.doc/.xls/.ppt 提示先另存为新格式。
- PDF 逐页取文字层；没有文字层的页（扫描件、照片）用多模态大模型逐页转录，不使用传统 OCR。
- 图片（png/jpg/bmp/tif/webp）同样交给多模态大模型转录，结果标注识别方式，必须由人核对后再入库。
- 单份最多识别 LLM_VISION_MAX_PAGES 页；超限明确拒绝，不静默截断后宣称成功。
"""
import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, ImageStat

from app.core.config import settings
from app.core.response import BizError, bad_request
from app.integration import llm

MAX_BYTES = 1024 * 1024 * 1024  # 单文件上传上限 1GB（超限明确拒绝，不静默截断）
DOC_EXTS = {".docx", ".pdf", ".pptx", ".md", ".txt", ".markdown"}
TABLE_EXTS = {".xlsx", ".xlsm", ".csv"}
LEGACY = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx", ".wps": ".docx", ".et": ".xlsx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
MAX_TABLE_ROWS = 20000
MAX_IMAGE_EDGE = 1600  # 逐页识别前的长边像素；再大只会增加费用，不提高可读性

VISION_SYSTEM = """你是企业文档数字化助手，负责把扫描件、照片或截图逐页转录成可检索的文字。
只输出图片里能看清的内容：不要总结、不要补全、不要编造原文没有的信息。
数字、合同编号、金额、日期、单位和人名必须与图片一致；表格按行输出，列之间用 | 分隔并保留表头。
印章、手写批注、勾选框用 [印章] [手写] [已勾选] 标注；看不清的字用 □ 代替。
如果这一页主要是照片或图纸，用一两句话描述画面并转录图中出现的文字。只输出转录结果，不要解释。"""


@dataclass
class Extracted:
    text: str
    method: str  # text 文字层 | vision 大模型逐页识别
    pages: int = 0
    vision_pages: int = 0
    note: str | None = None

    def meta(self) -> dict:
        return {"method": self.method, "pages": self.pages, "vision_pages": self.vision_pages, "note": self.note}


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


# ---------------------------------------------------------------- 扫描件 / 图片：多模态模型逐页识别
def _prepare_image(image: Image.Image) -> bytes:
    """统一转成白底 RGB 并压到长边 1600px，避免几十 MB 的原始照片直接送给网关。"""
    im = ImageOps.exif_transpose(image) or image
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, "white")
        canvas.paste(rgba, mask=rgba.split()[-1])
        im = canvas
    else:
        im = im.convert("RGB")
    width, height = im.size
    if max(width, height) > MAX_IMAGE_EDGE:
        ratio = MAX_IMAGE_EDGE / max(width, height)
        im = im.resize((max(1, int(width * ratio)), max(1, int(height * ratio))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue()


def _is_blank(image: Image.Image) -> bool:
    stat = ImageStat.Stat(image.convert("L").resize((64, 64)))
    try:
        return stat.stddev[0] < 3
    except IndexError:
        return True


def _clean_model_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _page_prompt(page_no: int, total: int) -> str:
    return (f"这是同一份资料的扫描件第 {page_no} 页（共 {total} 页）。请完整转录这一页的文字，"
            "保留条款顺序、表格、编号、金额和日期。看不清的字用 □，不要猜测。")


def _vision_pages(jobs: list[tuple[int, bytes]], total: int) -> dict[int, str]:
    """并发转录多页，保持页序；任一页最终失败就整份失败，不留下缺页的正文。"""
    if not llm.vision_enabled():
        raise bad_request("这份资料是扫描件或图片，需要用多模态大模型识别；"
                          "服务器还没有配置可用的视觉模型，请联系管理员配置后重新识别")
    workers = max(1, min(settings.llm_vision_workers, len(jobs)))

    def one(job: tuple[int, bytes]) -> tuple[int, str]:
        page_no, image = job
        for _ in range(2):  # 单页失败重试一次，避免一次网络抖动就让整份资料回到待整理
            out = llm.vision(VISION_SYSTEM, _page_prompt(page_no, total), image,
                             timeout_seconds=settings.llm_vision_timeout_seconds)
            cleaned = _clean_model_text(out or "")
            if cleaned:
                return page_no, cleaned
        return page_no, ""

    results: dict[int, str] = {}
    failures: list[int] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, job): job[0] for job in jobs}
        for future in as_completed(futures):
            page_no, text = future.result()
            if text:
                results[page_no] = text
            else:
                failures.append(page_no)
    if failures:
        pages = "、".join(str(n) for n in sorted(failures)[:8])
        raise bad_request(f"第 {pages} 页没有识别出文字（模型超时或不可用）。原件已保留，可稍后重新识别，"
                          "或先转成带文字层的 PDF 再上传")
    return results


def _images(data: bytes) -> Extracted:
    try:
        source = Image.open(io.BytesIO(data))
        frames: list[bytes] = []
        for frame in ImageSequence.Iterator(source):
            frame.load()
            if _is_blank(frame):
                continue
            frames.append(_prepare_image(frame.copy()))
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"图片无法读取（{type(exc).__name__}），请转为 JPG / PNG 后重新上传") from None
    if not frames:
        raise bad_request("图片是空白的，没有可识别的内容")
    if len(frames) > settings.llm_vision_max_pages:
        raise bad_request(f"这份图片有 {len(frames)} 页，超过单次识别上限 {settings.llm_vision_max_pages} 页；请拆分后上传")
    texts = _vision_pages([(i, image) for i, image in enumerate(frames, start=1)], len(frames))
    if len(frames) == 1:
        text = texts[1]
        note = "图片由大模型识别，请核对编号、金额、日期与签名等关键信息"
    else:
        text = "\n\n".join(f"## 第{i}页\n{texts[i]}" for i in sorted(texts))
        note = f"多页图片由大模型逐页识别（共 {len(frames)} 页），请核对关键信息"
    return Extracted(text, "vision", len(frames), len(frames), note)


def _pdf(data: bytes) -> Extracted:
    """文字层按页读取；无文字层的页渲染成图片交给大模型，混合型 PDF 也逐页判断。"""
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"PDF 无法读取：{type(exc).__name__}") from None
    try:
        if doc.needs_pass and not doc.authenticate(""):
            raise bad_request("PDF 已加密，请先解除密码保护再上传")
        total = doc.page_count
        if total == 0:
            raise bad_request("PDF 没有页面")
        raw_pages = [(page.get_text("text") or "").strip() for page in doc]
        total_chars = sum(len(re.sub(r"\s", "", t)) for t in raw_pages)
        sparse = total_chars / total < 30  # 与旧版「每页平均不足 30 字视为扫描件」保持一致
        page_text: dict[int, str] = {}
        jobs: list[tuple[int, bytes]] = []
        for page_no, (page, raw) in enumerate(zip(doc, raw_pages), start=1):
            if not sparse and len(re.sub(r"\s", "", raw)) >= 10:
                page_text[page_no] = raw
                continue
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if _is_blank(image):
                page_text[page_no] = ""
                continue
            jobs.append((page_no, _prepare_image(image)))
    finally:
        doc.close()

    if not jobs:
        return Extracted("\n".join(t for t in page_text.values() if t), "text", total, 0)
    if len(jobs) > settings.llm_vision_max_pages:
        raise bad_request(f"这份 PDF 有 {len(jobs)} 页需要图像识别，超过单次上限 "
                          f"{settings.llm_vision_max_pages} 页；请拆分后上传，或先转成带文字层的 PDF")
    page_text.update(_vision_pages(jobs, total))
    text = "\n\n".join(f"## 第{i}页\n{page_text[i]}" for i in sorted(page_text) if page_text[i].strip())
    note = (f"扫描件由大模型逐页识别（共 {total} 页，其中 {len(jobs)} 页按图像识别）；"
            "金额、编号、日期与签章页请对照原件核对")
    return Extracted(text, "vision", total, len(jobs), note)


def needs_vision(data: bytes, filename: str) -> bool:
    """轻量预判：是否必须走图像识别。用于长任务提前转后台，避免同步请求超时。"""
    ext = ext_of(filename)
    if ext in IMAGE_EXTS:
        return True
    if ext != ".pdf":
        return False
    try:
        import pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            if doc.page_count == 0:
                return False
            chars = 0
            for page in doc:
                text = re.sub(r"\s", "", page.get_text("text") or "")
                chars += len(text)
                if len(text) < 10 and page.get_images():
                    return True
            return chars / doc.page_count < 30
        finally:
            doc.close()
    except Exception:  # noqa: BLE001  预判失败交给正式抽取给出具体错误
        return False


def vision_available() -> bool:
    return llm.vision_enabled()


def extract_document(data: bytes, filename: str) -> Extracted:
    """返回正文及识别方式；不支持、无文字或识别失败时抛 bad_request（文案直接给上传人看）。"""
    if len(data) > MAX_BYTES:
        raise bad_request("文件超过 50MB，请拆分后再上传")
    validate_archive(data)
    ext = ext_of(filename)
    if ext in LEGACY:
        raise bad_request(f"暂不支持旧版 {ext} 格式，请用 Office/WPS 另存为 {LEGACY[ext]} 后再上传")
    try:
        if ext in IMAGE_EXTS:
            result = _images(data)
        elif ext == ".docx":
            result = Extracted(_docx(data), "text")
        elif ext == ".pdf":
            result = _pdf(data)
        elif ext == ".pptx":
            result = Extracted(_pptx(data), "text")
        elif ext in (".xlsx", ".xlsm"):
            result = Extracted(_xlsx(data), "text")
        elif ext in (".md", ".markdown", ".txt", ".csv"):
            result = Extracted(_text(data), "text")
        else:
            raise bad_request(f"暂不支持 {ext or '无扩展名'} 文件；支持 Word(docx)、PDF、PPT(pptx)、Excel(xlsx)、"
                              "Markdown、TXT 和常见图片")
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"文件解析失败，可能已损坏或格式不符：{type(exc).__name__}") from None
    text = re.sub(r"[ \t　]+\n", "\n", result.text or "").strip()
    if len(re.sub(r"\s", "", text)) < 10:
        raise bad_request("没有从文件里读到正文内容" if result.method == "text" else "没有从图片或扫描件里识别出正文内容")
    result.text = text
    return result


def extract_text(data: bytes, filename: str) -> str:
    """兼容旧调用：只返回正文。"""
    return extract_document(data, filename).text
