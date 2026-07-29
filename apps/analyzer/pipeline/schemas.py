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


# ── Task enrichment (drafted, page-specific fix content) ──────────────────────


class FaqPair(BaseModel):
    """One drafted FAQ entry."""

    question: str = Field(validation_alias=AliasChoices("question", "q"))
    answer: str = Field(validation_alias=AliasChoices("answer", "a"))

    model_config = ConfigDict(populate_by_name=True)


class FaqDraft(BaseModel):
    """A set of drafted FAQ Q&A pairs grounded in the page + brand corpus."""

    pairs: list[FaqPair] = Field(default_factory=list)


class CitationItem(BaseModel):
    """A claim on the page and a concrete, attributable source sentence for it."""

    claim: str
    source: str
    sentence: str


class CitationSuggestions(BaseModel):
    """Concrete citation sentences the author can drop next to existing claims."""

    items: list[CitationItem] = Field(default_factory=list)


class ParagraphRewrite(BaseModel):
    """A targeted rewrite of one weak paragraph from the page."""

    original: str = Field(default="")
    rewritten: str


class TaskGuidance(BaseModel):
    """A page-specific fix for any finding without a specialised drafter.

    The finding engine's static ``action`` text is identical for every customer
    ("Add a single H1 tag wrapping your page title"). This is the same
    instruction rewritten against the page actually being analysed: which
    element is wrong, what it currently says, and what to change it to.

    ``observation`` is what makes it non-generic — it must quote or name real
    content from the page, so a reviewer can tell instantly whether the model
    actually read the page or fell back to boilerplate.
    """

    observation: str = Field(default="")
    steps: list[str] = Field(default_factory=list)
    snippet: str = Field(default="")


# ── Site-specific findings (discovered, not rule-matched) ────────────────────


class AnswerBlock(BaseModel):
    """Paste-ready content that makes one page answer one tracked prompt.

    Engines retrieve *passages*, so the unit of work is not "improve this page"
    but "put a direct, self-contained answer to this exact question on it". The
    fields mirror what an extractor looks for: a question-shaped heading, the
    answer in the first sentences before any context, and short supporting
    points it can lift independently.

    ``faqs`` feed a deterministic FAQPage JSON-LD build in Python rather than
    being written by the model - schema is a format a parser should produce, not
    something to hope a language model emits validly.
    """

    heading: str
    answer: str
    supporting_points: list[str] = Field(default_factory=list)
    faqs: list[FaqPair] = Field(default_factory=list)
    placement: str = Field(default="")


class SiteFinding(BaseModel):
    """One issue found by reading the actual site, outside the 83 fixed rules.

    The rule engine can only report problems someone wrote a checker for. This
    is the open-ended half: real issues on this specific site, including ones no
    generic rule can express (a guide page structured unlike the blog pages, a
    pricing page that never states a price, a product page whose H2s answer no
    question a buyer would ask).

    ``evidence`` must be a verbatim quote from the crawled page. It is verified
    against the page text before the finding is kept, which is what stops this
    from becoming a hallucination surface.
    """

    title: str
    issue: str
    evidence: str
    fix: str
    url: str = Field(default="")
    pillar: str = Field(default="content")
    priority: str = Field(default="medium")
    snippet: str = Field(default="")
