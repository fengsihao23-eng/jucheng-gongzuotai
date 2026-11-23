# 财务账表导入与接口对接

## 财务人员操作

1. 在 **开票回款** 或 **出纳日记账** 打开要录入的台账，点击 **导入财务账表**。
2. 选择或拖入文件后直接打开核对预览。也可点 **粘贴表格**，复制 Excel、在线表格或消息中的明细后点 **预览粘贴内容**。非标准格式默认交由小聚协助识别，PDF、文档和图片先提取正文。
3. 核对弹窗显示识别出的台账和原表行号。点击 **待补充** 或 **重复** 可集中检查相应行。空白账户可以统一填写。需要换台账或指定表头时，打开 **调整表头或台账**。
4. 核对后点击 **确认导入**，按回执核实入账数量。需要以后继续时，点击 **稍后核对**，可在 **导入记录** 恢复预览。
5. 查账可使用 **全部年份**，也可选一个年份查看 **全年** 或指定月份，再按账户、关键字筛选。银行支出和现金支付可筛选 **待归集支出**。报销单在 **报销归集** 查看。
6. 错导一笔时，点击该行的 **删除**。整份文件错导时，可在 **导入记录 → 撤销本批** 操作。记录明细也可以发起撤销。确认后合计自动更新，同一流水号或发票号码可重新导入正确数据。

当前版本以独立财务台账为主。项目、审批编号、合同编号按原表保存为文本，关联其他业务模块留待后续对接。

## 台账及字段口径

| 入口 | `kind` | 字段及入账口径 |
| --- | --- | --- |
| 开票回款 → 开票台账 | `invoice` | `sequence`、`date` 开票日期、`invoice_no` 发票号码、`customer` 购方名称、`net` 含税开票金额、`contract_no` 合同编号、`approval_no` 审批编号、`note`。日期、发票号码、购方名称、含税金额必填。截图未提供开票字段，当前采用上述字段。 |
| 开票回款 → 收款记录明细 | `bank_receipt` | `sequence` 序号、`date` 收款日期、`account` 收款银行、`payer` 汇款公司、`net` 收入金额、`summary` 摘要、`project` 项目、`reference` 流水号、`note` 备注。日期、收款银行、汇款公司和收入金额必填。 |
| 出纳日记账 → 银行支出日记账 | `bank_expense` | `sequence`、`date` 日期、`account` 汇款公司、`summary`、`payee` 收款方资料、`payment` 金额、`applicant` 申请人、`approval_no` 审批编号、`note`、`project`、`reference`。日期、汇款公司、收款方和金额必填。 |
| 出纳日记账 → 现金日记账 | `cash_journal` | `sequence`、`date` 日期、`account` 现金账户、`counterparty` 对方科目、`approval_no` 申请编号、`summary`、`income` 收入金额、`payment` 支付金额、`balance` 结余金额、`note`、`project`、`reference`、`period` 所属月份。交易行要有账户、日期及收支金额。结转行要有账户、结余和所属月份，可保留空白日期，结转余额独立于本期收支。 |

- 金额单位为人民币元。成功校验的金额以两位小数字符串传输，如 `"3680.50"`。金额合计跟随台账的期间和账户筛选。
- 日期输出为 `YYYY-MM-DD`，月份为 `YYYY-MM`。账表标题和相邻交易日期可为现金结转提供月份。可在预览中核对从标题和相邻交易推断出的信息。
- 账号、审批编号、发票号和流水号按文本交接。Excel 里的长编号应保存为文本，以保留所有位数和前导零。
- 同一个账户名用同一写法。现金账户可以来自文件或工作表标题，也可在预览中指定。
- 二维码收款明细已停用。曾导入的这一类记录留档，混合工作簿中的二维码明细页会略过并在预览里提示。
- 文件支持 XLSX、XLSM、CSV、TSV、TXT、Markdown、DOCX、PDF 和常见图片。扫描件及图片依赖可用的视觉模型。单次上限为 20 MiB、5,000 个非空源行、每行 100 列，解读后的正文上限为 200 万字符。旧版二进制 XLS 可在 Excel 或 WPS 中另存为 XLSX。

## 接口契约

基础路径为 `/api/v1/finance-ledgers`。请求携带工作台登录凭证 `Authorization: Bearer <token>`。接口沿用工作台响应包 `{code, message, data, trace_id}`，`code=0` 表示成功。

| 方法 | 路径 | 请求 | `data` |
| --- | --- | --- | --- |
| GET | `/schema` | 无 | `ledgers` 以 `kind` 为键。每种类型返回 `label`、`menu`、`can_import`、`fields[]`、`totals`。另返回 `limits` 和 `can_allocate`。 |
| POST | `/preview` | `multipart/form-data` 字段 `file`、`kind`、`use_xiaoju`（默认 `true`）。调用方如需传粘贴数据，可以把正文作为 `file` 上传。 | 预览，包括 `id`、`version`、`filename`、`groups`、`allowed`、`rows`、`counts`、`notice`、`skipped`。 |
| GET | `/imports` | `menu=receivables` 或 `menu=journal`，`page` 从 1 开始，`size` 默认 20。 | 批次列表，含 `id`、`filename`、`uploader`、`updated_at`、`kinds`、`status`、`inserted`、`active`、`can_revoke`。 |
| GET | `/imports/{id}` | 批次 `id`，仅上传者可访问 | 待确认返回同结构预览。已确认返回 `id`、`version`、`filename` 和 `result`。 |
| POST | `/imports/{id}/review` | `version`，可选 `groups[]`、`rows[]` | 重新验证的预览及新 `version`。 |
| POST | `/imports/{id}/confirm` | `version`、`row_ids[]` | `inserted` 入账行数、`skipped` 重复跳过数、`inserted_by_kind` 实际写入数、`selection` 选中数，后两项按台账类型分组。 |
| GET | `/entries/{kind}` | `period` 接受 `YYYY` 全年或 `YYYY-MM` 单月，留空为全部期间。另支持 `account` 精确匹配、`keyword` 模糊搜索、`project` 精确匹配，值 `unassigned` 筛选待归集支出。分页 `page` 从 1 起，`size` 默认 30、最大 100。 | `items`、`total`、`page`、`page_size`、筛选后 `totals`（`inflow`、`outflow`、`fee`）及该类型的 `accounts`。 |
| PATCH | `/entries/{entry_id}/project` | `{"project":"实验室改造"}`。空字符串清空费用归属。 | 更新后的银行支出或现金支付记录。 |
| DELETE | `/entries/{entry_id}` | 可选 JSON `{"reason":"原表金额有误"}`，最多 200 字。 | `removed`，首次删除为 1，已删除为 0。 |
| GET | `/imports/{id}/revoke` | 已确认的批次 `id`。 | `filename`、仍有效的总笔数 `total` 和按台账类型分组的 `by_kind`。 |
| POST | `/imports/{id}/revoke` | JSON `{"reason":"原表上传错误"}`，原因可留空。 | `removed` 和按台账类型分组的 `by_kind`。 |

### 预览结构

- `groups[]` 每项含工作表 `id`、`name`、`kind`、`header`、列映射 `mapping`、默认 `account`、结转 `period`、`method`、可选 `headers` 和 `columns`。`header` 是原表行号，`0` 表示无表头。`mapping` 键从 `0` 起，例如 `"0"` 对应 Excel A 列。`method` 取值 `rule`、`xiaoju`、`manual`。
- `rows[]` 每项含稳定的预览行 `id`、`group`、`kind`、源工作表 `sheet`、原行号 `row`、原文摘录 `raw`、标准字段 `values`、按字段列出的 `errors`、`warnings` 和 `status`。`status` 取值 `ready`、`duplicate`（台账已存在）、`repeated`（本批有相同标识）、`error`（待核对）。`counts` 按这四种状态计数。
- `skipped[]` 包含略过的工作表名 `name` 和原因 `reason`。批次列表的 `status` 取值 `draft`、`confirmed`、`partial` 或 `removed`，已入账数量与剩余数量按当前模块统计。
- `notice` 提示小聚暂不可用或未完成整理的情况。应用可以继续展示规则识别结果，允许用户调整列对应及逐行修订。
- 正式台账 `items[]` 每项含数据库 `id`、`kind`、`period`、`values`、当前用户的删除权限 `can_delete` 和 `source`。`source` 含 `filename`、`sheet`、`row`、`batch`、`imported_at`。预览行 `id` 仅在当前批次使用，台账记录 `id` 用于之后的查询与归集。

### 调用示例

准备收款明细预览。`API_BASE` 为部署环境的 `/api/v1/finance-ledgers` 地址。

```bash
curl -fsS "$API_BASE/preview" \
  -H "Authorization: Bearer $WORKBENCH_TOKEN" \
  -F 'kind=bank_receipt' \
  -F 'use_xiaoju=true' \
  -F 'file=@银行收款明细.xlsx'
```

通过 `review` 调整工作表列或单笔数据。省略 `rows` 可只调整列映射。行修订的 `values` 仅提供变动字段，值使用字符串。

```json
{
  "version": 1,
  "groups": [{"id":"1","kind":"bank_receipt","header":2,"mapping":{"0":"date","1":"account","2":"payer","3":"net"},"account":"","period":""}],
  "rows": [{"id":"1:3","values":{"net":"3680.50"}}]
}
```

把预览交给上传者确认后，将最新版本和勾选行发送到 `confirm`。

```json
{"version":2,"row_ids":["1:3","1:4"]}
```

## 权限、重复与对接约定

- 读取需要对应菜单和全量数据权限。导入银行收款需要 `receivables:receipt`，导入开票需要 `receivables:invoice`，导入支出或现金账需要出纳日记账菜单及 `journal:write` 或 `expense:allocate`。出纳账表中的支出归集需要 `expense:allocate`。预览按上传者隔离，确认时再次校验选中行权限。
- 有当前台账导入权限的上传者可以删除自己导入的数据。管理员可以在可见台账中删除。具有更正权限的人员可以处理其他人的开票和收款，具有报销归集权限的财务经理可以处理其他人的银行支出和现金日记账。撤销整批要求拥有批次涉及的每种在用台账的删除权限。
- 删除的记录退出列表和金额合计，原始记录和操作人、时间、可选原因保留用于审计。批量撤销只处理该批次实际写入且仍有效的在用台账记录。已记账批次的原入账回执仍可用于重试核对，修正后从新的上传预览开始。
- 上传只建立草稿。草稿最近修订后可访问 24 小时，接口发现过期返回 `404`。记账前支持再次读取或修订草稿。未选中的行略过。确认成功后只保留批次回执和已记账明细的来源信息。
- 同一批次按最新版确认，事务提交后同版本重试返回原回执。台账优先按发票号码或同类型同账户的流水号识别重复。无流水号时按业务字段和同表出现次数匹配。原表流水号可帮助区分同日同额交易。
- 确认选中的异常行或版本冲突时返回 `409`，本次选中行整体不提交。已存在且关键字段相同的行会跳过。流水号或发票号相同但关键字段有差异时，应回到预览核对，`errors` 给出相应字段。
- `400` 表示输入字段、业务参数或文件无效，`401` 需重新登录，`403` 需由管理员核对权限，`404` 可核对批次或记录是否存在。联调记录 `trace_id`、批次 `id` 和原始行号即可定位问题。
- 对接方读取正式数据使用 `GET /entries/{kind}`。后续启用外部业务关联时，使用台账记录 `id` 作稳定标识，并以 `account`、`reference`、`approval_no`、`contract_no`、`project` 等原始字段作人工或规则核对线索。
