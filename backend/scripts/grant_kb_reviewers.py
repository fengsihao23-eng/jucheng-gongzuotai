"""把「知识库审核人」角色（KB-01）开通给指定账号，并挂到各一级分类的审核人角色上。

KB-01 是附属角色：不增加菜单、不改变数据范围，只让本人成为知识库分类审核人（可审核发布、传新版）。
默认只预览；确认后加 --apply 执行。

    python -m scripts.grant_kb_reviewers            # 预览
    python -m scripts.grant_kb_reviewers --apply    # 执行
生产：cd /opt/jucheng/deploy && docker compose exec -T api python -m scripts.grant_kb_reviewers --apply
"""
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import KbCategory, SysRole, SysUser, SysUserRole
from app.services import kb_service

ROLE_CODE = "KB-01"

# 图中的各部门负责人 / 指定审核人，外加谢斌揆、冯思皓
DEFAULT_NAMES = [
    "梁道灿", "王丽倩",                    # 总经办
    "刘映君",                              # 运营中心
    "陈钰雯", "邱睦华",                    # 综合部
    "梁华健", "莫纯威",                    # 市场部
    "林辽记",                              # 技术部
    "翁金燕",                              # 客服部
    "张宇琦",                              # 产品中心
    "李画",                                # AI 数据部
    "周建勋",                              # 项目部
    "黄换星",                              # 财务部
    "何佩英", "黄子伦",                    # 营销管控
    "谢斌揆", "冯思皓",
]


def main(apply: bool, names: list[str]) -> None:
    db = SessionLocal()
    try:
        role = db.scalar(select(SysRole).where(SysRole.role_code == ROLE_CODE))
        # 只在在职用户里找人，离职的跳过并提示
        found, missing = [], []
        for name in names:
            rows = list(db.scalars(select(SysUser).where(SysUser.name == name, SysUser.status == 1)))
            if not rows:
                left = db.scalar(select(SysUser).where(SysUser.name == name, SysUser.status == 2))
                missing.append(f"{name}（{'已离职' if left else '库中不存在'}）")
            elif len(rows) > 1:
                missing.append(f"{name}（重名 {len(rows)} 个，需人工指定）")
            else:
                found.append(rows[0])

        with_role = [u for u in found if any(r.role_code == ROLE_CODE for r in u.roles)]
        need_grant = [u for u in found if u not in with_role]

        kb_service.ensure_categories(db)  # 分类表为空时先建默认树，避免下面查不到分类
        tops = list(db.scalars(select(KbCategory).where(KbCategory.parent_id.is_(None))
                               .order_by(KbCategory.sort, KbCategory.id)))
        need_cat = [c for c in tops if ROLE_CODE not in (c.reviewer_roles or [])]

        print(f"角色 {ROLE_CODE}：{'已存在' if role else '待创建'}")
        print(f"匹配到在职账号 {len(found)} 个，其中已有该角色 {len(with_role)} 个")
        for u in found:
            print(f"  {u.id:>4} {u.name:<6} 部门 {u.dept_code or '-':<10} 现有角色 "
                  f"{','.join(r.role_code for r in u.roles) or '无'}")
        if need_grant:
            print(f"待开通 {len(need_grant)} 人：{'、'.join(u.name for u in need_grant)}")
        if missing:
            print(f"未处理：{'；'.join(missing)}")
        print(f"待挂到分类的一级分类 {len(need_cat)} 个："
              f"{'、'.join(c.name for c in need_cat) or '无（已全部覆盖）'}")

        if not apply:
            print("\n预览完成，确认后加 --apply 执行。")
            return

        if role is None:
            role = SysRole(role_code=ROLE_CODE, name="知识库审核人", dept_code="*", permissions={})
            db.add(role)
            db.flush()
        for u in need_grant:
            db.add(SysUserRole(user_id=u.id, role_id=role.id))
        for c in need_cat:
            c.reviewer_roles = [*(c.reviewer_roles or []), ROLE_CODE]
        db.commit()
        print(f"\n已开通 {len(need_grant)} 人、挂载 {len(need_cat)} 个一级分类。")
    finally:
        db.close()


if __name__ == "__main__":
    main("--apply" in sys.argv, DEFAULT_NAMES)
