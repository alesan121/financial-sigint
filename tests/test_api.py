"""
tests/test_api.py - Tests for the Ingress microservice (ADC).

Philosophy: these tests do NOT require Ollama to be running.
We mock the agent to verify the API CONTRACT (the endpoints)
in an isolated, reproducible way in any environment (CI/CD).

Hardware analogy: the tests are the 'production test bench' (ATE).
They verify that the connectors (endpoints) meet their electrical
specification without needing the fully assembled system.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ingress.main import create_app
from ingress.schemas.signals import InvestmentSignal


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client() -> TestClient:
    """
    Fixture that provides an isolated test client for each test.
    Using create_app() instead of the global instance guarantees
    that each test starts from a clean state (idempotency).
    """
    app = create_app()
    return TestClient(app)


@pytest.fixture
def mock_signal() -> InvestmentSignal:
    """Fixture with a valid InvestmentSignal for use in mocks."""
    return InvestmentSignal(
        ticker="NVDA",
        market_cap_tier="mega",
        impact_score=0.85,
        sentiment="bullish",
        stoch_confidence=0.88,
        reasoning="AI demand surge drives Nvidia revenue beat; P/E expansion likely.",
        entry_price=875.0,
        take_profit=950.0,
        stop_loss=840.0,
        kelly_fraction=0.12,
        horizon_days=14,
        kelly_calculation_path="p=0.88, b=(950-875)/(875-840)=2.143, f*=0.415, capped=0.12",
        latency_ms=18340.5,
    )


@pytest.fixture
def mock_bearish_signal() -> InvestmentSignal:
    """Fixture with a bearish signal for negative-value tests."""
    return InvestmentSignal(
        ticker=None,
        market_cap_tier="unknown",
        impact_score=-0.75,
        sentiment="bearish",
        stoch_confidence=0.82,
        reasoning="Fed rate hike increases borrowing costs, compressing tech valuations.",
        entry_price=100.0,
        take_profit=92.0,
        stop_loss=106.0,
        kelly_fraction=0.0,  # Negative → capped to 0
        horizon_days=7,
        kelly_calculation_path="p=0.82, b=(92-100)/(100-106)=undefined (invalid levels), capped=0.0",
        latency_ms=342.15,
    )


# =============================================================================
# Tests: Health Endpoint
# =============================================================================


class TestHealthEndpoint:
    """Tests for the liveness check endpoint."""

    def test_health_returns_200(self, client: TestClient) -> None:
        """The health check must always respond with 200 OK."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_correct_schema(self, client: TestClient) -> None:
        """The health check must return the correct schema."""
        response = client.get("/health")
        body = response.json()
        assert body["status"] == "ok"
        assert "service" in body
        assert "version" in body


# =============================================================================
# Tests: Analyze Endpoint
# =============================================================================


class TestAnalyzeEndpoint:
    """Tests for the main POST /analyze endpoint."""

    def test_valid_input_returns_200(
        self, client: TestClient, mock_signal: InvestmentSignal
    ) -> None:
        """With valid input and a mocked agent, it must return 200 and the InvestmentSignal schema."""
        with patch(
            "ingress.api.router.NasdaqImpactAgent.analyze",
            return_value=mock_signal,
        ):
            response = client.post(
                "/analyze",
                json={
                    "text": "Nvidia beats earnings estimates massively driven by AI chip demand.",
                    "source": "Reuters",
                },
            )

        assert response.status_code == 200
        body = response.json()
        # Verify InvestmentSignal schema fields
        assert "impact_score" in body
        assert "sentiment" in body
        assert "stoch_confidence" in body
        assert "reasoning" in body
        assert "latency_ms" in body
        # Quant fields
        assert "entry_price" in body
        assert "take_profit" in body
        assert "stop_loss" in body
        assert "kelly_fraction" in body
        assert "horizon_days" in body
        assert "kelly_calculation_path" in body

    def test_valid_input_matches_mock_values(
        self, client: TestClient, mock_signal: InvestmentSignal
    ) -> None:
        """The returned values must match the mock's (bus contract)."""
        with patch(
            "ingress.api.router.NasdaqImpactAgent.analyze",
            return_value=mock_signal,
        ):
            response = client.post(
                "/analyze",
                json={"text": "The Federal Reserve announced a surprise rate hike today."},
            )

        body = response.json()
        assert body["ticker"] == "NVDA"
        assert body["sentiment"] == "bullish"
        assert body["impact_score"] == 0.85
        assert body["stoch_confidence"] == 0.88
        assert body["kelly_fraction"] == 0.12
        assert body["horizon_days"] == 14

    def test_kelly_fraction_rejects_above_25_percent(self) -> None:
        """
        Pydantic V2 with Field(le=0.25) REJECTS values > 0.25 with ValidationError.
        The capping is done by _compute_kelly() in the agent BEFORE building the object.
        The schema is the final guard: it doesn't silence errors, it rejects them explicitly.
        """
        from pydantic import ValidationError as PydanticValidationError
        from ingress.schemas.signals import InvestmentSignal as IS

        with pytest.raises(PydanticValidationError, match="less_than_equal"):
            IS(
                ticker="AAPL", market_cap_tier="mega", impact_score=0.9,
                sentiment="bullish", stoch_confidence=0.95, reasoning="Strong signal.",
                entry_price=150.0, take_profit=200.0, stop_loss=140.0,
                kelly_fraction=0.99,  # > 0.25 → ValidationError (expected)
                horizon_days=30, kelly_calculation_path="capped test", latency_ms=100.0,
            )

    def test_kelly_fraction_accepts_valid_values(self) -> None:
        """A valid kelly_fraction (< 0.25) must be built without errors."""
        from ingress.schemas.signals import InvestmentSignal as IS

        signal = IS(
            ticker="AAPL", market_cap_tier="mega", impact_score=0.9,
            sentiment="bullish", stoch_confidence=0.95, reasoning="Strong signal.",
            entry_price=150.0, take_profit=200.0, stop_loss=140.0,
            kelly_fraction=0.20,  # 20% valid
            horizon_days=30, kelly_calculation_path="test", latency_ms=100.0,
        )
        assert signal.kelly_fraction == 0.20

    def test_text_too_short_returns_422(self, client: TestClient) -> None:
        """A text that is too short must generate a 422 validation error."""
        response = client.post("/analyze", json={"text": "short"})
        assert response.status_code == 422

    def test_missing_text_field_returns_422(self, client: TestClient) -> None:
        """A body missing the (required) 'text' field must return 422."""
        response = client.post("/analyze", json={"source": "Reuters"})
        assert response.status_code == 422

    def test_empty_body_returns_422(self, client: TestClient) -> None:
        """An empty body must return 422."""
        response = client.post("/analyze", json={})
        assert response.status_code == 422

    def test_ollama_unavailable_returns_503(self, client: TestClient) -> None:
        """If Ollama is unavailable (RuntimeError), it must return 503."""
        with patch(
            "ingress.api.router.NasdaqImpactAgent.analyze",
            side_effect=RuntimeError("Connection refused"),
        ):
            response = client.post(
                "/analyze",
                json={"text": "Markets plunge as inflation data exceeds expectations."},
            )

        assert response.status_code == 503

    def test_llm_invalid_schema_returns_500(self, client: TestClient) -> None:
        """If the LLM returns an invalid schema (ValueError), it must return 500."""
        with patch(
            "ingress.api.router.NasdaqImpactAgent.analyze",
            side_effect=ValueError("LLM output schema validation failed"),
        ):
            response = client.post(
                "/analyze",
                json={"text": "Tech stocks rally on positive earnings reports from Apple."},
            )

        assert response.status_code == 500

    def test_optional_source_field_is_optional(
        self, client: TestClient, mock_signal: InvestmentSignal
    ) -> None:
        """The 'source' field is optional: it must work without it."""
        with patch(
            "ingress.api.router.NasdaqImpactAgent.analyze",
            return_value=mock_signal,
        ):
            response = client.post(
                "/analyze",
                json={"text": "Bond yields spike to multi-decade highs amid inflation concerns."},
            )

        assert response.status_code == 200


# =============================================================================
# Tests: Kelly Criterion Unit Tests
# =============================================================================


class TestKellyCriterion:
    """Unit tests for the Kelly Criterion logic."""

    def test_compute_kelly_basic(self) -> None:
        """Verify the Kelly formula with known values."""
        from ingress.agents.impact_agent import _compute_kelly

        # p=0.6, entry=100, tp=130, sl=90 → b=(130-100)/(100-90)=3.0
        # f* = (0.6*3 - 0.4)/3 = (1.8-0.4)/3 = 0.4667
        # capped = min(0.4667, 0.25) = 0.25
        kelly, path = _compute_kelly(p=0.6, entry=100.0, tp=130.0, sl=90.0)
        assert kelly == 0.25  # capped
        assert "p=0.600" in path
        assert "b=" in path

    def test_compute_kelly_negative_expected_value(self) -> None:
        """With p<0.5 and b<1, Kelly can be negative → it must clamp to 0.0."""
        from ingress.agents.impact_agent import _compute_kelly

        # p=0.3, entry=100, tp=105, sl=90 → b=0.5
        # f* = (0.3*0.5 - 0.7)/0.5 = (0.15-0.7)/0.5 = -1.1 → capped to 0.0
        kelly, path = _compute_kelly(p=0.3, entry=100.0, tp=105.0, sl=90.0)
        assert kelly == 0.0

    def test_compute_kelly_invalid_levels(self) -> None:
        """If TP < entry or SL > entry, Kelly must return 0.0."""
        from ingress.agents.impact_agent import _compute_kelly

        kelly, path = _compute_kelly(p=0.8, entry=100.0, tp=90.0, sl=110.0)
        assert kelly == 0.0
        assert "undefined" in path
