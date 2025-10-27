from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, List, Optional

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """應用程式環境設定。"""

    phase: Annotated[int, Field(ge=1, le=4)] = Field(1, alias="PHASE")
    feature_news: bool = Field(False, alias="FEATURE_NEWS")
    feature_debank: bool = Field(False, alias="FEATURE_DEBANK")
    feature_glassnode_llama: bool = Field(False, alias="FEATURE_GLASSNODE_LLAMA")

    nansen_api_key: str = Field(..., alias="NANSEN_API_KEY")
    nansen_base_url: HttpUrl = Field("https://api.nansen.ai", alias="NANSEN_BASE_URL")
    nansen_chains: str = Field("ethereum,solana,base", alias="NANSEN_CHAINS")
    nansen_dex_token_address: Optional[str] = Field(None, alias="NANSEN_DEX_TOKEN_ADDRESS")
    nansen_enable_wallet_labels: bool = Field(True, alias="NANSEN_ENABLE_WALLET_LABELS")
    nansen_dex_include_labels: str = Field("Fund,Smart Trader", alias="NANSEN_DEX_INCLUDE_LABELS")
    nansen_dex_exclude_labels: str = Field("", alias="NANSEN_DEX_EXCLUDE_LABELS")
    nansen_dex_min_age_days: int = Field(1, alias="NANSEN_DEX_MIN_AGE_DAYS")
    nansen_dex_max_age_days: int = Field(365, alias="NANSEN_DEX_MAX_AGE_DAYS")
    nansen_dex_trade_max_usd: Optional[float] = Field(None, alias="NANSEN_DEX_TRADE_MAX_USD")
    nansen_dex_date_from: str = Field("24H_AGO", alias="NANSEN_DEX_DATE_FROM")
    nansen_dex_date_to: str = Field("NOW", alias="NANSEN_DEX_DATE_TO")

    db_url: str = Field("sqlite:///./collector.db", alias="DB_URL")

    min_usd_notional: int = Field(100000, alias="MIN_USD_NOTIONAL")
    min_usd_notional_dynamic: bool = Field(False, alias="MIN_USD_NOTIONAL_DYNAMIC")
    min_usd_notional_quantile: float = Field(0.75, alias="MIN_USD_NOTIONAL_QUANTILE")
    min_usd_notional_lookback_minutes: int = Field(10080, alias="MIN_USD_NOTIONAL_LOOKBACK_MINUTES")
    min_usd_notional_min_samples: int = Field(30, alias="MIN_USD_NOTIONAL_MIN_SAMPLES")
    min_usd_notional_fallback: int = Field(10000, alias="MIN_USD_NOTIONAL_FALLBACK")
    netflow_min_positive: float = Field(0.0, alias="NETFLOW_MIN_POSITIVE")
    trade_simulation_enabled: bool = Field(False, alias="TRADE_SIMULATION_ENABLED")
    trade_simulation_gain: float = Field(0.3, alias="TRADE_SIMULATION_GAIN")
    gecko_terminal_base_url: str = Field("https://api.geckoterminal.com/api/v2", alias="GECKO_TERMINAL_BASE_URL")
    gecko_terminal_version: str = Field("20230203", alias="GECKO_TERMINAL_VERSION")
    gecko_terminal_market_data_enabled: bool = Field(False, alias="GECKO_TERMINAL_MARKET_DATA_ENABLED")
    gecko_terminal_ohlcv_timeframe: str = Field("hour", alias="GECKO_TERMINAL_OHLCV_TIMEFRAME")
    gecko_terminal_ohlcv_limit: int = Field(24, alias="GECKO_TERMINAL_OHLCV_LIMIT")
    gecko_terminal_trade_min_usd: float = Field(0.0, alias="GECKO_TERMINAL_TRADE_MIN_USD")
    gecko_terminal_token_pools: str = Field("{}", alias="GECKO_TERMINAL_TOKEN_POOLS")
    volume_z_th_1h: float = Field(1.645, alias="VOLUME_Z_TH_1H")
    liquidity_min_score: float = Field(0.5, alias="LIQUIDITY_MIN_SCORE")
    thresh_signal: float = Field(0.65, alias="THRESH_SIGNAL")
    cooldown_min: int = Field(30, alias="COOLDOWN_MIN")
    dump_phase1_raw_events: bool = Field(False, alias="DUMP_PHASE1_RAW_EVENTS")
    telegram_notify_enabled: bool = Field(False, alias="TELEGRAM_NOTIFY_ENABLED")
    telegram_bot_token: Optional[str] = Field(None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = Field(None, alias="TELEGRAM_CHAT_ID")

    weight_usd: float = Field(0.25, alias="W_USD")
    weight_label: float = Field(0.25, alias="W_LABEL")
    weight_alpha: float = Field(0.25, alias="W_ALPHA")
    weight_volz: float = Field(0.15, alias="W_VOLZ")
    weight_bias: float = Field(0.10, alias="W_BIAS")
    penalty_explosive: float = Field(0.15, alias="PENALTY_EXPLOSIVE")
    penalty_low_liq: float = Field(0.10, alias="PENALTY_LOW_LIQ")

    timezone: str = Field("Europe/Berlin", alias="TZ")

    model_config = SettingsConfigDict(env_file=(".env",), env_file_encoding="utf-8", extra="ignore")

    @property
    def phase_ok_file(self) -> Path:
        """取得當前階段驗證檔案路徑。"""

        return Path(f"phase{self.phase}.ok")

    @property
    def chains(self) -> List[str]:
        return [item.strip() for item in self.nansen_chains.split(",") if item.strip()]

    @property
    def dex_include_labels(self) -> List[str]:
        return [item.strip() for item in self.nansen_dex_include_labels.split(",") if item.strip()]

    @property
    def dex_exclude_labels(self) -> List[str]:
        return [item.strip() for item in self.nansen_dex_exclude_labels.split(",") if item.strip()]

    @property
    def gecko_terminal_token_pools_map(self) -> dict[str, dict[str, list[str]]]:
        try:
            raw = json.loads(self.gecko_terminal_token_pools)
        except json.JSONDecodeError:
            return {}
        result: dict[str, dict[str, list[str]]] = {}
        if not isinstance(raw, dict):
            return result
        for chain, tokens in raw.items():
            if not isinstance(tokens, dict):
                continue
            normalized_chain = str(chain).lower()
            result.setdefault(normalized_chain, {})
            for token_address, pools in tokens.items():
                addr = str(token_address).lower()
                if isinstance(pools, list):
                    pool_list = [str(pool).lower() for pool in pools if pool]
                elif isinstance(pools, str):
                    pool_list = [pools.lower()]
                else:
                    continue
                result[normalized_chain][addr] = pool_list
        return result

    @field_validator("nansen_dex_trade_max_usd", mode="before")
    @classmethod
    def _empty_trade_max(cls, value: Optional[str | float]) -> Optional[float]:
        if value in (None, "", "null", "None"):
            return None
        if isinstance(value, str):
            return float(value)
        return value


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """載入並快取設定。"""

    return AppSettings()
