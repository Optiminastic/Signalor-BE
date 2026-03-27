import logging
import math
import re
from collections import Counter
from urllib.parse import urlparse

from .crawler import CrawlResult
from .utils import count_words, safe_score

logger = logging.getLogger("apps")

try:
    import textstat
except ImportError:
    textstat = None


# ── Patterns for GEO content quality detection ───────────────────────────

# Citations: "according to", "(Author, 2024)", "[1]", "Source: ...", etc.
CITATION_PATTERNS = [
    r"according to\b",
    r"\bcited? (?:by|in|from)\b",
    r"\(\w[\w\s&.,]+\d{4}\)",           # (Author, 2024) or (Chen et al., 2024)
    r"\[\d+\]",                          # [1], [2] numbered references
    r"\bsource:\s",
    r"\breference:\s",
    r"\bas reported by\b",
    r"\bpublished (?:in|by)\b",
    r"\bresearch (?:by|from|shows)\b",
    r"\bstudy (?:by|from|shows|found)\b",
    r"\bdata from\b",
]

# Statistics: numbers with context (not just any number)
STAT_PATTERNS = [
    r"\d+(?:\.\d+)?%",                   # 55%, 3.5%
    r"\$\d[\d,.]*\s*(?:billion|million|trillion|thousand|B|M|K)?",  # $1.5 billion
    r"\d[\d,.]*\s*(?:billion|million|trillion|thousand)",           # 1.5 million
    r"\d+x\s+(?:more|faster|slower|better|higher|lower|increase|growth)",  # 10x more
    r"\b(?:increased?|decreased?|grew?|rose|fell|dropped?)\s+(?:by\s+)?\d",
    r"\d+\s*(?:out of|/)\s*\d+",         # 9 out of 10, 3/4
    r"\b(?:average|median|mean)\s+(?:of\s+)?\d",
    r"\d+(?:\.\d+)?\s*(?:per ?cent|percent)",
]

# Expert quotes: quoted text with attribution
QUOTE_PATTERNS = [
    r"['\u2018\u201C\"][\w\s]{15,}['\u2019\u201D\"]\s*(?:,?\s*(?:says?|said|explains?|notes?|argues?|according to|wrote|states?))",
    r"(?:says?|said|explains?|notes?|argues?|wrote|states?)\s+[\w\s]+[,:]?\s*['\u2018\u201C\"]",
    r"['\u2018\u201C\"][\w\s]{15,}['\u2019\u201D\"]\s*[-\u2014\u2013]\s*\w",  # "Quote" — Name
]

# Authoritative tone markers
AUTHORITY_PATTERNS = [
    r"\b(?:demonstrably|definitively|conclusively|systematically)\b",
    r"\bbased on (?:our|my|the) (?:analysis|research|data|findings|testing)\b",
    r"\b(?:our|my) (?:research|analysis|data|findings|testing) (?:shows?|reveals?|indicates?|confirms?|demonstrates?)\b",
    r"\b(?:evidence|data) (?:shows?|suggests?|indicates?|confirms?)\b",
    r"\b(?:it is|it's) (?:clear|evident|well.established|proven|documented)\b",
    r"\b(?:critical|essential|fundamental|imperative)\s+(?:to|that|for)\b",
    r"\b(?:best practice|industry standard|proven (?:method|approach|strategy))\b",
    r"\b(?:we (?:recommend|advise|suggest)|(?:should|must) (?:be|ensure|implement))\b",
]

# Hedging/uncertain language (opposite of authoritative)
HEDGING_PATTERNS = [
    r"\b(?:i think|i guess|maybe|perhaps|possibly|might be|could be|not sure)\b",
    r"\b(?:it seems like|sort of|kind of|in my opinion)\b",
    r"\b(?:i believe|i feel|i suppose)\b",
]

# Answer-first indicators
ANSWER_FIRST_PATTERNS = [
    r"^(?:the\s+)?(?:short\s+)?answer\s+is\b",
    r"^(?:in\s+short|simply\s+put|to\s+summarize|in\s+summary|tl;?dr)\b",
    r"^(?:yes|no)[,.]",
    r"^(?:\w+\s+){1,10}(?:is|are|was|were|means?|refers?\s+to)\b",  # Definition-style opening
]


# ── Section 1: Structure Signals (35 pts) ─────────────────────────────────

def _score_structure(crawl: CrawlResult) -> tuple[float, dict]:
    """Score content structure signals. Max 35 pts."""
    soup = crawl.soup
    details = {}
    score = 0.0

    # 1. H1 present and singular (5 pts)
    h1_tags = soup.find_all("h1")
    h1_count = len(h1_tags)
    if h1_count == 1:
        score += 5
        details["h1_singular"] = True
    else:
        details["h1_singular"] = False
        details["_finding_h1"] = "no_h1" if h1_count == 0 else "multiple_h1"

    # 2. Proper heading hierarchy (5 pts)
    headings = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        headings.append(int(tag.name[1]))
    hierarchy_ok = True
    for i in range(1, len(headings)):
        if headings[i] - headings[i - 1] > 1:
            hierarchy_ok = False
            break
    if hierarchy_ok and headings:
        score += 5
        details["heading_hierarchy"] = True
    else:
        details["heading_hierarchy"] = False
        details["_finding_hierarchy"] = "broken_heading_hierarchy"

    # 3. FAQ section (8 pts)
    faq_found = False
    for tag in soup.find_all(re.compile(r"^h[2-4]$")):
        if tag.get_text() and "faq" in tag.get_text().lower():
            faq_found = True
            break
    if not faq_found:
        faq_found = bool(soup.find(class_=re.compile(r"faq", re.I)))
    if not faq_found:
        faq_found = bool(soup.find(id=re.compile(r"faq", re.I)))
    if not faq_found:
        # Check for details/summary elements (accordion FAQ pattern)
        for details_el in soup.find_all("details"):
            summary = details_el.find("summary")
            if summary and summary.get_text(strip=True).endswith("?"):
                faq_found = True
                break
    if not faq_found:
        # Check for headings that are questions (end with ?)
        question_headings = [
            tag for tag in soup.find_all(re.compile(r"^h[2-5]$"))
            if tag.get_text(strip=True).endswith("?")
        ]
        if len(question_headings) >= 3:
            faq_found = True
    details["faq_section"] = faq_found
    if faq_found:
        score += 8
    else:
        details["_finding_faq"] = "no_faq_section"

    # 4. Lists present (4 pts)
    lists = soup.find_all(["ul", "ol"])
    details["list_count"] = len(lists)
    if lists:
        score += 4
        details["lists_present"] = True
    else:
        details["lists_present"] = False
        details["_finding_lists"] = "no_lists"

    # 5. Tables present (3 pts)
    tables = soup.find_all("table")
    details["tables_present"] = bool(tables)
    if tables:
        score += 3

    # 6. Answer-first format (5 pts) — Princeton: direct answer at top
    first_p = soup.find("p")
    answer_first = False
    if first_p:
        first_text = first_p.get_text(strip=True).lower()[:200]
        for pattern in ANSWER_FIRST_PATTERNS:
            if re.search(pattern, first_text, re.I):
                answer_first = True
                break
        # Also check: first paragraph is concise and informative (definition-like)
        if not answer_first and 20 <= len(first_text.split()) <= 60:
            # Short, direct first paragraph is good
            if any(w in first_text for w in ["is ", "are ", "means ", "refers "]):
                answer_first = True
    details["answer_first_format"] = answer_first
    if answer_first:
        score += 5
    else:
        details["_finding_answer"] = "no_answer_first"

    # 7. Internal links >= 3 (5 pts)
    internal_count = len(crawl.internal_links)
    details["internal_link_count"] = internal_count
    if internal_count >= 3:
        score += 5
    else:
        details["_finding_links"] = "few_internal_links"

    return score, details


# ── Section 2: GEO Content Quality (65 pts) ──────────────────────────────

def _count_pattern_matches(text: str, patterns: list[str]) -> int:
    """Count total regex matches across all patterns."""
    count = 0
    for p in patterns:
        count += len(re.findall(p, text, re.I))
    return count


def _score_geo_quality(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Score GEO content quality based on Princeton research methods.
    Weighted by actual effectiveness data from the study. Max 65 pts.
    """
    text = crawl.text
    text_lower = text.lower()
    html_lower = crawl.html.lower()
    soup = crawl.soup
    details = {}
    score = 0.0
    word_count = count_words(text)
    details["word_count"] = word_count

    # ── Method 1: Cite Sources (+40% effectiveness) → 12 pts ──────────
    citation_count = _count_pattern_matches(text, CITATION_PATTERNS)
    # Also count reference/bibliography sections
    for tag in soup.find_all(re.compile(r"^h[2-4]$")):
        tag_text = tag.get_text(strip=True).lower()
        if tag_text in ("references", "sources", "bibliography", "works cited", "citations"):
            citation_count += 3  # Bonus for dedicated section

    details["citation_count"] = citation_count
    if citation_count >= 5:
        score += 12
    elif citation_count >= 3:
        score += 9
    elif citation_count >= 1:
        score += 5
    else:
        details["_finding_cite"] = "no_citations"

    # ── Method 2: Statistics Addition (+37% effectiveness) → 10 pts ───
    stat_count = _count_pattern_matches(text, STAT_PATTERNS)
    details["statistic_count"] = stat_count
    if stat_count >= 5:
        score += 10
    elif stat_count >= 3:
        score += 7
    elif stat_count >= 1:
        score += 4
    else:
        details["_finding_stats"] = "no_statistics"

    # ── Method 3: Expert Quotes (+30% effectiveness) → 8 pts ─────────
    quote_count = _count_pattern_matches(text, QUOTE_PATTERNS)
    # Also check for <blockquote> tags
    blockquotes = soup.find_all("blockquote")
    quote_count += len(blockquotes)
    details["quote_count"] = quote_count
    if quote_count >= 3:
        score += 8
    elif quote_count >= 1:
        score += 5
    else:
        details["_finding_quotes"] = "no_expert_quotes"

    # ── Method 4: Authoritative Tone (+25% effectiveness) → 8 pts ────
    authority_count = _count_pattern_matches(text_lower, AUTHORITY_PATTERNS)
    hedge_count = _count_pattern_matches(text_lower, HEDGING_PATTERNS)
    details["authority_signals"] = authority_count
    details["hedging_signals"] = hedge_count

    # Net authority = authority signals minus hedging
    net_authority = authority_count - hedge_count
    if net_authority >= 4:
        score += 8
    elif net_authority >= 2:
        score += 5
    elif net_authority >= 0 and authority_count >= 1:
        score += 3
    else:
        details["_finding_tone"] = "weak_authoritative_tone"

    # ── Method 5: Easy-to-Understand (+20% effectiveness) → 7 pts ────
    readability_score = 0
    if textstat and len(text) > 100:
        fk_grade = textstat.flesch_kincaid_grade(text)
        flesch_ease = textstat.flesch_reading_ease(text)
        details["fk_grade"] = round(fk_grade, 1)
        details["flesch_ease"] = round(flesch_ease, 1)
        # Optimal: grade 6-12, ease 50-80
        if 6 <= fk_grade <= 12:
            readability_score += 4
        elif 4 <= fk_grade <= 14:
            readability_score += 2
        else:
            details["_finding_read"] = "poor_readability"

        if flesch_ease >= 60:
            readability_score += 3
        elif flesch_ease >= 40:
            readability_score += 1
    else:
        details["fk_grade"] = None
        details["flesch_ease"] = None
        readability_score = 3  # Neutral if can't compute

    score += readability_score

    # ── Method 6: Technical Terms (+18% effectiveness) → 5 pts ────────
    # Detect domain-specific terminology (acronyms, compound technical terms)
    # Acronyms in parentheses like "Retrieval-Augmented Generation (RAG)"
    acronym_definitions = re.findall(r"\b[A-Z][a-z]+(?:[\s-][A-Z][a-z]+)+\s*\([A-Z]{2,}\)", text)
    # Standalone acronyms (3+ uppercase letters)
    standalone_acronyms = set(re.findall(r"\b[A-Z]{3,}\b", text))
    # Filter out common non-technical acronyms
    common_words = {"THE", "AND", "FOR", "NOT", "BUT", "ARE", "WAS", "HAS", "HIS", "HER",
                    "ITS", "ALL", "CAN", "HAD", "HIM", "WHO", "DID", "GET", "HOW", "MAY",
                    "NEW", "NOW", "OLD", "OUR", "OWN", "SAY", "SHE", "TOO", "USE", "FAQ"}
    technical_acronyms = standalone_acronyms - common_words
    # Compound technical terms (hyphenated)
    compound_terms = re.findall(r"\b\w+-(?:based|driven|powered|enabled|focused|oriented|specific|level|aware)\b", text_lower)

    tech_term_count = len(acronym_definitions) + len(technical_acronyms) + len(compound_terms)
    details["technical_term_count"] = tech_term_count
    details["acronym_definitions"] = len(acronym_definitions)
    if tech_term_count >= 8:
        score += 5
    elif tech_term_count >= 4:
        score += 3
    elif tech_term_count >= 1:
        score += 1
    else:
        details["_finding_tech"] = "no_technical_terms"

    # ── Method 7: Vocabulary Diversity (+15% effectiveness) → 5 pts ───
    if word_count >= 50:
        words = re.findall(r"\b[a-z]{3,}\b", text_lower)
        if words:
            unique_words = set(words)
            # Type-Token Ratio (TTR) — capped sample to avoid length bias
            sample = words[:500]
            ttr = len(set(sample)) / len(sample) if sample else 0
            details["vocabulary_ttr"] = round(ttr, 3)
            details["unique_word_count"] = len(unique_words)

            if ttr >= 0.65:
                score += 5
            elif ttr >= 0.50:
                score += 3
            elif ttr >= 0.35:
                score += 1
            else:
                details["_finding_vocab"] = "low_vocabulary_diversity"
        else:
            details["vocabulary_ttr"] = 0
            details["_finding_vocab"] = "low_vocabulary_diversity"
    else:
        details["vocabulary_ttr"] = None

    # ── Method 8: Fluency & Structure (+15-30% effectiveness) → 5 pts ─
    fluency_score = 0

    # Word count (comprehensive content)
    if word_count >= 1500:
        fluency_score += 2
    elif word_count >= 800:
        fluency_score += 1
    else:
        details["_finding_wc"] = "low_word_count"

    # Paragraph structure (short, focused paragraphs)
    paragraphs = soup.find_all("p")
    para_lengths = [count_words(p.get_text()) for p in paragraphs if p.get_text(strip=True)]
    if para_lengths:
        avg_para = sum(para_lengths) / len(para_lengths)
        details["avg_paragraph_words"] = round(avg_para, 1)
        details["paragraph_count"] = len(para_lengths)
        # Princeton: 2-3 sentences per paragraph is ideal
        if 20 <= avg_para <= 80:
            fluency_score += 2
        elif 15 <= avg_para <= 120:
            fluency_score += 1
        else:
            details["_finding_para"] = "poor_paragraph_structure"
    else:
        details["avg_paragraph_words"] = 0

    # Transition words (logical flow)
    transitions = [
        r"\bhowever\b", r"\btherefore\b", r"\bmoreover\b", r"\bfurthermore\b",
        r"\bin addition\b", r"\bconsequently\b", r"\bas a result\b",
        r"\bon the other hand\b", r"\bin contrast\b", r"\bfor (?:example|instance)\b",
        r"\bspecifically\b", r"\bnotably\b", r"\bimportantly\b",
    ]
    transition_count = sum(len(re.findall(p, text_lower)) for p in transitions)
    details["transition_word_count"] = transition_count
    if transition_count >= 5:
        fluency_score += 1

    score += fluency_score

    # ── Method 9: Keyword Stuffing Penalty (-10%) → -5 pts ───────────
    if word_count >= 100:
        # Detect keyword stuffing: high frequency of repeated 2-3 word phrases
        # Split into bigrams
        _STOP_WORDS = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
            "it", "its", "this", "that", "as", "if", "so", "not", "no", "can",
            "will", "do", "you", "we", "he", "she", "they", "have", "has", "had",
        }
        words_list = [w for w in re.findall(r"\b[a-z]{2,}\b", text_lower) if w not in _STOP_WORDS]
        bigrams = [f"{words_list[i]} {words_list[i+1]}" for i in range(len(words_list)-1)]
        if bigrams:
            bigram_counts = Counter(bigrams)
            most_common_freq = bigram_counts.most_common(1)[0][1] if bigram_counts else 0
            # If the top bigram appears more than 2% of all bigrams, likely stuffed
            stuffing_ratio = most_common_freq / len(bigrams) if bigrams else 0
            details["top_bigram_frequency"] = round(stuffing_ratio, 4)
            details["top_bigram"] = bigram_counts.most_common(1)[0][0] if bigram_counts else ""

            if stuffing_ratio > 0.03:  # >3% — heavy stuffing
                score -= 5
                details["_finding_stuff"] = "keyword_stuffing"
                details["keyword_stuffing_detected"] = True
            elif stuffing_ratio > 0.02:  # >2% — mild stuffing
                score -= 2
                details["keyword_stuffing_detected"] = "mild"
            else:
                details["keyword_stuffing_detected"] = False

    return score, details


# ── Main scorer ───────────────────────────────────────────────────────────

def score_content(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Content scoring based on Princeton GEO research (arXiv:2311.09735).

    Two sections:
    - Structure Signals (35 pts): H1, headings, FAQ, lists, tables, answer-first, links
    - GEO Content Quality (65 pts): citations (+40%), statistics (+37%),
      quotes (+30%), authority (+25%), readability (+20%), technical terms (+18%),
      vocabulary diversity (+15%), fluency (+15-30%), keyword stuffing penalty (-10%)
    """
    if not crawl.ok:
        return 0.0, {"error": crawl.error}

    details = {"checks": {}, "findings": []}

    # Part 1: Structure (max 35 pts)
    structure_score, structure_details = _score_structure(crawl)
    details["checks"]["structure"] = structure_details

    # Collect structure findings
    for key, val in structure_details.items():
        if key.startswith("_finding"):
            details["findings"].append(val)

    # Part 2: GEO Content Quality (max 65 pts)
    geo_score, geo_details = _score_geo_quality(crawl)
    details["checks"]["geo_quality"] = geo_details

    # Collect GEO findings
    for key, val in geo_details.items():
        if key.startswith("_finding"):
            details["findings"].append(val)

    total = structure_score + geo_score

    # Word count gate — thin pages cannot score high on structure alone
    word_count = geo_details.get("word_count", 0)
    if word_count < 50:
        total = min(total, 5.0)
        details["findings"].append("extremely_thin_content")
        details["checks"]["word_count_cap"] = 5
    elif word_count < 100:
        total = min(total, 10.0)
        details["findings"].append("very_thin_content")
        details["checks"]["word_count_cap"] = 10

    total = safe_score(total)
    details["score"] = total
    details["checks"]["structure_score"] = round(structure_score, 1)
    details["checks"]["geo_quality_score"] = round(geo_score, 1)

    return total, details
