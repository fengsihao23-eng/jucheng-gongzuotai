"""Natural-language performance report builder and collection follow-up stage summary."""
from __future__ import annotations

from decimal import Decimal
import re

from sqlalchemy import func, select

from app.core.audit import log_op
from app.core.response import bad_request, forbidden, not_found
from app.core.timeutil import TZ, month_of, month_range, today, week_range
from app.integration import llm
from app.models import SysUser, WorkbookCollectionReport
from app.services import (
    business_workbook as source,
    collection_amounts,
    collection_followups as followups,
    collection_plans,
    performance_details,
    workbook_metrics as metrics,
)

DEFAULT_YEAR = "2026"
STOP_WORDS = (
    "请", "帮我", "帮忙", "一下", "生成", "输出", "制作", "导出", "统计", "汇总", "计算", "分析",
    "查询", "查看", "查找", "列出", "展示", "显示", "看看", "所有", "全部", "当前", "目前", "今年",
    "当年", "本年", "各个", "各", "每个", "每", "按照", "按", "分", "根据", "有关", "相关", "关于",
    "的", "了", "吗", "呢", "及", "和", "与", "以及", "情况", "报表", "报告", "数据", "结果",
    "清单", "明细", "列表", "表格", "台账", "合同", "项目", "签约", "业绩", "总金额", "总额", "金额",
    "未收款", "未收", "待收款", "待收", "未回款", "待回款", "欠款", "已收款", "已收", "已回款", "回款",
    "结清", "排行", "排名", "前十", "前五", "对比", "分布", "占比",
)


def _dec(val) -> Decimal:
    if val is None or val == "":
        return Decimal(0)
    return Decimal(str(val))


def _money_str(val: Decimal | str | None) -> str:
    if val is None or val == "":
        return "0.00"
    d = val if isinstance(val, Decimal) else Decimal(str(val))
    return f"{d:,.2f}"


def _plain_dec(val: Decimal | None) -> str | None:
    if val is None:
        return None
    return format(val.quantize(Decimal("0.01")) if val == val.quantize(Decimal("0.01")) else val, "f")


def _pct(num: Decimal, den: Decimal) -> str:
    if den <= 0:
        return "0.0%"
    return f"{(num * Decimal(100) / den):.1f}%"


def _extract_top_n(q: str) -> int | None:
    cn_nums = {"三": 3, "五": 5, "八": 8, "十": 10, "二十": 20}
    if m := re.search(r"(?:前|top\s*)(\d{1,3})", q, re.IGNORECASE):
        n = int(m.group(1))
        return max(1, min(100, n))
    for k, v in cn_nums.items():
        if f"前{k}" in q:
            return v
    return None


def _extract_threshold(q: str):
    """Parse expressions like '大于50万', '超过100万元', '低于10万', '100万以上'."""
    patterns = [
        (r"(?:大于|超过|高于|不少于|不低于|>=|＞)\s*(\d+(?:\.\d+)?)\s*(万|万元|元)?", "gt"),
        (r"(\d+(?:\.\d+)?)\s*(万|万元|元)\s*(?:以上|及以上)", "gt"),
        (r"(?:小于|低于|少于|不足|<=|＜)\s*(\d+(?:\.\d+)?)\s*(万|万元|元)?", "lt"),
        (r"(\d+(?:\.\d+)?)\s*(万|万元|元)\s*(?:以下|以内)", "lt"),
    ]
    for pat, op in patterns:
        if m := re.search(pat, q):
            num = Decimal(m.group(1))
            unit = m.group(2) or ""
            if "万" in unit:
                num *= Decimal(10000)
            return op, num
    return None, None


def query_performance_report(db, p, question: str, *, year: str = "", start: str = "", end: str = ""):
    """Answer natural-language report queries over the fixed performance workbook."""
    q_raw = (question or "").strip()
    if not q_raw:
        raise bad_request("请输入报表提问内容，例如：统计各区域合同金额、查询未收款合同清单")
    wb = source.active_source(db, p, "contracts")
    if wb is None:
        raise not_found("固定来源")

    sheets = source.module_sheets(wb, "contracts")
    if not sheets:
        raise bad_request("经营业绩来源子表尚未同步")
    eligible = source.performance_rows(wb)
    q = q_raw.strip()
    q_norm = source.layout.norm(q)
    ref_today = today()

    # 1. Determine time scope
    scope_year = year or DEFAULT_YEAR
    month_filter = ""
    range_start, range_end = start, end
    period_desc = ""

    if any(w in q for w in ("未注明日期", "无日期", "缺日期", "未填日期")):
        scope_year = "undated"
        range_start = range_end = ""
        period_desc = "未注明签订日期"
    elif any(w in q for w in ("全部年份", "所有年份", "历年", "各年", "按年", "年度对比")):
        scope_year = "all"
        range_start = range_end = ""
        period_desc = "全部年份（2026年及以前年度未回款）"
    elif m_ym := re.search(r"(20\d{2})\s*[-年/.]\s*(1[0-2]|0?[1-9])\s*月?", q):
        scope_year = m_ym.group(1)
        month_filter = f"{scope_year}-{int(m_ym.group(2)):02d}"
        range_start = range_end = ""
        period_desc = f"{scope_year}年{int(m_ym.group(2))}月"
    elif m_y := re.search(r"(20\d{2})\s*年", q):
        scope_year = m_y.group(1)
        range_start = range_end = ""
        period_desc = f"{scope_year}年全年"
    elif any(w in q for w in ("本月", "当月", "这个月")):
        ms, me = month_range(ref=ref_today)
        scope_year = str(ref_today.year)
        range_start, range_end = str(ms), str(me)
        period_desc = f"当月（{range_start} 至 {range_end}）"
    elif any(w in q for w in ("本周", "当周", "这周")):
        ws, we = week_range(ref=ref_today)
        scope_year = str(ref_today.year) if ws.year == we.year else "all"
        range_start, range_end = str(ws), str(we)
        period_desc = f"本周（{range_start} 至 {range_end}）"
    elif m_m := re.search(r"(?<!\d)(1[0-2]|0?[1-9])\s*月份?(?!\d)", q):
        if not any(w in q for w in ("各月", "每月", "按月", "月度")):
            yr = scope_year if re.fullmatch(r"20\d{2}", scope_year) else DEFAULT_YEAR
            scope_year = yr
            month_filter = f"{yr}-{int(m_m.group(1)):02d}"
            range_start = range_end = ""
            period_desc = f"{yr}年{int(m_m.group(1))}月"

    if scope_year == "undated":
        rows = [r for r in eligible if not r["date_value"]]
    elif scope_year == "all":
        rows = list(eligible)
    else:
        yr = scope_year if re.fullmatch(r"20\d{2}", str(scope_year)) else DEFAULT_YEAR
        profile = "sales" if yr == DEFAULT_YEAR else "older"
        rows = [r for r in eligible if r["date_value"] and r["date_value"].startswith(yr + "-") and r["profile"] == profile]
        if not period_desc:
            period_desc = f"{yr}年全年"

    if month_filter:
        rows = [r for r in rows if (r.get("date_value") or "").startswith(month_filter + "-")]
    if range_start or range_end:
        rows = [r for r in rows if r.get("date_value")
                and (not range_start or r["date_value"] >= range_start)
                and (not range_end or r["date_value"] <= range_end)]
        if not period_desc:
            period_desc = f"{range_start or '…'} 至 {range_end or '…'}"

    # 2. Detect grouping dimension vs item filtering
    ask_group_region = any(w in q for w in ("各区域", "按区域", "分区域", "各地区", "按地区", "分地区", "区域统计", "区域合同", "区域汇总", "区域业绩", "区域占比", "区域排行"))
    ask_group_owner = any(w in q for w in ("各负责人", "按负责人", "分负责人", "各销售", "按销售", "业务员", "跟进人统计", "负责人统计", "负责人业绩", "销售排行", "人员业绩", "个人业绩", "谁签"))
    ask_group_month = any(w in q for w in ("各月", "每月", "按月", "分月", "月度", "月份统计", "月份汇总", "月趋势"))
    ask_group_year = any(w in q for w in ("各年", "历年", "按年", "分年", "年度统计", "年度汇总", "年度对比"))
    ask_group_customer = any(w in q for w in ("各客户", "按客户", "分客户", "客户统计", "客户汇总", "客户排行", "大客户", "客户前"))
    if not ask_group_customer and "客户" in q and _extract_top_n(q):
        ask_group_customer = True

    is_grouped = ask_group_region or ask_group_owner or ask_group_month or ask_group_year or ask_group_customer
    applied_filters = [f"范围：{period_desc}"]

    # Entity value filters (when not grouping by that exact dimension, or even narrowing within another group)
    known_regions = sorted({r["region"] for r in rows if r.get("region")}, key=len, reverse=True)
    known_owners = sorted({r["owner"] for r in rows if r.get("owner")}, key=len, reverse=True)

    if not ask_group_region:
        matched_region = next((reg for reg in known_regions if source.layout.norm(reg) in q_norm), None)
        if matched_region:
            rows = [r for r in rows if r.get("region") == matched_region]
            applied_filters.append(f"区域：{matched_region}")

    if not ask_group_owner:
        matched_owner = next((own for own in known_owners if source.layout.norm(own) in q_norm), None)
        if matched_owner:
            rows = [r for r in rows if r.get("owner") == matched_owner]
            applied_filters.append(f"负责人：{matched_owner}")

    # Payment status filter
    want_unpaid = any(w in q for w in ("未收款", "未收", "待收", "未回款", "待回款", "欠款", "还没收", "尚未回款", "未结清"))
    want_cleared = any(w in q for w in ("已结清", "全额回款", "全部收回", "已收齐", "无未收"))
    want_paid = not want_unpaid and not want_cleared and any(w in q for w in ("已收款", "已回款", "有回款"))

    # Note: if user says "统计各区域未收款金额", keep grouping by region! Only filter to unpaid items when listing or explicitly asking unpaid contracts
    if want_unpaid and (not is_grouped or "未收款合同" in q or "未回款合同" in q):
        rows = [r for r in rows if (r.get("outstanding") is not None and _dec(r["outstanding"]) > 0)
                or (r.get("outstanding") is None and r.get("amount") is not None and _dec(r.get("received")) < _dec(r["amount"]))]
        applied_filters.append("状态：存在未收款")
    elif want_cleared:
        rows = [r for r in rows if r.get("outstanding") is not None and _dec(r["outstanding"]) == 0 and _dec(r.get("received")) > 0]
        applied_filters.append("状态：已全额回款")
    elif want_paid and not is_grouped:
        rows = [r for r in rows if r.get("received") is not None and _dec(r["received"]) > 0]
        applied_filters.append("状态：已有回款记录")

    # Amount threshold filter
    th_op, th_val = _extract_threshold(q)
    if th_op and th_val is not None:
        metric_field = "outstanding" if want_unpaid else "received" if want_paid else "amount"
        metric_label = {"amount": "合同金额", "outstanding": "未收金额", "received": "已收金额"}[metric_field]
        rows = [r for r in rows if r.get(metric_field) is not None and (
            _dec(r[metric_field]) >= th_val if th_op == "gt" else _dec(r[metric_field]) <= th_val
        )]
        applied_filters.append(f"{metric_label}{'≥' if th_op == 'gt' else '≤'}{_money_str(th_val)}元")

    # Keyword extraction for specific project/customer lookup (only when not a generic grouped query)
    if not is_grouped:
        residual = q
        residual = re.sub(r"20\d{2}\s*[-年/.]?\s*(?:\d{1,2}\s*月?)?", " ", residual)
        residual = re.sub(r"(?:大于|超过|高于|不少于|不低于|小于|低于|少于|不足|>=|<=|＞|＜)?\s*\d+(?:\.\d+)?\s*(?:万元|万|元)?(?:以上|以下|以内)?", " ", residual)
        for token in [*known_regions, *known_owners, *STOP_WORDS]:
            if token:
                residual = residual.replace(token, " ")
        residual = re.sub(r"[，。？！、：；,.?!:;“”\"'（）()\[\]\s]+", " ", residual).strip()
        if residual:
            terms = [source.layout.norm(t) for t in residual.split() if len(t.strip()) >= 2]
            valid_terms = [t for t in terms if any(t in r["_search"] for r in eligible)]
            if valid_terms:
                rows = [r for r in rows if all(t in r["_search"] for t in valid_terms)]
                applied_filters.append(f"关键词：{'、'.join(valid_terms)}")

    top_n = _extract_top_n(q)
    total_count = len(rows)
    total_amount = sum((_dec(r["amount"]) for r in rows if r.get("amount") is not None), Decimal(0))
    total_received = sum((_dec(r["received"]) for r in rows if r.get("received") is not None), Decimal(0))
    total_outstanding = sum((_dec(r["outstanding"]) for r in rows if r.get("outstanding") is not None), Decimal(0))
    overall_rate = _pct(total_received, total_amount)

    # 3. Build grouped report or contract detail list
    if is_grouped:
        if ask_group_region:
            dim_key, dim_label, report_title = "region", "区域", f"{period_desc} · 各区域合同金额与回款统计"
            key_fn = lambda r: (r.get("region") or "").strip() or "未标注区域"
        elif ask_group_owner:
            dim_key, dim_label, report_title = "owner", "负责人", f"{period_desc} · 各负责人签约业绩与回款统计"
            key_fn = lambda r: (r.get("owner") or "").strip() or "未标注负责人"
        elif ask_group_month:
            dim_key, dim_label, report_title = "month", "签订月份", f"{period_desc} · 月度合同签约与回款汇总"
            key_fn = lambda r: (r.get("date_value") or "")[:7] or "未注明月份"
        elif ask_group_year:
            dim_key, dim_label, report_title = "year", "签订年份", "历年合同金额与回款汇总"
            key_fn = lambda r: (r.get("date_value") or "")[:4] or "未注明年份"
        else:
            dim_key, dim_label, report_title = "customer", "客户单位", f"{period_desc} · 客户合同金额统计"
            key_fn = lambda r: (r.get("customer") or "").strip() or "未注明客户"

        buckets: dict[str, list[dict]] = {}
        for r in rows:
            buckets.setdefault(key_fn(r), []).append(r)

        group_rows = []
        sort_metric = "outstanding" if want_unpaid else "received" if want_paid else "amount"
        for name, b_items in buckets.items():
            amt = sum((_dec(x["amount"]) for x in b_items if x.get("amount") is not None), Decimal(0))
            rec = sum((_dec(x["received"]) for x in b_items if x.get("received") is not None), Decimal(0))
            out = sum((_dec(x["outstanding"]) for x in b_items if x.get("outstanding") is not None), Decimal(0))
            group_rows.append({
                "group_name": name,
                "count": len(b_items),
                "amount": _plain_dec(amt),
                "received": _plain_dec(rec),
                "outstanding": _plain_dec(out),
                "received_rate": _pct(rec, amt),
                "share": _pct(amt, total_amount),
                "_sort_amt": out if sort_metric == "outstanding" else rec if sort_metric == "received" else amt,
            })

        if dim_key in ("month", "year"):
            group_rows.sort(key=lambda x: (x["group_name"].startswith("未"), x["group_name"]))
        else:
            group_rows.sort(key=lambda x: (x["group_name"].startswith("未"), -x["_sort_amt"], -x["count"], x["group_name"]))

        if top_n:
            group_rows = group_rows[:top_n]
            report_title += f"（前 {top_n} 名）"

        for idx, gr in enumerate(group_rows, 1):
            gr["rank"] = idx
            gr.pop("_sort_amt", None)

        columns = [
            {"prop": "rank", "label": "序号", "width": 68, "align": "center"},
            {"prop": "group_name", "label": dim_label, "min_width": 160},
            {"prop": "count", "label": "合同数（份）", "width": 115, "align": "right"},
            {"prop": "amount", "label": "合同总金额（元）", "min_width": 155, "align": "right", "kind": "money"},
            {"prop": "received", "label": "已收金额（元）", "min_width": 150, "align": "right", "kind": "money"},
            {"prop": "outstanding", "label": "未收金额（元）", "min_width": 150, "align": "right", "kind": "money"},
            {"prop": "received_rate", "label": "回款率", "width": 95, "align": "right"},
            {"prop": "share", "label": "金额占比", "width": 95, "align": "right"},
        ]

        top_desc = ""
        ranked_real = [g for g in group_rows if not g["group_name"].startswith("未")]
        if ranked_real:
            head = ranked_real[:3]
            top_desc = "其中位居前列的依次为：" + "；".join(
                f"{g['group_name']}（{g['count']}份，合同额 {_money_str(g['amount'])} 元，未收 {_money_str(g['outstanding'])} 元，占比 {g['share']}）"
                for g in head
            ) + "。"

        answer = (
            f"【{report_title}】共统计 {total_count} 份合同（分属 {len(buckets)} 个{dim_label}），"
            f"合同总金额 {_money_str(total_amount)} 元，已收金额 {_money_str(total_received)} 元，"
            f"未收金额 {_money_str(total_outstanding)} 元，整体回款率 {overall_rate}。{top_desc}"
        ) if total_count else f"在当前条件（{' · '.join(applied_filters)}）下未匹配到符合要求的合同记录。"

        return {
            "question": q_raw,
            "mode": "grouped",
            "title": report_title,
            "filters": applied_filters,
            "answer": answer,
            "summary": {
                "count": total_count,
                "amount": _plain_dec(total_amount),
                "received": _plain_dec(total_received),
                "outstanding": _plain_dec(total_outstanding),
                "received_rate": overall_rate,
            },
            "columns": columns,
            "rows": group_rows,
            "total_rows": len(group_rows),
            "truncated": False,
            "revision": wb.revision,
        }

    # Detail list mode
    decorated = performance_details.decorate(db, p, wb, rows)
    if want_unpaid:
        decorated.sort(key=lambda r: (-_dec(r.get("outstanding")), -_dec(r.get("amount")), r.get("date_value") or ""))
        report_title = f"{period_desc} · 未收款合同清单"
    elif top_n or th_op:
        decorated.sort(key=lambda r: (-_dec(r.get("amount")), r.get("date_value") or ""))
        report_title = f"{period_desc} · 合同筛选清单"
    else:
        decorated.sort(key=lambda r: (r.get("date_value") or "", _dec(r.get("amount"))), reverse=True)
        report_title = f"{period_desc} · 合同明细查询报表"

    if top_n:
        decorated = decorated[:top_n]
        report_title += f"（前 {top_n} 条）"

    max_rows = 200
    truncated = len(decorated) > max_rows
    shown = decorated[:max_rows]

    list_rows = []
    for idx, r in enumerate(shown, 1):
        list_rows.append({
            "rank": idx,
            "id": r["id"],
            "sheet_id": r["sheet_id"],
            "row": r["row"],
            "detail_path": r.get("detail_path"),
            "contract_no": r.get("contract_no") or "—",
            "name": r.get("name") or "—",
            "customer": r.get("customer") or "—",
            "region": r.get("region") or "—",
            "owner": r.get("owner") or "—",
            "amount": r.get("amount"),
            "amount_text": r.get("amount_text"),
            "received": r.get("received"),
            "outstanding": r.get("outstanding"),
            "received_rate": f"{r['received_rate'] * 100:.1f}%" if r.get("received_rate") is not None else "—",
            "date": r.get("date") or "—",
        })

    columns = [
        {"prop": "contract_no", "label": "合同编号", "width": 125},
        {"prop": "name", "label": "合同名称", "min_width": 230, "kind": "link"},
        {"prop": "customer", "label": "客户单位", "min_width": 160},
        {"prop": "region", "label": "区域", "width": 88},
        {"prop": "owner", "label": "负责人", "width": 90},
        {"prop": "amount", "label": "合同金额（元）", "min_width": 138, "align": "right", "kind": "money"},
        {"prop": "received", "label": "已收金额（元）", "min_width": 135, "align": "right", "kind": "money"},
        {"prop": "outstanding", "label": "未收金额（元）", "min_width": 135, "align": "right", "kind": "money"},
        {"prop": "date", "label": "签订日期", "width": 110},
    ]

    if total_count:
        top_examples = "；".join(
            f"《{r['name']}》（{'未收 ' + _money_str(r['outstanding']) if want_unpaid and r.get('outstanding') is not None else '合同额 ' + _money_str(r.get('amount'))} 元）"
            for r in list_rows[:3]
        )
        answer = (
            f"【{report_title}】共筛选出 {total_count} 份合同，"
            f"合同总金额 {_money_str(total_amount)} 元，已收金额 {_money_str(total_received)} 元，"
            f"未收金额 {_money_str(total_outstanding)} 元（整体回款率 {overall_rate}）。"
            f"主要项目包括：{top_examples}。"
        )
    else:
        answer = f"在当前条件（{' · '.join(applied_filters)}）下未查询到匹配的合同记录，可尝试调整提问关键词或年份范围。"

    return {
        "question": q_raw,
        "mode": "list",
        "title": report_title,
        "filters": applied_filters,
        "answer": answer,
        "summary": {
            "count": total_count,
            "amount": _plain_dec(total_amount),
            "received": _plain_dec(total_received),
            "outstanding": _plain_dec(total_outstanding),
            "received_rate": overall_rate,
        },
        "columns": columns,
        "rows": list_rows,
        "total_rows": total_count,
        "truncated": truncated,
        "revision": wb.revision,
    }


# ---------------------------------------------------------------------------
# Collection Follow-up Stage Summary & Archiving
# ---------------------------------------------------------------------------

SCOPE_LABELS = {
    "all": "全部未收及预估回款项目",
    "current_month": "当月预估回款项目",
    "next_month": "次月预估回款项目",
    "tracked": "已登记跟进进展项目",
    "problems": "存在问题 / 需协调项目",
}


def build_collection_summary(db, p, *, scope: str = "all", owner: str = "", focus_prompt: str = "",
                             include_cleared=False, project_name="", offset=0, limit=150):
    """Build a structured text summary + item table of current collection follow-up status."""
    if scope not in SCOPE_LABELS:
        raise bad_request("汇总范围参数无效")
    wb = source.active_source(db, p, "collections")
    if wb is None:
        raise not_found("回款来源")
    sheets = source.collection_sheets(wb)
    if not sheets:
        raise bad_request("回款情况的两张固定来源子表尚未完整同步")

    ref_date = today()
    cur_period = month_of(ref_date)
    all_rows = source.module_rows(wb, "collections")
    overall = collection_amounts.summary(sheets[0], all_rows)
    actual_month = metrics.collection_metrics(wb, cur_period, ref_date)
    actual_by_row = {}
    for receipt in actual_month["rows"]:
        key = (receipt["sheet_id"], receipt["row"])
        actual_by_row[key] = actual_by_row.get(key, Decimal(0)) + Decimal(receipt["received"])

    plan_periods = collection_plans.periods(ref_date)
    plan_buckets = {}
    plan_item_map: dict[str, dict[str, dict]] = {}
    for key, period, title in plan_periods:
        b = collection_plans.bucket(wb, period)
        plan_buckets[key] = {
            "period": period,
            "title": title,
            "amount": b["amount"],
            "count": b["count"],
            "available": b["available"],
        }
        for it in b["items"]:
            plan_item_map.setdefault(it["id"], {})[key] = it

    tracked_map = followups.all_active_followups(db, wb)

    # Filter candidate rows
    owner_norm = source.layout.norm(owner) if owner else ""
    unpaid_rows = [
        r for r in all_rows
        if (include_cleared or r.get("outstanding") is None or _dec(r["outstanding"]) != 0 or r["id"] in plan_item_map or r["id"] in tracked_map)
        and (not owner_norm or source.layout.norm(r.get("owner")) == owner_norm)
        and (not project_name or source.layout.norm(r["name"]) == source.layout.norm(project_name))
    ]

    if scope == "current_month":
        scoped_rows = [r for r in unpaid_rows if "current" in plan_item_map.get(r["id"], {})]
    elif scope == "next_month":
        scoped_rows = [r for r in unpaid_rows if "next" in plan_item_map.get(r["id"], {})]
    elif scope == "tracked":
        scoped_rows = [r for r in unpaid_rows if r["id"] in tracked_map]
    elif scope == "problems":
        scoped_rows = [r for r in unpaid_rows if tracked_map.get(r["id"], {}).get("has_problem")]
    else:
        scoped_rows = unpaid_rows

    stage_counts = {k: 0 for k in followups.STAGES}
    pending_count = 0
    untracked_count = 0
    problem_items = []
    progressed_items = []
    untracked_highlights = []
    table_items = []

    for r in scoped_rows:
        tr = tracked_map.get(r["id"])
        plans_here = plan_item_map.get(r["id"], {})
        cur_plan = plans_here.get("current")
        nxt_plan = plans_here.get("next")
        cur_plan_amt = cur_plan.get("plan_amount") if cur_plan else None
        nxt_plan_amt = nxt_plan.get("plan_amount") if nxt_plan else None
        src_progress = (cur_plan or nxt_plan or {}).get("source_progress")

        entry = {
            "id": r["id"],
            "sheet_id": r["sheet_id"],
            "row": r["row"],
            "name": r["name"],
            "customer": r.get("customer") or "—",
            "owner": r.get("owner") or "待确认",
            "amount": r.get("amount"),
            "outstanding": r.get("outstanding"),
            "month_received": _plain_dec(actual_by_row.get((r["sheet_id"], r["row"]), Decimal(0))) if actual_month["available"] else None,
            "current_plan_amount": cur_plan_amt,
            "next_plan_amount": nxt_plan_amt,
            "source_progress": src_progress,
            "stage": tr["stage"] if tr else None,
            "stage_name": tr["stage_name"] if tr else "未登记跟进",
            "has_problem": tr["has_problem"] if tr else False,
            "content": tr["content"] if tr else None,
            "problems": tr["problems"] if tr else None,
            "next_step": tr["next_step"] if tr else None,
            "expected_on": tr["expected_on"] if tr else None,
            "updated_at": str(tr["updated_at"])[:16].replace("T", " ") if (tr and tr.get("updated_at")) else None,
            "report_path": f"/collections/items/{r['sheet_id']}/{r['row']}/report",
        }
        table_items.append(entry)

        if tr:
            if tr.get("stage") in stage_counts:
                stage_counts[tr["stage"]] += 1
            elif tr.get("pending"):
                pending_count += 1
            if tr.get("has_problem"):
                problem_items.append(entry)
            progressed_items.append(entry)
        else:
            untracked_count += 1
            untracked_highlights.append(entry)

    # Sort items: problem items first, then tracked, then by current_plan / outstanding desc
    table_items.sort(key=lambda x: (
        not x["has_problem"],
        x["stage"] is None and x["stage_name"] == "未登记跟进",
        -_dec(x.get("current_plan_amount")),
        -_dec(x.get("outstanding")),
    ))
    untracked_highlights.sort(key=lambda x: (-_dec(x.get("current_plan_amount")), -_dec(x.get("outstanding"))))

    cur_card = plan_buckets["current"]
    nxt_card = plan_buckets["next"]
    scope_title = SCOPE_LABELS[scope] + (f"（负责人：{owner}）" if owner else "")
    report_title = f"回款与项目跟进阶段性总结（{ref_date:%Y-%m-%d}）"

    # Build deterministic high-precision Chinese executive report
    lines = [
        f"【{report_title}】",
        f"统计日期：{ref_date:%Y-%m-%d}  ｜  汇总范围：{scope_title}  ｜  原表版本：{wb.revision}",
        "",
        "一、回款总体资金盘面",
        f"1. 全部合同总额：{_money_str(overall['amount']['value'])} 元；当前未收总额：{_money_str(overall['outstanding']['value'])} 元。",
        f"2. 本月实际已回款（{cur_period}）：{_money_str(actual_month['amount']) if actual_month['available'] else '待核对'} 元"
        + (f"（共 {len(actual_month['rows'])} 笔）。" if actual_month["available"] else "。"),
        f"3. 当月预估回款金额（{cur_card['period']}）：{_money_str(cur_card['amount']) if cur_card['available'] else '待核对'} 元（涉及 {cur_card['count']} 个项目）。",
        f"4. 次月预估回款金额（{nxt_card['period']}）：{_money_str(nxt_card['amount']) if nxt_card['available'] else '待核对'} 元（涉及 {nxt_card['count']} 个项目）。",
        "",
        "二、项目执行跟进进展概况",
        f"本次纳入汇总共 {len(scoped_rows)} 个项目，其中已登记执行跟进 {len(progressed_items)} 项，存在问题/需协调 {len(problem_items)} 项，暂未登记线上跟进反馈 {untracked_count} 项。",
    ]
    stage_parts = [f"{label} {stage_counts[k]} 项" for k, label in followups.STAGES.items() if stage_counts[k] > 0]
    if pending_count:
        stage_parts.append(f"待回复催办 {pending_count} 项")
    if stage_parts:
        lines.append("阶段分布：" + "、".join(stage_parts) + "。")

    lines.extend(["", "三、存在问题及需协调卡点汇总"])
    if problem_items:
        for idx, it in enumerate(problem_items, 1):
            money_tag = f"未收 {_money_str(it['outstanding'])} 元" if it.get("outstanding") is not None else "未收待核对"
            if it.get("current_plan_amount"):
                money_tag += f"，当月预估 {_money_str(it['current_plan_amount'])} 元"
            prob_txt = it.get("problems") or it.get("content") or "需协调推进"
            next_txt = f"；下一步：{it['next_step']}" if it.get("next_step") else ""
            lines.append(f"{idx}. 《{it['name']}》（客户：{it['customer']}，负责人：{it['owner']}，{money_tag}）")
            lines.append(f"   - 存在问题：{prob_txt}{next_txt}")
    else:
        lines.append("当前已反馈项目中暂无标记“遇到阻碍 / 存在问题”的卡点事项。")

    lines.extend(["", "四、已反馈跟进明细与重点待推进项目"])
    normal_tracked = [it for it in progressed_items if not it["has_problem"]]
    if normal_tracked:
        lines.append(f"（一）正常推进项目（共 {len(normal_tracked)} 项）：")
        for idx, it in enumerate(normal_tracked[:30], 1):
            exp_txt = f"，预计回款日：{it['expected_on']}" if it.get("expected_on") else ""
            nxt_txt = f"，下一步：{it['next_step']}" if it.get("next_step") else ""
            lines.append(
                f"{idx}. 《{it['name']}》（{it['owner']} · {it['stage_name']}{exp_txt}）：{it['content'] or '已跟进'}{nxt_txt}"
            )
        if len(normal_tracked) > 30:
            lines.append(f"   ……其余 {len(normal_tracked) - 30} 项详见下方跟进明细表。")
    else:
        lines.append("（一）正常推进项目：暂无其余已反馈记录。")

    if untracked_highlights and scope != "tracked" and scope != "problems":
        top_untracked = untracked_highlights[:8]
        lines.append(f"（二）重点待补充跟进反馈项目（共 {untracked_count} 项未在线登记，列示前 {len(top_untracked)} 项重点项目）：")
        for idx, it in enumerate(top_untracked, 1):
            plan_note = f"，当月预估 {_money_str(it['current_plan_amount'])} 元" if it.get("current_plan_amount") else ""
            src_note = f"（原表备注：{it['source_progress']}）" if it.get("source_progress") else ""
            lines.append(
                f"{idx}. 《{it['name']}》— 负责人：{it['owner']}，未收 {_money_str(it['outstanding']) if it.get('outstanding') is not None else '待核对'} 元{plan_note}{src_note}"
            )

    deterministic_text = "\n".join(lines)
    final_text = deterministic_text
    assisted = False

    # Optional custom focus refinement via LLM strictly anchored on deterministic facts
    clean_focus = (focus_prompt or "").strip()
    if clean_focus:
        if llm.enabled():
            refined = llm.complete(
                system=(
                    "你是企业经营管理中心的回款总结撰写专家。下面给你一份100%精确核对过的回款盘面与跟进台账事实报告，以及用户的特定汇报要求。"
                    "请严格基于给定事实重新组织或补充阶段性汇报总结。铁律：绝对不得修改、编造任何金额、笔数、项目名称或负责人；"
                    "条理清晰、语气专业庄重，可直接用于向公司领导层汇报与留档。"
                ),
                user=f"【用户补充总结要求】\n{clean_focus}\n\n【核定事实底稿】\n{deterministic_text}",
                temperature=0.2,
                max_tokens=2000,
                timeout_seconds=15,
            )
            if refined:
                final_text = refined
                assisted = True
        if not assisted:
            final_text = f"【本期重点关注说明：{clean_focus}】\n\n" + deterministic_text

    facts = {
        "date": str(ref_date),
        "scope": scope,
        "scope_label": scope_title,
        "owner_filter": owner or None,
        "revision": wb.revision,
        "synced_at": wb.synced_at.astimezone(TZ).isoformat() if wb.synced_at else None,
        "total_items": len(scoped_rows),
        "tracked_count": len(progressed_items),
        "problem_count": len(problem_items),
        "untracked_count": untracked_count,
        "overall_amount": overall["amount"]["value"],
        "overall_outstanding": overall["outstanding"]["value"],
        "month_actual_received": _plain_dec(actual_month["amount"]),
        "current_plan": cur_card,
        "next_plan": nxt_card,
    }

    return {
        "title": report_title,
        "text": final_text,
        "assisted": assisted,
        "facts": facts,
        "items": table_items[offset:offset + limit],
        "total_items": len(table_items),
    }


def collection_progress_brief(db, p, *, page=1, size=5, project_name=""):
    report = build_collection_summary(db, p, include_cleared=True, project_name=project_name,
                                      offset=(page - 1) * size, limit=size)
    facts, total = report["facts"], report["total_items"]
    def yuan(value):
        if value is None:
            return "待核对"
        return f"{Decimal(value):,f} 元"

    lines = [f"回款进展汇报 · {facts['date']}",
             f"全部合同当月实收 {yuan(facts['month_actual_received'])}，当月预估 {yuan(facts['current_plan']['amount'])}。",
             f"本次共 {total} 份合同，已登记跟进 {facts['tracked_count']} 份，存在问题 {facts['problem_count']} 份。"]
    for index, item in enumerate(report["items"], (page - 1) * size + 1):
        lines.extend(["", f"{index}. {item['name']} · {item['owner']}",
                      f"当月实收 {yuan(item['month_received'])}，未收余额 {yuan(item['outstanding'])}。",
                      f"{item['stage_name']} · {item['content'] or item['source_progress'] or '暂无进展记录'}"])
        if item["problems"]:
            lines.append("未收原因或问题：" + item["problems"])
        if item["next_step"]:
            lines.append("下一步：" + item["next_step"])
        if item["expected_on"]:
            lines.append("预计回款日：" + item["expected_on"])
    next_page = page + 1 if page * size < total else None
    if next_page:
        request = f"汇报「{project_name}」的回款进展" if project_name else "逐笔回款汇报"
        lines.append(f"\n继续查看可输入「{request} 第{next_page}页」。")
    lines.append(f"\n来源：固定经营工作簿 · 版本 {facts['revision']}" +
                 (f" · 同步于 {facts['synced_at'][:16].replace('T', ' ')}" if facts["synced_at"] else ""))
    return {"text": "\n".join(lines), "items": report["items"], "total": total, "page": page,
            "next_page": next_page, "revision": facts["revision"], "synced_at": facts["synced_at"]}


def _report_brief(row: WorkbookCollectionReport, author_name: str | None) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "scope": row.scope,
        "scope_label": SCOPE_LABELS.get(row.scope, row.scope),
        "owner_filter": row.owner_filter,
        "focus_prompt": row.focus_prompt,
        "version": row.version,
        "revision": row.revision,
        "author_id": row.author_id,
        "author_name": author_name or "成员",
        "created_at": row.created_at,
    }


def list_saved_collection_reports(db, p):
    wb = source.active_source(db, p, "collections")
    if wb is None:
        raise not_found("回款来源")
    rows = list(db.scalars(
        select(WorkbookCollectionReport)
        .where(WorkbookCollectionReport.workbook_id == wb.id)
        .order_by(WorkbookCollectionReport.id.desc())
        .limit(100)
    ))
    users = dict(db.execute(select(SysUser.id, SysUser.name).where(
        SysUser.id.in_({r.author_id for r in rows})
    )).all()) if rows else {}
    return {"items": [_report_brief(r, users.get(r.author_id)) for r in rows]}


def get_saved_collection_report(db, p, report_id: int):
    wb = source.active_source(db, p, "collections")
    if wb is None:
        raise not_found("回款来源")
    row = db.get(WorkbookCollectionReport, report_id)
    if not row or row.workbook_id != wb.id:
        raise not_found("已留档回款报告")
    author = db.get(SysUser, row.author_id)
    return {**_report_brief(row, author.name if author else None), "text": row.text, "facts": row.facts or {}}


def save_collection_report(db, p, *, title: str = "", scope: str = "all", owner: str = "",
                           focus_prompt: str = "", text: str = ""):
    if scope not in SCOPE_LABELS:
        raise bad_request("汇总范围参数无效")
    wb = source.active_source(db, p, "collections")
    if wb is None:
        raise not_found("回款来源")
    generated = build_collection_summary(db, p, scope=scope, owner=owner, focus_prompt=focus_prompt)
    final_text = (text or "").strip() or generated["text"]
    final_title = (title or "").strip() or generated["title"]

    max_ver = db.scalar(select(func.max(WorkbookCollectionReport.version)).where(
        WorkbookCollectionReport.workbook_id == wb.id,
        WorkbookCollectionReport.scope == scope,
    ).execution_options(include_removed_records=True)) or 0
    row = WorkbookCollectionReport(
        workbook_id=wb.id,
        revision=wb.revision,
        author_id=p.id,
        title=final_title[:200],
        scope=scope,
        owner_filter=(owner or "").strip()[:80] or None,
        focus_prompt=(focus_prompt or "").strip() or None,
        version=int(max_ver) + 1,
        text=final_text,
        facts=generated["facts"],
    )
    db.add(row)
    db.flush()
    log_op(db, p.id, "collections.save_summary_report", "workbook_collection_report", row.id,
           after={"version": row.version, "scope": scope})
    author = db.get(SysUser, p.id)
    return {**_report_brief(row, author.name if author else p.name), "text": row.text, "facts": row.facts}
