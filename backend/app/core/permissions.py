"""RBAC 定义（部门 × 角色）。菜单键与前端 src/config/menu.js 一一对应。

data_scope：self 本人 / dept 本部门 / all 全公司。
portal：门户卡片方案键，见 sys_dict(portal_cards)。
"""

MENUS = {
    "portal": "工作台", "board": "经营看板", "reports": "报表中心",
    "contracts": "合同台账", "opportunities": "商机信息", "performance": "业绩与运营",
    "expenses": "报销归集", "flows": "流水核对", "journal": "出纳日记账", "finance_data": "财务数据",
    "profit": "项目利润", "submissions": "汇报提交", "projects": "项目流程", "market": "市场跟进",
    "intel": "竞品情报", "reviews": "项目复盘", "hr": "人事中心", "service": "客服工单",
    "tender": "标书助手", "kb": "知识库", "clean": "数据清洗", "mcp": "WorkBuddy 接入", "admin": "系统管理",
}

# 全员可见
PUBLIC_MENUS = ["portal", "kb", "mcp"]

FINANCE_MENUS = ["expenses", "flows", "journal", "finance_data", "profit", "reports"]
LEDGER_MENUS = ["contracts", "opportunities", "performance"]

ROLE_DEFS: dict[str, dict] = {
    "ZB-01": {"name": "董事长/总经理", "dept": "ZB", "portal": "exec", "data_scope": "all",
              "menus": ["board", "reports", *LEDGER_MENUS, *FINANCE_MENUS, "submissions", "projects", "market",
                        "intel", "reviews", "hr", "service", "tender"],
              "buttons": ["board:target", "contracts:export", "report:generate"]},
    "ZB-02": {"name": "总经办专员", "dept": "ZB", "portal": "exec_staff", "data_scope": "all",
              "menus": ["board", "reports", "contracts", "opportunities", "performance", "submissions", "projects", "market"],
              "buttons": ["board:target", "submissions:remind", "report:generate", "contracts:export"]},
    "CW-01": {"name": "财务经理", "dept": "CW", "portal": "finance", "data_scope": "all",
              "menus": [*FINANCE_MENUS, "contracts", "board"],
              "buttons": ["expense:allocate", "flows:import", "flows:match", "journal:write", "finance:sync",
                          "tax:upload", "cost:write", "report:generate", "contracts:archive", "contracts:export"]},
    "CW-02": {"name": "出纳", "dept": "CW", "portal": "cashier", "data_scope": "all",
              "menus": ["flows", "journal", "expenses", "reports"],
              "buttons": ["flows:import", "flows:match", "journal:write"]},
    "CW-03": {"name": "会计", "dept": "CW", "portal": "finance", "data_scope": "all",
              "menus": [*FINANCE_MENUS, "contracts"],
              "buttons": ["expense:allocate", "finance:sync", "tax:upload", "cost:write", "report:generate"]},
    "SC-01": {"name": "市场经理", "dept": "SC", "portal": "market", "data_scope": "all",
              "menus": ["market", "projects", "intel", "reviews", "reports", "contracts", "opportunities", "submissions"],
              "buttons": ["intel:write", "review:write", "feedback:write", "report:generate", "project:key"]},
    "SC-02": {"name": "市场专员", "dept": "SC", "portal": "market", "data_scope": "all",
              "menus": ["market", "projects", "intel", "reviews", "reports", "opportunities"],
              "buttons": ["intel:write", "feedback:write", "review:write"]},
    "YX-01": {"name": "营销管控专员", "dept": "YX", "portal": "marketing", "data_scope": "all",
              "menus": [*LEDGER_MENUS, "clean", "reports", "projects"],
              "buttons": ["contracts:write", "contracts:export", "contracts:archive", "opp:write", "clean:import"]},
    "CP-01": {"name": "售前工程师/方案经理", "dept": "CP", "portal": "product", "data_scope": "dept",
              "menus": ["tender", "projects"], "buttons": ["tender:write"]},
    "RS-01": {"name": "HR", "dept": "RS", "portal": "hr", "data_scope": "all",
              "menus": ["hr", "submissions"], "buttons": ["hr:write", "hr:attendance", "submissions:remind"]},
    "XM-01": {"name": "项目经理", "dept": "XM", "portal": "pm", "data_scope": "self",
              "menus": ["projects"], "buttons": ["milestone:write", "feedback:write", "project:weekly"]},
    "XM-02": {"name": "项目工程师", "dept": "XM", "portal": "pm", "data_scope": "self",
              "menus": ["projects"], "buttons": ["feedback:write"]},
    "KF-01": {"name": "客服专员", "dept": "KF", "portal": "service", "data_scope": "dept",
              "menus": ["service"], "buttons": ["ticket:write"]},
    "EMP": {"name": "普通员工", "dept": "*", "portal": "employee", "data_scope": "self", "menus": [], "buttons": []},
    "ADMIN-01": {"name": "平台管理员", "dept": "ADMIN", "portal": "admin", "data_scope": "all",
                 "menus": list(MENUS), "buttons": ["*"]},
}

# 首次登录按部门映射默认角色（FR-01-06）
DEPT_DEFAULT_ROLE = {"ZB": "ZB-02", "CW": "CW-03", "SC": "SC-02", "YX": "YX-01", "CP": "CP-01",
                     "RS": "RS-01", "XM": "XM-02", "KF": "KF-01", "ADMIN": "ADMIN-01"}

DEPARTMENTS = [
    ("ZB", "总经办"), ("CW", "财务部"), ("SC", "市场部"), ("YX", "营销管控部"), ("CP", "产品中心"),
    ("RS", "人事部"), ("XM", "项目部"), ("KF", "客服部"), ("ADMIN", "系统管理"),
]

BUSINESS_LINES = {"XY": "数字校园", "AF": "综合智慧安防", "KP": "素质教育考评", "YW": "运维服务"}

COMPANY = {
    "name": "广东聚成数智科技有限公司", "short": "聚成数智", "en": "Ju Cheng Technology",
    "intro": "专注智慧教育信息化，聚焦数字校园、综合智慧安防、素质教育考评三大核心业务，提供全流程解决方案。"
             "国家高新技术企业，通过 ISO 质量、环境、职业健康安全三体系认证，AAA 级信用企业；"
             "中国移动政企金牌伙伴、海康威视广东战略合作伙伴，建有广东海洋大学科产教融合实习基地。",
}
