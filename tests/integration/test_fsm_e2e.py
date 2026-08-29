import pytest
from unittest.mock import AsyncMock, patch
from orchestrator.graph import run_trading_cycle
from orchestrator.state import TradingState

@pytest.mark.asyncio
async def test_fsm_e2e_approved_flow():
    """
    Happy path test: Ingress -> RiskQuant (APPROVED) -> Execution.
    """
    # Mocks to avoid real calls to Ollama/YFinance
    with patch("orchestrator.nodes.fsm_risk_quant.node_technical_analysis") as mock_ta, \
         patch("orchestrator.nodes.fsm_risk_quant.llm_dsp") as mock_llm_dsp, \
         patch("orchestrator.nodes.fsm_execution.node_execution_agent") as mock_exec:

        # 1. Technical Data Mock
        mock_ta.return_value = {
            "price": 100.0, "sma_20": 95.0, "atr_14": 2.0,
            "support": 90.0, "resistance": 110.0, "vix": 15.0, "regime": "RISK_ON"
        }

        # 2. DSP Mock (Mistral)
        mock_llm_dsp.ainvoke = AsyncMock()
        mock_llm_dsp.ainvoke.return_value.content = '{"is_safe": true, "reason": "Looks good"}'

        # 3. Execution Mock
        mock_exec.return_value = {"execution_payload": {"status": "EXECUTED_DRY_RUN"}, "logs": ["Test OK"]}

        # Simulated Ingress Signal (via run_trading_cycle, mocked internally)
        # In reality run_trading_cycle calls the FSM.
        # But to test the real LangGraph flow, it's better to use the compiled FSM.
        from orchestrator.graph import fsm_pipeline
        
        initial_state = {
            "ingress_signal": {
                "ticker": "AAPL",
                "sentiment": "bullish",
                "impact_score": 0.8,
                "stoch_confidence": 0.9
            },
            "retry_count": 0,
            "logs": []
        }
        
        final_state = await fsm_pipeline.ainvoke(initial_state)
        
        assert final_state["risk_quant"]["routing_flag"] == "APPROVED"
        assert final_state["execution_payload"]["status"] == "EXECUTED_DRY_RUN"
        assert any("FSM Initialized" in log for log in final_state["logs"])

@pytest.mark.asyncio
async def test_fsm_e2e_adjuster_flow():
    """
    Feedback loop test: RiskQuant (BORDERLINE) -> Adjuster -> Execution.
    """
    with patch("orchestrator.nodes.fsm_risk_quant.node_technical_analysis") as mock_ta, \
         patch("orchestrator.nodes.fsm_risk_quant.llm_dsp") as mock_llm_dsp:

        # Force BORDERLINE: Low Kelly ($2000 < $2500)
        mock_ta.return_value = {
            "price": 100.0, "sma_20": 95.0, "atr_14": 2.0,
            "support": 98.0, "resistance": 102.0, "vix": 15.0, "regime": "RISK_ON"
        }

        mock_llm_dsp.ainvoke = AsyncMock()
        mock_llm_dsp.ainvoke.return_value.content = '{"is_safe": true, "reason": "Wait"}'

        from orchestrator.graph import fsm_pipeline

        initial_state = {
            "ingress_signal": {
                "ticker": "TSLA",
                "sentiment": "bullish",
                "impact_score": 0.2,
                "stoch_confidence": 0.5
            },
            "retry_count": 0,
            "logs": []
        }

        final_state = await fsm_pipeline.ainvoke(initial_state)

        # The flow should be: Risk (BORDERLINE) -> Adjuster (APPROVED + Trim) -> Execution
        assert final_state["risk_quant"]["routing_flag"] == "APPROVED"
        assert any("FSM Adjusted" in log for log in final_state["logs"])
        assert final_state["retry_count"] == 1
