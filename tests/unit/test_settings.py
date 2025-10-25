from nansen_sm_collector.config.settings import AppSettings


def test_settings_default_values() -> None:
    settings = AppSettings(
        NANSEN_API_KEY="dummy-key",
        NANSEN_ENABLE_WALLET_LABELS=True,
        NANSEN_DEX_INCLUDE_LABELS="Fund,Smart Trader",
        NANSEN_DEX_EXCLUDE_LABELS="",
        NANSEN_CHAINS="ethereum,solana,base",
    )
    assert settings.phase == 1
    assert settings.feature_news is False
    assert settings.chains == ["ethereum", "solana", "base"]
    assert settings.nansen_enable_wallet_labels is True
    assert settings.dex_include_labels == ["Fund", "Smart Trader"]
    assert settings.dex_exclude_labels == []
