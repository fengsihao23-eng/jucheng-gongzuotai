"""导入各领域任务模块以完成注册。新增领域任务：新建 app/jobs/<domain>.py 并加入 DOMAIN_MODULES。"""
import importlib

DOMAIN_MODULES = ["core", "finance_ops", "reports", "exec", "market", "hr", "knowledge", "marketing"]


def load_all() -> None:
    for m in DOMAIN_MODULES:
        importlib.import_module(f"app.jobs.{m}")
