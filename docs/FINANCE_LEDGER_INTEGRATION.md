# 财务账表导入与接口对接

## 财务人员操作

1. 在 **开票回款** 或 **出纳日记账** 打开要录入的台账，点击 **导入财务账表**。
2. 选择或拖入文件。标准表头直接进入核对预览，其他格式默认由小聚整理后进入预览。现金文件可以整张上传。也可点击 **粘贴表格** 并预览从 Excel、在线表格或消息中复制的明细。
3. 核对弹窗显示目标台账和原表行号。现金预览可按 **收入**、**支出**、**结转**、**零发生** 筛选。用 **待核对** 找到金额方向或其他字段待确认的记录。点击行尾可查看原文并修正。需要统一补账户、换台账或指定表头时，可在预览里操作。
4. 核对后点击 **确认导入**，按回执核实入账数量。需要以后继续时，点击 **稍后核对**，可在 **导入记录** 恢复预览。
5. **出纳日记账 → 银行日记账** 可切换 **收入** 和 **支出**。**现金日记账** 还可用 **全部** 查看结转和零发生行。选择年份后默认显示全年，也可下钻到某个月份。银行支出和现金支付可筛选 **待归集支出**。
6. 错导一笔时，点击该行的 **删除**。整份文件错导时，可在 **导入记录 → 撤销本批** 操作。记录明细也可以发起撤销。确认后合计自动更新，同一流水号或发票号码可重新导入正确数据。

台账独立运行。项目、审批编号、合同编号按原表保存为文本。

## 台账及字段口径

| 入口 | `kind` | 字段及入账口径 |
| --- | --- | --- |
| 开票回款 → 开票台账 | `invoice` | `date` 开票日期、`issuer` 开票单位、`customer` 客户、`project` 项目名称、`net` 借方、`note` 备注。开票日期、客户、借方必填。历史字段 `invoice_no`、`contract_no`、`approval_no`、`sequence` 可在明细查看，发票号码可留空。表中的「应收账款合计」行会略过。 |
| 开票回款 → 收款记录明细 | `collection_receipt` | `invoice_date` 开票日期、`issuer` 开票单位、`name` 名称、`date` 收款日期、`net` 收款金额、`note` 备注。名称、收款日期、金额必填。保留完整名称，按收款日期筛选。`reference` 可选。表中的「已收款合计」行会略过。 |
| 出纳日记账 → 银行日记账 → 收入 | `bank_receipt` | `sequence` 序号、`date` 收款日期、`account` 收款银行、`payer` 汇款公司、`net` 收入金额、`summary` 摘要、`project` 项目。收款日期、收款银行、汇款公司和收入金额必填。`reference`、`note` 可选。原有 `bank_receipt` 记录及批次在这里查看。 |
| 出纳日记账 → 银行日记账 → 支出 | `bank_expense` | `sequence` 序号、`date` 日期、`account` 汇款公司、`summary` 摘要、`payee` 收款方资料、`payment` 金额、`applicant` 申请人、`approval_no` 审批编号、`note` 备注、`project` 项目。日期、汇款公司、收款方和金额必填。`reference` 可选。 |
| 出纳日记账 → 现金日记账 | `cash_journal` | `sequence` 序号、`date` 日期、`counterparty` 对方科目、`approval_no` 申请编号、`summary` 摘要、`income` 收入金额、`payment` 支付金额、`balance` 结余金额、`note` 备注。普通行填写日期和至少一个金额，允许原表明确列示的零金额。结转行填写结余及日期或所属月份。`account`、`project`、`reference`、`period` 可按来源保存。 |

- 金额单位为人民币元。成功校验的金额以两位小数字符串传输，如 `"3680.50"`。金额合计跟随台账的期间和账户筛选。
- 日期输出为 `YYYY-MM-DD`，月份为 `YYYY-MM`。账表标题或后续交易日期可为现金结转提供月份。结转余额与现金结余按源值保存，合计只累计发生额。存在两项非零发生额的原表行在两种方向中均可查到。
- 账号、审批编号、发票号和流水号按文本交接。Excel 里的长编号应保存为文本，以保留所有位数和前导零。
- 同一个账户名用同一写法。现金账户可以来自文件或工作表标题，也可在预览中指定。现金模板没有账户列时可直接核对入账。
- 二维码收款明细已停用。曾导入的这一类记录留档，混合工作簿中的二维码明细页会略过并在预览里提示。
- 文件支持 XLSX、XLSM、CSV、TSV、TXT、Markdown、DOCX、PDF 和常见图片。企业微信导出的 XLSX 如包含空填充样式，读取时会自动补齐样式定义，保留原始数据和工作表。扫描件及图片依赖可用的视觉模型。单次上限为 20 MiB、5,000 个非空源行、每行 100 列，解读后的正文上限为 200 万字符。旧版二进制 XLS 可在 Excel 或 WPS 中另存为 XLSX。仍无法读取时，可通过弹窗的文件名和问题编号定位，也可粘贴表格进入核对。

## 接口契约

基础路径为 `/api/v1/finance-ledgers`。请求携带工作台登录凭证 `Authorization: Bearer <token>`。接口沿用工作台响应包 `{code, message, data, trace_id}`，`code=0` 表示成功。

| 方法 | 路径 | 请求 | `data` |
| --- | --- | --- | --- |
| GET | `/schema` | 无 | `ledgers` 以 `kind` 为键。每种类型返回 `label`、`menu`、`can_import`、`fields[]`、`totals`。`fields[].visible` 标记主表列。现金额外提供仅参与列映射的 `import_columns`。另返回 `limits` 和 `can_allocate`。 |
| POST | `/preview` | `multipart/form-data` 字段 `file`、`kind`、`use_xiaoju`（默认 `true`）。调用方如需传粘贴数据，可以把正文作为 `file` 上传。 | 预览，包括 `id`、`version`、`filename`、`groups`、`allowed`、`rows`、`counts`、`notice`、`skipped`。 |
| GET | `/imports` | `menu=receivables` 或 `menu=journal`，`page` 从 1 开始，`size` 默认 20。 | 批次列表，含 `id`、`filename`、`uploader`、`updated_at`、`kinds`、`status`、`inserted`、`active`、`can_revoke`。 |
| GET | `/imports/{id}` | 批次 `id`，仅上传者可访问 | 待确认返回同结构预览。已确认返回 `id`、`version`、`filename` 和 `result`。 |
| POST | `/imports/{id}/review` | `version`，可选 `groups[]`、`rows[]` | 重新验证的预览及新 `version`。 |
| POST | `/imports/{id}/confirm` | `version`、`row_ids[]` | `inserted` 入账行数、`skipped` 重复跳过数、`inserted_by_kind` 实际写入数、`selection` 选中数，后两项按台账类型分组。 |
| GET | `/entries/{kind}` | `period` 接受 `YYYY` 全年或 `YYYY-MM` 单月。留空为全部期间。`cash_journal` 可传 `direction=income` 或 `direction=expense` 筛选对应的非零发生额，省略时查看全部。另支持 `account` 精确匹配、`keyword` 模糊搜索、`project` 精确匹配，值 `unassigned` 筛选待归集支出。分页 `page` 从 1 起，`size` 默认 30、最大 100。 | `items`、`total`、`page`、`page_size`、筛选后 `totals`（`inflow`、`outflow`、`fee`）及该类型的 `accounts`。按现金方向筛选时，仅累计对应方向金额。 |
| PATCH | `/entries/{entry_id}/project` | `{"project":"实验室改造"}`。空字符串清空费用归属。 | 更新后的银行支出或现金支付记录。 |
| DELETE | `/entries/{entry_id}` | 可选 JSON `{"reason":"原表金额有误"}`，最多 200 字。 | `removed`，首次删除为 1，已删除为 0。 |
| GET | `/imports/{id}/revoke` | 已确认的批次 `id`。 | `filename`、仍有效的总笔数 `total` 和按台账类型分组的 `by_kind`。 |
| POST | `/imports/{id}/revoke` | JSON `{"reason":"原表上传错误"}`，原因可留空。 | `removed` 和按台账类型分组的 `by_kind`。 |

### 预览结构

- `groups[]` 每项含工作表 `id`、`name`、`kind`、`header`、列映射 `mapping`、默认 `account`、结转 `period`、`method`、可选 `headers` 和 `columns`。`header` 是原表行号，`0` 表示无表头。`mapping` 键从 `0` 起，例如 `"0"` 对应 Excel A 列。`method` 取值 `rule`、`xiaoju`、`manual`。
- `rows[]` 每项含稳定的预览行 `id`、`group`、`kind`、源工作表 `sheet`、原行号 `row`、原文摘录 `raw`、标准字段 `values`、按字段列出的 `errors`、`warnings` 和 `status`。`status` 取值 `ready`、`duplicate`（台账已存在）、`repeated`（本批有相同标识）、`error`（待核对）。`counts` 按这四种状态计数。
- 现金行的 `cash_category` 可为 `income`、`expense`、`both`、`opening`、`zero` 或 `unknown`，其他台账返回 `null`。有两列金额时分别映射 `income` 和 `payment`，保留原符号。来源使用同一列金额时，可映射 `flow_amount` 和 `flow_direction`。来源明确用正数表示流入、负数表示流出时，可映射 `signed_amount`，支付金额按绝对值入账。方向无据可查的行保留原文供核对。
- `skipped[]` 包含略过的工作表名 `name` 和原因 `reason`。批次列表的 `status` 取值 `draft`、`confirmed`、`partial` 或 `removed`，已入账数量与剩余数量按当前模块统计。
- `notice` 提示小聚暂不可用或未完成整理的情况。应用可以继续展示规则识别结果，允许用户调整列对应及逐行修订。
- 正式台账 `items[]` 每项含数据库 `id`、`kind`、`period`、`values`、当前用户的删除权限 `can_delete` 和 `source`。`source` 含 `filename`、`sheet`、`row`、`batch`、`imported_at`。预览行 `id` 仅在当前批次使用，台账记录 `id` 用于之后的查询与归集。

### 调用示例

准备银行收入预览。`API_BASE` 为部署环境的 `/api/v1/finance-ledgers` 地址。开票回款里的收款记录使用 `kind=collection_receipt`。

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

- 读取需要对应菜单和全量数据权限。导入开票需要 `receivables:invoice`，导入开票回款里的收款记录需要 `receivables:receipt`。银行收入、银行支出和现金均使用出纳日记账菜单及 `journal:write` 或 `expense:allocate`。出纳账表中的支出归集需要 `expense:allocate`。预览按上传者隔离，确认时再次校验选中行权限。
- 有当前台账导入权限的上传者可以删除自己导入的数据。管理员可以在可见台账中删除。具有更正权限的人员可以处理其他人的开票与回款明细。具有 `expense:allocate` 的财务经理可以处理其他人的银行和现金日记账。撤销整批要求拥有批次涉及的每种在用台账的删除权限。
- 删除的记录退出列表和金额合计，原始记录和操作人、时间、可选原因保留用于审计。批量撤销只处理该批次实际写入且仍有效的在用台账记录。已记账批次的原入账回执仍可用于重试核对，修正后从新的上传预览开始。
- 上传只建立草稿。草稿最近修订后可访问 24 小时，接口发现过期返回 `404`。记账前支持再次读取或修订草稿。未选中的行略过。确认成功后只保留批次回执和已记账明细的来源信息。
- 同一批次按最新版确认，事务提交后同版本重试返回原回执。台账优先按发票号码或同类型同账户的流水号识别重复。无编号时按业务字段和同表出现次数匹配。无发票号码的开票记录以开票日期、开票单位、客户、项目、借方等字段匹配。
- 确认选中的异常行或版本冲突时返回 `409`，本次选中行整体不提交。已存在且关键字段相同的行会跳过。流水号或发票号相同但关键字段有差异时，应回到预览核对，`errors` 给出相应字段。
- `400` 表示输入字段、业务参数或文件无效，`401` 需重新登录，`403` 需由管理员核对权限，`404` 可核对批次或记录是否存在。联调记录 `trace_id`、批次 `id` 和原始行号即可定位问题。
- 对接方读取正式数据使用 `GET /entries/{kind}`。后续启用外部业务关联时，使用台账记录 `id` 作稳定标识，并以 `account`、`reference`、`approval_no`、`contract_no`、`project` 等原始字段作人工或规则核对线索。
