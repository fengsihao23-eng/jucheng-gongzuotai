"""Excel 数据清洗助手（PRD 7.4 专项，P1）。

流程：上传文件 + 用途（target 或自由文本「导入合同台账」）→ 表头识别 → 列映射（同义词/模糊/值特征推断，带置信度）
→ 格式统一（日期/金额/编码/客户名/阶段/业务线）→ 去重（文件内 + 库内）→ 校验错误清单 → 预览（用户可改映射）
→ 确认导入对应台账（跳过 error/duplicate）→ 清洗报告 xlsx。任务记录 clean_job。

清洗模板配置在 sys_dict(clean_template)，key = target，value = {name, fields:[{key,label,type,required,synonyms}]}
每行输出：_row 源行号、_status ok|fixed|duplicate|error、_issues 问题、_changes [{field,label,from,to}]
"""
import csv
import io
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.orm import Session

from app.core.permissions import BUSINESS_LINES
from app.core.response import BizError, bad_request
from app.core.timeutil import today
from app.models import BizContract, BizOpportunity, BizPayment, BizProject, CleanJob, SysUser
from app.services import dict_service

TARGET_NAMES = {"contract": "合同台账", "opportunity": "商机信息表", "payment": "回款登记", "project": "项目建档"}


def _f(key, label, type_, required=False, synonyms=()):
    return {"key": key, "label": label, "type": type_, "required": required, "synonyms": list(synonyms)}


DEFAULT_TEMPLATES: dict[str, dict] = {
    "contract": {"name": "合同台账", "fields": [
        _f("contract_no", "合同编号", "contract_no", False, ["合同号", "合同编码", "编号", "合同序号", "contract no", "contract_no"]),
        _f("name", "合同名称", "text", False, ["合同名", "项目名称", "标的", "合同标题", "contract name"]),
        _f("project_code", "项目编码", "project_code", False, ["项目编号", "项目代码", "所属项目", "项目号", "project code", "project_code"]),
        _f("customer_name", "客户名称", "customer", True, ["客户", "甲方", "单位名称", "customer", "采购人", "采购单位", "建设单位", "甲方单位", "业主"]),
        _f("amount", "合同金额", "money", True, ["金额", "合同额", "含税金额", "总价", "合同总价", "签约金额", "合同价款", "amount"]),
        _f("sign_date", "签订日期", "date", True, ["签订日", "签约日期", "日期", "签署日期", "签约日", "合同日期", "sign date"]),
        _f("business_line", "业务线", "business_line", True, ["业务板块", "业务类型", "产品线", "板块", "业务", "business line"]),
        _f("owner", "销售负责人", "user", False, ["负责人", "销售", "经办人", "业务员", "签约人", "owner", "跟进人"]),
        _f("remark", "备注", "text", False, ["说明", "附注", "remark", "备注说明"]),
    ]},
    "opportunity": {"name": "商机信息表", "fields": [
        _f("name", "商机名称", "text", True, ["商机", "项目名称", "机会名称", "名称", "opportunity"]),
        _f("customer_name", "客户名称", "customer", True, ["客户", "甲方", "单位名称", "customer", "采购人", "采购单位", "业主"]),
        _f("business_line", "业务线", "business_line", True, ["业务板块", "业务类型", "产品线", "板块", "业务"]),
        _f("stage", "商机阶段", "stage", False, ["阶段", "进展", "状态", "进度", "stage"]),
        _f("amount", "预计金额", "money", True, ["金额", "预算", "预计合同额", "预算金额", "商机金额", "预估金额", "amount"]),
        _f("expected_sign_date", "预计签约日期", "date", False, ["预计签约", "签约时间", "预计成交日期", "预计日期", "成交日期"]),
        _f("owner", "负责人", "user", False, ["销售", "跟进人", "经办人", "业务员", "owner"]),
        _f("project_code", "项目编码", "project_code", False, ["项目编号", "所属项目"]),
        _f("remark", "备注", "text", False, ["说明", "remark"]),
    ]},
    "payment": {"name": "回款登记", "fields": [
        _f("contract_no", "合同编号", "contract_no", True, ["合同号", "合同编码", "编号", "合同"]),
        _f("amount", "回款金额", "money", True, ["金额", "到账金额", "收款金额", "回款额", "实收金额"]),
        _f("payment_date", "回款日期", "date", True, ["到账日期", "收款日期", "日期", "回款日", "到账日"]),
        _f("customer_name", "付款单位", "customer", False, ["客户", "客户名称", "付款方", "对方户名", "甲方"]),
        _f("remark", "备注", "text", False, ["摘要", "说明", "用途"]),
    ]},
    "project": {"name": "项目建档", "fields": [
        _f("project_code", "项目编码", "project_code", False, ["项目编号", "项目代码", "编号"]),
        _f("name", "项目名称", "text", True, ["项目", "名称", "工程名称"]),
        _f("customer_name", "客户名称", "customer", True, ["客户", "甲方", "建设单位", "业主", "采购人"]),
        _f("business_line", "业务线", "business_line", True, ["业务板块", "业务类型", "板块", "业务"]),
        _f("budget_amount", "预算金额", "money", False, ["预算", "投标额", "金额", "项目金额"]),
        _f("start_date", "开工日期", "date", False, ["开始日期", "启动日期", "开工日"]),
        _f("plan_end_date", "计划完工日期", "date", False, ["完工日期", "计划结束", "竣工日期"]),
        _f("owner", "项目经理", "user", False, ["负责人", "项目负责人", "经理"]),
        _f("remark", "备注", "text", False, ["说明"]),
    ]},
}
LINE_ALIASES = {"数字校园": "XY", "智慧校园": "XY", "校园": "XY", "教育信息化": "XY", "综合智慧安防": "AF", "智慧安防": "AF",
                "安防": "AF", "综合安防": "AF", "素质教育考评": "KP", "素质教育": "KP", "考评": "KP", "素质考评": "KP",
                "运维服务": "YW", "运维": "YW", "维保": "YW", "运营维护": "YW"}
STAGE_ALIASES = {"线索": "lead", "初步接触": "contact", "接触": "contact", "初访": "contact", "方案报价": "quote", "报价": "quote",
                 "方案": "quote", "商务谈判": "negotiation", "谈判": "negotiation", "洽谈": "negotiation", "赢单": "won",
                 "已签约": "won", "签约": "won", "成交": "won", "中标": "won", "丢单": "lost", "失败": "lost", "流失": "lost",
                 "未中标": "lost"}
TARGET_RULES = [("payment", ["回款", "到账", "收款"]), ("opportunity", ["商机", "线索", "机会"]),
                ("project", ["项目建档", "建档", "项目台账", "项目清单", "立项"]), ("contract", ["合同", "签约", "业绩"])]
SKIP_ROW = re.compile(r"^\s*(合计|总计|小计|汇总|备注|说明|注[:：]|填表|制表|审核|total)", re.I)
CN_NUM = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
PROJECT_RE = re.compile(r"^JC[-_ ]?([A-Z]{2})[-_ ]?(\d{4})[-_ ]?(\d{1,3})$")
CONTRACT_RE = re.compile(r"^HT[-_ ]?(\d{4})[-_ ]?(\d{1,4})$")


# ---------------- 模板 ----------------
def templates(db: Session) -> dict[str, dict]:
    stored = dict_service.items(db, "clean_template")
    return {k: stored.get(k) or v for k, v in DEFAULT_TEMPLATES.items()}


def detect_target(purpose: str | None, filename: str = "") -> str | None:
    text = f"{purpose or ''} {filename}"
    if purpose and purpose.strip() in TARGET_NAMES:
        return purpose.strip()
    for key, words in TARGET_RULES:
        if any(w in (purpose or "") for w in words):
            return key
    for key, words in TARGET_RULES:
        if any(w in text for w in words):
            return key
    return None


# ---------------- 通用规范化 ----------------
def nfkc(s) -> str:
    return unicodedata.normalize("NFKC", str(s)) if s is not None else ""


def norm_header(h) -> tuple[str, str | None]:
    """返回 (规范化表头, 单位)。去空白、全角转半角、去括号单位与必填星号。"""
    s = nfkc(h).strip().lower()
    unit = None
    m = re.search(r"[(\[（]\s*(万元|万|元|rmb|¥)\s*[)\]）]", s)
    if m:
        unit = "万元" if "万" in m.group(1) else "元"
    s = re.sub(r"[(\[（].*?[)\]）]", "", s)
    s = re.sub(r"[\s*＊:：_\-/]", "", s)
    return s, unit


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def display(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour or v.minute) else v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.10g}"
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else f"{v}"
    return str(v)


# ---------------- 字段解析器：返回 (值, 问题列表[(level, msg)]) ----------------
def _cn_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if not s or any(ch not in CN_NUM and ch != "十" for ch in s):
        return None
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN_NUM[a] if a else 1) * 10 + (CN_NUM[b] if b else 0)
    return int("".join(str(CN_NUM[ch]) for ch in s))


def parse_date(v, *, ref: date | None = None) -> tuple[date | None, list]:
    ref = ref or today()
    if _is_blank(v):
        return None, []
    if isinstance(v, datetime):
        return v.date(), []
    if isinstance(v, date):
        return v, []
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        n = float(v)
        if 19000101 <= n <= 21001231 and n == int(n):
            return parse_date(str(int(n)), ref=ref)
        if 20000 < n < 80000:  # Excel 序列号（1900 日期系统）
            return date(1899, 12, 30) + timedelta(days=int(n)), []
        return None, [("error", f"日期无法识别：{display(v)}")]
    s = nfkc(v).strip()
    s = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?$", "", s).replace("T00:00:00", "")
    m = re.fullmatch(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?", s)
    if not m:
        m2 = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
        m = m2
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), []
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s)
        if m:  # 美式 m/d/yyyy；首段 > 12 时按 d/m/yyyy
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return (date(y, a, b) if a <= 12 else date(y, b, a)), []
        m = re.fullmatch(r"(\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", s)
        if m:
            return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))), []
        m = re.fullmatch(r"(?:([〇零一二三四五六七八九\d]{4})年)?([一二三四五六七八九十\d]{1,3})月([一二三四五六七八九十\d]{1,3})[日号]?", s)
        if m:
            y = int("".join(str(CN_NUM.get(ch, ch)) for ch in m.group(1))) if m.group(1) else ref.year
            mo, d = _cn_int(m.group(2)), _cn_int(m.group(3))
            if mo and d:
                issues = [] if m.group(1) else [("warn", f"日期未写年份，按 {ref.year} 年处理")]
                return date(y, mo, d), issues
    except ValueError:
        return None, [("error", f"日期不存在：{s}")]
    return None, [("error", f"日期无法识别：{s}")]


def parse_money(v, unit: str | None = None) -> tuple[Decimal | None, list]:
    if _is_blank(v):
        return None, []
    mult = Decimal(10000) if unit == "万元" else Decimal(1)
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        d = Decimal(str(v)) * mult
        return d.quantize(Decimal("0.01"), ROUND_HALF_UP), []
    s = nfkc(v).strip().replace(" ", "")
    neg = False
    if re.fullmatch(r"[(（].*[)）]", s):
        neg, s = True, s[1:-1]
    s = re.sub(r"(?i)^(rmb|cny|人民币)", "", s).replace("¥", "").replace("￥", "")
    s = s.replace(",", "").replace("，", "")
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(亿元|亿|万元|万|元)?(?i:rmb|cny)?(整)?", s)
    if not m:
        return None, [("error", f"金额无法识别：{display(v)}")]
    d = Decimal(m.group(1))
    u = m.group(2) or ""
    d *= Decimal(10 ** 8) if u.startswith("亿") else Decimal(10000) if u.startswith("万") else mult
    d = d.quantize(Decimal("0.01"), ROUND_HALF_UP)
    return (-d if neg else d), []


def norm_project_code(v) -> tuple[str | None, list]:
    if _is_blank(v):
        return None, []
    s = re.sub(r"\s+", "", nfkc(v).upper()).replace("—", "-").replace("–", "-")
    m = PROJECT_RE.match(s)
    if not m:
        return s, [("error", f"项目编码格式不符（应为 JC-业务线-年份-三位流水）：{v}")]
    line, year, seq = m.groups()
    return f"JC-{line}-{year}-{int(seq):03d}", []


def norm_contract_no(v) -> tuple[str | None, list]:
    if _is_blank(v):
        return None, []
    s = re.sub(r"\s+", "", nfkc(v).upper())
    m = CONTRACT_RE.match(s)
    if not m:
        return s, [("warn", f"合同编号非标准格式（HT-年份-四位流水），按原值保留：{v}")]
    return f"HT-{m.group(1)}-{int(m.group(2)):04d}", []


def norm_line(v) -> tuple[str | None, list]:
    if _is_blank(v):
        return None, []
    s = nfkc(v).strip()
    up = s.upper()
    if up in BUSINESS_LINES:
        return up, []
    for name, code in sorted({**{n: c for c, n in BUSINESS_LINES.items()}, **LINE_ALIASES}.items(), key=lambda x: -len(x[0])):
        if name in s:
            return code, []
    return None, [("error", f"业务线无法识别：{s}")]


def norm_customer(v) -> tuple[str | None, list]:
    if _is_blank(v):
        return None, []
    s = nfkc(v)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[(\[【]\s*(盖章|公章|章|签章|印章|签字盖章|盖公章)\s*[)\]】]", "", s)
    s = re.sub(r"(盖章|公章)$", "", s).strip("：:，,。.;；")
    s = s.replace("(", "（").replace(")", "）")
    return s or None, []


def norm_stage(v) -> tuple[str | None, list]:
    if _is_blank(v):
        return None, []
    s = nfkc(v).strip()
    if s.lower() in {"lead", "contact", "quote", "negotiation", "won", "lost"}:
        return s.lower(), []
    for name, key in sorted(STAGE_ALIASES.items(), key=lambda x: -len(x[0])):
        if name in s:
            return key, []
    return None, [("error", f"商机阶段无法识别：{s}")]


def norm_text(v) -> tuple[str | None, list]:
    if _is_blank(v):
        return None, []
    return re.sub(r"\s+", " ", nfkc(v)).strip() if not isinstance(v, str) else re.sub(r"[ \t　]+", " ", v).strip(), []


# ---------------- 读取文件 ----------------
def read_table(data: bytes, filename: str, sheet_name: str | None = None, metadata: dict | None = None) -> list[tuple[int, list]]:
    """返回 [(源行号, [单元格值...])]；合并单元格以左上角值填充；选数据最多的工作表。"""
    name = filename.lower()
    if name.endswith(".csv") or not (data[:2] == b"PK" or data[:4] == b"\xd0\xcf\x11\xe0"):
        if data.lstrip()[:1] == b"<":
            raise BizError(42201, "文件为网页格式，请在 Excel 中另存为 .xlsx 后上传")
        for enc in ("utf-8-sig", "gb18030", "utf-16"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise BizError(42201, "CSV 编码无法识别，请另存为 UTF-8 或 .xlsx")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;") if text.strip() else csv.excel
        except csv.Error:
            dialect = csv.excel
        rows = []
        for i, row in enumerate(csv.reader(io.StringIO(text), dialect), start=1):
            if i > 20000 or len(row) > 200:
                raise bad_request("表格最多支持 20000 行、200 列，请拆分后上传；未截断导入")
            rows.append((i, row))
        if metadata is not None:
            metadata.update(sheets=[], selected_sheet=None, sheet_confirmed=True)
        return rows
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise BizError(42201, "暂不支持旧版 .xls（Excel 97-2003）二进制格式，请在 Excel 中另存为 .xlsx 后上传")
    from openpyxl import load_workbook
    from app.services.kb_extract import validate_archive
    validate_archive(data)
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise BizError(42201, f"Excel 文件无法解析：{exc}") from exc
    try:
        if sheet_name and sheet_name not in wb.sheetnames:
            raise bad_request("指定工作表不存在，请重新选择")
        sheets, best, best_n = [], None, -1
        for ws in wb.worksheets:
            if ws.max_row > 20000 or ws.max_column > 200 or ws.max_row * ws.max_column > 1000000:
                raise bad_request(f"工作表「{ws.title}」过大，请拆分为每表最多 20000 行、200 列、100 万单元格；未截断导入")
            n = sum(1 for r in ws.iter_rows(values_only=True) if any(not _is_blank(c) for c in r))
            sheets.append({"name": ws.title, "rows": n})
            if n > best_n:
                best, best_n = ws, n
        ws = wb[sheet_name] if sheet_name else best
        if ws is None:
            raise bad_request("文件里没有可读取的工作表")
        if metadata is not None:
            metadata.update(sheets=sheets, selected_sheet=ws.title, sheet_confirmed=bool(sheet_name) or len(sheets) == 1)
        fill: dict[tuple[int, int], object] = {}
        for rng in ws.merged_cells.ranges:
            v = ws.cell(rng.min_row, rng.min_col).value
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    fill[(r, c)] = v
        return [(r_idx, [fill.get((r_idx, c_idx), v) for c_idx, v in enumerate(row, start=1)])
                for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1)]
    finally:
        wb.close()


# ---------------- 表头识别 + 列映射 ----------------
def _synonyms(field: dict) -> list[str]:
    return [norm_header(x)[0] for x in [field["label"], field["key"], *field.get("synonyms", [])]]


def header_score(h: str, field: dict) -> tuple[float, str]:
    """表头与标准字段的匹配度：完全命中同义词 1.0 / 包含 0.8 / 模糊 ≤0.75。"""
    if not h:
        return 0.0, ""
    syns = _synonyms(field)
    if h in syns:
        return (1.0 if h == syns[0] else 0.95), "同义词"
    best = 0.0
    for s in syns:
        if len(s) >= 2 and (s in h or (len(h) >= 2 and h in s)):
            best = max(best, 0.8 * min(len(s), len(h)) / max(len(s), len(h)) + 0.15)
    if best:
        return round(min(best, 0.9), 2), "包含匹配"
    ratio = max(SequenceMatcher(None, h, s).ratio() for s in syns)
    return (round(ratio * 0.75, 2), "模糊匹配") if ratio >= 0.6 else (0.0, "")


def detect_header(rows: list[tuple[int, list]], tpl: dict) -> int:
    """在前 15 行中找与模板字段匹配最多的一行作为表头（表头不在第一行、上方有标题/说明行）。"""
    best_i, best_s = 0, -1.0
    for i, (_, vals) in enumerate(rows[:15]):
        cells = [norm_header(v)[0] for v in vals if not _is_blank(v)]
        if len(cells) < 2:
            continue
        s = sum(max((header_score(c, f)[0] for f in tpl["fields"]), default=0) for c in cells)
        s += 0.01 * len(cells)
        if s > best_s:
            best_i, best_s = i, s
    return best_i


def _infer_by_values(values: list) -> list[tuple[str, float]]:
    """值特征推断：返回 [(字段类型, 置信度)]。"""
    vals = [v for v in values if not _is_blank(v)][:30]
    if not vals:
        return []
    n = len(vals)
    s = [nfkc(v).strip().upper() for v in vals]
    out = []

    def frac(pred) -> float:
        return sum(1 for x in s if pred(x)) / n

    p = frac(lambda x: bool(re.match(r"^JC[-_ ]?[A-Z]{2}", x)))
    if p >= 0.5:
        out.append(("project_code", 0.5 + 0.3 * p))
    p = frac(lambda x: bool(re.match(r"^HT[-_ ]?\d{4}", x)))
    if p >= 0.5:
        out.append(("contract_no", 0.5 + 0.3 * p))
    p = sum(1 for v in vals if parse_date(v)[0] and not isinstance(v, (int, float)) or isinstance(v, (date, datetime))) / n
    if p >= 0.6:
        out.append(("date", 0.45 + 0.3 * p))
    p = sum(1 for v in vals if (m := parse_money(v)[0]) is not None and abs(m) >= 1000) / n
    if p >= 0.6:
        out.append(("money", 0.4 + 0.3 * p))
    p = frac(lambda x: norm_line(x)[0] is not None and len(x) <= 8)
    if p >= 0.6:
        out.append(("business_line", 0.5 + 0.3 * p))
    p = frac(lambda x: norm_stage(x)[0] is not None and len(x) <= 6)
    if p >= 0.6:
        out.append(("stage", 0.5 + 0.3 * p))
    p = frac(lambda x: bool(re.search(r"(局|学校|学院|大学|中学|小学|公司|中心|研究院|委员会|管委会|医院|政府)", x)))
    if p >= 0.6:
        out.append(("customer", 0.45 + 0.3 * p))
    return out


def build_mapping(db: Session, rows: list[tuple[int, list]], tpl: dict, header_idx: int) -> list[dict]:
    """返回列映射 [{col, header, unit, field, label, confidence, method, samples}]。"""
    header = rows[header_idx][1] if rows else []
    data_rows = [vals for _, vals in rows[header_idx + 1:header_idx + 40]]
    ncol = max([len(header)] + [len(v) for v in data_rows]) if rows else 0
    fields = tpl["fields"]
    cands: list[tuple[float, int, str, str]] = []
    cols = []
    for c in range(ncol):
        raw_h = header[c] if c < len(header) else None
        h, unit = norm_header(raw_h)
        samples = [v[c] for v in data_rows if c < len(v)]
        cols.append({"col": c, "header": display(raw_h) or f"(第{c + 1}列)", "unit": unit,
                     "samples": [display(x) for x in samples if not _is_blank(x)][:3]})
        if all(_is_blank(x) for x in samples) and _is_blank(raw_h):
            continue
        for f in fields:
            sc, how = header_score(h, f)
            if sc >= 0.45:
                cands.append((sc, c, f["key"], how))
        if max((header_score(h, f)[0] for f in fields), default=0) < 0.8:
            for typ, sc in _infer_by_values(samples):
                for f in fields:
                    if f["type"] == typ:
                        cands.append((round(sc, 2), c, f["key"], "值特征推断"))
    cands.sort(key=lambda x: -x[0])
    used_c, used_f, assign = set(), set(), {}
    for sc, c, fk, how in cands:
        if c in used_c or fk in used_f:
            continue
        used_c.add(c)
        used_f.add(fk)
        assign[c] = (fk, sc, how)
    labels = {f["key"]: f["label"] for f in fields}
    for col in cols:
        fk, sc, how = assign.get(col["col"], (None, 0.0, "未匹配"))
        col.update(field=fk, label=labels.get(fk), confidence=sc, method=how)
    return cols


# ---------------- 清洗 ----------------
class _Lookups:
    """一次清洗内复用的库内数据（用户、项目、合同、商机），用于校验与去重。"""

    def __init__(self, db: Session, target: str):
        self.users = {}
        for u in db.scalars(select(SysUser).where(SysUser.is_deleted.is_(False))).unique():
            self.users.setdefault(u.name, u)
            self.users.setdefault(u.wecom_userid.lower(), u)
        self.projects = {p.project_code: p for p in db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False)))}
        self.contracts_by_no: dict[str, BizContract] = {}
        self.contract_keys: dict[tuple, str] = {}
        self.opp_keys: dict[tuple, str] = {}
        self.payment_keys: set[tuple] = set()
        self.project_keys = {(p.name, p.customer_name): p.project_code for p in self.projects.values()}
        if target in ("contract", "payment"):
            for c in db.scalars(select(BizContract).where(BizContract.is_deleted.is_(False))):
                self.contracts_by_no[c.contract_no] = c
                self.contract_keys[(_ckey(c.customer_name), c.amount, c.sign_date)] = c.contract_no
        if target == "payment":
            for cno, amt, d in db.execute(select(BizContract.contract_no, BizPayment.amount, BizPayment.payment_date)
                                          .join(BizPayment.contract)):
                self.payment_keys.add((cno, amt, d))
        if target == "opportunity":
            for o in db.scalars(select(BizOpportunity).where(BizOpportunity.is_deleted.is_(False))):
                self.opp_keys[(o.name.strip(), _ckey(o.customer_name))] = o.opp_no


def _ckey(name: str | None) -> str:
    return norm_customer(name)[0] or "" if name else ""


def _same(a, b) -> bool:
    if a is None or b is None:
        return _is_blank(a) and _is_blank(b)
    if isinstance(b, Decimal):
        try:
            return Decimal(str(a)) == b if not isinstance(a, str) else False
        except InvalidOperation:
            return False
    if isinstance(b, date):
        if isinstance(a, str):
            return a.strip() == b.isoformat()
        return (isinstance(a, datetime) and a.date() == b and not (a.hour or a.minute)) or (
            isinstance(a, date) and not isinstance(a, datetime) and a == b)
    return display(a) == str(b)


def _parse_field(field: dict, raw, unit: str | None, lk: _Lookups) -> tuple[object, list, dict]:
    """返回 (标准值, 问题, 附加{owner_id})"""
    t = field["type"]
    extra = {}
    if t == "date":
        v, iss = parse_date(raw)
        if v and not (date(2000, 1, 1) <= v <= today() + timedelta(days=366 * 3)):
            iss.append(("error", f"{field['label']}超出合理范围：{v}"))
        return v, iss, extra
    if t == "money":
        return (*parse_money(raw, unit), extra)
    if t == "project_code":
        return (*norm_project_code(raw), extra)
    if t == "contract_no":
        return (*norm_contract_no(raw), extra)
    if t == "business_line":
        return (*norm_line(raw), extra)
    if t == "customer":
        return (*norm_customer(raw), extra)
    if t == "stage":
        return (*norm_stage(raw), extra)
    if t == "user":
        v, iss = norm_text(raw)
        if v:
            v = v.replace(" ", "")
            u = lk.users.get(v) or lk.users.get(v.lower())
            if u:
                extra["owner_id"] = u.id
                v = u.name
            else:
                iss.append(("warn", f"{field['label']}「{v}」不在通讯录中，导入后负责人为空"))
        return v, iss, extra
    return (*norm_text(raw), extra)


def _validate(target: str, v: dict, lk: _Lookups, add) -> None:
    code = v.get("project_code")
    if code and PROJECT_RE.match(code) and code not in lk.projects and target != "project":
        add("error", f"项目编码不存在：{code}")
    if target in ("contract", "opportunity", "project") and not v.get("business_line") and code in lk.projects:
        v["business_line"] = lk.projects[code].business_line
        v["_line_inferred"] = True
    amt = v.get("amount") if target != "project" else v.get("budget_amount")
    if amt is not None and amt <= 0 and (target != "project" or amt < 0):
        add("error", f"金额须大于 0：{amt}")
    if target == "payment":
        cno = v.get("contract_no")
        c = lk.contracts_by_no.get(cno) if cno else None
        if cno and not c:
            add("error", f"合同编号不存在：{cno}")
        elif c and v.get("customer_name") and _ckey(c.customer_name) != v["customer_name"]:
            add("warn", f"付款单位与合同客户不一致（合同客户：{c.customer_name}）")
    if target == "project" and code and code in lk.projects:
        pass  # 由去重处理
    if target == "project" and code and v.get("business_line") and PROJECT_RE.match(code) \
            and code.split("-")[1] != v["business_line"]:
        add("error", f"项目编码业务线 {code.split('-')[1]} 与业务线 {v['business_line']} 不一致")


def _dedupe_key(target: str, v: dict) -> list[tuple]:
    if target == "contract":
        keys = [("no", v["contract_no"])] if v.get("contract_no") else []
        return keys + [("cad", v.get("customer_name"), v.get("amount"), v.get("sign_date"))]
    if target == "opportunity":
        return [("nc", (v.get("name") or "").strip(), v.get("customer_name"))]
    if target == "payment":
        return [("pay", v.get("contract_no"), v.get("amount"), v.get("payment_date"))]
    keys = [("code", v["project_code"])] if v.get("project_code") else []
    return keys + [("nc", v.get("name"), v.get("customer_name"))]


def _db_duplicate(target: str, v: dict, lk: _Lookups) -> str | None:
    if target == "contract":
        if v.get("contract_no") and v["contract_no"] in lk.contracts_by_no:
            return f"合同编号 {v['contract_no']} 已在合同台账"
        hit = lk.contract_keys.get((v.get("customer_name"), v.get("amount"), v.get("sign_date")))
        return f"与台账合同 {hit} 客户+金额+签订日相同" if hit else None
    if target == "opportunity":
        hit = lk.opp_keys.get(((v.get("name") or "").strip(), v.get("customer_name")))
        return f"与商机 {hit} 名称+客户相同" if hit else None
    if target == "payment":
        k = (v.get("contract_no"), v.get("amount"), v.get("payment_date"))
        return "该合同同日同金额回款已登记" if k in lk.payment_keys else None
    if v.get("project_code") and v["project_code"] in lk.projects:
        return f"项目编码 {v['project_code']} 已建档"
    hit = lk.project_keys.get((v.get("name"), v.get("customer_name")))
    return f"与项目 {hit} 名称+客户相同" if hit else None


def jsonable(v):
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def clean_rows(db: Session, target: str, rows: list[tuple[int, list]], header_idx: int, mapping: list[dict],
               tpl: dict) -> tuple[list[dict], dict]:
    fields = {f["key"]: f for f in tpl["fields"]}
    colmap = {m["col"]: m for m in mapping if m.get("field") in fields}
    lk = _Lookups(db, target)
    out, skipped = [], []
    seen: dict[tuple, int] = {}
    for src_row, vals in rows[header_idx + 1:]:
        if all(_is_blank(x) for x in vals):
            skipped.append({"_row": src_row, "reason": "空行"})
            continue
        first = next((nfkc(x) for x in vals if not _is_blank(x)), "")
        nonblank = sum(1 for x in vals if not _is_blank(x))
        if SKIP_ROW.match(first) or (nonblank == 1 and len(first) > 12):
            skipped.append({"_row": src_row, "reason": f"备注/合计行：{first[:30]}"})
            continue
        v: dict = {}
        raw: dict = {}
        issues: list[str] = []
        warns: list[str] = []
        changes: list[dict] = []
        bad_fields: set[str] = set()

        def add(level, msg):
            (issues if level == "error" else warns).append(msg)

        for c, m in colmap.items():
            f = fields[m["field"]]
            cell = vals[c] if c < len(vals) else None
            raw[f["key"]] = display(cell)
            val, iss, extra = _parse_field(f, cell, m.get("unit"), lk)
            for lvl, msg in iss:
                add(lvl, msg)
                if lvl == "error":
                    bad_fields.add(f["key"])
            v[f["key"]] = val
            v.update(extra)
            if val is not None and not _same(cell, val):
                changes.append({"field": f["key"], "label": f["label"], "from": display(cell), "to": display(jsonable(val))})
            elif val is None and not _is_blank(cell) and not iss:
                changes.append({"field": f["key"], "label": f["label"], "from": display(cell), "to": None})
        _validate(target, v, lk, add)
        if v.pop("_line_inferred", False):
            changes.append({"field": "business_line", "label": fields["business_line"]["label"],
                            "from": raw.get("business_line"), "to": v["business_line"], "note": "按项目编码补全"})
        for k, f in fields.items():
            if f["required"] and v.get(k) in (None, "") and k not in bad_fields:
                add("error", f"必填字段缺失：{f['label']}")
        dup = None
        if not issues:
            keys = _dedupe_key(target, v)
            hit = next((seen[k] for k in keys if k in seen), None)
            if hit is not None:
                dup = f"与文件第 {hit} 行重复"
            else:
                dup = _db_duplicate(target, v, lk)
                for k in keys:
                    seen[k] = src_row
        status = "error" if issues else "duplicate" if dup else "fixed" if changes else "ok"
        out.append({"_row": src_row, "_status": status, "_issues": issues + ([dup] if dup else []), "_warnings": warns,
                    "_changes": changes, "_raw": raw, **{k: jsonable(x) for k, x in v.items()}})
    stats = {"total": len(out), "ok": sum(r["_status"] == "ok" for r in out),
             "fixed": sum(r["_status"] == "fixed" for r in out),
             "duplicate": sum(r["_status"] == "duplicate" for r in out),
             "error": sum(r["_status"] == "error" for r in out),
             "changed_cells": sum(len(r["_changes"]) for r in out), "header_row": rows[header_idx][0] if rows else None,
             "skipped_rows": skipped, "mapped_fields": len(colmap)}
    stats["importable"] = stats["ok"] + stats["fixed"]
    return out, stats


# ---------------- 任务：预览 / 改映射 / 导入 / 报告 ----------------
def _analyze(db: Session, data: bytes, filename: str, target: str, mapping_override: dict | None = None,
             header_row: int | None = None, sheet_name: str | None = None) -> dict:
    tpl = templates(db)[target]
    metadata = {}
    rows = read_table(data, filename, sheet_name, metadata)
    if not any(any(not _is_blank(x) for x in vals) for _, vals in rows):
        raise BizError(42201, "文件中没有数据")
    if header_row:
        idx = next((i for i, (r, _) in enumerate(rows) if r == header_row), None)
        if idx is None:
            raise bad_request(f"表头行 {header_row} 不存在")
    else:
        idx = detect_header(rows, tpl)
    mapping = build_mapping(db, rows, tpl, idx)
    if mapping_override is not None:
        valid = {f["key"]: f["label"] for f in tpl["fields"]}
        used = {}
        for m in mapping:
            key = str(m["col"])
            if key in mapping_override:
                fk = mapping_override[key] or None
                if fk and fk not in valid:
                    raise bad_request(f"未知标准字段：{fk}")
                m.update(field=fk, label=valid.get(fk), confidence=1.0 if fk else 0.0, method="人工指定" if fk else "忽略")
        for m in mapping:
            if m["field"]:
                if m["field"] in used:
                    raise bad_request(f"标准字段「{m['label']}」被多列映射（第 {used[m['field']] + 1} 列与第 {m['col'] + 1} 列）")
                used[m["field"]] = m["col"]
    out, stats = clean_rows(db, target, rows, idx, mapping, tpl)
    missing = [f["label"] for f in tpl["fields"] if f["required"] and f["key"] not in {m["field"] for m in mapping}]
    if missing and target in ("contract", "opportunity", "project") and "业务线" in missing \
            and any(m["field"] == "project_code" for m in mapping):
        missing.remove("业务线")  # 可由项目编码补全
    stats["missing_required_fields"] = missing
    stats.update(metadata)
    return {"mapping": mapping, "rows": out, "stats": stats, "template": tpl}


def job_view(db: Session, job: CleanJob, include_rows: bool = True) -> dict:
    from app.core.storage import file_brief
    from app.models import SysFile
    tpl = templates(db).get(job.target, {})
    d = {"id": job.id, "target": job.target, "target_name": TARGET_NAMES.get(job.target), "filename": job.filename,
         "status": job.status, "stats": job.stats, "mapping": job.mapping.get("columns", []),
         "purpose": job.mapping.get("purpose"), "header_row": job.stats.get("header_row"),
         "fields": [{k: f[k] for k in ("key", "label", "type", "required")} for f in tpl.get("fields", [])],
         "created_at": job.created_at, "updated_at": job.updated_at, "created_by": job.created_by,
         "file": file_brief(db.get(SysFile, job.file_id)) if job.file_id else None,
         "report_file": file_brief(db.get(SysFile, job.report_file_id)) if job.report_file_id else None}
    if include_rows:
        d["rows"] = job.rows
    return d


def preview(db: Session, user_id: int, data: bytes, filename: str, purpose: str | None = None,
            target: str | None = None) -> CleanJob:
    from app.core.storage import save_bytes
    if not data:
        raise bad_request("文件为空")
    if len(data) > 20 * 1024 * 1024:
        raise bad_request("文件超过 20MB")
    if not re.search(r"\.(xlsx|xlsm|xls|csv)$", filename.lower()):
        raise bad_request("仅支持 xlsx / xls / csv 文件")
    tgt = target if target in TARGET_NAMES else detect_target(purpose, filename)
    if not tgt:
        raise bad_request("无法识别文件用途，请选择目标台账（合同台账/商机表/回款登记/项目建档）或描述如「导入合同台账」")
    res = _analyze(db, data, filename, tgt)
    from pathlib import Path
    import uuid
    filename = Path(filename.replace("\\", "/")).name
    f = save_bytes(db, data=data, filename=filename, category="clean", key=f"clean/{user_id}/{uuid.uuid4().hex}", uploader_id=user_id)
    job = CleanJob(target=tgt, filename=filename, file_id=f.id, status="preview",
                   mapping={"columns": res["mapping"], "purpose": purpose, "detected_target": tgt},
                   rows=res["rows"], stats=res["stats"], created_by=user_id)
    db.add(job)
    db.flush()
    return job


def remap(db: Session, job: CleanJob, mapping: dict[str, str | None] | None, target: str | None = None,
          header_row: int | None = None, sheet_name: str | None = None) -> CleanJob:
    from app.core.storage import read_bytes
    from app.models import SysFile
    if job.status != "preview":
        raise BizError(40901, "任务已导入或已取消，不能修改映射")
    if target and target not in TARGET_NAMES:
        raise bad_request("未知目标台账")
    tgt = target or job.target
    data = read_bytes(db.get(SysFile, job.file_id))
    res = _analyze(db, data, job.filename, tgt, mapping_override=mapping if tgt == job.target else None,
                   header_row=header_row, sheet_name=sheet_name or (
                       job.stats.get("selected_sheet") if job.stats.get("sheet_confirmed") else None))
    job.target, job.rows, job.stats = tgt, res["rows"], res["stats"]
    if job.mapping.get("blocked_source_columns"):
        job.stats = {**job.stats, "blocked_source_columns": job.mapping["blocked_source_columns"]}
    job.mapping = {**job.mapping, "columns": res["mapping"]}
    db.flush()
    return job


def _dec(v) -> Decimal | None:
    return Decimal(v) if v not in (None, "") else None


def _dt(v) -> date | None:
    return date.fromisoformat(v[:10]) if v else None


def _next_contract_no(db: Session, year: int, reserved: set[str]) -> str:
    prefix = f"HT-{year}-"
    last = db.scalar(select(func.max(BizContract.contract_no)).where(BizContract.contract_no.like(f"{prefix}%")))
    seq = int(last[len(prefix):]) if last and last[len(prefix):].isdigit() else 0
    while True:
        seq += 1
        no = f"{prefix}{seq:04d}"
        if no not in reserved:
            reserved.add(no)
            return no


def _insert(db: Session, job: CleanJob, r: dict, user_id: int, reserved: set[str]) -> str:
    from app.services import codes, payment_service
    t = job.target
    note = f"Excel 清洗导入（任务 #{job.id}，{job.filename} 第 {r['_row']} 行）"
    if t == "contract":
        proj = db.scalar(select(BizProject).where(BizProject.project_code == r.get("project_code"))) if r.get("project_code") else None
        sd = _dt(r["sign_date"])
        c = BizContract(contract_no=r.get("contract_no") or _next_contract_no(db, sd.year, reserved),
                        name=r.get("name") or (f"{proj.name}合同" if proj else f"{r['customer_name']}合同"),
                        project_code=r.get("project_code"), customer_name=r["customer_name"], amount=_dec(r["amount"]),
                        sign_date=sd, business_line=r["business_line"],
                        owner_id=r.get("owner_id") or (proj.market_owner_id if proj else None), source_type=3, status=1,
                        remark="；".join(x for x in [r.get("remark"), note] if x))
        db.add(c)
        if proj and proj.contract_amount is None:
            proj.contract_amount = c.amount
        if proj and proj.status == 1:
            proj.status = 2
        db.flush()
        return c.contract_no
    if t == "opportunity":
        o = BizOpportunity(opp_no=codes.opp_no(db), name=r["name"], customer_name=r["customer_name"],
                           business_line=r["business_line"], stage=r.get("stage") or "lead", amount=_dec(r["amount"]),
                           expected_sign_date=_dt(r.get("expected_sign_date")), owner_id=r.get("owner_id") or user_id,
                           source_type=3, status={"won": "won", "lost": "lost"}.get(r.get("stage") or "lead", "open"),
                           project_code=r.get("project_code"), remark="；".join(x for x in [r.get("remark"), note] if x))
        db.add(o)
        db.flush()
        from app.core.audit import log_op
        log_op(db, user_id, "opportunity.stage", "opportunity", o.id, after={"stage": o.stage, "note": "Excel 清洗导入"})
        return o.opp_no
    if t == "payment":
        c = db.scalar(select(BizContract).where(BizContract.contract_no == r["contract_no"]))
        pay = payment_service.register_payment(db, c, _dec(r["amount"]), _dt(r["payment_date"]), source_type=2,
                                               user_id=user_id, remark=(r.get("remark") or note)[:255])
        return f"{c.contract_no}#{pay.id}"
    code = r.get("project_code") or codes.project_code(db, r["business_line"])
    p = BizProject(project_code=code, name=r["name"], business_line=r["business_line"], customer_name=r["customer_name"],
                   owner_id=r.get("owner_id"), status=1, budget_amount=_dec(r.get("budget_amount")),
                   start_date=_dt(r.get("start_date")), plan_end_date=_dt(r.get("plan_end_date")),
                   remark="；".join(x for x in [r.get("remark"), note] if x))
    db.add(p)
    db.flush()
    return code


def import_job(db: Session, job: CleanJob, user_id: int) -> CleanJob:
    if job.status != "preview":
        raise BizError(40901, "任务已导入或已取消")
    blocked = set(job.mapping.get("blocked_source_columns", []))
    if any(m.get("field") and m["col"] in blocked for m in job.mapping.get("columns", [])):
        raise bad_request("源表附件、关联或公式等复杂列不能直接作为台账字段，请在字段映射中忽略这些列；原始类型已保留在来源快照")
    if job.stats.get("missing_required_fields"):
        raise bad_request("必填字段未映射：" + "、".join(job.stats["missing_required_fields"]))
    if len(job.stats.get("sheets", [])) > 1 and not job.stats.get("sheet_confirmed"):
        raise bad_request("文件包含多个工作表，请先明确选择并核对要导入的工作表；其他工作表不会自动导入")
    if not job.stats.get("importable"):
        raise bad_request("没有可导入的有效记录，请先修正错误或重复数据；尚未写入台账")
    # 不同经理可以同时预览同一批业务，提交时必须重新查重；只给任务加锁不够。
    if db.bind.dialect.name == "postgresql":
        import zlib
        db.execute(sql_text("select pg_advisory_xact_lock(:key)"), {"key": zlib.crc32(f"clean:{job.target}".encode())})
    current = _Lookups(db, job.target)
    fields = templates(db)[job.target]["fields"]
    for row in job.rows:
        if row["_status"] not in ("ok", "fixed"):
            continue
        values = dict(row)
        for field in fields:
            key = field["key"]
            if values.get(key) is not None:
                if field["type"] == "money":
                    values[key] = _dec(values[key])
                elif field["type"] == "date":
                    values[key] = _dt(values[key])
        duplicate = _db_duplicate(job.target, values, current)
        if duplicate:
            raise BizError(40901, f"第 {row['_row']} 行在预览后已有对应记录：{duplicate}。请重新应用映射刷新校验，尚未写入本批数据")
    reserved = {r["contract_no"] for r in job.rows if r.get("contract_no")}
    imported = failed = 0
    rows = []
    for r in job.rows:
        r = dict(r)
        if r["_status"] in ("ok", "fixed"):
            try:
                with db.begin_nested():
                    r["_ref"] = _insert(db, job, r, user_id, reserved)
                r["_imported"] = True
                imported += 1
            except Exception as exc:  # noqa: BLE001
                r["_imported"] = False
                r["_import_error"] = str(getattr(exc, "message", exc))[:200]
                failed += 1
        else:
            r["_imported"] = False
        rows.append(r)
    job.rows = rows
    job.stats = {**job.stats, "imported": imported, "import_failed": failed,
                 "skipped": sum(1 for r in rows if not r["_imported"])}
    job.status = "imported"
    db.flush()
    build_report(db, job, user_id)
    return job


def guess_target(db: Session, data: bytes, filename: str) -> tuple[str | None, dict]:
    """用途描述/文件名认不出时，按表头与各模板的匹配度猜目标台账。返回 (target, 各模板得分)。

    只有一个模板能覆盖全部必填字段、且明显领先第二名时才给出结论，否则返回 None 交给人工判断。
    """
    try:
        rows = read_table(data, filename)
    except BizError:
        return None, {}
    scores = {}
    for key, tpl in templates(db).items():
        idx = detect_header(rows, tpl)
        heads = [norm_header(v)[0] for v in (rows[idx][1] if rows else []) if not _is_blank(v)]
        hit_fields = {f["key"] for f in tpl["fields"]
                      if any(header_score(h, f)[0] >= 0.8 for h in heads)}
        required = {f["key"] for f in tpl["fields"] if f["required"]}
        scores[key] = {"matched": len(hit_fields), "required_hit": len(hit_fields & required),
                       "required": len(required)}
    ok_targets = sorted((v["matched"], k) for k, v in scores.items() if v["required_hit"] == v["required"])
    if not ok_targets:
        return None, scores
    best_n, best = ok_targets[-1]
    if len(ok_targets) > 1 and ok_targets[-2][0] >= best_n - 1:
        return None, scores
    return best, scores


def rollback_job(db: Session, job: CleanJob) -> dict:
    """撤回一次已导入的清洗任务：删除本次写入台账的记录。全有或全无——任一条已被后续业务引用就整体拒绝并列出原因。"""
    from app.models import BizProjectCost, BizProjectMilestone, FinCashJournal
    from app.services import payment_service
    if job.status != "imported":
        raise BizError(40901, "只有已导入的任务可以撤回")
    refs = [r["_ref"] for r in job.rows if r.get("_imported") and r.get("_ref")]
    blockers, plan = [], []
    for ref in refs:
        if job.target == "contract":
            c = db.scalar(select(BizContract).where(BizContract.contract_no == ref))
            if c is None:
                continue
            if c.payments:
                blockers.append(f"合同 {ref} 已有 {len(c.payments)} 笔回款")
            elif c.archive_file_id:
                blockers.append(f"合同 {ref} 已归档原件")
            else:
                plan.append(c)
        elif job.target == "opportunity":
            o = db.scalar(select(BizOpportunity).where(BizOpportunity.opp_no == ref))
            if o is not None:
                plan.append(o)
        elif job.target == "payment":
            no, _, pid = ref.partition("#")
            pay = db.get(BizPayment, int(pid)) if pid.isdigit() else None
            if pay is None:
                continue
            if pay.bank_flow_id:
                blockers.append(f"回款 {ref} 已关联银行流水")
            else:
                plan.append(pay)
        else:
            pj = db.scalar(select(BizProject).where(BizProject.project_code == ref))
            if pj is None:
                continue
            used = []
            if db.scalar(select(func.count(BizContract.id)).where(BizContract.project_code == ref, BizContract.is_deleted.is_(False))):
                used.append("合同")
            if db.scalar(select(func.count(BizProjectCost.id)).where(BizProjectCost.project_code == ref)):
                used.append("成本")
            if db.scalar(select(func.count(FinCashJournal.id)).where(FinCashJournal.project_code == ref)):
                used.append("日记账")
            if db.scalar(select(func.count(BizProjectMilestone.id)).where(BizProjectMilestone.project_id == pj.id)):
                used.append("流程节点")
            if used:
                blockers.append(f"项目 {ref} 已有{'、'.join(used)}")
            else:
                plan.append(pj)
    if blockers:
        raise BizError(40901, "以下记录已被后续业务使用，不能整批撤回：" + "；".join(blockers[:10])
                       + (f" 等 {len(blockers)} 条" if len(blockers) > 10 else ""), data={"blockers": blockers})
    touched = set()
    for obj in plan:
        if isinstance(obj, BizPayment):
            touched.add(obj.contract_id)
        db.delete(obj)
    db.flush()
    for cid in touched:  # 回款删除后按剩余回款重新冲抵计划
        c = db.get(BizContract, cid)
        db.refresh(c)
        for pl in c.plans:
            pl.received_amount = Decimal("0")
        for pay in c.payments:
            pay.plan_id = payment_service.apply_to_plans(c, pay.amount)
        payment_service.refresh_plan_status(c)
    job.status = "rolled_back"
    job.stats = {**job.stats, "rolled_back": len(plan)}
    db.flush()
    return {"removed": len(plan), "target": job.target}


def build_report(db: Session, job: CleanJob, user_id: int | None = None):
    """清洗报告 xlsx：概览 / 清洗明细 / 原值→新值对照 / 跳过与异常行 / 口径说明。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    from app.core.storage import save_bytes
    head = PatternFill("solid", fgColor="1B3275")
    red = PatternFill("solid", fgColor="FCE8E4")
    amber = PatternFill("solid", fgColor="FBF1DC")
    tpl = templates(db)[job.target]
    fields = tpl["fields"]
    st = job.stats
    wb = Workbook()

    def sheet(title, headers, first=False):
        ws = wb.active if first else wb.create_sheet(title)
        ws.title = title
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), head, Alignment(vertical="center")
        ws.freeze_panes = "A2"
        return ws

    ws = sheet("概览", ["项目", "值"], first=True)
    for k, v in [("源文件", job.filename), ("目标台账", TARGET_NAMES[job.target]), ("用途描述", job.mapping.get("purpose")),
                 ("本次工作表", st.get("selected_sheet") or "CSV"),
                 ("表头所在行", st.get("header_row")), ("数据行数", st.get("total")), ("通过（无需修改）", st.get("ok")),
                 ("已修正", st.get("fixed")), ("重复（跳过）", st.get("duplicate")), ("错误（跳过）", st.get("error")),
                 ("改动单元格数", st.get("changed_cells")), ("已导入台账", st.get("imported", 0)),
                 ("导入失败", st.get("import_failed", 0)), ("跳过的空行/备注行", len(st.get("skipped_rows", []))),
                 ("任务状态", {"preview": "预览", "imported": "已导入", "cancelled": "已取消", "rolled_back": "已撤回"}.get(job.status, job.status))]:
        ws.append([k, v])
    ws.append([])
    ws.append(["列映射", "源列 → 标准字段（置信度 / 方式）"])
    for m in job.mapping.get("columns", []):
        ws.append([m["header"], f"{m.get('label') or '（忽略）'}（{m.get('confidence', 0):.2f} / {m.get('method')}）"])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 22, 60

    labels = {"ok": "通过", "fixed": "已修正", "duplicate": "重复", "error": "错误"}
    ws = sheet("清洗明细", ["源行号", "状态", "是否导入", "台账编号"] + [f["label"] for f in fields] + ["问题", "提示"])
    for r in job.rows:
        ws.append([r["_row"], labels[r["_status"]], "是" if r.get("_imported") else "否", r.get("_ref")] +
                  [r.get(f["key"]) for f in fields] + ["；".join(r["_issues"] + ([r["_import_error"]] if r.get("_import_error") else [])),
                                                       "；".join(r.get("_warnings", []))])
        if r["_status"] in ("error", "duplicate"):
            for c in ws[ws.max_row]:
                c.fill = red if r["_status"] == "error" else amber
    for i, w in enumerate([8, 8, 8, 16] + [16] * len(fields) + [40, 30], start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w

    ws = sheet("原值新值对照", ["源行号", "字段", "原值", "新值", "说明"])
    for r in job.rows:
        for ch in r["_changes"]:
            ws.append([r["_row"], ch["label"], ch["from"], ch["to"], ch.get("note")])
    for col, w in zip("ABCDE", [8, 14, 30, 30, 20]):
        ws.column_dimensions[col].width = w

    ws = sheet("跳过与异常行", ["源行号", "类型", "原因", "原始数据"])
    for r in job.rows:
        if not r.get("_imported"):
            kind = labels[r["_status"]] if r["_status"] in ("error", "duplicate") else "导入失败" if r.get("_import_error") else "未导入"
            ws.append([r["_row"], kind, "；".join(r["_issues"]) or r.get("_import_error"),
                       "，".join(f"{k}={v}" for k, v in r["_raw"].items() if v)])
    for s in st.get("skipped_rows", []):
        ws.append([s["_row"], "空行/备注行", s["reason"], None])
    for col, w in zip("ABCD", [8, 12, 50, 80]):
        ws.column_dimensions[col].width = w

    ws = sheet("口径说明", ["规则", "说明"])
    for k, v in [("表头识别", "前 15 行中与清洗模板（sys_dict clean_template）字段匹配度最高的一行为表头；上方标题/说明行忽略"),
                 ("列映射", "同义词完全匹配 → 包含匹配 → 模糊匹配 → 值特征推断；用户可在预览中改映射"),
                 ("日期", "支持 2026-10-01 / 2026/10/1 / 2026.10.01 / 20261001 / 2026年10月1日 / 10/1/2026 / Excel 序列号 / 十月一日"),
                 ("金额", "去除 ¥ ￥ RMB 元 千分位，全角转半角，括号表示负数，「12.5万」及表头（万元）自动 ×10000"),
                 ("编码", "项目编码统一为 JC-业务线-年份-三位流水；合同编号 HT-年份-四位；业务线中文名转编码"),
                 ("客户名", "去空格、全角半角统一、去「（盖章）」等，括号统一为中文括号"),
                 ("去重", "合同：合同编号 或 客户+金额+签订日；商机：名称+客户；回款：合同+金额+日期；项目：编码 或 名称+客户；"
                         "文件内与库内均检查"),
                 ("校验", "必填缺失、金额非正、日期超范围、项目编码不存在、业务线无法识别 → 错误行，不导入"),
                 ("落库", "合同 source_type=3（Excel 清洗导入）；回款经 payment_service.register_payment(source_type=2) 冲抵回款计划")]:
        ws.append([k, v])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 12, 100

    buf = io.BytesIO()
    wb.save(buf)
    base = re.sub(r"\.(xlsx|xlsm|xls|csv)$", "", job.filename, flags=re.I)
    f = save_bytes(db, data=buf.getvalue(), filename=f"清洗报告_{base}_{job.id}.xlsx", category="clean_report",
                   key=f"report/clean/{today():%Y-%m}/清洗报告_{job.id}.xlsx", uploader_id=user_id)
    job.report_file_id = f.id
    db.flush()
    return f
