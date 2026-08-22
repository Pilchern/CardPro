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


def test_ebay_alerts_mailbox_defaults_to_all_mail_when_absent(tmp_path, monkeypatch):
    """The fixture's settings.json has no "mailbox" key -- older configs
    written before this setting existed must still load, defaulting to
    All Mail (see ebay_email_alerts.DEFAULT_MAILBOX)."""
    _write_config_files(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.ebay_alerts_mailbox == "[Gmail]/All Mail"


def test_ebay_alerts_mailbox_loads_when_present(tmp_path, monkeypatch):
    _write_config_files(tmp_path)
    settings_path = tmp_path / "config" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["ebay_alerts"]["mailbox"] = "INBOX"
    settings_path.write_text(json.dumps(settings))
    _base_env(monkeypatch)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    cfg = config_module.load_config()

    assert cfg.ebay_alerts_mailbox == "INBOX"


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



def _load_with(tmp_path, monkeypatch, settings_extra=None, watchlist_extra=None):
    """Same setup as _write_config_files, plus optional extra top-level keys.
    Used by the CardPro 2.0 settings tests below."""
    _write_config_files(tmp_path)
    if settings_extra:
        path = tmp_path / "config" / "settings.json"
        data = json.loads(path.read_text())
        data.update(settings_extra)
        path.write_text(json.dumps(data))
    if watchlist_extra:
        path = tmp_path / "config" / "watchlist.json"
        data = json.loads(path.read_text())
        data.update(watchlist_extra)
        path.write_text(json.dumps(data))
    _base_env(monkeypatch)
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")
    return config_module.load_config()


def test_valuation_and_economics_settings_load(tmp_path, monkeypatch):
    # The new quality gates and resale assumptions must come from config, not
    # from constants buried in code -- tuning them is a normal operation.
    cfg = _load_with(
        tmp_path,
        monkeypatch,
        settings_extra={
            "valuation": {"min_comps_required": 5, "stale_after_days": 30, "require_flag_eligible_comp": False},
            "economics": {"marketplace_fee_pct": 12.0, "resale_haircut_pct": 8.0},
            "auctions": {"required_margin_pct": 35, "ending_soon_hours": 6},
            "alerts": {"immediate_alert_min_savings_dollars": 250, "immediate_alert_min_discount_pct": 45},
        },
    )
    assert cfg.valuation_min_comps_required == 5
    assert cfg.valuation_stale_after_days == 30
    assert cfg.require_flag_eligible_comp is False
    assert cfg.fee_marketplace_pct == 12.0
    assert cfg.resale_haircut_pct == 8.0
    assert cfg.auction_required_margin_pct == 35
    assert cfg.auction_ending_soon_hours == 6
    assert cfg.immediate_alert_min_savings_dollars == 250


def test_missing_new_sections_fall_back_to_safe_defaults(tmp_path, monkeypatch):
    # A settings.json written before CardPro 2.0 must keep working -- same
    # backwards-compatibility rule applied to stored data. Note the default
    # for require_flag_eligible_comp is True: an old config must not silently
    # re-enable the circular price-tier comparison.
    cfg = _load_with(tmp_path, monkeypatch)
    assert cfg.valuation_half_life_days == 30
    assert cfg.valuation_stale_after_days == 45
    assert cfg.require_flag_eligible_comp is True
    assert cfg.resale_haircut_pct == 5.0
    assert cfg.auction_ending_soon_hours == 24


def test_target_cards_load_from_the_watchlist(tmp_path, monkeypatch):
    cfg = _load_with(
        tmp_path,
        monkeypatch,
        watchlist_extra={
            "target_cards": [
                {"label": "T", "player": "Michael Jordan", "grader": "PSA", "grade": "10", "buy_zone": 5000}
            ]
        },
    )
    assert len(cfg.target_cards) == 1
    assert cfg.target_cards[0].player == "Michael Jordan"
    assert cfg.target_cards[0].buy_zone == 5000


def test_target_cards_default_to_empty_when_absent(tmp_path, monkeypatch):
    assert _load_with(tmp_path, monkeypatch).target_cards == []


def test_cheap_card_settings_load(tmp_path, monkeypatch):
    cfg = _load_with(
        tmp_path,
        monkeypatch,
        settings_extra={
            "cheap_cards": {
                "enabled": False,
                "price_ceiling": 25.0,
                "min_discount_pct": 60,
                "min_savings_dollars": 5,
                "require_desirable_attribute": False,
            }
        },
    )
    assert cfg.cheap_cards_enabled is False
    assert cfg.cheap_price_ceiling == 25.0
    assert cfg.cheap_min_discount_pct == 60
    assert cfg.cheap_min_savings_dollars == 5
    assert cfg.cheap_require_desirable_attribute is False


def test_cheap_card_defaults_keep_the_junk_filter_on(tmp_path, monkeypatch):
    # A settings.json without the section must not silently disable the
    # commodity filter -- that would surface every $2 base common.
    cfg = _load_with(tmp_path, monkeypatch)
    assert cfg.cheap_cards_enabled is True
    assert cfg.cheap_price_ceiling == 10.0
    assert cfg.cheap_min_discount_pct == 50.0
    assert cfg.cheap_require_desirable_attribute is True
