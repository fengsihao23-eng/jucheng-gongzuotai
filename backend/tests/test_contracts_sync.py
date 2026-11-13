"""合同台账固定来源同步（企微在线表格 → 台账）的字段校验与差异落库。"""
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.core.deps import build_principal
from app.models import BizContract, SysUser
from app.services import contracts_sync
from tests.conftest import data

BASE = "/api/v1/contracts"
CONFIG = {
    "enabled": True, "format": "sheet", "docid": "doc_test", "sheet_id": "sheet_test",
    "reader_id": 1, "auto_apply": False,
    "fields": {"contract_no": "合同编号", "customer_name": "客户名称", "amount": "合同金额",
               "sign_date": "签订日期", "business_line": "业务线", "owner_id": "销售负责人"},
}


@pytest.fixture(autouse=True)
def clean(db):
    """测试库共用，用例前后清掉本文件造的 JS- 合同，避免污染其他用例的统计口径。"""
    def wipe():
        for contract in db.scalars(select(BizContract).where(BizContract.contract_no.like("JS-%"))).unique().all():
            db.delete(contract)
        db.commit()

    wipe()
    yield
    wipe()


def first_user(db):
    return db.scalar(select(SysUser).where(SysUser.status == 1).order_by(SysUser.id))


def test_normalize_maps_labels(db):
    owner = first_user(db)
    values, errors = contracts_sync.normalize(db, {
        "contract_no": " js-2026-001 ", "customer_name": " 阳春一中 ", "amount": "¥1,234,567.80",
        "sign_date": "2026年3月5日", "business_line": "数字校园", "owner_id": owner.name,
    }, {owner.name: [owner.id]})
    assert errors == []
    assert values["contract_no"] == "JS-2026-001"
    assert values["customer_name"] == "阳春一中"
    assert values["amount"] == D("1234567.80")
    assert values["sign_date"] == date(2026, 3, 5)
    assert values["business_line"] == "XY"
    assert values["owner_id"] == owner.id


def test_normalize_rejects_bad_rows(db):
    _, errors = contracts_sync.normalize(db, {
        "contract_no": "", "customer_name": "", "amount": "0", "sign_date": "2026-13-01",
        "business_line": "随便写的", "region": "广州", "owner_id": "查无此人",
    }, {})
    assert "合同编号为空" in errors
    assert "客户名称为空" in errors
    assert "合同金额须大于 0" in errors
    assert "签订日期无法识别" in errors
    assert any("业务线" in e for e in errors)
    assert any("区域" in e for e in errors)
    assert any("查无此人" in e for e in errors)


def test_preview_and_apply(db, monkeypatch):
    owner = first_user(db)
    for no in ("JS-EXIST-001", "JS-GONE-009"):
        db.add(BizContract(contract_no=no, name=no, customer_name="老客户", amount=D("100000.00"),
                           sign_date=date(2026, 1, 1), business_line="XY", status=1, source_type=4,
                           source_approval_no="wecom:test"))
    db.commit()

    rows = [
        {"contract_no": "JS-EXIST-001", "customer_name": "老客户", "amount": "120000",
         "sign_date": "2026-01-01", "business_line": "数字校园"},
        {"contract_no": "JS-NEW-002", "customer_name": "新客户", "amount": "88,000",
         "sign_date": "2026/2/2", "business_line": "运维服务", "owner_id": owner.name},
        {"contract_no": "JS-BAD-003", "customer_name": "坏客户", "amount": "0",
         "sign_date": "2026-02-03", "business_line": "数字校园"},
    ]
    monkeypatch.setattr(contracts_sync, "fetch_rows", lambda db, config: rows)

    result = contracts_sync.preview(db, CONFIG, "test")
    assert result["summary"]["rows"] == 3
    assert result["summary"]["create"] == 1
    assert result["summary"]["update"] == 1
    assert result["summary"]["conflict"] == 1
    assert [r["contract_no"] for r in result["removed"]] == ["JS-GONE-009"]
    update_item = next(i for i in result["items"] if i["contract_no"] == "JS-EXIST-001")
    assert update_item["changes"]["amount"] == {"from": "100000.00", "to": "120000.00"}

    applied = contracts_sync.apply(db, build_principal(owner), CONFIG, result, key="test")
    assert applied["created"] == 1 and applied["updated"] == 1 and applied["skipped"] == 1

    db.expire_all()
    created = db.scalar(select(BizContract).where(BizContract.contract_no == "JS-NEW-002"))
    assert created.source_type == 4 and created.source_approval_no == "wecom:test"
    assert created.amount == D("88000.00")
    assert db.scalar(select(BizContract).where(BizContract.contract_no == "JS-EXIST-001")).amount == D("120000.00")

    again = contracts_sync.preview(db, CONFIG, "test")
    assert again["summary"]["create"] == 0 and again["summary"]["update"] == 0
    assert again["summary"]["unchanged"] == 2


def test_sync_config_api(client, login, db):
    headers = login("songjia")
    reader = db.scalar(select(SysUser).where(SysUser.wecom_userid == "songjia"))
    saved = data(client.put(BASE + "/sync/sources", headers=headers, json={
        "key": "验收来源", **{k: CONFIG[k] for k in ("docid", "sheet_id", "fields")}, "reader_id": reader.id,
    }))
    assert saved["key"] == "验收来源" and saved["fields"]["contract_no"] == "合同编号"

    listing = data(client.get(BASE + "/sync/sources", headers=headers))
    assert "验收来源" in [i["key"] for i in listing["items"]]
    assert "验收来源" not in [i["key"] for i in data(client.get(BASE + "/sync/sources", headers=login("chenjing")))["items"]]
    assert client.get(BASE + "/sync/preview", headers=login("chenjing"), params={"source": "验收来源"}).status_code == 403
    assert set(listing["required"]) == {"contract_no", "customer_name", "amount", "sign_date", "business_line"}
