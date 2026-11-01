"""Excel 清洗：多行表头、合并分组行、客户/业务线自动补齐，以及 AI 表头映射回退。"""
import io

from openpyxl import Workbook
from sqlalchemy import select

from app.models import SysDict
from tests.conftest import data


def _book(rows: list[list], merge: str | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    if merge:
        ws.merge_cells(merge)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _preview(client, login, content: bytes, name: str):
    return data(client.post("/api/v1/clean/preview", headers=login("admin"),
                            files={"file": (name, content)}, data={"purpose": "导入合同台账"}))


def test_two_row_header_merged_band_and_derived_customer(client, login):
    """第 1/2 行是两行表头，第 3 行是合并的「已签约」分组行，不能被当成表头或数据。"""
    content = _book([
        ["序号", "项目/合同名称", "对接单位", "我方签约主体", "合同总金额", "应收", "应收", "签订时间", "备注"],
        ["序号", "项目/合同名称", "对接单位", "我方签约主体", "合同总金额", "已收", "未收", "签订时间", "备注"],
        ["已签约"] * 9,
        [1, "阳江数科与十贤科技关于三座岛屿6号信号覆盖项目合作配套服务协议", "数科", "十贤", 3312189, 1987314, 1324876, "2026-07-10", 1270000],
        [2, "阳江移动与广东聚成公司关于3座岛屿6号信号覆盖项目受托代销及合作服务协议", "移动", "聚成", 1785400, None, 1785400, "2026-08-13", 535000],
        [3, "阳江移动与广东十贤公司关于阳江3座岛屿123号信号覆盖设备租赁合同", "移动", "十贤", 5015336, None, 5015336, "2026-06-30", None],
        ["合计", "合计", None, None, 10112925, None, None, None, None],
        ["未签约"] * 9,
        [1, "阳江移动与广东聚成公司关于海上船舶网络覆盖保障项目", "移动", "聚成", 240000, None, None, None, None],
        ["共计", None, None, None, 10352925],
    ], merge="A3:I3")
    job = _preview(client, login, content, "风电项目合同.xlsx")
    stats = job["stats"]
    assert stats["header_row"] == 2 and stats["total"] == 3 and stats["importable"] == 3
    assert stats["error"] == 0 and not stats["missing_required_fields"]
    assert stats["ai_plan"] is None  # 规则已经足够，不必调用模型
    fields = {m["field"]: m for m in job["mapping"] if m.get("field")}
    assert fields["customer_name"]["col"] == 2 and fields["amount"]["col"] == 4
    assert fields["sign_date"]["col"] == 7 and "contract_no" not in fields
    assert [r["customer_name"] for r in job["rows"]] == ["阳江数科", "阳江移动", "阳江移动"]
    assert {r["business_line"] for r in job["rows"]} == {"AF"}
    assert stats["derived_fields"] == ["business_line", "customer_name"]
    reasons = {x["_row"]: x["reason"] for x in stats["skipped_rows"]}
    assert "分组/合并行" in reasons[3] and "未签约" in reasons[9]
    assert "合计" in reasons[7] and "合计" in reasons[10]


def test_ai_plan_takes_over_when_headers_are_opaque(client, login, monkeypatch):
    from app.services import smart_intake
    monkeypatch.setattr(smart_intake, "plan_table", lambda *a, **k: {
        "header_row": 1, "mapping": {0: "name", 1: "customer_name", 2: "amount", 3: "sign_date"},
        "customer_from_name": False, "business_line": "XY", "reason": "A列是项目名称，B列是客户"})
    content = _book([
        ["A列", "B列", "C列", "D列"],
        ["智慧校园一卡通项目", "阳江市实验学校", 128600, "2026-07-31"],
        ["校园安防升级项目", "电白某中学", 300000, "2026-08-15"],
    ])
    job = _preview(client, login, content, "乱表头.xlsx")
    stats = job["stats"]
    assert stats["ai_plan"]["used"] and stats["ai_plan"]["header_row"] == 1
    assert stats["importable"] == 2 and not stats["missing_required_fields"]
    assert all(m["method"] == "AI 判断" for m in job["mapping"] if m.get("field"))
    assert {r["business_line"] for r in job["rows"]} == {"XY"}


def test_mixed_business_lines_are_inferred_per_row_and_saved(client, login, db):
    from app.models import BizContract
    content = _book([
        ["合同名称", "客户名称", "合同金额", "签订日期"],
        ["逐行识别智慧校园合同", "逐行识别校园客户", 128601, "2026-10-20"],
        ["逐行识别园区安防合同", "逐行识别安防客户", 300001, "2026-10-20"],
    ])
    job = _preview(client, login, content, "混合业务合同.xlsx")
    assert [r["business_line"] for r in job["rows"]] == ["XY", "AF"]
    assert job["stats"]["importable"] == 2
    result = data(client.post(f"/api/v1/clean/jobs/{job['id']}/import", headers=login("admin")))
    assert result["stats"]["imported"] == 2
    saved = db.scalars(select(BizContract).where(BizContract.name.in_([
        "逐行识别智慧校园合同", "逐行识别园区安防合同"]))).all()
    assert {c.name: c.business_line for c in saved} == {
        "逐行识别智慧校园合同": "XY", "逐行识别园区安防合同": "AF"}


def test_unknown_row_does_not_inherit_other_rows_business_line(client, login):
    content = _book([
        ["合同名称", "客户名称", "合同金额", "签订日期"],
        ["逐行识别智慧校园合同B", "逐行识别校园客户B", 128602, "2026-10-20"],
        ["无法确定类型的采购合同", "逐行识别未知客户", 300002, "2026-10-20"],
    ])
    job = _preview(client, login, content, "缺少业务线合同.xlsx")
    assert job["rows"][0]["business_line"] == "XY"
    assert not job["rows"][1].get("business_line")
    assert job["rows"][1]["_status"] == "error"
    assert job["stats"]["importable"] == 1


def test_ai_file_hint_does_not_override_recognizable_row(client, login, monkeypatch):
    from app.services import smart_intake
    monkeypatch.setattr(smart_intake, "plan_table", lambda *a, **k: {
        "header_row": 1, "mapping": {0: "name", 1: "customer_name", 2: "amount", 3: "sign_date"},
        "business_line": "XY", "reason": "主要是数字校园合同"})
    job = _preview(client, login, _book([
        ["A列", "B列", "C列", "D列"],
        ["智慧校园采购合同", "AI逐行校园客户", 128603, "2026-10-20"],
        ["园区安防采购合同", "AI逐行安防客户", 300003, "2026-10-20"],
    ]), "AI混合合同.xlsx")
    assert job["stats"]["ai_plan"]["used"]
    assert [r["business_line"] for r in job["rows"]] == ["XY", "AF"]


def test_unfitted_table_is_automatically_moved_to_knowledge(client, login):
    """进度汇报/凭证这类表硬套台账会一行都导不进，应自动改道知识库，而不是让人卡在字段映射。"""
    content = _book([["项目", "进度情况", "下一步计划", "项目最终结果"],
                     ["workbuddy 项目", "已部署到云服务器", "梳理模块数据源接口", "待验收"]])
    r = client.post("/api/v1/imports", headers=login("admin"), data={"purpose": ""},
                    files={"file": ("项目台账-进度汇报.xlsx", content)})
    created = data(r)
    batch = data(client.get(f"/api/v1/imports/{created['id']}", headers=login("admin")))
    assert batch["route"] == "kb" and batch["status"] == "pending"
    assert batch["clean_job"] is None
    assert batch["suggestion"]["auto_rerouted"] is True
    assert "自动转为知识库" in batch["note"]


def test_stored_template_gets_new_synonyms_without_manual_migration(db):
    """生产库里的旧 clean_template 不能被代码升级后的新同义词绕过。"""
    from app.services import clean_service
    row = db.scalar(select(SysDict).where(SysDict.dict_type == "clean_template", SysDict.dict_key == "contract"))
    original = row.dict_value
    try:
        row.dict_value = {**original, "fields": [
            {**f, "synonyms": [s for s in (f.get("synonyms") or []) if s != "对接单位"]}
            if f.get("key") == "customer_name" else f for f in original["fields"]]}
        db.commit()
        tpl = clean_service.templates(db)["contract"]
        customer = next(f for f in tpl["fields"] if f["key"] == "customer_name")
        assert "对接单位" in customer["synonyms"]
    finally:
        row.dict_value = original
        db.commit()
