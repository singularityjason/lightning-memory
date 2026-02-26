"""Tests for configuration module."""

import json

from lightning_memory.config import Config, DEFAULT_RELAYS, load_config, reset_cache


class TestConfig:
    def setup_method(self):
        reset_cache()

    def test_defaults(self):
        cfg = Config()
        assert cfg.relays == list(DEFAULT_RELAYS)
        assert cfg.sync_on_start is False
        assert cfg.sync_on_stop is True
        assert cfg.sync_timeout_seconds == 30
        assert cfg.max_events_per_sync == 500

    def test_to_dict(self):
        cfg = Config()
        d = cfg.to_dict()
        assert "relays" in d
        assert "sync_timeout_seconds" in d

    def test_save_and_load(self, tmp_path):
        cfg = Config(relays=["wss://custom.relay"], sync_timeout_seconds=60)
        config_path = tmp_path / "config.json"
        cfg.save(config_path)

        assert config_path.exists()

        reset_cache()
        loaded = load_config(config_path)
        assert loaded.relays == ["wss://custom.relay"]
        assert loaded.sync_timeout_seconds == 60

    def test_load_missing_file(self, tmp_path):
        reset_cache()
        cfg = load_config(tmp_path / "nonexistent.json")
        assert cfg.relays == list(DEFAULT_RELAYS)

    def test_load_corrupt_json(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text("not valid json{{{")

        reset_cache()
        cfg = load_config(config_path)
        assert cfg.relays == list(DEFAULT_RELAYS)

    def test_load_partial_config(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"relays": ["wss://only.one"]}))

        reset_cache()
        cfg = load_config(config_path)
        assert cfg.relays == ["wss://only.one"]
        assert cfg.sync_timeout_seconds == 30  # default

    def test_caching(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"relays": ["wss://first"]}))

        reset_cache()
        cfg1 = load_config(config_path)
        cfg2 = load_config(config_path)
        assert cfg1 is cfg2  # same object from cache
