"""Tests for natural-language performance report builder, collection execution dialog API, and stage report archiving."""
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from app.models import (
    BizContract,
    BizPayment,
    SysNotify,
    SysUser,
    WorkbookCollectionProgress,
    WorkbookCollectionReport,
    WorkbookCollectionRequest,
)
from app.services import collection_followups as tasks
from tests.conftest import data
from tests.test_business_workbook import sales_row, source_only  # noqa: F401
from tests.test_contract_workbook import older, sales

CONTRACTS_REPORT_API = "/api/v1/business-workbook/contracts/report-query"
COLLECTIONS_BASE = "/api/v1/business-workbook/collections"


@pytest.fixture
def rich_workbook(db, source_only, monkeypatch):
    wb, _ = source_only
    monkeypatch.setenv("FAKE_TODAY", "2026-11-18")
    person = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xiekai"))

    r1 = sales_row(3, "智慧校园一期项目", "阳江第一中学", 500000, received=200000)
    r1["cells"][3], r1["cells"][7], r1["cells"][9], r1["cells"][18] = person.name, "2026-05-10", 300000, "江城区"
    r1["cells"][10], r1["cells"][11] = 150000, "本月预计回15万"

    r2 = sales_row(4, "医疗信息化平台", "阳春人民医院", 800000, received=800000)
    r2["cells"][3], r2["cells"][7], r2["cells"][9], r2["cells"][18] = "陈华东", "2026-08-20", 0, "阳春市"
    r2["cells"][10], r2["cells"][11] = None, None

    r3 = sales_row(5, "政务云扩容服务", "市政务数管局", 300000, received=50000)
    r3["cells"][3], r3["cells"][7], r3["cells"][9], r3["cells"][18] = person.name, "2026-11-12", 250000, "江城区"
    r3["cells"][10], r3["cells"][11] = 100000, "审批走流程中"

    cur_sheet = sales(r1, r2, r3)
    cur_sheet["rows"][0]["cells"][10] = "11月预计可回款金额"
    cur_sheet["rows"][0]["cells"][11] = "11月回款进度"
    cur_sheet["rows"].append({"row": 8, "cells": [None] * 10 + [250000, None]})

    past_sheet = older(
        (3, "2025年安防监控改造", 400000, "2025-10-15", 100000, 300000),
        (4, "2024年网络维保结清", 120000, "2024-06-01", 120000, 0),
    )
    past_sheet["rows"][0]["cells"][10] = "11月预计可回款金额"
    past_sheet["rows"][0]["cells"][11] = "11月回款进度"
    past_sheet["rows"][2]["cells"][10] = 80000
    past_sheet["rows"][2]["cells"][11] = "2026年11月10日已回款20000元"
    past_sheet["rows"].append({"row": 8, "cells": [None] * 10 + [80000, None]})

    wb.sheets = [cur_sheet, past_sheet]
    wb.revision = "rev-101"
    db.commit()
    yield wb, person
    db.execute(delete(WorkbookCollectionReport).where(WorkbookCollectionReport.workbook_id == wb.id))
    db.execute(delete(WorkbookCollectionProgress).where(WorkbookCollectionProgress.request_id.in_(
        select(WorkbookCollectionRequest.id).where(WorkbookCollectionRequest.workbook_id == wb.id)
    )))
    db.execute(delete(SysNotify).where(SysNotify.ref_type == tasks.REF_TYPE))
    db.execute(delete(WorkbookCollectionRequest).where(WorkbookCollectionRequest.workbook_id == wb.id))
    db.commit()


def test_performance_natural_language_report_queries(client, login, rich_workbook):
    _, person = rich_workbook
    admin = login("admin")
    assert client.post(CONTRACTS_REPORT_API, json={"question": "统计各区域合同金额"}, headers=login("xiekai")).status_code == 403

    # 1. Group by region
    by_region = data(client.post(CONTRACTS_REPORT_API, json={"question": "统计各区域合同金额", "year": "2026"}, headers=admin))
    assert by_region["mode"] == "grouped"
    assert Decimal(by_region["summary"]["amount"]) == Decimal("1600000")
    assert {r["group_name"]: Decimal(r["amount"]) for r in by_region["rows"]} == {
        "江城区": Decimal("800000"),
        "阳春市": Decimal("800000"),
    }
    assert "1,600,000.00" in by_region["answer"]

    # 2. Group by owner
    by_owner = data(client.post(CONTRACTS_REPORT_API, json={"question": "按负责人统计签约业绩", "year": "2026"}, headers=admin))
    assert by_owner["mode"] == "grouped"
    assert {r["group_name"]: r["count"] for r in by_owner["rows"]} == {person.name: 2, "陈华东": 1}

    # 3. Unpaid contract list
    unpaid = data(client.post(CONTRACTS_REPORT_API, json={"question": "查询未收款合同清单", "year": "2026"}, headers=admin))
    assert unpaid["mode"] == "list"
    assert unpaid["summary"]["count"] == 2
    assert [r["name"] for r in unpaid["rows"]] == ["智慧校园一期项目", "政务云扩容服务"]
    assert Decimal(unpaid["summary"]["outstanding"]) == Decimal("550000")

    # 4. Monthly trend & top customer & threshold
    by_month = data(client.post(CONTRACTS_REPORT_API, json={"question": "统计各月合同签约金额"}, headers=admin))
    assert by_month["mode"] == "grouped" and [r["group_name"] for r in by_month["rows"]] == ["2026-05", "2026-08", "2026-11"]

    top_cust = data(client.post(CONTRACTS_REPORT_API, json={"question": "按客户统计合同金额前2名"}, headers=admin))
    assert top_cust["mode"] == "grouped" and len(top_cust["rows"]) == 2 and top_cust["rows"][0]["group_name"] == "阳春人民医院"

    high_unpaid = data(client.post(CONTRACTS_REPORT_API, json={"question": "查询未收款大于28万的合同"}, headers=admin))
    assert high_unpaid["mode"] == "list" and [r["name"] for r in high_unpaid["rows"]] == ["智慧校园一期项目"]


def test_collection_item_execution_tracking_and_problem_feedback(db, client, login, rich_workbook, monkeypatch):
    wb, person = rich_workbook
    admin = login("admin")
    monkeypatch.setattr(tasks, "deliver", lambda ids: None)
    before_counts = [db.scalar(select(func.count()).select_from(m)) for m in (BizContract, BizPayment)]

    exec_url = f"{COLLECTIONS_BASE}/items/sales-sheet/3/execution"
    initial = data(client.get(exec_url, params={"revision": wb.revision}, headers=admin))
    assert initial["contract"]["name"] == "智慧校园一期项目"
    assert initial["status_label"] == "待登记跟进"
    assert initial["can_feedback"] is True
    assert len(initial["plans"]) == 1 and Decimal(initial["plans"][0]["plan_amount"]) == Decimal("150000")

    # Draft polish endpoint works deterministically without LLM
    draft = data(client.post(f"{COLLECTIONS_BASE}/items/sales-sheet/3/draft", json={"text": "已对接学校总务处下周付款"}, headers=admin))
    assert draft["text"].endswith("。")

    # Blocking stage requires problem description
    assert client.post(exec_url, json={
        "revision": wb.revision,
        "stage": "blocked",
        "has_problem": True,
        "content": "卡在验收环节",
    }, headers=admin).status_code == 400

    # Submit execution feedback with problem
    saved = data(client.post(exec_url, json={
        "revision": wb.revision,
        "stage": "contact",
        "has_problem": True,
        "content": "已与校方核对账目，财政支付令待补材料",
        "problems": "缺少终验签字盖章单原件，需工程部本周三前配合补齐",
        "next_step": "联系工程部张工补签验收单",
        "expected_on": "2026-11-28",
        "expected_progress_id": initial["latest_progress_id"],
    }, headers=admin))
    assert saved["has_problem"] is True
    assert saved["status_tone"] == "down"
    assert saved["latest"]["stage"] == "blocked"
    assert "终验签字盖章单" in saved["latest"]["problems"]
    assert saved["latest"]["expected_on"] == "2026-11-28"
    assert len(saved["updates"]) == 1

    # Stale expected_progress_id is rejected
    assert client.post(exec_url, json={
        "revision": wb.revision,
        "stage": "approval",
        "content": "旧版本并发提交",
        "expected_progress_id": 999999,
    }, headers=admin).status_code == 409

    # Collection list & inline monthly period list reflect latest execution & problem badge
    listed = data(client.get(COLLECTIONS_BASE, params={"unpaid_only": True}, headers=admin))
    row3 = next(r for r in listed["items"] if r["sheet_id"] == "sales-sheet" and r["row"] == 3)
    assert row3["followup"]["has_problem"] is True
    assert row3["followup"]["stage"] == "blocked"

    period_list = data(client.get(f"{COLLECTIONS_BASE}/periods/2026-11", headers=admin))
    p_row3 = next(r for r in period_list["items"] if r["sheet_id"] == "sales-sheet" and r["row"] == 3)
    assert p_row3["followup"]["has_problem"] is True

    # Financial tables remain untouched
    assert [db.scalar(select(func.count()).select_from(m)) for m in (BizContract, BizPayment)] == before_counts


def test_collection_summary_report_generation_and_archiving(client, login, rich_workbook, monkeypatch):
    wb, person = rich_workbook
    admin = login("admin")
    monkeypatch.setattr(tasks, "deliver", lambda ids: None)

    # Record one blocked item and one normal approval item
    client.post(f"{COLLECTIONS_BASE}/items/sales-sheet/3/execution", json={
        "revision": wb.revision,
        "stage": "blocked",
        "has_problem": True,
        "content": "对接财务科等待补验收单",
        "problems": "需工程部补齐验收报告",
        "next_step": "周四前递交补件",
    }, headers=admin)
    client.post(f"{COLLECTIONS_BASE}/items/sales-sheet/5/execution", json={
        "revision": wb.revision,
        "stage": "approval",
        "content": "客户内部OA审批已到分管领导",
        "expected_on": "2026-11-25",
    }, headers=admin)

    # Generate stage summary
    summary = data(client.post(f"{COLLECTIONS_BASE}/summary-report", json={
        "scope": "all",
        "focus_prompt": "重点关注存在问题的项目",
    }, headers=admin))
    assert summary["facts"]["tracked_count"] == 2
    assert summary["facts"]["problem_count"] == 1
    assert "智慧校园一期项目" in summary["text"]
    assert "需工程部补齐验收报告" in summary["text"]
    assert "政务云扩容服务" in summary["text"]
    assert "330,000.00" in summary["text"]  # 2026-11 planned total: 250k + 80k

    # Filter only problems scope
    only_prob = data(client.post(f"{COLLECTIONS_BASE}/summary-report", json={"scope": "problems"}, headers=admin))
    assert only_prob["total_items"] == 1 and only_prob["items"][0]["name"] == "智慧校园一期项目"

    # Archive V1 & V2 and read back
    v1 = data(client.post(f"{COLLECTIONS_BASE}/saved-reports", json={
        "title": "11月中旬回款跟进阶段总结",
        "scope": "all",
        "text": summary["text"] + "\n（主管审阅签字留档）",
    }, headers=admin))
    assert v1["version"] == 1 and "主管审阅签字留档" in v1["text"]

    v2 = data(client.post(f"{COLLECTIONS_BASE}/saved-reports", json={
        "title": "11月中旬回款跟进阶段总结（更新版）",
        "scope": "all",
    }, headers=admin))
    assert v2["version"] == 2

    archives = data(client.get(f"{COLLECTIONS_BASE}/saved-reports", headers=admin))["items"]
    assert [a["version"] for a in archives] == [2, 1]
    loaded_v1 = data(client.get(f"{COLLECTIONS_BASE}/saved-reports/{v1['id']}", headers=admin))
    assert "主管审阅签字留档" in loaded_v1["text"]
