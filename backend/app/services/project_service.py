"""项目部：项目台账 + 标准控制流程节点引擎（PRD 4.2.2 / 7.6）。

- 项目读取范围由项目负责人和项目团队决定。
- 节点编辑权限：项目负责人(owner) 或 有 milestone:write 按钮者（且项目在其可见范围内）。
- 每次节点变更写 biz_milestone_log（操作人/时间/前后值/source web|mcp|system）。
- 完成节点自动 status=3，finish_date>plan_date 置 alert_flag（延期完成标红）；进行中/未开始节点过计划日 → 延期标红。
- update_milestone(...) 供 MCP proj.milestone_update 复用。
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import false, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.response import BizError, bad_request, conflict, forbidden, not_found
from app.core.timeutil import today, utcnow, week_no, week_range
from app.models import (MILESTONE_STATUS, PROJECT_STATUS, BizContract, BizMilestoneLog, BizPayment, BizProject,
                        BizProjectFeedback, BizProjectMilestone, KeyProjectMember, KeyProjectTaskPlan, SysUser)
from app.services import codes, dict_service

ZERO = Decimal("0")
MS_FIELDS = ["node_name", "seq", "owner_id", "duty", "plan_date", "finish_date", "status", "alert_flag", "progress_note"]
STATUS_ALIASES = {"未开始": 1, "进行中": 2, "已完成": 3, "完成": 3, "延期": 4, "todo": 1, "doing": 2, "done": 3,
                  "delayed": 4}


# ------------------------------------------------------------------ 数据范围 / 权限
def visible_filter(p):
    """项目负责人及项目团队成员才可读取项目，内部定时作业用 None。"""
    if p is None:
        return None
    if not p.workbench_enabled:
        return false()
    return or_(BizProject.owner_id == p.id, BizProject.id.in_(
        select(KeyProjectMember.project_id).where(KeyProjectMember.user_id == p.id)))


def team_user_ids(project_id):
    return select(KeyProjectMember.user_id).where(KeyProjectMember.project_id == project_id).union(
        select(BizProject.owner_id).where(BizProject.id == project_id, BizProject.owner_id.is_not(None)))


def visible_query(p):
    q = select(BizProject).where(BizProject.is_deleted.is_(False))
    cond = visible_filter(p)
    return q.where(cond) if cond is not None else q


def can_view(db: Session, p, project: BizProject) -> bool:
    return not project.is_deleted and (p is None or (p.workbench_enabled and
        (project.owner_id == p.id or db.get(KeyProjectMember, (project.id, p.id)) is not None)))


def can_edit(p, project: BizProject) -> bool:
    if p is None:
        return True  # 系统内部调用
    return p.is_admin or project.owner_id == p.id or p.can("milestone:write")


def get_project(db: Session, code_or_id, p=None, *, lock=False) -> BizProject:
    by_id = isinstance(code_or_id, int) or (isinstance(code_or_id, str) and code_or_id.isdigit())
    match = BizProject.id == int(code_or_id) if by_id else BizProject.project_code == code_or_id
    query = select(BizProject).where(match)
    if lock:
        query = query.with_for_update(of=BizProject).execution_options(populate_existing=True)
    proj = db.scalar(query)
    if not proj or proj.is_deleted:
        raise not_found("项目")
    if lock:
        db.expire(proj, ["milestones"])
    if p is not None and not can_view(db, p, proj):
        raise forbidden("仅项目团队成员可查看该项目")
    return proj


def ensure_edit(db: Session, p, proj: BizProject) -> None:
    if p is not None and not can_view(db, p, proj):
        raise forbidden("无权查看该项目")
    if not can_edit(p, proj):
        raise forbidden("仅项目负责人或具备节点维护权限者可修改流程节点")


# ------------------------------------------------------------------ 序列化
def _uname(u: SysUser | None) -> str | None:
    return u.name if u else None


def ms_delayed(m: BizProjectMilestone, T: date | None = None) -> bool:
    """当前是否处于延期（未完成且已过计划日 / 状态=延期）。"""
    T = T or today()
    if m.status == 4:
        return True
    return m.status in (1, 2) and m.plan_date is not None and m.plan_date < T


def ms_dict(m: BizProjectMilestone, T: date | None = None) -> dict:
    T = T or today()
    delayed = ms_delayed(m, T)
    return {"id": m.id, "project_id": m.project_id, "seq": m.seq, "node_name": m.node_name,
            "owner_id": m.owner_id, "owner_name": _uname(m.owner), "duty": m.duty,
            "plan_date": m.plan_date, "finish_date": m.finish_date, "status": m.status,
            "status_text": MILESTONE_STATUS.get(m.status), "alert_flag": bool(m.alert_flag),
            "is_delayed": delayed, "late_finished": m.status == 3 and bool(m.alert_flag),
            "alert": delayed or bool(m.alert_flag), "progress_note": m.progress_note,
            "days_left": (m.plan_date - T).days if m.plan_date and m.status != 3 else None}


def progress_stats(project: BizProject, T: date | None = None) -> dict:
    T = T or today()
    ms = list(project.milestones)
    done = sum(1 for m in ms if m.status == 3)
    delayed = sum(1 for m in ms if ms_delayed(m, T))
    late_done = sum(1 for m in ms if m.status == 3 and m.alert_flag)
    cur = next((m for m in sorted(ms, key=lambda x: x.seq) if m.status != 3), None)
    return {"total_nodes": len(ms), "done_nodes": done, "progress": round(done / len(ms), 4) if ms else 0,
            "delayed_nodes": delayed, "late_finished_nodes": late_done,
            "current_node": cur.node_name if cur else ("已全部完成" if ms else None),
            "current_node_status": cur.status if cur else None,
            "current_node_delayed": bool(cur and ms_delayed(cur, T)),
            "current_node_plan": cur.plan_date if cur else None}


def project_brief(db: Session, project: BizProject, T: date | None = None, money: dict | None = None) -> dict:
    T = T or today()
    mk = db.get(SysUser, project.market_owner_id) if project.market_owner_id else None
    d = {"id": project.id, "project_code": project.project_code, "name": project.name,
         "business_line": project.business_line, "customer_name": project.customer_name,
         "owner_id": project.owner_id, "owner_name": _uname(project.owner),
         "market_owner_id": project.market_owner_id, "market_owner_name": _uname(mk),
         "status": project.status, "status_text": PROJECT_STATUS.get(project.status), "is_key": project.is_key,
         "budget_amount": project.budget_amount, "contract_amount": project.contract_amount,
         "start_date": project.start_date, "plan_end_date": project.plan_end_date,
         "bid_deadline": project.bid_deadline, "milestone_template": project.milestone_template,
         "remark": project.remark, **progress_stats(project, T)}
    if money is not None:
        d.update(money)
    return d


# ------------------------------------------------------------------ 合同 / 回款
def money_summary(db: Session, codes_: list[str]) -> dict[str, dict]:
    """按项目汇总合同额（不含已终止）与已回款。"""
    if not codes_:
        return {}
    c_rows = db.execute(select(BizContract.project_code, func.coalesce(func.sum(BizContract.amount), 0),
                               func.count(BizContract.id))
                        .where(BizContract.project_code.in_(codes_), BizContract.is_deleted.is_(False),
                               BizContract.status != 3).group_by(BizContract.project_code)).all()
    p_rows = db.execute(select(BizContract.project_code, func.coalesce(func.sum(BizPayment.amount), 0))
                        .join(BizPayment.contract)
                        .where(BizContract.project_code.in_(codes_), BizContract.is_deleted.is_(False),
                               BizContract.status != 3).group_by(BizContract.project_code)).all()
    out = {c: {"contract_total": ZERO, "contract_count": 0, "received": ZERO} for c in codes_}
    for code, amt, n in c_rows:
        out[code].update(contract_total=Decimal(amt), contract_count=n)
    for code, amt in p_rows:
        out[code]["received"] = Decimal(amt)
    for v in out.values():
        v["receivable"] = v["contract_total"] - v["received"]
        v["payment_rate"] = (v["received"] / v["contract_total"]).quantize(Decimal("0.0001")) \
            if v["contract_total"] else None
    return out


def contract_detail(db: Session, code: str) -> dict:
    contracts = db.scalars(select(BizContract).where(BizContract.project_code == code, BizContract.is_deleted.is_(False))
                           .order_by(BizContract.sign_date)).all()
    items, payments = [], []
    for c in contracts:
        rec = sum((x.amount for x in c.payments), ZERO)
        items.append({"id": c.id, "contract_no": c.contract_no, "name": c.name, "amount": c.amount,
                      "sign_date": c.sign_date, "status": c.status, "received": rec,
                      "payment_rate": (rec / c.amount).quantize(Decimal("0.0001")) if c.amount else None,
                      "plans": [{"stage_name": pl.stage_name, "amount": pl.amount, "due_date": pl.due_date,
                                 "received_amount": pl.received_amount, "status": pl.status} for pl in c.plans]})
        payments += [{"contract_no": c.contract_no, "amount": x.amount, "payment_date": x.payment_date,
                      "source_type": x.source_type} for x in c.payments]
    payments.sort(key=lambda x: x["payment_date"], reverse=True)
    return {"contracts": items, "payments": payments[:50], **money_summary(db, [code])[code]}


def profit_of(db: Session, code: str, T: date | None = None, p=None) -> dict:
    from app.services.profit_service import project_profit, scoped_project_codes
    T = T or today()
    scope = None if p is None else set(db.scalars(visible_query(p).with_only_columns(BizProject.project_code)))
    codes = scoped_project_codes(db, code, scope)
    res = project_profit(db, date(T.year, 1, 1), T, project_codes=codes)
    row = res["rows"][0] if res["rows"] else None
    if not row:
        return {"income": ZERO, "cost": ZERO, "gross_profit": ZERO, "gross_margin": None, "cost_expense": ZERO,
                "cost_project": ZERO, "caliber": res["caliber"]}
    return {k: row[k] for k in ("income", "cost", "gross_profit", "gross_margin", "cost_expense", "cost_project")} | \
        {"caliber": res["caliber"], "group_codes": row.get("group_codes", []), "group_note": row.get("group_note")}


# ------------------------------------------------------------------ 列表 / 新建 / 详情
def list_projects(db: Session, p, *, status: int | None = None, business_line: str | None = None,
                  is_key: bool | None = None, owner_id: int | None = None, keyword: str | None = None,
                  mine: bool = False, scope: str = "all") -> list[dict]:
    q = visible_query(p)
    if scope == "pipeline":
        q = q.where(BizProject.status.in_((1, 2)))
    elif scope == "history":
        q = q.where(BizProject.status == 5)
    elif scope == "general":
        q = q.where(BizProject.status != 5, BizProject.is_key.is_(False))
    if status:
        q = q.where(BizProject.status == status)
    if business_line:
        q = q.where(BizProject.business_line == business_line)
    if is_key is not None:
        q = q.where(BizProject.is_key.is_(is_key))
    if owner_id:
        q = q.where(BizProject.owner_id == owner_id)
    if mine and p is not None:
        q = q.where(BizProject.owner_id == p.id)
    if keyword:
        kw = f"%{keyword}%"
        q = q.where(or_(BizProject.name.ilike(kw), BizProject.project_code.ilike(kw), BizProject.customer_name.ilike(kw)))
    rows = db.scalars(q.order_by(BizProject.is_key.desc(), BizProject.project_code)).unique().all()
    T = today()
    money = money_summary(db, [r.project_code for r in rows])
    items = [project_brief(db, r, T, money.get(r.project_code)) for r in rows]
    if scope == "pipeline" and items and p and p.has_menu("opportunities"):
        from app.models import BizOpportunity
        from app.services import opportunity_service as opp
        links = {}
        for opportunity in db.scalars(opp._filtered(p).where(BizOpportunity.project_code.in_([r.project_code for r in rows]))
                                     .order_by(BizOpportunity.id)).unique():
            links.setdefault(opportunity.project_code, []).append({"id": opportunity.id, "no": opportunity.opp_no, "name": opportunity.name})
        for item in items:
            item["opportunities"] = links.get(item["project_code"], [])
    return items


def templates(db: Session) -> dict:
    return dict_service.items(db, "milestone_template")


def build_nodes(tpl: list[dict], start: date | None, owner_id: int | None) -> list[BizProjectMilestone]:
    plan = start
    out = []
    for i, n in enumerate(tpl, start=1):
        if plan:
            plan = plan + timedelta(days=int(n.get("days") or 0))
        out.append(BizProjectMilestone(seq=i, node_name=n["node"], duty=n.get("duty"), plan_date=plan,
                                       owner_id=owner_id, status=1))
    return out


def ensure_nodes(db: Session, project: BizProject, start: date | None = None) -> list[BizProjectMilestone]:
    """给没有流程节点的项目补标准节点：合同导入建档的项目默认没有节点，卡片会显示 0/0。

    已验收 / 运维 / 结项的项目按全部完成补齐；在建项目从今天（或之后的开工日）开始排期，
    不用历史开工日反推，避免一上线就整片标红延期。
    """
    if list(project.milestones):
        return []
    key = project.milestone_template or ("software" if project.business_line == "KP" else "standard")
    tpl = dict_service.get(db, "milestone_template", key)
    if not tpl:
        return []
    finished = project.status in (3, 4, 5)
    if finished:
        base = None
    else:
        base = max(start or today(), today())
    nodes = build_nodes(tpl, base, project.owner_id)
    if finished:
        end = project.plan_end_date or project.start_date or today()
        span = sum(int(n.get("days") or 0) for n in tpl) or len(tpl)
        cursor = end - timedelta(days=span)
        for raw, node in zip(tpl, nodes):
            cursor = cursor + timedelta(days=int(raw.get("days") or 0))
            node.plan_date = cursor
            node.finish_date = cursor
            node.status = 3
    project.milestones = nodes
    if nodes and nodes[-1].plan_date and not project.plan_end_date:
        project.plan_end_date = nodes[-1].plan_date
    db.flush()
    return nodes


def create_project(db: Session, data: dict, operator_id: int | None, source: str = "web") -> BizProject:
    line = data.get("business_line")
    if line not in ("XY", "AF", "KP", "YW"):
        raise bad_request("业务线无效（XY/AF/KP/YW）")
    if not data.get("name") or not data.get("customer_name"):
        raise bad_request("项目名称与客户必填")
    tpl_key = data.get("milestone_template") or ("software" if line == "KP" else "standard")
    tpl = dict_service.get(db, "milestone_template", tpl_key)
    if tpl is None and tpl_key != "none":
        raise bad_request(f"流程模板 {tpl_key} 不存在")
    start = data.get("start_date")
    if isinstance(start, str) and start:
        start = date.fromisoformat(start)
    p = BizProject(project_code=codes.project_code(db, line), name=data["name"], business_line=line,
                   customer_name=data["customer_name"], owner_id=data.get("owner_id"),
                   market_owner_id=data.get("market_owner_id"), status=int(data.get("status") or 2),
                   is_key=bool(data.get("is_key")), budget_amount=data.get("budget_amount"),
                   bid_deadline=data.get("bid_deadline"), start_date=start or None, milestone_template=tpl_key, remark=data.get("remark"))
    nodes = build_nodes(tpl or [], start or today(), p.owner_id)
    p.milestones = nodes
    if nodes and nodes[-1].plan_date:
        p.plan_end_date = data.get("plan_end_date") or nodes[-1].plan_date
    db.add(p)
    db.flush()
    for m in nodes:
        _log(db, p.id, m.id, "create", None, _snap(m), operator_id, source)
    return p


def project_detail(db: Session, proj: BizProject, p=None) -> dict:
    T = today()
    fb = db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.project_id == proj.id)
                    .order_by(BizProjectFeedback.feedback_date.desc(), BizProjectFeedback.id.desc()).limit(50)).all()
    money = contract_detail(db, proj.project_code)
    prof = profit_of(db, proj.project_code, T, p)
    d = project_brief(db, proj, T)
    d.update({
        "milestones": [ms_dict(m, T) for m in sorted(proj.milestones, key=lambda x: x.seq)],
        "money": money, "profit": prof,
        "feedbacks": [feedback_dict(f) for f in fb],
        "logs": milestone_logs(db, proj, limit=100),
        "can_edit": can_edit(p, proj), "can_key": bool(p and p.can("project:key")),
    })
    return d


def feedback_dict(f: BizProjectFeedback) -> dict:
    return {"id": f.id, "project_id": f.project_id, "feedback_date": f.feedback_date, "kind": f.kind,
            "content": f.content, "risk_level": f.risk_level, "reporter_id": f.reporter_id,
            "reporter_name": _uname(f.reporter), "created_at": f.created_at}


PROJECT_EDITABLE = ["name", "customer_name", "owner_id", "market_owner_id", "status", "budget_amount",
                    "start_date", "plan_end_date", "remark", "business_line"]


def update_project(db: Session, proj: BizProject, data: dict, p=None) -> tuple[dict, dict]:
    before = {k: getattr(proj, k) for k in PROJECT_EDITABLE}
    for k in PROJECT_EDITABLE:
        if k in data and data[k] is not None:
            v = data[k]
            if k in ("start_date", "plan_end_date") and isinstance(v, str):
                v = date.fromisoformat(v) if v else None
            if k == "status" and int(v) not in PROJECT_STATUS:
                raise bad_request("项目状态无效")
            setattr(proj, k, v)
    after = {k: getattr(proj, k) for k in PROJECT_EDITABLE}
    db.flush()
    if before["status"] != proj.status and proj.status in (3, 5):
        from app.services import market_service
        market_service.generate_review(db, proj, author_id=p.id if p else None)
    return before, after


# ------------------------------------------------------------------ 节点引擎
def _snap(m: BizProjectMilestone) -> dict:
    from app.core.response import encode
    return encode({f: getattr(m, f) for f in MS_FIELDS})


def _log(db: Session, project_id: int, ms_id: int | None, action: str, before, after, operator_id, source):
    db.add(BizMilestoneLog(project_id=project_id, milestone_id=ms_id, action=action, before=before, after=after,
                           operator_id=operator_id, source=source, created_at=utcnow()))


def apply_status_rules(m: BizProjectMilestone, T: date | None = None) -> None:
    """完成→status=3 且 finish_date>plan_date 置 alert_flag；未完成过计划日→延期 status=4 + alert_flag。"""
    T = T or today()
    if m.finish_date and m.status != 3:
        m.status = 3
    if m.status == 3:
        m.finish_date = m.finish_date or T
        m.alert_flag = bool(m.plan_date and m.finish_date > m.plan_date)
        return
    m.finish_date = None
    if m.plan_date and m.plan_date < T:
        m.status, m.alert_flag = 4, True
    else:
        if m.status == 4:  # 计划日调整到未来 → 恢复进行中
            m.status = 2
        m.alert_flag = False


def parse_status(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
        v = int(v)
        if v not in MILESTONE_STATUS:
            raise bad_request("节点状态无效（1 未开始 2 进行中 3 已完成 4 延期）")
        return v
    s = STATUS_ALIASES.get(str(v).strip())
    if s is None:
        raise bad_request(f"无法识别的节点状态：{v}")
    return s


def _parse_date(v):
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _resequence(proj: BizProject) -> None:
    for i, m in enumerate(sorted(proj.milestones, key=lambda x: (x.seq, x.id or 0)), start=1):
        m.seq = i


def add_milestone(db: Session, proj: BizProject, data: dict, operator_id, source="web") -> BizProjectMilestone:
    if not (data.get("node_name") or "").strip():
        raise bad_request("节点名称必填")
    ms = sorted(proj.milestones, key=lambda x: x.seq)
    pos = data.get("seq")
    pos = len(ms) + 1 if not pos or int(pos) > len(ms) else max(1, int(pos))
    for m in ms:
        if m.seq >= pos:
            m.seq += 1
    owner_id = data.get("owner_id") or proj.owner_id
    _require_team_owner(db, proj, owner_id)
    m = BizProjectMilestone(node_name=data["node_name"].strip(), seq=pos, owner_id=owner_id,
                            duty=data.get("duty"), plan_date=_parse_date(data.get("plan_date")),
                            finish_date=_parse_date(data.get("finish_date")),
                            status=parse_status(data.get("status")) or 1, progress_note=data.get("progress_note"))
    apply_status_rules(m)
    proj.milestones.append(m)
    db.flush()
    _log(db, proj.id, m.id, "create", None, _snap(m), operator_id, source)
    return m


def _ensure_workflow_node(db, proj, m):
    if not proj.is_key and db.get(KeyProjectTaskPlan, m.id):
        raise conflict("请在「任务情况」调整安排、提交进展或删除任务")


def _require_team_owner(db, proj, owner_id):
    if owner_id is not None and owner_id != proj.owner_id and not db.get(KeyProjectMember, (proj.id, owner_id)):
        raise bad_request("责任人须为项目团队成员，请先在项目团队中添加")


def edit_milestone(db: Session, proj: BizProject, m: BizProjectMilestone, data: dict, operator_id,
                   source="web", *, from_task=False) -> BizProjectMilestone:
    if m.project_id != proj.id:
        raise not_found("节点")
    if not from_task:
        _ensure_workflow_node(db, proj, m)
    before = _snap(m)
    if "owner_id" in data and data["owner_id"] != m.owner_id:
        _require_team_owner(db, proj, data["owner_id"])
    if "node_name" in data and data["node_name"] is not None:
        if not str(data["node_name"]).strip():
            raise bad_request("节点名称不能为空")
        m.node_name = str(data["node_name"]).strip()
    for k in ("owner_id", "duty", "progress_note"):
        if k in data:
            setattr(m, k, data[k])
    if "plan_date" in data:
        m.plan_date = _parse_date(data["plan_date"])
    st = parse_status(data.get("status")) if "status" in data else None
    if "finish_date" in data:
        m.finish_date = _parse_date(data["finish_date"])
    if st is not None:
        m.status = st
        if st != 3 and "finish_date" not in data:
            m.finish_date = None
    apply_status_rules(m)
    after = _snap(m)
    if before != after:
        action = "complete" if m.status == 3 and before["status"] != 3 else "update"
        _log(db, proj.id, m.id, action, before, after, operator_id, source)
    db.flush()
    return m


def delete_milestone(db: Session, proj: BizProject, m: BizProjectMilestone, operator_id, source="web") -> None:
    if m.project_id != proj.id:
        raise not_found("节点")
    _ensure_workflow_node(db, proj, m)
    before = _snap(m)
    proj.milestones.remove(m)
    db.flush()
    _resequence(proj)
    _log(db, proj.id, m.id, "delete", before, None, operator_id, source)
    db.flush()


def reorder_milestones(db: Session, proj: BizProject, ids: list[int], operator_id, source="web") -> None:
    cur = {m.id: m for m in proj.milestones}
    if sorted(ids) != sorted(cur):
        raise bad_request("排序列表必须包含该项目全部节点")
    before = [{"id": m.id, "node_name": m.node_name, "seq": m.seq} for m in sorted(cur.values(), key=lambda x: x.seq)]
    for i, mid in enumerate(ids, start=1):
        cur[mid].seq = i
    after = [{"id": mid, "node_name": cur[mid].node_name, "seq": i} for i, mid in enumerate(ids, start=1)]
    if before != after:
        _log(db, proj.id, None, "reorder", {"order": before}, {"order": after}, operator_id, source)
    db.flush()


def move_milestone(db: Session, proj: BizProject, m: BizProjectMilestone, direction: str, operator_id, source="web"):
    ms = sorted(proj.milestones, key=lambda x: x.seq)
    i = ms.index(m)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(ms):
        return
    ms[i], ms[j] = ms[j], ms[i]
    reorder_milestones(db, proj, [x.id for x in ms], operator_id, source)


def milestone_logs(db: Session, proj: BizProject, limit: int = 200) -> list[dict]:
    rows = db.execute(select(BizMilestoneLog, SysUser.name).outerjoin(SysUser, SysUser.id == BizMilestoneLog.operator_id)
                      .where(BizMilestoneLog.project_id == proj.id)
                      .order_by(BizMilestoneLog.created_at.desc(), BizMilestoneLog.id.desc()).limit(limit)).all()
    out = []
    for lg, name in rows:
        snap = lg.after or lg.before or {}
        changes = []
        if lg.action in ("update", "complete") and lg.before and lg.after:
            changes = [{"field": k, "before": lg.before.get(k), "after": lg.after.get(k)}
                       for k in MS_FIELDS if lg.before.get(k) != lg.after.get(k)]
        out.append({"id": lg.id, "milestone_id": lg.milestone_id, "action": lg.action,
                    "node_name": snap.get("node_name") if isinstance(snap, dict) else None,
                    "before": lg.before, "after": lg.after, "changes": changes, "operator_id": lg.operator_id,
                    "operator_name": name or ("系统" if lg.source == "system" else None), "source": lg.source,
                    "created_at": lg.created_at})
    return out


def find_project(db: Session, key: str) -> BizProject:
    """按编码 / 名称（模糊）定位项目，供 MCP 对话式调用。"""
    key = (key or "").strip()
    if not key:
        raise bad_request("请提供项目编码或名称")
    proj = db.scalar(select(BizProject).where(BizProject.project_code == key, BizProject.is_deleted.is_(False)))
    if proj:
        return proj
    cands = db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False),
                                                or_(BizProject.name.ilike(f"%{key}%"),
                                                    BizProject.customer_name.ilike(f"%{key}%")))).unique().all()
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise not_found(f"项目「{key}」")
    raise BizError(40001, f"「{key}」匹配到多个项目，请指定项目编码",
                   [{"project_code": c.project_code, "name": c.name} for c in cands])


def find_node(proj: BizProject, node_name: str) -> BizProjectMilestone:
    ms = sorted(proj.milestones, key=lambda x: x.seq)
    exact = [m for m in ms if m.node_name == node_name]
    if exact:
        return exact[0]
    fuzzy = [m for m in ms if node_name in m.node_name or m.node_name in node_name]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if not fuzzy:
        raise not_found(f"节点「{node_name}」")
    raise BizError(40001, f"节点「{node_name}」不唯一", [m.node_name for m in fuzzy])


def update_milestone(db: Session, project_code: str, node_name: str | None = None, action: str = "query",
                     status=None, finish_date=None, note: str | None = None, operator=None,
                     source: str = "mcp") -> dict:
    """MCP proj.milestone_update 复用入口。

    action=query：返回项目节点列表（node_name 可选，仅返回该节点）。
    action=update：更新节点状态/完成日/进展说明，写留痕；operator 为 Principal（None 视为系统）。
    返回 {project, milestones|milestone, message}；调用方负责 db.commit()。
    """
    proj = find_project(db, project_code)
    if operator is not None and not can_view(db, operator, proj):
        raise forbidden("无权查看该项目（仅可查看本人负责的项目）")
    T = today()
    head = {"project_code": proj.project_code, "name": proj.name, "owner_name": _uname(proj.owner),
            **progress_stats(proj, T)}
    if action == "query":
        ms = sorted(proj.milestones, key=lambda x: x.seq)
        if node_name:
            ms = [find_node(proj, node_name)]
        return {"project": head, "milestones": [ms_dict(m, T) for m in ms],
                "message": f"{proj.name}：{head['done_nodes']}/{head['total_nodes']} 节点已完成，"
                           f"当前节点 {head['current_node']}，延期 {head['delayed_nodes']} 个"}
    if action != "update":
        raise bad_request("action 仅支持 query / update")
    if not node_name:
        raise bad_request("更新节点需提供 node_name")
    proj = get_project(db, proj.id, operator, lock=True)
    if operator is not None and not can_edit(operator, proj):
        raise forbidden("仅项目负责人或具备节点维护权限者可修改流程节点")
    m = find_node(proj, node_name)
    data: dict = {}
    if status is not None and status != "":
        data["status"] = status
    if finish_date:
        data["finish_date"] = finish_date
    if note is not None:
        data["progress_note"] = note
    if not data:
        raise bad_request("请提供 status / finish_date / note 中至少一项")
    edit_milestone(db, proj, m, data, operator.id if operator is not None else None, source)
    head.update(progress_stats(proj, T))
    md = ms_dict(m, T)
    msg = f"已更新 {proj.name}「{m.node_name}」为{md['status_text']}"
    if m.status == 3:
        msg += f"（完成日 {m.finish_date}{'，较计划延期' if m.alert_flag else ''}）"
    return {"project": head, "milestone": md, "message": msg}


# ------------------------------------------------------------------ 周进度
def weekly_progress(db: Session, projects: list[BizProject], week: str | None = None) -> dict:
    ws, we = week_range(week) if week else week_range(ref=today())
    nws, nwe = ws + timedelta(days=7), we + timedelta(days=7)
    T = today()
    rows = []
    for proj in projects:
        ms = sorted(proj.milestones, key=lambda x: x.seq)
        rows.append({**project_brief(db, proj, T),
                     "done": [ms_dict(m, T) for m in ms if m.finish_date and ws <= m.finish_date <= we],
                     "doing": [ms_dict(m, T) for m in ms if m.status == 2 and not ms_delayed(m, T)],
                     "delayed": [ms_dict(m, T) for m in ms if ms_delayed(m, T)],
                     "next": [ms_dict(m, T) for m in ms if m.status != 3 and m.plan_date and nws <= m.plan_date <= nwe]})
    return {"week": week_no(ws), "start": ws, "end": we, "projects": rows}


def save_weekly_xlsx(db, content, filename, data, *, actor_id=None, trigger="manual"):
    from app.core.storage import save_bytes
    from app.models import RptReport

    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:period))"), {"period": "project_weekly:" + data["week"]})
    version = (db.scalar(select(func.max(RptReport.version)).where(RptReport.report_type == "project_weekly",
        RptReport.period == data["week"]).execution_options(include_removed_records=True)) or 0) + 1
    file = save_bytes(db, data=content, filename=filename, category="project_team_report",
                      key=f"report/project_weekly/{data['end']:%Y-%m}/{filename}", uploader_id=actor_id)
    db.add(RptReport(report_type="project_weekly", period=data["week"], title=f"项目周进度汇总 {data['week']}", version=version,
        metrics={"project_codes": [row["project_code"] for row in data["projects"]], "projects": len(data["projects"]),
                 "delayed_nodes": sum(len(row["delayed"]) for row in data["projects"]),
                 "done_nodes": sum(len(row["done"]) for row in data["projects"])},
        file_ids=[file.id], created_by=actor_id, trigger=trigger))
    return file


def export_weekly_xlsx(db: Session, projects: list[BizProject], week: str | None = None,
                       title: str | None = None) -> tuple[bytes, str, dict]:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    data = weekly_progress(db, projects, week)
    wb = Workbook()
    ws = wb.active
    ws.title = "项目周进度"
    head_fill = PatternFill("solid", fgColor="1B3275")
    red = Font(color="A63D32", bold=True)
    ws.append([title or f"项目周进度（{data['week']}：{data['start']} ~ {data['end']}）"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    hdr = ["项目编码", "项目名称", "项目经理", "状态", "进度", "当前节点", "本周完成", "进行中", "延期节点", "下周计划"]
    ws.append(hdr)
    for c in ws[3]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), head_fill
    fmt = lambda xs, key="plan_date": "；".join(f"{x['node_name']}({x['owner_name'] or '-'} {x[key] or ''})" for x in xs) or "—"  # noqa: E731
    for r in data["projects"]:
        ws.append([r["project_code"], r["name"], r["owner_name"] or "—", r["status_text"],
                   f"{r['done_nodes']}/{r['total_nodes']}", r["current_node"] or "—",
                   fmt(r["done"], "finish_date"), fmt(r["doing"]),
                   "；".join(f"{x['node_name']}(计划 {x['plan_date']}，{x['progress_note'] or '未说明原因'})"
                            for x in r["delayed"]) or "—", fmt(r["next"])])
        if r["delayed"]:
            ws.cell(ws.max_row, 9).font = red
    for col, w in zip("ABCDEFGHIJ", [16, 30, 10, 8, 8, 12, 34, 30, 40, 30]):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=4):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    # 节点明细
    d = wb.create_sheet("节点明细")
    d.append(["项目编码", "项目名称", "序号", "节点", "责任人", "岗位职责", "计划日", "完成日", "状态", "延期", "进展说明"])
    for c in d[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), head_fill
    T = today()
    for proj in projects:
        for m in sorted(proj.milestones, key=lambda x: x.seq):
            md = ms_dict(m, T)
            d.append([proj.project_code, proj.name, m.seq, m.node_name, md["owner_name"], m.duty, m.plan_date,
                      m.finish_date, md["status_text"], "是" if md["alert"] else "", m.progress_note])
            if md["alert"]:
                d.cell(d.max_row, 10).font = red
    # 口径说明
    k = wb.create_sheet("口径说明")
    for line in [("统计周期", f"自然周 {data['start']} ~ {data['end']}（{data['week']}）"),
                 ("本周完成", "完成日落在本周内的节点"),
                 ("进行中", "状态=进行中且未超过计划日的节点"),
                 ("延期", "状态=延期，或未完成且计划日早于今天的节点（自动标红）"),
                 ("下周计划", "计划日落在下周、尚未完成的节点"),
                 ("进度", "已完成节点数 / 节点总数"),
                 ("数据来源", "biz_project / biz_project_milestone；生成时间 " + str(today()))]:
        k.append(list(line))
    k.column_dimensions["A"].width, k.column_dimensions["B"].width = 14, 70
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"项目周进度_{data['week']}.xlsx", data


def weekly_text(data: dict) -> str:
    lines = [f"**项目周进度 {data['week']}**（{data['start']} ~ {data['end']}）"]
    for r in data["projects"]:
        lines.append(f"\n> {r['name']}（{r['project_code']}）进度 {r['done_nodes']}/{r['total_nodes']}，当前：{r['current_node']}")
        if r["done"]:
            lines.append("本周完成：" + "、".join(x["node_name"] for x in r["done"]))
        if r["delayed"]:
            lines.append('<font color="warning">延期：' + "、".join(
                f"{x['node_name']}(计划 {x['plan_date']})" for x in r["delayed"]) + "</font>")
        if r["next"]:
            lines.append("下周计划：" + "、".join(x["node_name"] for x in r["next"]))
    return "\n".join(lines)
