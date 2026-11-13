# 05-changecast（变投影 · 双时钟提交）

Changecast — 变更日志投影：源变更流 → 按到达波次投影 → **seq 水位与 event_time 水位同成同败** → 批次计数与快照互证。

与 Feedstitch（多源 Feed 归并）、Batchgate（作业依赖编排）、Syncledger（双端对账）主链路不同。
这里的「双端」是**同一投影流的两种时钟水位**，不是左右两个数据源对账。夹具全本地，不连活库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5400 |
| API | http://localhost:10400 |

## 主链

配置投影流 → 按到达波次读取 CDC 夹具 → 双水位提交判定（同进 / 整趟拒绝 / 追赶）→ 投影落库 → 批次与双水位、快照计数互证

## 双水位提交规则

每条投影流同时维护：

- `watermark_seq`：已成功应用事件的最大 seq；
- `watermark_event_time`：已成功应用事件的最大 event_time。

两端都是只进不退的高水位。一趟成功提交必须**两端同时推进**，旧新值在同一个 UPDATE / 同一事务里落库，因此不存在「seq 已前进但时间水位落后」的撕裂态。

默认策略 **strict（整趟拒绝）**：当本趟只能合法推进其中一端（另一端候选相对上次成功水位未前进或回拨）时，整趟拒绝——不写投影行、两端水位都不动。批次以 `decision=reject` 留痕并给出原因码。

可选策略 **catch_up（追赶）**：仅推进 seq 端，event_time 端保持上次成功水位，批次 `decision=catch_up`、`mode=catch_up` 明确标记。追赶模式禁止应用任何会回拨任一端的事件（事件 seq ≤ seq 水位、或 event_time < 时间水位都会使整趟被拒）。

原因码：

| 原因码 | 含义 |
| --- | --- |
| `both_advance` | 双端同进，提交 |
| `strict_requires_both` | 严格模式下只有一端可前进，整趟拒绝 |
| `seq_only_catch_up` | 追赶模式仅推进 seq 端 |
| `time_only_prohibited` | 只有时间端可前进 / seq 回拨，拒绝 |
| `late_event_prohibited` | 可应用事件里有 event_time 早于时间水位，拒绝 |
| `seq_rollback_prohibited` | seq 端整体回拨，拒绝 |
| `no_pending_events` | 本波没有未应用事件，拒绝 |

## 互证

每批返回 `reconcile`，四项同屏：

1. **双水位同成同败** —— 拒绝批两端都不变；提交批两端都等于落库后的实际水位。
2. **双水位与已应用事件互证** —— `watermark_seq` / `watermark_event_time` 必须等于 `applied_events` 里成功应用事件的最大 seq / 最大 event_time。
3. **批次计数与快照互证** —— 批次记录的存活 / 已删必须等于投影快照实测值。
4. **拒绝批不动投影** —— 拒绝批的投影指纹与上一成功批、当前快照三者相同。

投影指纹是投影快照（实体键、载荷、applied_seq、事件 ID、删除态）的 sha256，用来证明「整趟拒绝时投影确实没动」。

## 种子投影流与演示波次

每条夹具用 `wave` 标记到达波次，一趟只取该波事件：

- **member_profile**：会员档案变更
  - W1 双端同进 → W2 仅 seq 前进（strict 拒 / catch_up 追赶）→ W3 双端同进 → W4 仅时间前进（拒）→ W5 可应用事件时间回拨（拒）
- **store_archive**：门店档案变更
  - W1 同进 → W2 仅 seq（追赶）→ W3 同进 → W4 仅时间（拒）
- **channel_attr**：渠道属性变更
  - W1、W2 同进 → W3 仅 seq（追赶）→ W4 仅时间（拒）→ W5 时间回拨（拒）

`POST /api/demo/{stream_id}` 会重置该流后按波次自动跑一遍：先 strict，被拒后再用 catch_up，直观展示三种路径。

## 页面

| 页面 | 用途 |
| --- | --- |
| 总览 | 成功 / 追赶 / 拒绝批次数，各流双水位 |
| 投影流 | 夹具、波次、双水位现状 |
| 投影台 | 选流 + 波次 + 模式发起一趟；展示双水位旧新值、互证四项 |
| 双水位 | 各流双水位看板、全波次演示、批次水位轨迹 |
| 投影快照 / 应用批次 / 事件留痕 | 快照、批次证据字段、逐事件结果（含「本趟拒绝」与「陈旧跳过」） |
| 缓冲区 / 设置 | 底座空挂 / 只读策略 |

对外文案不用「工单」「CRM」。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 测试

```bash
cd backend
python -m pytest tests/ -q
```

`tests/test_dual_watermark.py` 覆盖：双端同进、仅 seq 前进时严格拒绝 / 追赶提交、仅时间前进拒绝、追赶模式禁止回拨、拒绝事件保持待应用、水位与已应用事件互证。
