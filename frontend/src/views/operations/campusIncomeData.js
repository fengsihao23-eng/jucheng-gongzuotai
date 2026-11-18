// 智慧校园收入情况：按 2026-11-13 工作簿的显示精度整理。
// 五张来源表各自保留统计口径，不在页面中跨表累加。
export const incomeSource = '智慧校园2026年8月-27年2月学期缴费1113.xlsx'

export const annual = {
  total: { name: '总计', lastYear: 2221641, currentYear: 2433505, growth: 0.095, growthAmount: 211864, target: 2420400, targetRate: 1.005, isTotal: true },
  rows: [
    { name: '阳江市江城第一中学', lastYear: 95040, currentYear: 157200, growth: 0.654, growthAmount: 62160, target: 120000, targetRate: 1.310 },
    { name: '阳西县方正中学', lastYear: 0, currentYear: 0, growth: 0, growthAmount: 0, target: 0, targetRate: 0 },
    { name: '阳江市共青湖学校小学部', lastYear: 13020, currentYear: 3360, growth: -0.742, growthAmount: -9660, target: 48000, targetRate: 0.070 },
    { name: '阳江市共青湖学校初中部', lastYear: 66420, currentYear: 44220, growth: -0.334, growthAmount: -22200, target: 120000, targetRate: 0.369 },
    { name: '阳江市丰泰学校小学部', lastYear: 15636, currentYear: 12772, growth: -0.183, growthAmount: -2864, target: 67200, targetRate: 0.190 },
    { name: '阳江市丰泰学校初中部', lastYear: 199416, currentYear: 210273, growth: 0.054, growthAmount: 10857, target: 193200, targetRate: 1.088 },
    { name: '广东两阳中学', lastYear: 311040, currentYear: 374940, growth: 0.205, growthAmount: 63900, target: 312000, targetRate: 1.202 },
    { name: '阳江市一中初中部', lastYear: 253080, currentYear: 252900, growth: -0.001, growthAmount: -180, target: 252000, targetRate: 1.004 },
    { name: '阳江市第三中学', lastYear: 0, currentYear: 0, growth: 0, growthAmount: 0, target: 0, targetRate: 0 },
    { name: '阳东金桂学校', lastYear: 362160, currentYear: 244260, growth: -0.326, growthAmount: -117900, target: 252000, targetRate: 0.969 },
    { name: '阳江高级中学', lastYear: 375000, currentYear: 430800, growth: 0.149, growthAmount: 55800, target: 456000, targetRate: 0.945 },
    { name: '阳江市第一中学实验学校初中部', lastYear: 355980, currentYear: 329340, growth: -0.075, growthAmount: -26640, target: 384000, targetRate: 0.858 },
    { name: '阳江市第一中学实验学校小学部', lastYear: 109500, currentYear: 85860, growth: -0.216, growthAmount: -23640, target: 120000, targetRate: 0.716 },
    { name: '阳西县第一中学附属实验学校', lastYear: 29100, currentYear: 1320, growth: -0.955, growthAmount: -27780, target: 96000, targetRate: 0.014 },
    { name: '阳江市第一中学高中部', lastYear: 0, currentYear: 255600, growth: null, growthAmount: 255600, target: 0, targetRate: null },
    { name: '学生通话加油包（平台）', lastYear: 17355, currentYear: 30660, growth: 0, growthAmount: 13305, target: 0, targetRate: 0 },
    { name: '学生通话加油包（扫码）', lastYear: 18894, currentYear: 0, growth: 0, growthAmount: -18894, target: 0, targetRate: 0 },
  ],
}

export const semester = {
  period: '2026年8月至2027年1月',
  total: { name: '总计', students: 45270, amount: 2392705, payers: 22100, rate: 0.488, isTotal: true },
  rows: [
    { name: '阳江市江城第一中学', students: 2313, amount: 156480, payers: 1304, rate: 0.564 },
    { name: '阳西县方正中学', students: 3806, amount: 0, payers: 0, rate: 0 },
    { name: '阳江市共青湖学校小学部', students: 2573, amount: 3240, payers: 27, rate: 0.010 },
    { name: '阳江市共青湖学校初中部', students: 1188, amount: 43320, payers: 361, rate: 0.304 },
    { name: '阳江市丰泰学校小学部', students: 1604, amount: 12432, payers: 74, rate: 0.046, note: '小学走读' },
    { name: '阳江市丰泰学校初中部', students: 1223, amount: 208573, payers: 1242, rate: 1.016, note: '教师子女44个' },
    { name: '广东两阳中学', students: 4190, amount: 374520, payers: 3121, rate: 0.745, note: '教师子女6个' },
    { name: '阳江市一中初中部', students: 2345, amount: 252240, payers: 2102, rate: 0.896, note: '教师子女14个' },
    { name: '阳江市第三中学', students: 3954, amount: 0, payers: 0, rate: 0 },
    { name: '阳东金桂学校', students: 2273, amount: 242520, payers: 2022, rate: 0.890, note: '教师子女1个' },
    { name: '阳江高级中学', students: 4260, amount: 428940, payers: 3575, rate: 0.839, note: '教师子女3个' },
    { name: '阳江市第一中学实验学校初中部', students: 3184, amount: 328200, payers: 2735, rate: 0.859 },
    { name: '阳江市第一中学实验学校小学部', students: 3310, amount: 85320, payers: 711, rate: 0.215, note: '教师子女136个' },
    { name: '阳西县第一中学附属实验学校', students: 3674, amount: 1320, payers: 11, rate: 0.003 },
    { name: '阳江市第一中学高中部', students: 5373, amount: 255600, payers: 4261, rate: 0.793 },
    { name: '学生通话加油包（平台）', students: null, amount: 0, payers: 554, rate: null },
  ],
}

export const historyPeriods = [
  { short: '22年8月—23年1月', label: '2022年8月至2023年1月', rateLabel: '达成率' },
  { short: '23年2月—7月', label: '2023年2月至7月', rateLabel: '达成率' },
  { short: '23年8月—24年1月', label: '2023年8月至2024年1月 · 上学期', rateLabel: '达成率' },
  { short: '24年2月—7月', label: '2024年2月至7月 · 下学期', rateLabel: '达成率' },
  { short: '24年8月—25年1月', label: '2024年8月至2025年1月 · 上学期', rateLabel: '缴费率' },
  { short: '25年2月—7月', label: '2025年2月至7月 · 下学期', rateLabel: '缴费率' },
  { short: '25年8月—26年1月', label: '2025年8月至2026年1月 · 上学期', rateLabel: '缴费率' },
  { short: '26年2月—7月', label: '2026年2月至7月 · 下学期', rateLabel: '缴费率' },
  { short: '26年8月—27年1月', label: '2026年8月至2027年1月 · 本学期', rateLabel: '缴费率' },
]

// values 按 historyPeriods 顺序记录 [回款金额, 缴费/达成率]。
export const history = {
  note: '原表注明“更早的数据已隐藏”，此处仅展示工作簿已提供的九个学期及历史累计。',
  total: { name: '合计', values: [[53220, null], [3150, null], [559320, null], [56460, null], [1430540, null], [137760, null], [2066592, null], [25410, null], [2392705, null]], cumulative: 6725157, isTotal: true },
  rows: [
    { name: '阳江市江城第一中学', values: [[31320, .277], [480, .022], [114960, .888], [16440, .128], [65880, .256], [3480, .027], [91560, .321], [720, .005], [156480, .564]], cumulative: 481320 },
    { name: '阳西县方正中学', values: [[1080, .015], [60, .001], [3360, .025], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]], cumulative: 4500 },
    { name: '阳江市共青湖学校初中部', values: [[18060, .179], [2370, .025], [69240, .821], [1500, .036], [69960, .300], [2700, .026], [63720, .439], [900, .013], [43320, .304]], cumulative: 271770 },
    { name: '阳江市共青湖学校小学部', values: [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [540, .004], [12480, .039], [120, .001], [3240, .010]], cumulative: 16380 },
    { name: '阳江市丰泰学校初中部', values: [[0, 0], [0, 0], [45960, .329], [2820, .040], [117360, .881], [1680, .024], [197736, .900], [1700, .016], [208573, 1.016]], cumulative: 575829 },
    { name: '阳江市丰泰学校小学部', values: [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [180, .002], [15456, .056], [340, .002], [12432, .046]], cumulative: 28408 },
    { name: '广东两阳中学', values: [[0, 0], [0, 0], [209160, .440], [1980, .008], [214980, .444], [1200, .005], [309960, .666], [300, .001], [374520, .745]], cumulative: 1112100 },
    { name: '阳江市一中初中部', values: [[0, 0], [0, 0], [93960, .941], [120, .004], [176280, .886], [600, .006], [252480, .883], [660, .005], [252240, .896]], cumulative: 776340 },
    { name: '阳江市第三中学', values: [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]], cumulative: 0 },
    { name: '阳东金桂学校', values: [[0, 0], [0, 0], [0, 0], [0, 0], [121800, .879], [118500, .891], [245280, .938], [1620, .018], [242520, .890]], cumulative: 729720 },
    { name: '阳江高级中学', values: [[0, 0], [0, 0], [0, 0], [33600, .418], [211080, .667], [5940, .037], [367080, .695], [1500, .006], [428940, .839]], cumulative: 1048140 },
    { name: '阳江市第一中学实验学校初中部', values: [[0, 0], [0, 0], [0, 0], [0, 0], [367320, .848], [1860, .009], [354240, .871], [1020, .005], [328200, .859]], cumulative: 1052640 },
    { name: '阳江市第一中学实验学校小学部', values: [[0, 0], [0, 0], [0, 0], [0, 0], [76400, .403], [900, .006], [108600, .377], [540, .003], [85320, .215]], cumulative: 271760 },
    { name: '阳西县第一中学附属实验学校', values: [[2760, .005], [240, .001], [22680, .157], [0, 0], [9480, .032], [180, .001], [28920, .066], [0, 0], [1320, .003]], cumulative: 65580 },
    { name: '阳江市第一中学高中部', values: [[null, null], [null, null], [null, null], [null, null], [null, null], [null, null], [0, 0], [0, 0], [255600, .793]], cumulative: 255600 },
    { name: '学生通话加油包（平台）', values: [[null, null], [null, null], [null, null], [null, null], [null, null], [null, null], [19080, null], [15990, null], [0, null]], cumulative: 35070 },
  ],
}

export const weekly = {
  dates: ['11月6日', '11月7日', '11月8日', '11月9日', '11月10日', '11月11日', '11月12日'],
  range: '2026年11月6日—11月12日',
  total: { name: '总计', values: [8, 7, 12, 17, 6, 5, 15], sum: 70, isTotal: true },
  rows: [
    { name: '学生通话加油包', values: [3, 2, 3, 6, 5, 1, 6], sum: 26 },
    { name: '阳江市第一中学高中部', values: [4, 4, 3, 5, null, null, 2], sum: 18 },
    { name: '阳江市第一中学实验学校', values: [null, null, null, 1, 1, 1, 2], sum: 5 },
    { name: '阳东金桂学校', values: [null, null, null, null, null, null, 4], sum: 4 },
    { name: '阳江高级中学', values: [null, 1, 3, null, null, null, null], sum: 4 },
    { name: '阳江市第一中学初中部', values: [1, null, null, 1, null, 1, 1], sum: 4 },
    { name: '阳江市丰泰学校', values: [null, null, null, 2, null, 1, null], sum: 3 },
    { name: '广东两阳中学', values: [null, null, 1, 1, null, null, null], sum: 2 },
    { name: '江城第一中学', values: [null, null, 1, 1, null, null, null], sum: 2 },
    { name: '阳江市共青湖学校', values: [null, null, 1, null, null, 1, null], sum: 2 },
  ],
}

export const monthly = {
  months: ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月'],
  total: { name: '总计', values: [90, 21, 370, 135, 117, 117, 11, 16137, 5472, 404, 129], sum: 23003, isTotal: true },
  rows: [
    { name: '阳江市第一中学高中部', values: [null, null, null, null, null, null, null, 3758, 428, 29, 47], sum: 4262 },
    { name: '阳江高级中学', values: [3, 5, 18, 2, null, null, null, 3016, 540, 15, 4], sum: 3603 },
    { name: '阳江市第一中学实验学校', values: [1, 1, 25, null, null, null, null, 1870, 1472, 97, 7], sum: 3473 },
    { name: '广东两阳中学', values: [1, 1, 4, null, null, null, null, 2506, 601, 11, 3], sum: 3127 },
    { name: '阳江市第一中学初中部', values: [null, 1, 8, 2, null, null, null, 1730, 353, 14, 5], sum: 2113 },
    { name: '阳东金桂学校', values: [1, null, 23, 2, 1, 1, null, 1300, 701, 15, 6], sum: 2050 },
    { name: '阳江市丰泰学校', values: [null, 5, 17, 2, null, null, null, 993, 313, 4, 6], sum: 1340 },
    { name: '江城第一中学', values: [null, 1, 10, null, null, null, null, 904, 374, 24, 2], sum: 1315 },
    { name: '学生通话加油包', values: [84, 7, 249, 126, 116, 116, 11, 15, 367, 166, 47], sum: 1304 },
    { name: '阳江市共青湖学校', values: [null, null, 16, 1, null, null, null, 35, 322, 29, 2], sum: 405 },
    { name: '阳西县第一中学附属实验学校', values: [null, null, null, null, null, null, null, 10, 1, null, null], sum: 11 },
  ],
}
