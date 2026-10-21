"""网银流水模板化解析器（FR-09-01/02）。

- 列映射配置化：sys_dict(dict_type=bank_template)，每个 key 一套银行模板；库内为空时用 BUILTIN_TEMPLATES 兜底
- 自动识别表头：在前 header_scan 行内寻找与模板别名匹配度最高的一行；未指定模板时在全部模板中取最佳
- 行级错误定位：{row(文件中的行号，从 1 起), field(原始列名), message}
- 本模块只做“文件 → 标准行”的纯解析；入库（指纹去重/余额连续性/导入报告）在 bankflow_service

对外（供 MCP fin.bank_flow_sync 复用）：
    import_file(db, file_bytes, filename, uploader_id, source) -> FinBankImport
    auto_parse_dir(db) -> dict
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

# 标准字段：trade_date trade_time income expense amount direction balance summary counterparty
#          counterparty_account serial_no account currency
BUILTIN_TEMPLATES: dict[str, dict] = {
    "cmb": {
        "name": "招商银行", "bank": "招商银行", "header_scan": 15, "signature": ["交易日", "借方金额", "贷方金额", "余额"],
        "columns": {
            "trade_date": ["交易日", "交易日期", "记账日期"],
            "trade_time": ["交易时间"],
            "income": ["贷方金额", "收入金额"],
            "expense": ["借方金额", "支出金额"],
            "balance": ["余额", "账户余额"],
            "summary": ["摘要", "交易摘要"],
            "purpose": ["用途", "附言"],
            "counterparty": ["对方户名", "收(付)方名称", "收付方名称"],
            "counterparty_account": ["对方账号", "收(付)方账号", "收付方账号"],
            "serial_no": ["流水号", "交易流水号", "业务参考号"],
            "account": ["账号", "本方账号"],
        },
        "account_patterns": [r"账号[:：]\s*([0-9*\s]{6,})"],
    },
    "icbc": {
        "name": "工商银行", "bank": "工商银行", "header_scan": 15, "signature": ["借贷标志", "发生额", "对方单位名称"],
        "columns": {
            "account": ["本方账号", "账号"],
            "trade_date": ["交易时间", "交易日期", "入账日期"],
            "direction": ["借贷标志", "借贷方向"],
            "amount": ["发生额", "交易金额"],
            "income": ["贷方发生额"],
            "expense": ["借方发生额"],
            "balance": ["余额", "账户余额"],
            "counterparty": ["对方单位名称", "对方户名", "对方单位"],
            "counterparty_account": ["对方账号"],
            "summary": ["摘要"],
            "purpose": ["用途"],
            "serial_no": ["交易流水号", "凭证号"],
        },
        "direction_values": {"贷": 1, "收": 1, "收入": 1, "借": 2, "支": 2, "支出": 2},
        "account_patterns": [r"账号[:：]\s*([0-9*\s]{6,})"],
    },
    "ccb": {
        "name": "建设银行", "bank": "建设银行", "header_scan": 15, "signature": ["账户明细编号-交易流水号", "对方户名"],
        "columns": {
            "account": ["账号", "账户"],
            "trade_date": ["交易时间", "记账日期", "交易日期"],
            "expense": ["借方发生额（支取）", "借方发生额", "支出金额"],
            "income": ["贷方发生额（收入）", "贷方发生额", "收入金额"],
            "balance": ["余额", "账户余额"],
            "currency": ["币种"],
            "counterparty": ["对方户名", "对方账户名称"],
            "counterparty_account": ["对方账号"],
            "summary": ["摘要", "备注"],
            "serial_no": ["账户明细编号-交易流水号", "交易流水号"],
        },
        "account_patterns": [r"账号[:：]\s*([0-9*\s]{6,})"],
    },
}

REQUIRED_HINT = "需至少包含：交易日期、金额（收/支两列，或 发生额+借贷标志）"


@dataclass
class ParsedRow:
    row: int  # 文件行号（1 起）
    trade_date: date
    trade_time: datetime | None
    direction: int
    amount: Decimal
    balance: Decimal | None
    summary: str | None
    counterparty: str | None
    counterparty_account: str | None
    serial_no: str | None
    account_raw: str | None


@dataclass
class ParseResult:
    template_key: str | None
    template_name: str | None
    header_row: int | None
    total_rows: int = 0
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    header_account: str | None = None


def norm(s) -> str:
    return re.sub(r"[\s　:：*]", "", str(s or "")).replace("（", "(").replace("）", ")").lower()


# ---------------- 读表 ----------------
def read_table(file_bytes: bytes, filename: str) -> list[list]:
    name = filename.lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        text = None
        for enc in ("utf-8-sig", "gb18030"):
            try:
                text = file_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("CSV 编码无法识别（支持 UTF-8 / GBK）")
        return [list(r) for r in csv.reader(io.StringIO(text))]
    if name.endswith(".xls") and not name.endswith(".xlsx"):
        raise ValueError("暂不支持旧版 .xls，请在网银导出 xlsx/csv 或用 Excel 另存为 xlsx")
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法读取 Excel 文件：{exc}") from exc
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


# ---------------- 表头识别 ----------------
def _match_header(header: list, tpl: dict) -> tuple[int, dict[str, int]]:
    cells = [norm(c) for c in header]
    mapping: dict[str, int] = {}
    score = 0
    for fld, aliases in tpl["columns"].items():
        for alias in aliases:
            a = norm(alias)
            if a in cells:
                mapping[fld] = cells.index(a)
                score += 2
                break
    for sig in tpl.get("signature", []):
        if norm(sig) in cells:
            score += 1
    return score, mapping


def _usable(mapping: dict[str, int]) -> bool:
    has_amount = ("income" in mapping and "expense" in mapping) or ("amount" in mapping and "direction" in mapping)
    return "trade_date" in mapping and has_amount


def detect(table: list[list], templates: dict[str, dict], template_key: str | None = None):
    """返回 (template_key, header_row_index, mapping)；找不到返回 (None, None, {})。"""
    cands = {template_key: templates[template_key]} if template_key and template_key in templates else templates
    best = (None, None, {}, 0)
    for key, tpl in cands.items():
        scan = int(tpl.get("header_scan", 15))
        for i, row in enumerate(table[:scan]):
            if not any(c not in (None, "") for c in row):
                continue
            score, mapping = _match_header(row, tpl)
            if _usable(mapping) and score > best[3]:
                best = (key, i, mapping, score)
    return best[:3]


# ---------------- 值解析 ----------------
def parse_money(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v)).quantize(Decimal("0.01"))
    s = str(v).strip().replace(",", "").replace("，", "").replace("¥", "").replace("￥", "").replace("元", "")
    if s in ("", "-", "--", "—"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        d = Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(f"金额格式无效：{v!r}")
    return -d if neg else d


_DATE_FMTS = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d", "%Y年%m月%d日",
              "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y%m%d %H:%M:%S",
              "%Y年%m月%d日 %H:%M:%S", "%Y-%m-%d-%H.%M.%S"]


def parse_datetime(v) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, time())
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:  # Excel 序列日期
        return datetime(1899, 12, 30) + timedelta(days=float(v))
    s = str(v or "").strip()
    if not s:
        raise ValueError("日期为空")
    if re.fullmatch(r"\d{8}\.0", s):
        s = s[:-2]
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"日期格式无效：{v!r}")


def parse_time(v) -> time | None:
    if v in (None, ""):
        return None
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    s = str(v).strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%H%M%S"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


SKIP_ROW = re.compile(r"^(合计|本页合计|总计|小计|累计|共\d+笔)")


def parse_table(table: list[list], templates: dict[str, dict] | None = None,
                template_key: str | None = None) -> ParseResult:
    templates = templates or BUILTIN_TEMPLATES
    key, hidx, mapping = detect(table, templates, template_key)
    if key is None:
        hint = f"（指定模板：{templates[template_key]['name']}）" if template_key in templates else ""
        return ParseResult(None, None, None, errors=[{"row": 0, "field": "表头",
                                                      "message": f"未识别到流水表头{hint}，{REQUIRED_HINT}"}])
    tpl = templates[key]
    header = table[hidx]
    res = ParseResult(key, tpl.get("name", key), hidx + 1)
    # 表头上方的账号信息
    for row in table[:hidx]:
        line = " ".join(str(c) for c in row if c not in (None, ""))
        for pat in tpl.get("account_patterns", []):
            m = re.search(pat, line)
            if m:
                res.header_account = re.sub(r"\s", "", m.group(1))
                break
    col_name = {f: str(header[i]).strip() for f, i in mapping.items()}
    dir_values = {norm(k): v for k, v in (tpl.get("direction_values") or {"贷": 1, "收": 1, "借": 2, "支": 2}).items()}

    def cell(row, fld):
        i = mapping.get(fld)
        return row[i] if i is not None and i < len(row) else None

    for ridx in range(hidx + 1, len(table)):
        row = table[ridx]
        rownum = ridx + 1
        if not any(c not in (None, "") for c in row):
            continue
        first = next((str(c).strip() for c in row if c not in (None, "")), "")
        if SKIP_ROW.match(first):
            continue
        res.total_rows += 1
        errs = []
        try:
            dt = parse_datetime(cell(row, "trade_date"))
        except ValueError as e:
            errs.append({"row": rownum, "field": col_name["trade_date"], "message": str(e)})
            dt = None
        if dt is not None and "trade_time" in mapping:
            t = parse_time(cell(row, "trade_time"))
            if t:
                dt = datetime.combine(dt.date(), t)
        direction, amount = None, None
        try:
            if "income" in mapping and "expense" in mapping and "amount" not in mapping:
                inc, exp = parse_money(cell(row, "income")), parse_money(cell(row, "expense"))
                inc = inc if inc else None
                exp = exp if exp else None
                if inc and exp:
                    errs.append({"row": rownum, "field": f"{col_name['income']}/{col_name['expense']}",
                                 "message": "收入与支出同时有值，方向冲突"})
                elif not inc and not exp:
                    errs.append({"row": rownum, "field": f"{col_name['income']}/{col_name['expense']}",
                                 "message": "收入与支出均为空"})
                else:
                    direction, amount = (1, inc) if inc else (2, exp)
            else:
                amt = parse_money(cell(row, "amount"))
                raw_dir = norm(cell(row, "direction"))
                d = dir_values.get(raw_dir)
                if d is None and raw_dir:
                    d = next((v for k, v in dir_values.items() if raw_dir.startswith(k)), None)
                if amt is None or amt == 0:
                    errs.append({"row": rownum, "field": col_name.get("amount", "金额"), "message": "金额为空或为 0"})
                elif d is None:
                    errs.append({"row": rownum, "field": col_name.get("direction", "借贷标志"),
                                 "message": f"无法识别收支方向：{cell(row, 'direction')!r}"})
                else:
                    direction, amount = d, abs(amt)
        except ValueError as e:
            fld = col_name.get("amount") or f"{col_name.get('income')}/{col_name.get('expense')}"
            errs.append({"row": rownum, "field": fld, "message": str(e)})
        if amount is not None and amount < 0:
            errs.append({"row": rownum, "field": col_name.get("amount") or col_name.get("income"),
                         "message": "金额不能为负数（请使用收/支列区分方向）"})
        balance = None
        if "balance" in mapping:
            try:
                balance = parse_money(cell(row, "balance"))
            except ValueError as e:
                errs.append({"row": rownum, "field": col_name["balance"], "message": str(e)})
        if errs:
            res.errors.extend(errs)
            continue
        summary = _text(cell(row, "summary"))
        purpose = _text(cell(row, "purpose"))
        if purpose and purpose != summary:
            summary = f"{summary} {purpose}" if summary else purpose
        res.rows.append(ParsedRow(
            row=rownum, trade_date=dt.date(), trade_time=dt if (dt.time() != time()) else None,
            direction=direction, amount=amount, balance=balance, summary=(summary or "")[:255] or None,
            counterparty=(_text(cell(row, "counterparty")) or "")[:128] or None,
            counterparty_account=(_text(cell(row, "counterparty_account")) or "")[:64] or None,
            serial_no=_text(cell(row, "serial_no")), account_raw=_text(cell(row, "account"))))
    return res


# ---------------- 对外入口（委托入库服务） ----------------
def import_file(db, file_bytes: bytes, filename: str, uploader_id: int | None = None, source: str = "upload",
                template_key: str | None = None, account: str | None = None):
    """解析并入库一份网银流水文件，返回 FinBankImport（已提交）。"""
    from app.services import bankflow_service
    return bankflow_service.import_file(db, file_bytes, filename, uploader_id=uploader_id, source=source,
                                        template_key=template_key, account=account)


def auto_parse_dir(db) -> dict:
    """扫描 STORAGE_DIR 同级 inbox/bankflow/ 目录新文件并解析（幂等：按文件 sha256 去重）。"""
    from app.services import bankflow_service
    return bankflow_service.auto_parse_dir(db)
