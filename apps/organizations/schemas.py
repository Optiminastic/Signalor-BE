"""
Pydantic schemas for the LLM-synthesized sections of a BrandProfile (Epic 2).

Only the interpretive sections (identity/positioning/audience/voice) are LLM-synthesized
and validated here. The factual sections (canonical_facts/competitors/sources) are mapped
deterministically in ``services/brand_profile.py`` and never pass through the LLM.

Every field defaults empty so a partial model still validates -- we don't want to spend the
single ``ask_structured`` auto-repair on a minor omission. Pure module: no Django imports.
"""

from pydantic import BaseModel, Field


class BrandIdentity(BaseModel):
    name: str = ""
    legal_name: str = ""
    tagline: str = ""
    short_description: str = ""
    long_description: str = ""
    industry: str = ""
    hq_location: str = ""


class BrandPositioning(BaseModel):
    value_proposition: str = ""
    category: str = ""
    differentiators: list[str] = Field(default_factory=list)
    model_type: str = ""
    price_positioning: str = ""
    one_liner: str = ""


class BrandAudience(BaseModel):
    primary_segment: str = ""
    secondary_segments: list[str] = Field(default_factory=list)
    target_markets: list[str] = Field(default_factory=list)
    customer_segment: str = ""
    use_cases: list[str] = Field(default_factory=list)


class BrandVoice(BaseModel):
    tone: list[str] = Field(default_factory=list)
    style_notes: str = ""
    do: list[str] = Field(default_factory=list)
    dont: list[str] = Field(default_factory=list)
    example_phrases: list[str] = Field(default_factory=list)


class BrandSynthesis(BaseModel):
    """One ask_structured round-trip returns all four interpretive sections."""

    identity: BrandIdentity = Field(default_factory=BrandIdentity)
    positioning: BrandPositioning = Field(default_factory=BrandPositioning)
    audience: BrandAudience = Field(default_factory=BrandAudience)
    voice: BrandVoice = Field(default_factory=BrandVoice)
