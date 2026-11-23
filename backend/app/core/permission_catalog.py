"""可配置的小项及操作归属；管理页面和运行时共用这份目录。"""

OPERATION_SECTIONS = {
    "tasks": "任务情况 / 月度任务 / 当前待办", "progress": "项目进度",
    "products": "设备产品库", "team": "项目团队", "outreach": "市场开拓",
    "procurement": "学校采购", "income": "收入情况", "device_maintenance": "设备运维",
    "arrears": "欠费维护", "collection_maintenance": "回款维护", "customers": "客户资料维护",
    "warehouse": "仓库物料", "scrap": "设备报废", "reports": "周报 / 月报",
    "funds": "经费与审批", "settings": "工作设置",
}

MENU_SECTIONS = {"operations": OPERATION_SECTIONS}

BUTTON_MENUS = {
    "portal": ["record:manage"],
    "operations": ["operations:manage", "operations:archive", "operations:approve"],
    "market": ["project:key"], "projects": ["milestone:write", "feedback:write", "project:weekly"],
    "reviews": ["review:write"], "intel": ["intel:write"],
    "contracts": ["contracts:write", "contracts:export", "contracts:archive"],
    "receivables": ["collections:request", "receivables:invoice", "receivables:receipt", "receivables:correct", "receivables:source"],
    "expenses": ["expense:allocate"], "flows": ["flows:import", "flows:match"],
    "journal": ["journal:write"], "finance_data": ["finance:sync", "tax:upload"],
    "profit": ["cost:write"], "reports": ["report:generate"], "board": ["board:target"],
    "opportunities": ["opp:write"], "submissions": ["submissions:remind"], "clean": ["clean:import"],
    "imports": ["imports:handle"], "tender": ["tender:write"], "kb": ["kb:manage"],
    "hr": ["hr:write", "hr:attendance"], "service": ["ticket:write"],
}


def permission_groups(menus, buttons):
    assigned = {button for items in BUTTON_MENUS.values() for button in items} | {"operations:view_all"}
    return [{"key": key, "label": label, "sections": MENU_SECTIONS.get(key, {}),
             "buttons": {button: buttons[button] for button in BUTTON_MENUS.get(key, []) if button in buttons}}
            for key, label in menus.items()] + (
        [{"key": None, "label": "其他操作", "sections": {},
          "buttons": {key: label for key, label in buttons.items() if key not in assigned and key != "*"}}]
        if set(buttons) - assigned - {"*"} else [])
