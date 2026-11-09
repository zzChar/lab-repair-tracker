---
title: '故事 1.1 · 从 Django 骨架启动并登录'
type: feature
created: '2026-11-09'
status: done
route: oneshot
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

**问题**　报修应用需要可启动的项目和可区分的会话身份，后续工单才能绑定报修人和负责人。

**方案**　从 Django 5.2 LTS 官方骨架开始，使用本机 SQLite 保存演示账号。普通成员和负责人分别登录，只有负责人可访问负责人入口。未登录访问受保护入口时跳转登录。README 记录初始化、启动和验证步骤。工单能力从故事 1.2 开始。

</frozen-after-approval>

## Implementation Notes

- 运行官方 `startproject`，用 `pyproject.toml` 和 `uv.lock` 锁定 Django 5.2.19。本机运行环境为 Python 3.12.10、Django 5.2.19、SQLite 3.49.1。
- 应用复用 Django 登录、退出和数据库会话。负责人角色由服务器检查 `repair_handlers` 组，直接访问负责人入口也会校验角色。
- 使用 `seed_demo_accounts` 初始化本机两个演示账号。重复运行保留现有密码和账号，遇到不兼容账号或异常组权限时事务回滚。
- 从空数据库执行迁移和演示账号初始化。Django 系统检查通过，8 个测试通过。启动、停止并重启两个独立的 Django 进程，使用真实 HTTP 表单和 Cookie 验证两个账号可登录、普通成员 403、负责人 200、退出后重定向。
- Git 根位于练习目录的上级，当前无提交基线。本次交付保存在练习目录。

## Review Triage Log

| 发现 | 判断和处理 |
| --- | --- |
| 同名账号密码不可用时初始化报告成功 | 中。初始化改为拒绝该冲突，测试验证另一账号也未被部分创建。 |
| 现有负责人组有额外权限时演示账号继承权限 | 中。初始化检查组权限，测试用预置的 `auth.delete_user` 验证拒绝。 |
| 普通成员经负责人链接登录后落到缺乏导航的英文 403 | 低。保留 403，增加中文说明和返回首页链接。通过带 CSRF 的测试与实际 HTTP 流程验证。 |
| 已登录访问登录页会显示第二个登录表单 | 低。页面显示当前账号和首页入口，测试校验。 |
| 应追加单独的跨进程持久化测试文件 | 低。已用新建的文件数据库启动两个独立 Django 进程并验证双账号登录，README 保留复现步骤。持久化验收有实际证据。 |
