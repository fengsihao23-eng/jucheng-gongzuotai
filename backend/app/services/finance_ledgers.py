"""Preview, review and commit standalone finance ledgers."""
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from uuid import uuid4

from sqlalchemy import delete, func, select, text

from app.core.audit import log_op
from app.core.response import bad_request, conflict, forbidden, not_found, page_data
from app.core.timeutil import utcnow
from app.models import FinLedgerEntry, FinLedgerImport
from app.services import finance_ledger_fields as f, finance_ledger_source as source

DRAFT_TTL = timedelta(hours=24)


def require_access(principal, kind, *, write=False):
    allowed = f.can_import if write else f.can_read
    if not allowed(principal, kind):
        raise forbidden("没有此台账的导入权限" if write else "没有此台账的查看权限")


def build_draft(db, principal, filename, content, preferred, use_xiaoju, ip):
    require_access(principal, preferred, write=True)
    allowed = eligible(principal)
    groups = source.read_source(content, filename)
    ambiguous = {}
    for ident, group in groups.items():
        plan, strong = source.rule_plan(group, allowed, preferred, filename)
        group.update(plan)
        if not strong:
            ambiguous[ident] = group
    notice = ""
    if use_xiaoju and ambiguous:
        suggested, notice = source.run_xiaoju(ambiguous, allowed, preferred)
        for ident, plan in suggested.items():
            group = groups[ident]
            if "records" in plan and not plan["records"]:
                continue
            group.update(plan)
            group["account"], group["period"] = source.title_defaults(
                group["kind"], group["name"], filename, group["source"], group["header"])
    db.execute(delete(FinLedgerImport).where(FinLedgerImport.owner_id == principal.id, FinLedgerImport.result.is_(None),
                                            FinLedgerImport.updated_at < utcnow() - DRAFT_TTL))
    batch = FinLedgerImport(token=uuid4().hex, owner_id=principal.id, filename=filename,
                            draft={"groups": groups, "edits": {}, "notice": notice})
    db.add(batch)
    db.flush()
    data = report(db, batch, principal)
    log_op(db, principal.id, "finance_ledger.preview", "finance_ledger_import", batch.id,
           after={"filename": filename, **data["counts"]}, ip=ip)
    return data


def get_batch(db, principal, token, *, lock=False):
    query = select(FinLedgerImport).where(FinLedgerImport.token == token, FinLedgerImport.owner_id == principal.id)
    batch = db.scalar(query.with_for_update() if lock else query)
    if batch is None or (batch.result is None and utcnow() - batch.updated_at > DRAFT_TTL):
        raise not_found("导入预览（可能已过期，请重新上传）")
    return batch


def eligible(principal):
    return [kind for kind in f.LEDGERS if f.can_import(principal, kind)]


def require_version(batch, version):
    if version != batch.version:
        raise conflict("导入预览已变化，请刷新或重新上传后再确认")


def raw_rows(group):
    kind, mapping, header_row = group["kind"], group["mapping"], group["header"]
    extracted = {row: (end, values) for row, end, values in group.get("records", [])}
    used = {i for row, (end, _) in extracted.items() for i in range(row + 1, end + 1)}
    by_number = dict(group["source"])
    for number, cells in group["source"]:
        if number <= header_row or number in used:
            continue
        labels = {f.header(cell) for cell in cells if cell}
        if any(f.header(cell) in source.TOTALS for cell in [v for v in cells if v][:2]):
            continue
        if header_row and source.recognizable(kind, set(source.mapping_for(kind, cells).values()), labels):
            continue
        values = {key: cells[int(col)] if int(col) < len(cells) else "" for col, key in mapping.items()}
        end, values = extracted.get(number, (number, values))
        if group["account"] and any(field.key == "account" for field in f.LEDGERS[kind].fields):
            values = {**values, "account": values.get("account") or group["account"]}
        raw = [v[:250] for i in range(number, end + 1) for v in by_number.get(i, []) if v][:14]
        yield number, values, raw


def proposed_rows(batch):
    rows = []
    for ident, group in batch.draft["groups"].items():
        kind = group["kind"]
        candidates = list(raw_rows(group))
        for i, (number, values, raw) in enumerate(candidates):
            row_id = f"{ident}:{number}"
            amended = batch.draft["edits"].get(row_id, {})
            values = {**values, **amended}
            inferred = ""
            if kind == "cash_journal" and f.is_opening(values) and not values.get("date") and not values.get("period"):
                inferred = source.opening_period(candidates, i, values.get("account"), group["period"])
                if inferred:
                    values["period"] = inferred
            values, errors, warnings = f.normalize(kind, values)
            if inferred:
                warnings.append(f"结转月份取自账表（{inferred}），请核对")
            rows.append({"id": row_id, "group": ident, "kind": kind, "sheet": group["name"], "row": number,
                         "values": values, "errors": errors, "warnings": warnings, "status": "error" if errors else "ready", "raw": raw})
    return rows


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def identify(rows):
    occurrences = Counter()
    for row in rows:
        if row["errors"]:
            continue
        kind, values = row["kind"], row["values"]
        keys = ("date", "account", "payer", "payee", "counterparty", "customer", "approval_no", "gross", "fee", "net",
                "income", "payment", "balance", "summary", "period")
        facts = {k: values[k] for k in keys if values.get(k)}
        if kind == "qr_receipt" and not values.get("reference"):
            facts["project"] = values["project"]
        row["signature"] = signature = digest([kind, facts])
        if kind == "invoice":
            row["identity"] = digest([kind, values["invoice_no"]])
        elif values.get("reference"):
            row["identity"] = digest([kind, values.get("account"), values["reference"]])
        else:
            key = (row["group"], signature)
            occurrences[key] += 1
            row["identity"] = digest([signature, occurrences[key]])


def check_duplicates(db, rows):
    identify(rows)
    identities = {row["identity"] for row in rows if not row["errors"]}
    m = FinLedgerEntry
    existing = dict(db.execute(select(m.identity, m.signature).where(m.identity.in_(identities))).all()) if identities else {}
    seen = {}
    for row in rows:
        if row["errors"]:
            continue
        identity, signature = row["identity"], row["signature"]
        prior = existing.get(identity, seen.get(identity))
        if prior is not None and prior != signature:
            row["status"] = "error"
            key = "invoice_no" if row["kind"] == "invoice" else "reference"
            row["errors"][key] = "此编号已存在，关键数据有差异，请核对"
        elif identity in existing:
            row["status"] = "duplicate"
            row["warnings"].append("台账已存在，确认时自动跳过")
        elif identity in seen:
            row["status"] = "repeated"
            row["warnings"].append("本批有相同记录，最多入账一次")
        else:
            seen[identity] = signature
    return rows


def report(db, batch, principal):
    kinds = eligible(principal)
    if not kinds:
        raise forbidden()
    groups = []
    for ident, group in batch.draft["groups"].items():
        if group["kind"] not in kinds:
            raise forbidden("导入权限已变化，请重新上传")
        groups.append({"id": ident, **{k: group[k] for k in ("name", "kind", "header", "mapping", "account", "period", "method")},
                       "columns": source.columns_for(group),
                       "headers": [{"row": n, "text": " ｜ ".join(v[:50] for v in cells if v)[:180]}
                                   for n, cells in group["source"][:40]]})
    rows = check_duplicates(db, proposed_rows(batch))
    counts = Counter(row["status"] for row in rows)
    return {"id": batch.token, "version": batch.version, "filename": batch.filename,
            "notice": batch.draft["notice"], "groups": groups, "allowed": kinds,
            "rows": [{k: v for k, v in row.items() if k not in {"identity", "signature"}} for row in rows],
            "counts": {k: counts[k] for k in ("ready", "duplicate", "repeated", "error")}}


def review(db, batch, principal, version, settings, updates):
    require_version(batch, version)
    if batch.result is not None:
        raise conflict("此批次已经入账")
    draft = deepcopy(batch.draft)
    allowed = eligible(principal)
    for setting in settings:
        group = draft["groups"].get(setting.id)
        if group is None or setting.kind not in allowed:
            raise bad_request("请选择可导入的来源和台账")
        mapping = source.valid_mapping(group, setting.kind, setting.header, setting.mapping)
        if (setting.kind, setting.header, mapping) != (group["kind"], group["header"], group["mapping"]):
            group.pop("records", None)
            draft["edits"] = {key: value for key, value in draft["edits"].items() if not key.startswith(f"{setting.id}:")}
        group.update(kind=setting.kind, header=setting.header, mapping=mapping, account=setting.account.strip(),
                     period=f.parse_month(setting.period) if setting.period else "", method="manual")
    batch.draft = draft
    rows = {row["id"]: row for row in proposed_rows(batch)}
    for update in updates:
        row = rows.get(update.id)
        if row is None or row["kind"] not in allowed:
            raise bad_request("待修改的预览行不存在")
        if set(update.values) - {field.key for field in f.LEDGERS[row["kind"]].fields}:
            raise bad_request("预览行含有不支持的字段")
        draft["edits"][update.id] = {**draft["edits"].get(update.id, {}), **update.values}
    batch.version += 1
    return report(db, batch, principal)


def search_text(values):
    return " ".join(v for k, v in values.items() if k != "sequence" and v)


def write_rows(db, batch, principal, version, row_ids, ip):
    require_version(batch, version)
    if batch.result is not None:
        for kind in batch.result["selection"]:
            require_access(principal, kind, write=True)
        return batch.result
    # The unique identity and transaction-wide lock make concurrent imports idempotent.
    db.execute(text("select pg_advisory_xact_lock(73112611235)"))
    rows = check_duplicates(db, proposed_rows(batch))
    selected = set(row_ids)
    rows = [row for row in rows if row["id"] in selected]
    if len(rows) != len(selected):
        raise bad_request("确认清单中包含不存在的行，请重新核对")
    for row in rows:
        require_access(principal, row["kind"], write=True)
        if row["errors"]:
            raise conflict(f"「{row['sheet']}」第 {row['row']} 行需核对，请修正后入账", {"row_id": row["id"]})
    inserted, skipped, seen = Counter(), 0, set()
    for row in rows:
        if row["status"] == "duplicate" or row["identity"] in seen:
            skipped += 1
            continue
        v, kind = row["values"], row["kind"]
        inflow, outflow, fee = f.amounts(kind, v)
        db.add(FinLedgerEntry(import_id=batch.id, source_sheet=row["sheet"], source_row=row["row"], kind=kind,
                              identity=row["identity"], signature=row["signature"],
                              ledger_date=date.fromisoformat(v["date"]) if v.get("date") else None,
                              period=v["date"][:7] if v.get("date") else v.get("period") or None,
                              account=v.get("account") or None, project=v.get("project") or None, search_text=search_text(v),
                              inflow=inflow, outflow=outflow, fee=fee, values=v))
        inserted[kind] += 1
        seen.add(row["identity"])
    result = {"inserted": sum(inserted.values()), "skipped": skipped, "inserted_by_kind": dict(inserted),
              "selection": {kind: sum(r["kind"] == kind for r in rows) for kind in sorted({r["kind"] for r in rows})}}
    batch.result = result
    batch.draft = {}
    log_op(db, principal.id, "finance_ledger.confirm", "finance_ledger_import", batch.id,
           after={"filename": batch.filename, **result}, ip=ip)
    return result


def entry_row(row, batch):
    return {"id": row.id, "kind": row.kind, "period": row.period, "values": row.values,
            "source": {"filename": batch.filename, "sheet": row.source_sheet, "row": row.source_row,
                       "batch": batch.token, "imported_at": row.created_at}}


def list_entries(db, kind, period, account, keyword, project, page, size):
    m, batch = FinLedgerEntry, FinLedgerImport
    base = [m.kind == kind]
    filters = [*base]
    if period:
        filters.append(m.period == period)
    if account:
        filters.append(m.account == account)
    if keyword:
        filters.append(m.search_text.icontains(keyword, autoescape=True))
    if project == "unassigned":
        filters.extend((m.project.is_(None), m.outflow != 0))
    elif project:
        filters.append(m.project == project)
    total, inflow, outflow, fee = db.execute(select(func.count(m.id), func.sum(m.inflow), func.sum(m.outflow),
                                                   func.sum(m.fee)).where(*filters)).one()
    order = (m.period.desc(), m.ledger_date.asc().nulls_first(), m.import_id, m.source_row, m.id) if kind == "cash_journal" else (m.ledger_date.desc(), m.id.desc())
    rows = db.execute(select(m, batch).join(batch, batch.id == m.import_id).where(*filters)
                      .order_by(*order).offset((page - 1) * size).limit(size))
    return page_data([entry_row(r, b) for r, b in rows], total, page, size, totals={
        "inflow": inflow or f.ZERO, "outflow": outflow or f.ZERO, "fee": fee or f.ZERO},
        accounts=list(db.scalars(select(m.account).where(*base, m.account.is_not(None)).distinct().order_by(m.account).limit(300))))


def assign_project(db, principal, ident, project, ip):
    require_access(principal, "bank_expense")
    if not principal.can("expense:allocate"):
        raise forbidden("没有归集支出的权限")
    row = db.scalar(select(FinLedgerEntry).where(FinLedgerEntry.id == ident).with_for_update())
    if row is None or row.kind not in {"bank_expense", "cash_journal"} or not row.outflow:
        raise not_found("支出记录")
    before = row.project
    row.project = project or None
    row.values = {**row.values, "project": project}
    row.search_text = search_text(row.values)
    log_op(db, principal.id, "finance_ledger.allocate", "finance_ledger_entry", row.id,
           before={"project": before}, after={"project": row.project}, ip=ip)
    return entry_row(row, db.get(FinLedgerImport, row.import_id))
