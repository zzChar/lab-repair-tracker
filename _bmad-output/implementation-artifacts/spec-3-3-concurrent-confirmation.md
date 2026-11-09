---
title: '故事 3.3 · 保护同一轮验收的唯一结果'
type: feature
created: '2026-11-09'
status: done
route: oneshot
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

同一版本的关闭和退回并发提交时只允许一个结果成功，工单摘要和事件同步提交。

</frozen-after-approval>

## Implementation Notes

- 竞态测试在败方连接上做非阻塞写锁探测，确认触发真实 SQLite 写锁争用后再继续同一请求。两种胜出顺序分别检查状态、版本和事件。

- FileDatabaseAcceptanceTests 用独立会话、线程和连接分别验证关闭先提交、退回先提交，两轮都只有一条结果事件。
