# VocaFSRS 批改卡住問題分析與修復方案

## 問題現象

- 使用者完成作答後觸發批改（`POST /adjudicate`），50 題被打包成一個背景任務（`asyncio.create_task`）
- 服務重啟（換模型、部署、crash、OOM kill）時，**in-process 背景任務直接消失**
- DB 裡 50 筆 `typed_study_answers` 永遠停留在：
  - `adjudication_status = 'processing'`
  - `adjudication_claim_token = <uuid>`
  - `adjudication_claimed_at = <timestamp>`
- 前端再按批改、**完全沒反應**，因為 API 不處理 `PROCESSING` 狀態

## 根本原因

### 1. 狀態機缺口（constants.py:146-147）

```python
# 只有 PENDING 能被 adjudicate 撈到
CLAIMABLE_ADJUDICATION_STATUSES = (AdjudicationStatus.PENDING,)

# 只有 FAILED 能被 adjudication-retry 撈到
RETRYABLE_ADJUDICATION_STATUSES = (AdjudicationStatus.FAILED,)

# PROCESSING 不在任何名單裡！
```

### 2. Stale Lease 機制寫了但沒接上（study_answers.py:363-382）

```python
async def _claimable_answer_ids(db, session_id, statuses):
    stale_before = now() - ADJUDICATION_LEASE_TIMEOUT  # 15 分鐘
    # 邏輯：status in statuses OR (status == PROCESSING AND claimed_at < stale_before)
    # 但沒有任何 endpoint 傳入包含 PROCESSING 的 statuses
```

### 3. 背景任務非持久化

- `asyncio.create_task(_apply_llm_adjudication_wrapper(...))` 跑在 uvicorn worker 記憶體裡
- 任何重啟都會丟失任務，**無法恢復**

## 重現步驟

1. 開始 study session，作答 50 題
2. 按批改 → 觸發背景任務，50 筆變 `processing` + claim_token
3. 重啟服務（`systemctl restart vocafsrs` 或 kill 進程）
4. 前端再按批改 → 回傳 `{"processed": 0, "total": 0}`，資料卡住

## 修復方案（建議優先順序）

### 方案 A：最小改動——把 PROCESSING 加入 claimable（必做）

**檔案**：`app/constants.py`

```python
# 修改前
CLAIMABLE_ADJUDICATION_STATUSES = (AdjudicationStatus.PENDING,)

# 修改後
CLAIMABLE_ADJUDICATION_STATUSES = (
    AdjudicationStatus.PENDING,
    AdjudicationStatus.PROCESSING,  # 新增：stale processing 也能被撿起來
)
```

**效果**：前端呼叫 `POST /adjudicate` 時，`_claimable_answer_ids` 會把超過 15 分鐘的 `PROCESSING` 視為可 claim，自動重新批改。

---

### 方案 B：背景清理 Worker 定期掃 stale processing（建議做）

**檔案**：`app/main.py`（lifespan 裡啟動）

新增一個背景 task，每分鐘跑一次：

```python
async def adjudication_stale_cleanup_loop(session_factory):
    while True:
        await asyncio.sleep(60)
        try:
            async with session_factory() as db:
                await cleanup_stale_processing_answers(db)
        except Exception as e:
            logger.error(f"Stale cleanup failed: {e}")

async def cleanup_stale_processing_answers(db: AsyncSession):
    stale_before = datetime.now(timezone.utc).replace(tzinfo=None) - ADJUDICATION_LEASE_TIMEOUT
    await db.execute(
        update(TypedStudyAnswer)
        .where(
            TypedStudyAnswer.adjudication_status == AdjudicationStatus.PROCESSING,
            TypedStudyAnswer.adjudication_claimed_at < stale_before,
        )
        .values(
            adjudication_status=AdjudicationStatus.PENDING,
            adjudication_claim_token=None,
            adjudication_claimed_at=None,
        )
    )
    await db.commit()
```

**效果**：不依賴前端觸發，server 端主動把超時的 `PROCESSING` 重設為 `PENDING`。

---

### 方案 C：持久化任務佇列（長遠架構演進）

引入 **Redis + Celery / Dramatiq / ARQ** 或 **SQLite-based queue (sqlmq)**，把批改任務存入佇列，worker 獨立進程消費。

- 重啟 API server 不影響佇列
- 可水平擴展 worker
- 支援 retry、dead letter、監控

**建議**：先做 A + B 解決燃眉之急，C 留待後續重構。

---

## 驗收標準

1. 完成作答 → 按批改 → 重啟服務 → 再按批改 → **50 題正常批改完成**
2. DB 中不再有殘留 `PROCESSING` 超過 15 分鐘的資料
3. `adjudication-retry` 端點也能處理 stale processing（可選：把 `RETRYABLE_ADJUDICATION_STATUSES` 也加上 `PROCESSING`）

---

## 相關檔案清單

| 檔案 | 修改重點 |
|------|----------|
| `app/constants.py` | `CLAIMABLE_ADJUDICATION_STATUSES` 加入 `PROCESSING` |
| `app/constants.py` | （可選）`RETRYABLE_ADJUDICATION_STATUSES` 加入 `PROCESSING` |
| `app/main.py` | lifespan 啟動 `adjudication_stale_cleanup_loop` |
| `app/services/study_answers.py` | 新增 `cleanup_stale_processing_answers` 函數 |

---

## 給 Claude Code 的提示詞建議

> 請幫我修復 VocaFSRS 的批改卡住問題。問題是：背景批改任務是 in-process asyncio task，服務重啟時任務消失但 DB 留下 `processing` 狀態，導致前端無法重新觸發批改。
>
> 請實作：
> 1. `app/constants.py`：把 `PROCESSING` 加入 `CLAIMABLE_ADJUDICATION_STATUSES`（必要），可選也加入 `RETRYABLE_ADJUDICATION_STATUSES`
> 2. `app/services/study_answers.py`：新增 `cleanup_stale_processing_answers(db)` 函數，把超過 15 分鐘的 `PROCESSING` 重設為 `PENDING`
> 3. `app/main.py`：lifespan 裡啟動一個背景 task `adjudication_stale_cleanup_loop`，每 60 秒跑一次 cleanup
>
> 程式碼風格請遵循現有專案慣例（async/await、SQLAlchemy async、Structured logging）。