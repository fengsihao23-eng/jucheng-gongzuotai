"""所属区域业绩判定：优先用源表原值，缺失时按客户名/合同名推断，推不出归「待定」。

判定顺序（越靠前越可信）：
1. 同一客户在台账里已有区域（人工填过的），直接沿用
2. 客户名里出现区县名（阳春/阳东/阳西/江城/海陵、闸坡）
3. 合同名里出现区县名（渠道类客户：移动、电信、中移建设等，一个客户跨多片区）
4. 客户名/合同名出现市直属特征（阳江市××、两阳中学、市实验学校、高级中学等）
5. 都没有 → 待定

推断结果一律在备注写明来源，方便业绩复核时反查。
"""
import re

REGIONS = ("市直", "江城", "阳春", "阳东", "阳西", "海陵湾", "待定")

DISTRICT_RULES = (
    ("海陵湾", ("海陵", "闸坡")),
    ("阳春", ("阳春",)),
    ("阳东", ("阳东",)),
    ("阳西", ("阳西",)),
    ("江城", ("江城",)),
)
CITY_PATTERN = re.compile(
    r"阳江市|市直|市第一|市第二|市第三|市实验|两阳中学|市教育局|市卫生|市公安局|市自然资源|"
    r"市体育|市妇幼|市民政|高级中学|阳江一中|阳江职院|职业技术学院")


def _district(text: str) -> str | None:
    for region, keywords in DISTRICT_RULES:
        if any(keyword in text for keyword in keywords):
            return region
    return None


def classify(customer: str | None, name: str | None = None) -> tuple[str, str | None]:
    """返回 (区域, 判定依据)；依据为 None 表示待定。"""
    customer, name = str(customer or ""), str(name or "")
    region = _district(customer)
    if region:
        return region, "客户名含区县"
    region = _district(name)
    if region:
        return region, "合同名含区县"
    if CITY_PATTERN.search(customer) or CITY_PATTERN.search(name):
        return "市直", "市直属特征"
    return "待定", None
