"""
schemas/signals.py - Data contract for the quantitative investment signal.

Hardware analogy: 'InvestmentSignal' is the data bus's FRAME FORMAT.
Each field is a well-defined information channel, with its voltage range
(Pydantic validation) and its role in the control system.

The system now produces signals of the form:
    "Enter [ticker] at [entry_price], target [take_profit] in [horizon_days] days.
     Risk [kelly_fraction*100]% of capital. Stochastic confidence: [stoch_confidence]."

WARNING: The price fields (entry_price, take_profit, stop_loss) are
LLM ESTIMATES based on its knowledge of the market. They do not constitute
financial advice and are not connected to real-time market data.
In production, these values must be validated against a real price feed
(e.g.: yfinance, Alpaca MarketData API).
"""

from typing import Literal, Any

from pydantic import BaseModel, Field, field_validator


# Type aliases for readability
MarketCapTier = Literal["mega", "large", "mid", "small", "unknown"]
SentimentLabel = Literal["bullish", "bearish", "neutral"]


class InvestmentSignal(BaseModel):
    """
    Quantitative investment signal produced by the ADC + CoT agent.

    Superset of NasdaqImpactOutput: includes all the sentiment analysis
    fields plus the capital management fields (Kelly Criterion).
    """

    # --- Base market analysis ---
    ticker: str | None = Field(
        default=None,
        max_length=10,
        description="Identified stock ticker (e.g.: AAPL, NVDA). None if macroeconomic.",
    )
    market_cap_tier: MarketCapTier = Field(
        default="unknown",
        description=(
            "Market capitalization tier. Analogous to the system's 'thermal inertia': "
            "mega caps require more power to move. "
            "mega: >200B | large: 10-200B | mid: 2-10B | small: <2B"
        ),
    )
    impact_score: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Nasdaq impact score. Range: [-1.0 (very bearish), +1.0 (very bullish)].",
    )
    sentiment: SentimentLabel = Field(
        ...,
        description="Qualitative sentiment from the analysis.",
    )
    stoch_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Probability of the signal's success [0.0, 1.0]. "
            "Equivalent to SNR (Signal-to-Noise Ratio): "
            "the higher it is, the more reliable the signal is over market noise."
        ),
    )
    reasoning: str = Field(
        ...,
        min_length=10,
        description="Synthesis of the LLM's reasoning (extracted from the CoT's <output> block).",
    )

    # --- Capital management (Kelly Criterion) ---
    entry_price: float = Field(
        ...,
        gt=0.0,
        description=(
            "Estimated support/entry level (USD). "
            "Analogous to a transistor's 'threshold voltage': "
            "the price level where buying pressure overtakes selling pressure."
        ),
    )
    take_profit: float = Field(
        ...,
        gt=0.0,
        description=(
            "Estimated price target (resistance) (USD). "
            "Analogous to the 'saturation voltage': the level where power dissipates."
        ),
    )
    stop_loss: float = Field(
        ...,
        gt=0.0,
        description="Estimated stop loss level (USD). Protection against signal reversal.",
    )
    kelly_fraction: float = Field(
        ...,
        ge=0.0,
        le=0.25,
        description=(
            "Recommended fraction of capital per the Kelly Criterion [0.0, 0.25]. "
            "Formula: f* = (p·b - (1-p)) / b, where p=stoch_confidence, "
            "b=(take_profit-entry)/(entry-stop_loss). "
            "Capped at 25% for safety (fractional Kelly)."
        ),
    )
    horizon_days: int = Field(
        ...,
        ge=1,
        le=365,
        description=(
            "Estimated time horizon for the signal's convergence (days). "
            "Analogous to a 'capacitor's charging time': the market's RC constant."
        ),
    )
    kelly_calculation_path: str = Field(
        ...,
        description=(
            "Audit path of the Kelly calculation. "
            "Documents p, b, and the resulting f* for traceability."
        ),
    )

    # --- AgentOps Metadata ---
    latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Total inference latency in milliseconds.",
    )

    @field_validator("take_profit")
    @classmethod
    def take_profit_must_be_positive(cls, v: float) -> float:
        """
        Verifies that the saturation level (TP) is a positive voltage.
        Cross-validation (TP > Entry) is delegated to the logic controller (Agent)
        to keep this component a simple impedance validator.
        """
        if v <= 0:
            raise ValueError("take_profit must be strictly positive.")
        return v

    @field_validator("market_cap_tier", mode="before")
    @classmethod
    def force_valid_tier(cls, v: str) -> str:
        """
        Forces a valid tier if the LLM hallucinates (Noise Cancelling).
        If the LLM returns noise or a value outside the list, we force 'mid'
        to keep the system operating.
        """
        valid_tiers = {"mega", "large", "mid", "small"}
        v_lower = str(v).lower().strip()
        if v_lower not in valid_tiers:
            return "mid"
        return v_lower

    @field_validator("sentiment", mode="before")
    @classmethod
    def force_valid_sentiment(cls, v: str) -> str:
        """
        Forces a neutral sentiment if the LLM returns noise.
        Neutral will trigger a DISCARD in the Risk Engine, which is the
        desired fail-safe (Zero-Trust) behavior.
        """
        valid_sentiments = {"bullish", "bearish", "neutral"}
        v_lower = str(v).lower().strip()
        if v_lower not in valid_sentiments:
            return "neutral"
        return v_lower

    @field_validator(
        "impact_score", "stoch_confidence", "entry_price",
        "take_profit", "stop_loss", "kelly_fraction",
        mode="before"
    )
    @classmethod
    def coalesce_numeric(cls, v: Any) -> float:
        """
        COALESCE FILTER: Ensures numeric fields are never None or garbage.
        """
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        try:
            clean_v = "".join(c for c in str(v) if c.isdigit() or c in ".-")
            if not clean_v or clean_v == ".":
                return 0.0
            return float(clean_v)
        except (ValueError, TypeError):
            return 0.0

    @field_validator("kelly_fraction")
    @classmethod
    def cap_kelly_to_quarter(cls, v: float) -> float:
        """
        Fractional Kelly: never bet more than 25%.

        Full Kelly overestimates the optimal size in the presence of
        uncertainty about the parameters. 25% is the industry standard
        for conservative risk management.
        """
        return min(v, 0.25)

    @field_validator("ticker", mode="before")
    @classmethod
    def clean_and_limit_ticker(cls, v: str | None) -> str | None:
        """
        SIGNAL RECTIFIER:
        If the LLM sends 'BTC, ETH, COIN', extracts only 'BTC'.
        Cleans formatting noise and ensures compatibility with the orchestrator.
        """
        if v is None:
            return None

        # 1. Normalize: turn "BTC/USD" or "BTC, ETH" into a comma-separated list
        # 2. Split: Take the first element
        # 3. Strip: Remove whitespace and convert to Uppercase
        clean = str(v).replace("/", ",").split(",")[0].strip().upper()

        # 4. Sanitize: Keep only alphanumeric characters and the macro separator ':'
        clean = "".join(c for c in clean if c.isalnum() or c == ":")

        # 5. Truncate to 10 characters to comply with the schema's physical constraint
        return clean[:10]
