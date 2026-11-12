"""Build an editable proposal from authorized catalogue and published KB references."""
from decimal import Decimal

from pydantic import Field
from sqlalchemy import select

from app.core.response import bad_request, forbidden
from app.models import OpsProduct, OpsProject
from app.schemas.operations import Id, Input, Name
from app.services import kb_service, operations_service as ops


class ProductQuantity(Input):
    product_id: Id
    quantity: int = Field(ge=1, le=100000)


class ProposalIn(Input):
    school_id: Id
    need: str = Field(min_length=4, max_length=1000)
    products: list[ProductQuantity] = Field(min_length=1, max_length=40)


def draft(db, p, project_id, body):
    project = ops.get_project(db, p, project_id)
    if not ops.has_project_content(db, p, project):
        raise forbidden("仅项目成员可使用项目产品库")
    school = ops.school(db, project_id, body.school_id)
    ids = {r.product_id for r in body.products}
    if len(ids) != len(body.products):
        raise bad_request("同一产品请合计数量后再选")
    visible = ops.visible_projects(p, members_only=True).with_only_columns(OpsProject.id)
    by_id = {r.id: r for r in db.scalars(select(OpsProduct).where(
        OpsProduct.id.in_(ids), OpsProduct.project_id.in_(visible)))}
    if by_id.keys() != ids:
        raise bad_request("有产品不存在或当前无权使用")
    matches = kb_service.search(db, body.need, limit=4, viewer=p)
    sources = [{"title": r["title"], "heading": r["heading"], "id": r["doc_id"]} for r in matches]
    lines = [f"《{school.name}建设方案初稿》", f"需求：{body.need}", "",
             "一、范围与实施", "1. 与校方核对场地、网络、电源和数量。", "2. 现场确认后排定供货、部署与试运行。", "3. 按经校方确认的功能、数量和验收指标验收。",
             "", "二、设备清单（参考价不作为采购报价）"]
    total = Decimal("0")
    unpriced = 0
    for item in body.products:
        r = by_id[item.product_id]
        price = r.reference_price * item.quantity if r.reference_price is not None else None
        if price is None:
            unpriced += 1
        else:
            total += price
        specs = " / ".join(x for x in (r.brand, r.model) if x) or "规格待厂家确认"
        lines.append(f"- {r.name} · {specs} · {item.quantity}{r.unit}；" + (f"参考小计 ¥{price:,.2f}" if price is not None else "待询价"))
    lines.extend([f"已标价参考合计 ¥{total:,.2f}；待询价 {unpriced} 项。", "", "三、待确认",
                  "现场条件、参数和型号、含税报价、工期、交付及验收责任以确认单为准。", "", "四、可参考的知识库资料"])
    lines.extend([f"- {r['title']}（{r['heading'] or '正文'}）" for r in sources] or ["暂无可引用资料，请补充经审核的方案或设备说明。"])
    return {"text": "\n".join(lines), "sources": sources, "priced_total": total, "unpriced": unpriced}
