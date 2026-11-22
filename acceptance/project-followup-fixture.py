"""Install synthetic source rows into an already seeded, isolated browser-test database."""
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.timeutil import now
from app.models import ContractWorkbook, SysUser
from app.services import business_workbook, dict_service
from tests.test_contract_workbook import older, sales, sales_row, tender

KEY = "synthetic-followup-1122"


def sheets():
    current = sales(sales_row(3, "校园网络服务合同", "演示第一中学", 90000, raw_code="DEMO-001", received=30000),
                    sales_row(4, "教学终端维保合同", "演示实验学校", 12000, raw_code="DEMO-002", received=12000),
                    sales_row(5, "校园广播升级合同", "演示中心学校", 24000, raw_code="DEMO-003", received=0))
    past = older((3, "校园视频系统尾款", 46000, "2025-12-18", 18000, 28000))
    for sheet in (current, past):
        sheet["rows"][0]["cells"][10:13] = ["11月预计可回款金额", "11月回款进度", "12月预计可回款金额"]
    for row, values in zip(current["rows"][2:], [
        (60000, 15000, "11月11日已回款10000元", 6000),
        (0, 12000, "11月18日已回款12000元", None),
        (24000, None, "付款资料待确认", 8000),
    ]):
        row["cells"][9:13] = values
        row["cells"][3] = "演示负责人"
        row["cells"][7] = "2026-04-08"
    current["rows"].append({"row": 9, "cells": [None] * 10 + [27000, 22000, 14000]})
    past["rows"][2]["cells"][10:13] = [8000, "11月12日已收款3000元", 2000]
    past["rows"].append({"row": 9, "cells": [None] * 10 + [8000, 3000, 2000]})
    opp = {"id": "market", "name": "5、市场商机", "range": "A1:I5", "merges": [], "rows": [
        {"row": 1, "cells": ["市场商机"]},
        {"row": 2, "cells": ["工单编号", "工单模板", "联系人", "责任人", "协同人", "工单状态", "工单主题", "发起时间", "描述"]},
        {"row": 3, "cells": ["S-001", "校园服务", "校方代表", "演示负责人", "项目组", "已接受", "实验学校网络扩容", "2026-11-18", "确认教学楼的新接入需求。"]}]}
    pending = {"id": "pending", "name": "3、已建设或已中标待签合同", "range": "A1:H4", "merges": [], "rows": [
        {"row": 1, "cells": ["序号", "项目名称", "项目类型", "对接单位", "我方签约主体", "实际跟进人", "合同金额", "备注"]},
        {"row": 2, "cells": [1, "信息教室设备安装", "在建", "演示学校", "演示公司", "项目组", 25000, "教学楼现场实施"]}]}
    bid = tender()
    bid["rows"][1]["cells"][1:5] = ["演示负责人", "校园设备招标", "待开标", "2026-11-28"]
    return [current, past, opp, pending, bid]


if __name__ == "__main__":
    url = make_url(settings.database_url)
    assert settings.environment == "test" and url.host in ("localhost", "127.0.0.1") and url.database.startswith("jucheng_ui_")
    with SessionLocal.begin() as db:
        existing = business_workbook.configured_source(db)
        if existing:
            assert existing.source_key == KEY
        else:
            admin = db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin"))
            wb = ContractWorkbook(source_key=KEY, revision=KEY, checked_at=now(), synced_at=now(),
                                  config={"title": "项目跟进验收工作簿", "reader_id": admin.id, "enabled": True,
                                          "presentation_only": True, "profiles": {}, "view_user_ids": []},
                                  sheets=sheets(), summary={})
            db.add(wb)
            dict_service.put(db, business_workbook.CONFIG, "primary", {"enabled": True, "source_key": KEY})
