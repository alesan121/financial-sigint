"""Unit tests for orchestrator/nodes/fsm_risk_quant.py's routing decision tree."""

from types import SimpleNamespace

import pytest

from orchestrator.nodes import fsm_risk_quant, risk_engine
from orchestrator.nodes.fsm_risk_quant import node_risk_quant_agent


@pytest.fixture(autouse=True)
def fixed_settings(monkeypatch):
    """Pin the DSP settings this node (and the risk_engine it calls into) read,
    independent of any local .env."""
    monkeypatch.setattr(
        fsm_risk_quant,
        "settings",
        SimpleNamespace(virtual_balance_usd=100_000.0, min_allocation_usd=1_000.0),
    )
    monkeypatch.setattr(
        risk_engine,
        "settings",
        SimpleNamespace(kelly_divisor=4.0, max_kelly_fraction=0.10),
    )


def _base_state(**overrides) -> dict:
    state = {
        "ingress_signal": {"ticker": "AAPL", "sentiment": "bullish", "stoch_confidence": 0.9},
        "current_price": 100.0,
        "vix_level": 15.0,
        "market_regime": "RISK_ON",
        "support": 95.0,
        "resistance": 110.0,
        "atr_14": 2.0,
        "sma_20": 98.0,
        "risk_quant": {},
    }
    state.update(overrides)
    return state


class TestRoutingDecisionTree:
    @pytest.mark.asyncio
    async def test_high_confidence_signal_is_approved(self):
        state = _base_state()
        result = await node_risk_quant_agent(state)
        assert result["risk_quant"]["routing_flag"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_ambiguous_confidence_band_is_flagged_ambiguous(self):
        state = _base_state(
            ingress_signal={"ticker": "AAPL", "sentiment": "bullish", "stoch_confidence": 0.65}
        )
        result = await node_risk_quant_agent(state)
        assert result["risk_quant"]["routing_flag"] == "AMBIGUOUS"

    @pytest.mark.asyncio
    async def test_low_confidence_signal_is_rejected_for_low_snr(self):
        # A generous reward/risk ratio keeps the Kelly allocation above the minimum
        # floor, isolating the low-confidence ("Low SNR") rejection path from the
        # separate insufficient-allocation path.
        state = _base_state(
            ingress_signal={"ticker": "AAPL", "sentiment": "bullish", "stoch_confidence": 0.3},
            support=99.0,
            resistance=200.0,
        )
        result = await node_risk_quant_agent(state)
        # allocation_usd is always reported as 0.0 for a REJECTED signal (regardless
        # of reason), so the discard_reason is the only way to confirm which branch
        # of the routing decision actually fired.
        assert result["risk_quant"]["routing_flag"] == "REJECTED"
        assert "Low SNR" in result["risk_quant"]["discard_reason"]

    @pytest.mark.asyncio
    async def test_missing_ticker_is_rejected_as_sensor_fault(self):
        state = _base_state(ingress_signal={"sentiment": "bullish", "stoch_confidence": 0.9})
        result = await node_risk_quant_agent(state)
        assert result["risk_quant"]["routing_flag"] == "REJECTED"
        assert "Sensor Fault" in result["risk_quant"]["discard_reason"]

    @pytest.mark.asyncio
    async def test_zero_price_is_rejected_as_sensor_fault(self):
        state = _base_state(current_price=0.0)
        result = await node_risk_quant_agent(state)
        assert result["risk_quant"]["routing_flag"] == "REJECTED"
        assert "Sensor Fault" in result["risk_quant"]["discard_reason"]

    @pytest.mark.asyncio
    async def test_allocation_floor_takes_precedence_over_high_confidence(self):
        # A lopsided risk/reward (huge downside vs a razor-thin upside) drives the
        # Kelly fraction to a negative value that clamps to 0, so allocation_usd
        # falls under the $1,000 floor even though confidence is high (0.9 > 0.80,
        # which would otherwise route straight to APPROVED). The allocation check
        # runs before the confidence bands, so REJECTED must win here.
        state = _base_state(
            ingress_signal={"ticker": "AAPL", "sentiment": "bullish", "stoch_confidence": 0.9},
            support=1.0,
            resistance=100.5,
        )
        result = await node_risk_quant_agent(state)
        assert result["risk_quant"]["allocation_usd"] == 0.0
        assert result["risk_quant"]["routing_flag"] == "REJECTED"
        assert "Insufficient Allocation" in result["risk_quant"]["discard_reason"]

    @pytest.mark.asyncio
    async def test_manual_override_bypasses_dsp_logic(self):
        state = _base_state(risk_quant={"discard_reason": "[Manual_Override] Action: APPROVED"})
        result = await node_risk_quant_agent(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_response_includes_a_log_entry(self):
        state = _base_state()
        result = await node_risk_quant_agent(state)
        assert len(result["logs"]) == 1
        assert "FSM_Quant" in result["logs"][0]
