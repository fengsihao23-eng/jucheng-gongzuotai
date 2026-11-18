"""The approved selection changes collection contract amounts only."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from app.core.response import BizError
from app.services import business_workbook as source, collection_amounts as amounts, contract_workbook_sync as sync, collection_plans
from tests.conftest import data
from tests.test_business_workbook import source_only, sales_row  # noqa: F401
from tests.test_contract_workbook import sales, older

BASE = "/api/v1/business-workbook"


@pytest.fixture
def reviewed(db, source_only, monkeypatch):
    wb, principal = source_only
    monkeypatch.setenv("FAKE_TODAY", "2026-11-18")
    past = older((3, "同项目金额链", 800, "2024-04-24", None, 100),
                 (4, "同项目金额链", 800, "2024-04-24", 500, 300))
    past["rows"][2]["cells"][5] = "上游主体"
    past["rows"][3]["cells"][5] = "光海公司"
    for row in past["rows"][2:]:
        row["cells"][0] = 7
    wb.sheets = [sales(sales_row(3, "2026合同", "客户", 200, received=20)), past]
    wb.config = {**wb.config, "docid": "confirmed-test"}
    db.commit()
    sheet = source.collection_sheets(wb)[1]
    by_row = {r["row"]: r for r in sheet.items}
    def record(number):
        r = by_row[number]
        return {"row":number,"cell":f"G{number}","project":r["name"],"party":r["party"],"source_amount":r["amount"]}
    review = {"scope":"collections.contract_total_only","base_revision":wb.revision,
              "source_sheet":sheet.name,"source_sheet_id":sheet.id,"keep":[record(4)],"exclude_amount_only":[record(3)]}
    return wb, principal, review


def test_collection_summary_details_and_other_views_keep_independent_measures(db,client,login,reviewed):
    wb, _, review = reviewed
    headers = login("admin")
    saved = json.dumps(wb.sheets, sort_keys=True)
    contracts = data(client.get(BASE+"/contracts", params={"year":"2024"}, headers=headers))
    current = data(client.get(BASE+"/collections", headers=headers))
    assert current["summary"]["amount"]["value"] == "1800.00"
    wb.config = {**wb.config,amounts.CONFIG:amounts.build_policy(wb,review)}
    db.commit()
    result = data(client.get(BASE+"/collections",headers=headers))
    assert result["summary"]["amount"]["value"] == "1000.00"
    assert result["summary"]["amount"]["excluded_count"] == 1
    assert result["summary"]["outstanding"] == current["summary"]["outstanding"]
    assert result["summary"]["received"] == current["summary"]["received"]
    assert result["total"] == current["total"]
    excluded = next(r for r in result["items"] if r["sheet_id"]=="older" and r["row"]==3)
    assert excluded["amount"] is None and excluded["amount_source"]=="800.00"
    assert excluded["amount_text"]==amounts.NOTE
    detail = data(client.get(BASE+"/collections/items/older/3",headers=headers))
    assert detail["contract"]["amount"] is None and detail["contract"]["amount_note"]==amounts.NOTE
    assert data(client.get(BASE+"/contracts",params={"year":"2024"},headers=headers))["summary"]==contracts["summary"]
    db.refresh(wb)
    assert json.dumps(wb.sheets,sort_keys=True)==saved


def test_record_identity_survives_moves_and_amount_updates_and_detects_ambiguity(db,reviewed):
    wb, _, review=reviewed
    wb.config={**wb.config,amounts.CONFIG:amounts.build_policy(wb,review)}
    old=deepcopy(wb.sheets)
    historical=old[1]
    historical["rows"][2]["row"],historical["rows"][3]["row"]=12,11
    historical["rows"][2]["cells"][6]=900
    historical["rows"][3]["cells"][6]=950
    wb.sheets,wb.revision=old,"2"
    db.commit()
    rows=source.module_rows(wb,"collections")
    assert next(r for r in rows if r["sheet_id"]=="older" and r["row"]==12)["amount"] is None
    assert next(r for r in rows if r["sheet_id"]=="older" and r["row"]==11)["amount"]=="950.00"
    unknown=deepcopy(old)
    row=deepcopy(unknown[1]["rows"][2]);row["row"]=13;row["cells"][5]="新增未核对主体"
    unknown[1]["rows"].append(row)
    wb.sheets=unknown;db.commit()
    rows=source.module_rows(wb,"collections")
    assert next(r for r in rows if r["row"]==13)["collection_amount_state"]=="review"
    assert amounts.summary(source.collection_sheets(wb)[0],rows)["amount"]["value"] is None
    wb.config={**wb.config,"docid":"other-source"}
    with pytest.raises(BizError):
        source.module_rows(wb,"collections")


def test_policy_apply_requires_exact_review_and_rebinding_keeps_it(db,reviewed,monkeypatch):
    wb,principal,review=reviewed
    broken=deepcopy(review);broken["keep"][0]["source_amount"]="801.00"
    with pytest.raises(BizError,match="金额已变化"):
        amounts.build_policy(wb,broken)
    policy=amounts.build_policy(wb,review)
    wb.config={**wb.config,amounts.CONFIG:policy}
    wb.config={**wb.config,"format":"workbook","docid":"confirmed-test","binding_id":"same"}
    db.commit()
    monkeypatch.setattr(sync.cli,"_owned",lambda _:{"binding_id":"same"})
    sync.configure(db,wb.source_key,title="重存配置",url="https://doc.weixin.qq.com/sheet/confirmed-test",
                   reader_id=principal.id)
    assert wb.config[amounts.CONFIG]==policy
