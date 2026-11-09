# 历史项目报表导入与核对

工作台保留每份项目月度分析表的原始 Excel、SHA-256、全部非空单元格及公式、凭证行、关联合同和正式成本 ID。在「项目利润」顶部、合同详情和项目详情的「历史项目报表」中可以核对明细、月份汇总、待付款及差异，并下载原件。

## 数据口径

- 通过合同 ID、编号、名称、金额、项目编码和文件校验和核对身份，不凭文件名近似匹配入账。报表合同额须与关联合同合计相符。
- 有日期和金额的采购、施工及报销凭证进入统一利润服务。报销使用「历史报销」类型，按报销成本统计，不再重复计入采购成本。日期保留源凭证的记账日期，不能当作银行实际收付日期。
- 负数冲销保留原符号。「冲销」摘要却为正数、或日期晚于报表截止日的凭证保留为待核对行，暂缓计入正式成本；不猜测改符号或改项目。
- 待支付明细是报表截止日的供应商应付快照，按同一项目最新一期报表计入累计成本（成本来源 `report_payable`），不生成已付款凭证；原表税费保留完整测算值，不自动生成实际纳税。多期报表只取最新一期，避免跨期重复累计。
- 原表开票额缺少发票号码，不生成正式发票。源表「未收金额」通常是已开票减已收，不能替代合同总额减累计回款。
- 回款凭证仅在同合同、同月、同金额的原回款唯一匹配时挂接。到账日沿用原台账，原凭证记账日另留来源；不会新增回款或重复冲抵应收计划。
- 同一项目分析表对应多份合同时，成本在主项目保存；完整可见范围的利润、看板、报表与 WorkBuddy 按项目组合并核算，不按合同金额猜测分摊。项目经营报表注明合并关系，其他合同项目的合并利润留空，避免展示成零毛利。只有部分项目权限的用户不会因此获得其他项目数据。
- 小计错位、年份或月份笔误、分币差额、源表与既有回款台账差异均保留在核对事项中。既有合同、项目、回款及原件内容不被改写。

## 导入与防重

迁移为 `f6b7d92a41c0`，增加 `fin_project_report`、`fin_project_report_contract` 和 `fin_project_report_entry`。不修改原有业务表结构。

先备份数据库与文件卷，在独立的生产库副本验证，再导入生产。不能对业务库运行 `scripts.seed`。

核对清单 JSON 包含 `schema_version: 1`、整个 ZIP 的 `archive_sha256`，以及每份文件的 `filename`、`sha256`、`contract_ids`、`primary_contract_id` 和 `contracts`。`contracts` 以字符串合同 ID 为键，每项包含 `contract_no`、`name`、两位小数的 `amount` 及 `project_code`，用于检测核对后的业务变化。清单和原始业务数据保存在私有运行目录，不提交 Git。

```bash
cd backend
# 只读预览，不写数据库或文件卷
.venv/bin/python -m scripts.import_project_reports /private/reports.zip /private/mapping.json --operator-id <管理员ID>

# 一个事务提交全部文件，并刷新受影响的既有报表及本周利润表
.venv/bin/python -m scripts.import_project_reports /private/reports.zip /private/mapping.json --operator-id <管理员ID> --apply --refresh-reports
```

整个批次在同一事务中执行，使用数据库事务锁串行查重。相同文件重跑直接返回原批次；同一合同同一月份的新版本会阻止直接累计。重叠累计报表中的同一凭证复用正式成本，不重复计入。保存后的来源链按行复核金额、日期、项目及回款关系。来源成本不允许从财务数据页面单独改写或删除，须连同来源凭证办理更正。

`--refresh-reports` 为已有周利润表、项目经营报表、公司月报及本周利润表生成新版本，保留旧版快照与来源批次，不发送对外消息。相同批次重跑不会再次刷新报表。完成后重启 API 或通过看板 `refresh=1` 核对统计，避免沿用旧进程缓存。

## 查询与权限

- `GET /api/v1/reports/project-sources`：分页列表，可按 `project_code`、`contract_id`、`period` 筛选。
- `GET /api/v1/reports/project-sources/{id}`：明细、原单元格、公式、差异、正式记录 ID 和来源链校验。
- 读取须有项目利润或经营看板菜单；非全量数据范围须能查看报表关联的全部项目。
- 原件采用 `project_finance_source` 保护类别。即使持有签名链接，仍须携带登录态并通过同样的财务与项目范围检查。

## 验证

新测试覆盖有符号金额、暂缓入账、重复凭证、累计版本冲突、多合同合并、真实到账日、只读预览、源身份变化、利润下钻一致性、原件下载权限和成本来源保护。独立测试库中同时执行相关财务、看板、项目与 MCP 回归：

```bash
cd backend
TEST_DB=jucheng_test_project_reports .venv/bin/python -m pytest tests/test_exec.py tests/test_market.py tests/test_mcp.py tests/test_project_reports.py tests/test_reports.py tests/test_finance_ops.py tests/test_receivables.py -q
```

`acceptance/project-reports.mjs` 是只读浏览器验收，使用 `WEB`、包含短期会话令牌的私有 `AUTH_FILE`、私有证据目录 `OUT`，以及可选的 `BROWSER_EXECUTABLE`。验证页面入口、合同与项目反查、待核对行、多合同下钻、原件下载哈希和未登录下载拒绝，不写测试业务数据到生产。
