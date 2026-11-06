"""确定性解析项目月度分析表；读取缓存值及公式，不执行单元格内容。"""
import calendar
import hashlib
import io
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

from app.core.response import bad_request

ZERO = Decimal(0)
COST_KINDS = {"cost", "expense", "construction"}
METRICS = dict(zip("CDEFGHIJKLMNOPQ", ["contract", "invoiced", "received", "unreceived",
    "equipment", "construction", "traffic", "meals", "courier", "entertainment", "tea_wine",
    "project_expense", "other", "tax", "cash_profit"]))
KINDS = {"成本费用": "cost", "费用报销明细": "expense", "施工费": "construction",
         "待支付成本": "payable", "开票回款": "receipt", "开票回款明细": "receipt"}


def decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise bad_request(f"金额无效：{str(value)[:40]}") from None


def money(value):
    return decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.fullmatch(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]00:00:00)?", str(value).strip())
    if match:
        try:
            return date(*map(int, match.groups()))
        except ValueError:
            pass
    return None


def read_archive(data):
    if len(data) > 512 * 1024 * 1024:
        raise bad_request("ZIP 超过 512MB")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            items = [i for i in archive.infolist() if not i.is_dir()]
            if len(items) > 500 or sum(i.file_size for i in items) > 512 * 1024 * 1024:
                raise bad_request("ZIP 文件数或解压体积超限")
            out, names = [], set()
            for item in items:
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                    raise bad_request("ZIP 内含不安全路径")
                if item.filename.startswith("__MACOSX/") or path.name in {".DS_Store"}:
                    continue
                if path.suffix.lower() != ".xlsx" or item.file_size > 20 * 1024 * 1024:
                    raise bad_request(f"不支持的项目报表：{path.name}")
                if path.name in names:
                    raise bad_request("ZIP 内报表同名，无法唯一溯源")
                names.add(path.name)
                out.append((path.name, archive.read(item)))
            if not out:
                raise bad_request("ZIP 内没有项目报表")
            return out
    except BadZipFile:
        raise bad_request("ZIP 损坏") from None


def parse_report(data, filename):
    period_match = re.match(r"(20\d{2})年(\d{2})月", filename)
    if not period_match or not 1 <= int(period_match[2]) <= 12:
        raise bad_request("报表文件名须明确年份和月份")
    year, month = map(int, period_match.groups())
    period = f"{year:04}-{month:02}"
    cutoff = date(year, month, calendar.monthrange(year, month)[1])
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 5000 or sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
                raise bad_request("报表展开后过大")
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        fw = load_workbook(io.BytesIO(data), data_only=False, read_only=True)
    except (BadZipFile, ValueError, TypeError) as exc:
        raise bad_request(f"报表读取失败：{filename}，{type(exc).__name__}") from exc
    issues, sheets, entries = [], [], []

    def issue(code, message, sheet=None, row=None, **details):
        issues.append({"code": code, "message": message, "sheet": sheet, "row": row, **details})

    try:
        for ws in wb:
            # Exported accounting sheets can contain tens of thousands of styled
            # blank rows. Read them fully, but limit actual populated business rows.
            if (ws.max_row or 0) > 100000 or (ws.max_column or 0) > 100:
                raise bad_request(f"工作表过大：{ws.title}")
            frows = list(fw[ws.title].iter_rows())
            rows, cached = [], {}
            for rn, cells in enumerate(ws.iter_rows(), 1):
                row = []
                for cell in cells:
                    if cell.value is None:
                        continue
                    formula = frows[rn - 1][cell.column - 1].value
                    cached[(cell.row, cell.column)] = cell.value
                    row.append({"cell": cell.coordinate, "value": _value(cell.value),
                        **({"formula": formula} if isinstance(formula, str) and formula.startswith("=") else {})})
                    if cell.data_type == "e":
                        issue("formula_error", f"源表错误值 {cell.value}", ws.title, rn, cell=cell.coordinate)
                if row:
                    rows.append({"row": rn, "cells": row})
                    if len(rows) > 20000:
                        raise bad_request(f"工作表有效行数过多：{ws.title}")
            # Formula cells without cached results cannot silently become zero.
            for row in frows:
                for cell in row:
                    if cell.data_type == "f" and cached.get((cell.row, cell.column)) is None:
                        raise bad_request(f"{filename} {ws.title}!{cell.coordinate} 缺少公式计算结果，请先在 Excel 重算保存")
            sheets.append({"name": ws.title, "rows": rows})
        if "主表" not in wb.sheetnames:
            raise bad_request("缺少主表")
        ws = wb["主表"]
        if ws["C4"].value != "合同金额" or ws["E4"].value != "已收金额":
            raise bad_request("主表表头不符合项目分析表格式")
        main_last_row = max(r["row"] for s in sheets if s["name"] == "主表" for r in s["rows"])
        totals_rows = [r for r in range(6, main_last_row + 1) if ws.cell(r, 2).value == "合计"]
        if len(totals_rows) != 1:
            raise bad_request("主表合计行不唯一")
        total_row = totals_rows[0]
        totals = {metric: str(decimal(ws[f"{col}{total_row}"].value)) if ws[f"{col}{total_row}"].value is not None else None
                  for col, metric in METRICS.items()}
        for key in ("contract", "equipment", "construction", "received"):
            if totals[key] is None:
                raise bad_request(f"主表缺少必要控制总额：{key}")
        monthly, source_pending = [], ZERO
        for rn in range(6, total_row):
            label = str(ws.cell(rn, 2).value or "").strip()
            if not label:
                continue
            metrics = {key: str(decimal(ws[f"{col}{rn}"].value)) if ws[f"{col}{rn}"].value is not None else None
                       for col, key in METRICS.items()}
            monthly.append({"row": rn, "label": label, **metrics})
            if label == "未支付":
                source_pending += sum((decimal(metrics[k]) for k in ("equipment", "construction") if metrics[k] is not None), ZERO)
        for sheet in sheets:
            name = sheet["name"]
            if name == "主表":
                continue
            if name not in KINDS:
                raise bad_request(f"未识别工作表 {name}，不能跳过其中的数据")
            kind = KINDS[name]
            rows = sheet["rows"]
            header = next(({re.sub(r"\d+", "", c["cell"]): str(c["value"]).strip() for c in r["cells"]}
                           for r in rows if r["row"] == 2), {})
            if "摘要" not in header.values() and kind != "payable":
                raise bad_request(f"{name} 缺少摘要列")
            sheet_entries = []
            for raw in rows:
                if raw["row"] <= 2:
                    continue
                vals = {re.sub(r"\d+", "", c["cell"]): c["value"] for c in raw["cells"]}
                rn = raw["row"]
                if kind == "payable":
                    if not isinstance(vals.get("A"), (int, float)):
                        continue
                    pending_cols = [col for col, label in header.items() if label in {"未提单未付金额", "票据金额", "已提单应付", "待支付金额"}]
                    if not pending_cols or any(vals.get(col) is None for col in pending_cols):
                        raise bad_request(f"{name} 第 {rn} 行待付金额缺失")
                    pending = sum((decimal(vals[col]) for col in pending_cols), ZERO)
                    approval_col = next((col for col, label in header.items() if label == "提单审批编号"), None)
                    item = {"supplier": vals.get("C"), "company": vals.get("B"), "summary": vals.get("D"),
                            "contract_amount": str(decimal(vals["E"])), "paid": str(decimal(vals["F"])),
                            "amount": str(pending), "approval": str(vals.get(approval_col, "")),
                            "date": None, "posting_state": "snapshot"}
                    if money(decimal(item["contract_amount"]) - decimal(item["paid"])) != money(pending):
                        issue("payable_balance", "供应商合同额减已付与待付金额不一致", name, rn,
                              calculated=str(decimal(item["contract_amount"]) - decimal(item["paid"])), reported=str(pending))
                else:
                    dt = _date(vals.get("A"))
                    if dt is None:
                        if vals.get("B") is not None and vals.get("C") is not None:
                            raise bad_request(f"{name} 第 {rn} 行日期无效，不能跳过业务行")
                        continue  # Original subtotals stay in source_cells and are checked below.
                    amount_col = "E" if kind in {"expense", "construction"} else "D"
                    if vals.get(amount_col) is None or not str(vals.get("C", "")).strip():
                        raise bad_request(f"{name} 第 {rn} 行金额或摘要缺失")
                    amount = decimal(vals[amount_col])
                    if money(amount) != amount:
                        raise bad_request(f"{name} 第 {rn} 行明细金额超过两位小数")
                    item = {"date": dt.isoformat(), "voucher": str(vals.get("B", "")), "summary": str(vals["C"]).strip(),
                            "amount": str(amount), "subject": vals.get("D") if kind in {"expense", "construction"} else None,
                            "supplier": next((vals.get(col) for col, label in header.items() if label in {"供应商名称", "客户名称"}), None),
                            "input_tax": next((str(vals[col]) for col, label in header.items() if label == "进项税额" and col in vals), None),
                            "posting_state": "ready" if kind in COST_KINDS else "reference"}
                    remark = vals.get("F") if header.get("F") == "备注" else None
                    construction = kind == "construction" or remark == "施工费" or bool(re.search(r"(付.*工程款|施工费用|施工费-|支付工程款)", item["summary"]))
                    item["cost_type"] = "历史报销" if kind == "expense" else "分包" if construction else "采购"
                    if kind in COST_KINDS and str(item["summary"]).startswith("冲销") and amount > 0:
                        item["posting_state"] = "held"
                        issue("reversal_sign", "冲销摘要对应正数，保留原值待核对，暂不计入正式成本", name, rn, amount=str(amount))
                    if dt > cutoff:
                        item["posting_state"] = "held"
                        issue("after_cutoff", "凭证日期晚于报表截止日，暂不计入正式成本", name, rn)
                item.update({"sheet": name, "source_row": rn, "kind": kind})
                canonical = {k: item.get(k) for k in ("kind", "date", "voucher", "summary", "amount", "subject", "supplier", "approval")}
                item["fingerprint"] = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                if item["kind"] in COST_KINDS and any(e["fingerprint"] == item["fingerprint"] for e in entries):
                    issue("duplicate_voucher", "相同凭证明细重复出现，只挂接一条正式成本", name, rn)
                entries.append(item)
                sheet_entries.append(item)
            if kind != "payable":
                for raw in rows:
                    vals = {re.sub(r"\d+", "", c["cell"]): c["value"] for c in raw["cells"]}
                    label = str(vals.get("A", ""))
                    amount_col = "E" if kind in {"expense", "construction"} else "D"
                    if not re.search(r"小计|合计", label) or vals.get(amount_col) is None:
                        continue
                    selected = sheet_entries
                    ym = re.search(r"(20\d{2})[.年/-](\d{1,2})", label)
                    yy = re.search(r"(20\d{2})年?合计", label)
                    mm = re.search(r"^(\d{1,2})月小计", label)
                    if ym:
                        selected = [e for e in selected if e["date"].startswith(f"{int(ym[1]):04}-{int(ym[2]):02}")]
                    elif yy:
                        selected = [e for e in selected if e["date"].startswith(yy[1])]
                    elif mm:
                        selected = [e for e in selected if e["date"].startswith(f"{year:04}-{int(mm[1]):02}")]
                    actual = sum((decimal(e["amount"]) for e in selected), ZERO)
                    if money(actual) != money(vals[amount_col]):
                        issue("subtotal", f"{label}与按凭证日期重算的明细合计不一致", name, raw["row"],
                              reported=str(vals[amount_col]), calculated=str(actual))
        detail_cost = sum((decimal(e["amount"]) for e in entries if e["kind"] in {"cost", "construction"}), ZERO)
        detail_expense = sum((decimal(e["amount"]) for e in entries if e["kind"] == "expense"), ZERO)
        pending = sum((decimal(e["amount"]) for e in entries if e["kind"] == "payable"), ZERO)
        source_cost = decimal(totals["equipment"]) + decimal(totals["construction"])
        source_expense = sum((decimal(totals[k]) for k in ("traffic", "meals", "courier", "entertainment", "tea_wine", "project_expense", "other") if totals[k] is not None), ZERO)
        controls = [
            {"label": "成本明细加待支付与主表成本", "reported": str(source_cost), "calculated": str(detail_cost + pending)},
            {"label": "报销明细与主表费用（不含税费测算）", "reported": str(source_expense), "calculated": str(detail_expense)},
            {"label": "待支付明细与主表未支付", "reported": str(source_pending), "calculated": str(pending)},
        ]
        for control in controls:
            control["difference"] = str(money(decimal(control["reported"]) - decimal(control["calculated"])))
            if decimal(control["difference"]) != ZERO:
                issue("control_total", control["label"] + "存在差额", **control)
        held = [e for e in entries if e.get("posting_state") == "held"]
        summary = {"title": str(ws["B2"].value), "cutoff": cutoff.isoformat(), "totals": totals,
                   "monthly": monthly, "controls": controls, "detail_cost": str(detail_cost),
                   "detail_expense": str(detail_expense), "payable": str(pending),
                   "held_amount": str(sum((decimal(e["amount"]) for e in held), ZERO)), "held_rows": len(held)}
        return {"filename": filename, "sha256": hashlib.sha256(data).hexdigest(), "period": period,
                "entries": entries, "summary": summary, "issues": issues, "sheets": sheets}
    finally:
        wb.close()
        fw.close()
