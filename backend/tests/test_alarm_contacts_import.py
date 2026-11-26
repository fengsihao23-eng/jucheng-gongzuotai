"""Verify the school contact workbook lands in the alarm customer ledger."""
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.core.deps import build_principal
from app.core.response import BizError
from app.models import OpsImportBatch, SysUser
from app.services.alarm_contacts_import import import_workbook
from tests.conftest import data

BASE = "/api/v1/operations"


def workbook(*rows):
    book = Workbook()
    book.active.title = "通讯录"
    book.active.append(("姓名*", "所属单位*", "手机号", "性别", "地址", "职务"))
    for row in rows:
        book.active.append(row)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def test_import_preserves_multiple_contacts_and_records_remain_editable(client, login, db):
    admin = login("admin")
    principal = build_principal(db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin")))
    project = data(client.post(BASE, headers=admin, json={"name": "一键报警", "owner_id": principal.id}))
    project_id = project["id"]
    endpoint = f"{BASE}/{project_id}/maintenance"
    first = ("示例联系人甲", "示例学校", 13900000001, "女", "示例地址甲", "校长")
    second = ("示例联系人乙", "示例学校", "139 0000 0001", "", "示例地址乙", "教务负责人")
    payload = workbook(first, second, first)

    with pytest.raises(BizError, match="第 3 行手机号无效"):
        import_workbook(db, principal, project_id, "错误.xlsx", workbook(first, (*second[:2], "无效", *second[3:])))
    db.rollback()
    assert db.scalar(select(OpsImportBatch.id).where(OpsImportBatch.project_id == project_id)) is None

    report = import_workbook(db, principal, project_id, "示例学校.xlsx", payload)
    db.commit()
    assert (report["created"], report["organizations"], report["duplicates"]) == (2, 1, 1)
    assert import_workbook(db, principal, project_id, "重复文件.xlsx", payload)["already_imported"]
    db.rollback()
    overlap = import_workbook(db, principal, project_id, "重叠.xlsx", workbook(second))
    assert overlap["created"] == 0 and overlap["duplicates"] == 1
    db.rollback()

    def customers():
        return data(client.get(endpoint, headers=admin, params={"kind": "customers"}))["items"]

    records = {row["contact_name"]: row for row in customers()}
    assert len(records) == 2 and {row["phone"] for row in records.values()} == {"13900000001"}
    row = records[first[0]]
    assert (row["customer_name"], row["contact_gender"], row["address"], row["contact_position"]) == (
        first[1], first[3], first[4], first[5])
    assert "workbook_row" not in row
    body = {key: row[key] for key in ("kind", "title", "customer_name", "contact_name", "address", "owner_id", "phone")}
    updated = f"{endpoint}/{row['id']}"
    data(client.put(updated, headers=admin, json={**body, "expected_revision": row["revision"], "content": "已核对资料"}))
    updated_row = next(item for item in customers() if item["id"] == row["id"])
    assert updated_row["contact_position"] == first[5] and updated_row["contact_gender"] == first[3]
    data(client.put(updated, headers=admin, json={**body, "expected_revision": updated_row["revision"],
                                                 "contact_position": "分管负责人", "contact_gender": ""}))
    latest = data(client.get(updated + "/history", headers=admin))["items"][0]
    assert latest["contact_position"] == "分管负责人" and latest["contact_gender"] == ""
    assert client.get(endpoint, headers=login("xiekai"), params={"kind": "customers"}).status_code in (403, 404)
