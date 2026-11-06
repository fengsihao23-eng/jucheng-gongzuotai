"""Apply a reviewed, non-personal campus snapshot atomically. Original workbooks stay outside Git."""
from decimal import Decimal

from pydantic import Field
from sqlalchemy import func, select

from app.core.audit import log_op
from app.core.response import bad_request, conflict
from app.core.timeutil import now, today
from app.models import OpsEquipment, OpsImportBatch, OpsIncome, OpsProduct, OpsProjectIncome, OpsSchool
from app.schemas.operations import EquipmentIn, IncomeIn, Input, ProductIn, SchoolIn
from app.services import operations_service as ops


class ProductRow(ProductIn):
    key: str = Field(min_length=1, max_length=64)


class EquipmentRow(Input):
    school: str
    product_key: str
    values: EquipmentIn  # product_id is replaced only after matching the reviewed product key


class IncomeRow(IncomeIn):
    school: str | None = None


class Snapshot(Input):
    source_name: str = Field(min_length=1, max_length=255)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: str = Field(pattern=r"^(equipment|income)$")
    schools: list[SchoolIn] = Field(default_factory=list)
    products: list[ProductRow] = Field(default_factory=list)
    equipment: list[EquipmentRow] = Field(default_factory=list)
    incomes: list[IncomeRow] = Field(default_factory=list)
    expected_total: Decimal
    expected_rows: int = Field(ge=0)
    notes: list[str] = Field(default_factory=list)


def apply_snapshot(db, p, project_id, snapshot: Snapshot):
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    previous = db.scalar(select(OpsImportBatch).where(OpsImportBatch.project_id == project_id,
        OpsImportBatch.source_sha256 == snapshot.source_sha256))
    if previous:
        return {"already_imported": True, "batch_id": previous.id, **previous.result}
    if (snapshot.kind == "equipment" and snapshot.incomes) or (snapshot.kind == "income" and (snapshot.equipment or snapshot.products)):
        raise bad_request("清单和收款快照不能混用")
    expected_rows = len(snapshot.equipment if snapshot.kind == "equipment" else snapshot.incomes)
    if expected_rows != snapshot.expected_rows:
        raise bad_request("导入行数与核对清单不符")
    known_total = sum((r.values.recorded_amount if r.values.recorded_amount is not None else
                      r.values.quantity * r.values.unit_price if r.values.unit_price is not None else Decimal(0)
                      for r in snapshot.equipment), Decimal(0)) if snapshot.kind == "equipment" else sum((r.amount for r in snapshot.incomes), Decimal(0))
    if known_total != snapshot.expected_total:
        raise bad_request("导入金额与核对清单不符")
    names = [s.name for s in snapshot.schools]
    if len(names) != len(set(names)):
        raise bad_request("学校名称重复")
    schools = {s.name: s for s in db.scalars(select(OpsSchool).where(OpsSchool.project_id == project_id))}
    created_schools = []
    for data in snapshot.schools:
        if data.name not in schools:
            schools[data.name] = ops.save_school(db, p, project_id, data)
            created_schools.append(schools[data.name].id)
    product_keys = [r.key for r in snapshot.products]
    if len(product_keys) != len(set(product_keys)):
        raise bad_request("产品映射键重复")
    products, created_products = {}, []
    for data in snapshot.products:
        matches = db.scalars(select(OpsProduct).where(OpsProduct.project_id == project_id, OpsProduct.name == data.name,
            OpsProduct.brand == data.brand, OpsProduct.model == data.model, OpsProduct.unit == data.unit)).all()
        if len(matches) > 1:
            raise conflict("同名同规格产品有多个候选，请先核对")
        product = matches[0] if matches else ops.save_product(db, p, project_id, ProductIn(**data.model_dump(exclude={"key"})))
        products[data.key] = product
        if not matches:
            created_products.append(product.id)
    equipment_ids, income_ids, project_income_ids = [], [], []
    equipment_school_ids = set()
    for row in snapshot.equipment:
        if row.school not in schools or row.product_key not in products:
            raise bad_request("设备引用的学校或产品未匹配")
        equipment_school_ids.add(schools[row.school].id)
    if equipment_school_ids and db.scalar(select(func.count()).select_from(OpsEquipment).where(OpsEquipment.school_id.in_(equipment_school_ids))):
        raise conflict("学校已有设备记录；新文件不能直接再次累计，请先核对版本和差异")
    for row in snapshot.equipment:
        values = row.values.model_dump()
        values["product_id"] = products[row.product_key].id
        equipment_ids.append(ops.save_equipment(db, p, project_id, schools[row.school].id, EquipmentIn(**values)).id)
    for row in snapshot.incomes:
        values = row.model_dump(exclude={"school"})
        if row.school is not None:
            if row.school not in schools:
                raise bad_request("收入学校未匹配")
            income_ids.append(ops.save_income(db, p, project_id, schools[row.school].id, IncomeIn(**values)).id)
        else:
            if row.income_date > today():
                raise bad_request("收入日期不能晚于今天")
            if db.scalar(select(OpsProjectIncome.id).where(OpsProjectIncome.project_id == project_id, OpsProjectIncome.income_date == row.income_date)):
                raise conflict("该日期已有项目公共收入，请先核对，不能重复累计")
            item = OpsProjectIncome(project_id=project_id, operator_id=p.id, **values)
            db.add(item)
            db.flush()
            project_income_ids.append(item.id)
            log_op(db, p.id, "operations.income.import", "ops_project_income", item.id, after=values)
    result = {"source_name": snapshot.source_name, "kind": snapshot.kind, "rows": expected_rows,
              "amount": str(known_total), "schools_created": len(created_schools), "products_created": len(created_products),
              "equipment_created": len(equipment_ids), "school_days_created": len(income_ids),
              "project_days_created": len(project_income_ids), "notes": snapshot.notes,
              "created_ids": {"schools": created_schools, "products": created_products, "equipment": equipment_ids,
                              "incomes": income_ids, "project_incomes": project_income_ids}}
    batch = OpsImportBatch(project_id=project_id, source_sha256=snapshot.source_sha256, source_name=snapshot.source_name,
        kind=snapshot.kind, operator_id=p.id, result=result, created_at=now())
    db.add(batch)
    db.flush()
    log_op(db, p.id, "operations.snapshot.import", "ops_import_batch", batch.id,
           after={k: v for k, v in result.items() if k != "created_ids"})
    return {"already_imported": False, "batch_id": batch.id, **result}
