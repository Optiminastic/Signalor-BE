import logging

logger = logging.getLogger("apps")

RECOMMENDATION_RULES = {
    # ── Content Structure ─────────────────────────────────────────────────
    "no_h1": {
        "pillar": "content",
        "priority": "critical",
        "title": "Add an H1 Tag",
        "description": "Your page is missing an H1 tag. This is the first thing AI models look at to understand your page topic.",
        "action": "Add a single H1 tag wrapping your page title: <h1>Your Page Title</h1>. Ensure it clearly describes the page content.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },
    "multiple_h1": {
        "pillar": "content",
        "priority": "high",
        "title": "Use Only One H1 Tag",
        "description": "Your page has multiple H1 tags. AI models expect a single, clear page title.",
        "action": "Keep only one H1 tag for your main page title. Convert other H1 tags to H2 or H3.",
        "impact_estimate": "Could improve your score by ~3 points",
        "category": "content",
    },
    "broken_heading_hierarchy": {
        "pillar": "content",
        "priority": "high",
        "title": "Fix Heading Hierarchy",
        "description": "Your heading tags skip levels (e.g., H1 -> H3). AI models use heading hierarchy to understand content structure.",
        "action": "Ensure headings follow a logical order: H1 -> H2 -> H3. Never skip levels.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },
    "no_faq_section": {
        "pillar": "content",
        "priority": "high",
        "title": "Add an FAQ Section",
        "description": "No FAQ section detected. Princeton GEO research shows FAQ content directly maps to how LLMs extract answers. Pages with FAQ schema show 40% higher AI visibility.",
        "action": "Add an FAQ section with Q&A pairs. Use <h2>FAQ</h2> or <h2>Frequently Asked Questions</h2> followed by question/answer pairs. Also add FAQPage schema markup.",
        "impact_estimate": "Could improve your score by ~8 points (+40% AI visibility with schema)",
        "category": "content",
    },
    "no_lists": {
        "pillar": "content",
        "priority": "medium",
        "title": "Add Structured Lists",
        "description": "No bullet or numbered lists found. Lists help AI models parse and cite specific items.",
        "action": "Add <ul> or <ol> lists to present key points, features, or steps in your content.",
        "impact_estimate": "Could improve your score by ~4 points",
        "category": "content",
    },
    "no_answer_first": {
        "pillar": "content",
        "priority": "high",
        "title": "Use Answer-First Format",
        "description": "Your content doesn't start with a direct answer. AI models prefer content that leads with a clear, concise answer before expanding on details.",
        "action": "Restructure your opening paragraph to directly answer the main question your page addresses. Start with 'X is...' or 'The answer is...' before diving into details. This is what AI models extract first.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },
    "few_internal_links": {
        "pillar": "content",
        "priority": "medium",
        "title": "Add More Internal Links",
        "description": "Fewer than 3 internal links found. Internal links help AI models understand your site structure and content relationships.",
        "action": "Add at least 3 internal links to related pages on your site within your content.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },

    # ── GEO Content Quality (Princeton Research Methods) ──────────────────

    # Method 1: Citations (+40% visibility boost — highest impact)
    "no_citations": {
        "pillar": "content",
        "priority": "critical",
        "title": "Add Authoritative Citations (Research: +40% Visibility)",
        "description": "No citations or references found. The Princeton GEO study found that adding authoritative citations provides the HIGHEST visibility boost of all methods — up to 40%. AI systems strongly prefer well-researched content with credible sources.",
        "action": "Add 3-5 citations per major section. Use formats like:\n- 'According to a 2024 Stanford study, AI tools improve productivity by 55% (Chen et al., 2024)'\n- 'Research from McKinsey shows that...'\n- 'As published in Nature...'\nAlso consider adding a References/Sources section at the end.",
        "impact_estimate": "Could improve your score by ~12 points (highest-impact GEO method)",
        "category": "content",
    },

    # Method 2: Statistics (+37% visibility boost)
    "no_statistics": {
        "pillar": "content",
        "priority": "critical",
        "title": "Add Statistics and Data Points (Research: +37% Visibility)",
        "description": "No statistics or quantitative data found. The Princeton study shows statistics addition provides a 37% visibility boost — the second most effective GEO method. AI systems prioritize factual, verifiable information.",
        "action": "Include specific numbers throughout your content:\n- '67% of Fortune 500 companies now use AI chatbots'\n- 'Revenue increased by $2.3 million in Q3 2024'\n- 'The average response time improved from 4.2s to 0.8s'\nAlways cite the source of statistics for maximum credibility.",
        "impact_estimate": "Could improve your score by ~10 points (2nd highest-impact GEO method)",
        "category": "content",
    },

    # Method 3: Expert Quotes (+30% visibility boost)
    "no_expert_quotes": {
        "pillar": "content",
        "priority": "high",
        "title": "Add Expert Quotes with Attribution (Research: +30% Visibility)",
        "description": "No expert quotes detected. Adding properly attributed quotes from recognized experts boosts AI visibility by up to 30%. Quotes provide extractable, citable content that AI models prefer.",
        "action": "Add 1-3 expert quotes with proper attribution:\n- '\"AI will be the great equalizer for small businesses,\" predicts Sam Altman, CEO of OpenAI.'\n- Use <blockquote> tags for longer quotes\n- Include the expert's title/credentials for maximum E-E-A-T impact",
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "content",
    },

    # Method 4: Authoritative Tone (+25% visibility boost)
    "weak_authoritative_tone": {
        "pillar": "content",
        "priority": "high",
        "title": "Strengthen Authoritative Tone (Research: +25% Visibility)",
        "description": "Your content uses hedging or uncertain language instead of confident, authoritative writing. The Princeton study found authoritative tone boosts visibility by 25%. AI models assess content quality partly through linguistic signals of authority.",
        "action": "Replace uncertain language with confident statements:\n- AVOID: 'This might help with SEO, I think'\n- USE: 'This strategy demonstrably improves SEO performance'\n- AVOID: 'Maybe you should consider...'\n- USE: 'Based on our analysis of 10,000 websites, implementing structured data increases organic traffic by 30%'\nBack up confident claims with data.",
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "content",
    },

    # Method 5: Readability (+20% visibility boost)
    "poor_readability": {
        "pillar": "content",
        "priority": "medium",
        "title": "Improve Readability (Research: +20% Visibility)",
        "description": "Your content readability is outside the optimal range. The Princeton study shows easy-to-understand content gets a 20% visibility boost. AI aims to provide helpful answers to users of all knowledge levels.",
        "action": "Aim for 8th-12th grade reading level (Flesch-Kincaid):\n- Use shorter sentences (15-20 words average)\n- Replace jargon with plain language, or explain it: 'RAG (Retrieval-Augmented Generation) works like a research assistant'\n- Use bullet points for complex lists\n- Break long paragraphs into 2-3 sentences each",
        "impact_estimate": "Could improve your score by ~7 points",
        "category": "content",
    },

    # Method 6: Technical Terms (+18% visibility boost)
    "no_technical_terms": {
        "pillar": "content",
        "priority": "medium",
        "title": "Include Domain-Specific Terminology (Research: +18% Visibility)",
        "description": "No technical terms or domain-specific terminology detected. Including appropriate technical terms signals expertise and helps AI match your content to specialized queries (+18% visibility boost).",
        "action": "Include domain-specific terms with definitions:\n- 'Core Web Vitals: LCP (Largest Contentful Paint), CLS (Cumulative Layout Shift)'\n- Define acronyms on first use: 'Retrieval-Augmented Generation (RAG)'\n- Use industry-standard terminology naturally throughout\n- Balance: use technical terms but explain them for accessibility",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },

    # Method 7: Vocabulary Diversity (+15% visibility boost)
    "low_vocabulary_diversity": {
        "pillar": "content",
        "priority": "medium",
        "title": "Increase Vocabulary Diversity (Research: +15% Visibility)",
        "description": "Your content has low vocabulary diversity (repetitive word usage). Diverse vocabulary indicates depth of knowledge and makes content more distinguishable to AI models (+15% visibility).",
        "action": "Improve vocabulary variety:\n- Use synonyms instead of repeating the same terms\n- Vary your sentence structures\n- Include contextual variations (e.g., 'AI', 'artificial intelligence', 'machine learning systems')\n- Use industry-specific jargon mixed with plain language",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },

    # Method 8: Fluency
    "low_word_count": {
        "pillar": "content",
        "priority": "high",
        "title": "Expand Content Length",
        "description": "Your page has thin content (<800 words). Thin content rarely gets cited by AI models. The Princeton study shows comprehensive, fluent content gets 15-30% more AI visibility.",
        "action": "Expand your content to 1,500+ words. Cover the topic comprehensively with:\n- Multiple sections with clear headings\n- Examples and case studies\n- Statistics and citations\n- FAQ section at the end",
        "impact_estimate": "Could improve your score by ~2 points (also unlocks other GEO methods)",
        "category": "content",
    },
    "poor_paragraph_structure": {
        "pillar": "content",
        "priority": "medium",
        "title": "Improve Paragraph Structure",
        "description": "Paragraphs are too long or too short. The Princeton study recommends 2-3 sentences per paragraph for optimal AI readability.",
        "action": "Break long paragraphs into focused chunks of 20-80 words each. Each paragraph should cover one idea with a clear topic sentence.",
        "impact_estimate": "Could improve your score by ~2 points",
        "category": "content",
    },

    # Method 9: Keyword Stuffing Penalty
    "keyword_stuffing": {
        "pillar": "content",
        "priority": "critical",
        "title": "Remove Keyword Stuffing (Research: -10% Visibility Penalty)",
        "description": "Keyword stuffing detected! Unlike traditional SEO, the Princeton study found keyword stuffing ACTIVELY DECREASES AI visibility by 10%. This is one of the few methods that actually hurts your score.",
        "action": "Remove repetitive keyword usage:\n- BAD: 'SEO optimization for SEO is the best SEO strategy. Our SEO experts provide SEO services.'\n- GOOD: 'Search engine optimization is essential for online visibility. Our experts help businesses improve their search rankings through strategic content development.'\nWrite naturally and use synonyms.",
        "impact_estimate": "Removing stuffing could recover ~5 points and prevent visibility penalty",
        "category": "content",
    },

    # ── Schema pillar ─────────────────────────────────────────────────────
    "no_jsonld": {
        "pillar": "schema",
        "priority": "critical",
        "title": "Add JSON-LD Structured Data",
        "description": "No structured data markup found. Schema markup is essential for AI to understand your content type.",
        "action": 'Add structured data using JSON-LD. At minimum, include Organization schema: <script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Your Company","url":"https://yoursite.com"}</script>',
        "impact_estimate": "Could improve your score by ~25 points",
        "category": "schema",
    },
    "no_faqpage_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Add FAQPage Schema (+40% AI Visibility)",
        "description": "No FAQPage schema found. The Princeton study specifically highlights FAQPage schema as providing up to 40% higher AI visibility.",
        "action": 'Add FAQPage schema markup to your FAQ section: {"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"...","acceptedAnswer":{"@type":"Answer","text":"..."}}]}',
        "impact_estimate": "Could improve your score by ~15 points (+40% AI visibility)",
        "category": "schema",
    },
    "no_article_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Add Article Schema",
        "description": "No Article/BlogPosting schema found. Article schema helps AI understand your content's authorship and topic.",
        "action": 'Add Article schema: {"@type":"Article","headline":"...","author":{"@type":"Person","name":"..."},"datePublished":"...","publisher":{"@type":"Organization","name":"..."}}',
        "impact_estimate": "Could improve your score by ~15 points",
        "category": "schema",
    },
    "no_organization_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Add Organization Schema",
        "description": "No Organization schema found. This is critical for AI brand recognition.",
        "action": 'Add Organization schema with name, url, logo, and sameAs (social profiles).',
        "impact_estimate": "Could improve your score by ~15 points",
        "category": "schema",
    },
    "invalid_jsonld_structure": {
        "pillar": "schema",
        "priority": "medium",
        "title": "Fix JSON-LD Structure",
        "description": "Your JSON-LD markup has structural issues (missing @context).",
        "action": 'Ensure all JSON-LD blocks include "@context": "https://schema.org" at the top level.',
        "impact_estimate": "Could improve your score by ~15 points",
        "category": "schema",
    },
    "incomplete_article_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Complete Article Schema Properties",
        "description": "Your Article schema is missing required properties (headline, author, datePublished). Incomplete schemas score lower.",
        "action": "Add missing properties: headline (title), author (Person with name), datePublished (ISO date), and optionally image, publisher, dateModified.",
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "schema",
    },
    "incomplete_organization_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Complete Organization Schema Properties",
        "description": "Your Organization schema is missing key properties. Add logo, sameAs (social links), description, and contactPoint.",
        "action": 'Fill in: {"name":"...","url":"...","logo":"...","sameAs":["linkedin","twitter"],"description":"...","contactPoint":{"@type":"ContactPoint","telephone":"...","contactType":"customer service"}}',
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "schema",
    },
    "incomplete_faqpage_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Complete FAQPage Schema Properties",
        "description": "Your FAQPage schema is missing the mainEntity array with Question/Answer pairs.",
        "action": 'Add mainEntity with Q&A pairs: {"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"How does X work?","acceptedAnswer":{"@type":"Answer","text":"X works by..."}}]}',
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "schema",
    },
    "incomplete_product_schema": {
        "pillar": "schema",
        "priority": "medium",
        "title": "Complete Product Schema Properties",
        "description": "Your Product schema is missing key properties like description, offers, or reviews.",
        "action": "Add description, image, offers (with price/currency), brand, and aggregateRating to your Product schema.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "schema",
    },
    "incomplete_blogposting_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Complete BlogPosting Schema Properties",
        "description": "Your BlogPosting schema is missing required properties (headline, author, datePublished).",
        "action": "Add headline, author (Person), datePublished, and optionally image, publisher, dateModified.",
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "schema",
    },
    "incomplete_newsarticle_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Complete NewsArticle Schema Properties",
        "description": "Your NewsArticle schema is missing required properties.",
        "action": "Add headline, author, datePublished, and publisher to your NewsArticle schema.",
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "schema",
    },
    "incomplete_howto_schema": {
        "pillar": "schema",
        "priority": "medium",
        "title": "Complete HowTo Schema Properties",
        "description": "Your HowTo schema is missing the step property with actual instructions.",
        "action": 'Add step array: {"@type":"HowTo","name":"...","step":[{"@type":"HowToStep","text":"Step 1..."}]}',
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "schema",
    },

    # ── E-E-A-T pillar ────────────────────────────────────────────────────
    "no_author": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Add Author Attribution (Copy & Paste)",
        "description": "No author name found. E-E-A-T signals are critical for AI trust and citation. AI models deprioritize content without clear authorship.",
        "action": "STEP 1 — Add a visible author byline to your page. Copy this HTML:\n"
                  '<p class="author">By <strong>[Your Name]</strong>, [Your Title] at [Your Brand]</p>\n\n'
                  "STEP 2 — Add the meta tag in your page <head>:\n"
                  '<meta name="author" content="[Your Name]">\n\n'
                  "STEP 3 — If you have Article schema, add the author property:\n"
                  '"author": {"@type": "Person", "name": "[Your Name]", "jobTitle": "[Your Title]", "url": "https://yoursite.com/about"}\n\n'
                  "PRO TIP: Use your real name and title — AI models cross-reference author names across the web to verify expertise.",
        "impact_estimate": "Could improve your score by ~15 points",
        "category": "eeat",
    },
    "no_author_bio": {
        "pillar": "eeat",
        "priority": "medium",
        "title": "Add Author Bio with Credentials",
        "description": "No author bio found. Author credentials and experience significantly boost AI trust. This is one of the strongest E-E-A-T signals.",
        "action": "STEP 1 — Add an author bio section. Copy this HTML template:\n"
                  '<div class="author-bio">\n'
                  '  <img src="/images/author.jpg" alt="[Name]" width="64" height="64">\n'
                  '  <div>\n'
                  '    <strong>[Your Name]</strong>\n'
                  '    <p>[Your Name] is a [title/role] with [X] years of experience in [field]. '
                  'They have [key credential, e.g., "published 50+ articles on AI optimization" '
                  'or "helped 200+ businesses improve their search visibility"]. '
                  'Connect on <a href="https://linkedin.com/in/you">LinkedIn</a>.</p>\n'
                  '  </div>\n'
                  '</div>\n\n'
                  "STEP 2 — Place it at the bottom of your article, above the comments section.",
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "eeat",
    },
    "no_publish_date": {
        "pillar": "eeat",
        "priority": "medium",
        "title": "Add Publish Date",
        "description": "No publish date found. AI models prefer fresh, dated content.",
        "action": 'Add <time datetime="2025-01-15">January 15, 2025</time> and article:published_time meta tag.',
        "impact_estimate": "Could improve your score by ~3 points",
        "category": "eeat",
    },
    "no_updated_date": {
        "pillar": "eeat",
        "priority": "medium",
        "title": "Add Last Updated Date",
        "description": "No update date found. Freshness signals matter for AI content ranking.",
        "action": 'Add article:modified_time meta tag and visible "Last updated: [date]" on the page.',
        "impact_estimate": "Could improve your score by ~2 points",
        "category": "eeat",
    },
    "few_external_citations": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Add External Citations",
        "description": "Fewer than 3 external citations. The Princeton GEO study found citations provide up to 40% visibility boost.",
        "action": "Add 3+ citations linking to authoritative external sources (research papers, industry reports, .gov, .edu domains).",
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "eeat",
    },
    "no_trust_links": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Add Authoritative Source Links",
        "description": "No links to high-trust domains (.gov, .edu, Wikipedia, major publications).",
        "action": "Add links to authoritative sources like .gov, .edu, Wikipedia, Nature, PubMed, or major publications to support claims.",
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "eeat",
    },
    "low_source_diversity": {
        "pillar": "eeat",
        "priority": "medium",
        "title": "Diversify External Sources",
        "description": "External links come from fewer than 3 different domains.",
        "action": "Link to at least 3-5 different authoritative domains to demonstrate research breadth.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "eeat",
    },
    "no_about_page": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Add About Page Link",
        "description": "No link to an About page found. Transparency about who runs the site is a core trust signal.",
        "action": "Add an About page explaining your organization, team, mission, and qualifications. Link it from navigation or footer.",
        "impact_estimate": "Could improve your score by ~3 points",
        "category": "eeat",
    },
    "no_first_hand_experience": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Add First-Hand Experience Signals",
        "description": "Your content lacks first-hand experience indicators. The first 'E' in E-E-A-T stands for Experience. AI models strongly prefer content from people who've actually done the thing.",
        "action": "STEP 1 — Add experience phrases throughout your content:\n"
                  "• 'In our testing of 50+ tools, we found that...'\n"
                  "• 'After implementing this for 6 months, the results were...'\n"
                  "• 'We built this system for 200+ clients and learned that...'\n"
                  "• 'Based on our hands-on experience with...'\n\n"
                  "STEP 2 — Add a case study section:\n"
                  "• Include specific numbers: 'Client X saw a 45% increase in...'\n"
                  "• Add before/after comparisons\n"
                  "• Include screenshots or data visualizations\n\n"
                  "STEP 3 — Add 'Why Trust This Content?' box:\n"
                  '<div class="trust-box">\n'
                  "  <strong>Why trust this guide?</strong>\n"
                  "  <p>This is based on [X years] of experience and [Y] real implementations. "
                  "We've tested every recommendation on live projects.</p>\n"
                  "</div>\n\n"
                  "PRO TIP: Screenshots, original data tables, and 'lessons learned' sections are the strongest experience signals for AI.",
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "eeat",
    },
    "no_expertise_indicators": {
        "pillar": "eeat",
        "priority": "medium",
        "title": "Demonstrate Deeper Expertise",
        "description": "Content lacks depth signals that show genuine expertise.",
        "action": "Add expert-level details: explain WHY things work, include pro tips, address common mistakes, use specific examples and data points.",
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "eeat",
    },
    "low_authority": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Build Content Authority",
        "description": "Your content doesn't demonstrate strong authoritativeness in its topic area.",
        "action": "Cite authoritative sources, reference industry standards, include data/statistics, mention partnerships or recognitions.",
        "impact_estimate": "Could improve your score by ~15 points",
        "category": "eeat",
    },
    "low_trust_signals": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Improve Trustworthiness Signals",
        "description": "Your content lacks trust indicators that AI models look for before citing a source.",
        "action": "Add: editorial/fact-check policy, disclosure statements, contact info, clear sourcing for claims, corrections policy.",
        "impact_estimate": "Could improve your score by ~15 points",
        "category": "eeat",
    },

    # ── Technical pillar ──────────────────────────────────────────────────
    "no_llms_txt": {
        "pillar": "technical",
        "priority": "high",
        "title": "Create llms.txt File (2-Minute Fix)",
        "description": "No llms.txt found. This emerging standard tells AI models what your site is about. It's a quick win — just create one text file.",
        "action": "STEP 1 — Create the file:\n"
                  "Create a file called llms.txt at your website root (e.g., https://yoursite.com/llms.txt)\n\n"
                  "STEP 2 — Paste this template:\n"
                  "# [Your Brand Name]\n"
                  "\n"
                  "## About\n"
                  "[One paragraph describing what your company does]\n"
                  "\n"
                  "## Key Pages\n"
                  "- Homepage: https://yoursite.com/\n"
                  "- About: https://yoursite.com/about\n"
                  "- Products: https://yoursite.com/products\n"
                  "- Blog: https://yoursite.com/blog\n"
                  "- Contact: https://yoursite.com/contact\n"
                  "\n"
                  "## Contact\n"
                  "- Email: hello@yoursite.com\n"
                  "- Twitter: @yourbrand\n\n"
                  "STEP 3 — Verify:\n"
                  "• Visit https://yoursite.com/llms.txt in your browser\n"
                  "• It should display as plain text",
        "impact_estimate": "Could improve your score by ~20 points (2-minute fix)",
        "category": "technical",
    },
    "ai_bots_blocked": {
        "pillar": "technical",
        "priority": "critical",
        "title": "Unblock AI Crawlers in robots.txt (Instant Fix)",
        "description": "Your robots.txt blocks AI crawlers. This prevents ChatGPT, Perplexity, Claude, and Gemini from indexing your content. This is the #1 reason sites are invisible to AI.",
        "action": "STEP 1 — Open your robots.txt file (usually at /robots.txt in your website root)\n\n"
                  "STEP 2 — Add these lines to allow AI crawlers:\n"
                  "User-agent: GPTBot\n"
                  "Allow: /\n"
                  "\n"
                  "User-agent: Google-Extended\n"
                  "Allow: /\n"
                  "\n"
                  "User-agent: anthropic-ai\n"
                  "Allow: /\n"
                  "\n"
                  "User-agent: ClaudeBot\n"
                  "Allow: /\n"
                  "\n"
                  "User-agent: PerplexityBot\n"
                  "Allow: /\n\n"
                  "STEP 3 — Remove any Disallow rules that block these bots\n\n"
                  "PRO TIP: If you use Cloudflare, check Security > Bots to ensure AI bots aren't blocked at the WAF level.",
        "impact_estimate": "Critical — could recover ~20 points immediately",
        "category": "technical",
    },
    "no_sitemap": {
        "pillar": "technical",
        "priority": "medium",
        "title": "Add sitemap.xml",
        "description": "No sitemap.xml found. AI crawlers use sitemaps to discover content.",
        "action": "Add a sitemap.xml to your domain root. Most CMS platforms generate these automatically.",
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "technical",
    },
    "meta_noindex": {
        "pillar": "technical",
        "priority": "critical",
        "title": "Remove noindex Meta Tag",
        "description": "Your page has a noindex meta tag, preventing AI models from indexing it.",
        "action": 'Remove <meta name="robots" content="noindex"> or change to content="index, follow".',
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "technical",
    },
    "no_https": {
        "pillar": "technical",
        "priority": "high",
        "title": "Enable HTTPS",
        "description": "Your site does not use HTTPS. Secure connections are a trust signal.",
        "action": "Install an SSL certificate and redirect HTTP to HTTPS.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "technical",
    },
    "slow_load_time": {
        "pillar": "technical",
        "priority": "medium",
        "title": "Improve Page Load Speed",
        "description": "Your page takes over 5 seconds to load. Fast pages are prioritized by AI crawlers.",
        "action": "Optimize images, enable compression, use a CDN, and minimize JavaScript.",
        "impact_estimate": "Could improve your score by ~15 points",
        "category": "technical",
    },
    "no_viewport": {
        "pillar": "technical",
        "priority": "medium",
        "title": "Add Viewport Meta Tag",
        "description": "No viewport meta tag found. This affects mobile-friendliness.",
        "action": 'Add <meta name="viewport" content="width=device-width, initial-scale=1"> to your <head>.',
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "technical",
    },
    "no_canonical": {
        "pillar": "technical",
        "priority": "medium",
        "title": "Add Canonical Tag",
        "description": "No canonical URL tag found. Helps prevent duplicate content issues.",
        "action": 'Add <link rel="canonical" href="https://yoursite.com/page-url"> to your <head>.',
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "technical",
    },

    # ── Entity pillar ─────────────────────────────────────────────────────
    "brand_not_in_ai": {
        "pillar": "entity",
        "priority": "high",
        "title": "Get Your Brand Into AI Responses",
        "description": "Your brand doesn't appear in AI responses (ChatGPT, Perplexity, Gemini) for your category. This is the ultimate GEO goal — here's the playbook.",
        "action": "STEP 1 — Get listed on directories AI models index:\n"
                  "• Product Hunt (producthunt.com) — submit your product\n"
                  "• G2 Reviews (g2.com) — claim your profile, ask customers for reviews\n"
                  "• Capterra / GetApp — create your listing\n"
                  "• Crunchbase — add your company profile\n"
                  "• AlternativeTo — add your product as an alternative\n\n"
                  "STEP 2 — Create comparison content:\n"
                  "• Write blog posts: '[Your Brand] vs [Competitor]'\n"
                  "• Create a 'Why [Your Brand]' page with data\n"
                  "• AI models heavily cite comparison content\n\n"
                  "STEP 3 — Get press mentions:\n"
                  "• Submit to HARO (Help A Reporter Out)\n"
                  "• Contribute guest posts to industry blogs\n"
                  "• Issue press releases for milestones\n\n"
                  "STEP 4 — Post on Reddit and Medium (see other recommendations)\n\n"
                  "PRO TIP: AI models update knowledge every few weeks. Changes you make today can appear in AI responses within 2-4 weeks.",
        "impact_estimate": "Could improve entity + AI visibility score by ~20 points",
        "category": "entity",
    },
    "no_social_profiles": {
        "pillar": "entity",
        "priority": "high",
        "title": "Add Social Profile Links (5-Minute Fix)",
        "description": "No social media profile links found. Social profiles strengthen your brand's entity graph — AI models use them to verify your brand exists and is active.",
        "action": "STEP 1 — Add social links to your page footer. Copy this HTML:\n"
                  '<div class="social-links">\n'
                  '  <a href="https://linkedin.com/company/yourbrand" rel="me">LinkedIn</a>\n'
                  '  <a href="https://twitter.com/yourbrand" rel="me">Twitter/X</a>\n'
                  '  <a href="https://github.com/yourbrand" rel="me">GitHub</a>\n'
                  '</div>\n\n'
                  "STEP 2 — Add sameAs to your Organization schema:\n"
                  '"sameAs": [\n'
                  '  "https://linkedin.com/company/yourbrand",\n'
                  '  "https://twitter.com/yourbrand",\n'
                  '  "https://github.com/yourbrand"\n'
                  ']\n\n'
                  "PRO TIP: LinkedIn and GitHub carry the most weight for B2B/SaaS brands.",
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "entity",
    },
    "no_wikipedia_presence": {
        "pillar": "entity",
        "priority": "medium",
        "title": "Build Toward Wikipedia Presence",
        "description": "Your brand was not found on Wikipedia. Wikipedia is the #1 source AI models use to verify entities. Having a Wikipedia page massively boosts AI brand recognition.",
        "action": "STEP 1 — Build notability (Wikipedia requires 'notability'):\n"
                  "• Get featured in 3+ independent reliable sources (news sites, industry publications)\n"
                  "• Aim for press coverage that isn't just press releases\n"
                  "• Win industry awards or get certifications\n\n"
                  "STEP 2 — While building notability, get on Wikipedia-adjacent sites:\n"
                  "• Wikidata (wikidata.org) — easier to create entries, still indexed by AI\n"
                  "• Crunchbase — often cited by AI models as an alternative\n"
                  "• LinkedIn Company Page — verified brand presence\n\n"
                  "STEP 3 — Once you have 3+ independent sources:\n"
                  "• Request creation at Wikipedia's 'Articles for Creation'\n"
                  "• Do NOT write it yourself (conflict of interest)\n"
                  "• Hire an experienced Wikipedia editor\n\n"
                  "PRO TIP: Even a Wikidata entry (much easier than Wikipedia) helps AI models recognize your brand as a real entity.",
        "impact_estimate": "Could improve your score by ~25 points (long-term strategy)",
        "category": "entity",
    },

    # ── Community Presence (Reddit & Medium) ──────────────────────────────
    "no_reddit_presence": {
        "pillar": "entity",
        "priority": "high",
        "title": "Post on Reddit to Boost Visibility",
        "description": "No Reddit presence detected. Reddit discussions are heavily indexed by AI models — Google, ChatGPT, and Perplexity all surface Reddit content. A single well-placed post can generate ongoing AI citations.",
        "action": "Post on Reddit to get your brand mentioned in AI-indexed discussions:\n\n"
                  "STEP 1 — Find the right subreddits:\n"
                  "• Search reddit.com for your industry keywords\n"
                  "• Good subreddits: r/SaaS, r/startups, r/webdev, r/smallbusiness, r/marketing, r/Entrepreneur, or niche subreddits for your industry\n\n"
                  "STEP 2 — Create a valuable post (NOT promotional):\n"
                  "• Title: \"How we solved [problem] at [Your Brand] — lessons learned\"\n"
                  "• Share a genuine story, case study, or insight\n"
                  "• Include specific data: \"We improved X by 40% in 3 months\"\n"
                  "• End with a question to encourage discussion\n\n"
                  "STEP 3 — Engage with comments:\n"
                  "• Reply to every comment within 24 hours\n"
                  "• Add more context and data when asked\n\n"
                  "PRO TIP: Answer questions in relevant threads where your product/expertise is a natural fit. This builds organic mentions that AI models pick up.",
        "impact_estimate": "Could improve entity score by ~10 points + ongoing AI citations",
        "category": "entity",
    },
    "no_medium_presence": {
        "pillar": "entity",
        "priority": "high",
        "title": "Publish on Medium for AI Discovery",
        "description": "No Medium presence detected. Medium articles rank highly in AI training data and search results. Publishing thought leadership on Medium creates authoritative backlinks and AI-indexable content about your brand.",
        "action": "Publish on Medium to create AI-discoverable brand content:\n\n"
                  "STEP 1 — Create a Medium account:\n"
                  "• Use your real name + company title for E-E-A-T\n"
                  "• Add a bio mentioning your brand and expertise\n\n"
                  "STEP 2 — Write a high-value article:\n"
                  "• Title: \"[Year] Guide to [Your Topic] — What Actually Works\"\n"
                  "• Include statistics, citations, and data points\n"
                  "• Mention your brand naturally (2-3 times max)\n"
                  "• Add relevant tags to reach the right audience\n\n"
                  "STEP 3 — Submit to publications:\n"
                  "• Submit to relevant Medium publications (Towards Data Science, Better Programming, The Startup, etc.)\n"
                  "• Publications amplify reach 5-10x\n\n"
                  "STEP 4 — Cross-link:\n"
                  "• Link from Medium article to your website\n"
                  "• Link from your website to the Medium article\n"
                  "• This creates a citation loop that AI models recognize",
        "impact_estimate": "Could improve entity score by ~10 points + brand authority",
        "category": "entity",
    },

    # ── AI Visibility — Web Presence ──────────────────────────────────────
    "not_in_google_ai": {
        "pillar": "ai_visibility",
        "priority": "high",
        "title": "Get Into Google AI Overviews",
        "description": "Your brand doesn't appear in Google's AI Overview (SGE) results. Google AI Overviews are shown above traditional search results and are the first thing users see.",
        "action": "STEP 1 — Optimize for featured snippets (Google AI pulls from these):\n"
                  "• Structure content as Q&A — ask a question in an H2, answer immediately below\n"
                  "• Use tables, lists, and concise definitions\n"
                  "• Add FAQ schema markup\n\n"
                  "STEP 2 — Target 'People Also Ask' queries:\n"
                  "• Search your main keywords on Google\n"
                  "• Note the 'People Also Ask' questions\n"
                  "• Create content that directly answers each one\n\n"
                  "STEP 3 — Build topical authority:\n"
                  "• Create a content cluster: 1 pillar page + 5-10 supporting articles\n"
                  "• Interlink all pages in the cluster\n"
                  "• Cover the topic comprehensively\n\n"
                  "PRO TIP: Google AI Overviews heavily favor content from sites with strong E-E-A-T signals. Fix your E-E-A-T issues first.",
        "impact_estimate": "Could improve AI visibility score by ~10 points",
        "category": "ai_visibility",
    },
    "no_reddit_ai_presence": {
        "pillar": "ai_visibility",
        "priority": "high",
        "title": "Build Reddit Presence for AI Discovery",
        "description": "Your brand has no presence on Reddit. AI models (ChatGPT, Perplexity, Gemini) heavily index Reddit discussions. Brands mentioned positively on Reddit get cited in AI responses.",
        "action": "STEP 1 — Find your subreddits:\n"
                  "• Search reddit.com for your industry keywords\n"
                  "• Join 3-5 relevant subreddits\n"
                  "• Lurk for 1 week to understand the culture\n\n"
                  "STEP 2 — Start contributing value:\n"
                  "• Answer questions where your expertise is relevant\n"
                  "• Share insights without being promotional\n"
                  "• Build karma and reputation\n\n"
                  "STEP 3 — Create a showcase post:\n"
                  "• Title: 'How we solved [problem] — lessons learned'\n"
                  "• Share genuine data and results\n"
                  "• Engage with every comment\n\n"
                  "STEP 4 — Maintain presence:\n"
                  "• Comment on relevant threads weekly\n"
                  "• Share industry insights monthly\n"
                  "• AI models re-index Reddit frequently\n\n"
                  "PRO TIP: A single popular Reddit thread mentioning your brand can appear in AI responses for months.",
        "impact_estimate": "Could improve AI visibility score by ~10 points",
        "category": "ai_visibility",
    },
    "no_medium_ai_presence": {
        "pillar": "ai_visibility",
        "priority": "high",
        "title": "Publish on Medium for AI Citations",
        "description": "Your brand has no Medium presence. Medium articles are heavily indexed by AI models and frequently cited in AI-generated responses. This is one of the fastest ways to get into AI search results.",
        "action": "STEP 1 — Create your Medium profile:\n"
                  "• Use your real name + company credentials\n"
                  "• Write a bio that establishes expertise\n\n"
                  "STEP 2 — Publish your first article:\n"
                  "• Title: '[Year] Guide to [Your Topic]'\n"
                  "• Include statistics, citations, and expert insights\n"
                  "• Mention your brand naturally 2-3 times\n"
                  "• Add 5+ relevant tags\n\n"
                  "STEP 3 — Submit to a publication:\n"
                  "• 'The Startup', 'Better Programming', 'Towards Data Science'\n"
                  "• Publications multiply reach 5-10x\n\n"
                  "STEP 4 — Cross-link for maximum AI impact:\n"
                  "• Link Medium article → your website\n"
                  "• Link your website → Medium article\n"
                  "• Add Medium link to your social profiles\n\n"
                  "PRO TIP: Medium articles rank on Google within days, and AI models index them within 2-4 weeks.",
        "impact_estimate": "Could improve AI visibility score by ~10 points",
        "category": "ai_visibility",
    },
    "weak_brand_site": {
        "pillar": "ai_visibility",
        "priority": "medium",
        "title": "Strengthen Your Brand Website Signals",
        "description": "Your website is missing key pages that AI models expect from a credible brand (About, Contact, Blog, Social links). These are trust signals that determine whether AI cites your content.",
        "action": "STEP 1 — Add essential pages (if missing):\n"
                  "• /about — Who you are, your mission, your team\n"
                  "• /contact — Email, phone, address, contact form\n"
                  "• /blog — Regular content shows you're active and authoritative\n\n"
                  "STEP 2 — Add footer links:\n"
                  "• Social media profiles (LinkedIn, Twitter, GitHub)\n"
                  "• Privacy policy and terms of service\n"
                  "• About, Contact, Blog links\n\n"
                  "STEP 3 — Ensure content depth:\n"
                  "• Homepage should have 800+ words of meaningful content\n"
                  "• Explain what you do, who you serve, and why you're different\n"
                  "• Include testimonials or case studies\n\n"
                  "PRO TIP: AI models check for these pages to verify you're a legitimate entity. Missing them = lower trust = fewer citations.",
        "impact_estimate": "Could improve AI visibility score by ~5 points",
        "category": "ai_visibility",
    },

    # ── Crawl failure findings ────────────────────────────────────────────
    "crawl_blocked_403": {
        "pillar": "technical",
        "priority": "critical",
        "title": "Your Site Blocks Automated Access (HTTP 403)",
        "description": "Your server returned a 403 Forbidden error, which means it blocks automated requests. If your site blocks our crawler, it likely blocks AI crawlers (GPTBot, ClaudeBot, PerplexityBot) too — meaning AI search engines cannot index your content.",
        "action": "Check your server configuration, CDN (Cloudflare, AWS WAF), or hosting provider settings. Ensure legitimate bots are allowed. Add specific allow rules for AI crawlers in your firewall/WAF settings.",
        "impact_estimate": "Critical — AI engines cannot see your content at all",
        "category": "technical",
    },
    "crawl_timeout": {
        "pillar": "technical",
        "priority": "critical",
        "title": "Your Site Is Too Slow to Crawl",
        "description": "Your page took too long to respond (>15 seconds). AI crawlers have strict timeouts — if your site is this slow, AI search engines will skip it entirely.",
        "action": "Investigate server performance: check hosting plan, enable caching, optimize database queries, use a CDN. Aim for <3 second response time.",
        "impact_estimate": "Critical — slow sites get skipped by AI crawlers",
        "category": "technical",
    },
}

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Numeric impact scores for smart ranking (higher = more impactful)
# Based on Princeton GEO research effectiveness data + pillar weight
IMPACT_SCORES = {
    # Content — Princeton research ranked methods
    "no_citations": 95,         # +40% visibility — highest impact
    "no_statistics": 90,        # +37% visibility
    "keyword_stuffing": 88,     # -10% penalty — actively hurts
    "no_expert_quotes": 75,     # +30% visibility
    "weak_authoritative_tone": 70,  # +25% visibility
    "poor_readability": 60,     # +20% visibility
    "no_technical_terms": 50,   # +18% visibility
    "low_vocabulary_diversity": 45,  # +15% visibility
    "no_faq_section": 72,       # FAQ + schema = +40% AI visibility
    "no_answer_first": 65,      # Direct answers get cited more
    "low_word_count": 55,       # Thin content rarely cited
    "no_h1": 40,
    "multiple_h1": 20,
    "broken_heading_hierarchy": 25,
    "no_lists": 15,
    "poor_paragraph_structure": 15,
    "few_internal_links": 15,
    # Schema
    "no_jsonld": 85,            # No schema at all — critical
    "no_faqpage_schema": 70,    # +40% AI visibility per research
    "no_article_schema": 55,
    "no_organization_schema": 55,
    "invalid_jsonld_structure": 40,
    "incomplete_article_schema": 30,
    "incomplete_organization_schema": 30,
    "incomplete_faqpage_schema": 30,
    "incomplete_product_schema": 20,
    "incomplete_blogposting_schema": 25,
    "incomplete_newsarticle_schema": 25,
    "incomplete_howto_schema": 20,
    # E-E-A-T (boosted — actionable pillar)
    "no_citations_eeat": 88,    # Overlaps with content citations
    "few_external_citations": 82,
    "no_trust_links": 78,
    "no_first_hand_experience": 76,
    "low_authority": 74,
    "low_trust_signals": 74,
    "no_author": 72,
    "no_about_page": 65,
    "no_author_bio": 55,
    "no_expertise_indicators": 60,
    "low_source_diversity": 45,
    "no_publish_date": 35,
    "no_updated_date": 25,
    # Technical (boosted — actionable pillar, instant fixes)
    "ai_bots_blocked": 97,      # Blocking AI = zero visibility
    "meta_noindex": 95,         # Blocking indexing = zero visibility
    "no_llms_txt": 80,          # Quick win — create a file
    "no_https": 70,
    "slow_load_time": 60,
    "no_sitemap": 50,
    "no_viewport": 35,
    "no_canonical": 35,
    # Entity (boosted — actionable pillar)
    "brand_not_in_ai": 78,
    "no_wikipedia_presence": 62,
    "no_social_profiles": 40,
    # Community presence (entity sub-actions)
    "no_reddit_presence": 68,   # Reddit indexed by AI heavily
    "no_medium_presence": 64,   # Medium = brand authority
    # AI Visibility — Web Presence
    "not_in_google_ai": 82,     # Google AI Overview is #1 discovery
    "no_reddit_ai_presence": 72, # Reddit feeds AI responses
    "no_medium_ai_presence": 66, # Medium = AI-indexed authority
    "weak_brand_site": 48,       # Brand site quality matters
    # Crawl failures
    "crawl_blocked_403": 98,    # Can't be indexed at all
    "crawl_timeout": 96,        # Too slow for any crawler
}

MAX_RECOMMENDATIONS = 10


def generate_recommendations(pillar_details: dict[str, dict]) -> list[dict]:
    """
    Generate top 5-7 highest-impact recommendations.

    Uses numeric impact scores based on Princeton GEO research effectiveness
    data to rank and select only the most impactful improvements.
    """
    candidates = []

    for _pillar_name, details in pillar_details.items():
        findings = details.get("findings", [])
        for finding in findings:
            rule = RECOMMENDATION_RULES.get(finding)
            if rule:
                rec = dict(rule)
                rec["impact_score"] = IMPACT_SCORES.get(finding, 10)
                candidates.append(rec)

    # Sort by impact score (highest first), then by priority as tiebreaker
    candidates.sort(
        key=lambda r: (-r["impact_score"], PRIORITY_ORDER.get(r["priority"], 99))
    )

    # Take top N recommendations
    top = candidates[:MAX_RECOMMENDATIONS]

    # Remove internal impact_score before returning
    for rec in top:
        rec.pop("impact_score", None)

    return top
