"""演示组织花名册（Mock 企微通讯录与种子数据同源）。姓名均为虚构。"""

DEPT_WECOM_IDS = {
    "ZB": (2, "总经办"), "CW": (3, "财务部"), "SC": (4, "市场部"), "YX": (5, "营销管控部"), "CP": (6, "产品中心"),
    "RS": (7, "人事部"), "XM": (8, "项目部"), "KF": (9, "客服部"), "ADMIN": (10, "系统管理"),
}

_R = [
    # userid, 姓名, 部门, 职位, 角色
    ("zhangzhenhua", "张振华", "ZB", "董事长", "ZB-01"),
    ("limingyuan", "李明远", "ZB", "总经理", "ZB-01"),
    ("wangting", "王婷", "ZB", "总经办专员", "ZB-02"),
    ("chenjing", "陈静", "CW", "财务经理", "CW-01"),
    ("zhaoli", "赵丽", "CW", "出纳", "CW-02"),
    ("sunyan", "孙燕", "CW", "会计", "CW-03"),
    ("zhoumin", "周敏", "CW", "会计", "CW-03"),
    ("wujun", "吴骏", "SC", "市场经理", "SC-01"),
    ("zhengyu", "郑宇", "SC", "市场专员", "SC-02"),
    ("fengxue", "冯雪", "SC", "市场专员", "SC-02"),
    ("heyang", "何洋", "SC", "市场专员", "SC-02"),
    ("luoqian", "罗倩", "SC", "市场专员", "SC-02"),
    ("liangwei", "梁伟", "YX", "营销管控主管", "YX-01"),
    ("songjia", "宋佳", "YX", "营销管控专员", "YX-01"),
    ("xiekai", "谢凯", "CP", "方案经理", "CP-01"),
    ("tanglei", "唐磊", "CP", "售前工程师", "CP-01"),
    ("hanxiao", "韩晓", "CP", "售前工程师", "CP-01"),
    ("caofang", "曹芳", "RS", "人事经理", "RS-01"),
    ("dengxin", "邓欣", "RS", "HR专员", "RS-01"),
    ("xuhao", "许浩", "XM", "项目经理", "XM-01"),
    ("pengbo", "彭博", "XM", "项目经理", "XM-01"),
    ("jiangtao", "蒋涛", "XM", "项目经理", "XM-01"),
    ("yuanhang", "袁航", "XM", "项目经理", "XM-01"),
    ("lufeng", "陆峰", "XM", "项目经理", "XM-01"),
    ("dingrui", "丁锐", "XM", "项目工程师", "XM-02"),
    ("weichen", "魏晨", "XM", "项目工程师", "XM-02"),
    ("shenkang", "沈康", "XM", "项目工程师", "XM-02"),
    ("yangyue", "杨悦", "KF", "客服主管", "KF-01"),
    ("huangqi", "黄琪", "KF", "客服专员", "KF-01"),
    ("linna", "林娜", "KF", "客服专员", "KF-01"),
    ("guojian", "郭健", "XM", "项目工程师", "XM-02"),
    ("majun", "马骏", "SC", "商务专员", "SC-02"),
    ("hufei", "胡斐", "CP", "售前工程师", "CP-01"),
    ("zhuyi", "朱怡", "RS", "招聘专员", "RS-01"),
    ("qinlang", "秦朗", "XM", "项目工程师", "XM-02"),
    ("yaoyao", "姚瑶", "KF", "客服专员", "KF-01"),
    ("liuchang", "刘畅", "YX", "数据专员", "YX-01"),
    ("xiaoyu", "肖羽", "CW", "出纳助理", "CW-02"),
    ("zengli", "曾立", "ZB", "行政专员", "EMP"),
    ("panhui", "潘慧", "XM", "资料员", "EMP"),
    ("jinbo", "金波", "SC", "市场助理", "EMP"),
    ("admin", "平台管理员", "ADMIN", "平台管理员", "ADMIN-01"),
    ("tianye", "田野", "XM", "项目工程师", "XM-02"),  # 已离职（通讯录同步后应自动禁用）
]

ROSTER = [
    {"userid": u, "name": n, "dept": d, "position": p, "role": r, "mobile": f"138{i:08d}", "left": u == "tianye"}
    for i, (u, n, d, p, r) in enumerate(_R, start=1)
]
