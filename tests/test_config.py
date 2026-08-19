import json

from src import config as config_module


def _write_config_files(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "watchlist.json").write_text(json.dumps({"players": ["Michael Jordan"]}))
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps(
            {
                "discount_threshold_pct": 30,
                "min_savings_dollars": 0,
                "ebay": {
                    "category_id": "212",
                    "marketplace_id": "EBAY_US",
                    "active_listing_limit_per_player": 50,
                    "sold_lookback_days": 60,
                    "min_comps_required": 3,
                },
                "ebay_alerts": {
                    "enabled": False,
                    "sender_contains": "ebay.com",
                    "lookback_days": 2,
                    "price_history_path": "data/ebay_alert_price_history.json",
                    "price_history_max_age_days": 180,
                },
                "craigslist": {"site": "chicago", "category": "sss"},
                "dedupe": {"seen_listings_path": "data/seen_listings.json", "prune_after_days": 120},
                "email": {"subject_prefix": "[Card Deals]"},
            }
        )
    )


def _base_env(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "fake@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "fakepassword")
    monkeypatch.setenv("EMAIL_TO", "fake@gmail.com")


def test_ebay_not_required_when_env_vars_absent(tmp_path, monkeypatch):
    _write_config_files(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.ebay_client_id is None
    assert cfg.ebay_client_secret is None


def test_unfilled_placeholder_values_treated_as_absent(tmp_path, monkeypatch):
    _write_config_files(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setenv("EBAY_CLIENT_ID", "your_ebay_client_id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "your_ebay_client_secret")
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.ebay_client_id is None
    assert cfg.ebay_client_secret is None


def test_real_ebay_values_pass_through(tmp_path, monkeypatch):
    _write_config_files(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setenv("EBAY_CLIENT_ID", "real-id-123")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "real-secret-456")
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.ebay_client_id == "real-id-123"
    assert cfg.ebay_client_secret == "real-secret-456"


def test_ebay_alerts_config_loads(tmp_path, monkeypatch):
    _write_config_files(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.ebay_alerts_enabled is False
    assert cfg.ebay_alerts_sender_contains == "ebay.com"
    assert cfg.ebay_alerts_lookback_days == 2
    assert cfg.ebay_alert_price_history_path == tmp_path / "data" / "ebay_alert_price_history.json"
    assert cfg.ebay_alert_price_history_max_age_days == 180


def test_min_savings_dollars_loads(tmp_path, monkeypatch):
    _write_config_files(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.min_savings_dollars == 0


def test_player_tiers_defaults_to_empty_dict_when_absent(tmp_path, monkeypatch):
    _write_config_files(tmp_path)  # watchlist.json here has no player_tiers key at all
    _base_env(monkeypatch)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.player_tiers == {}


def test_player_tiers_loads_when_present(tmp_path, monkeypatch):
    _write_config_files(tmp_path)
    (tmp_path / "config" / "watchlist.json").write_text(
        json.dumps({"players": ["Michael Jordan", "Caleb Wilson"], "player_tiers": {"Caleb Wilson": "young_core"}})
    )
    _base_env(monkeypatch)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.player_tiers == {"Caleb Wilson": "young_core"}


def test_missing_gmail_still_raises(tmp_path, monkeypatch):
    _write_config_files(tmp_path)
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    try:
        config_module.load_config()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "GMAIL_ADDRESS" in str(e)
