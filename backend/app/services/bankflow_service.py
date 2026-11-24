"""银行流水导入编排（FR-09-01/02/04）：模板 → 解析 → 指纹去重 → 余额连续性校验 → 入库 → 导入报告。

指纹 = sha256(账号|日期|方向|金额|摘要|对方|交易流水号 或 余额 或 同文件内序号)。
- 有交易流水号优先用流水号；无流水号用余额（同一账户余额序列天然唯一）；两者都无时用同一文件内相同五元组的出现序号
- 同一文件重复导入 → 全部判重，0 新增
"""
from __future__ import annotations

import hashlib
import re
import time as _time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import encode
from app.core.storage import save_bytes
from app.core.timeutil import today
from app.integration.bankflow import parser
from app.models import FinBankFlow, FinBankImport, SysFile
from app.services import dict_service

D = Decimal

# 本公司银行账户（展示名 → 账号尾号）。可在 sys_dict(bank_account) 覆盖。
BUILTIN_ACCOUNTS = {
    "招商银行基本户 7559****0001": {"bank": "招商银行", "tail": "0001", "prefix": "7559", "template": "cmb"},
    "工商银行一般户 3602****0088": {"bank": "工商银行", "tail": "0088", "prefix": "3602", "template": "icbc"},
    "建设银行一般户 4400****0166": {"bank": "建设银行", "tail": "0166", "prefix": "4400", "template": "ccb"},
}


def templates(db: Session) -> dict[str, dict]:
    t = dict_service.items(db, "bank_template")
    return t or parser.BUILTIN_TEMPLATES


def accounts(db: Session) -> dict[str, dict]:
    return dict_service.items(db, "bank_account") or BUILTIN_ACCOUNTS


def resolve_account(db: Session, raw: str | None, template_key: str | None, explicit: str | None = None) -> str:
    accts = accounts(db)
    if explicit:
        if explicit in accts:
            return explicit
        raw = explicit
    digits = re.sub(r"\D", "", raw or "")
    if digits:
        for name, a in accts.items():
            if digits.endswith(a["tail"]) and (not a.get("prefix") or digits.startswith(a["prefix"]) or len(digits) <= 8):
                return name
        bank = parser.BUILTIN_TEMPLATES.get(template_key or "", {}).get("bank", "银行")
        return f"{bank} {digits[:4]}****{digits[-4:]}" if len(digits) >= 8 else f"{bank} {digits}"
    # 文件未给出账号：取该模板对应的唯一账户
    same = [n for n, a in accts.items() if a.get("template") == template_key]
    if len(same) == 1:
        return same[0]
    raise ValueError("无法确定本方账号：文件中未包含账号信息，请在上传时选择账户")


def fingerprint(account: str, d, direction: int, amount: D, summary: str | None, counterparty: str | None,
                tail: str) -> str:
    raw = f"{account}|{d}|{direction}|{D(amount):.2f}|{summary or ''}|{counterparty or ''}|{tail}"
    return hashlib.sha256(raw.encode()).hexdigest()


def flow_fingerprint(f: FinBankFlow, serial_no: str | None = None, occ: int = 0) -> str:
    tail = f"sn:{serial_no}" if serial_no else (f"bal:{D(f.balance):.2f}" if f.balance is not None else f"occ:{occ}")
    return fingerprint(f.bank_account, f.trade_date, f.direction, f.amount, f.summary, f.counterparty, tail)


def _balance_check(db: Session, rows: list[tuple[str, parser.ParsedRow]]) -> dict:
    """按账户检查余额连续性：上一行余额 ± 本行金额 = 本行余额；自动识别文件正序/倒序；并与库内上一笔衔接。"""
    by_acct: dict[str, list[parser.ParsedRow]] = defaultdict(list)
    for acct, r in rows:
        by_acct[acct].append(r)
    out = {"ok": True, "accounts": []}
    for acct, rs in by_acct.items():
        rs_b = [r for r in rs if r.balance is not None]
        info = {"account": acct, "rows": len(rs), "with_balance": len(rs_b), "order": None, "breaks": [],
                "opening": None, "closing": None, "db_previous_balance": None, "db_continuity": None}
        if len(rs_b) >= 1:
            def signed(r):
                return r.amount if r.direction == 1 else -r.amount

            asc_bad = [rs_b[i] for i in range(1, len(rs_b)) if rs_b[i - 1].balance + signed(rs_b[i]) != rs_b[i].balance]
            desc_bad = [rs_b[i] for i in range(len(rs_b) - 1) if rs_b[i + 1].balance + signed(rs_b[i]) != rs_b[i].balance]
            asc = len(asc_bad) <= len(desc_bad)
            bad = asc_bad if asc else desc_bad
            info["order"] = "asc" if asc else "desc"
            seq = rs_b if asc else list(reversed(rs_b))
            first = seq[0]
            info["opening"] = first.balance - signed(first)
            info["closing"] = seq[-1].balance
            info["breaks"] = [{"row": r.row, "field": "余额",
                               "message": f"余额不连续：按上一笔推算与本行余额 {r.balance:,.2f} 不符"} for r in bad]
            prev = db.scalar(select(FinBankFlow.balance).where(
                FinBankFlow.bank_account == acct, FinBankFlow.trade_date < first.trade_date,
                FinBankFlow.balance.is_not(None)).order_by(FinBankFlow.trade_date.desc(), FinBankFlow.trade_time.desc().nulls_last(),
                                                           FinBankFlow.id.desc()).limit(1))
            same_day = set(db.scalars(select(FinBankFlow.balance).where(
                FinBankFlow.bank_account == acct, FinBankFlow.trade_date == first.trade_date,
                FinBankFlow.balance.is_not(None))))
            if prev is not None or same_day:
                ok_ = info["opening"] == prev or info["opening"] in same_day
                info["db_previous_balance"] = info["opening"] if ok_ else prev
                info["db_continuity"] = ok_
            if bad:
                out["ok"] = False
        out["accounts"].append(info)
    return out


def import_file(db: Session, file_bytes: bytes, filename: str, *, uploader_id: int | None = None,
                source: str = "upload", template_key: str | None = None, account: str | None = None) -> FinBankImport:
    t0 = _time.perf_counter()
    tpls = templates(db)
    f = save_bytes(db, data=file_bytes, filename=filename, category="bankflow",
                   key=f"bankflow/{today():%Y-%m}/{filename}", uploader_id=uploader_id)
    imp = FinBankImport(file_id=f.id, filename=filename, bank_template=template_key or "auto", source=source,
                        uploader_id=uploader_id, errors=[])
    db.add(imp)
    try:
        table = parser.read_table(file_bytes, filename)
        res = parser.parse_table(table, tpls, template_key)
    except ValueError as e:
        res = parser.ParseResult(None, None, None, errors=[{"row": 0, "field": "文件", "message": str(e)}])
    imp.bank_template = res.template_key or template_key or "unknown"
    imp.total_rows = res.total_rows
    errors = list(res.errors)
    resolved: list[tuple[str, parser.ParsedRow]] = []
    acct_cache: dict[str | None, str] = {}
    for r in res.rows:
        raw = r.account_raw or res.header_account
        try:
            if raw not in acct_cache:
                acct_cache[raw] = resolve_account(db, raw, res.template_key, account)
            resolved.append((acct_cache[raw], r))
        except ValueError as e:
            errors.append({"row": r.row, "field": "账号", "message": str(e)})
    # 指纹
    occ: dict[tuple, int] = defaultdict(int)
    fps: list[str] = []
    for acct, r in resolved:
        if r.serial_no:
            tail = f"sn:{r.serial_no}"
        elif r.balance is not None:
            tail = f"bal:{r.balance:.2f}"
        else:
            k = (acct, r.trade_date, r.direction, r.amount, r.summary, r.counterparty)
            occ[k] += 1
            tail = f"occ:{occ[k]}"
        fps.append(fingerprint(acct, r.trade_date, r.direction, r.amount, r.summary, r.counterparty, tail))
    existing = set()
    for i in range(0, len(fps), 500):
        existing |= set(db.scalars(select(FinBankFlow.fingerprint).where(FinBankFlow.fingerprint.in_(fps[i:i + 500]))
                                   .execution_options(include_removed_records=True)))
    imp.balance_check = encode(_balance_check(db, resolved)) if resolved else None
    db.flush()
    inserted, dup, seen = 0, 0, set()
    for (acct, r), fp in zip(resolved, fps):
        if fp in existing or fp in seen:
            dup += 1
            continue
        seen.add(fp)
        db.add(FinBankFlow(bank_account=acct, trade_date=r.trade_date, trade_time=r.trade_time, direction=r.direction,
                           amount=r.amount, counterparty=r.counterparty, counterparty_account=r.counterparty_account,
                           summary=r.summary, balance=r.balance, fingerprint=fp, import_id=imp.id,
                           source_file=filename, source_row=r.row, match_status=0))
        inserted += 1
    imp.inserted_rows, imp.duplicate_rows = inserted, dup
    imp.error_rows = len({e["row"] for e in errors if e.get("row")}) or (1 if errors and not res.rows else 0)
    imp.errors = errors[:500]
    imp.cost_ms = int((_time.perf_counter() - t0) * 1000)
    db.commit()
    return imp


def import_report(db: Session, imp: FinBankImport, with_suggest: bool = True) -> dict:
    out = {"id": imp.id, "filename": imp.filename, "bank_template": imp.bank_template,
           "template_name": templates(db).get(imp.bank_template, {}).get("name", imp.bank_template),
           "total_rows": imp.total_rows, "inserted_rows": imp.inserted_rows, "duplicate_rows": imp.duplicate_rows,
           "error_rows": imp.error_rows, "errors": imp.errors, "balance_check": imp.balance_check,
           "cost_ms": imp.cost_ms, "source": imp.source, "created_at": imp.created_at, "file_id": imp.file_id}
    if with_suggest and imp.inserted_rows:
        from app.services import reconcile_service
        flows = db.scalars(select(FinBankFlow).where(FinBankFlow.import_id == imp.id, FinBankFlow.match_status == 0)).all()
        ctx = reconcile_service.MatchContext(db)
        high = sum(1 for fl in flows if (s := reconcile_service.suggest(db, fl, ctx)) and s[0]["score"] >= reconcile_service.AUTO_THRESHOLD)
        out["suggest_summary"] = {"unmatched": len(flows), "high_confidence": high}
    return out


# ---------------- 目录自动解析（FR-09-04） ----------------
def inbox_dir() -> Path:
    return Path(settings.storage_dir).parent / "inbox" / "bankflow"


def auto_parse_dir(db: Session) -> dict:
    d = inbox_dir()
    d.mkdir(parents=True, exist_ok=True)
    done, skipped, reports = 0, 0, []
    for p in sorted(d.iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".xlsx", ".csv") or p.name.startswith((".", "~$")):
            continue
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        seen = db.scalar(select(func.count()).select_from(FinBankImport).join(SysFile, SysFile.id == FinBankImport.file_id)
                         .where(SysFile.sha256 == sha, FinBankImport.source == "auto_dir"))
        if seen:
            skipped += 1
            continue
        imp = import_file(db, data, p.name, source="auto_dir")
        done += 1
        reports.append({"file": p.name, "inserted": imp.inserted_rows, "duplicate": imp.duplicate_rows,
                        "errors": imp.error_rows})
    return {"files": done, "skipped": skipped, "reports": reports,
            "rows": sum(r["inserted"] for r in reports), "dir": str(d)}
