# 配对可见性口径与投影-正式联动

配对/对齐结果页的「已配对」必须全局只贯彻一种正确性口径，由
`settings.pair_visibility_policy` 决定（设置页可切换，后端白名单校验，
写脏值直接 400，绝不静默落到第三种行为）：

| 口径 | 配对页听谁 | 总览已配计数 | 页有库无 | 库齐页未配 |
| --- | --- | --- | --- | --- |
| `formal_intersection`（默认） | 正式估抄∩实抄键集合实时重放 | 正式交集 | 页面立即不绿；修复=回滚半截配对投影 | 页面由正式重放自给；修复=补齐投影行 |
| `pair_projection` | `pair_projection` 物化表 | 投影行数 | 幽灵行显式标记且页面报分叉；修复=剔幽灵投影 | 页面未配且报分叉；修复=补投影，总览必跟 |

两种口径共用同一条收敛不变量，全局只有这一种写法语义：

> **任意写事务提交时，`pair_projection` == 正式估抄∩实抄键集合。**

口径只决定「配对可见性听谁」，不允许任何一侧长期撕裂；正式读数是计量事实，
投影不能反向捏造正式行，故修复只收敛投影侧（「补物化」只发生在投影侧）。

## 联动链路（装载口/正式表/投影/对齐批/配对页/总览/差额入口，禁止只改页面缓存）

1. **装载口** `POST /api/estimates`、`POST /api/estimates/revise`、`POST /api/actuals`
   正式行与投影在同一个 `BEGIN IMMEDIATE` 事务内提交（`sync_key`）：双侧齐则补
   投影行，缺一侧则剔投影行；回执带 `paired_now`。并发装载两笔不产抢写窗口。
2. **对齐物化** `POST /api/align`：批头快照 `paired_count`（正式交集键数），同事务
   按正式交集全量重建该期投影（剔幽灵+补缺失），回执带 `pair_consistent=true`。
   **禁止回执绿了双侧键与投影仍分叉**：收敛失败即 500 回滚。
3. **差额入口** `POST /api/variances/{id}/fate`：处置同事务收敛该期投影，
   处置后页绿必须库不拧，回执带 `pair_consistent`。
4. **配对页** `GET /api/pairs`：按口径出数；任何分叉以
   `diverged`/`ghost_keys`/`missing_keys` 显式报出，页面只在 `consistent=true`
   时记绿；formal 口径下页面必须能由正式双侧重放。
5. **修复口** `POST /api/pairs/repair`：formal=回滚半截配对投影
   （`rollback_projection`）；projection=剔幽灵+补投影（`converge_projection`）。
   修复后总览计数必跟。
6. **总览** `GET /api/overview`：`paired_count` 按口径取数（正式交集/投影行数），
   并暴露 `pair_diverged` 与幽灵/缺失计数告警，不跟任何页面缓存。
7. **封印预检** `POST /api/seal`：封印事务内先收敛投影，再按选定口径取已配集合
   计入回执（`paired_count`/`pair_policy`），已配集合与选定口径一致。
8. **启动收敛**：`seed_if_empty` 每次启动按正式双侧全量重建投影，交错半截不得
   长期现行；老库迁移补建 `pair_projection` 表、按正式双侧回填、批表补
   `paired_count` 快照列，并废弃 `pair_projection_v1` JSON 页面缓存。

## 并发

装载、对齐、处置、封印全部经 `with_write_tx` 包成 `BEGIN IMMEDIATE`（WAL +
busy_timeout + 重试）：投影维护与正式行写在同一事务提交，装载与对齐交错、
并发装载两笔都只产生串行化的中间态，最终必收敛；不存在「投影与正式抢写」
的窗口，也不存在「页面缓存已刷新、正式行停在半截」的可见状态。

## 测试

`backend/tests/test_pair_visibility.py` 覆盖四种夹具（页有库无、库齐页未配、
交错半截、处置后页绿库拧）× 两口径，外加装载口联动、对齐回执、封印预检、
总览口径、口径白名单、老库迁移，共 15 个用例。估抄锁口径套件见
[ESTIMATE_LOCK.md](ESTIMATE_LOCK.md)。

```
cd backend
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```
