# 估抄锁口径与读写联动

全局同一时刻只贯彻一种口径，由 `settings.estimate_lock_policy` 决定（设置页可切换，
后端白名单校验，写脏值直接 400，绝不静默落到第三种行为）：

| 口径 | 首笔实抄后估抄 | 纠错方式 | 锁标记 | 对齐批 |
| --- | --- | --- | --- | --- |
| `lock_on_actual`（默认） | 拒绝再改（409） | 估抄订正链路（只追加，不动本体） | `estimate_locks` 落行 | 任一侧写入即失效 open 批 |
| `editable_until_sealed` | 封印前始终可覆盖修订 | 直接修订，订正接口 409 | 不落有效锁（历史锁行不生效） | 修订即失效 open 批 |

两种架构共用同一道封印闸：账期 `sealed` 后估抄、实抄、订正、对齐、差额命运一律拒写。

## 联动链路（禁止只改锁文案不驱动写闸）

1. **估抄入口** `POST /api/estimates`、`POST /api/estimates/revise`
   统一走 `gate.assert_estimate_mutable`：先封印闸，再按口径查 `estimate_locks` 真实落行。
2. **实抄到达事件** `POST /api/actuals`
   与估抄写闸在同一个 `BEGIN IMMEDIATE` 事务内：`lock_on_actual` 下
   `INSERT OR IGNORE INTO estimate_locks` 打锁标，随后失效该期 open 对齐批，原子提交。
3. **锁标记** 以 `estimate_locks` 表为准；可改口径下即使存在历史锁行，写闸、列表、
   总览、对齐快照都不视为有效锁。
4. **对齐物化** `POST /api/align`：每个表该账期只取最新一笔估抄；差额行快照
   `variances.estimate_locked`，批头快照 `reconcile_batches.policy/locked_count`；
   先失效旧 open 批再插新批。
5. **开批失效** `gate.expire_open_batches`：估抄装载/修订、实抄到达任一发生，
   该期 `status='open'` 的批整体置 `expired`，封印只接受 open 批，杜绝
   “实抄后改估抄成功却对齐仍用旧估抄”。
6. **封印闸** `gate.assert_period_open`：两架构共用，已封印一律 409。
7. **快照** 历史批/差额行的锁标记是对齐瞬间的定格，后续口径切换或锁变化不回改历史。
8. **总览** `/api/overview` 与对齐页同口径：有效锁计数受口径门控，未决差额只统计 open 批。

## 并发

所有写操作经 `with_write_tx` 包成 `BEGIN IMMEDIATE` 事务（WAL + busy_timeout + 重试）。
并发的「首笔实抄」与「改估抄」串行化：实抄提交则锁标必在同一事务落行，不存在
“实抄已成功但无锁标”的半截状态；修订若晚于锁定必被 409，若成功则严格发生在锁定之前。

## 订正链路（仅 lock_on_actual）

`POST /api/estimates/corrections` 向 `estimate_corrections` 追加订正，记录锁定量与
订正量；估抄本体与对齐输入不变，对齐仍以锁定时估抄为准。订正不触发批失效。

## 测试

`backend/tests/` 覆盖两架构各自口径、封印闸、快照/总览一致性、老库迁移、
50 轮真实线程并发与混合压力，共 33 个用例：

```
cd backend
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```
