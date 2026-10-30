"""智能识别：上传文档 → 标题/摘要/标签/分类；认不出的表格 → 判断目标台账或说明是什么表。

- 先用大模型（DeepSeek 等，JSON 模式，12 秒超时）；未配置、超时、返回不合法时退回规则（kb_service.suggest_category）。
- 只把正文前 3000 字、表头和前 5 行样例发给模型，不发整份文件；模型输出逐字段校验，分类必须在现有分类树里。
- 返回值都带 source=llm|rule，界面据此提示「由 AI 建议」，最终以上传人确认为准。
"""
import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.integration import llm
from app.services import kb_service

DOC_SYSTEM = """你是聚成数智（智慧教育信息化公司，业务线：数字校园、综合智慧安防、素质教育考评、运维服务）的知识库管理员。
根据文件名和正文开头，为这份文档整理入库信息。只输出 JSON：
{"title": "规范的文档标题，不带扩展名和日期编号，≤40字", "summary": "一两句话说明讲什么、适用什么场景，≤120字",
 "tags": ["2-5个短标签，如业务线、年份、客户、文档类型"], "category": "一级分类名", "subcategory": "二级分类名或空字符串",
 "reason": "一句话说明分类依据"}
category 和 subcategory 必须从给定分类树里选；拿不准时 category 填空字符串。不要编造正文里没有的事实。"""

TABLE_SYSTEM = """你帮同事判断一份 Excel 是什么数据、应该导入哪个业务台账。可选台账：
contract 合同台账（客户、合同金额、签订日期、业务线）；opportunity 商机信息表（商机名称、客户、预计金额、阶段）；
payment 回款登记（合同编号、回款金额、回款日期）；project 项目建档（项目名称、客户、业务线、预算）。
只输出 JSON：{"target": "contract|opportunity|payment|project|none", "description": "这是什么表，≤40字", "reason": "判断依据，≤60字"}
表格不属于这四类时 target 填 none。"""

TABLE_PLAN_SYSTEM = """你是企业 Excel 导入助手。同事要把一份表格导入指定台账，但表头可能有多行、合并分组行、列名不统一。
请根据表头和前几行样例，判断这张表是否真的属于该台账，并判断真正的表头行、做列映射。只输出 JSON：
{"fits": true, "header_row": 2, "mapping": {"1": "name", "2": "customer_name", "4": "amount"},
 "customer_from_name": false, "business_line": "", "reason": "一句话"}
规则：
- fits：这张表的数据是否属于目标台账。进度汇报、会计凭证/流水、分析汇总、名单通讯录等不是台账数据时填 false，不要硬套字段。
- header_row 用 Excel 行号（从 1 开始）；两行表头取最下面一行；跳过「已签约/已回款/合计」这类整行合并的分组行。
- mapping 的键是列序号（从 0 开始），值是标准字段 key；一列只能对应一个字段，一个字段只取一列；没有合适列就不写，不要硬塞。
- customer_from_name：客户名称需要从合同/项目名称中提取时填 true。
- business_line：表里没有业务线列时，根据名称和样例判断整份文件的主业务线，只能填 XY / AF / KP / YW，拿不准留空字符串。
- 不要编造表里不存在的列。"""


def plan_table(target_name: str, filename: str, purpose: str | None, rows: list[tuple[int, list]],
               tpl: dict) -> dict | None:
    """让模型判断表头行和列映射。返回 {header_row, mapping{int: key}, customer_from_name, business_line, reason}。"""
    if not llm.enabled() or not rows:
        return None
    fields = "\n".join(f"- {f['key']}（{f['label']}，{'必填' if f['required'] else '可选'}，类型 {f['type']}）"
                       for f in tpl["fields"])
    lines = []
    for row_no, vals in rows[:12]:
        cells = [re.sub(r"\s+", " ", str(v)).strip()[:60] if v is not None else "" for v in vals]
        lines.append(f"{row_no}: " + " | ".join(cells))
    prompt = (f"目标台账：{target_name}\n文件名：{filename}\n上传人说明：{purpose or '无'}\n"
              f"标准字段：\n{fields}\n\n表格前 {len(lines)} 行（行号: 单元格 | 单元格...）：\n" + "\n".join(lines))
    out = _parse_json(llm.complete(TABLE_PLAN_SYSTEM, prompt, temperature=0.1, max_tokens=900,
                                   timeout_seconds=20, json_mode=True))
    if not out:
        return None
    valid = {f["key"] for f in tpl["fields"]}
    row_numbers = {r for r, _ in rows}
    try:
        header_row = int(out.get("header_row") or 0)
    except (TypeError, ValueError):
        return None
    if header_row not in row_numbers:
        return None
    ncol = max((len(v) for _, v in rows), default=0)
    mapping, used = {}, set()
    raw_map = out.get("mapping") if isinstance(out.get("mapping"), dict) else {}
    for key, field in raw_map.items():
        try:
            col = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= col < ncol and field in valid and field not in used:
            mapping[col] = field
            used.add(field)
    if not mapping:
        return None
    line = str(out.get("business_line") or "").strip().upper()
    def as_bool(v, default=False):
        if isinstance(v, bool):
            return v
        if v is None:
            return default
        return str(v).strip().lower() not in ("false", "0", "no", "否", "不")
    return {"fits": as_bool(out.get("fits"), True), "header_row": header_row, "mapping": mapping,
            "customer_from_name": as_bool(out.get("customer_from_name")),
            "business_line": line if line in ("XY", "AF", "KP", "YW") else None,
            "reason": str(out.get("reason") or "").strip()[:120]}



def _tree_text(tree: list[dict]) -> str:
    return "\n".join(f"- {c['name']}：{'、'.join(x['name'] for x in c['children']) or '（无二级）'}" for c in tree if c.get("id"))


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        v = json.loads(m.group(0) if m else text)
    except (ValueError, AttributeError):
        return None
    return v if isinstance(v, dict) else None


def clean_title(filename: str) -> str:
    """文件名 → 默认标题：去扩展名、去「副本/最终版/(1)」等噪声。"""
    t = Path(filename).stem
    t = re.sub(r"[（(]\s*\d+\s*[)）]$", "", t)
    t = re.sub(r"[-_\s]*(副本|copy|最终版|终版|定稿|修订版|v\d+(\.\d+)*)$", "", t, flags=re.I)
    return t.strip(" -_") or Path(filename).stem


def describe_document(db: Session, filename: str, text: str, purpose: str | None = None) -> dict:
    """返回 {title, summary, tags, category, subcategory, reason, source}。"""
    title = clean_title(filename)
    rule = kb_service.suggest_category(title, f"{purpose or ''}\n{text}")
    paragraphs = [line.strip().lstrip("# ") for line in text.splitlines() if line.strip()]
    base = {"title": title, "summary": " ".join(paragraphs)[:180], "tags": [], "source": "rule", **rule}
    if not llm.enabled():
        return base
    tree = kb_service.category_tree(db)
    prompt = (f"文件名：{filename}\n上传人说明：{purpose or '无'}\n分类树：\n{_tree_text(tree)}\n\n"
              f"正文摘录（开头及中后部，不代表全文）：\n{text[:1800]}\n{text[len(text)//2:len(text)//2+600] if len(text)>3000 else ''}\n{text[-600:] if len(text)>3000 else text[1800:3000]}")
    out = _parse_json(llm.complete(DOC_SYSTEM, prompt, temperature=0.2, max_tokens=400, timeout_seconds=12,
                                   json_mode=True))
    if not out:
        return base
    tops = {c["name"]: {x["name"] for x in c["children"]} for c in tree if c.get("id")}
    cat = str(out.get("category") or "").strip()
    sub = str(out.get("subcategory") or "").strip()
    if cat not in tops:
        cat, sub = rule["category"], rule["subcategory"]
        reason = rule["reason"]
    else:
        sub = sub if sub in tops[cat] else None
        reason = str(out.get("reason") or "").strip()[:80] or "AI 根据正文判断"
    tags = [str(t).strip()[:20] for t in (out.get("tags") or []) if str(t).strip()][:5] if isinstance(out.get("tags"), list) else []
    return {"title": (str(out.get("title") or "").strip()[:60] or title), "summary": str(out.get("summary") or "").strip()[:200],
            "tags": tags, "category": cat or None, "subcategory": sub or None, "reason": reason, "source": "llm"}


def describe_table(header: list, samples: list[list], filename: str, purpose: str | None = None) -> dict | None:
    """规则认不出目标台账时调用。返回 {target|None, description, reason, source='llm'}；模型不可用返回 None。"""
    if not llm.enabled():
        return None
    def cell(v):
        return "" if v is None else str(v)[:30]
    lines = [" | ".join(cell(v) for v in header)] + [" | ".join(cell(v) for v in r) for r in samples[:5]]
    prompt = f"文件名：{filename}\n上传人说明：{purpose or '无'}\n表头与前几行：\n" + "\n".join(lines)
    out = _parse_json(llm.complete(TABLE_SYSTEM, prompt, temperature=0.1, max_tokens=200, timeout_seconds=12, json_mode=True))
    if not out:
        return None
    target = out.get("target")
    return {"target": target if target in ("contract", "opportunity", "payment", "project") else None,
            "description": str(out.get("description") or "").strip()[:60],
            "reason": str(out.get("reason") or "").strip()[:80], "source": "llm"}
