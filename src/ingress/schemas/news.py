"""
schemas/news.py - Data contracts (Pydantic V2).

Hardware analogy: these models are the system's 'normalized connectors'.
They define the voltage and protocol of every signal entering and leaving the ADC.
If the signal doesn't meet the standard, Pydantic raises an exception at runtime
before the data contaminates the internal bus.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class NewsInput(BaseModel):
    """
    Input schema: the raw-text financial news item.
    Represents the analog signal before being processed by the ADC.
    """

    text: str = Field(
        ...,
        min_length=10,
        max_length=4096,
        description="Text of the financial news item to analyze.",
        examples=["Fed raises interest rates by 50 basis points, market reacts negatively."],
    )
    source: str | None = Field(
        default=None,
        max_length=100,
        description="Source of the news item (e.g.: Reuters, Bloomberg). Optional.",
        examples=["Reuters"],
    )

    @field_validator("text")
    @classmethod
    def text_must_not_be_empty_or_whitespace(cls, v: str) -> str:
        """Rejects strings that are only whitespace."""
        if not v.strip():
            raise ValueError("The 'text' field cannot contain only whitespace.")
        return v.strip()


class NasdaqImpactOutput(BaseModel):
    """
    Output schema: the Nasdaq impact analysis.
    Represents the digital signal already converted and validated by the ADC.
    Each field is a 'bit' of the analysis that the downstream pipeline will consume.
    """

    impact_score: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description=(
            "Nasdaq impact score. " "Range: [-1.0 (very bearish) to +1.0 (very bullish)]."
        ),
    )
    sentiment: Literal["bullish", "bearish", "neutral"] = Field(
        ...,
        description="Qualitative sentiment derived from the analysis.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model's confidence level in its prediction. Range: [0.0 to 1.0].",
    )
    reasoning: str = Field(
        ...,
        min_length=10,
        description="Explanation of the LLM's reasoning for its prediction.",
    )
    latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Inference latency in milliseconds (AgentOps: observability).",
    )
