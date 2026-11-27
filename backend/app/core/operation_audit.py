"""Request context and durable audit metadata; row changes are captured by PostgreSQL."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import time
import uuid

from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.audit import mask
from app.models import AuditEvent, AuditSetting

log = logging.getLogger("operation_audit")
current = ContextVar("operation_audit", default=None)
AREAS = {"auth":"账号与登录", "admin":"系统管理", "files":"文件资料", "operations":"运营项目",
         "contracts":"经营业绩", "collections":"回款情况", "opportunities":"商机信息",
         "tender":"招标与在建", "board":"经营看板", "expenses":"报销归集", "flows":"流水核对",
         "journal":"出纳日记账", "finance_data":"财务数据", "reports":"报表中心", "receivables":"开票与收款",
         "projects":"项目台账", "market":"重点项目", "hr":"人事中心", "service":"客服工单", "kb":"知识库",
         "imports":"资料导入", "clean":"数据清洗", "notify":"消息通知", "notifications":"消息通知",
         "assistant":"小聚", "operation-records":"操作记录", "record-followups":"业务跟进", "submissions":"汇报提交",
         "source_checks":"数据源核对", "source-checks":"数据源核对", "wecom":"企业微信",
         "finance-data":"财务数据","finance-ledgers":"财务账表","performance":"业绩与运营","intel":"竞品情报","reviews":"项目复盘",
         "technical-orders":"技术部工单",
         "mcp_config":"工具接入","mcp-config":"工具接入"}


@dataclass
class Context:
    key: str = field(default_factory=lambda: uuid.uuid4().hex)
    source: str = "web"
    module: str = ""
    action: str = ""
    actor_id: int | None = None
    actor_name: str | None = None
    account: str | None = None
    notes: list = field(default_factory=list)
    started: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def sql_context(connection, ctx):
    values = {"event":ctx.key, "actor":str(ctx.actor_id or ""), "name":ctx.actor_name or "",
              "account":ctx.account or "", "source":ctx.source, "module":ctx.module, "action":ctx.action}
    expr = ", ".join(f"set_config('jc.audit_{key}',:{key},true)" for key in values)
    connection.execute(text("SELECT "+expr), values)


@event.listens_for(Session, "after_begin")
def transaction_context(session, transaction, connection):
    ctx = current.get()
    if ctx is not None and connection.dialect.name == "postgresql":
        sql_context(connection, ctx)


def bind_actor(db, user):
    ctx = current.get()
    if ctx and user:
        ctx.actor_id, ctx.actor_name, ctx.account = user.id, user.name, user.wecom_userid
        if db.in_transaction():
            sql_context(db.connection(), ctx)


def identify(request):
    """An invalid request body can fail before dependencies authenticate the session."""
    try:
        from app.core.deps import get_principal
        with SessionLocal() as db:
            get_principal(request,db)
    except Exception:
        pass  # Never turn a claimed/expired token into an authenticated actor.


def describe(path, method):
    parts = path.split("/")
    key = parts[3] if len(parts)>3 else ""
    if key == "business-workbook":
        key = parts[4] if len(parts)>4 else key
    area = AREAS.get(key,key or "工作台")
    if key.startswith("wecom"):
        area="企业微信"
    if key == "finance-ledgers" and path.endswith("/revoke"):
        return area, "查看批次撤销范围" if method == "GET" else "撤销整批导入"
    verb = {"GET":"查看", "POST":"提交", "PUT":"修改", "PATCH":"修改", "DELETE":"删除"}.get(method,method)
    if any(x in path for x in ("/export","/download")): verb = "导出/下载"
    elif "/login" in path: return area,"账号登录"
    elif "/logout" in path: return area,"退出登录"
    elif "/password" in path: return area,"修改密码"
    elif "/preferences" in path: return area,"查看个人设置" if method=="GET" else "修改个人设置"
    elif "/sync" in path: verb = "同步"
    elif "/import" in path or "/upload" in path: verb = "导入/上传"
    elif "/reply" in path: verb = "回复"
    elif "/progress" in path or "/feedback" in path: verb = "提交进展"
    return area, verb+area


def finish(ctx, *, outcome="success", status=None, duration=None, method=None, path=None, ip=None, detail=None):
    values = dict(event_key=ctx.key,actor_id=ctx.actor_id,actor_name=ctx.actor_name,account=ctx.account,
                  source=ctx.source,module=ctx.module,action=ctx.action,outcome=outcome,status_code=status,
                  method=method,path=path,client_ip=ip,duration_ms=duration,created_at=ctx.started,
                  detail=mask({**(detail or {}),"business_actions":ctx.notes}))
    try:
        _save(values)
    except Exception:
        from app.core.config import settings
        folder=settings.storage_dir.parent/"audit-spool"
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        target=folder/(ctx.key+".json")
        with target.open("w") as handle:
            __import__("os").fchmod(handle.fileno(),0o600)
            json.dump(values,handle,ensure_ascii=False,default=str)
        log.exception("Audit %s deferred to the local journal",ctx.key)


def _save(values):
    with SessionLocal() as db:
        # A caller can still hold an outer transaction/savepoint. Never wait on its audit stub.
        db.execute(text("SET LOCAL lock_timeout = '250ms'"))
        db.execute(text("SET LOCAL statement_timeout = '3000ms'"))
        stmt=insert(AuditEvent).values(**values)
        db.execute(stmt.on_conflict_do_update(index_elements=["event_key"],
            set_={k:v for k,v in values.items() if k not in {"event_key","created_at"}}))
        db.commit()


def install():
    from app.core.db import engine
    from app.core.audit_sql import install as install_capture
    with engine.begin() as connection:
        install_capture(connection)
        connection.execute(insert(AuditSetting).values(key="enabled",value={"since":datetime.now(timezone.utc).isoformat()})
                           .on_conflict_do_nothing(index_elements=["key"]))
    recover()


def recover(limit=100):
    from app.core.config import settings
    restored=0
    for path in sorted((settings.storage_dir.parent/"audit-spool").glob("*.json"))[:limit]:
        try:
            data=json.loads(path.read_text())
            data["created_at"]=datetime.fromisoformat(data["created_at"])
            _save(data)
            path.unlink()
            restored+=1
        except Exception:
            log.exception("Could not recover audit journal %s",path.name)
    return restored


def recorded(source):
    """Keep jobs and MCP tools distinct from their optional calling HTTP request."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args,**kwargs):
            parent = current.get()
            name = str(args[0] if args else kwargs.get("name",""))[:120]
            ctx = Context(source=source,module="系统任务" if source=="job" else "智能工具",
                          action=("执行任务：" if source=="job" else "调用工具：")+name)
            if parent:
                ctx.actor_id,ctx.actor_name,ctx.account=parent.actor_id,parent.actor_name,parent.account
            token=current.set(ctx)
            start=time.perf_counter()
            result=None
            outcome="failed"
            try:
                result=function(*args,**kwargs)
                if source=="job":
                    outcome="success" if result.status=="success" else "skipped" if result.status=="skipped" else "failed"
                    ctx.notes.append({"job":name,"trigger":result.trigger,"result":result.status,
                                      "rows":result.rows_affected,"message":result.message})
                else:
                    outcome="success" if json.loads(result).get("ok") else "failed"
                    for note in reversed(ctx.notes):
                        if note.get("tool")==name:
                            outcome={"ok":"success","error":"failed","denied":"denied","unauthorized":"denied",
                                     "rate_limited":"denied"}.get(note["outcome"],outcome)
                            break
                return result
            finally:
                try:
                    finish(ctx,outcome=outcome,duration=round((time.perf_counter()-start)*1000))
                except Exception:
                    log.exception("Could not persist task audit %s",ctx.key)
                current.reset(token)
        return wrapped
    return decorate
