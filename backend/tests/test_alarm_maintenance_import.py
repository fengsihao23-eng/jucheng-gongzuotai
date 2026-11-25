"""Alarm workbook mapping, reconciliation and maintenance permissions."""
from datetime import date
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.core.deps import build_principal
from app.core.response import BizError
from app.models import OpsImportBatch, OpsMaintenanceRecord, SysUser
from app.services.alarm_maintenance_import import import_workbook
from tests.conftest import data

BASE = "/api/v1/operations"


def workbook(total=310.03):
    book = Workbook()
    sites = book.active
    sites.title = "全量"
    for cell, label in zip("ABCDEFGHIJKLMNOPQR", (
        "(返回顶部)", "序", "区域", "学校名称(全市全量）", "计费\n个数", "学校名称(中心校）（隐藏", "其他名称",
        "撤校情况", "结果判定", "拆机日期", "摄像头IP", "录像机IP", "报警盒IP", "配套", "摄像头状态",
        "录像机状态", "报警盒状态", "离线数量",
    ), strict=True):
        sites[cell + "1"] = label
    for cell, label in {
        "AD": "计费月份\n(移动合同次月)", "AE": "销账月份", "AF": "每月应交额", "AG": "应缴月份",
        "AH": "销账金额", "AI": "减免金额", "AJ": "累计金额", "AK": "已缴月份", "AL": "应补缴月份",
        "AM": "补缴未销账", "AN": "损失", "BA": "应补缴金额",
    }.items():
        sites[cell + "1"] = label
    for cell, value in {
        "A2": "移动总体", "B2": 1, "C2": "验收区域", "D2": "验收学校", "E2": 3, "F2": "验收中心校",
        "G2": "验收学校别名", "I2": "已安装", "K2": "192.0.2.10", "O2": "在线", "P2": "离线", "Q2": "#N/A",
        "R2": 1, "AD2": date(2023, 6, 1), "AE2": date(2026, 9, 1), "AF2": 280, "AG2": 39,
        "AH2": 0, "AJ2": "#VALUE!", "AK2": 41, "AL2": -2, "AN2": 999, "BA2": 0,
        "A3": "移动总体", "B3": 2, "C3": "验收区域", "D3": "验收学校分点", "E3": 1, "H3": "撤校",
        "I3": "撤校", "J3": date(2024, 12, 4), "R3": 2, "AE3": date(2026, 9, 1), "BA3": 840,
    }.items():
        sites[cell] = value
    annual = book.create_sheet("年度取数")
    annual["H1"] = total
    for cell, name in zip("GHIJKL", ("日期", "月份", "对方", "摘要", "借方", "所属"), strict=True):
        annual[cell + "2"] = name
    for index, amount in enumerate((200.01, 110.02), start=3):
        for cell, value in zip("GHIJKL", (date(2026, 2, 14), "2月", "验收单位", None, amount, "移动"), strict=True):
            annual[cell + str(index)] = value
    annual["S5"] = 310.03
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_alarm_workbook_import_and_permissions(client, login, db):
    admin = login("admin")
    actor = build_principal(db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin")))
    target = data(client.post(BASE, headers=admin, json={"name": "一键报警", "owner_id": actor.id}))
    path = f"{BASE}/{target['id']}/maintenance"
    manual = data(client.post(path, headers=admin, json={
        "kind": "arrears", "title": "既有跟进", "amount": "77.00", "owner_id": actor.id,
    }))
    payload = workbook()

    with pytest.raises(BizError, match="本年实际到账"):
        import_workbook(db, actor, target["id"], "核对.xlsx", workbook(total=500))
    db.rollback()
    assert db.scalar(select(OpsImportBatch).where(OpsImportBatch.project_id == target["id"])) is None

    result = import_workbook(db, actor, target["id"], "验收.xlsx", payload)
    db.commit()
    assert result["rows"] == {"device_maintenance": 2, "arrears": 2, "collection_maintenance": 2}
    assert result["arrears_due"] == "840.00"
    assert result["receipts"] == "310.03"
    assert import_workbook(db, actor, target["id"], "同一文件.xlsx", payload)["already_imported"]
    db.rollback()
    assert len(db.scalars(select(OpsMaintenanceRecord).where(OpsMaintenanceRecord.project_id == target["id"])).all()) == 7

    def items(kind, headers=admin):
        return data(client.get(path, params={"kind": kind}, headers=headers))["items"]

    devices = sorted(items("device_maintenance"), key=lambda row: row["workbook_row"]["row_number"])
    assert devices[0]["workbook_row"]["values"]["alarm_box_status"] == "#N/A"
    assert devices[0]["workbook_row"]["values"]["camera_ip"] == "192.0.2.10"
    assert devices[1]["workbook_row"]["values"]["removal_date"] == "2024-12-04"
    debts = items("arrears")
    assert next(row for row in debts if row["id"] == manual["id"])["amount"] == "77.00"
    school_debt = next(row for row in debts if row["title"] == "验收学校")
    values = school_debt["workbook_row"]["values"]
    assert values["arrears_months"] == -2 and values["writeoff_month"] == "2026-09"
    assert values["cumulative_amount"] == "#VALUE!" and school_debt["amount"] == "0.00"
    receipts = items("collection_maintenance")
    assert len(receipts) == 2 and {row["amount"] for row in receipts} == {"200.01", "110.02"}
    assert receipts[0]["workbook_row"]["values"]["receipt_date"] == "2026-02-14"

    row = devices[0]
    body = {"kind": row["kind"], "title": row["title"], "owner_id": actor.id,
            "content": "验收跟进", "expected_revision": row["revision"]}
    data(client.put(f"{path}/{row['id']}", headers=admin, json=body))
    updated = next(r for r in items("device_maintenance") if r["id"] == row["id"])
    assert updated["workbook_row"] == row["workbook_row"]
    assert data(client.get(f"{path}/{row['id']}/history", headers=admin))["items"][0]["content"] == "验收跟进"
    outsider = login("xiekai")
    assert client.get(path, params={"kind": "device_maintenance"}, headers=outsider).status_code in (403, 404)
