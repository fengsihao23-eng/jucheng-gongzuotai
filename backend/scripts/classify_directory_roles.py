"""按真实组织架构给公司通讯录成员分角色。

规则：先按部门名称定基准角色，再看岗位是否属于管理岗（主管/经理/总监）升一级；
财务部按出纳/会计/主管单独判断；岗位含「助理」不升级（避免总经理助理拿到总经理权限）。
其他部门（行政、采购、后勤、饭堂、资产经营等）没有对应业务模块，保留普通员工。

用法：
    python -m scripts.classify_directory_roles            # 只预览，不改数据
    python -m scripts.classify_directory_roles --apply    # 实际写入
"""
import sys
from collections import Counter

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import SysDepartment, SysUser, SysUserRole
from app.services.user_service import role_by_code

MARKER = "company_directory"

# 部门关键字 → (基准角色, 管理岗角色)
DEPT_RULES = [
    ("总经办", "ZB-02", "ZB-01"),
    ("财务", "CW-03", "CW-01"),
    ("营销管控", "YX-01", "YX-01"),
    ("市场", "SC-02", "SC-01"),
    ("客服", "KF-01", "KF-01"),
    ("产品", "CP-01", "CP-01"),
    ("人事", "RS-01", "RS-01"),
    ("项目", "XM-02", "XM-01"),
    ("技术服务", "XM-02", "XM-01"),
    ("工程", "XM-02", "XM-01"),
    ("智慧校园", "XM-02", "XM-01"),
    ("一键报警", "XM-02", "XM-01"),
    ("英语听说", "XM-02", "XM-01"),
    ("体育综合", "XM-02", "XM-01"),
    ("分部", "XM-02", "XM-01"),
]

MANAGER_WORDS = ("主管", "经理", "总监", "负责人", "组长")


def pick_role(dept_name: str, position: str, current: set[str]) -> tuple[str, str]:
    """返回 (角色代码, 判定理由)。"""
    if any(code.startswith("ADMIN") for code in current):
        return "", "平台管理员，跳过"
    name = dept_name or ""
    pos = position or ""
    if "文员" in pos:
        return "EMP", "文员岗，无业务模块"
    for keyword, base, manager in DEPT_RULES:
        if keyword not in name:
            continue
        if keyword == "财务":
            if "出纳" in pos:
                return "CW-02", "财务部·出纳"
            if any(w in pos for w in MANAGER_WORDS):
                return "CW-01", "财务部·管理岗"
            return base, "财务部·会计"
        if keyword == "总经办":
            if "助理" in pos:
                return base, "总经办·助理"
            if any(w in pos for w in ("总经理", "董事长", "总监")):
                return manager, "总经办·负责人"
            return base, "总经办"
        if keyword == "分部" and "市场" in pos:
            return "SC-01" if "经理" in pos else "SC-02", f"{name}·市场岗"
        if any(w in pos for w in MANAGER_WORDS) and "助理" not in pos:
            return manager, f"{name}·管理岗"
        return base, name
    if any(w in pos for w in ("技术工程师", "售后", "工程师")):
        return "XM-02", f"{name or '未分配部门'}·技术岗"
    return "EMP", f"{name or '未分配部门'}·无对应业务模块"


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    db = SessionLocal()
    try:
        departments = {d.id: d.name for d in db.scalars(select(SysDepartment))}
        users = db.scalars(select(SysUser).order_by(SysUser.department_id, SysUser.id)).all()
        targets = [u for u in users if (u.preferences or {}).get(MARKER)]
        plans, unmatched, unchanged = [], [], 0
        for user in targets:
            current = {r.role_code for r in user.roles}
            code, reason = pick_role(departments.get(user.department_id, ""), user.position or "", current)
            if not code:
                continue
            if code == "EMP":
                unmatched.append(user)
            if current == {code}:
                unchanged += 1
                continue
            plans.append((user, code, reason))
            print(f"  {user.name:<6}{(departments.get(user.department_id, '-')):<10}{(user.position or '-'):<22}"
                  f"{'/'.join(sorted(current)) or '-':<8}→ {code}")

        per_role = Counter(code for _, code, _ in plans)
        print()
        for code, count in per_role.most_common():
            print(f"{code:<8}{role_by_code(db, code).name:<16}{count}")
        print(f"\n待调整 {len(plans)} 人，已符合 {unchanged} 人，共 {len(targets)} 人")
        if unmatched:
            print(f"\n保留普通员工 {len(unmatched)} 人（无对应业务模块，可在系统管理手工指定）：")
            for u in unmatched:
                print(f"  {u.name}｜{departments.get(u.department_id, '-')}｜{u.position or '-'}")
        if not apply:
            print("\n预览模式，未写入。加 --apply 执行。")
            return 0

        for user, code, _ in plans:
            role = role_by_code(db, code)
            db.query(SysUserRole).filter(SysUserRole.user_id == user.id).delete()
            db.add(SysUserRole(user_id=user.id, role_id=role.id))
        db.commit()
        print(f"\n已写入 {len(plans)} 人的角色。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
