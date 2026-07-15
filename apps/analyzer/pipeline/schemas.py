"""
Pydantic response schemas for structured LLM output (Epic 1).

Only schemas whose callers are migrated to ``ask_structured`` live here. The
competitor discovery path intentionally does NOT use a schema -- its raw LLM
items feed a rich ``_normalize_*`` pipeline in ``competitors.py``, so it only
swaps its ad-hoc regex+json.loads for the shared ``extract_json`` helper and
keeps the normalizers as the source of truth.
"""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, RootModel


class MetaFix(BaseModel):
    """SEO title + meta description.

    Accepts both key styles the generators use today:
    ``{seo_title, seo_description}`` (auto_fix) and ``{title, description}``
    (geo_improvement). Both fields are required so a missing key triggers the
    one auto-repair round-trip in ``ask_structured``.
    """

    model_config = ConfigDict(populate_by_name=True)

    seo_title: str = Field(validation_alias=AliasChoices("seo_title", "title"))
    seo_description: str = Field(validation_alias=AliasChoices("seo_description", "description"))


class PromptList(RootModel[list[str]]):
    """A bare JSON array of prompt strings (brand-prompt generation)."""
