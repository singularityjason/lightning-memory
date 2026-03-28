"""Tests for configuration module."""

import json
import os

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


def test_config_has_attestation_fields():
    """Config should have auto_attest_threshold and broad_attestation_pull."""
    from lightning_memory.config import Config
    c = Config()
    assert c.auto_attest_threshold == 5
    assert c.broad_attestation_pull is False


def test_gateway_discovery_config_defaults():
    """Config should have gateway_discovery and gateway_url defaults."""
    from lightning_memory.config import Config
    c = Config()
    assert c.gateway_discovery is False
    assert c.gateway_url == ""
    d = c.to_dict()
    assert "gateway_discovery" in d
    assert "gateway_url" in d


class TestEnvVarOverrides:
    """Environment variables should override config file values."""

    def setup_method(self):
        reset_cache()

    def teardown_method(self):
        reset_cache()
        # Clean up any env vars we set
        for key in (
            "LIGHTNING_MEMORY_PHOENIXD_URL",
            "LIGHTNING_MEMORY_PHOENIXD_PASSWORD",
            "LIGHTNING_MEMORY_GATEWAY_PORT",
            "LIGHTNING_MEMORY_RELAYS",
            "LIGHTNING_MEMORY_GATEWAY_URL",
            "PHOENIXD_URL",
            "PHOENIXD_PASSWORD",
        ):
            os.environ.pop(key, None)

    def test_phoenixd_url_from_env(self, tmp_path):
        os.environ["LIGHTNING_MEMORY_PHOENIXD_URL"] = "http://custom:9740"
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.phoenixd_url == "http://custom:9740"

    def test_phoenixd_password_from_env(self, tmp_path):
        os.environ["LIGHTNING_MEMORY_PHOENIXD_PASSWORD"] = "secret123"
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.phoenixd_password == "secret123"

    def test_legacy_env_vars(self, tmp_path):
        """docker-compose uses PHOENIXD_URL / PHOENIXD_PASSWORD without prefix."""
        os.environ["PHOENIXD_URL"] = "http://phoenixd:9740"
        os.environ["PHOENIXD_PASSWORD"] = "changeme"
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.phoenixd_url == "http://phoenixd:9740"
        assert cfg.phoenixd_password == "changeme"

    def test_namespaced_env_beats_legacy(self, tmp_path):
        """LIGHTNING_MEMORY_* should take priority over bare names."""
        os.environ["PHOENIXD_URL"] = "http://legacy:9740"
        os.environ["LIGHTNING_MEMORY_PHOENIXD_URL"] = "http://namespaced:9740"
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.phoenixd_url == "http://namespaced:9740"

    def test_env_overrides_config_file(self, tmp_path):
        """Env vars should win over values in config.json."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "phoenixd_url": "http://from-file:9740",
            "phoenixd_password": "file-password",
        }))
        os.environ["LIGHTNING_MEMORY_PHOENIXD_URL"] = "http://from-env:9740"
        cfg = load_config(config_path)
        assert cfg.phoenixd_url == "http://from-env:9740"
        assert cfg.phoenixd_password == "file-password"  # not overridden

    def test_gateway_port_from_env(self, tmp_path):
        os.environ["LIGHTNING_MEMORY_GATEWAY_PORT"] = "9999"
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.gateway_port == 9999

    def test_invalid_port_ignored(self, tmp_path):
        os.environ["LIGHTNING_MEMORY_GATEWAY_PORT"] = "not-a-number"
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.gateway_port == 8402  # default

    def test_relays_from_env(self, tmp_path):
        os.environ["LIGHTNING_MEMORY_RELAYS"] = "wss://r1.example,wss://r2.example"
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.relays == ["wss://r1.example", "wss://r2.example"]

    def test_gateway_url_from_env(self, tmp_path):
        os.environ["LIGHTNING_MEMORY_GATEWAY_URL"] = "https://gw.example.com"
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.gateway_url == "https://gw.example.com"
