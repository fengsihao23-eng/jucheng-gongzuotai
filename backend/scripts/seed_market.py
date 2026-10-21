"""市场部 / 项目部演示数据：竞品与行业情报、项目复盘（2 定稿 + 1 初稿）、节点变更日志样例。

进展反馈（biz_project_feedback）由 seed_exec 写入，这里不重复。内容均为虚构的行业风格情报。
"""
from datetime import datetime, time, timedelta

from app.core.timeutil import TZ
from app.models import BizMilestoneLog, MktIntel
from app.services import market_service
from app.services import project_service as ps

# (相对天数, kind, competitor, title, summary, tags, importance, source_name)
INTEL = [
    (-2, "competitor", "希沃", "希沃发布新一代交互智能平板及教学大模型",
     "希沃在秋季发布会推出新一代交互平板，内置教学大模型助手，主打备课、授课、学情分析一体化，并宣布对广东区域经销商加大返点。",
     ["智慧教室", "大模型", "渠道政策"], 3, "厂商发布会"),
    (-5, "competitor", "科大讯飞教育", "讯飞中标某地市因材施教综合平台项目",
     "讯飞以约 2,800 万元中标粤西某地市因材施教综合平台，含学情诊断、个性化学习手册与教师研修模块，采用区域整体推进模式。",
     ["综合评价", "区域平台", "中标"], 3, "政府采购网"),
    (-8, "competitor", "海康威视教育", "海康推出校园安全一体化解决方案 3.0",
     "海康教育事业部发布校园安全一体化方案 3.0，整合周界防范、一键报警、明厨亮灶与宿舍管理，强调 AI 行为分析与平台开放接口。",
     ["校园安全", "安防", "AI 视频"], 2, "行业媒体"),
    (-11, "competitor", "大华", "大华在珠三角多所高校落地智慧安防平台",
     "大华以平台+前端打包方式在珠三角 3 所高校落地智慧安防，报价较同类方案低约 12%，并提供 3 年免费运维。",
     ["安防", "高校", "价格策略"], 2, "合作伙伴反馈"),
    (-13, "competitor", "锐捷教育", "锐捷发布校园网极简以太全光方案",
     "锐捷推出面向中小学的极简以太全光网络方案，主打弱电间减少 60%、运维人力减少，已在佛山多所学校试点。",
     ["数字校园", "校园网", "运维"], 2, "厂商官网"),
    (-16, "competitor", "天喻信息", "天喻信息中标某省级教育数字基座项目",
     "天喻信息中标某省教育数字基座二期，涉及统一身份认证、数据中台与应用商店，后续地市接入存在配套集成机会。",
     ["数字基座", "统一认证", "中标"], 2, "政府采购网"),
    (-19, "competitor", "正元智慧", "正元智慧推出一卡通与智慧后勤融合平台",
     "正元智慧发布一卡通与智慧后勤融合平台，覆盖消费、门禁、宿管与能耗，正在华南拓展职业院校客户。",
     ["智慧校园", "一卡通", "职业院校"], 1, "行业媒体"),
    (-22, "competitor", "希沃", "希沃与多区教育局签订教师数字素养培训合作",
     "希沃通过教师培训+设备捆绑方式与 2 个区教育局签订合作，预计带动后续智慧教室采购。",
     ["智慧教室", "教师培训"], 2, "客户拜访"),
    (-26, "competitor", "科大讯飞教育", "讯飞智慧体育考评系统进入广东多地中考体育",
     "讯飞智慧体育考评系统已在广东 3 个地市用于中考体育项目测试，具备 AI 计数与成绩自动上传能力。",
     ["体质", "考评", "中考体育"], 3, "客户拜访"),
    (-30, "competitor", "海康威视教育", "海康调整教育行业合作伙伴分级政策",
     "海康教育行业伙伴分级调整，金牌伙伴需年度教育销售额达到新门槛，项目报备保护期由 90 天调整为 60 天。",
     ["渠道政策", "安防"], 2, "合作伙伴通知"),
    (-34, "competitor", "大华", "大华明厨亮灶方案在深圳某区批量部署",
     "大华在深圳某区中小学批量部署明厨亮灶及食安监管平台，接入市场监管部门，单校造价约 6 万元。",
     ["明厨亮灶", "安防", "食品安全"], 2, "政府采购网"),
    (-40, "competitor", "锐捷教育", "锐捷云课堂进入粤北多县区薄弱校改造",
     "锐捷云课堂方案入选粤北县域薄弱学校改造项目，以云桌面+运维托管方式降低学校运维门槛。",
     ["数字校园", "云桌面", "运维"], 1, "行业媒体"),
    (-3, "policy", None, "教育部推进教育数字化战略行动年度工作要点",
     "要点提出深化国家智慧教育平台应用、推进区域教育数字化转型试点、加快数字基座与数据标准建设，鼓励以区域为单位整体规划。",
     ["教育数字化", "数字基座", "政策"], 3, "教育部官网"),
    (-9, "policy", None, "广东省发布中小学智慧校园建设指南（修订）",
     "指南细化基础设施、教学应用、校园安全、综合评价四类建设指标，明确 2027 年底前全省中小学智慧校园达标率目标。",
     ["智慧校园", "建设指南", "综合评价"], 3, "省教育厅"),
    (-15, "policy", None, "校园安全防控体系建设三年行动方案印发",
     "方案要求中小学实现封闭化管理、一键报警与公安联网、重点部位视频全覆盖，并纳入督导考核。",
     ["校园安全", "安防", "督导"], 3, "省教育厅"),
    (-28, "policy", None, "学生体质健康监测与评价工作通知",
     "通知要求各地完善学生体质健康监测机制，推广智能化测试设备，测试数据纳入综合素质评价。",
     ["体质", "综合评价", "政策"], 2, "省教育厅"),
    (-6, "bid", None, "增城区智慧教育云平台项目招标预告",
     "增城区教育局发布智慧教育云平台建设项目采购意向，预算约 560 万元，预计 11 月挂网，含平台、数据治理与 3 年运维。",
     ["数字校园", "招标预告", "云平台"], 3, "采购意向公开"),
    (-12, "bid", None, "珠海市属中学一键报警及周界防范项目公开招标",
     "珠海市教育局公开招标市属中学一键报警及周界防范项目，预算 680 万元，要求具备公安联网对接案例。",
     ["校园安全", "安防", "招标公告"], 3, "政府采购网"),
    (-20, "bid", None, "东莞市中考体育考评系统采购意向",
     "东莞市教育局发布中考体育考评系统采购意向，预算约 150 万元，要求 AI 计数与防作弊能力。",
     ["考评", "体质", "采购意向"], 2, "采购意向公开"),
    (-36, "bid", None, "佛山禅城区教育城域网升级项目结果公示",
     "佛山禅城区教育城域网升级项目中标结果公示，中标方为某运营商联合体，中标价较预算下浮 15%。",
     ["数字校园", "中标结果", "运维"], 1, "政府采购网"),
    (-18, "industry", None, "教育信息化行业三季度市场观察",
     "三季度教育信息化招标规模同比增长约 9%，其中校园安全与智慧教室占比提升，区域平台类项目集中度提高。",
     ["行业报告", "市场规模"], 1, "第三方研究机构"),
    (-45, "industry", None, "AI 大模型在 K12 教学场景的应用进展",
     "多家厂商推出教学大模型产品，集中在备课、作业批改与学情分析；地方教育局关注数据安全与内容合规要求。",
     ["大模型", "数据安全", "行业报告"], 2, "行业论坛"),
]

# (项目编码, 状态 1 初稿 2 定稿, 定稿补充经验)
REVIEWS = [
    ("JC-KP-2025-001", 2, "补充：艺术素养考评需提前与学校确认测评排期，避开期中考试周，避免现场测评窗口压缩。"),
    ("JC-AF-2026-004", 2, "补充：安防改造类项目需在深化设计阶段完成弱电管井与供电点位联合踏勘，减少后期返工。"),
    ("JC-KP-2026-002", 1, None),
]


def seed(db, ctx) -> None:
    T, U = ctx.T, ctx.users
    authors = [U["wujun"], U["zhengyu"], U["fengxue"], U["heyang"], U["luoqian"]]
    for i, (back, kind, comp, title, summary, tags, imp, src) in enumerate(INTEL):
        db.add(MktIntel(title=title, kind=kind, competitor=comp, summary=summary, tags=tags, importance=imp,
                        source_name=src, source_url=None, published_on=T + timedelta(days=back),
                        author_id=authors[i % len(authors)].id))
    db.flush()

    # 项目复盘：按规则生成初稿，2 篇人工定稿
    for code, status, extra in REVIEWS:
        proj = ctx.projects[code]
        r = market_service.generate_review(db, proj, author_id=U["wujun"].id)
        if status == 2:
            r.lessons = (r.lessons or "") + f"\n{len(r.lessons.splitlines()) + 1}. {extra}"
            r.status = 2
            r.tags = sorted(set(r.tags or []) | {"已定稿经验"})
    db.flush()

    # 节点变更日志样例（与当前数据一致：记录进展说明的补录过程，不改变节点现值）
    samples = [("JC-XY-2026-001", "xuhao", "web"), ("JC-AF-2026-001", "jiangtao", "mcp"),
               ("JC-XY-2026-002", "pengbo", "web"), ("JC-KP-2026-001", "xuhao", "mcp")]
    for code, uid, source in samples:
        proj = ctx.projects[code]
        for k, m in enumerate(sorted(proj.milestones, key=lambda x: x.seq)[:3]):
            after = ps._snap(m)
            before = {**after, "progress_note": None,
                      "status": 2 if m.status == 3 else after["status"], "finish_date": None if m.status == 3 else after["finish_date"],
                      "alert_flag": False if m.status == 3 else after["alert_flag"]}
            when = datetime.combine((m.finish_date or T - timedelta(days=3 + k)), time(17, 20 + k), TZ)
            db.add(BizMilestoneLog(project_id=proj.id, milestone_id=m.id,
                                   action="complete" if m.status == 3 else "update", before=before, after=after,
                                   operator_id=U[uid].id, source=source, created_at=when))
    db.flush()
