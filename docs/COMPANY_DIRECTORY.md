# 公司员工账号与组织架构接入

## 最短操作流程

公司管理员在企业微信创建一个由公司直接管理的自建应用，设置需要同步的部门和成员范围。自建应用的 AgentID、CorpID 和 Secret 只需配置一次，员工无需重复配置。

在本站「系统管理 → 账号与角色 → 配置公司应用」填写三个值，点击「验证并读取员工预览」。应用授权验证通过后，自动保存受限凭证、启用企业微信真实接口、读取授权范围内的部门和员工并展示变更预览。回调密钥自动生成，通讯录同步不依赖消息回调。

如果已有接入配置文件，也可使用小聚的企业凭证导入入口，由小聚读取并验证。Secret 不应粘贴到聊天中。

首次导入需要核对预览后点击「确认导入公司人员」。确认的内容包括实际成员、待新增/更新账号、身份冲突和演示账号归档清单。之后每日 06:00 自动更新已确认企业的目录。

### 平台可能要求的配置

- **可信 IP**：由服务器实际请求企业微信验证；若返回错误码 `60020`，在应用的「企业可信 IP」加入服务器出口公网 IP。页面只提取经校验的错误码和 IP，不回显平台原始错误中的凭证。
- **可信域名**：按企业微信后台要求配置。企业微信网页登录另需设置本站域名；域名校验文件可由运维放置到网站根目录。
- **可见范围**：成员与部门以接口实际返回为准。选中一个部门不代表已授予全公司目录权限。
- **隐私字段**：新建应用通常无法直接读取手机和邮箱；缺失字段显示“未返回”，不会编造或清空原有值。

腾讯官方对自建应用凭证、可见范围、可信域名和 IP 的说明见[企业微信数据源指南](https://identity.tencent.com/docs/guides/SyncConfig/wecom/)。个人 CLI 的[官方功能说明](https://github.com/WeComTeam/wecom-cli)包含联系人搜索；本项目已安装版本的 discovery 仅提供 `contact.users.search`，搜索截断结果不用于宣称已同步完整公司名册。

## 账号处理规则

- 真实账号以公司 CorpID 和企业 UserID 识别，默认普通员工 `EMP` 权限，密码为空，使用企业微信登录；企业微信密码不会导入。
- 新部门单独建档，保留企业部门 ID 与父子关系。真实部门不会覆盖同号的模拟部门。
- 已核实的虚构演示账号在确认后改名归档、停用并撤销口令和 MCP 令牌，原数据库 ID 与业务引用保留。真实同名 UserID 创建独立账号，不继承演示角色或历史记录。
- 现有平台管理员、带管理员角色、已删除、改过演示密码或绑定个人 CLI 的账号受保护。身份冲突显示在预览中，跳过自动覆盖。
- 已绑定本企业的真实账号更新姓名、部门、岗位及接口获准返回的联系方式，保留业务角色、已有密码和未返回的可选字段。
- 未出现在授权响应中的成员不自动停用。只有明确返回禁用/退出状态的已绑定成员会被停用并撤销 MCP 令牌。
- 企业微信 OAuth 登录只能进入已经绑定当前企业的真实账号，避免企业 UserID 与本地管理员或演示账号重名导致提权。
- 企业微信真实消息只能发给绑定当前企业的有效成员，模拟账号与旧模拟通知不会直接投递给同名真实员工。

预览绑定提交人、企业凭证、账号权限和账号/部门快照，有效期 30 分钟。确认前发生变化会要求重新预览，已完成的预览可以安全重试。预览文件权限为 `0600`；首次未确认前，定时任务不能建立公司目录。

此功能导入账号和组织资料；财务、考勤、合同等业务数据仍需各自真实数据源接入。

## 验证

2026-10-22：全量后端 **275 项通过**，包含 18 项公司目录和身份隔离测试；前端生产构建通过，`acceptance/company-directory.mjs` 的 6 项浏览器流程检查通过。测试使用独立数据库与虚构接口响应，未导入企业真实员工数据。

## 企业微信应用图标

上传文件：[750 × 750 PNG](assets/jucheng-workbench-icon.png)，518137 字节。由内置 imagegen 生成，使用 `sips` 缩小至后台建议尺寸，原始文件保留。

生成提示：

> Use case: logo-brand. Create ONE finished square WeCom application icon for the Chinese company internal workbench named 聚成工作台 with its AI assistant 小聚. A polished minimal blue-and-white enterprise logo, full-bleed vivid royal-blue background, centered bold white abstract symbol of three rounded geometric ribbons converging into a clear central hub, subtly suggesting a capital J and a friendly spark of intelligence. Premium modern Chinese SaaS identity, clean flat vector-like geometry with only a very slight blue tonal gradient. Strong silhouette legible at 48 pixels. Generous safe margins, centered composition. Opaque background extending to all four square edges, no outer border and no mockup. No letters, Chinese characters, watermark, captions or extra icons. Output a single crisp square bitmap suitable for an enterprise application logo; do not include the user screenshots or any UI.
