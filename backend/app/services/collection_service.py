"""Read existing contract balances; store only the separate collection conversation."""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.audit import log_op
from app.core.deps import build_principal
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import TZ, month_range, now, today, week_range
from app.models import BizCollectionEntry, BizCollectionFollowup, BizContract, BizPayment, SysUser
from app.services import feedback_attachments, notify_service

ZERO = Decimal("0")


def scoped(p):
    q = select(BizContract).where(BizContract.is_deleted.is_(False), BizContract.status.in_((1, 2)))
    if p.data_scope == "self":
        q = q.where(BizContract.owner_id == p.id)
    elif p.data_scope == "dept":
        q = q.where(BizContract.owner_id.in_(select(SysUser.id).where(
            SysUser.department_id == p.user.department_id))) if p.user.department_id else q.where(BizContract.owner_id == p.id)
    return q


def _split(contract, paid):
    """Use the payment ledger once, allocate in the same sequence as payment_service."""
    remaining_face = max(ZERO, contract.amount)
    remaining_paid = max(ZERO, paid)
    for plan in contract.plans:
        amount = min(remaining_face, max(ZERO, plan.amount))
        received = min(remaining_paid, amount)
        yield plan.id, plan.stage_name, plan.due_date, amount, received
        remaining_face -= amount
        remaining_paid -= received
    if remaining_face:
        yield 0, "未设计划余额", None, remaining_face, min(remaining_face, remaining_paid)


def _payments(db, ids):
    return dict(db.execute(select(BizPayment.contract_id, func.sum(BizPayment.amount)).where(
        BizPayment.contract_id.in_(ids), BizPayment.payment_date <= today()).group_by(BizPayment.contract_id)).all()) if ids else {}


def monthly_receipts(db, p, page, size):
    day = today()
    query = scoped(p).with_only_columns(BizContract.id).order_by(None)
    criteria = (BizPayment.contract_id.in_(query), BizPayment.payment_date.between(month_range(ref=day)[0], day))
    count, amount = db.execute(select(func.count(), func.sum(BizPayment.amount)).select_from(BizPayment).where(*criteria)).one()
    rows = db.execute(select(BizPayment, BizContract).join(BizContract, BizPayment.contract_id == BizContract.id)
                      .where(*criteria).order_by(BizPayment.payment_date.desc(), BizPayment.id.desc())
                      .offset((page - 1) * size).limit(size))
    return {"period": day.strftime("%Y-%m"), "count": count, "amount": amount or ZERO,
            "items": [{"id": payment.id, "date": payment.payment_date, "amount": payment.amount,
                       "contract_name": contract.name, "contract_no": contract.contract_no, "customer": contract.customer_name}
                      for payment, contract in rows]}


def _follow_fields(entry):
    wstart = week_range()[0]
    return {"expected_on": entry.expected_on, "reply_to_id": entry.reply_to_id, "last_note": entry.last_note,
            "requested_at": entry.requested_at, "replied_at": entry.replied_at,
            "pending_reply": bool(entry.requested_at and not entry.replied_at),
            "replied_this_week": bool(entry.replied_at and entry.replied_at.astimezone(TZ).date() >= wstart)} if entry else {
                "expected_on": None, "reply_to_id": None, "last_note": "", "requested_at": None, "replied_at": None,
                "pending_reply": False, "replied_this_week": False}


def board(db, p, *, year, basis, bucket, keyword, owner_id, state, page, size):
    q = scoped(p)
    if keyword:
        from sqlalchemy import or_
        q = q.where(or_(BizContract.contract_no.icontains(keyword, autoescape=True),
                        BizContract.name.icontains(keyword, autoescape=True),
                        BizContract.customer_name.icontains(keyword, autoescape=True)))
    contracts = db.scalars(q.options(selectinload(BizContract.plans))).all()
    ids = [c.id for c in contracts]
    paid = _payments(db, ids)
    followups = {(r.contract_id, r.plan_key): r for r in db.scalars(select(BizCollectionFollowup).where(
        BizCollectionFollowup.contract_id.in_(ids)))} if ids else {}
    owner_ids = {c.owner_id for c in contracts if c.owner_id} | {r.reply_to_id for r in followups.values() if r.reply_to_id}
    names = dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_(owner_ids))).all()) if owner_ids else {}
    totals = {key: {"key": key, "amount": ZERO, "count": 0} for key in ("all", "before", "current", "future", "undated")}
    rows = []
    for c in contracts:
        actual = paid.get(c.id, ZERO)
        for key, label, due, amount, received in _split(c, actual):
            entry = followups.get((c.id, key))
            responsible = (entry.reply_to_id if entry else None) or c.owner_id
            if owner_id is not None and responsible != owner_id:
                continue
            outstanding = amount - received
            stamp = due if basis == "due" else c.sign_date
            group = "undated" if stamp is None else "before" if stamp.year < year else "current" if stamp.year == year else "future"
            if outstanding:
                for group_key in ("all", group):
                    totals[group_key]["amount"] += outstanding
                    totals[group_key]["count"] += 1
            f = _follow_fields(entry)
            if (state == "open" and not outstanding) or (state == "settled" and outstanding) or (
                state == "pending" and (not outstanding or not f["pending_reply"])) or (
                state == "unreported" and (not outstanding or f["replied_this_week"])):
                continue
            if bucket and bucket != "all" and group != bucket:
                continue
            rows.append({"contract_id": c.id, "contract_no": c.contract_no, "contract_name": c.name,
                         "customer": c.customer_name, "plan_key": key, "label": label, "due_on": due, "sign_on": c.sign_date,
                         "amount": amount, "received": received, "outstanding": outstanding, "bucket": group,
                         "owner_id": responsible, "owner_name": names.get(responsible),
                         "overdue": bool(outstanding and due and due < today()), **f})
    rows.sort(key=lambda r: (not r["overdue"], r["due_on"] or date.max, r["contract_id"], r["plan_key"]))
    return {"items": rows[(page - 1) * size:page * size], "total": len(rows), "totals": list(totals.values()),
            "owners": [{"id": uid, "name": name} for uid, name in sorted(names.items(), key=lambda kv: (kv[1], kv[0]))],
            "year": year, "basis": basis, "can_request": can_request(p),
            "note": "按有效合同金额与截至今日已登记的实际回款计算；回款按分期顺序抵扣，未设计划的余额单列。预计日期和催办回复不会改变财务到账。金额合计不受明细状态筛选影响。"}


def can_request(p):
    return p.is_admin or p.can("collections:request") or p.can("contracts:write") or p.can("receivables:correct")


def _item(db, p, contract_id, plan_key, *, lock=False):
    q = scoped(p).where(BizContract.id == contract_id).options(selectinload(BizContract.plans))
    c = db.scalar(q.with_for_update(of=BizContract) if lock else q)
    if not c:
        raise not_found("应收合同")
    row = db.scalar(select(BizCollectionFollowup).where(BizCollectionFollowup.contract_id == c.id,
                                                       BizCollectionFollowup.plan_key == plan_key))
    actual = _payments(db, [c.id]).get(c.id, ZERO)
    plan = next((x for x in _split(c, actual) if x[0] == plan_key), None)
    if plan is None and row is None:
        raise not_found("应收明细")
    return c, row, plan


def read_entry(db, p, entry):
    followup = db.get(BizCollectionFollowup, entry.followup_id)
    if not followup or not any(p.has_menu(menu) for menu in ("contracts", "receivables")):
        raise not_found("回款跟进")
    _item(db, p, followup.contract_id, followup.plan_key)


def _eligible(db, c, u):
    if not u or u.status != 1 or u.is_deleted:
        return False
    recipient = build_principal(u)
    if not (recipient.has_menu("contracts") or recipient.has_menu("receivables")):
        return False
    return bool(db.scalar(scoped(recipient).with_only_columns(BizContract.id).where(BizContract.id == c.id)))


def detail(db, p, contract_id, plan_key):
    c, row, plan = _item(db, p, contract_id, plan_key)
    entries = []
    if row:
        author = SysUser.__table__.alias("author")
        recipient = SysUser.__table__.alias("recipient")
        q = select(BizCollectionEntry, author.c.name, recipient.c.name).join(author, author.c.id == BizCollectionEntry.author_id)
        q = q.outerjoin(recipient, recipient.c.id == BizCollectionEntry.recipient_id).where(
            BizCollectionEntry.followup_id == row.id).order_by(BizCollectionEntry.id.desc()).limit(150)
        rows = db.execute(q).all()
        attachments = feedback_attachments.by_record(db, BizCollectionEntry, (item.id for item, _, _ in rows))
        entries = [{"id": item.id, "author": by, "recipient": to, "kind": item.kind, "content": item.content,
                    "at": item.created_at, "expected_on": item.expected_on, "attachments": attachments[item.id]}
                   for item, by, to in rows]
    owner = db.get(SysUser, c.owner_id) if c.owner_id else None
    current = db.get(SysUser, row.reply_to_id) if row and row.reply_to_id else None
    candidates = []
    if can_request(p):
        candidates = [{"id": u.id, "name": u.name, "department": u.dept_code} for u in
                      db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False)).order_by(SysUser.name))
                      if _eligible(db, c, u)]
    return {"contract_id": c.id, "contract_no": c.contract_no, "customer": c.customer_name,
            "label": plan[1] if plan else row.label, "active": bool(plan and plan[3] > plan[4]),
            "contract_owner": owner.name if owner else None, "reply_to_name": current.name if current else None,
            "entries": entries, "candidates": candidates, **_follow_fields(row),
            "can_request": can_request(p), "can_note": can_request(p) or c.owner_id == p.id, "can_reply": bool(row and row.reply_to_id == p.id and row.requested_at and not row.replied_at)}


def record(db, p, contract_id, plan_key, body, *, uploads=None):
    c, row, plan = _item(db, p, contract_id, plan_key, lock=True)
    if body.kind == "request" and not can_request(p):
        raise forbidden("只有获授权的业务或财务人员可以催办")
    if body.kind != "reply" and not (can_request(p) or c.owner_id == p.id):
        raise forbidden("只有合同负责人或获授权人员可以登记跟进")
    if body.kind == "request" and (not plan or plan[3] == plan[4]):
        raise conflict("该分期已无待回款，请核对实际到账")
    if body.kind == "reply" and (not row or row.reply_to_id != p.id or not row.requested_at or row.replied_at):
        raise conflict("没有分配给你的待回复催办")
    recipient = db.get(SysUser, body.recipient_id) if body.recipient_id else None
    if body.kind == "request" and not _eligible(db, c, recipient):
        raise bad_request("请选择已能查看该合同的在职人员")
    if row is None:
        row = BizCollectionFollowup(contract_id=c.id, plan_key=plan_key, label=plan[1])
        db.add(row)
        db.flush()
    stamp = now()
    row.last_note = body.content
    if body.expected_on is not None:
        row.expected_on = body.expected_on
    if body.kind == "request":
        row.reply_to_id, row.requested_at, row.replied_at = recipient.id, stamp, None
    elif body.kind == "reply":
        row.replied_at = stamp
    entry = BizCollectionEntry(followup_id=row.id, author_id=p.id, kind=body.kind, content=body.content,
                               expected_on=body.expected_on, recipient_id=recipient.id if body.kind == "request" else None,
                               created_at=stamp)
    db.add(entry)
    db.flush()
    if uploads:
        uploads.save(db, p, entry)
    if recipient and body.kind == "request":
        notify_service.send(db, [recipient], "collection_request", "有一笔回款需要反馈", f"{c.contract_no} · {row.label}：{body.content}",
                            link=f"/collections?contract={c.id}&plan={plan_key}", ref_type="collection_entry", ref_id=str(entry.id), wecom=False)
    log_op(db, p.id, f"collections.{body.kind}", "biz_collection_followup", row.id,
           after={"contract_id": c.id, "plan_key": plan_key, "recipient": row.reply_to_id})
    return detail(db, p, contract_id, plan_key)
