"""报表渲染器：xlsx（openpyxl）/ docx（python-docx）。

xlsx 约定：表头藏青底白字、千分位金额、百分比、冻结首行、自动列宽、合计行加粗；每个工作簿必须含「口径说明」sheet。
docx 约定：微软雅黑正文、标题层级、带表头底色的表格；“初稿”字样供人工润色。
"""
import io
from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BRAND = "1B3275"
HEAD_FILL = PatternFill("solid", fgColor=BRAND)
HEAD_FONT = Font(name="微软雅黑", bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name="微软雅黑", size=10)
BOLD_FONT = Font(name="微软雅黑", size=10, bold=True)
TOTAL_FILL = PatternFill("solid", fgColor="F3F0E8")
ALERT_FILL = PatternFill("solid", fgColor="FCF0EC")
THIN = Side(style="thin", color="D9D4C7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FMT = {"money": "#,##0.00", "int": "#,##0", "pct": "0.0%", "date": "yyyy-mm-dd", "text": "@"}


def _width(v) -> int:
    s = str(v if v is not None else "")
    return sum(2 if ord(ch) > 127 else 1 for ch in s)


def _cell_value(v, kind):
    if v is None:
        return None
    if kind in ("money", "pct") and not isinstance(v, (int, float, Decimal)):
        try:
            return Decimal(str(v))
        except Exception:  # noqa: BLE001
            return v
    if isinstance(v, (list, dict)):
        return str(v)
    return v


class XlsxBook:
    """columns: [(key, 标题, kind)]，kind ∈ money|int|pct|date|text。"""

    def __init__(self):
        self.wb = Workbook()
        self.wb.remove(self.wb.active)

    def sheet(self, title: str, columns: list[tuple], rows: list[dict], total: dict | None = None,
              alert=None, note: str | None = None):
        ws = self.wb.create_sheet(title[:31])
        widths = [_width(c[1]) + 4 for c in columns]
        for j, (_, label, _k) in enumerate(columns, start=1):
            c = ws.cell(row=1, column=j, value=label)
            c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 22
        r = 1
        for row in rows:
            r += 1
            is_alert = bool(alert and alert(row))
            for j, (key, _l, kind) in enumerate(columns, start=1):
                v = _cell_value(row.get(key), kind)
                c = ws.cell(row=r, column=j, value=v)
                c.font, c.border = BODY_FONT, BORDER
                if kind in FMT and kind != "text":
                    c.number_format = FMT[kind]
                if is_alert:
                    c.fill = ALERT_FILL
                widths[j - 1] = max(widths[j - 1], min(60, _width(_fmt_preview(v, kind)) + 2))
        if total is not None:
            r += 1
            for j, (key, _l, kind) in enumerate(columns, start=1):
                v = _cell_value(total.get(key), kind)
                c = ws.cell(row=r, column=j, value=v)
                c.font, c.border, c.fill = BOLD_FONT, BORDER, TOTAL_FILL
                if kind in FMT and kind != "text":
                    c.number_format = FMT[kind]
                widths[j - 1] = max(widths[j - 1], min(60, _width(_fmt_preview(v, kind)) + 2))
        if note:
            r += 2
            ws.cell(row=r, column=1, value=note).font = Font(name="微软雅黑", size=9, color="7B7E86")
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = max(8, min(60, w))
        ws.freeze_panes = "A2"
        return ws

    def kv_sheet(self, title: str, pairs: list[tuple]):
        """两列指标表：[(指标, 值, kind)]。"""
        rows = [{"k": k, "v": v, "_kind": kind} for k, v, kind in pairs]
        ws = self.wb.create_sheet(title[:31])
        for j, label in enumerate(("指标", "数值"), start=1):
            c = ws.cell(row=1, column=j, value=label)
            c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
            c.alignment = Alignment(horizontal="center")
        for i, row in enumerate(rows, start=2):
            a = ws.cell(row=i, column=1, value=row["k"])
            b = ws.cell(row=i, column=2, value=_cell_value(row["v"], row["_kind"]))
            a.font = b.font = BODY_FONT
            a.border = b.border = BORDER
            if row["_kind"] in FMT and row["_kind"] != "text":
                b.number_format = FMT[row["_kind"]]
        ws.column_dimensions["A"].width = max([_width(r["k"]) for r in rows] + [10]) + 4
        ws.column_dimensions["B"].width = 24
        ws.freeze_panes = "A2"
        return ws

    def caliber_sheet(self, report_name: str, lines: list[str], meta: dict | None = None):
        """口径说明 sheet（必含）。"""
        ws = self.wb.create_sheet("口径说明")
        ws.cell(row=1, column=1, value=f"{report_name} · 口径说明").font = Font(name="微软雅黑", size=12, bold=True,
                                                                              color=BRAND)
        r = 2
        for k, v in (meta or {}).items():
            r += 1
            ws.cell(row=r, column=1, value=k).font = BOLD_FONT
            ws.cell(row=r, column=2, value=str(v)).font = BODY_FONT
        r += 1
        for i, line in enumerate(lines, start=1):
            r += 1
            ws.cell(row=r, column=1, value=f"{i}.").font = BODY_FONT
            c = ws.cell(row=r, column=2, value=line)
            c.font, c.alignment = BODY_FONT, Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions["A"].width = 16
        ws.column_dimensions["B"].width = 110
        return ws

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.wb.save(buf)
        return buf.getvalue()


def _fmt_preview(v, kind):
    if v is None:
        return ""
    if kind == "money":
        try:
            return f"{float(v):,.2f}"
        except Exception:  # noqa: BLE001
            return str(v)
    if kind == "pct":
        return "100.0%"
    if isinstance(v, (date, datetime)):
        return "2026-10-20"
    return v


# ---------------- 文本格式化（docx / 推送） ----------------
def yuan(v) -> str:
    if v is None:
        return "—"
    return f"¥{Decimal(str(v)):,.2f}"


def wan(v) -> str:
    if v is None:
        return "—"
    return f"{Decimal(str(v)) / 10000:,.1f} 万"


def pct(v) -> str:
    if v is None:
        return "—"
    return f"{Decimal(str(v)) * 100:.1f}%"


class DocxDoc:
    def __init__(self, title: str, subtitle: str | None = None):
        from docx import Document
        from docx.oxml.ns import qn
        from docx.shared import Pt, RGBColor
        self._Pt, self._RGB, self._qn = Pt, RGBColor, qn
        self.doc = Document()
        st = self.doc.styles["Normal"]
        st.font.name = "微软雅黑"
        st.font.size = Pt(10.5)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        for name in ("Title", "Heading 1", "Heading 2"):
            s = self.doc.styles[name]
            s.font.name = "微软雅黑"
            s.font.color.rgb = RGBColor(0x1B, 0x32, 0x75)
            s.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "微软雅黑")
        self.doc.add_heading(title, level=0)
        if subtitle:
            p = self.doc.add_paragraph(subtitle)
            p.runs[0].font.color.rgb = RGBColor(0x7B, 0x7E, 0x86)

    def h1(self, text):
        self.doc.add_heading(text, level=1)

    def h2(self, text):
        self.doc.add_heading(text, level=2)

    def p(self, text, muted: bool = False):
        para = self.doc.add_paragraph(text)
        if muted and para.runs:
            para.runs[0].font.color.rgb = self._RGB(0x7B, 0x7E, 0x86)
            para.runs[0].font.size = self._Pt(9)
        return para

    def bullet(self, text):
        return self.doc.add_paragraph(text, style="List Bullet")

    def table(self, headers: list[str], rows: list[list]):
        from docx.oxml import OxmlElement
        t = self.doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        for j, h in enumerate(headers):
            cell = t.rows[0].cells[j]
            cell.text = str(h)
            run = cell.paragraphs[0].runs[0]
            run.font.bold = True
            run.font.color.rgb = self._RGB(0xFF, 0xFF, 0xFF)
            run.font.size = self._Pt(9.5)
            shd = OxmlElement("w:shd")
            shd.set(self._qn("w:val"), "clear")
            shd.set(self._qn("w:color"), "auto")
            shd.set(self._qn("w:fill"), BRAND)
            cell._tc.get_or_add_tcPr().append(shd)
        for row in rows:
            cells = t.add_row().cells
            for j, v in enumerate(row):
                cells[j].text = "" if v is None else str(v)
                for r in cells[j].paragraphs[0].runs:
                    r.font.size = self._Pt(9.5)
        self.doc.add_paragraph()
        return t

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.doc.save(buf)
        return buf.getvalue()
