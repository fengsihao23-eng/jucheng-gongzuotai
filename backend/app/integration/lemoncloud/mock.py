"""柠檬云 Mock：基于库内业务数据（出纳日记账 = 已核对流水 + 现金账）生成与业务一致的凭证与科目余额。

凭证规则（一笔日记账 = 一张两行凭证，凭证号按月 记-001 递增，按 日期+日记账ID 排序）：
- 回款          借 1002 银行存款        贷 1122 应收账款（辅助核算：项目）
- 报销（有项目）借 5403 合同履约成本（项目） 贷 1002；报销（公共）借 5602 管理费用 贷 1002
- 采购（项目成本）借 5403 合同履约成本（项目） 贷 1002
- 公户支出：工资/社保 借 2211 应付职工薪酬；税费 借 2221 应交税费；其余 借 5602 管理费用；贷 1002
- 其他收入：结息/利息 贷 5603 财务费用；其余 贷 5301 营业外收入；借 1002
- 现金账：支出 借 5602 贷 1001；提取备用金 借 1001 贷 1221 其他应收款-备用金
科目余额：期初（sys_dict journal_opening 合计，贷方对应 3001 实收资本）+ 启用日起至期末的凭证发生额。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import month_range
from app.integration.lemoncloud.base import (SUBJECTS, CashJournalDto, DateRange, ProjectAuxDto, SubjectBalanceDto,
                                             VoucherDto)
from app.models import BizExpenseReport, FinCashJournal

D = Decimal
PUBLIC_MAP = [(("工资", "薪酬", "社保", "社会保险", "公积金"), "2211"), (("税",), "2221")]


def counter_subject(j: FinCashJournal, expense_project: dict[str, str | None]) -> tuple[str, str | None]:
    """日记账对应的对方科目与辅助核算项目。"""
    s = j.summary or ""
    if j.account_type == "cash":
        if j.direction == 1:
            return "1221", None
        return "5602", None
    if j.direction == 1:
        if j.category == "回款":
            return "1122", j.project_code
        if any(k in s for k in ("结息", "利息")):
            return "5603", None
        return "5301", None
    if j.category == "报销":
        pc = j.project_code if j.project_code else expense_project.get(j.biz_ref or "")
        return ("5403", pc) if pc else ("5602", None)
    if j.category == "采购":
        return "5403", j.project_code or j.biz_ref
    if j.category == "公户支出":
        for kws, code in PUBLIC_MAP:
            if any(k in s for k in kws):
                return code, None
    return "5602", None


class MockLemonCloud:
    def __init__(self, db: Session):
        self.db = db

    # ---- 凭证 ----
    def _journals(self, start: date, end: date) -> list[FinCashJournal]:
        return self.db.scalars(select(FinCashJournal).where(FinCashJournal.journal_date.between(start, end))
                               .order_by(FinCashJournal.journal_date, FinCashJournal.id)).all()

    def _month_vouchers(self, period: str) -> list[VoucherDto]:
        start, end = month_range(period)
        js = self._journals(start, end)
        refs = [j.biz_ref for j in js if j.category == "报销" and j.biz_ref]
        exp_proj = dict(self.db.execute(select(BizExpenseReport.approval_no, BizExpenseReport.project_code)
                                        .where(BizExpenseReport.approval_no.in_(refs))).all()) if refs else {}
        out: list[VoucherDto] = []
        for i, j in enumerate(js, start=1):
            no = f"记-{i:03d}"
            money = "1002" if j.account_type == "bank" else "1001"
            other, aux = counter_subject(j, exp_proj)
            summary = (j.summary or j.category or "")[:120]
            money_line = VoucherDto(no, 1, j.journal_date, summary, money, SUBJECTS[money])
            other_line = VoucherDto(no, 2, j.journal_date, summary, other, SUBJECTS[other], aux_project=aux)
            if j.direction == 1:
                money_line.debit, other_line.credit = j.amount, j.amount
                out += [money_line, other_line]
            else:
                other_line.debit, money_line.credit = j.amount, j.amount
                other_line.line_no, money_line.line_no = 1, 2
                out += [other_line, money_line]
        return out

    def fetch_vouchers(self, date_range: DateRange) -> list[VoucherDto]:
        out = []
        y, m = date_range.start.year, date_range.start.month
        while date(y, m, 1) <= date_range.end:
            out += [v for v in self._month_vouchers(f"{y}-{m:02d}")
                    if date_range.start <= v.voucher_date <= date_range.end]
            y, m = (y + (m == 12), m % 12 + 1)
        return out

    # ---- 科目余额 ----
    def _opening(self) -> tuple[date | None, dict[str, D]]:
        from app.services import dict_service
        ops = dict_service.items(self.db, "journal_opening")
        bal: dict[str, D] = defaultdict(D)
        first = None
        for acct, v in ops.items():
            amt = D(str(v.get("amount", 0)))
            bal["1001" if acct == "cash" else "1002"] += amt
            bal["3001"] -= amt
            d = date.fromisoformat(v["date"]) if v.get("date") else None
            first = d if first is None or (d and d < first) else first
        return first, bal

    def fetch_subject_balances(self, period: str) -> list[SubjectBalanceDto]:
        start, end = month_range(period)
        first, net = self._opening()
        first_j = self.db.scalar(select(FinCashJournal.journal_date).order_by(FinCashJournal.journal_date).limit(1))
        begin = min(x for x in (first, first_j, start) if x)
        moves: dict[str, list[D]] = defaultdict(lambda: [D(0), D(0)])
        if begin < start:
            for v in self.fetch_vouchers(DateRange(begin, start - timedelta(days=1))):
                net[v.subject_code] += v.debit - v.credit
        for v in self.fetch_vouchers(DateRange(start, end)):
            moves[v.subject_code][0] += v.debit
            moves[v.subject_code][1] += v.credit
        out = []
        for code in sorted(set(net) | set(moves)):
            op = net.get(code, D(0))
            dr, cr = moves[code] if code in moves else (D(0), D(0))
            close = op + dr - cr
            if not op and not dr and not cr:
                continue
            out.append(SubjectBalanceDto(period, code, SUBJECTS.get(code, code),
                                         opening_debit=op if op > 0 else D(0), opening_credit=-op if op < 0 else D(0),
                                         debit_amount=dr, credit_amount=cr,
                                         debit_balance=close if close > 0 else D(0),
                                         credit_balance=-close if close < 0 else D(0)))
        return out

    # ---- 日记账 / 辅助核算 ----
    def fetch_cash_journal(self, date_range: DateRange) -> list[CashJournalDto]:
        return [CashJournalDto(j.journal_date, "现金" if j.account_type == "cash" else j.bank_account or "银行",
                               j.direction, j.amount, j.summary) for j in self._journals(date_range.start, date_range.end)]

    def fetch_project_aux(self, project_code: str) -> ProjectAuxDto:
        first = self.db.scalar(select(FinCashJournal.journal_date).order_by(FinCashJournal.journal_date).limit(1))
        last = self.db.scalar(select(FinCashJournal.journal_date).order_by(FinCashJournal.journal_date.desc()).limit(1))
        res = ProjectAuxDto(project_code)
        if not first:
            return res
        subj: dict[str, list[D]] = defaultdict(lambda: [D(0), D(0)])
        for v in self.fetch_vouchers(DateRange(first, last)):
            if v.aux_project != project_code:
                continue
            subj[v.subject_code][0] += v.debit
            subj[v.subject_code][1] += v.credit
            if v.subject_code == "5403":
                res.cost += v.debit - v.credit
            if v.subject_code in ("1122", "5001"):
                res.income += v.credit - v.debit
        res.subjects = [{"subject_code": k, "subject_name": SUBJECTS.get(k, k), "debit": a, "credit": b}
                        for k, (a, b) in sorted(subj.items())]
        return res
