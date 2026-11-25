"""Project-locked inventory transitions; procurement and approved withdrawals share one ledger."""
from collections import defaultdict
from uuid import uuid4

from sqlalchemy import select

from app.core.audit import log_op, snapshot
from app.core.deps import build_principal
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import today
from app.models import (OpsAssetMovement as Movement, OpsEquipment, OpsMaintenanceRecord as Record,
                        OpsMaintenanceUpdate as Update, OpsProduct, OpsSchool, SysUser)
from app.services import dict_service, operations_scope as scope, operations_service as ops

ACTIONS = {"inbound": "入库", "outbound": "出库投入运营", "return": "退回仓库",
           "scrap_warehouse": "在库设备报废", "scrap_operating": "运营设备报废"}
KINDS = {"device_maintenance": "设备运维", "arrears": "欠费维护",
         "collection_maintenance": "回款维护", "customers": "客户资料维护"}
FIELDS = ["kind", "title", "customer_name", "contact_name", "phone", "address", "device_name", "amount",
          "due_on", "owner_id", "status", "content", "problems", "next_step"]


def _balance(rows):
    result = dict(total=0, warehouse=0, operating=0, scrapped=0)
    for r in rows:
        q = r.quantity
        if r.action == "inbound":
            result["total"] += q
            result["warehouse"] += q
        elif r.action == "outbound":
            result["warehouse"] -= q
            result["operating"] += q
        elif r.action == "return":
            result["operating"] -= q
            result["warehouse"] += q
        else:
            result["warehouse" if r.action == "scrap_warehouse" else "operating"] -= q
            result["scrapped"] += q
    return result


def _rows(db, project_id, product_id=None):
    q = select(Movement).where(Movement.project_id == project_id)
    return list(db.scalars(q.where(Movement.product_id == product_id) if product_id else q))


def sync_equipment(db, p, project, equipment):
    """Called in the same transaction as every manual/imported procurement change."""
    old = db.scalar(select(Movement).where(Movement.equipment_id == equipment.id))
    if old:
        rows = [r for r in _rows(db, project.id, old.product_id) if r.id != old.id]
        if old.product_id == equipment.product_id:
            probe = Movement(action="inbound", quantity=equipment.quantity)
            rows.append(probe)
        if any(v < 0 for v in _balance(rows).values()):
            raise conflict("采购数量或产品调整会导致库存为负，请先核对出库与报废记录")
    row = old or Movement(project_id=project.id, action="inbound", equipment_id=equipment.id,
                          occurred_on=today(), source_type="purchase", source_key=f"equipment:{equipment.id}")
    row.product_id, row.school_id, row.quantity = equipment.product_id, equipment.school_id, equipment.quantity
    row.note, row.operator_id = "学校采购自动入库", p.id
    db.add(row)
    db.flush()


def remove_school_stock(db, project, school_id):
    rows = list(db.scalars(select(Movement).where(Movement.project_id == project.id, Movement.school_id == school_id)))
    if any(r.source_type != "purchase" for r in rows):
        raise conflict("该学校已有出入库或报废记录，请保留学校资料以便追溯")
    for row in rows:
        remaining = [r for r in _rows(db, project.id, row.product_id) if r.id not in {x.id for x in rows}]
        if any(v < 0 for v in _balance(remaining).values()):
            raise conflict("该学校采购已用于出库或报废，不能删除采购来源")
    for row in rows:
        db.delete(row)
    db.flush()


def overview(db, p, project_id, *, scrap=False):
    project = ops.get_project(db, p, project_id)
    scope.require(db, p, project, "scrap" if scrap else "warehouse")
    rows = _rows(db, project.id)
    grouped = defaultdict(list)
    for r in rows:
        grouped[r.product_id].append(r)
    products = {r.id: r for r in db.scalars(select(OpsProduct).where(OpsProduct.id.in_(grouped)))}
    items = [{"product_id": pid, "name": products[pid].name, "model": products[pid].model, "unit": products[pid].unit,
              **_balance(moves)} for pid, moves in sorted(grouped.items())]
    names = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    schools = dict(db.execute(select(OpsSchool.id, OpsSchool.name).where(OpsSchool.project_id == project_id)).all())
    history = [{"id": r.id, "product_id": r.product_id, "product_name": products[r.product_id].name,
                "action": r.action, "action_name": ACTIONS[r.action], "quantity": r.quantity,
                "occurred_on": r.occurred_on, "school": schools.get(r.school_id), "note": r.note,
                "source_type": r.source_type, "reference": r.source_key if r.source_type == "wecom" else None,
                "operator": names.get(r.operator_id, "采购来源"), "created_at": r.created_at} for r in sorted(rows, key=lambda r:r.id, reverse=True)
               if not scrap or r.action.startswith("scrap_")]
    templates = [key for key, cfg in dict_service.items(db, "approval_template").items()
                 if isinstance(cfg, dict) and cfg.get("key") == "stock_out"]
    return {"items": items, "movements": history[:200], "movement_count": len(history),
            "can_manage": ops.role(db, p, project) == "manager" and project.status == "active",
            "today": today(), "approval_connected": bool(templates),
            "approval_note": "已配置企微出库审批；审批通过后自动扣减，重复回调不重复出库" if templates else "尚未配置企业微信出库审批模板，可先登记出入库",
            "note": "采购登记自动入库；总数 = 在库 + 在运营 + 已报废。历史采购先计入在库，可按实际出库记录核对。"}


def move(db, p, project_id, body, *, source_type="manual", source_key=None, actor_id=None):
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    ops.active(project)
    scope.require(db, p, project, "scrap" if body.action.startswith("scrap_") else "warehouse")
    key = source_key or f"{project_id}:{body.request_key}"
    previous = db.scalar(select(Movement).where(Movement.source_type == source_type, Movement.source_key == key))
    if previous:
        expected = (project_id, body.product_id, body.action, body.quantity, body.school_id, body.occurred_on, body.note)
        actual = (previous.project_id, previous.product_id, previous.action, previous.quantity, previous.school_id, previous.occurred_on, previous.note)
        if expected != actual:
            raise conflict("同一出入库单的内容已变化，请人工核对")
        return previous
    product = db.get(OpsProduct, body.product_id)
    existing = _rows(db, project_id, body.product_id)
    if not product or (not existing and product.project_id not in scope.content_project_ids(db, p, "products")):
        raise not_found("可用设备产品")
    if body.school_id:
        ops.school(db, project_id, body.school_id)
    if body.occurred_on > today():
        raise bad_request("出入库日期不能晚于今天")
    balance = _balance(existing)
    available = balance["operating" if body.action in {"return", "scrap_operating"} else "warehouse"]
    if body.action != "inbound" and body.quantity > available:
        raise conflict(f"可用数量仅 {available}，不能超量出库、退库或报废")
    row = Movement(project_id=project_id, product_id=body.product_id, action=body.action,
                   quantity=body.quantity, school_id=body.school_id, occurred_on=body.occurred_on,
                   source_type=source_type, source_key=key, note=body.note, operator_id=actor_id or p.id)
    db.add(row)
    db.flush()
    log_op(db, actor_id or p.id, "operations.inventory.move", "ops_asset_movement", row.id,
           after={"action": row.action, "quantity": row.quantity, "product_id": row.product_id, "source": source_type})
    return row


def approved_stock_out(db, approval, fields):
    """Only explicit template mappings with stable project/product IDs may withdraw stock."""
    from app.schemas.operations_assets import AssetMovementIn
    try:
        project_id = int(fields.get("project_id", ""))
        product_id = int(fields.get("product_id", ""))
        quantity = int(fields.get("quantity", ""))
        school_id = int(fields["school_id"]) if fields.get("school_id") else None
    except (ValueError, TypeError):
        raise bad_request("出库审批须明确提供运营项目编号、产品编号和整数数量")
    actor = db.scalar(select(SysUser).where(SysUser.wecom_userid == approval.applicant_userid))
    if not actor or not actor.status == 1 or actor.is_deleted:
        raise forbidden("审批申请人未关联有效工作台账号")
    p = build_principal(actor)
    if not p.has_menu("operations"):
        raise forbidden("审批申请人无运营项目权限")
    # Approval supplies authorization; its applicant must be a project execution member.
    project = ops.get_project(db, p, project_id, lock=True)
    if ops.role(db, p, project) not in ops.EXECUTION_ROLES:
        raise forbidden("审批申请人不是本项目执行成员")
    scope.require(db, p, project, "warehouse")
    owner = ops.user(db, project.owner_id)
    manager = build_principal(owner)
    body = AssetMovementIn(product_id=product_id, quantity=quantity, school_id=school_id, action="outbound",
        occurred_on=fields.get("occurred_on") or (approval.approved_time or approval.apply_time).astimezone(ops.TZ).date(),
        note=fields.get("note") or f"企业微信已通过出库审批 {approval.sp_no}", request_key=uuid4().hex)
    row = move(db, manager, project_id, body, source_type="wecom", source_key=approval.sp_no, actor_id=actor.id)
    return str(row.id)


def records(db, p, project_id, kind):
    project = ops.get_project(db, p, project_id)
    scope.require(db, p, project, kind)
    role = ops.role(db, p, project)
    active = project.status == "active"
    members = ops.members(db, project)
    users = {m["user_id"]: m["name"] for m in members}
    rows = db.scalars(select(Record).where(Record.project_id == project_id, Record.kind == kind).order_by(Record.id.desc())).all()
    return {"items": [{"id": r.id, **snapshot(r, FIELDS), "revision": r.revision, "owner_name": users.get(r.owner_id, "历史成员"),
                       **({"workbook_row": r.workbook_row} if r.workbook_row else {}),
                       "updated_at": r.updated_at, "can_edit": active and (role == "manager" or
                       (r.owner_id == p.id and role in ops.EXECUTION_ROLES))} for r in rows],
            "members": [m for m in members if m["active"] and m["role"] != "viewer"],
            "can_manage": active and role == "manager"}


def record_history(db, p, project_id, record_id):
    project = ops.get_project(db, p, project_id)
    row = db.get(Record, record_id)
    if not row or row.project_id != project_id:
        raise not_found("维护记录")
    scope.require(db, p, project, row.kind)
    return {"items": [{"id": u.id, "author": name, "created_at": u.created_at, **u.values}
                      for u, name in db.execute(select(Update, SysUser.name).join(SysUser, Update.author_id == SysUser.id)
                      .where(Update.record_id == record_id).order_by(Update.id.desc()))]}


def save_record(db, p, project_id, body, record_id=None):
    project = ops.get_project(db, p, project_id, lock=True)
    scope.require(db, p, project, body.kind)
    ops.active(project)
    row = db.get(Record, record_id) if record_id else None
    if record_id and (not row or row.project_id != project_id or row.kind != body.kind):
        raise not_found("本项目维护记录")
    manager = ops.role(db, p, project) == "manager"
    if not manager and (not row or row.owner_id != p.id or ops.role(db, p, project) not in ops.EXECUTION_ROLES):
        raise forbidden("仅项目管理者可新建；负责人可更新本人维护记录")
    if row and body.expected_revision != row.revision:
        raise conflict("维护记录已更新，请重新打开后提交")
    if not manager and body.owner_id != row.owner_id:
        raise forbidden("仅项目管理者可转交负责人")
    ops.assignee(db, project, body.owner_id)
    before = snapshot(row, FIELDS) if row else None
    row = row or Record(project_id=project_id, revision=0)
    for key, value in body.model_dump(include=set(FIELDS)).items():
        setattr(row, key, value)
    row.revision += 1
    db.add(row)
    db.flush()
    values = body.model_dump(mode="json", include=set(FIELDS))
    db.add(Update(record_id=row.id, author_id=p.id, values=values))
    log_op(db, p.id, "operations.maintenance.save", "ops_maintenance_record", row.id, before=before, after=values)
    return {"id": row.id, "revision": row.revision}


def report_facts(db, p, project_ids, start, end, start_at, end_at, person_id=None):
    """Read only the sections currently visible to this report's audience."""
    projects = [ops.get_project(db, p, pid) for pid in project_ids]
    maintenance, movements, sections = [], [], set()
    for project in projects:
        allowed = set(scope.view(db, p, project)["sections"])
        kinds = allowed & KINDS.keys()
        if kinds:
            query = select(Update, Record.kind, SysUser.name).join(Record, Update.record_id == Record.id).join(
                SysUser, SysUser.id == Update.author_id).where(Record.project_id == project.id,
                Record.kind.in_(kinds), Update.created_at >= start_at, Update.created_at < end_at).order_by(Update.id)
            for update, kind, author in db.execute(query):
                if person_id and update.values.get("owner_id") != person_id:
                    continue
                maintenance.append({"id": update.id, "record_id": update.record_id, "project_id": project.id,
                    "project_name": project.name, "kind": kind, "kind_label": KINDS[kind], "author": author,
                    "created_at": update.created_at, **update.values})
                sections.add(kind)
        if allowed & {"warehouse", "scrap"}:
            query = select(Movement, OpsProduct.name).join(OpsProduct, Movement.product_id == OpsProduct.id).where(
                Movement.project_id == project.id, Movement.occurred_on >= start, Movement.occurred_on <= end).order_by(Movement.id)
            for row, product_name in db.execute(query):
                section = "scrap" if row.action.startswith("scrap_") else "warehouse"
                if section not in allowed or (person_id and row.operator_id != person_id):
                    continue
                movements.append({"id": row.id, "project_id": project.id, "project_name": project.name,
                    "product_name": product_name, "action": row.action, "action_name": ACTIONS[row.action],
                    "quantity": row.quantity, "occurred_on": row.occurred_on, "note": row.note})
                sections.add(section)
    return {"maintenance": maintenance, "asset_movements": movements, "content_sections": sorted(sections)}
