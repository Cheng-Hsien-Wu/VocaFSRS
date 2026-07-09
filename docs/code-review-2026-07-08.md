# 程式碼檢查報告（2026-07-08）

檢查基準：本專案只在內網執行、單人使用，因此不以公網服務或多人並發的標準要求，
重點放在「實際會造成錯誤行為或誤導使用者」的問題。

檢查當下狀態：後端 81 個測試全數通過、前端 `tsc -b` 與 `eslint` 皆乾淨。
沒有發現嚴重問題，以下為找到的小問題與修法（皆已於本次修正）。

---

## 1. 前端把「批改未完成」誤判成「單字來源不明確」

- **位置**：`frontend/src/services/study-session-store.ts`（`studySetupError`）、`frontend/src/pages/StudyPage.tsx`
- **問題**：後端建立複習 session 時，若上一輪還有未完成的 LLM 批改，會回
  `409` 且 `detail.error = "pending_adjudication"`（`backend/app/routers/study.py`）。
  但前端 `studySetupError` 沒有這個分支，任何不認識的 409/400 都會落到
  `deck_scope_required`，畫面顯示「請先整理複習來源，請重新匯入單字」——與實際
  原因無關，會誤導使用者去重新匯入。
- **修法**：在 `studySetupError` 加上 `pending_adjudication` 分支，回傳專屬的
  `errorType`；`StudyPage` 沿用既有的「有批改尚未完成」畫面顯示正確引導。

## 2. `uv run pytest` 直接執行會失敗

- **位置**：`backend/pytest.ini`
- **問題**：`uv run pytest` 會噴 `ModuleNotFoundError: No module named 'app'`，
  必須手動 `PYTHONPATH=. uv run pytest` 才能跑。
- **修法**：在 `pytest.ini` 加一行 `pythonpath = .`。

## 3. 答題送出失敗時，使用者看不到任何錯誤訊息

- **位置**：`frontend/src/pages/StudyPage.tsx`（`handleTypedNext`）
- **問題**：送出答案的網路請求失敗時沒有 try/catch，錯誤只進 console；
  按鈕從「儲存中…」變回「下一題」，使用者不知道答案沒存成功。
  （資料不會壞：重按會用新的 idempotency key 重送，若先前其實已成功，
  伺服器會回 conflict 並觸發重新同步。）
- **修法**：加上 try/catch 與 `submitError` 狀態，失敗時在「下一題」按鈕下方
  顯示「答案尚未儲存，請確認連線後再按一次」。

## 4. finish / abandon 端點不檢查目前狀態

- **位置**：`backend/app/routers/study.py`（`finish_study_session`、`abandon_study_session`）
- **問題**：可以把已放棄（abandoned）的 session 改成 completed，
  或把已完成的改成 abandoned，狀態會被無聲覆寫。
- **修法**：
  - `finish`：已是 completed 就直接回傳（冪等）；abandoned 回 409。
  - `abandon`：已是 abandoned 就直接回傳（冪等）；completed 回 409。
  - 冪等回傳同時讓前端「重試完成本輪」在「第一次其實已成功、只是回應遺失」時
    不會再改寫一次狀態。

## 5. 雜項

- **`backend/app/routers/study.py` 死碼**：`if data.mode == TIMED and req_size <= 0`
  永遠不成立（Pydantic 已限制 `requested_size > 0`），移除。
- **`backend/app/routers/import_csv.py` 孤兒檔案**：上傳流程先寫檔再建 DB 記錄，
  若 commit 失敗會留下沒有對應 job 的 `.csv`（過期清理只認 DB 裡有記錄的 job）。
  修法：commit 失敗時順手刪掉剛寫入的檔案。

---

## 刻意不動的部分

以下機制以個人內網使用來說偏重，但已寫好、有測試覆蓋、且不妨礙使用，
拆除的風險大於保留的成本，維持原狀：

- `study_answers.py` 的批改 claim token + 15 分鐘 lease + `_claim_is_current`
  雙重確認（單人情境下其實一個 UPDATE 就足夠）。
- 匯入 commit 的 `idempotency_key` + `request_hash` 雙重驗證。
- `main.py` 靜態檔案的路徑跳脫檢查（成本極低，留著）。
- `reset-progress` 需輸入 `RESET` 確認——這是防手滑，建議永久保留。

---

## 升級指南：既有資料庫如何無痛更新到新版

**所有學習進度都存在 `backend/data/vocab.db`（SQLite）**，升級只是替換程式碼，
不會動到這個檔案。前端瀏覽器裡的 IndexedDB 只是快取，伺服器才是唯一資料來源，
重新整理後會自動從伺服器重新同步。

本次修正**沒有更動資料庫 schema**（沒有新增 migration），因此步驟很簡單：

```bash
# 1. 停掉伺服器（在執行 start.sh 的終端機按 Ctrl+C）

# 2. 備份資料庫（必須在伺服器停止後複製，WAL 附屬檔一起帶走）
cp backend/data/vocab.db      backend/data/vocab.db.bak
cp backend/data/vocab.db-wal  backend/data/vocab.db-wal.bak 2>/dev/null || true
cp backend/data/vocab.db-shm  backend/data/vocab.db-shm.bak 2>/dev/null || true

# 3. 更新程式碼（git pull，或把新版檔案覆蓋上去；backend/.env 與 .vocafsrs.conf 不會被 git 覆蓋）

# 4. 套用資料庫 migration（本次沒有新 migration，此步是 no-op，但養成習慣）
cd backend && uv sync --no-dev && uv run alembic upgrade head && cd ..

# 5. 重建前端
cd frontend && npm ci && npm run build && cd ..

# 6. 重新啟動
./start.sh
```

或者直接重跑 `./install.sh`：它會問「backend/.env already exists. Replace it?」，
回答 **N** 就會保留現有設定，並自動執行 `uv sync`、前端 build 與
`alembic upgrade head`，同樣不會碰 `vocab.db`。

注意事項：

- **升級前最好先把進行中的複習做完或放棄**（回到首頁沒有「繼續複習」按鈕即可）。
  就算不做，session 狀態也都在伺服器上，重啟後仍可續作；只是跨版本續作沒必要冒險。
- 未來若某次更新有新的 migration，`alembic upgrade head` 會自動套用，
  進度一樣保留；真正要避免的只有 `reset_db.py` 和 App 內的
  「重設進度」功能，那才會清空資料。
- 若升級後行為異常，把 `.bak` 檔案改回原名即可完整還原。
