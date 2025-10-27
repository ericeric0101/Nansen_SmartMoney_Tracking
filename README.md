# Nansen Smart Money Collector

此專案依據 `docs/Nansen_SmartMoney_Collector_FullPlan.md` 的 Phase‑1 規劃打造，用於蒐集與評分 Nansen 提供的 Smart Money 資料。專案採模組化結構，便於後續擴充 Phase‑2 交易引擎與 Phase‑3 外部資料來源，同時透過 STOP_GATE 控制流程，確保每個階段完成驗證後才繼續前進。

## 核心特色
- `src/nansen_sm_collector` 封裝組態、資料模型、資料庫、資料擷取與評分邏輯。
- CLI 入口 (`python -m nansen_sm_collector`) 會依階段參數執行對應流程並產出報告，並在報告中依據信號類型分為「建議買入」與「建議賣出」，附上代表地址與前三名最主要錢包。
- 預設使用 SQLite，亦可透過環境變數切換其他資料庫。
- 測試骨架已配置 `tests/unit` 與 `tests/integration`，便於之後撰寫詳細測試案例。
- 可啟用 GeckoTerminal API 進行模擬買賣：偵測到買入訊號後記錄買價、設定 +30%（可調）目標並於達標時記錄賣出，結果會顯示在報告與統計中。
- 每次執行除了產出 Markdown/JSON 報表，也會把摘要寫入 SQLite (`run_history`, `signal_summaries`)，方便後續匯出或統計分析。
- 內部時間以 UTC 儲存，並同步保存 Berlin 時區的本地時間欄位，報表與 DB 均可直接查閱當地時間。

### 目前用了以下三種API call: 
- /api/v1/smart-money/netflow 
- /api/v1/token-screener 
- /api/v1/smart-money/dex-trades 
* 管線先呼叫 `/api/v1/smart-money/dex-trades` 尋找聰明資金的大額交易，再用 `/api/v1/token-screener` 補足基本面與異常訊號，最後透過 `/api/v1/smart-money/netflow` 確認整體資金流向。目前 run-once 的流程是即時拉 Nansen API：smart-money/dex-trades 取**近 1 日**交易、token-screener 取**近 24 小時**、smart-money/netflow 取**近 7/30 日**。

### 評分邏輯 (Phase‑1)
- 事件需先通過必備條件：最小美元金額、流動性門檻、黑名單等。
- 符合條件的事件依下列指標加權計算分數（權重可透過 `.env` 調整）：
  - `usd_notional`：單筆聰明資金買入的美元規模。
  - `wallet.labels`：若錢包具備 Smart Money 標籤給予加分。
  - `wallet.alpha_score`：從歷史事件計算的錢包命中率，代表其過往表現。
  - `volume_jump`：Token Screener 提供的交易量異常幅度。
  - `smart_money_netflow`：聰明資金的淨流入量。
   * Positive Net Flow: Smart money is accumulating (buying more than selling, or withdrawing from CEXs)
   * Negative Net Flow: Smart money is distributing (selling more than buying, or depositing to CEXs)
- 若發現極端爆量 (`volume_jump` 過高) 或流動性不足會套用懲罰，避免追逐高風險事件。
- 最終訊號會匯總為 `signals` 表與報表中的 Token/Wallet 建議清單。

## 快速開始
1. 建立虛擬環境並安裝依賴：
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```
2. 複製 `.env.example` 至 `.env` 並填入 `NANSEN_API_KEY`。
   - 若需鎖定特定代幣，可設定 `NANSEN_DEX_TOKEN_ADDRESS`，未設定則以鏈別為主巡檢。
   - `NANSEN_ENABLE_WALLET_LABELS` 可控制是否呼叫 profiler API；若想節省額度，設為 `false` 即可。
   - `NANSEN_DEX_INCLUDE_LABELS`、`NANSEN_DEX_EXCLUDE_LABELS` 可調整欲納入／排除的 Smart Money 標籤。
   - `NANSEN_DEX_MIN_AGE_DAYS`、`NANSEN_DEX_MAX_AGE_DAYS` 用於控制代幣地址建立時間範圍。
   - `NANSEN_DEX_TRADE_MAX_USD` 可限制最大交易金額（空白則不限）。
   - `NETFLOW_MIN_POSITIVE` 可設定淨流入的最低美元門檻，確保聚合後的訊號真的是正向流量。
   - 如欲啟用模擬交易，設定 `TRADE_SIMULATION_ENABLED=true` 並提供 GeckoTerminal 相關設定；`TRADE_SIMULATION_GAIN` 控制目標報酬（預設 30%）。
3. 執行 CLI：
   ```bash
   python -m nansen_sm_collector run-once
   ```
   > 若要改用實際 Nansen API 資料，可加上 `--no-use-mock`（需確保 API 權限足夠）。
   - EventFilterSet 現在支援動態成交額門檻，想改用動態成交額門檻，可設定 `MIN_USD_NOTIONAL_DYNAMIC=true`，並調整 `MIN_USD_NOTIONAL_QUANTILE`、`MIN_USD_NOTIONAL_LOOKBACK_MINUTES` 等參數；未滿足樣本數時會落回 `MIN_USD_NOTIONAL_FALLBACK`。評分器同時識別 DEX 大額買入/賣出與 Netflow 正負值，產出買賣訊號並在報告中顯示相關地址與錢包；若開啟模擬交易，系統也會記錄每次買入、計算目標價並於達標時寫入賣出紀錄與時間。評分器同時識別 DEX 大額買入/賣出與 Netflow 正負值，產出買賣訊號並在報告中顯示相關地址與錢包。

## Telegram通知腳本
- 執行 `python -m nansen_sm_collector run-once --no-use-mock` 會在報表 `reports/phase1_latest.md` 生成後，自動把該 Markdown 檔傳到 Telegram；失敗時會記錄 warning。
- 記得把 Bot token / chat id 換成實際值。若想同時傳其他檔案（像 `trade_candidates_latest.json`），可在 notifier 那段再延伸 `send_document` 呼叫。

## Telegram 控制面板 Bot
- 新增了 `python-telegram-bot` 介面，可透過 inline keyboard 控制 Zeabur 排程與立即執行 collector。
- 需要在 `.env`（或 Zeabur Environment）設定：
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_DASHBOARD_ALLOWED_CHAT_IDS`（可留空，或以逗號分隔允許使用者）
  - `ZEABUR_API_TOKEN`
  - `ZEABUR_PROJECT_ID`、`ZEABUR_SERVICE_ID`、`ZEABUR_HOURLY_JOB_ID`
  - 若 Zeabur API 路徑與預設不同，可透過 `ZEABUR_RUN_JOB_ENDPOINT`、`ZEABUR_ENABLE_JOB_ENDPOINT`、`ZEABUR_DISABLE_JOB_ENDPOINT`、`ZEABUR_JOB_STATUS_ENDPOINT` 覆寫。
- 啟動方式：`python -m nansen_sm_collector.bot`（已提供腳本 `scripts/start_dashboard_bot.sh`）。
- 控制面板提供「立即執行 / 啟用排程（1~24 小時）/ 停止排程 / 查看狀態」等按鈕，內部會呼叫 Zeabur API 包裝器（`ZeaburAPIClient`）。
- 若未設定 Zeabur API，Bot 會改用本地指令執行，按下「啟用排程」即預設每小時跑一次 (`ZEABUR_PIPELINE_COMMAND` 可自訂)。

## 0x Swap 交易腳本
- 腳本路徑：`scripts/zeroex_trade.py`，目前僅支援 `USDC`、`WETH`（大部分鏈）與 `WBNB`（BSC）作為 base token。
- 先於 `.env` 設定 `ZEROEX_API_KEY`、`ZEROEX_PRIVATE_KEY`、`ZEROEX_TAKER_ADDRESS`（或 `ZEROEX_WALLET_ADDRESS`）；RPC 可以用 `ZEROEX_RPC_URL` 作為預設值，或針對鏈別額外設定 `ZEROEX_RPC_URL_<CHAIN_ID>`（例如 `ZEROEX_RPC_URL_8453`）或 `ZEROEX_RPC_URL_<CHAIN_ALIAS>`（例如 `ZEROEX_RPC_URL_BASE`、`ZEROEX_RPC_URL_BSC`）。
- 模擬報價→ `python scripts/zeroex_trade.py simulate --chain-id 1 --base-token USDC --quote-token-address <addr> --amount 100`。
- 實際送單→ `python scripts/zeroex_trade.py trade --chain-id 1 --base-token USDC --quote-token-address <addr> --amount 100`（可加 `--no-wait-for-receipt` 與 `--rpc-url <url>` 強制指定 RPC）。
  - 這個指令是用USDC去買quote token
  - 如果要把quote token賣掉換回USDC，則使用指令：
   `python scripts/zeroex_trade.py trade \
      --chain-id 8453 \
      --base-token USDC \
      --quote-token-address <想賣出的token地址> \
      --direction QUOTE_TO_BASE \
      --amount 50` --amount 代表你想賣出的 USDT 數量

- 每次執行都會把報價／成交細節寫入 `collector.db` 的 `executed_trades`，包含交易量、狀態、報價 JSON、Tx hash 與時間（UTC 與本地時區）。

## API call計費
- 10000 API credit = 10 USD
- 每跑一次bot需要使用11次credit
- 如果每5分鐘跑一次，則每天會調用12*24*11=3168次credit
- 也就是說，10 USD只能跑不到3天

## 後續工作
- 依規劃補齊各模組實作邏輯與單元／整合測試。
- 實作資料庫遷移流程並補足資料集樣本。
- 建立 Phase STOP_GATE 驗證邏輯與報告輸出。

更多細節請參考 `docs/Nansen_SmartMoney_Collector_FullPlan.md`。
