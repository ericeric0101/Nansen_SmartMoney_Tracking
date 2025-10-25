# Nansen Smart Money Collector — Full Build Plan (Unified Spec + Strategy)
**Audience:** Codex / Gemini CLI  
**Goal:** Build a production-grade, modular system to *collect → normalize → enrich → score* on-chain signals from **Nansen API** first; then (only if needed) extend to other data sources; finally add automated trading and monitoring.  
**Important product constraints:** No NFT tracking. No L2 gas tracking. Phase‑1 uses **Nansen only**. Additional APIs are optional *after we validate Phase‑1 accuracy.*

---

## 0) Executive Summary
- We unify two streams of work:
  - **Code architecture spec** (modules, folders, tests, CI).
  - **Smart Money strategy** (how to detect tokens & wallets worth tracking).
- We ship in **gated phases**. **After each phase, STOP and wait for manual tests/approval** (see `STOP_GATE` notes). Only proceed when the user says “GO NEXT”.

---

## 1) Phased Roadmap (with hard stop gates)

### Phase 1 — Nansen‑only Collector (no trading)
**Scope:** Pull Nansen endpoints, normalize to Events, enrich, filter, **score to Signals**, persist to DB, CLI & tests.  
**APIs (Nansen only):**
- **Token God Mode / Smart Money**
  - `POST /api/v1/tgm/dex-trades` — per‑token DEX trades, with *only smart money* filter
  - `POST /api/v1/tgm/token-screener` — token screener / anomalies
  - `POST /api/v1/smart-money/netflows` — smart trader & fund netflows
  - `GET  /api/v1/profiler/address-labels` — address labels (smart_money, fund, etc.)
**Outputs:**
- **Signals**: top tokens + reasons (e.g., `smart_buy_big+pos_netflow`), and **Wallet Library** candidates.  
- **No trading**; optional **Telegram/Console** alerts only.
**Deliverables:**
- CLI: `run-once` (pure Nansen), `refresh-wallet-alpha` (placeholder, rule-based).
- Unit & integration tests (mockable IO).
- SQLite by default; easy swap to Postgres.
**STOP_GATE:** Print a summary report + write `phase1.ok` once tests pass. **Halt.**

### Phase 2 — Trading Execution (minimal, optional testnet)
**Scope:** Add *strategy engine* + *risk manager* + *broker adapter* (e.g., Binance testnet).  
**Inputs:** Signals from Phase‑1 (score ≥ threshold, liquidity OK, not blacklisted, cooldown OK).  
**Behaviors:** Open/close positions with basic SL/TP; DRY‑RUN or Testnet first.  
**Deliverables:** `run-live` process + per‑trade audit logs + metrics.  
**STOP_GATE:** Print trade simulation report + write `phase2.ok`. **Halt.**

### Phase 3 — External Data Enrichment (only if Phase‑1 accuracy insufficient)
**Add (feature‑flagged) providers:**  
- **Crypto news:** CryptoPanic (sentiment/latest).  
- **Macro/flows:** Glassnode (stablecoin flows/SSR) or DeFiLlama (TVL, stablecoins).  
- **Wallet introspection:** DeBank Cloud (address portfolio & protocol usage).  
**Rules:** Keep **Nansen as the primary** signal source. Use others only to *confirm or veto* signals.  
**STOP_GATE:** Print ablation study (Nansen‑only vs Nansen+X) + write `phase3.ok`. **Halt.**

### Phase 4 — Backtesting, Monitoring & Ops
- Event‑driven backtest (signals→fills→P&L, fee + slippage).  
- Daily/weekly reports; SLOs (data freshness, pipeline success).  
- Optional feature store / model registry (if moving to ML later).  
**STOP_GATE:** Backtest report saved + `phase4.ok`. **Halt.**

> **Note:** Phase labels are strict. Do not code Phase‑2+ items before Phase‑1 `phase1.ok` exists, etc.

---

## 2) Project Structure (single repo)

```
nansen-sm-collector/
├─ README.md
├─ pyproject.toml
├─ .env.example
├─ src/
│  ├─ config/
│  │  ├─ settings.py            # pydantic Settings; env & flags
│  │  └─ validators.py          # ranges/types checks
│  ├─ core/
│  │  ├─ types.py               # Pydantic models: Event, Signal, Wallet, Token
│  │  ├─ errors.py              # custom exceptions
│  │  ├─ logging.py             # structured logging
│  │  └─ utils.py               # retry, time, math helpers
│  ├─ adapters/
│  │  ├─ nansen_api.py          # Phase-1: only provider
│  │  ├─ mock_nansen.py         # deterministic fixtures for tests
│  │  ├─ news_cryptopanic.py    # Phase-3 (feature-flagged)
│  │  ├─ glassnode_llama.py     # Phase-3 (feature-flagged)
│  │  └─ debank_cloud.py        # Phase-3 (feature-flagged)
│  ├─ data/
│  │  ├─ db.py                  # engine/session + migrations
│  │  ├─ schemas.py             # SQLAlchemy ORM (wallets/tokens/events/signals/...)
│  │  └─ repos.py               # repositories (CRUD + aggregates)
│  ├─ collectors/
│  │  ├─ normalize.py           # raw JSON -> Event
│  │  ├─ enrich.py              # +labels, liquidity, netflow, vol_z...
│  │  ├─ filters.py             # must/must-not (tradable, blacklist, cooldown)
│  │  ├─ scorer.py              # weighted scoring; reasons
│  │  └─ pipeline.py            # fetch→normalize→enrich→filter→score→persist
│  ├─ strategy/
│  │  ├─ engine.py              # Phase-2: action from Signal (BUY/SELL/NOOP)
│  │  └─ rules.py               # thresholds, cooldown, finalize
│  ├─ risk/
│  │  └─ manager.py             # sizing caps, SL/TP, per-symbol limits
│  ├─ broker/
│  │  ├─ binance.py             # Phase-2: DRY-RUN/testnet first
│  │  └─ paper.py               # paper-trade fallback
│  ├─ alerts/
│  │  ├─ telegram.py            # optional; Phase-1 allowed
│  │  └─ report.py              # daily summaries
│  ├─ cli/
│  │  └─ main.py                # run-once / run-live / refresh-wallet-alpha
│  └─ services/
│     └─ wallet_alpha.py        # self-built alpha score (rule-based V1)
├─ tests/
│  ├─ unit/
│  │  ├─ test_normalize.py
│  │  ├─ test_enrich.py
│  │  ├─ test_filters.py
│  │  ├─ test_scorer.py
│  │  ├─ test_repos.py
│  │  └─ test_wallet_alpha.py
│  └─ integration/
│     └─ test_pipeline_with_mock.py
└─ .github/workflows/ci.yml      # lint + tests
```

**Design principles**
- Nansen is the **source of truth** for Ph‑1. External data is opt‑in via flags in Ph‑3.
- Strict DI/abstractions allow unit tests to run **without** network calls.
- Everything type‑annotated + docstrings; zero trading code in Ph‑1.

---

## 3) Data Contracts (Pydantic)

**Event** (normalized)
- `ts: datetime`
- `source: Literal["dex_trades","token_screener","netflow","holders","transfers"]`
- `chain: str`
- `token_address: str`
- `symbol: str`
- `wallet_address: Optional[str]`
- `is_buy: Optional[bool]`
- `usd_notional: Optional[float]`
- `tx_hash: Optional[str]`
- `features: Dict[str, Any]` *(pool, price_impact, labels, netflow, vol_z, liq_score, risk_flags, etc.)*

**Signal**
- `ts: datetime`
- `token_address: str`
- `symbol: str`
- `score: float [0,1]`
- `reason: str` *(e.g., "smart_buy_big+pos_netflow")*
- `features: Dict[str, Any]`
- `candidate: bool` *(score ≥ threshold)*

---

## 4) DB Schema (SQLAlchemy ORM)
- `wallets(address PK, labels JSON, alpha_score FLOAT, winrate_30d FLOAT, status TEXT, notes TEXT)`
- `tokens(chain, token_address PK, symbol, liquidity_score FLOAT, tradable_on_cex BOOL, cex_symbol TEXT, risk_flags JSON, vol_z_1h FLOAT)`
- `wallet_token_stats(address, token_address, entries INT, avg_size_usd FLOAT, avg_profit_pct FLOAT, last_entry_ts DATETIME)`
- `events(id PK, ts, source, chain, token_address, symbol, wallet_address, is_buy, usd_notional, tx_hash, raw_json JSON)`
- `signals(id PK, ts, token_address, symbol, score, reason, candidate BOOL, features JSON)`

---

## 5) Scoring (Phase‑1 default, rule‑based)
```
score = W_USD*usd_score
      + W_LABEL*label_score
      + W_ALPHA*alpha_score
      + W_VOLZ*volz_score
      + W_BIAS*bias_score
      - PENALTY_EXPLOSIVE*I(explosive)
      - PENALTY_LOW_LIQ*I(low_liq)
```
- Start weights (env‑configurable):  
  - `W_USD=0.25, W_LABEL=0.25, W_ALPHA=0.25, W_VOLZ=0.15, W_BIAS=0.10`  
  - `PENALTY_EXPLOSIVE=0.15, PENALTY_LOW_LIQ=0.10`
- **Must conditions:** tradable on our CEX, liquidity ≥ threshold, not blacklisted, cooldown ok.
- **Not ALL detectors must be true**; thresholding is by final `score` (e.g., ≥0.65 → candidate).

---

## 6) Configuration (`.env.example`)
```
# Phase flags
PHASE=1
FEATURE_NEWS=false
FEATURE_DEBANK=false
FEATURE_GLASSNODE_LLAMA=false

# Nansen
NANSEN_API_KEY=your-nansen-api-key
NANSEN_BASE_URL=https://api.nansen.ai

# DB
DB_URL=sqlite:///./collector.db

# Thresholds
MIN_USD_NOTIONAL=100000
VOLUME_Z_TH_1H=1.645
LIQUIDITY_MIN_SCORE=0.5
THRESH_SIGNAL=0.65
COOLDOWN_MIN=30

# Weights & penalties
W_USD=0.25
W_LABEL=0.25
W_ALPHA=0.25
W_VOLZ=0.15
W_BIAS=0.10
PENALTY_EXPLOSIVE=0.15
PENALTY_LOW_LIQ=0.10

# Ops
TZ=Europe/Berlin
```

---

## 7) CLI (strict gates)
- `python -m nansen_sm_collector run-once --use-mock false`
  - Runs Phase‑1 pipeline (Nansen only).  
  - Writes a **markdown report** with **Top Tokens** + **Top Wallets** and reasons.
  - **On success:** create `./phase1.ok` and **exit**.
- `python -m nansen_sm_collector run-live`
  - Phase‑2 only (requires `phase1.ok`). DRY‑RUN/testnet allowed via env.
  - **On success:** create `./phase2.ok` and **exit**.
- `python -m nansen_sm_collector run-ablation`
  - Phase‑3 only (requires `phase2.ok`). Compares Nansen‑only vs Nansen+{news,flows,debank}.  
  - **On success:** create `./phase3.ok` and **exit`.

> **Codex/Gemini CLI instruction:** if the required `*.ok` file is missing, **stop immediately** and print a message asking the user to run the previous phase’s tests.

---

## 8) Acceptance Criteria (per phase)

**Phase‑1**
- All unit & integration tests pass with `mock_nansen` and with limited live calls (rate‑limited).
- DB contains: ≥1 `signals` row; `events` persisted; schema migrations applied.
- Report lists *Top‑N tokens* and *Top‑N wallets* with `score`, `reasons`, and raw evidence links.
- Flags `FEATURE_*` are ignored (must be false). **Creates `phase1.ok`**.

**Phase‑2**
- Strategy selects signals with `score≥THRESH_SIGNAL` and executes **paper or testnet trades**.
- Risk manager caps exposure per symbol & in total; SL/TP simulated.
- Per‑trade audit log contains inputs (signal snapshot) and outputs (order qty, price, status).
- **Creates `phase2.ok`**.

**Phase‑3**
- Each external provider is optional and feature‑flagged.
- Ablation report: Precision/Recall or hit‑rate uplift vs Nansen‑only.
- **Creates `phase3.ok`**.

**Phase‑4**
- Backtest over ≥3 months of signals; include fees & slippage.
- Monitoring dashboards & daily report job defined.
- **Creates `phase4.ok`**.

---

## 9) What to Build Exactly (Phase‑1 only)

### 9.1 Nansen API client (`adapters/nansen_api.py`)
- Auth: API key in header (`x-api-key` or `Authorization: Bearer ...` depending on plan).  
- Endpoints (JSON body):
  - `POST /api/v1/tgm/dex-trades` — filterable by token, chain, time window, `onlySmartMoney=true`.
  - `POST /api/v1/tgm/token-screener` — anomalies & holders/flows context.
  - `POST /api/v1/smart-money/netflows` — aggregated netflow by cohort.
  - `GET  /api/v1/profiler/address-labels?address=...` — labels for wallet(s).
- Must implement: retries, backoff, timeout, idempotent logging, schema validation.

### 9.2 Normalizers (`collectors/normalize.py`)
Transform each response into `Event`:
- `dex_trades`: map trade rows → `Event(source="dex_trades", is_buy, usd_notional, wallet, tx_hash, chain, token)`.
- `token_screener`: anomalies → `Event(source="token_screener", features.volume_jump, ... )`.
- `netflows`: → `Event(source="netflow", features.smart_money_netflow)`.

### 9.3 Enricher (`collectors/enrich.py`)
Attach:
- `wallet_labels` (from Nansen profiler/labels).  
- `wallet_alpha` (from `services/wallet_alpha.py` rule‑based v1).  
- `token_liquidity`, `risk_flags`, `volume_z_1h` (repos aggregation).

### 9.4 Filters (`collectors/filters.py`)
- `tradable_on_cex`, `liquidity_ok`, `not_blacklisted`, `cooldown_ok`.

### 9.5 Scorer (`collectors/scorer.py`)
- Compute feature scores, penalties, total `score` and textual `reason` string.  
- Threshold to mark `candidate` signals.

### 9.6 Pipeline (`collectors/pipeline.py`)
- Orchestration: fetch → normalize → enrich → filter → score → persist.  
- Return `List[Signal]` and write a human‑readable markdown summary.

### 9.7 Wallet Alpha v1 (`services/wallet_alpha.py`)
- Rule‑based: recent hit‑rate over 1h/4h/24h windows from historical events (no price API yet).  
- Time‑decay weighting (90d > 180d). Store `alpha_score ∈ [0,1]` on wallet.

### 9.8 Tests
- **Unit:** normalize, enrich, filters, scorer, repos, wallet_alpha.  
- **Integration:** pipeline with `mock_nansen` + in‑memory SQLite.

---

## 10) Phase‑3 Optional Providers (feature‑flagged stubs now)
- `adapters/news_cryptopanic.py`: fetch latest headlines & sentiment for symbols/keywords.
- `adapters/glassnode_llama.py`: stablecoin flows, SSR, TVL (coarse macro confirmation).
- `adapters/debank_cloud.py`: wallet portfolios & behavior (use only for tie‑breaks).
> These must be **OFF** in Ph‑1. Only enable after we have Phase‑1 baselines.

---

## 11) Strategy Notes (from Smart Money guide, adapted)
- Focus on tokens with **Smart Fund/Smart Trader net inflow**, large *USD notional* buys, and **rising volume z‑score**, but **avoid** already‑explosive moves and thin liquidity.
- Build a **Wallet Library**: addresses with consistent post‑buy performance & clear labels.  
- L2 gas and NFT tracking are **out of scope** by design.

---

## 12) Runbook (for you, the human)
1. Run Phase‑1: `python -m nansen_sm_collector run-once`  
2. Inspect the markdown report (Top Tokens / Top Wallets + reasons).  
3. If accurate enough → say **“GO NEXT”** to proceed to Phase‑2; else adjust thresholds & rerun.  
4. Only if signals are weak, enable Phase‑3 flags and run ablation.

---

## 13) Legal & Safety
- No financial advice; paper/testnet first.  
- API keys: least privilege; never commit secrets.  
- Respect API rate limits and ToS.

---

## 14) Appendix — Environment & Defaults
See `.env.example` in this document. All thresholds are configurable.

---

## 15) STOP_GATES (machine‑readable)
- If `PHASE==1`: forbid imports from `strategy`, `risk`, `broker`; error if used.  
- If `PHASE>=2` but `./phase1.ok` missing: **exit 2** with message: *“Phase‑1 verification required.”*  
- If `PHASE>=3` but `./phase2.ok` missing: **exit 2** with message: *“Phase‑2 verification required.”*  
- Each phase must create its `.ok` file on success and **exit instead of chaining**.
