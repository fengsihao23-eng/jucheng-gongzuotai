"""生成财务运营样例文件（基于当前库内数据，保证可与库内待核对数据匹配）：python -m scripts.make_samples_finance

产出 backend/samples/：
- bankflow_cmb_sample.xlsx   招商银行格式：可匹配合同回款/报销/项目成本/公户支出的行 + 1 行与库内重复 + 1 行格式错误
- bankflow_cmb_500.xlsx      招商银行格式 520 行（性能验收：≤10s）
- bankflow_icbc_sample.csv   工商银行格式（GBK 编码 CSV，发生额 + 借贷标志）
- lemon_vouchers_sample.xlsx 柠檬云标准凭证导出（上月，100% 可解析，与 API 通道一致）
- tax_vat_sample.xlsx        增值税及附加税费申报表（上月）
测试用例直接调用 build_* 函数，在测试库上现场生成同口径文件。
"""
import csv
import io
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.timeutil import month_range, prev_month, today
from app.integration.lemoncloud.base import DateRange
from app.integration.lemoncloud.mock import MockLemonCloud
from app.models import (BizContract, BizExpenseReport, BizProject, BizProjectCost, FinBankFlow, SysUser)
from app.services import lemoncloud_service, tax_service

D = Decimal
SAMPLES = Path(__file__).resolve().parents[1] / "samples"
CMB_ACCOUNT = "招商银行基本户 7559****0001"
CMB_NO = "755912345670001"
ICBC_ACCOUNT = "工商银行一般户 3602****0088"
ICBC_NO = "3602012345678900088"
CMB_HEAD = ["交易日", "交易时间", "借方金额", "贷方金额", "余额", "摘要", "用途", "对方户名", "对方账号", "流水号"]


def _last_flow(db, account):
    return db.scalar(select(FinBankFlow).where(FinBankFlow.bank_account == account, FinBankFlow.balance.is_not(None))
                     .order_by(FinBankFlow.trade_date.desc(), FinBankFlow.trade_time.desc().nulls_last(),
                               FinBankFlow.id.desc()).limit(1))


def _cmb_xlsx(rows: list[list], account_no: str = CMB_NO, title: str = "招商银行 账务明细") -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "账务明细"
    ws.append([title])
    ws.append([f"账号：{account_no}", None, None, "户名：广东聚成数智科技有限公司"])
    ws.append([f"币种：人民币", None, None, f"查询日期：{today():%Y-%m-%d}"])
    ws.append([])
    ws.append(CMB_HEAD)
    for r in rows:
        ws.append(r)
    for col, w in zip("ABCDEFGHIJ", (12, 10, 14, 14, 16, 20, 30, 32, 22, 22)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _f(d):
    return float(d) if d is not None else None


def build_cmb_sample(db) -> tuple[bytes, dict]:
    """返回 (xlsx bytes, expected)。expected 列出每个可匹配行的正确目标，供准确率断言。"""
    T = today()
    last = _last_flow(db, CMB_ACCOUNT)
    bal = last.balance
    rows: list[list] = []
    expected: list[dict] = []
    # 1) 与库内重复的一行（最后一笔流水原样，无流水号 → 以余额作指纹尾）
    rows.append([last.trade_date.strftime("%Y%m%d"), (last.trade_time or datetime.combine(last.trade_date, time(9))).strftime("%H:%M:%S"),
                 _f(last.amount) if last.direction == 2 else None, _f(last.amount) if last.direction == 1 else None,
                 _f(last.balance), last.summary, None, last.counterparty, None, None])
    seq = [0]
    t0 = datetime.combine(T, time(18, 0))

    def add(direction, amount, cp, summary, purpose=None, cp_acct=None, exp=None):
        nonlocal bal
        seq[0] += 1
        bal = bal + amount if direction == 1 else bal - amount
        rows.append([T.strftime("%Y%m%d"), (t0 + timedelta(minutes=seq[0] * 3)).strftime("%H:%M:%S"),
                     _f(amount) if direction == 2 else None, _f(amount) if direction == 1 else None, _f(bal),
                     summary, purpose, cp, cp_acct, f"CMB{T:%Y%m%d}{seq[0]:06d}"])
        if exp:
            expected.append({"row": len(rows) + 5, "amount": amount, **exp})

    # 2) 合同回款：计划阶段剩余额精确匹配（排除已有待核对到账的合同、金额唯一、客户唯一）
    pending = {(f.counterparty, f.amount) for f in db.scalars(select(FinBankFlow).where(FinBankFlow.match_status == 0))}
    contracts = db.scalars(select(BizContract).where(BizContract.is_deleted.is_(False))
                           .options(selectinload(BizContract.plans), selectinload(BizContract.payments))
                           .order_by(BizContract.id)).all()
    dues = []
    for c in contracts:
        bal_c = c.amount - sum((p.amount for p in c.payments), D(0))
        nxt = next((p for p in c.plans if p.amount > p.received_amount), None)
        dues.append((c, bal_c, (nxt.amount - nxt.received_amount) if nxt else None, nxt))
    all_amounts = [x[2] for x in dues if x[2]] + [x[1] for x in dues]
    used_customers = {cp for cp, _ in pending}
    projects = {p.project_code: p for p in db.scalars(select(BizProject))}
    picked = 0
    for c, bal_c, due, nxt in dues:
        if picked >= 2 or not due or due > bal_c or c.customer_name in used_customers or all_amounts.count(due) > 1:
            continue
        add(1, due, c.customer_name, f"{nxt.stage_name}", f"{projects[c.project_code].name} {nxt.stage_name}",
            exp={"kind": "payment", "target_id": c.id, "target_no": c.contract_no})
        used_customers.add(c.customer_name)
        picked += 1
    # 合同余额精确匹配（无回款计划的新签合同）
    for c, bal_c, due, nxt in dues:
        if not c.plans and bal_c > 0 and c.customer_name not in used_customers and all_amounts.count(bal_c) == 1:
            add(1, bal_c, c.customer_name, "合同款", f"{c.contract_no} 合同款",
                exp={"kind": "payment", "target_id": c.id, "target_no": c.contract_no})
            used_customers.add(c.customer_name)
            break
    # 3) 报销付款：审批通过、未付款、无待核对流水
    pend_exp = {(f.counterparty, f.amount) for f in db.scalars(select(FinBankFlow).where(
        FinBankFlow.match_status == 0, FinBankFlow.direction == 2))}
    reps = db.scalars(select(BizExpenseReport).where(BizExpenseReport.status.in_((2, 4)), BizExpenseReport.pay_status == 0,
                                                     BizExpenseReport.bank_flow_id.is_(None))
                      .order_by(BizExpenseReport.approved_at.desc())).all()
    amounts = [r.amount for r in reps]
    n = 0
    for r in reps:
        u = db.get(SysUser, r.applicant_id)
        if n >= 3 or (u.name, r.amount) in pend_exp or amounts.count(r.amount) > 1:
            continue
        add(2, r.amount, u.name, "代发报销款", f"报销款 {u.name}",
            exp={"kind": "expense", "target_id": r.id, "target_no": r.approval_no})
        n += 1
    # 4) 已登记待付款的项目采购成本
    cost = db.scalar(select(BizProjectCost).where(BizProjectCost.bank_flow_id.is_(None),
                                                  BizProjectCost.summary.contains("待付款")).limit(1))
    if cost:
        add(2, cost.amount, "锐捷网络股份有限公司", "汇款", f"{projects[cost.project_code].name} 核心交换机货款",
            exp={"kind": "project_cost", "target_id": cost.id, "target_no": cost.project_code})
    # 5) 新登记项目成本（摘要含项目名）
    p = projects["JC-AF-2026-002"]
    add(2, D("43760.00"), "广州盛通线缆有限公司", "汇款", f"{p.name} 线缆辅材采购",
        exp={"kind": "project_cost", "target_id": None, "target_no": p.project_code})
    # 6) 公户支出
    add(2, D("3268.45"), "广州供电局有限公司", "电费", "办公室电费 9 月",
        exp={"kind": "public", "target_id": None, "target_no": None})
    add(2, D("35.00"), None, "手续费", "网银转账手续费", exp={"kind": "public", "target_id": None, "target_no": None})
    # 7) 格式错误行（放在末尾，不影响余额连续性校验）
    rows.append(["2026-13-45", "18:59:00", None, "1,2x3.00", None, "格式错误示例", None, "某公司", None, "CMBERR000001"])
    return _cmb_xlsx(rows), {"expected": expected, "duplicate_rows": 1, "error_row": len(rows) + 5,
                             "data_rows": len(rows), "account": CMB_ACCOUNT}


def build_cmb_500(n: int = 520) -> bytes:
    """招商银行一般户（7559****0002）上上月流水 n 行，余额连续，用于性能/去重验收。"""
    rnd = random.Random(500)
    T = today()
    ms, me = month_range(prev_month(prev_month(f"{T:%Y-%m}")))
    bal = D("2000000.00")
    cps = ["深圳市宝安区教育局", "华为技术有限公司", "广州盛通线缆有限公司", "中国移动通信集团广东有限公司",
           "杭州海康威视数字技术股份有限公司", "广州天河科技园物业管理有限公司", "东莞松山湖实验学校", "锐捷网络股份有限公司"]
    rows = []
    days = (me - ms).days + 1
    for i in range(n):
        d = ms + timedelta(days=i * days // n)
        direction = 1 if rnd.random() < 0.45 else 2
        amt = D(rnd.randint(300, 180000)) + D(rnd.randint(0, 99)) / 100
        if direction == 2 and bal - amt < 0:
            direction = 1
        bal = bal + amt if direction == 1 else bal - amt
        rows.append([d.strftime("%Y%m%d"), f"{9 + (i % 9):02d}:{(i * 7) % 60:02d}:{i % 60:02d}",
                     _f(amt) if direction == 2 else None, _f(amt) if direction == 1 else None, _f(bal),
                     "汇款" if direction == 2 else "转账收入", f"业务往来 {i + 1:04d}", rnd.choice(cps),
                     f"62220{rnd.randint(10 ** 9, 10 ** 10 - 1)}", f"CMB2{d:%Y%m%d}{i:06d}"])
    return _cmb_xlsx(rows, "755912345670002", "招商银行 账务明细（一般户）")


def build_icbc_csv(db) -> bytes:
    T = today()
    last = _last_flow(db, ICBC_ACCOUNT)
    bal = last.balance if last else D("3500000")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["中国工商银行 对公账户明细"])
    w.writerow([f"账号：{ICBC_NO}", "户名：广东聚成数智科技有限公司"])
    w.writerow(["本方账号", "交易时间", "借贷标志", "发生额", "余额", "对方单位名称", "对方账号", "摘要", "用途", "交易流水号"])
    for i, (dirn, amt, cp, sm, use) in enumerate([
            ("借", D("2380.00"), "中国电信股份有限公司广州分公司", "代扣", "宽带及电话费"),
            ("借", D("600.00"), None, "收费", "网银年费"),
            ("贷", D("12800.00"), "广州市越秀区教育局", "转账", "劳动教育评价平台服务费")], start=1):
        bal = bal + amt if dirn == "贷" else bal - amt
        w.writerow([ICBC_NO, f"{T:%Y-%m-%d} {10 + i}:{15 * i:02d}:00", dirn, f"{amt:,.2f}", f"{bal:,.2f}", cp or "",
                    "", sm, use, f"ICBC{T:%Y%m%d}{i:05d}"])
    return out.getvalue().encode("gb18030")


def build_lemon_sample(db, period: str | None = None) -> bytes:
    period = period or prev_month(f"{today():%Y-%m}")
    s, e = month_range(period)
    vs = MockLemonCloud(db).fetch_vouchers(DateRange(s, e))
    return lemoncloud_service.export_vouchers_xlsx(vs, f"凭证列表 {period}")


def build_tax_sample(period: str | None = None) -> bytes:
    period = period or prev_month(f"{today():%Y-%m}")
    y, m = map(int, period.split("-"))
    nxt = date(y + (m == 12), m % 12 + 1, 15)
    return tax_service.make_tax_xlsx([
        {"tax_type": "增值税", "period": period, "taxable": D("6458000.00"), "payable": D("193740.00"),
         "paid": D("193740.00"), "filing_date": nxt},
        {"tax_type": "附加税", "period": period, "taxable": D("193740.00"), "payable": D("23248.80"),
         "paid": D("23248.80"), "filing_date": nxt}])


def main():
    from app.core.db import SessionLocal
    SAMPLES.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        data, meta = build_cmb_sample(db)
        (SAMPLES / "bankflow_cmb_sample.xlsx").write_bytes(data)
        (SAMPLES / "bankflow_cmb_500.xlsx").write_bytes(build_cmb_500())
        (SAMPLES / "bankflow_icbc_sample.csv").write_bytes(build_icbc_csv(db))
        (SAMPLES / "lemon_vouchers_sample.xlsx").write_bytes(build_lemon_sample(db))
        (SAMPLES / "tax_vat_sample.xlsx").write_bytes(build_tax_sample())
        print(f"samples written to {SAMPLES}; cmb sample expects {len(meta['expected'])} matchable rows")
    finally:
        db.close()


if __name__ == "__main__":
    main()
