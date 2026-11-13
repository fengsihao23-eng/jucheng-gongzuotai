"""Pull a fixed workbook, reconcile its source rows and publish one complete revision."""
from __future__ import annotations

import json
import re
import zlib
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text as sql
from sqlalchemy.orm import defer, load_only

from app.core.audit import log_op
from app.core.deps import build_principal
from app.core.response import BizError, bad_request, forbidden, not_found
from app.core.timeutil import today, utcnow
from app.models import (BizContract, BizOpportunity, BizPayment, BizProject, ContractWorkbook,
                        ContractWorkbookLink, SysUser)
from app.services import (contract_service as contracts, contract_workbook_layout as layout,
                          contract_workbook_match as match, contract_workbook_source as source,
                          opportunity_service as opportunities, region_rules, wecom_cli_service as cli)

SOURCE_TYPE = 4
JOB = "contracts_workbook_sync"
POLL_MINUTES = 10
MODELS = {"contract": BizContract, "opportunity": BizOpportunity}
FIELDS = {
    "contract": ("name", "customer_name", "amount", "sign_date", "region", "owner_id", "business_line"),
    "opportunity": ("name", "customer_name", "amount", "stage", "owner_id", "business_line"),
}
LABELS = {"name": "名称", "customer_name": "客户", "amount": "金额", "sign_date": "签订日期",
          "region": "区域", "owner_id": "跟进人", "stage": "阶段", "business_line": "业务线",
          "project_code": "关联项目"}
PROFILE_NAMES = {key: name for key, name in match.SHEET_LABELS.items()}
SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")


def profile_for(name):
    title = layout.norm(name)
    return next((profile for profile, prefix in PROFILE_NAMES.items()
                 if (prefix.isdigit() and title.startswith(f"{prefix}、"))
                 or (not prefix.isdigit() and title == layout.norm(prefix))), None)


def can_view(p, config):
    if p is None:
        return False
    return (p.id == config["reader_id"] or p.id in config.get("view_user_ids", [])
            or p.user.department_id in config.get("view_department_ids", []))


def reader(db, config):
    u = db.get(SysUser, config["reader_id"])
    p = build_principal(u) if u and u.status == 1 and not u.is_deleted else None
    if not p or p.data_scope != "all" or not p.has_menu("contracts") or not p.can("contracts:write"):
        raise forbidden("固定来源读取账号无效或失去了合同台账维护权限")
    if config.get("auto_apply") and not p.can("opp:write"):
        raise forbidden("固定来源读取账号缺少商机维护权限")
    if config.get("auto_receipts") and (not p.can("receivables:receipt") or not p.has_menu("receivables")):
        raise forbidden("固定来源读取账号缺少到账维护权限")
    return p


def configure(db, key, *, title, url, reader_id, view_department_ids=(), view_user_ids=(),
              profiles=None, enabled=True, auto_apply=False, auto_receipts=False, actor_id=None):
    if not SLUG.fullmatch(key) or not title.strip() or len(title) > 160:
        raise bad_request("请填写有效的来源名称和标识")
    doc = re.fullmatch(r"https://doc\.weixin\.qq\.com/sheet/([A-Za-z0-9_-]{5,200})(?:\?[^\s#]*)?(?:#[^\s]*)?", url)
    file = re.fullmatch(r"wedrive:([A-Za-z0-9_-]{1,200})", url)
    if not (doc or file):
        raise bad_request("请填写企微在线表格链接，或 wedrive:微盘文件ID")
    if any(profile not in layout.PROFILES for profile in (profiles or {}).values()):
        raise bad_request("子表映射无效")
    config = {"title": title.strip(), "format": "workbook" if doc else "xlsx", "reader_id": reader_id,
              "enabled": enabled, "auto_apply": auto_apply, "auto_receipts": auto_receipts, "profiles": profiles or {},
              "view_department_ids": sorted(set(view_department_ids)), "view_user_ids": sorted(set(view_user_ids))}
    config["docid" if doc else "file_id"] = (doc or file)[1]
    if doc:
        config["url"] = f"https://doc.weixin.qq.com/sheet/{doc[1]}"
    reader(db, config)
    config["binding_id"] = cli._owned(reader_id)["binding_id"]
    lock(db, key)
    row = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == key)
                    .options(defer(ContractWorkbook.sheets)).with_for_update())
    if row:
        if (row.config.get("format"), row.config.get("docid"), row.config.get("file_id")) != (
                config.get("format"), config.get("docid"), config.get("file_id")):
            raise bad_request("固定来源已经绑定；更换文档时请使用新来源")
        if any(row.config.get(f) != config[f] for f in ("auto_apply", "auto_receipts", "profiles", "reader_id", "binding_id")):
            row.revision = None
        row.config = config
    else:
        row = ContractWorkbook(source_key=key, config=config)
        db.add(row)
    log_op(db, actor_id or reader_id, "contract.workbook.configure", "contract_workbook", key,
           after={"enabled": enabled, "auto_apply": auto_apply, "reader_id": reader_id})
    db.flush()
    return row


def _entries(db):
    result = []
    common = ("id", "name", "customer_name", "amount", "business_line", "owner_id", "source_approval_no",
              "remark", "project_code", "is_deleted")
    for kind, model in MODELS.items():
        extra = ("contract_no", "sign_date", "region") if kind == "contract" else ("opp_no", "stage", "contract_no")
        for item in db.execute(select(*(getattr(model, f) for f in (*common, *extra)))).mappings():
            row = {k: match.comparable(v) for k, v in item.items()}
            row.update(kind=kind, number=row["contract_no"] if kind == "contract" else row["opp_no"])
            result.append(row)
    return result


def _source_values(record, owners, notes):
    proposed = {k: record[k] for k in ("name", "customer_name", "amount", "sign_date", "region", "stage")
                if record.get(k) is not None}
    if record.get("owner"):
        ids = owners.get(layout.norm(record["owner"]), [])
        if len(ids) == 1:
            proposed["owner_id"] = ids[0]
        else:
            notes.append("原表跟进人未对应到唯一的在职账号")
    for field in ("name", "customer_name"):
        if len(proposed.get(field, "")) > 128:
            notes.append(f"{LABELS[field]}超过业务台账字段长度，已保留完整原文")
            del proposed[field]
    return proposed


def _build_target(row, state, target=None, candidates=()):
    row["state"] = state
    if target:
        row["target"] = {"kind": target["kind"], "id": target["id"], "number": target["number"],
                         "project_code": target.get("project_code")}
    if candidates:
        row["candidates"] = [{"kind": c["kind"], "id": c["id"], "number": c["number"],
                              "name": c["name"], "customer": c["customer_name"],
                              "amount": c["amount"], "sign_date": c.get("sign_date")} for c in candidates[:12]]


def _amendments(target, proposed, link, notes, received):
    changes = {}
    for f, v in proposed.items():
        if f not in (*FIELDS[target["kind"]], "project_code") or target.get(f) == v:
            continue
        prev = link.baseline.get(f) if link else None
        if f == "stage" and target.get("stage") in {"won", "lost"}:
            continue
        if f == "amount" and Decimal(v) <= 0:
            continue
        if f == "sign_date" and date.fromisoformat(v) > today():
            notes.append("原表签订日期晚于今天，待核实")
            continue
        # On first match establish the identity and surface any disagreement.
        # On later runs update only fields still equal to the previous source.
        if link and prev is not None and target.get(f) == prev:
            if f == "amount" and target["kind"] == "contract" and Decimal(v) < received:
                notes.append("源合同金额低于已登记回款，已暂缓覆盖")
            else:
                changes[f] = v
        elif f in {"region", "owner_id", "project_code"} and target.get(f) is None:
            changes[f] = v
        elif f in {"amount", "sign_date", "name", "customer_name"}:
            notes.append(f"源表{LABELS[f]}和工作台不同，保留工作台记录待核对")
        elif link and prev is not None and v != prev:
            notes.append(f"源表{LABELS[f]}与工作台的人工修改不一致，已保留工作台记录")
    return changes


def _set_receipts(row, registered):
    rec = row["record"]
    amount = rec["received"]
    if amount is None:
        return
    if "target" not in row:
        row["receipts"] = {"source": amount, "registered": None, "difference": None}
        return
    real = registered.get(row["target"]["id"], Decimal(0))
    diff = Decimal(amount) - real
    row["receipts"] = {"source": amount, "registered": str(real.quantize(layout.CENT)),
                       "difference": str(diff.quantize(layout.CENT))}
    if diff:
        row["issues"].append("源表累计回款与工作台到账记录不同")
    if Decimal(amount) > 0 and not rec["paid_on"]:
        row["issues"].append("源表未给出明确到账日，累计金额不作为新增到账凭证")


def _receipt_totals(db):
    return dict(db.execute(select(BizPayment.contract_id, func.sum(BizPayment.amount))
                           .group_by(BizPayment.contract_id)).all())


def _public(record):
    return {k: v for k, v in record.items() if k not in {"entity", "exact", "keys", "issues", "attachment_title"}}


def _summary(sheets, removed):
    totals = Counter()
    for sheet in sheets:
        count = Counter(rows=len(sheet["rows"]))
        for row in sheet["rows"]:
            if row.get("record"):
                count["business"] += 1
                count[row["state"]] += 1
            if row.get("issues"):
                count["review"] += 1
            if row.get("receipts", {}).get("difference") not in (None, "0.00"):
                count["payment_diff"] += 1
            count["new_payments"] += row.get("receipt_sync", {}).get("new_payments", 0)
            count["linked_payments"] += row.get("receipt_sync", {}).get("linked_payments", 0)
        sheet["summary"] = dict(count)
        totals.update(count)
    return {"sheets": len(sheets), **totals, "removed": removed}


def reconcile(db, workbook, sheets, p, *, write=False, allow_create=True):
    """Plan or apply one version. Caller owns the transaction."""
    links = list(db.scalars(select(ContractWorkbookLink).where(ContractWorkbookLink.workbook_id == workbook.id)))
    linked_by_target = {(l.kind, l.target_id): l for l in links}
    index = match.Candidates(_entries(db), links)
    owners = defaultdict(list)
    for uid, name in db.execute(select(SysUser.id, SysUser.name).where(SysUser.status == 1, SysUser.is_deleted.is_(False))):
        owners[layout.norm(name)].append(uid)
    registered = _receipt_totals(db)
    project_codes = defaultdict(list)
    for code, name, customer in db.execute(select(BizProject.project_code, BizProject.name, BizProject.customer_name)
                                           .where(BizProject.is_deleted.is_(False))):
        if name and customer:
            project_codes[layout.key(name, customer)].append(code)

    candidates_by_row, proposals, by_target, seen = {}, {}, defaultdict(list), set()
    for sheet in sheets:
        sid = sheet["id"]
        configured = workbook.config.get("profiles") or {}
        profile = configured.get(sid)
        if not profile and (workbook.config["format"] == "xlsx" or not configured):
            profile = profile_for(sheet["name"])
        sheet["profile"] = profile
        sheet["warning"] = layout.project_sheet(sheet, profile)
        for row in sheet["rows"]:
            record = row.get("record")
            if not record:
                continue
            n = row["row"]
            row["issues"] = notes = list(record["issues"])
            proposed = _source_values(record, owners, notes)
            codes = project_codes.get(layout.key(record["name"], record["customer_name"]), [])
            if len(codes) == 1:
                proposed["project_code"] = codes[0]
            proposals[sid, n] = proposed
            old, ambiguous = index.previous(sid, record)
            if ambiguous:
                notes.append("原表行身份发生冲突，请核对后再关联")
                _build_target(row, "unresolved")
                continue
            target, method, alternatives = (index.by_id.get((old.kind, old.target_id)), "已绑定来源行", []) if old else (
                index.initial(profile, n, record))
            if old and (not target or target["is_deleted"]):
                notes.append("原关联的业务记录已停用，未自动重建")
                _build_target(row, "unresolved")
                continue
            if target:
                stored = linked_by_target.get((target["kind"], target["id"]))
                if stored and not old:
                    # The same contract can appear in different reports. A
                    # second row stays visible without overwriting the binding.
                    notes.append("该业务记录已经绑定另一源表行，请核对是否同一合同")
                    _build_target(row, "unresolved", candidates=[target])
                    continue
                candidates_by_row[sid, n] = target, method, old
                rank = {"已绑定来源行": 0, "编号": 1, "历史来源及业务字段": 2, "业务字段": 3, "日期、金额和名称": 4}
                by_target[target["kind"], target["id"]].append((rank[method], sid, n))
                if old:
                    seen.add(old.id)
            elif alternatives:
                notes.append("存在相似或历史关联记录，尚不能唯一确认")
                _build_target(row, "unresolved", candidates=alternatives)

    disappeared = [l for l in links if l.present and l.id not in seen and not any(
        s["id"] == l.sheet_id and s["warning"] for s in sheets)]
    # If a prior identity disappeared, an unnumbered row could be the same
    # contract with an edited name. Never create a second business record for it.
    changed_identity = {l.sheet_id for l in disappeared}
    previous = workbook.sheets
    previous_rows = {(s["id"], r["row"]): r for s in previous for r in s["rows"] if r.get("record")}
    held = {(s["id"], r["identity_hold"]) for s in previous for r in s["rows"] if r.get("identity_hold")}
    new_counts = Counter((r["record"]["kind"], r["record"]["exact"]) for s in sheets for r in s["rows"] if r.get("record"))
    created = updated = 0
    for sheet in sheets:
        sid = sheet["id"]
        for row in sheet["rows"]:
            rec = row.get("record")
            if not rec:
                continue
            notes = row["issues"]
            n, kind = row["row"], rec["kind"]
            proposed = proposals[sid, n]
            possible = candidates_by_row.get((sid, n))
            if possible and not row.get("state"):
                target, method, link = possible
                competing = sorted(by_target[kind, target["id"]])
                if len(competing) > 1 and (competing[0][0] == competing[1][0] or competing[0][1:] != (sid, n)):
                    notes.append("多条原表记录可能指向同一业务记录，未重复登记")
                    _build_target(row, "unresolved", candidates=[target])
                else:
                    row["match"] = method
                    changes = _amendments(target, proposed, link, notes,
                                          registered.get(target["id"], Decimal(0)))
                    row["changes"] = {f: {"from": target.get(f), "to": v} for f, v in changes.items()}
                    row["differences"] = {LABELS[f]: {"source": proposed[f], "ledger": target.get(f)}
                                          for f in ("name", "customer_name", "amount", "sign_date")
                                          if f in proposed and f not in changes and proposed[f] != target.get(f)}
                    _build_target(row, "update" if changes else "linked", target)
                    if "project_code" in changes:
                        row["target"]["project_code"] = changes["project_code"]
                    if write:
                        if changes:
                            obj = db.get(MODELS[kind], target["id"], with_for_update=True, populate_existing=True)
                            if not obj or obj.is_deleted:
                                raise bad_request("业务台账同时发生变更，请重试同步")
                            if any(match.comparable(getattr(obj, f)) != target.get(f) for f in changes):
                                raise bad_request("业务台账同时发生变更，请重试同步")
                            if kind == "contract":
                                contracts.update(db, p, obj, changes, "wecom-sync", commit=False, reprice_plans=False)
                            else:
                                # Missing fields do not reset ownership or stage.
                                opportunities.update(db, p, obj, changes, "wecom-sync")
                            updated += 1
                            row["state"] = "updated"
                        _store_link(db, workbook.id, sid, n, rec, proposed, target["id"], link, target, changes)
            elif not row.get("state"):
                has_id = any(mode == "external" for mode, _ in rec["keys"])
                previous = previous_rows.get((sid, n), {}).get("record")
                pending_identity = not has_id and (sid in changed_identity or (sid, rec["entity"]) in held)
                if (new_counts[kind, rec["exact"]] > 1 or pending_identity
                        or not allow_create or (previous and previous.get("kind") != kind)):
                    if pending_identity:
                        row["identity_hold"] = rec["entity"]
                    notes.append("来源身份变化或疑似重复，已保留原表等待确认")
                    _build_target(row, "unresolved")
                elif not _ready(rec, proposed):
                    notes.append("业务台账必填信息不完整，原表记录已同步")
                    _build_target(row, "unresolved")
                else:
                    _build_target(row, "create")
                    if write:
                        target = _create(db, p, workbook, rec, proposed)
                        _store_link(db, workbook.id, sid, n, rec, proposed, target["id"])
                        _build_target(row, "created", target)
                        created += 1
            if kind == "contract":
                if write and workbook.config.get("auto_receipts") and row.get("target"):
                    from app.services.contract_workbook_receipts import apply as sync_receipts

                    row["receipt_sync"] = sync_receipts(db, workbook, row, p)
                    if row["receipt_sync"].get("new_payments"):
                        registered = _receipt_totals(db)
                _set_receipts(row, registered)
            row["record"] = _public(rec)
    if write:
        for link in disappeared:
            link.present = False
    return _summary(sheets, sum(not l.present for l in links) if write else len(disappeared)), created, updated


def _ready(record, proposed):
    if not proposed.get("name"):
        return False
    if record["kind"] == "contract":
        return (all(proposed.get(f) for f in ("customer_name", "amount", "sign_date")) and
                Decimal(proposed["amount"]) > 0 and date.fromisoformat(proposed["sign_date"]) <= today())
    return len(record.get("customer_name") or "") <= 128


def _store_link(db, workbook_id, sid, row_number, record, proposed, target_id, current=None, target=None, changes=None):
    link = current or ContractWorkbookLink(workbook_id=workbook_id, kind=record["kind"], target_id=target_id)
    link.sheet_id, link.row_number, link.keys, link.present = sid, row_number, record["keys"], True
    # Keep the prior baseline for a rejected change. Otherwise a mismatch
    # could be treated as acknowledged the next time the source is polled.
    values = {k: v for k, v in proposed.items() if v is not None and (
        not current or (changes and k in changes) or (target and target.get(k) == v))}
    link.baseline = {**(link.baseline or {}), **values}
    if not current:
        db.add(link)


def _create(db, p, workbook, rec, proposed):
    data = {k: v for k, v in proposed.items() if k in (*FIELDS[rec["kind"]], "project_code")}
    data["business_line"] = rec["business_line"]
    data["remark"] = "来自企业微信固定台账，源表详情见「企业微信同步」。"
    if rec["kind"] == "contract":
        data["region"] = data.get("region") or region_rules.classify(rec["customer_name"], rec["name"])[0]
        obj = contracts.create(db, p, data, "wecom-sync", source_type=SOURCE_TYPE, commit=False)
        obj.source_approval_no = "wecom:" + workbook.source_key
        number = obj.contract_no
    else:
        data["customer_name"] = data.get("customer_name") or "待确认"
        if not data.get("amount") or Decimal(data["amount"]) == 0:
            data.pop("amount", None)
        obj = opportunities.create(db, p, data, "wecom-sync", source_type=SOURCE_TYPE)
        number = obj.opp_no
    db.flush()
    return {"kind": rec["kind"], "id": obj.id, "number": number, "project_code": obj.project_code}


def lock(db, key):
    if db.bind.dialect.name == "postgresql" and not db.scalar(
        sql("SELECT pg_try_advisory_xact_lock(:key)"), {"key": zlib.crc32(f"{JOB}:{key}".encode())}
    ):
        raise BizError(40901, "该来源正在同步，请稍后刷新")


def sync(db, key, *, force=False, preview=False, allow_create=True, expected_revision=None):
    lock(db, key)
    wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == key).options(defer(ContractWorkbook.sheets)))
    if not wb:
        raise not_found("固定来源")
    if not wb.config["enabled"] and not force:
        raise bad_request("该固定来源未启用")
    cfg = wb.config
    checked_at = utcnow()
    try:
        p = reader(db, cfg)
        revision, sheets = source.fetch_workbook(cfg, None if force or preview else wb.revision)
        if expected_revision is not None and revision != expected_revision:
            raise BizError(40901, "企微表在预览后已更新，请重新预览最新版本")
        if sheets is None:
            wb.checked_at, wb.last_error = checked_at, None
            db.commit()
            return {"changed": False, "revision": wb.revision, "summary": wb.summary}
        summary, created, updated = reconcile(db, wb, sheets, p, write=cfg["auto_apply"] and not preview,
                                             allow_create=allow_create)
        if preview:
            return {"revision": revision, "summary": summary, "sheets": sheets}
        wb.sheets, wb.revision, wb.summary = sheets, revision, summary
        wb.synced_at, wb.checked_at, wb.last_error = utcnow(), checked_at, None
        log_op(db, p.id, "contract.workbook.sync", "contract_workbook", key,
               after={"revision": revision, "sheets": summary["sheets"], "rows": summary.get("rows", 0),
                      "created": created, "updated": updated, "review": summary.get("review", 0)})
        db.commit()
        if created or updated or summary.get("new_payments") or summary.get("linked_payments"):
            contracts._invalidate()
        return {"changed": True, "revision": revision, "summary": summary}
    except Exception as exc:
        db.rollback()
        if not preview:
            message = exc.message if isinstance(exc, BizError) else "同步没有完成，请查看服务器日志"
            # A queued run may have published after rollback. Leave its newer
            # status alone instead of replacing it with an older failure.
            wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == key).with_for_update())
            if wb and (not wb.checked_at or wb.checked_at <= checked_at):
                wb.checked_at, wb.last_error = checked_at, message[:500]
                db.commit()
        raise


def review_link(db, p, key, *, sheet_id, row_number, number, revision, ip=None):
    """Link a reviewed source row without editing the underlying business record."""
    import copy

    lock(db, key)
    wb = for_view(db, p, key)
    if not describe(wb, p)["can_run"]:
        raise forbidden("仅绑定账号可核对业务对应关系")
    if wb.revision != revision:
        raise BizError(40901, "企微版本已更新，请刷新后重新核对")
    sheets = copy.deepcopy(wb.sheets)
    sheet = next((s for s in sheets if s["id"] == sheet_id), None)
    row = next((r for r in sheet["rows"] if r["row"] == row_number and r.get("record")), None) if sheet else None
    if not row or row.get("state") != "unresolved":
        raise bad_request("仅能核对尚未关联的业务行")
    kind = row["record"]["kind"]
    model = MODELS[kind]
    column = model.contract_no if kind == "contract" else model.opp_no
    target = db.scalar(select(model).where(func.upper(column) == number.strip().upper(), model.is_deleted.is_(False)))
    if not target:
        raise not_found("业务记录")
    if db.scalar(select(ContractWorkbookLink.id).where(ContractWorkbookLink.workbook_id == wb.id,
                                                       ContractWorkbookLink.kind == kind,
                                                       ContractWorkbookLink.target_id == target.id)):
        raise BizError(40901, "该业务记录已和另一原表行关联")
    clean = {**sheet, "rows": [{"row": r["row"], "cells": r["cells"]} for r in sheet["rows"]]}
    if layout.project_sheet(clean, clean["profile"]):
        raise bad_request("当前子表的映射发生变化，无法核对")
    item = next((r["record"] for r in clean["rows"] if r["row"] == row_number and r.get("record")), None)
    if not item or not item["keys"]:
        raise bad_request("该行没有唯一来源标识，请先在企微原表补充同步编号")
    baseline = {f: item[f] for f in FIELDS[kind] if f in item and item[f] is not None}
    _store_link(db, wb.id, sheet_id, row_number, item, baseline, target.id)
    _build_target(row, "linked", {"kind": kind, "id": target.id, "number": getattr(target, column.key),
                                  "project_code": target.project_code})
    row["issues"] = item["issues"].copy()
    row["differences"] = {LABELS[f]: {"source": baseline[f], "ledger": match.comparable(getattr(target, f))}
                          for f in ("name", "customer_name", "amount", "sign_date") if f in baseline and
                          baseline[f] != match.comparable(getattr(target, f))}
    if row["differences"]:
        row["issues"].append("原表字段与工作台不同，已保留工作台记录")
    row.pop("identity_hold", None)
    row.pop("candidates", None)
    row["match"] = "人工核对"
    if kind == "contract":
        _set_receipts(row, _receipt_totals(db))
    wb.summary, wb.sheets = _summary(sheets, wb.summary.get("removed", 0)), sheets
    log_op(db, p.id, "contract.workbook.link", kind, target.id,
           after={"source": key, "sheet_id": sheet_id, "row": row_number, "revision": revision}, ip=ip)
    db.commit()
    return {"linked": True}


def visible_sources(db, p):
    workbooks = db.scalars(select(ContractWorkbook).options(defer(ContractWorkbook.sheets))
                          .order_by(ContractWorkbook.source_key))
    return [describe(wb, p) for wb in workbooks if can_view(p, wb.config)]


def has_source(db, p):
    return any(can_view(p, config) for config in db.scalars(select(ContractWorkbook.config)))


def describe(wb, p):
    config = wb.config
    return {"key": wb.source_key, "title": config["title"], "url": config.get("url"),
            "enabled": config["enabled"], "auto_apply": config["auto_apply"], "poll_minutes": POLL_MINUTES,
            "auto_receipts": config.get("auto_receipts", False),
            "revision": wb.revision, "summary": wb.summary, "checked_at": wb.checked_at,
            "synced_at": wb.synced_at, "last_error": wb.last_error,
            "can_export": p.can("contracts:export"),
            "can_run": p.id == config["reader_id"] and p.can("contracts:write")}


def for_view(db, p, key):
    wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == key)
                   .options(defer(ContractWorkbook.sheets)))
    if not wb or not can_view(p, wb.config):
        raise not_found("固定来源")
    return wb


def _current_receipts(db, rows):
    ids = {row["target"]["id"] for row in rows if row.get("receipts") and row.get("target", {}).get("kind") == "contract"}
    if not ids:
        return rows
    registered = dict(db.execute(select(BizPayment.contract_id, func.sum(BizPayment.amount))
                                 .where(BizPayment.contract_id.in_(ids)).group_by(BizPayment.contract_id)).all())
    for row in rows:
        if row.get("target", {}).get("id") not in ids or not row.get("receipts"):
            continue
        row["issues"] = [s for s in row.get("issues", [])
                         if s not in {"源表累计回款与工作台到账记录不同", "源表未给出明确到账日，累计金额不作为新增到账凭证"}]
        _set_receipts(row, registered)
    return rows


def sheet_list_from(sheets):
    return [{"id": s["id"], "name": s["name"], "range": s["range"], "profile": s["profile"],
             "warning": s["warning"], "summary": s["summary"]} for s in sheets]


def sheet_list(wb):
    return sheet_list_from(wb.sheets)


def sheet_page(db, wb, sid, page, size, keyword="", state="", source_row=None):
    from openpyxl.utils.cell import get_column_letter, range_boundaries

    sheet = next((s for s in wb.sheets if s["id"] == sid), None)
    if not sheet:
        raise not_found("子表")
    rows = _current_receipts(db, sheet["rows"])
    if state == "business":
        rows = [r for r in rows if r.get("record")]
    elif state == "review":
        rows = [r for r in rows if r.get("issues")]
    elif state == "unresolved":
        rows = [r for r in rows if r.get("state") == "unresolved"]
    if keyword.strip():
        query = layout.norm(keyword)
        rows = [r for r in rows if query in layout.norm(r.get("target", {}).get("number")) or
                any(query in layout.norm(source.cell_value(c)) for c in r["cells"])]
    if source_row:
        index = next((i for i, row in enumerate(rows) if row["row"] == source_row), None)
        if index is not None:
            page = index // size + 1
    _, _, right, _ = range_boundaries(sheet["range"])
    return {"sheet_id": sid, "name": sheet["name"], "range": sheet["range"], "merges": sheet["merges"],
            "items": rows[(page - 1) * size:page * size], "total": len(rows), "page": page, "warning": sheet["warning"],
            "columns": [get_column_letter(i) for i in range(1, right + 1)]}


def linked_sources(db, p, kind, target_id):
    if p is None:
        return []
    configs = db.execute(select(ContractWorkbookLink, ContractWorkbook).join(
        ContractWorkbook, ContractWorkbook.id == ContractWorkbookLink.workbook_id)
        .where(ContractWorkbookLink.kind == kind, ContractWorkbookLink.target_id == target_id)
        .options(load_only(ContractWorkbook.id, ContractWorkbook.source_key, ContractWorkbook.config))).all()
    result = []
    for link, wb in configs:
        if not can_view(p, wb.config):
            continue
        sheet = next((s for s in wb.sheets if s["id"] == link.sheet_id), None)
        row = next((r for r in sheet["rows"] if r["row"] == link.row_number
                    and r.get("target", {}).get("id") == target_id), None) if sheet and link.present else None
        if row:
            _current_receipts(db, [row])
        result.append({"key": wb.source_key, "title": wb.config["title"], "sheet_id": link.sheet_id,
                       "sheet_name": sheet["name"] if sheet else link.sheet_id, "row": link.row_number,
                       "present": bool(row), "data": row})
    return result


def export_snapshot(wb):
    """Create a viewable XLSX of the current data, with calculated values as values."""
    import io

    from openpyxl import Workbook
    from openpyxl.utils.cell import get_column_letter

    book = Workbook()
    book.remove(book.active)
    for sheet in wb.sheets:
        title = re.sub(r"[\\/*?:\[\]]", " ", sheet["name"]).strip() or "子表"
        ws = book.create_sheet(title[:31])
        for row in sheet["rows"]:
            for col, raw in enumerate(row["cells"], 1):
                if not source.occupied(raw):
                    continue
                value = source.cell_value(raw)
                if value in (None, "") and isinstance(raw, dict):
                    value = raw.get("formula") or raw.get("error") or json.dumps(raw, ensure_ascii=False)
                if not isinstance(value, (str, int, float, bool, type(None))):
                    value = json.dumps(value, ensure_ascii=False)
                cell = ws.cell(row["row"], col, value)
                if isinstance(value, str):
                    cell.data_type = "s"
                links = layout._extract_links(raw)
                if len(links) == 1 and links[0].startswith(("https://doc.weixin.qq.com/", "https://drive.weixin.qq.com/")):
                    cell.hyperlink = links[0]
                    if not value:
                        cell.value = "查看企微文件"
        for merge in sheet["merges"]:
            ws.merge_cells(merge)
        for col in range(1, min(ws.max_column, 30) + 1):
            ws.column_dimensions[get_column_letter(col)].width = 18
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
