"""营销管控部种子：清洗模板 clean_template、签约目标 sign_target、商机阶段变更历史样例（log_operation）。

脏数据样例文件由 scripts/make_samples_marketing.py 生成到 samples/（不在种子中写库）。
"""
import random
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import TZ
from app.models import BizOpportunity, LogOperation, SysDict
from app.services.clean_service import DEFAULT_TEMPLATES

SIGN_TARGET = {
    "2026": {"XY": 22_000_000, "AF": 15_000_000, "KP": 6_000_000, "YW": 7_000_000},
    "2025": {"XY": 18_000_000, "AF": 12_000_000, "KP": 5_000_000, "YW": 6_000_000},
}
LOST_REASONS = {"佛山禅城教育网升级": "价格高于竞品约 12%，客户最终选择本地集成商",
                "珠海金湾区校园安防运维": "客户预算削减，运维项目延至明年统一招标"}
CHAIN = ["lead", "contact", "quote", "negotiation"]


def seed(db: Session, ctx) -> None:
    for target, tpl in DEFAULT_TEMPLATES.items():
        db.add(SysDict(dict_type="clean_template", dict_key=target, dict_value=tpl,
                       remark=f"Excel 清洗模板：{tpl['name']}（标准字段/必填/类型/同义列名）"))
    for year, v in SIGN_TARGET.items():
        db.add(SysDict(dict_type="sign_target", dict_key=year, dict_value=v,
                       remark=f"{year} 年度签约目标（元），按业务线；月度目标 = 年度 ÷ 12，可用 yyyy-MM 键覆盖"))
    db.flush()

    rnd = random.Random(7401)
    T = ctx.T
    end = datetime.combine(T, time(18, 0), TZ)
    for o in db.scalars(select(BizOpportunity).where(BizOpportunity.source_type == 1).order_by(BizOpportunity.id)):
        start = o.created_at
        path = CHAIN[:CHAIN.index(o.stage) + 1] if o.stage in CHAIN else CHAIN + [o.stage]
        if o.stage == "lost":
            path = CHAIN[:rnd.randint(2, 4)] + ["lost"]
        span = max(1, int((end - start).total_seconds() // 86400))
        step = max(1, span // max(1, len(path)))
        at = start
        prev = None
        for i, st in enumerate(path):
            if i:
                at = min(end - timedelta(hours=len(path) - i), at + timedelta(days=rnd.randint(max(1, step // 2), step),
                                                                             hours=rnd.randint(0, 6)))
            after = {"stage": st, "note": "企微商机工单自动登记" if i == 0 else rnd.choice(
                ["客户需求确认", "已提交方案与报价", "完成现场调研", "商务条款沟通中", "客户内部立项通过"])}
            if st == "lost":
                after["lost_reason"] = LOST_REASONS.get(o.name, "客户预算调整，项目暂缓")
                o.remark = f"丢单原因：{after['lost_reason']}"
            if st == "won":
                after["note"] = "中标/签约确认"
            db.add(LogOperation(user_id=None if i == 0 else o.owner_id, action="opportunity.stage",
                                target_type="opportunity", target_id=str(o.id),
                                before={"stage": prev} if prev else None, after=after, created_at=at))
            prev = st
    db.flush()
