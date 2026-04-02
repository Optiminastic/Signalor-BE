"""
Content Scorer v3 — Impact-based, not feature-counting.

Question: "Would an AI TRUST this page enough to speak from it?"

Score = (Intent Clarity × 0.30) + (Coverage Depth × 0.30) +
        (Information Density × 0.20) + (Structure & Flow × 0.20)
"""
import json
import logging
import math
import re
from collections import Counter

from .crawler import CrawlResult
from .utils import count_words, safe_score

logger = logging.getLogger("apps")

try:
    import textstat
except ImportError:
    textstat = None


# ── Patterns ──────────────────────────────────────────────────────────────

CITATION_PATTERNS = [
    r"according to\b",
    r"\bcited? (?:by|in|from)\b",
    r"\(\w[\w\s&.,]+\d{4}\)",
    r"\[\d+\]",
    r"\bsource:\s",
    r"\breference:\s",
    r"\bas reported by\b",
    r"\bpublished (?:in|by)\b",
    r"\bresearch (?:by|from|shows)\b",
    r"\bstudy (?:by|from|shows|found)\b",
    r"\bdata from\b",
]

STAT_PATTERNS = [
    r"\d+(?:\.\d+)?%",
    r"\$\d[\d,.]*\s*(?:billion|million|trillion|thousand|B|M|K)?",
    r"\d[\d,.]*\s*(?:billion|million|trillion|thousand)",
    r"\d+x\s+(?:more|faster|slower|better|higher|lower|increase|growth)",
    r"\b(?:increased?|decreased?|grew?|rose|fell|dropped?)\s+(?:by\s+)?\d",
    r"\d+\s*(?:out of|/)\s*\d+",
]

VAGUE_WORDS = [
    r"\bbest\b", r"\btop\b", r"\bamazing\b", r"\bgreat\b", r"\bawesome\b",
    r"\bincredible\b", r"\bfantastic\b", r"\bwonderful\b", r"\bexcellent\b",
    r"\bperfect\b", r"\bultimate\b", r"\bunique\b",
]

HEDGING_PATTERNS = [
    r"\b(?:i think|i guess|maybe|perhaps|possibly|might be|could be|not sure)\b",
    r"\b(?:it seems like|sort of|kind of|in my opinion)\b",
    r"\b(?:i believe|i feel|i suppose)\b",
]

AUTHORITY_PATTERNS = [
    r"\bbased on (?:our|my|the) (?:analysis|research|data|findings|testing)\b",
    r"\b(?:our|my) (?:research|analysis|data|findings|testing) (?:shows?|reveals?|indicates?)\b",
    r"\b(?:evidence|data) (?:shows?|suggests?|indicates?|confirms?)\b",
    r"\b(?:we (?:tested|built|implemented|analyzed|measured))\b",
]


def _count_patterns(text: str, patterns: list[str]) -> int:
    return sum(len(re.findall(p, text, re.I)) for p in patterns)


# ── 1. Intent Clarity (30 pts) ────────────────────────────────────────────

def _score_intent_clarity(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Can an AI summarize this page in one clear sentence?
    Measures: clear topic, focused content, no confusion.
    """
    text = crawl.text
    soup = crawl.soup
    details = {}
    score = 0.0
    word_count = count_words(text)

    # 1a. Title clarity (8 pts)
    title_tag = soup.find("title")
    h1_tags = soup.find_all("h1")
    title_text = (title_tag.get_text(strip=True) if title_tag else "").lower()
    h1_text = (h1_tags[0].get_text(strip=True) if h1_tags else "").lower()

    has_clear_title = bool(title_text) and len(title_text.split()) >= 3
    has_clear_h1 = bool(h1_text) and len(h1_text.split()) >= 2
    h1_count = len(h1_tags)

    if has_clear_title and has_clear_h1:
        score += 8
    elif has_clear_title or has_clear_h1:
        score += 5
    else:
        score += 1

    details["has_clear_title"] = has_clear_title
    details["has_clear_h1"] = has_clear_h1
    details["h1_count"] = h1_count

    if h1_count == 0:
        details["_finding"] = "no_h1"
    elif h1_count > 1:
        details["_finding"] = "multiple_h1"

    # 1b. Opening paragraph is direct (8 pts) — answer-first format
    first_p = soup.find("p")
    opening_clear = False
    if first_p:
        opening = first_p.get_text(strip=True)
        opening_words = len(opening.split())
        # Good: 15-60 words, contains a definition or direct statement
        if 10 <= opening_words <= 80:
            # Contains "is", "are", "means" — definition style
            if re.search(r"\b(?:is|are|means|refers to|provides?|helps?|allows?)\b", opening.lower()):
                opening_clear = True
                score += 8
            else:
                score += 4
        elif opening_words > 0:
            score += 2
    details["opening_clear"] = opening_clear

    # 1c. Consistent topic throughout (7 pts)
    headings = [tag.get_text(strip=True).lower() for tag in soup.find_all(re.compile(r"^h[2-4]$"))]
    details["subheading_count"] = len(headings)
    if len(headings) >= 3:
        score += 7
    elif len(headings) >= 1:
        score += 4
    else:
        score += 1

    # 1d. Meta description exists and is informative (7 pts)
    meta_desc = soup.find("meta", attrs={"name": "description"})
    desc_text = (meta_desc["content"].strip() if meta_desc and meta_desc.get("content") else "")
    details["has_meta_description"] = bool(desc_text)
    if desc_text and 50 <= len(desc_text) <= 160:
        score += 7
    elif desc_text:
        score += 4
    else:
        score += 0

    return min(score, 30), details


# ── 2. Coverage Depth (30 pts) ────────────────────────────────────────────

def _score_coverage_depth(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Does the content cover the topic comprehensively?
    Not word count — subtopic coverage, evidence, examples.
    """
    text = crawl.text
    text_lower = text.lower()
    soup = crawl.soup
    details = {}
    score = 0.0
    word_count = count_words(text)

    # 2a. Subtopic coverage (10 pts) — number of distinct sections
    headings = [tag.get_text(strip=True) for tag in soup.find_all(re.compile(r"^h[2-4]$"))]
    unique_headings = len(set(h.lower() for h in headings))
    details["unique_sections"] = unique_headings

    if unique_headings >= 6:
        score += 10
    elif unique_headings >= 4:
        score += 7
    elif unique_headings >= 2:
        score += 4
    elif unique_headings >= 1:
        score += 2

    # 2b. Evidence density (10 pts) — citations + statistics + quotes
    citation_count = _count_patterns(text, CITATION_PATTERNS)
    stat_count = _count_patterns(text, STAT_PATTERNS)
    quote_count = len(soup.find_all("blockquote"))
    evidence_total = citation_count + stat_count + quote_count

    details["citation_count"] = citation_count
    details["stat_count"] = stat_count
    details["quote_count"] = quote_count
    details["evidence_total"] = evidence_total

    if evidence_total >= 10:
        score += 10
    elif evidence_total >= 6:
        score += 7
    elif evidence_total >= 3:
        score += 5
    elif evidence_total >= 1:
        score += 2
    else:
        details["_finding_evidence"] = "no_citations"

    # 2c. FAQ / Q&A coverage (5 pts)
    faq_found = False
    for tag in soup.find_all(re.compile(r"^h[2-4]$")):
        if "faq" in tag.get_text(strip=True).lower():
            faq_found = True
            break
    question_headings = [t for t in soup.find_all(re.compile(r"^h[2-5]$")) if t.get_text(strip=True).endswith("?")]
    if faq_found or len(question_headings) >= 3:
        score += 5
        details["has_faq"] = True
    else:
        details["has_faq"] = False
        details["_finding_faq"] = "no_faq_section"

    # 2d. Content depth (5 pts) — meaningful content, not thin
    if word_count >= 1500:
        score += 5
    elif word_count >= 800:
        score += 3
    elif word_count >= 300:
        score += 1
    else:
        details["_finding_thin"] = "low_word_count"

    details["word_count"] = word_count

    return min(score, 30), details


# ── 3. Information Density (20 pts) ───────────────────────────────────────

def _score_information_density(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Ratio of meaningful content to fluff.
    Penalizes: repetition, vague words, generic AI-style writing.
    """
    text = crawl.text
    text_lower = text.lower()
    details = {}
    score = 0.0
    word_count = count_words(text)

    if word_count < 30:
        return 0, {"too_short": True}

    # 3a. Vague word ratio (6 pts) — fewer vague words = better
    vague_count = _count_patterns(text_lower, VAGUE_WORDS)
    vague_ratio = vague_count / word_count if word_count else 0
    details["vague_word_count"] = vague_count
    details["vague_ratio"] = round(vague_ratio, 4)

    if vague_ratio < 0.005:
        score += 6  # Very precise writing
    elif vague_ratio < 0.01:
        score += 4
    elif vague_ratio < 0.02:
        score += 2
    else:
        score += 0  # Too much fluff

    # 3b. Repetition penalty (6 pts) — unique bigrams ratio
    words_list = [w for w in re.findall(r"\b[a-z]{2,}\b", text_lower)
                  if w not in {"the", "and", "for", "are", "was", "were", "with", "that", "this", "from", "have", "has"}]
    bigrams = [f"{words_list[i]} {words_list[i+1]}" for i in range(len(words_list)-1)]
    if bigrams:
        bigram_counts = Counter(bigrams)
        top_freq = bigram_counts.most_common(1)[0][1]
        repetition_ratio = top_freq / len(bigrams)
        details["repetition_ratio"] = round(repetition_ratio, 4)
        details["top_repeated"] = bigram_counts.most_common(1)[0][0]

        if repetition_ratio < 0.015:
            score += 6
        elif repetition_ratio < 0.025:
            score += 4
        elif repetition_ratio < 0.035:
            score += 2
        else:
            details["_finding_stuff"] = "keyword_stuffing"
    else:
        score += 3

    # 3c. Vocabulary richness (4 pts)
    words = re.findall(r"\b[a-z]{3,}\b", text_lower)
    if words:
        sample = words[:500]
        ttr = len(set(sample)) / len(sample)
        details["vocabulary_ttr"] = round(ttr, 3)
        if ttr >= 0.65:
            score += 4
        elif ttr >= 0.50:
            score += 2
        elif ttr >= 0.35:
            score += 1
    else:
        details["vocabulary_ttr"] = 0

    # 3d. Authority vs hedging (4 pts)
    authority_count = _count_patterns(text_lower, AUTHORITY_PATTERNS)
    hedge_count = _count_patterns(text_lower, HEDGING_PATTERNS)
    net_authority = authority_count - hedge_count
    details["authority_signals"] = authority_count
    details["hedging_signals"] = hedge_count

    if net_authority >= 3:
        score += 4
    elif net_authority >= 1:
        score += 2
    elif net_authority >= 0:
        score += 1
    else:
        details["_finding_tone"] = "weak_authoritative_tone"

    return min(score, 20), details


# ── 4. Structure & Flow (20 pts) ──────────────────────────────────────────

def _score_structure_flow(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Logical progression: intro → explanation → examples → conclusion.
    Good structure helps AI parse and extract information.
    """
    text = crawl.text
    text_lower = text.lower()
    soup = crawl.soup
    details = {}
    score = 0.0

    # 4a. Heading hierarchy (5 pts)
    headings = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        headings.append(int(tag.name[1]))
    hierarchy_ok = True
    for i in range(1, len(headings)):
        if headings[i] - headings[i - 1] > 1:
            hierarchy_ok = False
            break
    if hierarchy_ok and len(headings) >= 3:
        score += 5
    elif headings:
        score += 3
    details["heading_hierarchy_ok"] = hierarchy_ok
    details["heading_count"] = len(headings)

    # 4b. Content variety (5 pts) — lists, tables, images
    lists = len(soup.find_all(["ul", "ol"]))
    tables = len(soup.find_all("table"))
    images = len(soup.find_all("img"))
    details["list_count"] = lists
    details["table_count"] = tables
    details["image_count"] = images

    variety = sum([lists > 0, tables > 0, images > 0])
    if variety >= 3:
        score += 5
    elif variety >= 2:
        score += 3
    elif variety >= 1:
        score += 2

    # 4c. Paragraph quality (5 pts)
    paragraphs = soup.find_all("p")
    para_lengths = [count_words(p.get_text()) for p in paragraphs if p.get_text(strip=True)]
    if para_lengths:
        avg_para = sum(para_lengths) / len(para_lengths)
        details["avg_paragraph_words"] = round(avg_para, 1)
        details["paragraph_count"] = len(para_lengths)
        if 20 <= avg_para <= 80:
            score += 5
        elif 15 <= avg_para <= 120:
            score += 3
        else:
            score += 1
            details["_finding_para"] = "poor_paragraph_structure"
    else:
        details["avg_paragraph_words"] = 0

    # 4d. Readability (3 pts)
    if textstat and len(text) > 100:
        fk_grade = textstat.flesch_kincaid_grade(text)
        details["fk_grade"] = round(fk_grade, 1)
        if 6 <= fk_grade <= 12:
            score += 3
        elif 4 <= fk_grade <= 14:
            score += 1
    else:
        details["fk_grade"] = None
        score += 1

    # 4e. Internal links (2 pts)
    internal_count = len(crawl.internal_links)
    details["internal_link_count"] = internal_count
    if internal_count >= 5:
        score += 2
    elif internal_count >= 2:
        score += 1
    else:
        details["_finding_links"] = "few_internal_links"

    return min(score, 20), details


# ── Main Scorer ───────────────────────────────────────────────────────────

def score_content(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Content Score v3 — Impact-based.

    Score = (Intent Clarity × 0.30) + (Coverage Depth × 0.30) +
            (Information Density × 0.20) + (Structure & Flow × 0.20)

    Each sub-score is 0-100 normalized, then weighted.
    """
    if not crawl.ok:
        return 0.0, {"error": crawl.error}

    details = {"checks": {}, "findings": []}

    # Score each dimension (each returns 0-N, we normalize to 0-100)
    intent_raw, intent_details = _score_intent_clarity(crawl)       # max 30
    coverage_raw, coverage_details = _score_coverage_depth(crawl)    # max 30
    density_raw, density_details = _score_information_density(crawl)  # max 20
    structure_raw, structure_details = _score_structure_flow(crawl)   # max 20

    # Normalize each to 0-100
    intent_score = (intent_raw / 30) * 100
    coverage_score = (coverage_raw / 30) * 100
    density_score = (density_raw / 20) * 100
    structure_score = (structure_raw / 20) * 100

    # Weighted total
    total = (
        intent_score * 0.30 +
        coverage_score * 0.30 +
        density_score * 0.20 +
        structure_score * 0.20
    )

    # Store details
    details["checks"]["intent_clarity"] = intent_details
    details["checks"]["coverage_depth"] = coverage_details
    details["checks"]["information_density"] = density_details
    details["checks"]["structure_flow"] = structure_details

    details["checks"]["intent_score"] = round(intent_score, 1)
    details["checks"]["coverage_score"] = round(coverage_score, 1)
    details["checks"]["density_score"] = round(density_score, 1)
    details["checks"]["structure_score"] = round(structure_score, 1)

    # Collect all findings
    for sub in [intent_details, coverage_details, density_details, structure_details]:
        for key, val in sub.items():
            if key.startswith("_finding"):
                details["findings"].append(val)

    total = safe_score(total)
    details["score"] = total
    details["checks"]["word_count"] = count_words(crawl.text)

    return total, details
