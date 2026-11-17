# D-011 至 D-015 验证记录

- 日期：2026-11-17。
- 核查基线：`f6df988bf464667b63e2cbad3d784f34b133c4b2`（远端 main）。
- 本地工作分支：`work/d011-d015-verification`。
- 执行主体：Codex；不将自动化验证记为组员人工决策或人工浏览器验收。
- 环境：Python 3.12.14、Django 5.2.19、SQLite 3.53.1；依赖按现有 `uv.lock` 安装。

## 结论与本轮工作

基线已有 D-011 至 D-015 的实现，原有 60 项测试全部通过。本轮新增 6 项测试、扩展报修长度测试和真实文件库验收，补齐防重作用域、输入边界、历史长字段、草稿转义及六个写入口的锁故障验证。业务代码、依赖和数据库结构无需修改。

D-011 日志标题与结果已明确区分历史 SessionStorage 方案和当前会话绑定签名 Cookie，避免按旧方案回退实现。

## 逐项证据

| 决策 | 已有实现 | 验证与本轮补充 |
| --- | --- | --- |
| D-011 | `accounts/message_storage.py` 的 `SessionBoundCookieStorage`；`lab_repair/settings.py` 配置；`accounts/views.py` 首页禁止缓存 | `accounts.test_messages.ReceiptStorageTests` 的 11 项测试覆盖退出、换号、同账号不同会话、篡改、过期、容量及不额外保存会话。文件库测试验证业务提交后出现真实写锁和排他锁，仍正确重定向且提示仅消费一次。上述实现和测试来自基线，本轮复跑。 |
| D-012 | 报修 GET 生成 UUID；`create_ticket` 按报修人与标识查重；模型及迁移 `0005` 有联合唯一约束 | 基线覆盖相同表单重试、同标识不同内容冲突、新表单新建以及两个独立连接并发重试。本轮新增 `test_submission_id_is_scoped_to_reporter`、`test_invalid_submission_id_does_not_write_and_keeps_report`，验证不同报修人可使用同一标识且彼此独立，无效标识不写入并保留表单。 |
| D-013 | 设备名称 160、位置 240、故障描述及操作文字 4000 字符；`ticket_list_page` 每页 25 条；列表仅预览长名称，详情读全文 | 扩展 `test_report_fields_over_limit_are_rejected_without_writes` 至三个报修字段；新增 `test_report_fields_at_limits_are_saved_in_full`、`test_legacy_long_fields_remain_readable_in_full`、`test_note_length_boundary_for_progress_result_and_return`。分别核对等于上限可保存、超出一字符不写入、历史长字段可读及三类操作文字限制。基线分页测试验证 27 条记录分为 25 和 2 条，列表名称摘要与详情全文不同。 |
| D-014 | `update_ticket` 在表单校验后保留有效草稿，409 或服务层 503 返回可复制文字和详情入口；模板自动转义 | 基线覆盖旧版本进度、处理结果、退回。本轮新增 `test_conflict_draft_is_escaped_and_has_recovery_link`，核对包含 HTML 的草稿安全回显、详情入口、禁止缓存及数据不变。扩展文件库验收，实际持有写锁，验证进度、处理结果、退回的有效草稿和详情入口，另验证报修表单保留内容。 |
| D-015 | `_handle_locked_database` 位于 `login_required` 外层，捕获身份读取时的 SQLite 锁异常并返回 503；非锁 OperationalError 继续抛出 | 本轮新增文件库验收辅助函数 `_verify_locked_write_endpoints`，对报修、接单、进度、提交验收、关闭和退回，分别持有 `BEGIN IMMEDIATE` 与 `BEGIN EXCLUSIVE`，经带 CSRF 的 HTTP 测试客户端请求，验证 503、禁止缓存，以及工单与事件全表快照不变。 |

## 边界说明

- 防重针对正常表单携带的提交标识；为兼容旧提交入口，缺少标识的请求仍可创建工单，不承诺防重。
- 普通写锁下，身份与表单可先解析，已经校验的有效草稿可回显。排他锁可能在身份解析时就失败，此时返回通用 503，不承诺回显未经校验的 POST 内容。锁测试区分这两个路径。
- 签名 Cookie 防篡改而非加密，容量受限；它只承载短期提示，工单与事件历史才是持久记录。
- 文件数据库测试使用临时目录、独立 SQLite 连接和子进程，不读写项目的业务库。
- 未进行人工浏览器操作，也未补写课程要求的三条待补人工实质修正。

## 复跑命令

在包含 `manage.py` 的项目根目录执行：

```bash
uv sync --locked --python 3.12
uv run --locked python manage.py test --verbosity 2
uv run --locked python manage.py check
uv run --locked python manage.py makemigrations --check --dry-run
git diff --check
```

若仅抽查 D-011 至 D-015，可运行：

```bash
uv run --locked python manage.py test accounts.test_messages repairs.tests repairs.test_workflow repairs.test_file_database --verbosity 2
```

本次依赖安装后直接使用同一虚拟环境的 `.venv/bin/python` 执行检查；对应 Windows 为 `.venv\Scripts\python.exe`。

## 最终验证

- 基线：60 项测试通过，耗时 7.186 秒。
- 修改后定向检查：30 项测试通过，耗时 11.221 秒。
- 最终全套：66 项测试通过，耗时 14.121 秒；[完整测试输出](d011-d015-verification/test-output.txt)。
- `manage.py check`：`System check identified no issues (0 silenced).`。
- `manage.py makemigrations --check --dry-run`：`No changes detected`。
- `git diff --check`：通过。
- 本轮差异复核：仅测试与文档变化；真实锁故障后核对全表快照，草稿按 HTML 转义，未发现待修业务缺陷。该复核由同一执行智能体完成，未声称独立评审。
