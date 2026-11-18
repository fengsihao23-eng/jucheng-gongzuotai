"""Annual/monthly performance from the two owner-designated worksheets."""
from datetime import date
import re

from app.core.response import bad_request, not_found
from app.services import business_workbook as source

DEFAULT_YEAR = "2026"


def _date(text, field):
    if text:
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise bad_request(field + "格式无效") from None
    return None


def page(wb, p, *, sheet_id="", keyword="", year="", page=1, size=30, raw=False, start="", end="",
         region="", customer="", owner="", business_line="", sign_month="", source_type="", status=""):
    sheets = source.module_sheets(wb, "contracts")
    if raw:
        raise bad_request("经营业绩按年月展示业务明细，逐格原值请从明细行查看")
    if sheet_id and sheet_id != "all" and not any(s.id == sheet_id for s in sheets):
        raise not_found("经营业绩来源子表")
    if year and year != "undated" and not re.fullmatch(r"20\d{2}", str(year)):
        raise bad_request("年份筛选无效")
    start_date, end_date = _date(start, "起始日期"), _date(end, "结束日期")
    if start_date and end_date and start_date > end_date:
        raise bad_request("起始日期不能晚于结束日期")
    if sign_month:
        if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", sign_month):
            raise bad_request("签订月份格式无效")
        if year and year != sign_month[:4]:
            raise bad_request("签订月份与所选年份不一致")
        year = sign_month[:4]
    date_range = not year and not sign_month and start_date and end_date and start_date.year != end_date.year
    year = year or ("range" if date_range else (start or end)[:4]) or DEFAULT_YEAR
    if year not in {"undated", "range"} and int(year) > 2026:
        raise bad_request("当前经营业绩来源仅提供2026年及以前的数据")
    if source_type not in ("", "wecom", "4"):
        raise bad_request("经营业绩仅使用指定企微工作簿")

    profile = "sales" if year == DEFAULT_YEAR else "older"
    chosen = [s for s in sheets if s.profile == profile]
    if year in {"undated", "range"}:
        chosen = sheets
    if not chosen:
        raise bad_request("所选年份的固定来源子表尚未同步，暂不展示其他来源")
    warnings = [s.warning for s in chosen if s.warning]
    if warnings:
        raise bad_request("；".join(warnings))

    eligible = source.performance_rows(wb)
    years = sorted({DEFAULT_YEAR, *[r["date_value"][:4] for r in eligible if r["date_value"]]}, reverse=True)
    undated = sum(not r["date_value"] for r in eligible)
    rows = [r for r in eligible if (r["date_value"] if year == "range" else not r["date_value"] if year == "undated" else
            r["date_value"] and r["date_value"].startswith(year + "-") and r["profile"] == profile)]

    def choices(field):
        return sorted({r[field] for r in rows if r.get(field)})

    options = {"years": years, "regions": choices("region"), "owners": choices("owner"),
               "business_lines": choices("business_line"), "statuses": choices("contract_status"),
               "sources": [{"value": "wecom", "label": "企业微信表格"}], "undated_count": undated}
    if keyword.strip():
        rows = [r for r in rows if source.layout.norm(keyword) in r["_search"]]
    if sign_month:
        rows = [r for r in rows if (r["date_value"] or "").startswith(sign_month + "-")]
    if start or end:
        rows = [r for r in rows if r["date_value"] and (not start or r["date_value"] >= start)
                and (not end or r["date_value"] <= end)]
    if customer.strip():
        rows = [r for r in rows if source.layout.norm(customer) in source.layout.norm(r.get("customer"))]
    for field, value in (("region", region), ("owner", owner), ("business_line", business_line), ("contract_status", status)):
        if value:
            rows = [r for r in rows if source.layout.norm(r.get(field)) == source.layout.norm(value)]

    summary = chosen[0].summary(rows)
    count = len(rows)
    page = min(page, max(1, (count + size - 1) // size))
    brief = chosen[0].brief()
    brief["summary"] = summary
    if year == "undated":
        brief.update(id="undated", name="未注明签订日期的合同", controls=[])
    if year == "range":
        brief.update(id="range", name="经营业绩期间明细", controls=[])
    period_label = f"{start} 至 {end}" if year == "range" else "未注明签订日期" if year == "undated" else (sign_month.replace("-", " 年 ") + " 月" if sign_month else year + " 年全年")
    return {"active": True, "source": source.source_info(wb, p), "sheets": [s.brief() for s in chosen],
            "sheet": brief, "years": years, "summary": summary, "filter_options": options,
            "period": {"year": "" if year == "range" else year, "month": sign_month, "label": period_label,
                       "source_names": [s.name for s in chosen], "source_rule": "2026年使用2026年销售合同；以前年度使用2026年前已签单个项目未回款，按原表签订日期归集"},
            "items": [{k: v for k, v in r.items() if k != "_search"} for r in rows[(page - 1) * size:page * size]],
            "total": count, "page": page, "size": size, "columns": []}
