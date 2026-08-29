"""Unit tests for orchestrator/nodes/risk_engine.py's pure DSP math functions."""

from types import SimpleNamespace

import pytest

from orchestrator.nodes import risk_engine
from orchestrator.nodes.risk_engine import (
    _calculate_pop,
    _compute_real_kelly,
    calculate_dynamic_safety,
)


@pytest.fixture(autouse=True)
def fixed_kelly_settings(monkeypatch):
    """
    _compute_real_kelly reads the module-level `settings` singleton, which is
    populated from the developer's local .env at import time. Pin it to known
    values so these tests are deterministic regardless of ambient environment
    configuration (CI has no .env; a local dev .env may override the defaults).
    """
    monkeypatch.setattr(
        risk_engine,
        "settings",
        SimpleNamespace(kelly_divisor=4.0, max_kelly_fraction=0.10),
    )


class TestCalculateDynamicSafety:
    def test_zero_or_negative_price_returns_zeros(self):
        assert calculate_dynamic_safety(0.0, 2.0, "bullish") == (0.0, 0.0, 0.0)
        assert calculate_dynamic_safety(-10.0, 2.0, "bullish") == (0.0, 0.0, 0.0)

    def test_bullish_uses_atr_based_stop_and_target(self):
        sl, tp, rr = calculate_dynamic_safety(price=100.0, atr=2.0, sentiment="bullish")
        assert (sl, tp, rr) == (96.0, 110.0, 2.5)

    def test_bearish_mirrors_the_stop_and_target(self):
        sl, tp, rr = calculate_dynamic_safety(price=100.0, atr=2.0, sentiment="bearish")
        assert (sl, tp, rr) == (104.0, 90.0, 2.5)

    def test_zero_atr_falls_back_to_2_percent_of_price(self):
        # atr=0 -> safe_atr = price * 0.02 = 2.0, same as an explicit atr=2.0
        sl, tp, rr = calculate_dynamic_safety(price=100.0, atr=0.0, sentiment="bullish")
        assert (sl, tp, rr) == (96.0, 110.0, 2.5)

    def test_stop_loss_never_closer_than_10_cents_bullish(self):
        # A tiny ATR would put the raw stop 2 cents away; the 10-cent floor must win.
        sl, tp, rr = calculate_dynamic_safety(price=100.0, atr=0.01, sentiment="bullish")
        assert sl == 99.9
        assert tp == 100.05
        assert rr == 0.5

    def test_stop_loss_never_closer_than_10_cents_bearish(self):
        sl, tp, rr = calculate_dynamic_safety(price=100.0, atr=0.01, sentiment="bearish")
        assert sl == 100.1
        assert tp == 99.95
        assert rr == 0.5


class TestCalculatePop:
    def test_neutral_impact_weights_confidence_at_70_percent(self):
        pop, reason = _calculate_pop(p=0.8, impact_score=0.0)
        # adj_impact = (0 + 1) / 2 = 0.5 -> pop = 0.8*0.7 + 0.5*0.3 = 0.71
        assert pop == 0.71
        assert reason == "PoP Fusion Successful"

    def test_max_bullish_impact(self):
        pop, _ = _calculate_pop(p=0.9, impact_score=1.0)
        assert pop == 0.93

    def test_max_bearish_impact_drags_pop_down(self):
        pop, _ = _calculate_pop(p=0.5, impact_score=-1.0)
        assert pop == 0.35


class TestComputeRealKelly:
    def test_zero_or_negative_price_returns_zero_fraction(self):
        f, b, reason = _compute_real_kelly(
            p=0.8,
            current_price=0.0,
            support=95.0,
            resistance=110.0,
            sentiment="bullish",
            market_regime="RISK_ON",
        )
        assert (f, b, reason) == (0.0, 0.0, "Zero Price")

    def test_bullish_fraction_is_clamped_to_max_kelly_fraction(self):
        # Raw f* = 0.7, attenuated by kelly_divisor=4.0 -> 0.175, clamped to max 0.10.
        f, b, reason = _compute_real_kelly(
            p=0.8,
            current_price=100.0,
            support=95.0,
            resistance=110.0,
            sentiment="bullish",
            market_regime="RISK_ON",
        )
        assert f == 0.10
        assert b == 2.0
        assert reason == "Kelly Calc OK"

    def test_bearish_fraction_uses_mirrored_risk_reward(self):
        f, b, reason = _compute_real_kelly(
            p=0.7,
            current_price=100.0,
            support=90.0,
            resistance=105.0,
            sentiment="bearish",
            market_regime="RISK_ON",
        )
        assert f == 0.10  # also clamps at the max
        assert b == 2.0

    def test_unclamped_fraction_reflects_the_raw_kelly_math(self):
        # b=1.0, p=0.55 -> f* = (1*0.55 - 0.45)/1 = 0.10 -> attenuated = 0.025 (below the cap).
        f, b, reason = _compute_real_kelly(
            p=0.55,
            current_price=100.0,
            support=98.0,
            resistance=102.0,
            sentiment="bullish",
            market_regime="RISK_ON",
        )
        assert f == 0.025
        assert b == 1.0

    def test_negative_edge_clamps_to_zero_not_negative(self):
        # Low confidence against a poor risk/reward setup yields a negative f* -> clamp to 0.0.
        f, b, reason = _compute_real_kelly(
            p=0.2,
            current_price=100.0,
            support=90.0,
            resistance=95.0,
            sentiment="bullish",
            market_regime="RISK_ON",
        )
        assert f == 0.0
        assert b == 0.4

    def test_risk_off_volatile_regime_halves_the_allocation(self):
        f_normal, _, _ = _compute_real_kelly(
            p=0.55,
            current_price=100.0,
            support=98.0,
            resistance=102.0,
            sentiment="bullish",
            market_regime="RISK_ON",
        )
        f_volatile, _, _ = _compute_real_kelly(
            p=0.55,
            current_price=100.0,
            support=98.0,
            resistance=102.0,
            sentiment="bullish",
            market_regime="RISK_OFF_VOLATILE",
        )
        assert f_volatile == round(f_normal * 0.5, 4)
