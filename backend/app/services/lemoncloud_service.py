"""柠檬云集成（FR-08）：API 通道同步、文件通道解析、同步对账、双通道一致性校验、供数。

批次号：LC-{yyyymm}-{API|FILE}-{seq:02d}。同期最新一次成功同步覆盖 fin_voucher 该期间数据（channel 标注来源）；
每个文件批次保留原件（file_id），一致性校验时重新解析原件与 API 拉取结果逐张比对。
对账（reconcile）：
  1) 每张凭证借贷平衡；批次借贷合计相等
  2) 来源比对：银行存款(1002)/库存现金(1001) 按日发生额 vs 工作台出纳日记账（银行/现金）按日收支——金额与笔数
  3) 文件通道：文件「合计」行声明的借贷合计 vs 明细合计
"""
from __future__ import annotations

import io
import re
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.response import encode
from app.core.storage import read_bytes, save_bytes
from app.core.timeutil import month_range, today
from app.integration.bankflow.parser import norm, parse_datetime, parse_money
from app.integration.lemoncloud import get_lemoncloud
from app.integration.lemoncloud.base import SUBJECTS, DateRange, VoucherDto
from app.models import FinCashJournal, FinSubjectBalance, FinVoucher, SyncLemonBatch, SysFile
from app.services import dict_service

D = Decimal
BUILTIN_TEMPLATE = {
    "name": "柠檬云凭证导出（标准）", "header_scan": 10,
    "columns": {
        "voucher_date": ["凭证日期", "日期", "记账日期"],
        "voucher_no": ["凭证字号", "凭证号", "凭证字"],
        "summary": ["摘要"],
        "subject_code": ["科目编码", "科目代码"],
        "subject_name": ["科目名称", "会计科目", "科目"],
        "debit": ["借方金额", "借方"],
        "credit": ["贷方金额", "贷方"],
        "aux": ["辅助核算", "辅助项", "项目"],
    },
    "required": ["voucher_date", "voucher_no", "subject_code", "debit", "credit"],
    "project_pattern": r"JC-[A-Z]{2}-\d{4}-\d{3}",
}
HEADERS = ["凭证日期", "凭证字号", "摘要", "科目编码", "科目名称", "借方金额", "贷方金额", "辅助核算"]


def template(db: Session) -> dict:
    return dict_service.get(db, "lemon_template", "standard") or BUILTIN_TEMPLATE


def _batch_no(db: Session, period: str, channel: str) -> str:
    prefix = f"LC-{period.replace('-', '')}-{channel.upper()}-"
    n = db.scalar(select(func.count()).select_from(SyncLemonBatch).where(SyncLemonBatch.batch_no.like(prefix + "%"))) or 0
    return f"{prefix}{n + 1:02d}"


# ---------------- 文件通道：导出 / 解析 ----------------
def export_vouchers_xlsx(vouchers: list[VoucherDto], title: str = "凭证列表") -> bytes:
    """按柠檬云标准导出格式生成 xlsx（mock 演示、样例文件、种子用）。"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "凭证"
    ws.append([f"{title}"])
    ws.append([f"导出时间：{today():%Y-%m-%d}", None, "单位：元"])
    ws.append(HEADERS)
    dr = cr = D(0)
    for v in vouchers:
        ws.append([v.voucher_date.isoformat(), v.voucher_no, v.summary, v.subject_code, v.subject_name,
                   float(v.debit) if v.debit else None, float(v.credit) if v.credit else None,
                   f"项目：{v.aux_project}" if v.aux_project else None])
        dr += v.debit
        cr += v.credit
    ws.append(["合计", None, None, None, None, float(dr), float(cr), None])
    for col, w in zip("ABCDEFGH", (12, 10, 40, 10, 16, 14, 14, 26)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_file(db: Session, file_bytes: bytes, filename: str) -> dict:
    """解析柠檬云导出凭证文件：返回 {vouchers, errors[{row, field, message}], declared, total_rows, period}。"""
    tpl = template(db)
    from openpyxl import load_workbook
    if not filename.lower().endswith(".xlsx"):
        return {"vouchers": [], "errors": [{"row": 0, "field": "文件", "message": "仅支持柠檬云导出的 .xlsx 凭证文件"}],
                "declared": None, "total_rows": 0, "period": None}
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:  # noqa: BLE001
        return {"vouchers": [], "errors": [{"row": 0, "field": "文件", "message": f"无法读取 Excel：{exc}"}],
                "declared": None, "total_rows": 0, "period": None}
    table = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    hidx, mapping = None, {}
    for i, row in enumerate(table[: int(tpl.get("header_scan", 10))]):
        cells = [norm(c) for c in row]
        m = {}
        for fld, aliases in tpl["columns"].items():
            for a in aliases:
                if norm(a) in cells:
                    m[fld] = cells.index(norm(a))
                    break
        if all(k in m for k in tpl["required"]):
            hidx, mapping = i, m
            break
    if hidx is None:
        miss = "、".join(tpl["columns"][k][0] for k in tpl["required"])
        return {"vouchers": [], "errors": [{"row": 0, "field": "表头", "message": f"未识别到柠檬云凭证表头，需包含：{miss}"}],
                "declared": None, "total_rows": 0, "period": None}
    header = table[hidx]
    cname = {k: str(header[i]).strip() for k, i in mapping.items()}
    pat = re.compile(tpl.get("project_pattern") or BUILTIN_TEMPLATE["project_pattern"])
    vouchers, errors, declared, total = [], [], None, 0
    line_no: dict[tuple, int] = defaultdict(int)
    last_date, last_no = None, None

    def cell(row, k):
        i = mapping.get(k)
        return row[i] if i is not None and i < len(row) else None

    for ridx in range(hidx + 1, len(table)):
        row, rn = table[ridx], ridx + 1
        if not any(c not in (None, "") for c in row):
            continue
        first = str(next((c for c in row if c not in (None, "")), "")).strip()
        if first.startswith(("合计", "总计")):
            try:
                declared = {"debit": parse_money(cell(row, "debit")) or D(0), "credit": parse_money(cell(row, "credit")) or D(0),
                            "row": rn}
            except ValueError:
                errors.append({"row": rn, "field": "合计", "message": "合计行金额无效"})
            continue
        total += 1
        errs = []
        raw_d, raw_no = cell(row, "voucher_date"), cell(row, "voucher_no")
        vd = None
        if raw_d in (None, "") and last_date and raw_no in (None, ""):
            vd = last_date  # 同一凭证后续分录可省略日期与字号
        else:
            try:
                vd = parse_datetime(raw_d).date()
            except ValueError as e:
                errs.append({"row": rn, "field": cname["voucher_date"], "message": str(e)})
        no = str(raw_no).strip() if raw_no not in (None, "") else last_no if raw_d in (None, "") else None
        if not no:
            errs.append({"row": rn, "field": cname["voucher_no"], "message": "凭证字号为空"})
        else:
            m = re.match(r"^\s*([^\d\-\s]*)[-\s]?0*(\d+)\s*$", str(no))
            if not m:
                errs.append({"row": rn, "field": cname["voucher_no"], "message": f"凭证字号格式无效：{no!r}"})
            else:
                no = f"{m.group(1) or '记'}-{int(m.group(2)):03d}"
        code = cell(row, "subject_code")
        code = str(int(code)) if isinstance(code, float) and code.is_integer() else (str(code).strip() if code not in (None, "") else "")
        if not code:
            errs.append({"row": rn, "field": cname["subject_code"], "message": "科目编码为空"})
        try:
            dr = parse_money(cell(row, "debit")) or D(0)
            cr = parse_money(cell(row, "credit")) or D(0)
            if dr and cr:
                errs.append({"row": rn, "field": f"{cname['debit']}/{cname['credit']}", "message": "借贷方同时有金额"})
            elif not dr and not cr:
                errs.append({"row": rn, "field": f"{cname['debit']}/{cname['credit']}", "message": "借贷方金额均为空"})
        except ValueError as e:
            errs.append({"row": rn, "field": f"{cname['debit']}/{cname['credit']}", "message": str(e)})
            dr = cr = D(0)
        if errs:
            errors.extend(errs)
            continue
        last_date, last_no = vd, no
        line_no[(no, vd)] += 1
        aux_txt = str(cell(row, "aux") or "")
        am = pat.search(aux_txt)
        sname = str(cell(row, "subject_name") or "").strip() or SUBJECTS.get(code[:4], code)
        vouchers.append(VoucherDto(no, line_no[(no, vd)], vd, str(cell(row, "summary") or "")[:255], code, sname[:64],
                                   debit=dr, credit=cr, aux_project=am.group(0) if am else None))
    periods = sorted({f"{v.voucher_date:%Y-%m}" for v in vouchers})
    return {"vouchers": vouchers, "errors": errors, "declared": declared, "total_rows": total,
            "period": periods[0] if len(periods) == 1 else (periods[0] if periods else None), "periods": periods}


# ---------------- 入库 + 对账 ----------------
def _replace_vouchers(db: Session, period: str, vouchers: list[VoucherDto], batch_no: str, channel: str) -> None:
    start, end = month_range(period)
    db.execute(delete(FinVoucher).where(FinVoucher.voucher_date.between(start, end)))
    db.add_all([FinVoucher(voucher_no=v.voucher_no, line_no=v.line_no, voucher_date=v.voucher_date, summary=v.summary,
                           amount=v.amount, direction=v.direction, subject_code=v.subject_code,
                           subject_name=v.subject_name, aux_project=v.aux_project, sync_batch=batch_no, channel=channel)
                for v in vouchers])
    db.flush()


def reconcile(db: Session, period: str, vouchers: list[VoucherDto], declared: dict | None = None) -> dict:
    diffs = []
    by_no: dict[str, list[D]] = defaultdict(lambda: [D(0), D(0)])
    for v in vouchers:
        by_no[v.voucher_no][0] += v.debit
        by_no[v.voucher_no][1] += v.credit
    for no, (dr, cr) in sorted(by_no.items()):
        if dr != cr:
            diffs.append({"type": "unbalanced", "voucher_no": no, "debit": dr, "credit": cr,
                          "message": f"凭证 {no} 借贷不平：借 {dr:,.2f} / 贷 {cr:,.2f}"})
    dr_total = sum((v.debit for v in vouchers), D(0))
    cr_total = sum((v.credit for v in vouchers), D(0))
    if declared:
        for k, val in (("debit", dr_total), ("credit", cr_total)):
            if declared[k] != val:
                diffs.append({"type": "declared_total", "side": k, "declared": declared[k], "actual": val,
                              "message": f"文件合计行{'借' if k == 'debit' else '贷'}方 {declared[k]:,.2f} 与明细合计 {val:,.2f} 不符"})
    # 来源比对：银行/现金科目按日 vs 工作台日记账
    start, end = month_range(period)
    src: dict[tuple, list] = defaultdict(lambda: [D(0), 0])
    for acct, d, s, n in db.execute(select(FinCashJournal.account_type, FinCashJournal.journal_date,
                                           func.sum(FinCashJournal.amount), func.count())
                                    .where(FinCashJournal.journal_date.between(start, end))
                                    .group_by(FinCashJournal.account_type, FinCashJournal.journal_date)):
        src[("1002" if acct == "bank" else "1001", d)] = [D(s), n]
    got: dict[tuple, list] = defaultdict(lambda: [D(0), 0])
    for v in vouchers:
        if v.subject_code[:4] in ("1001", "1002"):
            g = got[(v.subject_code[:4], v.voucher_date)]
            g[0] += abs(v.debit) + abs(v.credit)
            g[1] += 1
    for key in sorted(set(src) | set(got)):
        s, g = src.get(key, [D(0), 0]), got.get(key, [D(0), 0])
        if s[0] != g[0] or s[1] != g[1]:
            diffs.append({"type": "source_mismatch", "subject_code": key[0], "subject_name": SUBJECTS[key[0]],
                          "date": key[1], "source_amount": s[0], "source_count": s[1], "lemon_amount": g[0],
                          "lemon_count": g[1],
                          "message": f"{key[1]} {SUBJECTS[key[0]]}：柠檬云 {g[1]} 笔 {g[0]:,.2f} / 工作台日记账 {s[1]} 笔 {s[0]:,.2f}"})
    src_count = sum(v[1] for v in src.values())
    src_total = sum((v[0] for v in src.values()), D(0))
    return {"voucher_count": len(by_no), "line_count": len(vouchers), "debit_total": dr_total, "credit_total": cr_total,
            "source_count": src_count, "source_total": src_total,
            "lemon_cash_count": sum(v[1] for v in got.values()),
            "lemon_cash_total": sum((v[0] for v in got.values()), D(0)),
            "status": "pass" if not diffs else "diff", "diffs": diffs}


def _apply(batch: SyncLemonBatch, rec: dict) -> None:
    batch.voucher_count, batch.debit_total, batch.credit_total = rec["voucher_count"], rec["debit_total"], rec["credit_total"]
    batch.source_count, batch.source_total = rec["source_count"], rec["source_total"]
    batch.reconcile_status, batch.diffs = rec["status"], encode(rec["diffs"])


def sync_api(db: Session, period: str | None = None) -> SyncLemonBatch:
    period = period or f"{today():%Y-%m}"
    start, end = month_range(period)
    prov = get_lemoncloud(db)
    batch = SyncLemonBatch(batch_no=_batch_no(db, period, "api"), channel="api", period=period, errors=[], diffs=[])
    db.add(batch)
    try:
        vouchers = prov.fetch_vouchers(DateRange(start, end))
        balances = prov.fetch_subject_balances(period)
    except Exception as exc:  # noqa: BLE001
        batch.reconcile_status, batch.errors = "error", [{"row": 0, "field": "API", "message": str(exc)[:500]}]
        db.flush()
        return batch
    _replace_vouchers(db, period, vouchers, batch.batch_no, "api")
    db.execute(delete(FinSubjectBalance).where(FinSubjectBalance.period == period))
    db.add_all([FinSubjectBalance(period=period, subject_code=b.subject_code, subject_name=b.subject_name,
                                  debit_amount=b.debit_amount, credit_amount=b.credit_amount,
                                  debit_balance=b.debit_balance, credit_balance=b.credit_balance,
                                  sync_batch=batch.batch_no) for b in balances])
    _apply(batch, reconcile(db, period, vouchers))
    db.flush()
    return batch


def sync_file(db: Session, file_bytes: bytes, filename: str, uploader_id: int | None = None,
              period: str | None = None) -> tuple[SyncLemonBatch, dict]:
    res = parse_file(db, file_bytes, filename)
    period = period or res["period"] or f"{today():%Y-%m}"
    errors = list(res["errors"])
    if res.get("periods") and len(res["periods"]) > 1:
        errors.append({"row": 0, "field": "凭证日期", "message": f"文件跨多个期间：{', '.join(res['periods'])}，请按月导出"})
    f = save_bytes(db, data=file_bytes, filename=filename, category="lemoncloud",
                   key=f"lemoncloud/{period}/{filename}", uploader_id=uploader_id)
    batch = SyncLemonBatch(batch_no=_batch_no(db, period, "file"), channel="file", period=period, file_id=f.id,
                           errors=errors[:500], diffs=[])
    db.add(batch)
    if errors:
        batch.reconcile_status = "error"
        batch.voucher_count = len({v.voucher_no for v in res["vouchers"]})
        db.flush()
        return batch, res
    _replace_vouchers(db, period, res["vouchers"], batch.batch_no, "file")
    _apply(batch, reconcile(db, period, res["vouchers"], res["declared"]))
    db.flush()
    return batch, res


def rerun_reconcile(db: Session, batch: SyncLemonBatch) -> SyncLemonBatch:
    if batch.channel == "file" and batch.file_id:
        f = db.get(SysFile, batch.file_id)
        res = parse_file(db, read_bytes(f), f.filename)
        rec = reconcile(db, batch.period, res["vouchers"], res["declared"])
    else:
        start, end = month_range(batch.period)
        rec = reconcile(db, batch.period, get_lemoncloud(db).fetch_vouchers(DateRange(start, end)))
    _apply(batch, rec)
    db.flush()
    return batch


def _key(v: VoucherDto) -> tuple:
    return (v.subject_code, f"{v.debit:.2f}", f"{v.credit:.2f}", v.aux_project or "")


def consistency(db: Session, period: str) -> dict:
    """API 与文件双通道同周期一致性：API 实时拉取 vs 最新文件批次原件逐张比对。"""
    start, end = month_range(period)
    api_v = get_lemoncloud(db).fetch_vouchers(DateRange(start, end))
    fb = db.scalar(select(SyncLemonBatch).where(SyncLemonBatch.period == period, SyncLemonBatch.channel == "file",
                                                SyncLemonBatch.file_id.is_not(None))
                   .order_by(SyncLemonBatch.id.desc()).limit(1))
    if not fb:
        return {"period": period, "status": "no_file", "message": "该期间尚无文件通道批次，请先上传柠檬云导出凭证"}
    f = db.get(SysFile, fb.file_id)
    file_v = parse_file(db, read_bytes(f), f.filename)["vouchers"]

    def group(vs):
        g: dict[str, dict] = {}
        for v in vs:
            x = g.setdefault(v.voucher_no, {"date": v.voucher_date, "lines": []})
            x["lines"].append(_key(v))
        for x in g.values():
            x["lines"].sort()
        return g

    a, b = group(api_v), group(file_v)
    diffs = []
    for no in sorted(set(a) | set(b)):
        if no not in b:
            diffs.append({"voucher_no": no, "type": "only_api", "message": f"{no} 仅 API 通道存在"})
        elif no not in a:
            diffs.append({"voucher_no": no, "type": "only_file", "message": f"{no} 仅文件通道存在"})
        elif a[no]["date"] != b[no]["date"] or a[no]["lines"] != b[no]["lines"]:
            diffs.append({"voucher_no": no, "type": "mismatch", "api": a[no]["lines"], "file": b[no]["lines"],
                          "message": f"{no} 分录不一致"})
    summ = lambda vs: {"vouchers": len({v.voucher_no for v in vs}), "lines": len(vs),  # noqa: E731
                       "debit": sum((v.debit for v in vs), D(0)), "credit": sum((v.credit for v in vs), D(0))}
    return {"period": period, "status": "pass" if not diffs else "diff", "api": summ(api_v), "file": summ(file_v),
            "file_batch": fb.batch_no, "diffs": diffs[:200], "diff_count": len(diffs)}


def batch_row(db: Session, b: SyncLemonBatch) -> dict:
    from app.core.storage import file_brief
    return {"id": b.id, "batch_no": b.batch_no, "channel": b.channel, "period": b.period,
            "voucher_count": b.voucher_count, "debit_total": b.debit_total, "credit_total": b.credit_total,
            "source_count": b.source_count, "source_total": b.source_total, "reconcile_status": b.reconcile_status,
            "diff_count": len(b.diffs or []), "error_count": len(b.errors or []), "created_at": b.created_at,
            "file": file_brief(db.get(SysFile, b.file_id)) if b.file_id else None}


def supply(db: Session, period: str) -> dict:
    """FR-08-04 供数：按期间返回项目辅助核算（收入/成本）+ 科目汇总，供项目利润表/公司月报取数。"""
    start, end = month_range(period)
    rows = db.execute(select(FinVoucher.aux_project, FinVoucher.subject_code, FinVoucher.direction,
                             func.sum(FinVoucher.amount)).where(FinVoucher.voucher_date.between(start, end))
                      .group_by(FinVoucher.aux_project, FinVoucher.subject_code, FinVoucher.direction)).all()
    proj: dict[str, dict] = defaultdict(lambda: {"income": D(0), "cost": D(0)})
    subj: dict[str, dict] = defaultdict(lambda: {"debit": D(0), "credit": D(0)})
    for aux, code, dirn, s in rows:
        s = D(s)
        subj[code]["debit" if dirn == 1 else "credit"] += s
        if aux:
            if code[:4] == "5403":
                proj[aux]["cost"] += s if dirn == 1 else -s
            elif code[:4] in ("1122", "5001"):
                proj[aux]["income"] += s if dirn == 2 else -s
    return {"period": period,
            "projects": [{"project_code": k, **v} for k, v in sorted(proj.items())],
            "subjects": [{"subject_code": k, "subject_name": SUBJECTS.get(k[:4], k), **v} for k, v in sorted(subj.items())]}


def balances(db: Session, period: str) -> list[dict]:
    rows = db.scalars(select(FinSubjectBalance).where(FinSubjectBalance.period == period)
                      .order_by(FinSubjectBalance.subject_code)).all()
    out = []
    for r in rows:
        close = r.debit_balance - r.credit_balance
        op = close - r.debit_amount + r.credit_amount
        out.append({"subject_code": r.subject_code, "subject_name": r.subject_name,
                    "opening_debit": op if op > 0 else D(0), "opening_credit": -op if op < 0 else D(0),
                    "debit_amount": r.debit_amount, "credit_amount": r.credit_amount,
                    "debit_balance": r.debit_balance, "credit_balance": r.credit_balance, "sync_batch": r.sync_batch})
    return out
