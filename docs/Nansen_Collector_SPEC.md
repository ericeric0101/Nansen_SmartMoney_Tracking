# Nansen Collector Specification (Spec-as-Code)

## Overview
This document defines a modular, testable Python project for building a **Nansen Collector** — a system that retrieves, normalizes, enriches, scores, and stores on-chain data signals from the Nansen API.  
The project focuses on *data intelligence*, **not trading execution**.

---

## 1. Project Goals

- Retrieve on-chain activity from Nansen API (or mock data)
- Normalize multiple API responses into unified `Event` objects
- Enrich events with wallet, token, and liquidity metadata
- Apply required/forbidden filters
- Score events using weighted features to produce `Signal` objects
- Store events and signals in a database (SQLite/Postgres)
- Provide CLI and unit tests for all modules

---

## 2. Project Structure

```
nansen-collector/
├─ README.md
├─ pyproject.toml
├─ .env.example
├─ src/
│  ├─ config/
│  │  ├─ settings.py
│  │  └─ validators.py
│  ├─ core/
│  │  ├─ types.py
│  │  ├─ errors.py
│  │  ├─ logging.py
│  │  └─ utils.py
│  ├─ adapters/
│  │  ├─ nansen_api.py
│  │  ├─ mock_nansen.py
│  │  └─ price_news_stub.py
│  ├─ data/
│  │  ├─ db.py
│  │  ├─ schemas.py
│  │  └─ repos.py
│  ├─ collectors/
│  │  ├─ normalize.py
│  │  ├─ enrich.py
│  │  ├─ filters.py
│  │  ├─ scorer.py
│  │  └─ pipeline.py
│  ├─ cli/
│  │  └─ main.py
│  └─ services/
│     └─ wallet_alpha.py
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
└─ .github/workflows/ci.yml
```

---

## 3. Core Concepts

- **Event** → Raw on-chain event normalized from Nansen API
- **Signal** → Scored and filtered candidate for trading logic
- **Collector** → Data pipeline: fetch → normalize → enrich → filter → score
- **Adapters** → Isolated data providers (Nansen, mock, price/news stub)
- **Repositories** → Database access and persistence layer
- **Services** → Higher-level analytics (wallet alpha scoring)

---

## 4. Configuration

### `.env.example`
```
NANSEN_API_KEY=your-nansen-api-key
DB_URL=sqlite:///./collector.db
MIN_USD_NOTIONAL=100000
VOLUME_Z_TH_1H=1.645
LIQUIDITY_MIN_SCORE=0.5
THRESH_SIGNAL=0.65
COOLDOWN_MIN=30
W_USD=0.25
W_LABEL=0.25
W_ALPHA=0.25
W_VOLZ=0.15
W_BIAS=0.10
PENALTY_EXPLOSIVE=0.15
PENALTY_LOW_LIQ=0.10
TZ=Europe/Berlin
```

---

## 5. Core Models

### Event (core/types.py)
Represents a normalized blockchain transaction.

| Field | Type | Description |
|-------|------|-------------|
| ts | datetime | Timestamp |
| source | str | Data source (dex_trades, discovery, etc.) |
| chain | str | Chain name |
| token_address | str | Token contract |
| symbol | str | Symbol |
| wallet_address | str | Origin wallet |
| is_buy | bool | Direction |
| usd_notional | float | Trade value |
| tx_hash | str | Transaction hash |
| features | dict | Extended attributes |

### Signal
| Field | Type | Description |
|-------|------|-------------|
| ts | datetime | Timestamp |
| token_address | str | Token |
| symbol | str | Symbol |
| score | float | Weighted score |
| reason | str | Scoring summary |
| features | dict | All calculated inputs |
| candidate | bool | Whether it passes threshold |

---

## 6. Collectors

### normalize.py
- Converts raw JSON from each Nansen endpoint into `Event` objects.

### enrich.py
- Adds wallet labels, liquidity, risk flags, and token metrics to Events.

### filters.py
- Enforces **must** and **must-not** conditions:  
  - Tradable token on CEX  
  - Liquidity above threshold  
  - Not blacklisted  
  - Cooldown respected  

### scorer.py
Applies weighted scoring model:

```
score = W_USD*usd_score + W_LABEL*label_score + W_ALPHA*alpha_score +
        W_VOLZ*volz_score + W_BIAS*bias_score - penalties
```

### pipeline.py
Coordinates all steps:
1. Fetch data from adapters
2. Normalize → Enrich → Filter → Score
3. Store results via `repos`
4. Return list of Signals

---

## 7. Database Schema

Tables: `wallets`, `tokens`, `wallet_token_stats`, `events`, `signals`.

Each table has clear PKs, FKs, and JSON columns for flexibility.

---

## 8. Services

### wallet_alpha.py
Calculates performance-based scores for wallets based on historical events.  
Methods:
- `compute(address: str) -> float`
- `refresh_all(topN: int = 1000)`

---

## 9. Testing

- **Unit tests:** Each module (normalize, enrich, filters, scorer, repos, wallet_alpha)
- **Integration tests:** Full pipeline with mock adapters and in-memory SQLite

---

## 10. CLI Interface

Example usage:
```bash
python -m nansen_collector run-once --use-mock true
python -m nansen_collector refresh-wallet-alpha
```

CLI arguments can override environment variables.

---

## 11. Acceptance Criteria

- `run-once` executes full pipeline, produces Signals in DB
- Filters correctly block ineligible events
- Scorer ranks and thresholds events properly
- Wallet alpha scoring is consistent and time-decayed
- All tests pass via CI

---

## 12. Extensibility

- Add new detectors (multi-wallet correlation)
- Add new scoring features (depth, slippage)
- Replace rule-based scoring with ML-based model
- Extend adapters for new data sources

---

## 13. Design Guarantees

- No trading or financial execution code
- All external I/O can be mocked for testing
- Fully type-annotated and documented
- Clean architecture and maintainable separation of concerns
