"""知识库 / 客服 / 标书助手演示数据。

- 乐享 Mock 全量同步（29 篇 → kb_document / kb_chunk）
- 客服字典 cs_category（分类关键词规则，可在线维护）
- 客服工单 60 条（近 90 天，含 24h 重复报修、已解决带根因与方案、聚类明显的高频问题）
- 标书样例：samples/tender_xx学校智慧校园.docx、bid_xx学校智慧校园.docx、material_xx学校现场勘察记录.docx
  入库后生成一次完整合规检查（5 轮）与一份实施方案、一份安装调试方案
- 上月客服归因报告一份
"""
import random
from datetime import datetime, time, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.timeutil import TZ, month_range, prev_month
from app.services import cs_service, dict_service, kb_service, tender_service
from scripts import knowledge_samples as ks

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"

# 簇：(权重, [(描述, 设备, 根因类型, 根因, 方案)])
CLUSTERS = [
    (14, [("{room}交互大屏开机显示无信号，老师无法上课", None, "操作", "老师误切到 HDMI 通道，OPS 未选中",
           "远程指导切换信号源至内置电脑（OPS），并在教室张贴大屏使用指引"),
          ("{room}班班通大屏没画面，提示 No Signal", None, "硬件", "OPS 电脑模块松动接触不良",
           "工程师上门拔插并加固 OPS 模块，开启 BIOS 来电自启动"),
          ("{room}交互大屏无信号，外接电脑投不上去", None, "配置", "外接电脑分辨率超出大屏支持范围",
           "将电脑分辨率调整为 1920×1080，更换 HDMI 线后恢复")]),
    (10, [("{area}监控摄像机离线，平台看不到画面", None, "网络", "交换机端口故障导致摄像机掉线",
           "更换交换机端口并重新压接水晶头，摄像机恢复在线"),
          ("{area}监控离线，回放发现昨晚录像丢失", None, "硬件", "NVR 硬盘坏道导致录像中断",
           "质保期内免费更换 NVR 硬盘，开启硬盘满循环覆盖"),
          ("{area}室外摄像头雨后离线", "摄像机", "硬件", "室外防水接头进水短路",
           "重新做防水处理并加装防雷器，设备恢复")]),
    (7, [("{room}校园广播没有声音，打铃不响", None, "配置", "节假日调整后作息表未更新",
          "远程修正定时打铃任务与作息表并重新下发"),
         ("{area}广播无声，其他区域正常", None, "网络", "该分区广播终端离线",
          "修复终端网线与 PoE 供电，终端恢复在线")]),
    (6, [("{area}门禁人脸识别失败，新生刷脸不开门", None, "配置", "新生人脸库未下发到门禁终端",
          "平台重新下发人脸库与通行权限"),
         ("{area}人脸门禁早上逆光识别失败", None, "硬件", "设备安装位置逆光",
          "调整宽动态参数并加装遮光罩")]),
    (6, [("老师登录素质评价平台提示未授权，无法录入评价", None, "配置", "教师调岗后角色权限未更新",
          "学校管理员更新教师任教班级与角色权限"),
         ("素质评价平台体测数据未同步，学生报告缺少成绩", None, "软件缺陷", "凌晨增量同步任务失败",
          "手动重跑同步任务并修复同步脚本超时问题")]),
    (5, [("{room}无线 AP 掉线，Wi-Fi 连不上", None, "网络", "PoE 交换机功率不足导致 AP 重启",
          "增加 PoE 供电模块，调整功率预算")]),
    (4, [("食堂消费机刷卡失败，提示网络异常", None, "网络", "消费机网络中断脱机",
          "恢复网络后上传脱机记录并核对流水"),
         ("学生饭卡充值后消费机余额不到账", None, "第三方", "圈存机与银行接口同步延迟",
          "联系银行核对后手工补录充值记录")]),
    (4, [("明厨亮灶后厨视频卡顿，就餐区大屏画面延迟", None, "网络", "食堂上行带宽不足",
          "优化码流为 H.264 2Mbps，并协助学校申请专线扩容")]),
]
ROOMS = ["高一（3）班", "初二（5）班", "三年级 2 班", "科学实验室", "录播教室", "六年级 1 班", "多功能厅"]
AREAS = ["东门", "操场", "宿舍楼", "教学楼 A 栋", "食堂", "图书馆", "南门"]
CUSTOMERS = ["南沙区中小学数字校园（南沙一中）", "东莞松山湖实验学校", "广东海洋大学", "江门新会区实验小学",
             "深圳宝安区西乡中学", "广州市天河区育才实验学校", "佛山市南海区桂城中学", "湛江市第二中学"]
HANDLERS = ["huangqi", "linna", "yaoyao", "yangyue"]


def _seed_tickets(db: Session, ctx) -> int:
    rnd = random.Random(20261020)
    T = ctx.T
    plan: list[tuple] = []
    for weight, variants in CLUSTERS:
        for i in range(weight):
            plan.append(variants[i % len(variants)])
    rnd.shuffle(plan)
    plan = plan[:56]
    entries = []
    for i, v in enumerate(plan):
        days_ago = 88 - int(i * 89 / len(plan))
        entries.append((days_ago, rnd.randint(8, 16), v, rnd.choice(CUSTOMERS), rnd.choice(ROOMS), rnd.choice(AREAS)))
    # 4 条 24h 内重复报修（同客户同症状，间隔 2~5 小时）
    for k in (5, 17, 33, 48):
        d, h, v, c, room, area = entries[k]
        entries.append((d, min(h + rnd.randint(2, 5), 20), v, c, room, area))
    entries.sort(key=lambda e: (-e[0], e[1]))
    users = ctx.users
    n = 0
    for days_ago, hour, v, cust, room, area in entries:
        desc, dev, rc_type, rc, sol = v
        text = desc.format(room=room, area=area)
        created = datetime.combine(T - timedelta(days=days_ago), time(hour, rnd.randint(0, 59)), TZ)
        handler = users.get(rnd.choice(HANDLERS))
        res = cs_service.register_ticket(db, fields={"customer_name": cust, "problem_desc": text, "device": dev,
                                                     "channel": rnd.choice(["电话", "电话", "微信", "企微"])},
                                         user=handler, created_at=created, with_suggestion=days_ago <= 20)
        t = db.get(cs_service.CsTicket, res["ticket"]["id"])
        if days_ago > 6 and not t.duplicate_of:
            t.status = 4 if days_ago > 40 else 3
            t.root_cause_type, t.root_cause, t.solution = rc_type, rc, sol
            t.resolved_at = created + timedelta(hours=rnd.choice([1.5, 3, 5, 8, 20, 30, 46]))
        elif days_ago > 2 and not t.duplicate_of:
            t.status = 2
        if t.duplicate_of:
            parent = db.get(cs_service.CsTicket, t.duplicate_of)
            if parent.status >= 3:
                t.status, t.root_cause_type, t.root_cause = 3, parent.root_cause_type, parent.root_cause
                t.solution, t.resolved_at = f"随首单 {parent.ticket_no} 一并解决：{parent.solution}", parent.resolved_at
        n += 1
    db.flush()
    return n


def _seed_tender(db: Session, ctx) -> dict:
    files = ks.write_samples(SAMPLES_DIR)
    owner = ctx.users.get("xiekai")
    uid = owner.id if owner else None
    tender = tender_service.upload_document(db, "tender", ks.TENDER_FILE, files[ks.TENDER_FILE].read_bytes(),
                                            ks.TENDER["title"], None, uid)
    bid = tender_service.upload_document(db, "bid", ks.BID_FILE, files[ks.BID_FILE].read_bytes(), ks.BID["title"], None, uid)
    material = tender_service.upload_document(db, "material", ks.MATERIAL_FILE, files[ks.MATERIAL_FILE].read_bytes(),
                                              ks.MATERIAL["title"], None, uid)
    chk = tender_service.start_check(db, tender.id, bid.id, uid)
    for ans in ["安防工程企业能力证书正在向综合部确认", "继续", "工期与质保期需与商务确认后调整", "继续", "确认"]:
        if chk.status == "done":
            break
        tender_service.reply(db, chk, ans)
    tender_service.export_check(db, chk, "xlsx", uid)
    g1 = tender_service.generate(db, "implementation", tender.id, [material.id], user_id=uid)
    tender_service.export_generation(db, g1, uid)
    tender_service.generate(db, "installation", tender.id, [material.id], user_id=uid)
    db.flush()
    return {"tender": tender.id, "bid": bid.id, "check": chk.id, "findings": len(chk.findings)}


def seed(db: Session, ctx) -> None:
    for cat, kws in cs_service.DEFAULT_CATEGORY_RULES.items():
        dict_service.put(db, "cs_category", cat, {"keywords": kws}, remark="客服工单自动分类关键词（命中关键词长度加权）")
    kb_service.sync(db, full=True)
    n = _seed_tickets(db, ctx)
    info = _seed_tender(db, ctx)
    period = prev_month(f"{ctx.T:%Y-%m}")
    s, e = month_range(period)
    cs_service.generate_report(db, s, e, period, trigger="schedule")
    db.flush()
    print(f"    knowledge: kb synced, tickets={n}, tender check findings={info['findings']}")
