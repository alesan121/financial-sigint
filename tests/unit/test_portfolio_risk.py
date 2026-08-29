"""Unit tests for the pure portfolio-level guard functions in orchestrator/nodes/portfolio_risk.py.

node_portfolio_risk_check itself (the async graph node) talks to the live Alpaca and
Yahoo Finance APIs and is out of scope for unit tests; these tests cover the guard
predicates it composes, which hold all of the actual risk logic.
"""

from types import SimpleNamespace

from orchestrator.nodes import portfolio_risk
from orchestrator.nodes.portfolio_risk import (
    _check_drawdown_killswitch,
    _check_max_positions,
    _check_meta_labeling,
    _check_sector_exposure,
    _get_sector,
    _trip_breaker,
    _trip_hitl,
)


def _position(symbol: str, market_value: float) -> SimpleNamespace:
    return SimpleNamespace(symbol=symbol, market_value=market_value)


class TestGetSector:
    def test_known_ticker_maps_to_its_sector(self):
        assert _get_sector("AAPL") == "TECH"
        assert _get_sector("XOM") == "ENERGY"

    def test_lookup_is_case_insensitive(self):
        assert _get_sector("aapl") == "TECH"

    def test_macro_prefix_is_stripped_before_lookup(self):
        assert _get_sector("MACRO:XLE") == "ENERGY"

    def test_unmapped_ticker_is_unknown(self):
        assert _get_sector("SOME_UNLISTED_TICKER") == "UNKNOWN"


class TestCheckMaxPositions:
    def test_below_the_cap_passes(self):
        ok, msg = _check_max_positions([_position("AAPL", 1000)] * 4)
        assert ok is True
        assert "4/5" in msg

    def test_at_the_cap_is_rejected(self):
        ok, msg = _check_max_positions([_position("AAPL", 1000)] * 5)
        assert ok is False
        assert "MAX_POSITIONS" in msg

    def test_empty_portfolio_passes(self):
        ok, _ = _check_max_positions([])
        assert ok is True


class TestCheckSectorExposure:
    def test_zero_equity_skips_the_check(self):
        ok, msg = _check_sector_exposure("AAPL", 1000.0, [], equity=0.0)
        assert ok is True
        assert "SKIP" in msg

    def test_unmapped_sector_is_always_allowed(self):
        ok, msg = _check_sector_exposure("SOME_UNLISTED_TICKER", 50_000.0, [], equity=10_000.0)
        assert ok is True
        assert "Unknown sector" in msg

    def test_new_allocation_within_the_cap_passes(self):
        # 1000 / 10000 = 10% < the 30% cap.
        ok, _ = _check_sector_exposure("AAPL", 1000.0, [], equity=10_000.0)
        assert ok is True

    def test_existing_same_sector_exposure_counts_toward_the_cap(self):
        positions = [_position("MSFT", 2500.0)]
        # (2500 existing + 1000 new) / 10000 = 35% > the 30% cap.
        ok, msg = _check_sector_exposure("AAPL", 1000.0, positions, equity=10_000.0)
        assert ok is False
        assert "SECTOR_CAP" in msg

    def test_other_sector_positions_do_not_count(self):
        positions = [_position("XOM", 9_000.0)]  # ENERGY, not TECH
        ok, _ = _check_sector_exposure("AAPL", 1000.0, positions, equity=10_000.0)
        assert ok is True


class TestCheckDrawdownKillswitch:
    def test_no_peak_data_skips_the_check(self):
        ok, msg = _check_drawdown_killswitch(equity=10_000.0, peak=0.0)
        assert ok is True
        assert "SKIP" in msg

    def test_drawdown_below_threshold_passes(self):
        ok, _ = _check_drawdown_killswitch(equity=95_000.0, peak=100_000.0)  # 5% dd
        assert ok is True

    def test_drawdown_at_threshold_trips_the_killswitch(self):
        ok, msg = _check_drawdown_killswitch(equity=90_000.0, peak=100_000.0)  # 10% dd
        assert ok is False
        assert "KILL-SWITCH" in msg

    def test_drawdown_beyond_threshold_trips_the_killswitch(self):
        ok, _ = _check_drawdown_killswitch(equity=80_000.0, peak=100_000.0)  # 20% dd
        assert ok is False


class TestCheckMetaLabeling:
    def test_no_model_loaded_skips_the_check(self, monkeypatch):
        monkeypatch.setattr(portfolio_risk, "_meta_model", None)
        ok, msg = _check_meta_labeling({})
        assert ok is True
        assert "SKIP: No ML model" in msg

    def test_model_veto_rejects_the_signal(self, monkeypatch):
        monkeypatch.setattr(portfolio_risk, "_meta_model", SimpleNamespace(predict=lambda df: [0]))
        ok, msg = _check_meta_labeling({"ingress_signal": {}, "risk_quant": {}})
        assert ok is False
        assert "ML_VETO" in msg

    def test_model_approval_passes(self, monkeypatch):
        monkeypatch.setattr(portfolio_risk, "_meta_model", SimpleNamespace(predict=lambda df: [1]))
        ok, msg = _check_meta_labeling({"ingress_signal": {}, "risk_quant": {}})
        assert ok is True
        assert "OK: ML Approved" in msg

    def test_model_error_fails_open(self, monkeypatch):
        def _boom(df):
            raise RuntimeError("model corrupted")

        monkeypatch.setattr(portfolio_risk, "_meta_model", SimpleNamespace(predict=_boom))
        ok, msg = _check_meta_labeling({"ingress_signal": {}, "risk_quant": {}})
        assert ok is True
        assert "SKIP: ML error" in msg


class TestBreakerHelpers:
    def test_trip_breaker_rejects_and_preserves_prior_risk_fields(self):
        result = _trip_breaker({"pop_score": 0.8}, "MAX_POSITIONS reached", ["audit line"])
        assert result["risk_quant"]["routing_flag"] == "REJECTED"
        assert result["risk_quant"]["pop_score"] == 0.8
        assert result["portfolio_risk_status"] == "BLOCKED"
        assert result["portfolio_risk_audit"] == ["audit line"]

    def test_trip_hitl_escalates_instead_of_rejecting(self):
        result = _trip_hitl({"pop_score": 0.8}, "ML_VETO", ["audit line"])
        assert result["risk_quant"]["routing_flag"] == "AMBIGUOUS"
        assert result["portfolio_risk_status"] == "HITL_REQUIRED"
