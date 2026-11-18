"""Owner-approved contract-total scope, independent of source cells and receipt amounts."""
from collections import Counter
import hashlib
import json

from app.core.response import bad_request, conflict
from app.services import business_workbook as source

CONFIG = "collection_amount_selection"
NOTE = "按已核对口径不计入合同总金额"


def project_key(row):
    return source.layout.key("collection-project", row.get("name"), row.get("customer"))


def party_key(row):
    return source.layout.key("collection-party", row.get("party"))


def apply(wb, rows):
    """Only amount is selected; never alter receipts, balances, plans or row identity."""
    rule = wb.config.get(CONFIG)
    if not rule:
        return [dict(r) for r in rows]
    if rule.get("version") != 1 or rule.get("source_key") != wb.source_key or rule.get("docid") != wb.config.get("docid"):
        raise bad_request("已核对的合同金额口径与当前来源不一致，请联系管理员核对")
    groups = {(g["sheet_id"], g["project_key"]): g for g in rule["groups"]}
    sequence_groups = {(g["sheet_id"], g["sequence"]): g for g in rule["groups"]}
    candidates = source.collection_sheets(wb)
    counts = Counter((s.id, project_key(r), party_key(r)) for s in candidates for r in s.items)
    result = []
    for original in rows:
        row = dict(original)
        if row.get("receipt_cell"):
            result.append(row)  # Actual receipt event amounts belong to a different measure.
            continue
        group = groups.get((row["sheet_id"], project_key(row)))
        uncertain = group is None and (row["sheet_id"], row.get("sequence")) in sequence_groups
        if group is None and not uncertain:
            result.append(row)
            continue
        selected = group["parties"].get(party_key(row)) if group else None
        if counts[row["sheet_id"], project_key(row), party_key(row)] != 1:
            selected = None
        state = "included" if selected is True else "excluded" if selected is False else "review"
        row["amount_source"] = row.get("amount_source", row.get("amount"))
        row["collection_amount_state"] = state
        if state != "included":
            row["amount"] = None
            row["amount_text"] = NOTE if state == "excluded" else "合同金额口径待核对"
            row["amount_note"] = row["amount_text"]
        result.append(row)
    return result


def summary(sheet, rows):
    report = sheet.summary(rows)
    metric = report["amount"]
    excluded = sum(r.get("collection_amount_state") == "excluded" for r in rows)
    review = sum(r.get("collection_amount_state") == "review" for r in rows)
    metric.update(excluded_count=excluded, review_count=review,
                  missing_count=metric["missing_count"] - excluded - review)
    if review:
        metric["value"] = None
    return report


def build_policy(wb, review):
    """Validate every approved value before applying a durable, position-independent rule."""
    if review.get("scope") != "collections.contract_total_only":
        raise bad_request("核对清单的适用范围无效")
    if wb.revision != review["base_revision"]:
        raise conflict("原表版本已变化，请核对最新版后再清洗")
    sheet = next((s for s in source.collection_sheets(wb) if s.id == review["source_sheet_id"]), None)
    if not sheet or sheet.profile != "older" or source.layout.norm(sheet.name) != source.layout.norm(review["source_sheet"]):
        raise conflict("核对清单的来源子表不匹配")
    groups = {}
    touched = set()
    for selected, entries in ((True, review["keep"]), (False, review["exclude_amount_only"])):
        for entry in entries:
            row = next((r for r in sheet.items if r["row"] == entry["row"]), None)
            if not row or (source.layout.norm(row["name"]), source.layout.norm(row["party"]), row["amount"]) != (
                    source.layout.norm(entry["project"]), source.layout.norm(entry["party"]), entry["source_amount"]):
                raise conflict(f"原表{entry['cell']}的项目、签约主体或金额已变化")
            key = project_key(row)
            group = groups.setdefault(key, {"sheet_id": sheet.id, "project_key": key, "sequence": row["sequence"],
                                            "name": row["name"], "parties": {}})
            ident = party_key(row)
            if ident in group["parties"]:
                raise bad_request("清单包含重复签约主体")
            group["parties"][ident] = selected
            touched.add(row["row"])
    covered = {r["row"] for r in sheet.items if project_key(r) in groups}
    if covered != touched:
        raise conflict("原表存在未确认的同项目金额，请重新核对")
    digest = hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"version": 1, "source_key": wb.source_key, "docid": wb.config.get("docid"),
            "review_revision": review["base_revision"], "review_sha256": digest,
            "scope": review["scope"], "groups": list(groups.values())}
