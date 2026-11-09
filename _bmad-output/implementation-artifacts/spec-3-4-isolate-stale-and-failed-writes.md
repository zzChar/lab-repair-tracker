---
title: '故事 3.4 · 隔离旧请求与写入失败'
type: feature
created: '2026-11-09'
status: done
route: oneshot
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

旧验收页面不能影响新一轮。事件写入异常回滚，SQLite 写锁超时返回暂时不可用。

</frozen-after-approval>

## Implementation Notes

- 测试子进程忽略外部 Python 优化设置，避免断言被跳过。用独立读连接比对失败前后记录。迁移 0004 在练习数据库已应用，并用含旧工单和两条事件的数据库验证升级保留数据。

- RepairWorkflowTests 覆盖上一轮关闭和退回均被拒绝。FileDatabaseAcceptanceTests 用独立连接核对写入回滚及真实锁超时前后的工单和事件。
