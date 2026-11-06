from copy import deepcopy
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import func, select

from app.core.deps import build_principal
from app.core.response import BizError
from app.models import OpsEquipment, OpsImportBatch, OpsIncome, OpsProjectIncome, SysUser
from app.services import operations_service as ops
from app.services.operations_import import Snapshot, apply_snapshot
from tests.test_operations import project, uid
from tests.conftest import data


def snapshot():
    return {"source_name": "导入验收.xlsx", "source_sha256": uuid.uuid4().hex * 2, "kind": "equipment",
        "schools": [{"name": "导入测试学校", "stage": "unknown", "potential": "unknown"}],
        "products": [{"key": "p", "name": "电话线", "model": "规格说明" * 40, "reference_price": "1.814"},
                     {"key": "q", "name": "利旧设备", "reference_price": None}],
        "equipment": [
            {"school": "导入测试学校", "product_key": "p", "values": {"product_id": 1, "quantity": 430, "unit_price": "1.814", "recorded_amount": "780.02"}},
            {"school": "导入测试学校", "product_key": "q", "values": {"product_id": 1, "quantity": 2, "unit_price": None}},
            {"school": "导入测试学校", "product_key": "p", "values": {"product_id": 1, "quantity": 4, "unit_price": "46.32", "recorded_amount": "148.23", "review_note": "原清单金额与数量乘单价不一致，待核对"}}],
        "expected_rows": 3, "expected_total": "928.25", "notes": ["未标价不作为零成本"]}


def principal(db):
    return build_principal(db.get(SysUser, uid(db, "admin")))


def test_faithful_prices_missing_values_and_idempotency(client, login, db, project):
    original = Snapshot.model_validate(snapshot())
    first = apply_snapshot(db, principal(db), project["id"], original)
    db.commit()
    again = apply_snapshot(db, principal(db), project["id"], original)
    db.commit()
    assert again["already_imported"] and again["batch_id"] == first["batch_id"]
    result = data(client.get(f"/api/v1/operations/{project['id']}", headers=login("admin")))
    school = result["schools"][0]
    rows = school["equipment"]
    assert Decimal(rows[0]["unit_price"]) == Decimal("1.814")
    assert Decimal(rows[0]["amount"]) == Decimal("780.02")
    assert rows[1]["unit_price"] is None and rows[1]["amount"] is None
    assert Decimal(rows[2]["amount"]) == Decimal("148.23")
    assert Decimal(rows[2]["calculated_amount"]) == Decimal("185.28")
    assert Decimal(school["total_investment"]) == Decimal("928.25")
    assert school["unpriced_count"] == 1 and school["review_count"] == 1
    assert len(rows) == 3 and len(result["products"][0]["model"]) > 128


def test_import_conflict_rolls_back_and_other_members_denied(client, login, db, project):
    original = Snapshot.model_validate(snapshot())
    apply_snapshot(db, principal(db), project["id"], original)
    db.commit()
    changed = snapshot()
    changed["products"].append({"key": "new", "name": "不能残留的产品"})
    with pytest.raises(BizError, match="已有设备"):
        apply_snapshot(db, principal(db), project["id"], Snapshot.model_validate(changed))
    db.rollback()
    result = ops.project_detail(db, principal(db), project["id"])
    assert len(result["products"]) == 2 and len(result["import_batches"]) == 1
    with pytest.raises(BizError):
        apply_snapshot(db, build_principal(db.get(SysUser, uid(db, "caofang"))), project["id"], original)
    db.rollback()


def test_income_batch_counts_school_and_public_once(client, login, db, project):
    apply_snapshot(db, principal(db), project["id"], Snapshot.model_validate(snapshot()))
    db.commit()
    payload = {"source_name": "2026实收验收.xlsx", "source_sha256": uuid.uuid4().hex * 2, "kind": "income", "expected_rows": 2,
        "expected_total": "135", "incomes": [{"school": "导入测试学校", "income_date": "2026-10-19", "amount": "120"},
                                             {"school": None, "income_date": "2026-10-19", "amount": "15"}]}
    apply_snapshot(db, principal(db), project["id"], Snapshot.model_validate(payload))
    db.commit()
    d = ops.project_detail(db, principal(db), project["id"])
    assert d["financials"]["total_income"] == Decimal("135")
    assert d["financials"]["unassigned_income"] == Decimal("15")
    assert d["schools"][0]["total_income"] == Decimal("120")
    assert d["financials"]["daily"] == [{"date": "2026-10-19", "amount": Decimal("135")}]
    assert apply_snapshot(db, principal(db), project["id"], Snapshot.model_validate(payload))["already_imported"]
    db.rollback()
    payload["source_sha256"] = uuid.uuid4().hex * 2
    with pytest.raises(BizError, match="已登记"):
        apply_snapshot(db, principal(db), project["id"], Snapshot.model_validate(payload))
    db.rollback()


def test_amount_controls_and_null_edit(client, login, db, project):
    payload = snapshot()
    payload["expected_total"] = "999"
    with pytest.raises(BizError, match="金额"):
        apply_snapshot(db, principal(db), project["id"], Snapshot.model_validate(payload))
    db.rollback()
    payload = snapshot()
    payload["equipment"][2]["values"]["review_note"] = ""
    with pytest.raises(ValueError, match="核对说明"):
        Snapshot.model_validate(payload)
    apply_snapshot(db, principal(db), project["id"], Snapshot.model_validate(snapshot()))
    db.commit()
    d = ops.project_detail(db, principal(db), project["id"])
    s = d["schools"][0]; e = s["equipment"][1]
    result = client.put(f"/api/v1/operations/{project['id']}/schools/{s['id']}/equipment/{e['id']}", headers=login("admin"),
        json={"product_id": e["product_id"], "quantity": 2, "unit_price": "3.125", "recorded_amount": None})
    data(result)
    d = data(client.get(f"/api/v1/operations/{project['id']}", headers=login("admin")))
    assert d["financials"]["unpriced_count"] == 0
    assert Decimal(d["schools"][0]["equipment"][1]["amount"]) == Decimal("6.25")
