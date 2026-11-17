---
title: '完成 D-006 至 D-010 的收尾与回归'
type: 'bugfix'
created: '2026-11-17'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
baseline_commit: 'd63cddb808a364effd6454d34a39558803f520a6'
context: []
---

<frozen-after-approval reason="用户已要求完成并 review">

## Intent

修复工单与事件成功提交后因成功消息保存会话失败而返回错误的问题，同时保持账号间回执隔离；补齐已核查的权限、旧版本、CSRF 和回滚回归，将详情历史读取统一放入查询层。保持现有页面、重定向、状态机和事务语义，不新增依赖或迁移。

</frozen-after-approval>

## Implementation Notes

- 计划：先补真实文件库的提交后锁故障回归及正式工作流边界测试；再修复消息存储与查询分层；最后运行全套测试、系统与迁移检查，独立 review 并处理有效发现。
- 消息采用绑定当前会话的签名 Cookie，避免成功反馈再次写数据库，保留退出和切换账号后的隔离。复用 Django 消息实现，不自行实现密码学。
- 文件职责：消息修复及文件库故障测试由独立实现代理负责；主代理负责查询提取、工作流边界回归及文档同步。
- 无需求缺口、不可逆操作、新依赖或数据库结构变更；不接触已有业务数据库。
- 已补五项工作流回归：同状态旧版本、四类工作流无 CSRF、进度/验收/退回事件异常回滚、角色撤销后的原会话、伪造报修人及操作者。重构前后 `uv run --locked python manage.py test repairs.test_workflow` 均为 10 项通过。
- `repairs/queries.py` 新增 `ticket_history`，视图仅使用返回的历史与最新进度，保留按已读工单版本截断和服务端可见性过滤。
- 消息回归先验证旧 SessionStorage 在真实文件锁下返回 400，再验证新存储在提交后普通写锁、排他锁下均返回 302。另直接覆盖原始关闭场景，独立连接确认已关闭且唯一关闭事件存在，解锁后提示仅消费一次。
- Cookie 存储不修改会话、无会话保存需求，绑定签名在读取、排队和响应保存时同步当前认证会话。容量超限时丢弃最旧提示，允许单条无法容纳的提示丢弃，避免 DEBUG 下消息中间件因未存储提示而抛错；持久工单事件不受影响。

## Review Triage Log

四层独立审查：Blind Hunter、Edge Case Hunter、Verification Gap Reviewer、Acceptance Auditor。后三层无发现；Blind Hunter 未确认功能缺陷，提出以下六项测试建议，逐项处理如下。

| 编号 | 结论 | 证据及处理 |
| --- | --- | --- |
| 1 | low，已补 | 新建路径不能直接回归原始关闭故障。增加关闭提交后 `BEGIN EXCLUSIVE`，断言 302、详情重定向、已关闭、唯一关闭事件及一次性成功提示。 |
| 2 | low，已补 | 原测试只覆盖退出/账号切换，增加同账号两个有效会话之间复制 Cookie 后拒绝读取，原会话仍可读取。 |
| 3 | low，已补 | 增加篡改及旧会话 Cookie 随新报修提交，确认新提示恰好显示一次。 |
| 4 | low，已补 | 将身份轮换测试扩展为读取后再保存、直接响应保存两个分支，确认原提示与队列均不流入新会话。 |
| 5 | low，拒绝 | 建议直接测试未使用存储不解析惰性用户。现有真实文件库写入前排他锁回归已断言中间件完整返回 503 且数据不变；专门断言私有惰性读取分支会重复实现细节，未另增测试。 |
| 6 | low，已补 | 将容量回归扩展至下一请求合并现存 Cookie 和新队列，确认最新提示保留且 Cookie 有界。 |

无待人工决策项、未修复功能缺陷或本次新增的延期项。

## Verification

- 最终 `uv run --locked python manage.py test`：60 项全部通过（原 44 项，新增 16 项）。真实文件库断言在独立临时目录运行，不改动现有业务库。
- `uv run --locked python manage.py check`：无问题。
- `uv run --locked python manage.py makemigrations --check --dry-run`：无迁移变更。
- `git diff --check`：通过。项目未配置额外 lint/typecheck 工具，本次不新增依赖。
- Blind Hunter 对补强测试再复核通过，无新增可达问题。无独立 story_key，未变更已完成故事状态；只将本次已解决的史诗 2、3 复盘跟进项标记 done，史诗 1 的其他跟进仍保留 open。
