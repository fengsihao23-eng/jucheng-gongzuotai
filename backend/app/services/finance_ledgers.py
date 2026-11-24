"""Import, query and correct standalone finance ledgers."""
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import re
from uuid import uuid4

from sqlalchemy import column, delete, func, or_, select, text, update

from app.core.audit import log_op
from app.core.db import JSON
from app.core.response import bad_request, conflict, forbidden, not_found, page_data
from app.core.timeutil import utcnow
from app.models import FinLedgerEntry, FinLedgerImport, SysUser
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
    ambiguous, skipped = {}, []
    for ident, group in list(groups.items()):
        plan, strong = source.rule_plan(group, (*f.ACTIVE_KINDS, "qr_receipt"), preferred, filename)
        kind = plan["kind"]
        if strong and kind not in allowed:
            reason = "二维码收款明细已停用" if kind == "qr_receipt" else f"没有{f.LEDGERS[kind].label}的导入权限"
            skipped.append({"name": group["name"], "reason": reason})
            del groups[ident]
            continue
        if kind not in allowed:
            plan, strong = source.rule_plan(group, allowed, preferred, filename)
        group.update(plan)
        if not strong or source.needs_xiaoju(group):
            ambiguous[ident] = {**group, "recognized_kind": group["kind"] if strong else None}
    if not groups:
        raise bad_request("没有可导入的工作表。" + "；".join(dict.fromkeys(g["reason"] for g in skipped)))
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
                            draft={"groups": groups, "edits": {}, "notice": notice, "skipped": skipped})
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
    return [kind for kind in f.ACTIVE_KINDS if f.can_import(principal, kind)]


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
        if header_row and source.recognizable(kind, set(source.mapping_for(kind, cells).values()), labels):
            continue
        values = source.mapped_values(cells, mapping)
        end, values = extracted.get(number, (number, values))
        if source.is_total(cells, values):
            continue
        if group["account"] and any(field.key == "account" for field in f.LEDGERS[kind].fields):
            values = {**values, "account": values.get("account") or group["account"]}
        raw = [v[:250] for i in range(number, end + 1) for v in by_number.get(i, []) if v][:14]
        values = values.copy()
        flow_error = source.cash_source_values(values, group.get("direction_labels", {})) if kind == "cash_journal" else ""
        yield number, values, raw, flow_error


def without_cash_placeholders(group, rows, edits):
    if group.get("records"):
        return rows
    source_cells = dict(group["source"])
    placeholder_columns = {int(col) for col, key in group["mapping"].items() if key in {"sequence", "balance"}}
    end = len(rows)
    # Cash templates may prefill row numbers and copy the closing balance down the sheet.
    for row in reversed(rows):
        if row["id"] in edits or set(row["errors"]) != {"date", "income"}:
            break
        sequence = row["values"]["sequence"]
        if sequence and not re.fullmatch(r"\d+(?:\.0+)?", sequence):
            break
        if any(value for col, value in enumerate(source_cells[row["row"]]) if col not in placeholder_columns):
            break
        end -= 1
    if not 0 < end < len(rows) or rows[end - 1]["errors"]:
        return rows
    previous_balance = rows[end - 1]["values"]["balance"]
    return rows[:end] + [row for row in rows[end:] if row["values"]["balance"] not in {"", previous_balance}]


def proposed_rows(batch):
    rows = []
    skipped_blank_rows = 0
    edits = batch.draft["edits"]
    for ident, group in batch.draft["groups"].items():
        kind = group["kind"]
        if kind not in f.ACTIVE_KINDS:
            continue
        candidates = list(raw_rows(group))
        group_rows = []
        for i, (number, values, raw, flow_error) in enumerate(candidates):
            row_id = f"{ident}:{number}"
            amended = edits.get(row_id, {})
            values = {**values, **amended}
            inferred = ""
            if kind == "cash_journal" and f.is_opening(values) and not values.get("date") and not values.get("period"):
                inferred = source.opening_period(candidates, i, values.get("account"), group["period"])
                if inferred:
                    values["period"] = inferred
            values, errors, warnings = f.normalize(kind, values)
            if flow_error and not (values["income"] or values["payment"]):
                errors["income"] = flow_error
            if inferred:
                warnings.append(f"结转月份取自账表（{inferred}），请核对")
            group_rows.append({"id": row_id, "group": ident, "kind": kind, "sheet": group["name"], "row": number,
                               "values": values, "errors": errors, "warnings": warnings, "status": "error" if errors else "ready", "raw": raw,
                               "cash_category": f.cash_category(values, errors) if kind == "cash_journal" else None})
        if kind == "cash_journal":
            group_rows = without_cash_placeholders(group, group_rows, edits)
        rows.extend(group_rows)
        skipped_blank_rows += len(candidates) - len(group_rows)
    return rows, skipped_blank_rows


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
        if kind == "invoice":
            keys += ("issuer", "project")
        elif kind == f.COLLECTION:
            keys += ("issuer", "invoice_date", "name")
        facts = {k: values[k] for k in keys if values.get(k)}
        row["signature"] = signature = digest([kind, facts])
        if kind == "invoice" and values.get("invoice_no"):
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
    existing = dict(db.execute(select(m.identity, m.signature).where(m.deleted_at.is_(None), m.identity.in_(identities))).all()) if identities else {}
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
    skipped = list(batch.draft.get("skipped", []))
    for ident, group in batch.draft["groups"].items():
        if group["kind"] == "qr_receipt":
            skipped.append({"name": group["name"], "reason": "二维码收款明细已停用"})
            continue
        if group["kind"] not in kinds:
            raise forbidden("导入权限已变化，请重新上传")
        groups.append({"id": ident, **{k: group[k] for k in ("name", "kind", "header", "mapping", "account", "period", "method")},
                       "columns": source.columns_for(group),
                       "headers": [{"row": n, "text": " ｜ ".join(v[:50] for v in cells if v)[:180]}
                                   for n, cells in group["source"][:40]]})
    rows, skipped_blank_rows = proposed_rows(batch)
    rows = check_duplicates(db, rows)
    counts = Counter(row["status"] for row in rows)
    return {"id": batch.token, "version": batch.version, "filename": batch.filename,
            "notice": batch.draft["notice"], "skipped": skipped, "skipped_blank_rows": skipped_blank_rows, "groups": groups, "allowed": kinds,
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
            group.pop("direction_labels", None)
            draft["edits"] = {key: value for key, value in draft["edits"].items() if not key.startswith(f"{setting.id}:")}
        group.update(kind=setting.kind, header=setting.header, mapping=mapping, account=setting.account.strip(),
                     period=f.parse_month(setting.period) if setting.period else "", method="manual")
    batch.draft = draft
    proposed, _ = proposed_rows(batch)
    rows = {row["id"]: row for row in proposed}
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


def lock_ledger(db):
    # Serialize changes to the set of active transaction identities.
    db.execute(text("select pg_advisory_xact_lock(73112611235)"))


def write_rows(db, batch, principal, version, row_ids, ip):
    require_version(batch, version)
    if batch.result is not None:
        for kind in batch.result["selection"]:
            if kind in f.ACTIVE_KINDS:
                require_access(principal, kind, write=True)
        return batch.result
    lock_ledger(db)
    proposed, _ = proposed_rows(batch)
    rows = check_duplicates(db, proposed)
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


def can_remove(principal, kind, owner_id):
    if not f.can_read(principal, kind):
        return False
    if principal.id == owner_id and f.can_import(principal, kind):
        return True
    if principal.is_admin:
        return True
    if f.LEDGERS[kind].menu == "receivables":
        return principal.can("receivables:correct")
    return "CW-01" in principal.role_codes and principal.can("expense:allocate")


def entry_row(row, batch, principal):
    return {"id": row.id, "kind": row.kind, "period": row.period, "values": row.values,
            "cash_category": f.cash_category(row.values) if row.kind == "cash_journal" else None,
            "can_delete": can_remove(principal, row.kind, batch.owner_id),
            "source": {"filename": batch.filename, "sheet": row.source_sheet, "row": row.source_row,
                       "batch": batch.token, "imported_at": row.created_at}}


def list_entries(db, principal, kind, period, account, keyword, project, page, size, direction=None):
    m, batch = FinLedgerEntry, FinLedgerImport
    base = [m.kind == kind, m.deleted_at.is_(None)]
    filters = [*base]
    if len(period) == 4:
        filters.append(m.period.between(f"{period}-01", f"{period}-12"))
    elif period:
        filters.append(m.period == period)
    if account:
        filters.append(m.account == account)
    if keyword:
        filters.append(m.search_text.icontains(keyword, autoescape=True))
    if direction == "income":
        filters.append(m.inflow != 0)
    elif direction == "expense":
        filters.append(m.outflow != 0)
    if project == "unassigned":
        filters.extend((m.project.is_(None), m.outflow != 0))
    elif project:
        filters.append(m.project == project)
    total, inflow, outflow, fee = db.execute(select(func.count(m.id), func.sum(m.inflow), func.sum(m.outflow),
                                                   func.sum(m.fee)).where(*filters)).one()
    order = (m.period.desc(), m.ledger_date.asc().nulls_first(), m.import_id, m.source_row, m.id) if kind == "cash_journal" else (m.ledger_date.desc(), m.id.desc())
    rows = db.execute(select(m, batch).join(batch, batch.id == m.import_id).where(*filters)
                      .order_by(*order).offset((page - 1) * size).limit(size))
    return page_data([entry_row(r, b, principal) for r, b in rows], total, page, size, totals={
        "inflow": (inflow or f.ZERO) if direction != "expense" else f.ZERO,
        "outflow": (outflow or f.ZERO) if direction != "income" else f.ZERO, "fee": fee or f.ZERO},
        accounts=list(db.scalars(select(m.account).where(*base, m.account.is_not(None)).distinct().order_by(m.account).limit(300))))


def assign_project(db, principal, ident, project, ip):
    require_access(principal, "bank_expense")
    if not principal.can("expense:allocate"):
        raise forbidden("没有归集支出的权限")
    row = db.scalar(select(FinLedgerEntry).where(FinLedgerEntry.id == ident, FinLedgerEntry.deleted_at.is_(None)).with_for_update())
    if row is None or row.kind not in {"bank_expense", "cash_journal"} or not row.outflow:
        raise not_found("支出记录")
    before = row.project
    row.project = project or None
    row.values = {**row.values, "project": project}
    row.search_text = search_text(row.values)
    log_op(db, principal.id, "finance_ledger.allocate", "finance_ledger_entry", row.id,
           before={"project": before}, after={"project": row.project}, ip=ip)
    return entry_row(row, db.get(FinLedgerImport, row.import_id), principal)


def delete_entry(db, principal, ident, reason, ip):
    lock_ledger(db)
    m, batches = FinLedgerEntry, FinLedgerImport
    found = db.execute(select(m, batches).join(batches, batches.id == m.import_id)
                       .where(m.id == ident).with_for_update(of=m)).one_or_none()
    if found is None:
        raise not_found("台账记录")
    row, batch = found
    if not can_remove(principal, row.kind, batch.owner_id):
        raise forbidden("没有此笔台账记录的删除权限")
    if row.deleted_at is not None:
        return {"removed": 0}
    row.deleted_at, row.deleted_by, row.delete_reason = utcnow(), principal.id, reason or None
    log_op(db, principal.id, "finance_ledger.delete", "finance_ledger_entry", row.id,
           after={"kind": row.kind, "batch_id": batch.id, "sheet": row.source_sheet,
                  "row": row.source_row, "reason": reason}, ip=ip)
    return {"removed": 1}


def batch_active_kinds(batch):
    return [kind for kind in f.ACTIVE_KINDS if kind in batch.result["inserted_by_kind"]]


def may_revoke(principal, batch):
    kinds = batch_active_kinds(batch)
    return bool(kinds) and all(can_remove(principal, kind, batch.owner_id) for kind in kinds)


def list_imports(db, principal, menu, page, size):
    kinds = [kind for kind in f.ACTIVE_KINDS if f.LEDGERS[kind].menu == menu and f.can_read(principal, kind)]
    if not kinds:
        raise forbidden("没有该模块的导入记录查看权限")
    m, batch = FinLedgerEntry, FinLedgerImport
    writable = eligible(principal)
    groups = func.jsonb_each(batch.draft["groups"]).table_valued("key", column("value", JSON))
    group_kind = groups.c.value["kind"].as_string()
    complete = or_(*(batch.result["selection"][kind].as_integer().is_not(None) for kind in kinds))
    valid_draft = (batch.result.is_(None) & (batch.owner_id == principal.id) & (batch.updated_at >= utcnow() - DRAFT_TTL)
                   & select(1).select_from(groups).where(group_kind.in_(set(kinds) & set(writable))).exists()
                   & ~select(1).select_from(groups).where(group_kind.not_in([*writable, "qr_receipt"])).exists())
    matches = or_(complete, valid_draft)
    total = db.scalar(select(func.count(batch.id)).where(matches))
    batches = db.execute(select(batch, SysUser.name).join(SysUser, SysUser.id == batch.owner_id).where(matches)
                         .order_by(batch.updated_at.desc(), batch.id.desc()).offset((page - 1) * size).limit(size)).all()
    by_batch = {}
    if batches:
        for ident, kind, active in db.execute(select(m.import_id, m.kind, func.count().filter(m.deleted_at.is_(None)))
                                            .where(m.import_id.in_([item.id for item, _ in batches]), m.kind.in_(kinds))
                                            .group_by(m.import_id, m.kind)):
            by_batch.setdefault(ident, {})[kind] = active
    items = []
    for item, uploader in batches:
        current_count = sum(by_batch.get(item.id, {}).values())
        if item.result is None:
            targets = {group["kind"] for group in item.draft["groups"].values()}
            inserted = {}
            status = "draft"
        else:
            targets = item.result["selection"]
            inserted = {kind: count for kind, count in item.result["inserted_by_kind"].items() if kind in kinds}
            original = sum(inserted.values())
            status = "confirmed" if current_count == original else "partial" if current_count else "removed"
        items.append({"id": item.token, "filename": item.filename, "uploader": uploader,
                      "updated_at": item.updated_at, "status": status, "kinds": [kind for kind in kinds if kind in targets],
                      "inserted": sum(inserted.values()), "active": current_count,
                      "can_revoke": current_count > 0 and may_revoke(principal, item)})
    return page_data(items, total, page, size)


def revocable_batch(db, principal, token):
    batch = db.scalar(select(FinLedgerImport).where(FinLedgerImport.token == token))
    if batch is None or batch.result is None or not batch_active_kinds(batch):
        raise not_found("已入账批次")
    if not may_revoke(principal, batch):
        raise forbidden("整批撤销需要有该批次内各类台账的删除权限")
    return batch


def revoke_preview(db, principal, token):
    batch = revocable_batch(db, principal, token)
    m = FinLedgerEntry
    counts = dict(db.execute(select(m.kind, func.count(m.id)).where(m.import_id == batch.id, m.kind.in_(batch_active_kinds(batch)),
                                                                m.deleted_at.is_(None))
                            .group_by(m.kind)).all())
    return {"filename": batch.filename, "total": sum(counts.values()), "by_kind": counts}


def revoke_import(db, principal, token, reason, ip):
    lock_ledger(db)
    batch = revocable_batch(db, principal, token)
    m = FinLedgerEntry
    counts = Counter(db.scalars(update(m).where(m.import_id == batch.id, m.kind.in_(batch_active_kinds(batch)), m.deleted_at.is_(None))
                                .values(deleted_at=utcnow(), deleted_by=principal.id, delete_reason=reason or None)
                                .returning(m.kind)))
    result = {"removed": sum(counts.values()), "by_kind": dict(counts)}
    if counts:
        log_op(db, principal.id, "finance_ledger.revoke", "finance_ledger_import", batch.id,
               after={"filename": batch.filename, "reason": reason, **result}, ip=ip)
    return result
