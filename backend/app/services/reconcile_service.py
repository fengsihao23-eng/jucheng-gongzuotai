"""流水核对（FR-09-03）：智能匹配建议 + 人工确认入账 + 撤销 + 批量自动核对高置信建议。

匹配规则（口径可在 sys_dict(reconcile_rule, default) 调整关键字/阈值）：
- 收入 → 合同回款：金额精确等于某回款计划阶段剩余额 / 合同未回款余额（55）+ 对方户名包含/相似客户名（35/20）
  + 摘要含项目名/合同号（10）；仅名称一致但金额小于余额 → 部分回款（15）
- 收入 → 其他收入：摘要/对方含 结息/利息 等关键字
- 支出 → 报销单：金额精确（50）+ 摘要/对方含申请人姓名（30）+ 日期距审批通过 ±3 天（15）+ 摘要含审批单号（15）
- 支出 → 项目采购成本：已登记未付款的采购成本金额精确（50）+ 供应商（30）+ 日期（15）；
  或摘要含项目名/编码 → 新登记项目成本（60）
- 支出 → 公户支出：工资/社保/税费/房租/手续费 等关键字（90）
确认后：回款走 payment_service.register_payment(source_type=1)；其余写 fin_cash_journal(source_type=1)。
"""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.response import BizError, bad_request, conflict, not_found
from app.core.timeutil import utcnow
from app.models import (BizContract, BizExpenseReport, BizPayment, BizProject, BizProjectCost, FinBankFlow,
                        FinCashJournal, SysUser)
from app.services import dict_service, payment_service

D = Decimal
AUTO_THRESHOLD = 90
NEW_COST_MARK = "流水核对登记"
KIND_STATUS = {"expense": 1, "payment": 2, "public": 3, "project_cost": 4, "other_income": 5}
KIND_NAME = {"expense": "报销付款", "payment": "合同回款", "public": "公户支出", "project_cost": "项目成本",
             "other_income": "其他收入"}
DEFAULT_RULE = {
    "auto_threshold": AUTO_THRESHOLD,
    "auto_margin": 10,
    "date_window_days": 3,
    "other_income_keywords": ["结息", "利息", "退税", "补贴", "返还"],
    "public_rules": [
        {"keywords": ["代发工资", "工资", "薪酬"], "label": "工资"},
        {"keywords": ["社会保险", "社保", "公积金"], "label": "社保公积金"},
        {"keywords": ["税务局", "增值税", "所得税", "税费", "附加"], "label": "税费"},
        {"keywords": ["物业", "租金", "房租"], "label": "租金物业"},
        {"keywords": ["手续费", "服务费", "年费", "电费", "水费", "电信", "宽带"], "label": "费用"},
    ],
}


def rule(db: Session) -> dict:
    return {**DEFAULT_RULE, **(dict_service.get(db, "reconcile_rule", "default") or {})}


def name_score(a: str | None, b: str | None) -> float:
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    strip = lambda s: re.sub(r"(有限公司|股份|有限责任公司|集团|广东省?|广州市?|深圳市?|\(.*?\)|（.*?）)", "", s)  # noqa: E731
    a2, b2 = strip(a), strip(b)
    if a2 and b2 and (a2 in b2 or b2 in a2):
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


class MatchContext:
    """一次批量建议计算复用的候选集（避免逐条查询）。"""

    def __init__(self, db: Session):
        self.rule = rule(db)
        self.contracts = db.scalars(select(BizContract).where(BizContract.is_deleted.is_(False))
                                    .options(selectinload(BizContract.plans), selectinload(BizContract.payments))).all()
        self.projects = {p.project_code: p for p in db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False)))}
        self.reports = db.scalars(select(BizExpenseReport).where(BizExpenseReport.status.in_((2, 4)),
                                                                 BizExpenseReport.pay_status == 0,
                                                                 BizExpenseReport.bank_flow_id.is_(None))).all()
        self.users = {u.id: u for u in db.scalars(select(SysUser))}
        self.costs = db.scalars(select(BizProjectCost).where(BizProjectCost.bank_flow_id.is_(None))).all()


def _contract_balance(c: BizContract) -> D:
    return c.amount - sum((p.amount for p in c.payments), D(0))


def _income(flow: FinBankFlow, ctx: MatchContext) -> list[dict]:
    out = []
    amt, cp, sm = flow.amount, flow.counterparty or "", flow.summary or ""
    for c in ctx.contracts:
        bal = _contract_balance(c)
        if bal <= 0:
            continue
        score, reasons = 0, []
        hit_plan = None
        for pl in c.plans:
            due = pl.amount - pl.received_amount
            if due > 0 and due == amt:
                hit_plan = pl
                break
        if hit_plan:
            score += 55
            reasons.append(f"金额 {amt:,.2f} 等于「{hit_plan.stage_name}」剩余应收")
        elif bal == amt:
            score += 55
            reasons.append(f"金额 {amt:,.2f} 等于合同未回款余额")
        stage_full = None if (hit_plan or bal == amt) else next((pl for pl in c.plans if pl.amount == amt), None)
        if stage_full:
            score += 20
            reasons.append(f"金额等于「{stage_full.stage_name}」阶段应收额")
        partial = amt < bal and not hit_plan and bal != amt
        ns = name_score(c.customer_name, cp)
        if ns >= 0.999:
            score += 35
            reasons.append(f"对方户名「{cp}」包含客户名称")
        elif ns >= 0.6:
            score += 20
            reasons.append(f"对方户名与客户「{c.customer_name}」相似度 {ns:.0%}")
        p = ctx.projects.get(c.project_code or "")
        if (p and p.name and p.name in sm) or c.contract_no in sm:
            score += 10
            reasons.append("摘要含项目名称/合同号")
        if partial and ns >= 0.6 and score < 55:
            score += 15
            reasons.append(f"金额小于合同余额 {bal:,.2f}，可作部分回款")
        if amt > bal:
            continue
        if score >= 40:
            out.append({"kind": "payment", "target_id": c.id, "target_no": c.contract_no,
                        "title": f"{c.customer_name} · {c.contract_no}", "amount": amt, "score": min(score, 100),
                        "reasons": reasons,
                        "extra": {"contract_amount": c.amount, "balance": bal, "project_code": c.project_code,
                                  "project_name": p.name if p else None,
                                  "plan_stage": hit_plan.stage_name if hit_plan else None}})
    kw = next((k for k in ctx.rule["other_income_keywords"] if k in sm or k in cp), None)
    if kw:
        out.append({"kind": "other_income", "target_id": None, "target_no": None, "title": f"其他收入（{kw}）",
                    "amount": amt, "score": 95, "reasons": [f"摘要/对方含关键字「{kw}」"], "extra": {}})
    return out


def _expense(flow: FinBankFlow, ctx: MatchContext) -> list[dict]:
    out = []
    amt, cp, sm = flow.amount, flow.counterparty or "", flow.summary or ""
    win = int(ctx.rule["date_window_days"])
    for r in ctx.reports:
        if r.amount != amt:
            continue
        u = ctx.users.get(r.applicant_id)
        score, reasons = 50, [f"金额 {amt:,.2f} 与报销单一致"]
        if u and (u.name in sm or u.name in cp):
            score += 30
            reasons.append(f"摘要/对方含申请人「{u.name}」")
        if r.approval_no in sm:
            score += 15
            reasons.append("摘要含审批单号")
        ref = (r.approved_at.date() if r.approved_at else r.expense_date)
        diff = (flow.trade_date - ref).days
        if -1 <= diff <= win:
            score += 15
            reasons.append(f"付款日距审批通过 {diff} 天")
        elif abs(diff) <= 10:
            score += 5
            reasons.append(f"付款日距审批通过 {diff} 天（超出 ±{win} 天）")
        else:
            score -= 10
        out.append({"kind": "expense", "target_id": r.id, "target_no": r.approval_no,
                    "title": f"报销 · {u.name if u else '-'} · {r.category}", "amount": amt, "score": min(score, 100),
                    "reasons": reasons, "_diff": abs(diff),
                    "extra": {"applicant": u.name if u else None, "category": r.category,
                              "project_code": r.project_code, "approved_at": r.approved_at, "reason": r.reason}})
    for c in ctx.costs:
        if c.amount != amt:
            continue
        score, reasons = 50, [f"金额与已登记项目成本一致（{c.project_code}）"]
        if c.summary and cp and (cp in c.summary or name_score(cp, c.summary) >= 0.6):
            score += 30
            reasons.append(f"对方「{cp}」与成本供应商一致")
        if abs((flow.trade_date - c.cost_date).days) <= win:
            score += 15
            reasons.append("日期相差 ≤ 3 天")
        p = ctx.projects.get(c.project_code)
        out.append({"kind": "project_cost", "target_id": c.id, "target_no": c.project_code,
                    "title": f"项目成本 · {p.name if p else c.project_code}", "amount": amt, "score": min(score, 100),
                    "reasons": reasons, "_diff": 0, "extra": {"project_code": c.project_code, "cost_type": c.cost_type}})
    for code, p in ctx.projects.items():
        if code in sm or (p.name and p.name in sm):
            reasons = [f"摘要含项目「{p.name}」"]
            score = 60
            if re.search(r"(公司|厂|中心|研究院)$", cp) and not any(u.name == cp for u in ctx.users.values()):
                score += 15
                reasons.append("对方为供应商单位")
            if any(k in sm for k in ("采购", "设备", "材料", "分包", "货款")):
                score += 10
                reasons.append("摘要含采购类关键字")
            out.append({"kind": "project_cost", "target_id": None, "target_no": code,
                        "title": f"新登记项目成本 · {p.name}", "amount": amt, "score": min(score, 89),
                        "reasons": reasons, "_diff": 99, "extra": {"project_code": code, "cost_type": "采购"}})
    for rl in ctx.rule["public_rules"]:
        kw = next((k for k in rl["keywords"] if k in sm or k in cp), None)
        if kw:
            out.append({"kind": "public", "target_id": None, "target_no": None, "title": f"公户支出 · {rl['label']}",
                        "amount": amt, "score": 90, "reasons": [f"摘要/对方含关键字「{kw}」"], "_diff": 0,
                        "extra": {"label": rl["label"]}})
            break
    return out


def suggest(db: Session, flow: FinBankFlow, ctx: MatchContext | None = None, limit: int = 5) -> list[dict]:
    ctx = ctx or MatchContext(db)
    cands = _income(flow, ctx) if flow.direction == 1 else _expense(flow, ctx)
    cands.sort(key=lambda s: (-s["score"], s.get("_diff", 0), s["target_id"] or 0))
    for s in cands:
        s.pop("_diff", None)
        s["kind_name"] = KIND_NAME[s["kind"]]
        s["confidence"] = "高" if s["score"] >= ctx.rule["auto_threshold"] else "中" if s["score"] >= 70 else "低"
    return cands[:limit]


# ---------------- 确认 / 撤销 ----------------
def _journal(db: Session, flow: FinBankFlow, category: str, user_id: int | None, *, project_code=None, biz_ref=None,
             summary=None) -> FinCashJournal:
    if db.scalar(select(FinCashJournal.id).where(FinCashJournal.bank_flow_id == flow.id)):
        raise conflict("该流水已生成日记账")
    j = FinCashJournal(journal_date=flow.trade_date, account_type="bank", bank_account=flow.bank_account,
                       direction=flow.direction, amount=flow.amount, summary=(summary or flow.summary or category)[:255],
                       category=category, project_code=project_code, biz_ref=biz_ref, bank_flow_id=flow.id,
                       register_by=user_id, source_type=1)
    db.add(j)
    return j


def confirm(db: Session, flow: FinBankFlow, kind: str, user_id: int | None, *, target_id: int | None = None,
            project_code: str | None = None, cost_type: str | None = None, remark: str | None = None) -> dict:
    if flow.match_status != 0:
        raise conflict(f"该流水已核对（{KIND_NAME.get(next((k for k, v in KIND_STATUS.items() if v == flow.match_status), ''), '已处理')}），如需修改请先撤销")
    if kind not in KIND_STATUS:
        raise bad_request(f"未知核对类型 {kind}")
    if kind in ("payment", "other_income") and flow.direction != 1:
        raise bad_request("支出流水不能登记为回款/其他收入")
    if kind in ("expense", "public", "project_cost") and flow.direction != 2:
        raise bad_request("收入流水不能登记为报销/公户支出/项目成本")
    ref = None
    if kind == "payment":
        c = db.get(BizContract, target_id or 0)
        if not c or c.is_deleted:
            raise not_found("合同")
        if flow.amount > _contract_balance(c):
            raise conflict(f"到账金额超过合同未回款余额 {_contract_balance(c):,.2f}")
        pay = payment_service.register_payment(db, c, flow.amount, flow.trade_date, flow=flow, source_type=1,
                                               user_id=user_id, remark=remark)
        ref = {"payment_id": pay.id, "contract_no": c.contract_no}
    elif kind == "expense":
        r = db.get(BizExpenseReport, target_id or 0)
        if not r:
            raise not_found("报销单")
        if r.pay_status == 1 or r.bank_flow_id:
            raise conflict("该报销单已关联付款流水")
        if r.status not in (2, 4):
            raise conflict("报销单未审批通过")
        if r.amount != flow.amount:
            raise conflict(f"金额不一致：报销 {r.amount:,.2f} / 流水 {flow.amount:,.2f}")
        r.pay_status, r.bank_flow_id = 1, flow.id
        flow.biz_ref = r.approval_no
        _journal(db, flow, "报销", user_id, project_code=r.project_code, biz_ref=r.approval_no)
        ref = {"approval_no": r.approval_no}
    elif kind == "project_cost":
        if target_id:
            cost = db.get(BizProjectCost, target_id)
            if not cost:
                raise not_found("项目成本")
            if cost.bank_flow_id:
                raise conflict("该成本已关联流水")
            if cost.amount != flow.amount:
                raise conflict("金额不一致")
        else:
            if not project_code or not db.scalar(select(BizProject.id).where(BizProject.project_code == project_code)):
                raise bad_request("请选择项目")
            cost = BizProjectCost(project_code=project_code, cost_date=flow.trade_date, cost_type=cost_type or "采购",
                                  amount=flow.amount, created_by=user_id,
                                  summary=f"{NEW_COST_MARK}：{flow.counterparty or ''} {remark or flow.summary or ''}".strip()[:255])
            db.add(cost)
            db.flush()
        cost.bank_flow_id = flow.id
        flow.biz_ref = cost.project_code
        _journal(db, flow, "采购", user_id, project_code=cost.project_code, biz_ref=cost.project_code)
        ref = {"project_cost_id": cost.id, "project_code": cost.project_code}
    elif kind == "public":
        _journal(db, flow, "公户支出", user_id, summary=f"{flow.summary or ''}{'（' + remark + '）' if remark else ''}")
    elif kind == "other_income":
        _journal(db, flow, "其他收入", user_id, summary=f"{flow.summary or ''}{'（' + remark + '）' if remark else ''}")
    flow.match_status = KIND_STATUS[kind]
    flow.matched_by, flow.matched_at = user_id, utcnow()
    db.flush()
    return {"flow_id": flow.id, "kind": kind, "match_status": flow.match_status, **(ref or {})}


def unmatch(db: Session, flow: FinBankFlow) -> dict:
    st = flow.match_status
    if st == 0:
        raise conflict("该流水尚未核对")
    undone = {"flow_id": flow.id, "previous_status": st}
    if st == 2:
        pay = db.scalar(select(BizPayment).where(BizPayment.bank_flow_id == flow.id))
        if pay:
            from app.models import FinReceipt
            db.execute(select(BizContract.id).where(BizContract.id == pay.contract_id).with_for_update())
            if db.scalar(select(FinReceipt.id).where(FinReceipt.payment_id == pay.id)):
                raise conflict("该流水的回款已被到账台账引用，请先解除到账关联")
            c = pay.contract
            left = pay.amount
            for pl in reversed(c.plans):
                take = min(pl.received_amount, left)
                pl.received_amount -= take
                left -= take
                if left <= 0:
                    break
            db.delete(pay)
            db.flush()
            db.refresh(c)
            payment_service.refresh_plan_status(c)
            undone["payment_removed"] = pay.id
    elif st == 1:
        for r in db.scalars(select(BizExpenseReport).where(BizExpenseReport.bank_flow_id == flow.id)):
            r.pay_status, r.bank_flow_id = 0, None
            undone["approval_no"] = r.approval_no
    elif st == 4:
        for c in db.scalars(select(BizProjectCost).where(BizProjectCost.bank_flow_id == flow.id)):
            if (c.summary or "").startswith(NEW_COST_MARK):
                db.delete(c)
                undone["project_cost_removed"] = c.id
            else:
                c.bank_flow_id = None
    for j in db.scalars(select(FinCashJournal).where(FinCashJournal.bank_flow_id == flow.id)):
        undone["journal_removed"] = j.id
        db.delete(j)
    flow.match_status, flow.biz_ref, flow.matched_by, flow.matched_at = 0, None, None, None
    db.flush()
    return undone


def auto_match(db: Session, user_id: int | None, *, flow_ids: list[int] | None = None, threshold: int | None = None,
               dry_run: bool = False) -> dict:
    """批量确认高置信建议：最高分 ≥ 阈值，且领先第二名 ≥ auto_margin（避免歧义）。逐条确认后同批次内候选即时更新。"""
    q = select(FinBankFlow).where(FinBankFlow.match_status == 0)
    if flow_ids:
        q = q.where(FinBankFlow.id.in_(flow_ids))
    flows = db.scalars(q.order_by(FinBankFlow.trade_date, FinBankFlow.id)).all()
    ctx = MatchContext(db)
    th = threshold or int(ctx.rule["auto_threshold"])
    margin = int(ctx.rule["auto_margin"])
    done, skipped = [], []
    for fl in flows:
        s = suggest(db, fl, ctx)
        if not s or s[0]["score"] < th:
            skipped.append({"flow_id": fl.id, "reason": "无高置信建议"})
            continue
        if len(s) > 1 and s[0]["score"] - s[1]["score"] < margin and s[1]["kind"] == s[0]["kind"]:
            skipped.append({"flow_id": fl.id, "reason": "存在多个相近候选，需人工确认"})
            continue
        top = s[0]
        item = {"flow_id": fl.id, "amount": fl.amount, "kind": top["kind"], "title": top["title"], "score": top["score"]}
        if not dry_run:
            try:
                with db.begin_nested():
                    confirm(db, fl, top["kind"], user_id, target_id=top["target_id"],
                            project_code=(top.get("extra") or {}).get("project_code"))
            except BizError as e:
                skipped.append({"flow_id": fl.id, "reason": e.message})
                continue
            # 已被占用的候选移出上下文
            if top["kind"] == "expense":
                ctx.reports = [r for r in ctx.reports if r.id != top["target_id"]]
            elif top["kind"] == "project_cost" and top["target_id"]:
                ctx.costs = [c for c in ctx.costs if c.id != top["target_id"]]
            elif top["kind"] == "payment":
                for c in ctx.contracts:
                    if c.id == top["target_id"]:
                        db.refresh(c)
        done.append(item)
    return {"matched": len(done), "skipped": len(skipped), "threshold": th, "items": done, "skipped_items": skipped,
            "dry_run": dry_run}


def candidates(db: Session, flow: FinBankFlow, kind: str, keyword: str = "") -> list[dict]:
    """人工核对时的候选搜索（无建议或建议不准时）。"""
    kw = keyword.strip()
    if kind == "payment":
        q = select(BizContract).where(BizContract.is_deleted.is_(False)).options(selectinload(BizContract.payments))
        if kw:
            q = q.where((BizContract.customer_name.contains(kw)) | (BizContract.contract_no.contains(kw))
                        | (BizContract.project_code.contains(kw)))
        rows = []
        for c in db.scalars(q.order_by(BizContract.sign_date.desc()).limit(50)):
            bal = _contract_balance(c)
            if bal > 0:
                rows.append({"id": c.id, "label": f"{c.contract_no} · {c.customer_name}", "amount": c.amount,
                             "balance": bal, "project_code": c.project_code})
        return rows
    if kind == "expense":
        q = select(BizExpenseReport).where(BizExpenseReport.status.in_((2, 4)), BizExpenseReport.pay_status == 0)
        if kw:
            q = q.join(SysUser, SysUser.id == BizExpenseReport.applicant_id, isouter=True).where(
                (SysUser.name.contains(kw)) | (BizExpenseReport.approval_no.contains(kw)))
        return [{"id": r.id, "label": f"{r.approval_no} · {r.applicant.name if r.applicant else '-'} · {r.category}",
                 "amount": r.amount, "date": r.expense_date, "project_code": r.project_code}
                for r in db.scalars(q.order_by(BizExpenseReport.expense_date.desc()).limit(50))]
    if kind == "project_cost":
        q = select(BizProjectCost).where(BizProjectCost.bank_flow_id.is_(None))
        if kw:
            q = q.where(BizProjectCost.project_code.contains(kw) | BizProjectCost.summary.contains(kw))
        return [{"id": c.id, "label": f"{c.project_code} · {c.cost_type} · {c.summary or ''}", "amount": c.amount,
                 "date": c.cost_date, "project_code": c.project_code}
                for c in db.scalars(q.order_by(BizProjectCost.cost_date.desc()).limit(50))]
    return []


def flow_row(f: FinBankFlow) -> dict:
    from app.models.finance import FLOW_MATCH_STATUS
    return {"id": f.id, "bank_account": f.bank_account, "trade_date": f.trade_date, "trade_time": f.trade_time,
            "direction": f.direction, "amount": f.amount, "counterparty": f.counterparty,
            "counterparty_account": f.counterparty_account, "summary": f.summary, "balance": f.balance,
            "match_status": f.match_status, "match_status_name": FLOW_MATCH_STATUS.get(f.match_status),
            "biz_ref": f.biz_ref, "source_file": f.source_file, "source_row": f.source_row, "import_id": f.import_id,
            "matched_at": f.matched_at}
