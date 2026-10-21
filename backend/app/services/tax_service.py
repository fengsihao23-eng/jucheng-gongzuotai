"""税务申报表归集（FR-09-05）：上传申报表 xlsx → 解析期间/税种/计税依据/应纳税额/已缴 → sync_tax_filing 登记 + 原件归档。

一个文件可含多张申报表（每个 sheet 一张）。识别关键字可在 sys_dict(tax_template, default) 覆盖。
"""
from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import save_bytes
from app.integration.bankflow.parser import norm, parse_money
from app.models import SyncTaxFiling
from app.services import dict_service

D = Decimal
TAX_TYPES = ["增值税", "企业所得税", "个人所得税", "附加税"]
DEFAULT_RULE = {
    # 按顺序匹配；“增值税及附加税费申报表”主表为增值税，附加税以“附加税费申报表/附列资料”识别
    "type_keywords": [["附加税费情况表", "附加税"], ["城市维护建设税", "附加税"],                       ["企业所得税", "企业所得税"], ["个人所得税", "个人所得税"], ["扣缴", "个人所得税"],
                      ["增值税", "增值税"]],
    "payable_labels": ["本期应补(退)税额", "本期应补（退）税额", "应纳税额合计", "应纳税额", "本期应纳税额",
                       "应补(退)税额", "本期应补(退)税(费)额", "应补（退）税（费）额", "应纳税(费)额合计", "应扣缴税额", "本期应补(退)税(费)额"],
    "taxable_labels": ["按适用税率计税销售额", "应税销售额", "销售额", "利润总额", "应纳税所得额", "收入额",
                       "计税依据", "增值税税额", "计税(费)依据"],
    "paid_labels": ["本期已缴税额", "已缴税额", "本期已缴税(费)额", "已预缴所得税额", "已缴(扣)税额"],
    "period_patterns": [r"税款所属(?:期|时间|期间)[:：]?\s*(\d{4})[-年./](\d{1,2})",
                        r"所属期[:：]?\s*(\d{4})[-年./](\d{1,2})", r"(\d{4})年(\d{1,2})月"],
    "filing_patterns": [r"填表日期[:：]?\s*(\d{4})[-年./](\d{1,2})[-月./](\d{1,2})",
                        r"申报日期[:：]?\s*(\d{4})[-年./](\d{1,2})[-月./](\d{1,2})"],
}


def rule(db: Session) -> dict:
    return {**DEFAULT_RULE, **(dict_service.get(db, "tax_template", "default") or {})}


VALUE_COLS = ["本月数", "本期数", "本期金额", "金额", "本期", "税额"]


def _value_col(table: list[list]) -> int | None:
    for row in table[:15]:
        cells = [norm(c) for c in row]
        for name in VALUE_COLS:
            if norm(name) in cells:
                return cells.index(norm(name))
    return None


def _find_amount(table: list[list], labels: list[str]) -> D | None:
    """按标签优先级定位行；金额取「本月数」列，否则取标签后第一个金额（跳过“栏次”序号列）。"""
    vcol = _value_col(table)
    for key in (norm(x) for x in labels):
        for row in table:
            cells = [norm(c) for c in row]
            idx = next((i for i, c in enumerate(cells) if c and (c == key or (c.startswith(key) and len(c) - len(key) <= 4))), None)
            if idx is None:
                continue
            if vcol is not None and vcol > idx and vcol < len(row):
                try:
                    m = parse_money(row[vcol])
                    if m is not None:
                        return m
                except ValueError:
                    pass
            for j, v in enumerate(row[idx + 1:], start=idx + 1):
                if isinstance(v, int) and j == idx + 1 and abs(v) < 100:
                    continue  # 栏次
                if isinstance(v, (int, float, D)) or (isinstance(v, str) and re.fullmatch(r"-?[\d,]+(\.\d+)?", v.strip())):
                    try:
                        m = parse_money(v)
                    except ValueError:
                        continue
                    if m is not None:
                        return m
    return None


def parse_sheet(rows: list[list], rl: dict, sheet_name: str = "") -> dict:
    text = " ".join(str(c) for r in rows[:8] for c in r if c not in (None, "")) + " " + sheet_name
    tax_type = next((t for kw, t in rl["type_keywords"] if kw in text), None)
    full = " ".join(str(c) for r in rows for c in r if c not in (None, ""))
    period = None
    for pat in rl["period_patterns"]:
        m = re.search(pat, full)
        if m:
            period = f"{int(m.group(1))}-{int(m.group(2)):02d}"
            break
    filing = None
    for pat in rl["filing_patterns"]:
        m = re.search(pat, full)
        if m:
            filing = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            break
    out = {"sheet": sheet_name, "tax_type": tax_type, "period": period, "filing_date": filing,
           "payable_amount": _find_amount(rows, rl["payable_labels"]),
           "taxable_amount": _find_amount(rows, rl["taxable_labels"]),
           "paid_amount": _find_amount(rows, rl["paid_labels"]), "errors": []}
    if not tax_type:
        out["errors"].append({"sheet": sheet_name, "field": "税种", "message": "未识别税种（增值税/企业所得税/个人所得税/附加税）"})
    if not period:
        out["errors"].append({"sheet": sheet_name, "field": "所属期", "message": "未识别税款所属期"})
    if out["payable_amount"] is None:
        out["errors"].append({"sheet": sheet_name, "field": "应纳税额", "message": "未找到应纳税额/应补(退)税额"})
    return out


def parse_file(db: Session, file_bytes: bytes, filename: str) -> list[dict]:
    if not filename.lower().endswith(".xlsx"):
        return [{"sheet": "", "errors": [{"sheet": "", "field": "文件", "message": "仅支持 .xlsx 申报表（电子税务局导出）"}]}]
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:  # noqa: BLE001
        return [{"sheet": "", "errors": [{"sheet": "", "field": "文件", "message": f"无法读取 Excel：{exc}"}]}]
    rl = rule(db)
    out = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if not any(any(c not in (None, "") for c in r) for r in rows):
            continue
        out.append(parse_sheet(rows, rl, ws.title))
    return out


def register(db: Session, file_bytes: bytes, filename: str, uploader_id: int | None, *,
             overrides: dict | None = None) -> dict:
    """解析 + 归档 + 登记（同期间同税种覆盖更新）。overrides 可人工指定 period/tax_type/payable_amount（单表时）。"""
    parsed = parse_file(db, file_bytes, filename)
    ov = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    if ov and len(parsed) == 1:
        p = parsed[0]
        for k in ("period", "tax_type"):
            if ov.get(k):
                p[k] = ov[k]
        if ov.get("payable_amount") is not None:
            p["payable_amount"] = D(str(ov["payable_amount"]))
        p["errors"] = [e for e in p.get("errors", []) if not (
            (e["field"] == "税种" and p.get("tax_type")) or (e["field"] == "所属期" and p.get("period"))
            or (e["field"] == "应纳税额" and p.get("payable_amount") is not None))]
    ok_items = [p for p in parsed if not p.get("errors")]
    errors = [e for p in parsed for e in p.get("errors", [])]
    if not ok_items:
        return {"registered": [], "errors": errors or [{"sheet": "", "field": "文件", "message": "未解析到申报表"}],
                "file": None}
    period = ok_items[0]["period"]
    f = save_bytes(db, data=file_bytes, filename=filename, category="tax", key=f"tax/{period}/{filename}",
                   uploader_id=uploader_id)
    reg = []
    for p in ok_items:
        if p["tax_type"] not in TAX_TYPES:
            errors.append({"sheet": p["sheet"], "field": "税种", "message": f"不支持的税种 {p['tax_type']}"})
            continue
        row = db.scalar(select(SyncTaxFiling).where(SyncTaxFiling.period == p["period"],
                                                    SyncTaxFiling.tax_type == p["tax_type"]))
        created = row is None
        if created:
            row = SyncTaxFiling(period=p["period"], tax_type=p["tax_type"], payable_amount=p["payable_amount"])
            db.add(row)
        row.payable_amount, row.taxable_amount, row.paid_amount = p["payable_amount"], p["taxable_amount"], p["paid_amount"]
        row.filing_date, row.file_id, row.uploader_id = p["filing_date"], f.id, uploader_id
        db.flush()
        reg.append({"id": row.id, "period": row.period, "tax_type": row.tax_type, "payable_amount": row.payable_amount,
                    "taxable_amount": row.taxable_amount, "paid_amount": row.paid_amount, "filing_date": row.filing_date,
                    "created": created, "sheet": p["sheet"]})
    return {"registered": reg, "errors": errors, "file_id": f.id}


# ---------------- 生成申报表（样例 / 种子） ----------------
TITLES = {"增值税": "增值税及附加税费申报表（一般纳税人适用）", "企业所得税": "中华人民共和国企业所得税月（季）度预缴纳税申报表（A类）",
          "个人所得税": "个人所得税扣缴申报表", "附加税": "附加税费情况表（城市维护建设税、教育费附加、地方教育附加）"}


def make_tax_xlsx(filings: list[dict], taxpayer: str = "广东聚成数智科技有限公司",
                  tax_no: str = "91440101MA5XXXXXXX") -> bytes:
    """filings: [{tax_type, period, taxable, payable, paid, filing_date}] → 每项一个 sheet 的申报表 xlsx。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    wb.remove(wb.active)
    for f in filings:
        t = f["tax_type"]
        ws = wb.create_sheet({"增值税": "增值税主表", "企业所得税": "企业所得税预缴", "个人所得税": "个税扣缴",
                              "附加税": "附加税费"}[t])
        y, m = f["period"].split("-")
        ws.append([TITLES[t]])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([f"税款所属期：{y}年{int(m):02d}月01日 至 {y}年{int(m):02d}月末", None, None,
                   f"填表日期：{f['filing_date']:%Y-%m-%d}"])
        ws.append([f"纳税人名称：{taxpayer}", None, None, f"纳税人识别号：{tax_no}"])
        ws.append(["金额单位：元（列至角分）"])
        ws.append(["项目", "栏次", "本月数", "本年累计"])
        lab = {"增值税": ("按适用税率计税销售额", "应纳税额合计", "本期已缴税额"),
               "企业所得税": ("利润总额", "本期应补(退)税额", "已预缴所得税额"),
               "个人所得税": ("收入额", "应扣缴税额", "已缴(扣)税额"),
               "附加税": ("计税(费)依据", "本期应补(退)税(费)额", "本期已缴税(费)额")}[t]
        ws.append([lab[0], 1, float(f["taxable"]), float(f["taxable"]) * int(m)])
        if t == "增值税":
            ws.append(["销项税额", 11, round(float(f["taxable"]) * 0.09, 2), None])
            ws.append(["进项税额", 12, round(float(f["taxable"]) * 0.09 - float(f["payable"]), 2), None])
        ws.append([lab[1], 24, float(f["payable"]), None])
        ws.append([lab[2], 27, float(f.get("paid") or 0), None])
        ws.append([])
        ws.append(["声明：此表是根据国家税收法律法规及相关规定填写的，本人（单位）对填报内容的真实性、可靠性、完整性负责。"])
        for col, w in zip("ABCD", (34, 8, 18, 30)):
            ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
