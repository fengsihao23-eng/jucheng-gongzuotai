"""从现有业务路由生成小聚能力目录，沿用菜单、按钮与服务内的数据权限。"""
import ast
import hashlib
import inspect
import textwrap
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from pydantic import create_model
from sqlalchemy import select

from app.core.deps import Principal
from app.core.response import forbidden, not_found
from app.models import McpTool

MODULES = {
    "portal": ("个人工作台", "/"), "board": ("经营看板", "/board"),
    "contracts": ("合同与回款", "/contracts"), "submissions": ("汇报与催交", "/submissions"),
    "expenses": ("报销归集", "/expenses"), "flows": ("银行流水核对", "/flows"),
    "journal": ("出纳日记账", "/journal"), "finance_data": ("财务数据与税务", "/finance-data"),
    "reports": ("报表与项目利润", "/reports"), "projects": ("项目与进度", "/projects"),
    "market": ("市场情报与复盘", "/market"), "opportunities": ("商机管理", "/opportunities"),
    "performance": ("业绩与运营", "/performance"), "clean": ("数据清洗导入", "/clean"),
    "hr": ("人事考勤与招聘", "/hr"), "service": ("客服工单", "/service"),
    "tender": ("标书与方案", "/tender"), "kb": ("知识库", "/kb"),
    "admin": ("系统管理", "/admin"), "notify": ("我的通知", "/notifications"),
    "mcp_config": ("公司工具接入", "/workbuddy"),
    "wecom_cli": ("企业微信个人连接", "/workbuddy"),
    "imports": ("资料导入与处理进度", "/imports"),
}
LABELS = {
    "list_contracts": "查询合同", "create": "新增记录", "update": "更新记录", "detail": "查看详情",
    "options": "查询选项与人员编码", "export": "导出文件", "archive": "归档合同原件", "save_plans": "维护回款计划",
    "add_payment": "登记合同回款", "list_expenses": "查询报销单", "stats": "查询汇总", "allocate": "归集报销项目",
    "import_flow": "导入银行流水", "workbench": "查询待核对流水", "suggestions": "查询核对建议",
    "candidates": "查询候选记录", "match": "确认流水匹配", "unmatch": "撤销流水匹配", "auto_match": "批量核对流水",
    "generate": "生成报告", "regenerate_all": "更新全部报表", "types": "查询可生成报表类型", "profit": "查询项目利润",
    "profit_trend": "查询利润趋势", "profit_project": "查询项目利润明细", "versions": "查询历史版本",
    "overview": "查询概览", "compare": "经营指标对比", "get_targets": "查询回款目标", "set_targets": "设置回款目标",
    "morning": "生成经营晨报预览", "summary": "查询统计摘要", "remind": "发送汇报催交通知", "sync": "同步外部数据",
    "list_projects": "查询项目", "create_project": "新建项目", "generate_weekly": "生成项目周报", "export_weekly": "导出项目周进度",
    "set_key": "标记重点项目", "milestones": "查询项目节点", "add_ms": "新增项目节点", "edit_ms": "更新项目节点",
    "del_ms": "删除项目节点", "reorder": "调整节点顺序", "move": "移动节点", "milestone_action": "查询或更新项目进度",
    "feedback": "登记项目进展反馈", "list_intel": "查询竞品情报", "create_intel": "登记竞品情报", "update_intel": "更新情报",
    "get_intel": "读取情报", "delete_intel": "删除情报", "intel_brief": "生成情报简报", "generate_review": "生成项目复盘",
    "update_review": "编辑复盘", "get_review": "读取复盘", "export_review": "导出复盘", "funnel": "查询商机漏斗",
    "advance": "推进商机阶段", "contract_draft": "生成合同草稿", "history": "查询流转历史", "owners": "查询人员业绩",
    "trend": "查询趋势", "board": "查询运营看板", "preview": "预览数据清洗结果", "remap": "调整数据列映射", "import_": "导入已清洗数据",
    "report": "生成清洗报告", "cancel": "取消清洗任务", "employees": "查询员工", "import_attendance": "导入考勤",
    "recheck": "重新核对考勤", "crosscheck": "核对人员与考勤一致性", "exceptions": "查询考勤异常", "analysis_ppt": "生成人力分析 PPT",
    "alerts": "查询到期预警", "alerts_scan": "扫描人事预警", "alert_handle": "处理人事预警", "doc_generate": "生成人事文书草稿",
    "doc_pack": "生成入职材料包", "doc_minutes": "生成沟通纪要", "risk_check": "检查文书风险", "recruit_jd": "生成岗位说明",
    "recruit_screen": "整理简历与岗位要求的匹配信息", "cand_stage": "更新候选人阶段", "cand_interview": "生成面试资料",
    "cand_offer": "生成录用文书草稿", "qa": "回答员工制度问题", "my_profile": "查询我的人事信息", "register": "登记客服工单",
    "tickets": "查询工单", "ticket": "查看工单详情", "set_status": "更新状态", "refresh_suggestion": "生成排查建议",
    "adopt": "标记辅助回复采纳", "attribution": "分析客服归因", "make_report": "生成客服分析报告", "upload": "上传业务资料",
    "create_text": "创建文本资料", "start_check": "开始标书合规检查", "reply": "补充合规检查回答", "bid_check": "执行标书合规检查",
    "checks": "查询合规检查", "export_check": "导出检查报告", "export_generation": "导出生成方案", "library": "查询售前资料库",
    "search": "检索知识库", "doc": "读取文档", "docs": "查询文档", "categories": "查询知识分类", "users": "查询账号",
    "roles": "查询角色", "set_roles": "调整账号角色", "update_role": "调整角色权限", "upsert_dict": "维护系统字典",
    "jobs": "查询任务", "run_job": "运行后台任务", "audit": "查询操作审计", "approvals": "查询审批归集队列", "retry_approval": "重试审批登记",
    "addressbook_sync": "同步企业通讯录", "my_notifications": "查询我的通知", "mark_read": "标记通知已读", "patch_tool": "调整工具授权",
    "company_directory_status": "检查公司通讯录授权状态", "preview_addressbook": "预览真实公司人员与账号变更",
    "batches": "查询本人资料导入进度", "batch": "查看资料识别与入库状态", "confirm_kb": "确认资料分类与可见范围并提交知识库",
    "to_clean": "准备资料台账清洗预览", "to_kb": "将资料改为知识库文档", "to_pool": "交给授权整理人处理",
    "dismiss": "标记资料已处理但不入库", "withdraw": "撤回资料与关联入库结果", "retry": "重新识别未完成的资料",
    "doc_chunks": "分页读取知识文档完整正文与出处",
    "analyze": "用小聚整理资料并准备具体导入清单",
    "decision": "确认或拒绝小聚已整理的具体导入清单",
    "limits": "读取资料上传和压缩包限额",
    "wecom_status": "检查本人企微资料接入状态", "wecom_search": "搜索本人的企微微盘和在线资料",
    "wecom_preview": "读取企微原件或在线正文生成本人预览", "wecom_source": "查询本人企微读取进度",
    "wecom_confirm": "确认保存企微快照并进入资料审核或台账清洗",
}
MCP_IMPLS = {
    "fin.daily_expense_report": "t_daily_expense", "fin.project_profit": "t_project_profit",
    "fin.bank_flow_sync": "t_bank_flow_sync", "oa.pending_approvals": "t_pending_approvals",
    "crm.contract_register": "t_contract_register", "board.executive_daily": "t_executive_daily",
    "board.compare": "t_compare", "kb.search": "t_kb_search", "kb.bid_check": "t_bid_check",
    "hr.attendance_summary": "t_attendance", "proj.milestone_update": "t_milestone",
    "cs.ticket_register": "t_ticket", "market.weekly_report": "t_market_weekly",
}


def _expr(node, p, bindings):
    if isinstance(node, ast.Constant): return node.value
    if isinstance(node, ast.Name): return bindings.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "p" and node.attr == "is_admin": return p.is_admin
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "p" and node.func.attr in {"can", "has_menu"}:
        key = _expr(node.args[0], p, bindings) if node.args else None
        return getattr(p, node.func.attr)(key) if isinstance(key, str) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _expr(node.operand, p, bindings); return not value if isinstance(value, bool) else None
    if isinstance(node, ast.BoolOp):
        values = [_expr(item, p, bindings) for item in node.values]
        if isinstance(node.op, ast.Or): return True if True in values else False if all(v is False for v in values) else None
        return False if False in values else True if all(v is True for v in values) else None
    return None


@lru_cache(maxsize=300)
def _body(func):
    try: return ast.parse(textwrap.dedent(inspect.getsource(func))).body[0].body
    except (OSError, TypeError, SyntaxError): return []


def _source_allowed(func, p, bindings=None, depth=0):
    if depth > 2: return True
    bindings = bindings or {}
    for statement in _body(func):
        if isinstance(statement, ast.If) and any(isinstance(item, ast.Raise) for item in statement.body):
            if _expr(statement.test, p, bindings) is True: return False
        call = statement.value if isinstance(statement, ast.Expr) else None
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id.startswith("_"):
            helper = func.__globals__.get(call.func.id)
            if inspect.isfunction(helper) and helper.__module__.startswith("app.api."):
                params = list(inspect.signature(helper).parameters)
                args = {name: _expr(value, p, bindings) for name, value in zip(params, call.args) if name != "p"}
                if not _source_allowed(helper, p, args, depth + 1): return False
    return True


def _guards(dependency):
    groups, buttons = [], []
    def walk(item):
        if inspect.isfunction(item.call):
            closure = inspect.getclosurevars(item.call).nonlocals
            if "menus" in closure: groups.append(tuple(closure["menus"]))
            if "button" in closure: buttons.append(closure["button"])
        for child in item.dependencies: walk(child)
    walk(dependency)
    return groups, buttons


def resolve(schema, components, seen=()):
    if isinstance(schema, list): return [resolve(v, components, seen) for v in schema]
    if not isinstance(schema, dict): return schema
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return {} if name in seen else resolve(components.get(name, {}), components, (*seen, name))
    return {key: resolve(value, components, seen) for key, value in schema.items() if key not in {"examples"}}


def _effect(module, method, path, name):
    if method == "GET": return "query"
    if method == "DELETE": return "confirm"
    if module == "imports" and name in {"wecom_search", "wecom_preview"}: return "query"
    if module == 'imports' and name in {'upload', 'analyze'}: return 'execute'
    if module in {"admin", "mcp_config", "contracts", "journal", "expenses", "finance_data", "imports"}: return "confirm"
    if module == "flows": return "confirm"
    if path.endswith("/remind") or (module == "hr" and ("/rule" in path or "/template" in path or "/alerts/scan" in path or "/candidates/" in path and path.endswith("/stage"))): return "confirm"
    if module == "clean" and path.endswith("/import"): return "confirm"
    if name in {"qa", "risk_check"}: return "query"
    return "execute"


@dataclass
class Operation:
    id: str
    module: str
    title: str
    source: str
    effect: str
    path: str = ""
    method: str = ""
    page: str = ""
    schema: dict = field(default_factory=dict)
    route: Any = None
    groups: list = field(default_factory=list)
    buttons: list = field(default_factory=list)
    mcp_code: str = ""
    impl: Any = None
    params_model: Any = None
    cli_method: str = ""
    connection_id: str = ""

    def allowed(self, p, mcp_rows):
        if any(not any(p.has_menu(menu) for menu in group) for group in self.groups): return False
        if any(not p.can(button) for button in self.buttons): return False
        if self.route and not _source_allowed(self.route.endpoint, p): return False
        if self.mcp_code:
            row = mcp_rows.get(self.mcp_code)
            if not row or not row.enabled: return False
            roles = set(row.allowed_roles or [])
            return "*" in roles or p.is_admin or bool(roles & set(p.role_codes)) or p.dept_code in roles
        return True

    def brief(self):
        return {"operation_id": self.id, "title": self.title, "area": MODULES.get(self.module, (self.module,))[0],
                "effect": self.effect, "needs_confirmation": self.effect == "confirm", "page": self.page,
                "supports_files": self.schema.get("content_type") == "multipart/form-data"}


@lru_cache(maxsize=1)
def operations():
    from app.main import app
    from app.mcp_server import server
    routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/v1/")
              and r.endpoint.__module__.rsplit(".", 1)[-1] in MODULES
              and not any(r.path.endswith(suffix) for suffix in ("/token/reveal-config", "/mcp-config/test", "/mcp-config/config"))]
    docs = get_openapi(title="小聚业务", version="1", routes=routes)
    components = docs.get("components", {}).get("schemas", {})
    result = {}
    for route in routes:
        module = route.endpoint.__module__.rsplit(".", 1)[-1]
        for method in sorted(route.methods):
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}: continue
            data = docs["paths"][route.path][method.lower()]
            op_id = f"{module}.{route.name}"
            if op_id in result: op_id += "." + hashlib.sha256((method + route.path).encode()).hexdigest()[:8]
            content = data.get("requestBody", {}).get("content", {})
            content_type = next(iter(content), "application/json")
            params = {"path": {"type": "object", "properties": {}}, "query": {"type": "object", "properties": {}}}
            for parameter in data.get("parameters", []):
                location = parameter["in"]
                if location not in params: continue
                params[location]["properties"][parameter["name"]] = resolve(parameter["schema"], components)
                if parameter.get("required"): params[location].setdefault("required", []).append(parameter["name"])
            for part in params.values(): part["additionalProperties"] = False
            schema = {**params, "body": resolve(content.get(content_type, {}).get("schema", {}), components),
                      "body_required": data.get("requestBody", {}).get("required", False), "content_type": content_type}
            groups, buttons = _guards(route.dependant)
            title = MODULES[module][0] + " · " + LABELS.get(route.name, "查询" if method == "GET" else "执行操作")
            page = MODULES[module][1]
            if route.path.startswith("/api/v1/reports/profit"): page = "/profit"
            elif route.path.startswith("/api/v1/market/intel"): page = "/intel"
            elif route.path.startswith("/api/v1/market/reviews"): page = "/reviews"
            result[op_id] = Operation(op_id, module, title, "api", _effect(module, method, route.path, route.name),
                                     route.path, method, page, schema, route, groups, buttons)
    mcp_modules = {"fin": "reports", "oa": "portal", "crm": "contracts", "board": "board", "kb": "kb", "hr": "hr", "proj": "projects", "cs": "service", "market": "market"}
    for code, function_name in MCP_IMPLS.items():
        impl = getattr(server, function_name)
        fields = {name: (param.annotation if param.annotation is not inspect.Parameter.empty else Any,
                         param.default if param.default is not inspect.Parameter.empty else ...)
                  for name, param in inspect.signature(impl).parameters.items() if name not in {"db", "p"}}
        params_model = create_model("Xiaoju_" + code.replace(".", "_"), **fields)
        module = mcp_modules[code.split(".")[0]]
        effect = "confirm" if code in {"fin.bank_flow_sync", "crm.contract_register", "proj.milestone_update"} else "query" if code in {"oa.pending_approvals", "board.compare", "kb.search"} else "execute"
        op_id = "mcp." + code
        result[op_id] = Operation(op_id, module, server.TOOL_META[code]["name"], "mcp", effect,
                                  page=MODULES[module][1], schema={"path": {"type": "object", "additionalProperties": False},
                                  "query": {"type": "object", "additionalProperties": False}, "body": params_model.model_json_schema(),
                                  "content_type": "application/json", "body_required": bool(params_model.model_json_schema().get("required"))},
                                  mcp_code=code, impl=impl, params_model=params_model)
    result["attachments.preview"] = Operation("attachments.preview", "portal", "预览本人上传的业务文件", "attachment", "query", page="/",
        schema={"path": {"type": "object", "additionalProperties": False}, "query": {"type": "object", "additionalProperties": False},
                "body": {"type": "object", "properties": {"file_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}}, "required": ["file_id"], "additionalProperties": False},
                "content_type": "application/json", "body_required": True})
    return result


def available(p, db):
    rows = {row.tool_code: row for row in db.scalars(select(McpTool))}
    result = {key: op for key, op in operations().items() if op.allowed(p, rows)}
    from app.services import wecom_cli_service
    result.update(wecom_cli_service.operations(p.id))
    return result


def get(operation_id, p, db):
    permitted = available(p, db)
    op = permitted.get(operation_id) or operations().get(operation_id)
    if not op: raise not_found("业务功能")
    if operation_id not in permitted: raise forbidden("当前账号无权执行该业务功能")
    return op
