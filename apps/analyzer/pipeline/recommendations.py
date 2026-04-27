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
    "crawl_failed": {
        "pillar": "technical",
        "priority": "critical",
        "title": "Fix Page Accessibility — Crawler Blocked",
        "description": "The crawler could not access your page. This means AI models (ChatGPT, Gemini, Perplexity) also cannot read your content. Your technical score is severely capped until this is resolved.",
        "action": "STEP 1 — Check if your page returns a 200 OK status code.\nSTEP 2 — Ensure your server doesn't block bot user agents.\nSTEP 3 — Check for Cloudflare/firewall bot protection that may be blocking crawlers.\nSTEP 4 — Verify robots.txt isn't blocking the page path.\nSTEP 5 — Test with: curl -A 'Mozilla/5.0' your-url",
        "impact_estimate": "Critical — your page is invisible to AI. Fixing this could improve your score by ~50+ points",
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
                  "STEP 4 — Post on Reddit (see other recommendations)\n\n"
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

    # ── Community Presence (Reddit) ───────────────────────────────────────
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

    # ── D2C / E-Commerce Specific ─────────────────────────────────────────

    "no_product_schema": {
        "pillar": "schema",
        "priority": "critical",
        "title": "Add Product Schema with Price & Availability",
        "description": "No Product schema found on what appears to be a product page. AI shopping assistants (ChatGPT, Gemini, Perplexity) rely on Product schema to recommend products. Without it, your products are invisible to AI-powered shopping.",
        "action": 'Add Product schema with name, description, image, price, availability, brand, and reviews. Example:\n{"@type":"Product","name":"Your Product","description":"...","image":"...","offers":{"@type":"Offer","price":"29.99","priceCurrency":"GBP","availability":"https://schema.org/InStock"},"brand":{"@type":"Brand","name":"Your Brand"},"aggregateRating":{"@type":"AggregateRating","ratingValue":"4.8","reviewCount":"124"}}',
        "impact_estimate": "Could improve your score by ~20 points — critical for D2C brands",
        "category": "schema",
    },
    "no_review_schema": {
        "pillar": "schema",
        "priority": "high",
        "title": "Add Review / Rating Schema",
        "description": "No AggregateRating or Review schema found. AI assistants prioritize products with visible ratings. '4.8 stars from 200+ reviews' is exactly the kind of data AI cites when recommending products.",
        "action": 'Add AggregateRating to your Product schema:\n"aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.8", "bestRating": "5", "reviewCount": "203"}\nAlso add individual Review entries for your best reviews.',
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "schema",
    },
    "no_breadcrumb_schema": {
        "pillar": "schema",
        "priority": "medium",
        "title": "Add BreadcrumbList Schema",
        "description": "No breadcrumb schema found. Breadcrumbs help AI understand your site hierarchy — Home > Category > Product. This improves how AI navigates and recommends pages from your site.",
        "action": 'Add BreadcrumbList schema:\n{"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Home","item":"https://yoursite.com"},{"@type":"ListItem","position":2,"name":"Category","item":"https://yoursite.com/category"},{"@type":"ListItem","position":3,"name":"Product Name"}]}',
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "schema",
    },
    "no_shipping_info": {
        "pillar": "content",
        "priority": "high",
        "title": "Add Shipping & Delivery Information",
        "description": "No shipping or delivery details found. AI shopping assistants always check for shipping info before recommending products. 'Free shipping over £50' or '2-day delivery' are key signals.",
        "action": "Add a visible shipping section on your product pages and a dedicated /shipping page. Include: delivery timeframes, free shipping thresholds, international availability, and return window.",
        "impact_estimate": "Could improve your score by ~8 points — AI assistants cite shipping details",
        "category": "content",
    },
    "no_returns_policy": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Add Returns & Refund Policy",
        "description": "No returns/refund policy found. AI engines check for return policies as a trust signal before recommending D2C brands. A clear returns policy = higher trust = more AI citations.",
        "action": "Create a /returns or /refund-policy page. Include: return window (e.g., 30 days), conditions, refund process, and contact method. Link it from your footer and product pages.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "eeat",
    },
    "no_customer_reviews": {
        "pillar": "eeat",
        "priority": "critical",
        "title": "Add Customer Reviews / Testimonials",
        "description": "No customer reviews or testimonials detected. AI models heavily weight social proof when recommending D2C brands. Pages with real customer reviews are cited 3x more by AI assistants.",
        "action": "STEP 1 — Add 5-10 real customer reviews to your page with name, date, and star rating.\nSTEP 2 — Use a reviews widget (Judge.me, Trustpilot, Yotpo) or add them manually.\nSTEP 3 — Add Review schema markup for each review.\nSTEP 4 — Include specific details: 'This moisturizer cleared my acne in 2 weeks' is better than 'Great product!'",
        "impact_estimate": "Could improve your score by ~15 points — strongest D2C trust signal",
        "category": "eeat",
    },
    "no_brand_story": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Add Brand Story / Origin Content",
        "description": "No brand story or 'About Us' narrative found. AI engines distinguish brands by their story. 'Founded in 2020 by two dermatologists who...' creates a memorable identity that AI cites.",
        "action": "Add a compelling About section with: founding story, mission, team credentials, what makes you different, and specific numbers ('helped 50,000+ customers').",
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "eeat",
    },
    "no_comparison_content": {
        "pillar": "content",
        "priority": "high",
        "title": "Create 'Brand vs Competitor' Comparison Content",
        "description": "No comparison content found. AI assistants heavily cite comparison pages when users ask 'What's the best X?' or 'Brand A vs Brand B'. D2C brands with comparison content appear in AI results 40% more.",
        "action": "STEP 1 — Create a comparison page: '[Your Brand] vs [Top Competitor]'.\nSTEP 2 — Include a feature comparison table with checkmarks.\nSTEP 3 — Be honest — acknowledge competitor strengths while highlighting your advantages.\nSTEP 4 — Add specific data: pricing, ingredients, ratings, delivery speed.",
        "impact_estimate": "Could improve your AI visibility by ~12 points",
        "category": "content",
    },
    "no_ingredients_list": {
        "pillar": "content",
        "priority": "medium",
        "title": "Add Full Ingredients / Materials List",
        "description": "No ingredients or materials list found. For beauty, food, supplements, and fashion D2C brands, AI assistants frequently cite ingredient lists when users ask 'Is X safe?' or 'What's in X?'",
        "action": "Add a complete ingredients/materials section on each product page. For food/beauty: list every ingredient. For fashion: list materials and sourcing. For tech: list specifications.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },
    "no_use_cases": {
        "pillar": "content",
        "priority": "high",
        "title": "Add Use Cases / 'Who Is This For?' Section",
        "description": "No use case content detected. AI assistants match products to users based on use cases. 'Best for sensitive skin' or 'Ideal for remote teams' helps AI recommend your product for the right queries.",
        "action": "Add a 'Who Is This For?' section listing 3-5 ideal customer profiles with specific scenarios. Use headings like 'Best for [use case]' — these map directly to AI search queries.",
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "content",
    },
    "no_how_to_use": {
        "pillar": "content",
        "priority": "medium",
        "title": "Add 'How to Use' Guide",
        "description": "No usage instructions found. AI models cite 'how to use' content when users ask about product application. This is especially important for beauty, supplements, and tech products.",
        "action": "Add a step-by-step 'How to Use' section with clear instructions, dosage (if applicable), and tips. Add HowTo schema markup for extra AI visibility.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },
    "no_pricing_transparency": {
        "pillar": "content",
        "priority": "high",
        "title": "Make Pricing Visible and Transparent",
        "description": "Product pricing not clearly visible. AI shopping assistants need clear pricing to recommend products. Pages where price is hidden behind logins or 'contact us' get deprioritized.",
        "action": "Display prices clearly on all product pages. Include: base price, any subscription discounts, bundle pricing, and currency. Add price to your Product schema's offers.price field.",
        "impact_estimate": "Could improve your score by ~8 points — AI assistants always cite price",
        "category": "content",
    },
    "no_trust_badges": {
        "pillar": "eeat",
        "priority": "medium",
        "title": "Add Trust Badges & Certifications",
        "description": "No trust badges, certifications, or security indicators found. AI engines look for trust signals: 'FDA approved', 'Certified B Corp', 'SSL secured', 'Money-back guarantee'. These boost AI confidence.",
        "action": "Add visible trust badges: payment security (Visa/Mastercard/PayPal), certifications (organic, cruelty-free, FDA), guarantees (30-day money-back), and awards. Place them near the buy button and in the footer.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "eeat",
    },
    "no_social_proof_numbers": {
        "pillar": "content",
        "priority": "high",
        "title": "Add Social Proof Numbers",
        "description": "No social proof metrics found. AI assistants love specific numbers: '50,000+ happy customers', 'Rated 4.9/5 on Trustpilot', 'Featured in Vogue and GQ'. These are exactly what AI cites.",
        "action": "Add prominently: customer count, review score, press mentions, social followers, and units sold. Use specific numbers, not vague claims. Place them above the fold.",
        "impact_estimate": "Could improve your score by ~10 points",
        "category": "content",
    },
    "no_video_content": {
        "pillar": "content",
        "priority": "medium",
        "title": "Add Video Content or Demo",
        "description": "No video content detected. Product demos and explainer videos increase time on page and provide rich content that AI can reference. Google's AI Overview frequently surfaces pages with video.",
        "action": "Add a product demo video, unboxing, or tutorial. Embed from YouTube (AI indexes YouTube content). Add VideoObject schema markup with name, description, and thumbnailUrl.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },
    "no_sustainability_info": {
        "pillar": "eeat",
        "priority": "medium",
        "title": "Add Sustainability / Ethical Sourcing Info",
        "description": "No sustainability or ethical sourcing content found. AI assistants increasingly answer queries like 'Is X brand sustainable?' or 'eco-friendly alternatives to...'. D2C brands with sustainability content get cited in these responses.",
        "action": "Add a sustainability section or page covering: materials sourcing, manufacturing practices, packaging, carbon footprint initiatives, and certifications. Be specific with data.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "eeat",
    },
    "no_bundle_offers": {
        "pillar": "content",
        "priority": "medium",
        "title": "Add Bundle / Subscription Options",
        "description": "No bundle deals or subscription options visible. AI shopping assistants often recommend bundles and subscriptions when users ask for 'best value' or 'cheapest way to buy X'.",
        "action": "Create bundle pages showing savings: 'Buy 3, Save 20%'. If applicable, offer subscriptions: 'Subscribe & Save 15% — cancel anytime'. Make savings percentages prominent.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },
    "thin_product_description": {
        "pillar": "content",
        "priority": "critical",
        "title": "Expand Product Description (Min 300 Words)",
        "description": "Product description is too thin (<150 words). AI models cannot recommend products they don't understand. Short descriptions get skipped. Aim for 300-500 words covering benefits, features, use cases, and differentiators.",
        "action": "STEP 1 — Write a compelling opening that answers 'What is this and who is it for?'\nSTEP 2 — List 5+ benefits (not just features).\nSTEP 3 — Add a 'What makes this different?' section.\nSTEP 4 — Include specific data: dimensions, weight, ingredients, materials.\nSTEP 5 — End with a use case: 'Perfect for [scenario].'",
        "impact_estimate": "Could improve your score by ~15 points — thin descriptions = invisible to AI",
        "category": "content",
    },
    "no_structured_specs": {
        "pillar": "content",
        "priority": "medium",
        "title": "Add Structured Product Specifications",
        "description": "No structured specs table found. AI assistants pull specifications when answering 'What are the specs of X?' or comparison queries. A clean specs table is highly citable.",
        "action": "Add a specifications table with: dimensions, weight, materials, color options, warranty, compatibility. Use an HTML <table> or definition list (<dl>) for structured data.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "content",
    },
    "no_local_business_schema": {
        "pillar": "schema",
        "priority": "medium",
        "title": "Add LocalBusiness Schema (If Applicable)",
        "description": "No LocalBusiness schema found. If you have a physical location, showroom, or warehouse, adding LocalBusiness schema helps AI map your brand to location-based queries.",
        "action": 'Add LocalBusiness schema:\n{"@type":"LocalBusiness","name":"Your Brand","address":{"@type":"PostalAddress","streetAddress":"...","addressLocality":"London","postalCode":"...","addressCountry":"GB"},"telephone":"+44...","openingHours":"Mo-Fr 09:00-17:00"}',
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "schema",
    },
    "no_meta_description": {
        "pillar": "technical",
        "priority": "high",
        "title": "Add a Compelling Meta Description",
        "description": "No meta description found. AI engines use meta descriptions as a summary when deciding whether to cite your page. A missing meta description means AI generates its own (often poorly).",
        "action": 'Add a unique meta description (150-160 chars) that summarizes the page value:\n<meta name="description" content="Award-winning organic skincare loved by 50,000+ customers. Free shipping over £30. Shop our dermatologist-approved range.">\nInclude: key benefit, social proof number, and call to action.',
        "impact_estimate": "Could improve your score by ~8 points",
        "category": "technical",
    },
    "no_og_tags": {
        "pillar": "technical",
        "priority": "medium",
        "title": "Add Open Graph Tags for Social Sharing",
        "description": "No Open Graph (og:) tags found. When your pages are shared on social media or referenced by AI, og:title, og:description, and og:image provide the preview. Missing OG tags = ugly previews = fewer clicks.",
        "action": 'Add to your <head>:\n<meta property="og:title" content="Your Page Title">\n<meta property="og:description" content="Your page summary">\n<meta property="og:image" content="https://yoursite.com/image.jpg">\n<meta property="og:type" content="product">',
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "technical",
    },
    "no_contact_page": {
        "pillar": "eeat",
        "priority": "high",
        "title": "Add Contact Information",
        "description": "No contact page or contact information detected. AI engines verify brand legitimacy by checking for contact info. A brand without visible contact details appears less trustworthy.",
        "action": "Add a /contact page with: email address, phone number (if applicable), physical address, contact form, and response time expectation. Link from the footer on every page.",
        "impact_estimate": "Could improve your score by ~5 points",
        "category": "eeat",
    },
    "no_privacy_policy": {
        "pillar": "eeat",
        "priority": "medium",
        "title": "Add Privacy Policy Page",
        "description": "No privacy policy found. AI trust signals include legal compliance pages. A missing privacy policy is a red flag for AI systems evaluating brand trustworthiness.",
        "action": "Create a /privacy-policy page covering: data collection, usage, cookies, third parties, and user rights. Link from footer. Use a privacy policy generator if needed.",
        "impact_estimate": "Could improve your score by ~3 points",
        "category": "eeat",
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
    "crawl_failed": 100,        # Page inaccessible = everything fails
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
    # AI Visibility — Web Presence
    "not_in_google_ai": 82,     # Google AI Overview is #1 discovery
    "no_reddit_ai_presence": 72, # Reddit feeds AI responses
    "weak_brand_site": 48,       # Brand site quality matters
    # Crawl failures
    "crawl_blocked_403": 98,    # Can't be indexed at all
    "crawl_timeout": 96,        # Too slow for any crawler
    # D2C / E-Commerce specific
    "no_product_schema": 88,    # Critical for shopping AI
    "no_review_schema": 72,     # Reviews = trust signal
    "no_breadcrumb_schema": 30, # Site hierarchy
    "no_shipping_info": 68,     # AI cites shipping details
    "no_returns_policy": 55,    # Trust signal
    "no_customer_reviews": 85,  # Strongest D2C trust signal
    "no_brand_story": 60,       # Brand identity
    "no_comparison_content": 78, # AI loves comparisons
    "no_ingredients_list": 35,  # Niche but important
    "no_use_cases": 65,         # Maps to AI queries
    "no_how_to_use": 40,        # Usage content
    "no_pricing_transparency": 70, # AI needs prices
    "no_trust_badges": 42,      # Certifications
    "no_social_proof_numbers": 75, # AI cites numbers
    "no_video_content": 35,     # Rich content
    "no_sustainability_info": 30, # Growing trend
    "no_bundle_offers": 32,     # Value queries
    "thin_product_description": 82, # Thin = invisible
    "no_structured_specs": 38,  # Specification queries
    "no_local_business_schema": 25, # Local queries
    "no_meta_description": 65,  # AI summary source
    "no_og_tags": 28,           # Social sharing
    "no_contact_page": 55,      # Trust signal
    "no_privacy_policy": 20,    # Legal compliance
}

MAX_RECOMMENDATIONS = 12
MAX_PER_PILLAR = 3

# Off-page findings — filtered out, only on-page + technical shown
OFFPAGE_FINDINGS = {
    "brand_not_in_ai", "no_reddit_ai_presence", "no_reddit_presence", "no_social_profiles",
    "no_wikipedia_presence", "not_in_google_ai", "weak_brand_site",
}
OFFPAGE_PILLARS = {"entity", "ai_visibility"}

# Findings that require manual action — cannot be auto-fixed by plugin/app
MANUAL_FINDINGS = {
    "no_sitemap", "no_https", "slow_load_time", "crawl_failed",
    "crawl_blocked_403", "crawl_timeout",
    "brand_not_in_ai", "no_wikipedia_presence", "no_social_profiles",
    "no_reddit_presence",
    "not_in_google_ai", "no_reddit_ai_presence",
    "weak_brand_site",
}

# Why each pillar matters for AI visibility
PILLAR_WHY = {
    "content": "AI models extract and cite well-structured content with citations and data",
    "schema": "Structured data helps AI understand your page context and extract answers",
    "eeat": "AI systems prioritize content from trusted, authoritative sources",
    "technical": "AI crawlers must be able to access and parse your site efficiently",
}

# Suppress recs for pillars scoring above this threshold
# Set high so we always push toward 100 (especially technical)
SUPPRESS_THRESHOLD = 95

# ── Gamified Steps & Metadata ─────────────────────────────────────────────
# Each finding maps to structured steps, XP, difficulty, and time estimate.
# Steps are rendered as a gamified checklist on the frontend.

STEP_META = {
    # ── Content ──
    "no_h1": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 2,
        "steps": [
            {"n": 1, "title": "Open your page editor", "detail": "Find the page you want to fix.", "xp": 5,
             "shopify": {"detail": "Shopify Admin → Online Store → Pages → click on the page you want to edit."},
             "wordpress": {"detail": "WordPress Admin → Pages → click on the page you want to edit."}},
            {"n": 2, "title": "Check the page title", "detail": "Your page title automatically becomes the main heading (H1). Make sure it clearly describes the page topic.", "xp": 15,
             "shopify": {"detail": "The 'Title' field at the top of the page editor is your H1. Make it clear and descriptive. Example: 'About Our Brand' or 'Organic Skincare Collection'. Click Save."},
             "wordpress": {"detail": "The title at the top of the editor is your H1. Click on it and make sure it clearly describes the page. Click Update."}},
            {"n": 3, "title": "Verify it works", "detail": "Visit your page in a browser. The main title should be large and clear at the top.", "xp": 10},
        ],
    },
    "multiple_h1": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Open your page editor", "detail": "Find the page with multiple main headings.", "xp": 5,
             "shopify": {"detail": "Shopify Admin → Online Store → Pages → click on the page."},
             "wordpress": {"detail": "WordPress Admin → Pages → click on the page."}},
            {"n": 2, "title": "Change extra headings to smaller size", "detail": "Your page should have only ONE main heading (the title). Any other large headings in the content should be changed to 'Heading 2' or 'Heading 3'.", "xp": 10,
             "shopify": {"detail": "In the content editor, select any heading that's too large → click the formatting dropdown → change from 'Heading 1' to 'Heading 2'. Only the page Title should be H1."},
             "wordpress": {"detail": "In the Block Editor, click on each Heading block → in the toolbar, change 'H1' to 'H2' or 'H3'. The page title is already your H1."}},
            {"n": 3, "title": "Save and verify", "detail": "Save the page. Visit it and confirm only the page title is the largest heading.", "xp": 10},
        ],
    },
    "broken_heading_hierarchy": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Review your heading sizes", "detail": "Read through your page content. Headings should go in order: Title (biggest) → Section headings (medium) → Sub-sections (smaller). Don't skip sizes.", "xp": 10},
            {"n": 2, "title": "Fix the order", "detail": "Make sure your headings follow a logical flow. Think of it like a book: Title → Chapter → Sub-chapter.", "xp": 15,
             "shopify": {"detail": "In the page editor, click each heading → use the formatting dropdown to set the right level. After the Title: use 'Heading 2' for main sections, 'Heading 3' for sub-sections."},
             "wordpress": {"detail": "Click each Heading block → change the level in the toolbar. H2 for main sections, H3 for sub-sections. Never skip from H2 to H4."}},
            {"n": 3, "title": "Save and check", "detail": "Save the page. The headings should create a clear outline of your content.", "xp": 5},
        ],
    },
    "no_faq_section": {
        "xp_reward": 60, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {"n": 1, "title": "Write 5-8 common questions", "detail": "Use the FAQ prompts in this recommendation, your support inbox, and reviews. Ask in Chat for a tailored Q&A list—no manual keyword research needed.", "xp": 10},
            {"n": 2, "title": "Write clear answers", "detail": "Each answer should be 2-4 sentences. Start with a direct answer, then add details. Use specific facts and numbers.", "xp": 15},
            {"n": 3, "title": "Add the FAQ to your page", "detail": "Add a FAQ section at the bottom of your page.", "xp": 15,
             "shopify": {"detail": "Option A (easiest): Shopify Admin → Online Store → Themes → Customize → select the page → click 'Add section' → choose 'Collapsible content' → add your Q&As as rows → Save.\n\nOption B: Go to Online Store → Pages → edit the page → in the text editor, type each question as a bold heading and the answer below it → Save."},
             "wordpress": {"detail": "Edit the page → click '+' → search 'FAQ' or 'Accordion' → add the block → type your questions and answers → Update.\n\nOr simply type each question as a Heading (H3) with the answer as a Paragraph below it."}},
            {"n": 4, "title": "Verify it's visible", "detail": "Visit your page. Scroll to the bottom — your FAQ should be visible and readable. AI models love FAQ content because it's easy to extract.", "xp": 20},
        ],
    },
    "no_lists": {
        "xp_reward": 20, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Find content that could be a list", "detail": "Look for paragraphs that describe features, benefits, steps, or comparisons. These work better as bullet points.", "xp": 5},
            {"n": 2, "title": "Convert to bullet points", "detail": "Select the text and click the bullet list icon in your editor toolbar.", "xp": 15,
             "shopify": {"detail": "In the page/product editor, select the text → click the bullet list icon (•) in the toolbar. For numbered lists, click the numbered list icon (1.). Save when done."},
             "wordpress": {"detail": "Select the text → click the List block icon in the toolbar. Or add a new List block with '+' → type each item on a new line."}},
        ],
    },
    "no_answer_first": {
        "xp_reward": 40, "difficulty": "medium", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "What question does your page answer?", "detail": "Think about what someone searching for this page wants to know. Write that question down.", "xp": 10},
            {"n": 2, "title": "Write a direct 1-2 sentence answer", "detail": "Start with the answer, not background. Example: 'Organic skincare uses plant-based ingredients without synthetic chemicals.' NOT 'In today's world, many people are wondering about...'", "xp": 15},
            {"n": 3, "title": "Put the answer at the very top", "detail": "Open your page editor. Move your direct answer to the first paragraph, before any other text. AI models extract the first sentences as the answer.", "xp": 15,
             "shopify": {"detail": "Shopify Admin → Online Store → Pages → edit the page → cut your answer text → paste it at the very top of the content area, before everything else → Save."},
             "wordpress": {"detail": "Edit the page → drag the answer paragraph to the top, or cut/paste it as the first block → Update."}},
        ],
    },
    "few_internal_links": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find 3-5 related pages on your site", "detail": "Think of other pages that relate to this one. Products, blog posts, category pages, or your About page.", "xp": 5},
            {"n": 2, "title": "Add links naturally in your text", "detail": "Select a relevant word or phrase in your content, click the link icon in the toolbar, and paste the URL.", "xp": 15,
             "shopify": {"detail": "In the page editor, highlight a word → click the link icon (chain icon) → paste the URL of another page on your store → Save. Add 3-5 links throughout."},
             "wordpress": {"detail": "In the Block Editor, highlight text → click the link icon → search for or paste the URL of another page → Update."}},
            {"n": 3, "title": "Use descriptive text for links", "detail": "Good: 'See our organic skincare collection'. Bad: 'Click here'. Descriptive links help AI understand what the linked page is about.", "xp": 5},
        ],
    },
    "no_citations": {
        "xp_reward": 80, "difficulty": "medium", "estimated_minutes": 25,
        "steps": [
            {"n": 1, "title": "Start from Signalor's suggestions", "detail": "Use the citation ideas, example wording, and source links in this recommendation (or tap Ask in Chat for tailored sources). You do not need to search the web yourself.", "xp": 15},
            {"n": 2, "title": "Weave citations into your page", "detail": "Place the suggested attributions next to the claims they support—Signalor's examples show the pattern; adjust names and links only if your editor requires it.", "xp": 25,
             "shopify": {"detail": "In the page editor, add the suggested 'According to…' lines next to each claim → link the source name to the URL Signalor provided → Save."},
             "wordpress": {"detail": "In the block editor, add the suggested citation lines beside each claim → link source titles to the URLs from this recommendation → Update."}},
            {"n": 3, "title": "Add a Sources section", "detail": "At the end of the page, add a 'Sources' heading and type or insert the bullet list from this recommendation (or ask Chat to regenerate the list for you).", "xp": 20},
            {"n": 4, "title": "Keep every source clickable", "detail": "Each entry in Sources should link to the original page or study. Use the URLs from this card so readers and AI can follow the evidence.", "xp": 20},
        ],
    },
    "no_statistics": {
        "xp_reward": 70, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {"n": 1, "title": "Find vague claims in your content", "detail": "Look for phrases like 'many customers', 'most people', 'a lot of'. These need specific numbers.", "xp": 10},
            {"n": 2, "title": "Use data Signalor suggests", "detail": "Take the statistics and source lines from this recommendation or Ask in Chat—pick numbers that match your claims and cite the source name we provide.", "xp": 20},
            {"n": 3, "title": "Replace vague with specific", "detail": "Change 'many people prefer organic' to '73% of consumers prefer organic products (Nielsen, 2024)'. Always mention the source.", "xp": 25,
             "shopify": {"detail": "Edit your page content → find vague statements → replace with specific numbers and sources → Save."},
             "wordpress": {"detail": "Edit your page → replace vague claims with data points → Update."}},
            {"n": 4, "title": "Always cite your statistics", "detail": "Every number should include who reported it: '(Gartner, 2024)' or link to the report.", "xp": 15},
        ],
    },
    "no_expert_quotes": {
        "xp_reward": 50, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Find experts to quote", "detail": "Think of recognized people in your industry — CEOs, researchers, authors, doctors. You can also quote your own team members if they have credentials.", "xp": 10},
            {"n": 2, "title": "Use provided or team quotes", "detail": "Use the example quotes and attributions from this recommendation, or Ask in Chat for options. Your own experts can approve a short quote—no outside research required.", "xp": 15},
            {"n": 3, "title": "Add quotes to your page", "detail": "Add 2-3 expert quotes with the person's name and title.", "xp": 25,
             "shopify": {"detail": "Edit your page → type the quote in quotation marks → on the next line, write the person's name and title. Example:\n\n\"Natural ingredients are the future of skincare.\"\n— Dr. Sarah Chen, Dermatologist at Stanford Medical\n\nSave when done."},
             "wordpress": {"detail": "Edit the page → click '+' → search 'Quote' → add the Quote block → type the quote and attribution → Update."}},
        ],
    },
    "weak_authoritative_tone": {
        "xp_reward": 45, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Find weak language", "detail": "Read your content and highlight words like 'might', 'maybe', 'I think', 'possibly', 'could be', 'some people say'.", "xp": 10},
            {"n": 2, "title": "Rewrite with confidence", "detail": "Change weak phrases to strong ones. 'This might help your skin' → 'This visibly improves skin texture within 2 weeks'. Back every claim with evidence.", "xp": 20,
             "shopify": {"detail": "Edit your page → find and replace weak language with confident, evidence-backed statements → Save."},
             "wordpress": {"detail": "Edit your page → rewrite hedging language with confident statements → Update."}},
            {"n": 3, "title": "Add proof after every claim", "detail": "Every strong statement should have a data point, citation, or example right after it.", "xp": 15},
        ],
    },
    "poor_readability": {
        "xp_reward": 40, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {"n": 1, "title": "Spot hard sentences in your editor", "detail": "In your CMS, skim for sentences longer than about 25 words or dense jargon—Signalor's summary above usually flags the worst spots; Ask in Chat for rewritten alternatives.", "xp": 5},
            {"n": 2, "title": "Shorten long sentences", "detail": "Break any sentence over 25 words into two shorter ones. Aim for 15-20 words per sentence.", "xp": 15},
            {"n": 3, "title": "Simplify complex words", "detail": "Replace jargon with plain language. If you must use a technical term, explain it: 'Retinol (a form of Vitamin A) helps reduce wrinkles.'", "xp": 10},
            {"n": 4, "title": "Use shorter paragraphs", "detail": "Each paragraph should be 2-3 sentences max, covering one idea. Add line breaks between paragraphs.", "xp": 10,
             "shopify": {"detail": "Edit your page → break long paragraphs by pressing Enter → keep each paragraph to 2-3 sentences → Save."},
             "wordpress": {"detail": "Edit your page → split long paragraphs into shorter ones → each paragraph = one idea → Update."}},
        ],
    },
    "no_technical_terms": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "List 5-10 industry terms", "detail": "Write down the professional vocabulary in your field. For skincare: 'hyaluronic acid', 'ceramides', 'SPF protection'. For tech: 'API', 'machine learning', 'encryption'.", "xp": 10},
            {"n": 2, "title": "Add them naturally to your content", "detail": "Weave these terms into your text. Define each on first use: 'Ceramides (natural fats that protect your skin barrier) are essential for moisture retention.'", "xp": 15,
             "shopify": {"detail": "Edit your page or product description → add industry terms with brief explanations → Save."},
             "wordpress": {"detail": "Edit your page → add technical terms with plain-language definitions → Update."}},
            {"n": 3, "title": "Balance expert and simple language", "detail": "Mix technical terms with everyday explanations. This signals expertise while staying accessible.", "xp": 5},
        ],
    },
    "low_vocabulary_diversity": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find words you repeat too often", "detail": "Read through your content. Notice if you use the same word more than 5 times. Common culprits: your brand name, 'product', 'best', 'quality'.", "xp": 5},
            {"n": 2, "title": "Replace with synonyms", "detail": "Swap repeated words with alternatives. 'product' → 'item' → 'solution' → 'offering'. 'best' → 'top-rated' → 'leading' → 'most effective'.", "xp": 15,
             "shopify": {"detail": "Edit your page → find overused words → replace some with synonyms → Save. Tip: read it aloud — if a word feels repetitive, swap it."},
             "wordpress": {"detail": "Edit your page → use find/replace (Ctrl+H) to identify and swap overused words → Update."}},
            {"n": 3, "title": "Vary sentence structure", "detail": "Mix short punchy sentences with longer explanatory ones. This keeps the content engaging.", "xp": 5},
        ],
    },
    "low_word_count": {
        "xp_reward": 60, "difficulty": "hard", "estimated_minutes": 45,
        "steps": [
            {"n": 1, "title": "Use the content gaps listed here", "detail": "Signalor already compared your page to strong patterns—use the subtopics and bullets in this finding (or Ask in Chat) as your outline; no competitor search needed.", "xp": 10},
            {"n": 2, "title": "Expand your content", "detail": "For each subtopic, write 2-3 paragraphs with examples, data, and explanations. Aim for 1,500+ words total.", "xp": 25,
             "shopify": {"detail": "Edit your page → add new sections with headings for each subtopic → write detailed content under each → Save."},
             "wordpress": {"detail": "Edit your page → add Heading blocks for new sections → write content under each → Update."}},
            {"n": 3, "title": "Add an FAQ section", "detail": "Add 5-8 frequently asked questions with 2-4 sentence answers. This is the easiest way to add valuable content.", "xp": 15},
            {"n": 4, "title": "Add a summary at the end", "detail": "End with a short summary that recaps the key takeaways from your page.", "xp": 10},
        ],
    },
    "poor_paragraph_structure": {
        "xp_reward": 20, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find long paragraphs", "detail": "Scan your content for any paragraph longer than 3-4 sentences. These need breaking up.", "xp": 5},
            {"n": 2, "title": "Split into smaller chunks", "detail": "Each paragraph should cover one idea only. Press Enter to create breaks between ideas.", "xp": 10,
             "shopify": {"detail": "Edit your page → find long text blocks → press Enter to split them → each chunk should be 2-3 sentences → Save."},
             "wordpress": {"detail": "Edit your page → click inside long paragraphs → press Enter to split them → Update."}},
            {"n": 3, "title": "Start each paragraph with the main point", "detail": "The first sentence of each paragraph should tell the reader what that paragraph is about.", "xp": 5},
        ],
    },
    "keyword_stuffing": {
        "xp_reward": 50, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Find the repeated keyword", "detail": "Read your content and notice which word or phrase appears way too often. It might be your product name, brand, or a keyword you're trying to rank for.", "xp": 5,
             "shopify": {"detail": "Shopify Admin → Online Store → Pages (or Products) → open the page → read through the content → count how many times the main keyword appears. More than 5-6 times on a short page is too many."},
             "wordpress": {"detail": "Edit your page → use Ctrl+F to search for the keyword → count occurrences. If using Yoast SEO, check the 'Keyphrase density' indicator."}},
            {"n": 2, "title": "Remove forced repetitions", "detail": "Keep the keyword in: the title, first paragraph, one heading, and 2-3 natural mentions. Delete all other forced uses.", "xp": 20,
             "shopify": {"detail": "In the editor, rewrite sentences where the keyword feels forced. Focus on describing benefits and features naturally instead of repeating the same word. Save after editing."},
             "wordpress": {"detail": "Click on paragraphs with excessive keywords → rewrite them naturally. Focus on value, not repetition. Update when done."}},
            {"n": 3, "title": "Use synonyms instead", "detail": "Replace some keyword instances with related terms. 'organic moisturizer' → 'natural face cream' → 'plant-based hydrating lotion'.", "xp": 15},
            {"n": 4, "title": "Read it aloud", "detail": "Read your content out loud. If any phrase sounds forced or robotic, rewrite it to sound natural.", "xp": 10},
        ],
    },

    # ── Schema ──
    "no_jsonld": {
        "xp_reward": 75, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Use Signalor's Auto Fix (recommended)", "detail": "If your store is connected, click Auto Fix on this item — Signalor will generate and inject the right schema automatically.", "xp": 10,
             "shopify": {"detail": "If you see an Auto Fix button, click it — Signalor injects schema via the Theme Extension. If it shows Verify, the Signalor Theme Extension handles this automatically. Go to Online Store → Themes → Customize → App embeds → toggle ON 'Signalor Schema' → Save."},
             "wordpress": {"detail": "If using Signalor GEO plugin, schema is auto-injected. Otherwise, install Rank Math or Yoast SEO — they add schema automatically for all your pages."}},
            {"n": 2, "title": "Verify schema is working", "detail": "Go to search.google.com/test/rich-results → paste your page URL → click Test. You should see your schema detected with no errors.", "xp": 30},
            {"n": 3, "title": "Check all pages have schema", "detail": "Test your homepage, a product page, and a blog post. Each should have the right schema type (Organization, Product, Article).", "xp": 20},
            {"n": 4, "title": "Ask in Chat if stuck", "detail": "Click 'Ask in Chat' and our AI assistant will generate the exact schema for your specific page.", "xp": 15},
        ],
    },
    "no_faqpage_schema": {
        "xp_reward": 65, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Ensure FAQ content exists", "detail": "You need visible FAQ content on the page before adding the schema.", "xp": 5},
            {"n": 2, "title": "Build the FAQPage JSON-LD", "detail": "Create a Question/Answer pair for each FAQ item.", "code": '<script type="application/ld+json">\n{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[\n  {"@type":"Question","name":"How does X work?","acceptedAnswer":{"@type":"Answer","text":"X works by..."}},\n  {"@type":"Question","name":"What does X cost?","acceptedAnswer":{"@type":"Answer","text":"X costs..."}}\n]}\n</script>', "xp": 30},
            {"n": 3, "title": "Add to page and validate", "detail": "Insert in <head>, then test at Google Rich Results Test.", "xp": 30},
        ],
    },
    "no_article_schema": {
        "xp_reward": 55, "difficulty": "medium", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Gather article metadata", "detail": "Collect: headline, author name, publish date, and a featured image URL.", "xp": 10},
            {"n": 2, "title": "Build Article schema", "detail": "Use the template and fill in your article's details.", "code": '<script type="application/ld+json">\n{"@context":"https://schema.org","@type":"Article","headline":"Your Title","author":{"@type":"Person","name":"Author Name"},"datePublished":"2025-01-15","publisher":{"@type":"Organization","name":"Your Brand"},"image":"https://yoursite.com/image.jpg"}\n</script>', "xp": 25},
            {"n": 3, "title": "Insert and validate", "detail": "Add to <head> and validate with Google Rich Results Test.", "xp": 20},
        ],
    },
    "no_organization_schema": {
        "xp_reward": 55, "difficulty": "medium", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Gather brand info", "detail": "Collect: company name, URL, logo URL, description, and social profile URLs.", "xp": 10},
            {"n": 2, "title": "Build Organization schema", "detail": "Fill in the template with your brand details.", "code": '<script type="application/ld+json">\n{"@context":"https://schema.org","@type":"Organization","name":"Your Brand","url":"https://yoursite.com","logo":"https://yoursite.com/logo.png","sameAs":["https://linkedin.com/company/you","https://twitter.com/you"],"description":"What you do"}\n</script>', "xp": 25},
            {"n": 3, "title": "Add to homepage <head>", "detail": "This should be on your homepage at minimum. Validate with Google.", "xp": 20},
        ],
    },
    "invalid_jsonld_structure": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Find your JSON-LD blocks", "detail": "Search page source for 'application/ld+json'.", "xp": 5},
            {"n": 2, "title": "Add missing @context", "detail": 'Ensure every JSON-LD block starts with "@context": "https://schema.org".', "xp": 15},
            {"n": 3, "title": "Validate JSON syntax", "detail": "Paste into jsonlint.com to check for syntax errors (missing commas, brackets).", "xp": 10},
        ],
    },
    "incomplete_article_schema": {
        "xp_reward": 35, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find your Article schema", "detail": "Search source for '@type.*Article' and locate the JSON-LD block.", "xp": 5},
            {"n": 2, "title": "Add missing fields", "detail": "Ensure headline, author (Person with name), datePublished (ISO format), and publisher exist.", "xp": 20},
            {"n": 3, "title": "Validate", "detail": "Test at Google Rich Results Test and fix any warnings.", "xp": 10},
        ],
    },
    "incomplete_organization_schema": {
        "xp_reward": 35, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find your Organization schema", "detail": "Search source for '@type.*Organization'.", "xp": 5},
            {"n": 2, "title": "Add logo, sameAs, description", "detail": "Add logo URL, social profile URLs in sameAs array, and a company description.", "xp": 20},
            {"n": 3, "title": "Add contactPoint", "detail": 'Add: "contactPoint": {"@type": "ContactPoint", "telephone": "+1-xxx", "contactType": "customer service"}', "xp": 10},
        ],
    },
    "incomplete_faqpage_schema": {
        "xp_reward": 35, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find your FAQPage schema", "detail": "Search source for '@type.*FAQPage'.", "xp": 5},
            {"n": 2, "title": "Add mainEntity array", "detail": "Add Question/Answer pairs matching your visible FAQ content.", "xp": 25},
            {"n": 3, "title": "Validate", "detail": "Test at Google Rich Results Test.", "xp": 5},
        ],
    },
    "incomplete_product_schema": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find your Product schema", "detail": "Search source for '@type.*Product'.", "xp": 5},
            {"n": 2, "title": "Add missing properties", "detail": "Add description, image, offers (with price/currency), brand, and aggregateRating.", "xp": 20},
            {"n": 3, "title": "Validate", "detail": "Test at Google Rich Results Test.", "xp": 5},
        ],
    },
    "incomplete_blogposting_schema": {
        "xp_reward": 35, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find your BlogPosting schema", "detail": "Search source for '@type.*BlogPosting'.", "xp": 5},
            {"n": 2, "title": "Add headline, author, dates", "detail": "Add headline, author (Person), datePublished, and optionally image and publisher.", "xp": 25},
            {"n": 3, "title": "Validate", "detail": "Test at Google Rich Results Test.", "xp": 5},
        ],
    },
    "incomplete_newsarticle_schema": {
        "xp_reward": 35, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find your NewsArticle schema", "detail": "Search source for '@type.*NewsArticle'.", "xp": 5},
            {"n": 2, "title": "Add required properties", "detail": "Add headline, author, datePublished, and publisher with name and logo.", "xp": 25},
            {"n": 3, "title": "Validate", "detail": "Test at Google Rich Results Test.", "xp": 5},
        ],
    },
    "incomplete_howto_schema": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find your HowTo schema", "detail": "Search source for '@type.*HowTo'.", "xp": 5},
            {"n": 2, "title": "Add step array", "detail": 'Add "step" with HowToStep items matching your visible instructions.', "xp": 20},
            {"n": 3, "title": "Validate", "detail": "Test at Google Rich Results Test.", "xp": 5},
        ],
    },

    # ── E-E-A-T ──
    "no_author": {
        "xp_reward": 60, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Add your name to the page", "detail": "Add a visible author line near the top of your content with your name and role.", "xp": 15,
             "shopify": {"detail": "For blog posts: Shopify shows the author automatically if you set it when creating the post.\n\nFor pages: Edit the page → at the top of the content, type: 'By [Your Name], [Your Role] at [Brand]'. Example: 'By Sarah Chen, Founder at Blessing Skincare'. Save."},
             "wordpress": {"detail": "For posts: WordPress shows the author automatically. Make sure your user profile has your full name (Users → Profile).\n\nFor pages: edit the page → add a Paragraph block near the top: 'By [Your Name], [Your Role]'. Update."}},
            {"n": 2, "title": "Use your real name", "detail": "AI models cross-reference author names across the web. Use your real name and make sure you have a LinkedIn profile with the same name.", "xp": 20},
            {"n": 3, "title": "Add credentials after your name", "detail": "If you have relevant qualifications, mention them: 'Dr. Sarah Chen, Board-Certified Dermatologist' or 'John Smith, 15-Year E-commerce Veteran'. This builds AI trust.", "xp": 15},
            {"n": 4, "title": "Link to your LinkedIn", "detail": "If your page has an author bio section, include a link to your LinkedIn profile. AI models verify author identity through external profiles.", "xp": 10},
        ],
    },
    "no_author_bio": {
        "xp_reward": 50, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Write a short bio about yourself", "detail": "Include: your name, your role, how many years of experience you have, and one key credential or achievement. Keep it 2-3 sentences.", "xp": 15},
            {"n": 2, "title": "Add the bio to your page", "detail": "Put it at the bottom of your content, after the main text.", "xp": 25,
             "shopify": {"detail": "Edit your page or blog post → scroll to the bottom of the content → add a bold heading 'About the Author' → type your bio paragraph below. Example:\n\n'Sarah Chen is the founder of Blessing Skincare with 12 years in organic beauty. She holds a degree in biochemistry from UCLA and has been featured in Vogue and Allure.'\n\nSave when done."},
             "wordpress": {"detail": "Edit your page → scroll to the bottom → add a Heading 'About the Author' → add a Paragraph with your bio → Update.\n\nOr install 'Simple Author Box' plugin for a professional author card with photo."}},
            {"n": 3, "title": "Add a real photo of yourself", "detail": "Upload a professional headshot. Real photos build trust with both readers and AI. Avoid stock images or logos.", "xp": 10},
        ],
    },
    "no_publish_date": {
        "xp_reward": 20, "difficulty": "easy", "estimated_minutes": 2,
        "steps": [
            {"n": 1, "title": "Add a visible date to your content", "detail": "Show when the content was written or last updated. This signals freshness to AI models.", "xp": 10,
             "shopify": {"detail": "For blog posts: Shopify shows the date automatically.\n\nFor pages: edit the page → add 'Published: [Month Day, Year]' or 'Last updated: [Month Day, Year]' near the top of your content → Save."},
             "wordpress": {"detail": "For posts: WordPress shows dates automatically.\n\nFor pages: edit the page → add a line like 'Last updated: January 15, 2025' below the title → Update."}},
            {"n": 2, "title": "Keep content fresh", "detail": "AI models prefer recent content. Update your pages regularly and change the 'Last updated' date when you do.", "xp": 10},
        ],
    },
    "no_updated_date": {
        "xp_reward": 15, "difficulty": "easy", "estimated_minutes": 2,
        "steps": [
            {"n": 1, "title": "Add visible update date", "detail": "Show 'Last updated: [date]' near the publish date.", "xp": 5},
            {"n": 2, "title": "Add meta tag", "detail": "Add article:modified_time to <head>.", "code": '<meta property="article:modified_time" content="2025-06-01T00:00:00Z">', "xp": 10},
        ],
    },
    "few_external_citations": {
        "xp_reward": 55, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Identify unsupported claims", "detail": "Find statements in your content that make claims without linking to sources.", "xp": 10},
            {"n": 2, "title": "Add links Signalor suggests", "detail": "Use the authoritative URLs and citation snippets in this recommendation—or Ask in Chat for a short list matched to your claims.", "xp": 20},
            {"n": 3, "title": "Add 3+ external links", "detail": "Link to the sources inline, using descriptive anchor text.", "xp": 25},
        ],
    },
    "no_trust_links": {
        "xp_reward": 50, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Use trusted links from this card", "detail": "Signalor suggests .gov, .edu, and major-publication links you can drop in—start there instead of searching manually.", "xp": 15},
            {"n": 2, "title": "Link to them in your content", "detail": "Add 2-3 links to authoritative domains within your article body.", "xp": 25},
            {"n": 3, "title": "Use descriptive anchor text", "detail": "Link text should describe the source: 'according to the FDA guidelines' not 'click here'.", "xp": 10},
        ],
    },
    "low_source_diversity": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Audit your external links", "detail": "List all domains you link to. Are there fewer than 3 unique domains?", "xp": 5},
            {"n": 2, "title": "Add links to new domains", "detail": "Use the extra domain suggestions in this recommendation or Ask in Chat—pick 2–3 you do not already link to.", "xp": 20},
        ],
    },
    "no_about_page": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Create or find your About page", "detail": "Your site should have /about with team info, mission, and history.", "xp": 10},
            {"n": 2, "title": "Link to it from this page", "detail": "Add a link to your About page in the footer or navigation visible on this page.", "xp": 15},
            {"n": 3, "title": "Mention team credentials", "detail": "The About page should list team members with titles and qualifications.", "xp": 5},
        ],
    },
    "no_first_hand_experience": {
        "xp_reward": 70, "difficulty": "hard", "estimated_minutes": 30,
        "steps": [
            {"n": 1, "title": "Add experience phrases", "detail": "Insert first-person experience signals: 'In our testing of 50+ tools...', 'After implementing for 6 months...'", "xp": 15},
            {"n": 2, "title": "Add a case study section", "detail": "Include specific numbers: 'Client X saw a 45% increase in...' with before/after data.", "xp": 25},
            {"n": 3, "title": "Add a 'Why Trust This' box", "detail": "Add a prominent trust box explaining your authority on this topic.", "code": '<div class="trust-box">\n  <strong>Why trust this guide?</strong>\n  <p>Based on [X years] of experience and [Y] real implementations. Every recommendation is tested on live projects.</p>\n</div>', "xp": 20},
            {"n": 4, "title": "Include original data or screenshots", "detail": "Screenshots, data tables, and 'lessons learned' are the strongest experience signals.", "xp": 10},
        ],
    },
    "no_expertise_indicators": {
        "xp_reward": 45, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {"n": 1, "title": "Add 'why' explanations", "detail": "Don't just say what to do — explain WHY it works. This signals deep knowledge.", "xp": 15},
            {"n": 2, "title": "Include pro tips", "detail": "Add expert-level tips that show insider knowledge: 'Most people miss this, but...'", "xp": 15},
            {"n": 3, "title": "Address common mistakes", "detail": "Add a 'Common Mistakes' section showing you know the pitfalls.", "xp": 15},
        ],
    },
    "low_authority": {
        "xp_reward": 60, "difficulty": "hard", "estimated_minutes": 30,
        "steps": [
            {"n": 1, "title": "Add data and statistics", "detail": "Reference industry reports, surveys, and research throughout.", "xp": 15},
            {"n": 2, "title": "Cite industry standards", "detail": "Reference frameworks, standards, or methodologies relevant to your topic.", "xp": 15},
            {"n": 3, "title": "Mention recognitions", "detail": "If you have awards, certifications, or partnerships — mention them.", "xp": 15},
            {"n": 4, "title": "Use authoritative tone", "detail": "Write with confidence backed by evidence. Avoid hedging words.", "xp": 15},
        ],
    },
    "low_trust_signals": {
        "xp_reward": 55, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {"n": 1, "title": "Add editorial policy", "detail": "Create a page explaining your content standards and fact-checking process.", "xp": 15},
            {"n": 2, "title": "Add disclosure statements", "detail": "If applicable, add affiliate disclosures or conflict of interest statements.", "xp": 10},
            {"n": 3, "title": "Add contact information", "detail": "Visible contact info (email, phone, address) signals legitimacy.", "xp": 15},
            {"n": 4, "title": "Source all claims", "detail": "Every factual claim should have a link or citation to its source.", "xp": 15},
        ],
    },

    # ── Technical ──
    "no_llms_txt": {
        "xp_reward": 75, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Prepare your llms.txt content", "detail": "Copy this template and customize with your brand.", "code": "# Your Brand Name\n\n## About\nOne paragraph about what your company does.\n\n## Key Pages\n- Homepage: https://yoursite.com/\n- About: https://yoursite.com/about\n- Products: https://yoursite.com/products\n- Blog: https://yoursite.com/blog\n\n## Contact\n- Email: hello@yoursite.com\n- Twitter: @yourbrand", "xp": 15},
            {"n": 2, "title": "Upload llms.txt to your site", "detail": "Make it accessible at https://yoursite.com/llms.txt", "xp": 35,
             "shopify": {"detail": "Shopify doesn't serve files at the root. Use our Signalor app: Apps → Signalor → it serves llms.txt at /apps/signalor/llms.txt automatically.\n\nAlternative: Go to Online Store → Pages → Add page → title: 'llms.txt' → URL handle: 'llms-txt' → paste content. Creates /pages/llms-txt (crawlable by AI).\n\nBest: use a Cloudflare Worker to serve at /llms.txt."},
             "wordpress": {"detail": "If Signalor GEO plugin is installed: go to Settings → Signalor GEO → paste content in 'llms.txt Content' field → Save. Plugin serves it at /llms.txt.\n\nWithout plugin: connect via FTP/SFTP → upload llms.txt to your WordPress root (same folder as wp-config.php)."}},
            {"n": 3, "title": "Verify it's live", "detail": "Open your llms.txt URL in a browser — should show as plain text.", "xp": 25,
             "shopify": {"detail": "Visit https://your-store.myshopify.com/apps/signalor/llms.txt (Signalor app) or https://your-store.myshopify.com/pages/llms-txt (page method)."},
             "wordpress": {"detail": "Visit https://yoursite.com/llms.txt — should display plain text. If 404, check plugin is active or file is in the correct directory."}},
        ],
    },
    "ai_bots_blocked": {
        "xp_reward": 80, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Check your current robots.txt", "detail": "Visit https://yoursite.com/robots.txt — look for lines blocking GPTBot, ClaudeBot, PerplexityBot.", "xp": 5},
            {"n": 2, "title": "Add AI crawler allow rules", "detail": "Add these lines to allow all major AI crawlers.", "code": "User-agent: GPTBot\nAllow: /\n\nUser-agent: Google-Extended\nAllow: /\n\nUser-agent: anthropic-ai\nAllow: /\n\nUser-agent: ClaudeBot\nAllow: /\n\nUser-agent: PerplexityBot\nAllow: /", "xp": 35,
             "shopify": {"detail": "Go to Online Store → Themes → Edit code → Templates → find 'robots.txt.liquid'. Add the allow rules. If file doesn't exist, create it to override Shopify's default.\n\nIf using Signalor app, it can serve a custom robots.txt via App Proxy.", "code": "{% comment %} Allow AI crawlers {% endcomment %}\nUser-agent: GPTBot\nAllow: /\n\nUser-agent: Google-Extended\nAllow: /\n\nUser-agent: ClaudeBot\nAllow: /\n\nUser-agent: PerplexityBot\nAllow: /"},
             "wordpress": {"detail": "Using Yoast SEO: go to Yoast → Tools → File editor → robots.txt → add the allow rules → Save.\n\nUsing Rank Math: go to Rank Math → General Settings → Edit robots.txt.\n\nUsing Signalor GEO plugin: Settings → Signalor GEO → paste robots.txt content → Save.\n\nManually: edit robots.txt in your WordPress root via FTP."}},
            {"n": 3, "title": "Remove any Disallow rules for AI bots", "detail": "Search robots.txt for 'Disallow' lines targeting GPTBot, ClaudeBot, etc. Delete them.", "xp": 20},
            {"n": 4, "title": "Check your CDN/WAF", "detail": "If using Cloudflare: Security → Bots → ensure AI bots aren't blocked. Add firewall rules to allow GPTBot, ClaudeBot user agents.", "xp": 20},
        ],
    },
    "no_sitemap": {
        "xp_reward": 35, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Generate a sitemap", "detail": "Most CMS platforms (WordPress, Shopify) auto-generate sitemaps. Check /sitemap.xml.", "xp": 10},
            {"n": 2, "title": "Verify it lists your pages", "detail": "Open the sitemap and confirm your important pages are included.", "xp": 10},
            {"n": 3, "title": "Submit to Google Search Console", "detail": "Go to Search Console > Sitemaps > submit your sitemap URL.", "xp": 15},
        ],
    },
    "crawl_failed": {
        "xp_reward": 90, "difficulty": "hard", "estimated_minutes": 30,
        "steps": [
            {"n": 1, "title": "Check HTTP status", "detail": "Visit your page — does it return 200 OK? Check with: curl -I your-url", "xp": 15},
            {"n": 2, "title": "Check bot blocking", "detail": "Your server may block non-browser user agents. Test with: curl -A 'Mozilla/5.0' your-url", "xp": 25},
            {"n": 3, "title": "Check firewall/CDN", "detail": "If using Cloudflare or AWS WAF, check if bot protection is blocking crawlers.", "xp": 25},
            {"n": 4, "title": "Check robots.txt", "detail": "Verify robots.txt isn't blocking the page path.", "xp": 15},
            {"n": 5, "title": "Re-run analysis", "detail": "After fixing, re-run the analysis to verify the crawler can access your page.", "xp": 10},
        ],
    },
    "meta_noindex": {
        "xp_reward": 40, "difficulty": "easy", "estimated_minutes": 2,
        "steps": [
            {"n": 1, "title": "Find the noindex tag", "detail": 'Search your page source for \'noindex\'. It might be in a <meta> tag or HTTP header.', "xp": 10},
            {"n": 2, "title": "Remove or change it", "detail": 'Change content="noindex" to content="index, follow" or remove the tag entirely.', "xp": 25},
            {"n": 3, "title": "Check for header-level noindex", "detail": "Some servers send X-Robots-Tag: noindex as an HTTP header. Check server config too.", "xp": 5},
        ],
    },
    "no_https": {
        "xp_reward": 35, "difficulty": "medium", "estimated_minutes": 30,
        "steps": [
            {"n": 1, "title": "Get an SSL certificate", "detail": "Use Let's Encrypt (free) or your hosting provider's SSL option.", "xp": 10},
            {"n": 2, "title": "Install the certificate", "detail": "Follow your hosting provider's instructions to install and activate SSL.", "xp": 15},
            {"n": 3, "title": "Redirect HTTP to HTTPS", "detail": "Set up a 301 redirect so all HTTP URLs redirect to HTTPS.", "xp": 10},
        ],
    },
    "slow_load_time": {
        "xp_reward": 50, "difficulty": "hard", "estimated_minutes": 45,
        "steps": [
            {"n": 1, "title": "Run PageSpeed Insights", "detail": "Test at pagespeed.web.dev — note the top issues.", "xp": 5},
            {"n": 2, "title": "Optimize images", "detail": "Compress images, use WebP format, and add width/height attributes.", "xp": 15},
            {"n": 3, "title": "Enable compression", "detail": "Enable Gzip/Brotli compression on your server.", "xp": 10},
            {"n": 4, "title": "Use a CDN", "detail": "Cloudflare (free tier) can dramatically improve load times globally.", "xp": 10},
            {"n": 5, "title": "Minimize JavaScript", "detail": "Defer non-critical JS and remove unused scripts.", "xp": 10},
        ],
    },
    "no_viewport": {
        "xp_reward": 20, "difficulty": "easy", "estimated_minutes": 1,
        "steps": [
            {"n": 1, "title": "Add viewport meta tag", "detail": "Add to your <head> section.", "code": '<meta name="viewport" content="width=device-width, initial-scale=1">', "xp": 15},
            {"n": 2, "title": "Test on mobile", "detail": "Open your page on a phone or use browser dev tools mobile view.", "xp": 5},
        ],
    },
    "no_canonical": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 2,
        "steps": [
            {"n": 1, "title": "Determine the canonical URL", "detail": "This is the preferred version of your page URL (no query params, consistent www/non-www).", "xp": 5},
            {"n": 2, "title": "Add canonical tag", "detail": "Add to your <head> section.", "code": '<link rel="canonical" href="https://yoursite.com/your-page-url">', "xp": 15},
            {"n": 3, "title": "Verify consistency", "detail": "Ensure the canonical URL matches the actual URL of the page.", "xp": 5},
        ],
    },

    # ── Entity ──
    "brand_not_in_ai": {
        "xp_reward": 100, "difficulty": "hard", "estimated_minutes": 120,
        "steps": [
            {"n": 1, "title": "Get listed on directories", "detail": "Submit to Product Hunt, G2, Capterra, Crunchbase, and AlternativeTo.", "xp": 25},
            {"n": 2, "title": "Create comparison content", "detail": "Write blog posts: '[Your Brand] vs [Competitor]'. AI models heavily cite comparison content.", "xp": 25},
            {"n": 3, "title": "Get press mentions", "detail": "Submit to HARO, contribute guest posts, issue press releases for milestones.", "xp": 25},
            {"n": 4, "title": "Post on Reddit", "detail": "Create valuable content mentioning your brand on Reddit.", "xp": 25},
        ],
    },
    "no_social_profiles": {
        "xp_reward": 40, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Add social links to footer", "detail": "Add LinkedIn, Twitter/X, and GitHub links to your page footer.", "code": '<a href="https://linkedin.com/company/yourbrand" rel="me">LinkedIn</a>\n<a href="https://twitter.com/yourbrand" rel="me">Twitter</a>', "xp": 15},
            {"n": 2, "title": "Add sameAs to schema", "detail": "Add social URLs to your Organization schema's sameAs array.", "code": '"sameAs": [\n  "https://linkedin.com/company/yourbrand",\n  "https://twitter.com/yourbrand"\n]', "xp": 20},
            {"n": 3, "title": "Ensure profiles are active", "detail": "Inactive profiles hurt more than help. Post at least monthly.", "xp": 5},
        ],
    },
    "no_wikipedia_presence": {
        "xp_reward": 100, "difficulty": "hard", "estimated_minutes": 180,
        "steps": [
            {"n": 1, "title": "Build notability first", "detail": "Get featured in 3+ independent reliable sources (news, industry publications).", "xp": 25},
            {"n": 2, "title": "Create a Wikidata entry", "detail": "Wikidata is easier than Wikipedia and still indexed by AI. Create an entry at wikidata.org.", "xp": 25},
            {"n": 3, "title": "Get on Crunchbase & LinkedIn", "detail": "These are cited by AI as Wikipedia alternatives.", "xp": 20},
            {"n": 4, "title": "Request Wikipedia creation", "detail": "Once you have 3+ independent sources, submit to Articles for Creation. Don't write it yourself.", "xp": 30},
        ],
    },
    "no_reddit_presence": {
        "xp_reward": 60, "difficulty": "medium", "estimated_minutes": 30,
        "steps": [
            {"n": 1, "title": "Find relevant subreddits", "detail": "Search reddit.com for your industry keywords. Join 3-5 subreddits.", "xp": 10},
            {"n": 2, "title": "Create a valuable post", "detail": "Title: 'How we solved [problem] — lessons learned'. Share real data, not promotion.", "xp": 25},
            {"n": 3, "title": "Engage with comments", "detail": "Reply to every comment within 24 hours. Add more context when asked.", "xp": 15},
            {"n": 4, "title": "Answer existing questions", "detail": "Find threads where your expertise is relevant and provide helpful answers.", "xp": 10},
        ],
    },

    # ── AI Visibility ──
    "not_in_google_ai": {
        "xp_reward": 65, "difficulty": "hard", "estimated_minutes": 60,
        "steps": [
            {"n": 1, "title": "Optimize for featured snippets", "detail": "Structure content as Q&A: question in H2, direct answer immediately below.", "xp": 20},
            {"n": 2, "title": "Cover follow-up questions", "detail": "Use the question ideas and headings Signalor suggests (or Ask in Chat) so your page answers the same follow-ups AI surfaces—without searching SERPs yourself.", "xp": 20},
            {"n": 3, "title": "Build topical authority", "detail": "Create a content cluster: 1 pillar page + 5-10 supporting articles, interlinked.", "xp": 25},
        ],
    },
    "no_reddit_ai_presence": {
        "xp_reward": 55, "difficulty": "medium", "estimated_minutes": 30,
        "steps": [
            {"n": 1, "title": "Join relevant subreddits", "detail": "Find 3-5 subreddits where your audience hangs out. Lurk 1 week first.", "xp": 10},
            {"n": 2, "title": "Start contributing value", "detail": "Answer questions with genuine expertise. Build karma and reputation.", "xp": 15},
            {"n": 3, "title": "Create a showcase post", "detail": "Share genuine data: 'How we solved [problem] — lessons learned'.", "xp": 20},
            {"n": 4, "title": "Maintain weekly presence", "detail": "Comment on relevant threads weekly. AI re-indexes Reddit frequently.", "xp": 10},
        ],
    },
    "weak_brand_site": {
        "xp_reward": 45, "difficulty": "medium", "estimated_minutes": 60,
        "steps": [
            {"n": 1, "title": "Add essential pages", "detail": "Ensure you have: /about, /contact, /blog, privacy policy, and terms of service.", "xp": 15},
            {"n": 2, "title": "Add footer links", "detail": "Social profiles, About, Contact, Blog, Privacy, and Terms in your footer.", "xp": 10},
            {"n": 3, "title": "Ensure content depth", "detail": "Homepage should have 800+ words: what you do, who you serve, testimonials.", "xp": 20},
        ],
    },

    # ── Crawl failures ──
    "crawl_blocked_403": {
        "xp_reward": 85, "difficulty": "hard", "estimated_minutes": 30,
        "steps": [
            {"n": 1, "title": "Check your firewall/CDN", "detail": "If using Cloudflare or AWS WAF, check if bot protection is too aggressive.", "xp": 25},
            {"n": 2, "title": "Add bot allow rules", "detail": "Add specific allow rules for GPTBot, ClaudeBot, PerplexityBot in your WAF settings.", "xp": 35},
            {"n": 3, "title": "Test with curl", "detail": "Run: curl -I -A 'GPTBot' your-url — should return 200, not 403.", "xp": 25},
        ],
    },
    "crawl_timeout": {
        "xp_reward": 80, "difficulty": "hard", "estimated_minutes": 45,
        "steps": [
            {"n": 1, "title": "Check server response time", "detail": "Use pagespeed.web.dev or curl -w '%{time_total}' your-url. Aim for <3 seconds.", "xp": 15},
            {"n": 2, "title": "Upgrade hosting if needed", "detail": "Shared hosting often can't handle crawler traffic. Consider VPS or CDN.", "xp": 25},
            {"n": 3, "title": "Enable caching", "detail": "Server-side caching (Redis, Varnish) and CDN caching dramatically reduce response time.", "xp": 25},
            {"n": 4, "title": "Optimize database queries", "detail": "Slow DB queries are the #1 cause of timeout. Check your CMS for slow queries.", "xp": 15},
        ],
    },

    # ── D2C Specific Steps ──

    "no_product_schema": {
        "xp_reward": 80, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {
                "n": 1, "title": "Gather your product data", "xp": 10,
                "detail": "Collect: product name, description, image URL, price, currency, availability, brand name, and average rating.",
            },
            {
                "n": 2, "title": "Add Product schema to your page", "xp": 40,
                "detail": "Add JSON-LD Product markup with all required fields.",
                "code": '<script type="application/ld+json">\n{\n  "@context": "https://schema.org",\n  "@type": "Product",\n  "name": "Your Product Name",\n  "description": "Product description here",\n  "image": "https://yoursite.com/product.jpg",\n  "brand": {"@type": "Brand", "name": "Your Brand"},\n  "offers": {\n    "@type": "Offer",\n    "price": "29.99",\n    "priceCurrency": "GBP",\n    "availability": "https://schema.org/InStock",\n    "url": "https://yoursite.com/product"\n  },\n  "aggregateRating": {\n    "@type": "AggregateRating",\n    "ratingValue": "4.8",\n    "reviewCount": "124"\n  }\n}\n</script>',
                "shopify": {
                    "detail": "Shopify auto-generates basic Product schema, but it's often incomplete. To add full schema:\n\n1. Go to Online Store → Themes → Edit code\n2. Open Sections → main-product.liquid (or product-template.liquid)\n3. Paste the JSON-LD at the bottom of the file, before the closing tag\n4. Replace hardcoded values with Liquid variables:",
                    "code": '<script type="application/ld+json">\n{\n  "@context": "https://schema.org",\n  "@type": "Product",\n  "name": {{ product.title | json }},\n  "description": {{ product.description | strip_html | json }},\n  "image": {{ product.featured_image | image_url: width: 1024 | json }},\n  "brand": {"@type": "Brand", "name": {{ shop.name | json }}},\n  "offers": {\n    "@type": "Offer",\n    "price": {{ product.price | money_without_currency | json }},\n    "priceCurrency": {{ cart.currency.iso_code | json }},\n    "availability": {% if product.available %}"https://schema.org/InStock"{% else %}"https://schema.org/OutOfStock"{% endif %},\n    "url": "{{ shop.url }}{{ product.url }}"\n  }\n}\n</script>',
                },
                "wordpress": {
                    "detail": "If using WooCommerce:\n\n1. Install 'Yoast WooCommerce SEO' plugin — it auto-generates Product schema\n2. Or install 'WPCode' plugin → Code Snippets → Add New\n3. Paste the JSON-LD below → set Location to 'WooCommerce Product Pages Only'\n4. Replace placeholder values with your product data\n\nIf not using WooCommerce: add the JSON-LD manually to your product page template via Appearance → Theme Editor → single-product.php",
                },
            },
            {"n": 3, "title": "Validate with Google Rich Results Test", "detail": "Go to search.google.com/test/rich-results → paste your product URL → check for Product schema with no errors.", "xp": 15},
            {"n": 4, "title": "Add rating data if available", "detail": "If you use a reviews app (Judge.me, Yotpo, Trustpilot), ensure the aggregateRating is populated with real data.", "xp": 15},
        ],
    },
    "no_customer_reviews": {
        "xp_reward": 70, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {
                "n": 1, "title": "Install a reviews solution", "xp": 20,
                "detail": "Choose a reviews platform to collect and display customer reviews.",
                "shopify": {
                    "detail": "Go to Shopify App Store and install one of:\n\n• Judge.me (free tier available) — Apps → Search 'Judge.me' → Install\n• Loox (photo reviews) — Apps → Search 'Loox' → Install\n• Yotpo — Apps → Search 'Yotpo' → Install\n\nAfter installing, enable the review widget on your product pages via the app settings.",
                },
                "wordpress": {
                    "detail": "If using WooCommerce, reviews are built-in:\n\n1. Go to WooCommerce → Settings → Products tab\n2. Check 'Enable product reviews'\n3. Check 'Show verified owner label'\n\nFor non-WooCommerce sites, install a reviews plugin:\n• Go to Plugins → Add New → search 'Site Reviews' or 'WP Customer Reviews' → Install & Activate",
                },
            },
            {"n": 2, "title": "Collect 5-10 initial reviews", "detail": "Email your best customers asking for reviews. Offer a small incentive (10% off next order). Focus on getting detailed reviews with specific results.", "xp": 15},
            {
                "n": 3, "title": "Add Review schema markup", "xp": 20,
                "detail": "Ensure reviews generate schema markup so AI can read them.",
                "shopify": {
                    "detail": "Most Shopify review apps (Judge.me, Loox) automatically add Review schema. Verify by:\n1. Visit your product page\n2. Right-click → View Page Source\n3. Search for 'AggregateRating' — it should be present\n\nIf not, check the app settings for 'SEO' or 'Schema' options and enable them.",
                },
                "wordpress": {
                    "detail": "WooCommerce + Yoast SEO auto-generates Review schema. Verify by:\n1. Visit your product page\n2. Right-click → View Page Source\n3. Search for 'AggregateRating'\n\nIf missing, install 'Schema & Structured Data for WP' plugin and enable Review schema.",
                },
            },
            {"n": 4, "title": "Display reviews prominently", "detail": "Show reviews above the fold or right below the product description. Include star ratings, reviewer names, and dates. AI specifically looks for visible review content.", "xp": 15},
        ],
    },
    "no_meta_description": {
        "xp_reward": 40, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {
                "n": 1, "title": "Write a compelling meta description (150-160 chars)", "xp": 15,
                "detail": "Include: key benefit, a number (social proof), and what makes you different.",
                "code": '<meta name="description" content="Award-winning organic skincare loved by 50,000+ customers. Free shipping over £30. Dermatologist-approved, cruelty-free formulas.">',
            },
            {
                "n": 2, "title": "Add the meta description to your page", "xp": 25,
                "detail": "Add it to your page's <head> section.",
                "shopify": {
                    "detail": "For product pages:\n1. Go to Products → select the product\n2. Scroll down to 'Search engine listing' → click 'Edit'\n3. Enter your meta description in the 'Description' field\n4. Save\n\nFor other pages:\n1. Go to Online Store → Pages → select the page\n2. Scroll to 'Search engine listing' → click 'Edit'\n3. Enter meta description → Save",
                },
                "wordpress": {
                    "detail": "If using Yoast SEO:\n1. Edit the page/post\n2. Scroll to the Yoast SEO box below the editor\n3. Click 'Edit snippet'\n4. Enter your meta description\n5. Update the page\n\nIf using Rank Math:\n1. Edit the page → scroll to Rank Math box\n2. Click 'Edit Snippet'\n3. Enter description → Update",
                },
            },
        ],
    },
    "thin_product_description": {
        "xp_reward": 65, "difficulty": "medium", "estimated_minutes": 25,
        "steps": [
            {"n": 1, "title": "Write a benefit-led opening (50 words)", "detail": "Answer: What is this product and why should I care? Lead with the main benefit, not features.", "xp": 10},
            {"n": 2, "title": "List 5+ benefits with details", "detail": "Not just 'Long-lasting'. Write: 'Lasts 12+ hours without reapplication — tested in humid conditions.' Each benefit = one bullet.", "xp": 15},
            {"n": 3, "title": "Add a 'What makes this different' section", "detail": "Compare to alternatives: 'Unlike traditional formulas, ours uses X technology that...'", "xp": 10},
            {"n": 4, "title": "Include specifications", "detail": "Add: dimensions, weight, ingredients/materials, color options, warranty. Use a table or list.", "xp": 10},
            {
                "n": 5, "title": "Add a use case paragraph", "xp": 10,
                "detail": "End with: 'Perfect for [scenario].' This maps directly to AI search queries.",
                "shopify": {
                    "detail": "Go to Products → select the product → edit the Description field in the rich text editor. Aim for 300-500 words total. Use headings (H2, H3) to structure sections.",
                },
                "wordpress": {
                    "detail": "Edit the product/page in the Block Editor. Use Heading blocks (H2) for sections and Paragraph blocks for content. For WooCommerce: edit the 'Product description' tab. Aim for 300-500 words.",
                },
            },
            {"n": 6, "title": "Verify word count", "detail": "Select all text on the page → paste into wordcounter.net → aim for 300-500 words minimum.", "xp": 10},
        ],
    },
    "no_shipping_info": {
        "xp_reward": 45, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {
                "n": 1, "title": "Create a shipping information section", "xp": 20,
                "detail": "Include: delivery timeframes, costs, free shipping threshold, international availability.",
                "shopify": {
                    "detail": "1. Go to Settings → Shipping and delivery — configure your shipping zones\n2. Go to Online Store → Pages → Add page → title: 'Shipping & Delivery'\n3. Add your shipping details: timeframes per zone, costs, free shipping threshold\n4. Link from footer: Online Store → Navigation → Footer → Add 'Shipping' link\n\nFor product pages: add shipping info in the product description or use a collapsible tab (most themes support this in Customize → Product page → add 'Collapsible tab' block).",
                },
                "wordpress": {
                    "detail": "1. Go to Pages → Add New → title: 'Shipping & Delivery'\n2. Add your shipping details with headings for each section\n3. Publish the page\n4. Add to footer: Appearance → Menus → select Footer menu → add the Shipping page → Save\n\nFor WooCommerce: go to WooCommerce → Settings → Shipping to configure zones, then add a 'Shipping' tab to product pages via a plugin like 'WooCommerce Tab Manager'.",
                },
            },
            {"n": 2, "title": "Add shipping details to product pages", "detail": "Show estimated delivery time and cost directly on each product page, near the Add to Cart button.", "xp": 15},
            {"n": 3, "title": "Highlight free shipping threshold", "detail": "If you offer free shipping over a threshold, make it prominent: 'Free shipping on orders over £50'. Place it in the announcement bar.", "xp": 10},
        ],
    },
    "no_comparison_content": {
        "xp_reward": 65, "difficulty": "hard", "estimated_minutes": 45,
        "steps": [
            {"n": 1, "title": "Pick comparison brands", "detail": "Use the competitor names Signalor already surfaced for your run (or Ask in Chat for a short list). You do not need to search the web to discover them.", "xp": 10},
            {
                "n": 2, "title": "Create a comparison page", "xp": 30,
                "detail": "Title: '[Your Brand] vs [Competitor]'. Include a feature table, pricing comparison, and honest pros/cons.",
                "shopify": {
                    "detail": "Go to Online Store → Pages → Add page\nTitle: 'Your Brand vs Competitor'\nUse the rich text editor to create:\n1. An intro paragraph\n2. A comparison table (use HTML: <table>)\n3. Sections for pricing, features, and reviews\n4. A clear conclusion\n\nAdd the page to your blog or navigation for discovery.",
                },
                "wordpress": {
                    "detail": "Go to Posts → Add New (or Pages → Add New)\nTitle: 'Your Brand vs Competitor'\nUse the Block Editor:\n1. Add a Paragraph block for intro\n2. Add a Table block for comparison\n3. Add Heading blocks (H2) for each section\n4. Publish and add to your blog category\n\nInstall 'TablePress' plugin for more advanced comparison tables.",
                },
            },
            {"n": 3, "title": "Be honest and specific", "detail": "Acknowledge competitor strengths. Use specific data: '30% faster shipping', 'Half the price'. Dishonest comparisons backfire with AI.", "xp": 15},
            {"n": 4, "title": "Add FAQ schema", "detail": "Add FAQ section: 'Is [Your Brand] better than [Competitor]?' with a factual answer. Add FAQPage schema.", "xp": 10},
        ],
    },
    "no_social_proof_numbers": {
        "xp_reward": 50, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Gather your proof points", "detail": "List: customer count, review score, units sold, press mentions, social followers, years in business. Use exact numbers.", "xp": 10},
            {
                "n": 2, "title": "Add prominently to your pages", "xp": 25,
                "detail": "Place social proof above the fold — in the hero section or just below it.",
                "shopify": {
                    "detail": "Go to Online Store → Themes → Customize\n1. Add a 'Rich text' or 'Custom Liquid' section below the hero\n2. Add content like: '50,000+ happy customers | 4.9★ on Trustpilot | Featured in Vogue'\n3. For product pages: add a section showing review count and average rating above the description\n\nOr edit theme code: Sections → main-product.liquid → add trust badges HTML near the Add to Cart button.",
                },
                "wordpress": {
                    "detail": "Edit your homepage in the Block Editor:\n1. Add a Columns block with 3-4 columns\n2. In each column: add a number (Heading block, large size) and label (Paragraph block)\n3. Example: '50,000+' / 'Happy Customers'\n\nFor product pages: edit the product template or use 'Elementor' widgets to add trust numbers near the buy button.",
                },
            },
            {"n": 3, "title": "Keep numbers updated", "detail": "Set a monthly reminder to update customer counts and review scores. Stale numbers lose trust.", "xp": 15},
        ],
    },
}


def _default_xp(priority: str) -> int:
    return {"critical": 80, "high": 50, "medium": 30, "low": 15}.get(priority, 20)


def _default_difficulty(priority: str) -> str:
    return {"critical": "hard", "high": "medium", "medium": "easy", "low": "easy"}.get(priority, "medium")


def generate_recommendations(
    pillar_details: dict[str, dict],
    pillar_scores: dict[str, float] | None = None,
) -> list[dict]:
    """
    Smart recommendation engine:
    1. Filters off-page actions
    2. Sorts by pillar weakness (lowest score first) + impact
    3. Caps at MAX_PER_PILLAR per pillar
    4. Suppresses recs for high-scoring pillars (>70)
    5. Adds "why" context to each recommendation
    6. Caps total at MAX_RECOMMENDATIONS
    """
    scores = pillar_scores or {}
    candidates = []

    for _pillar_name, details in pillar_details.items():
        findings = details.get("findings", [])
        for finding in findings:
            if finding in OFFPAGE_FINDINGS:
                continue

            rule = RECOMMENDATION_RULES.get(finding)
            if rule:
                pillar = rule.get("pillar", "")
                if pillar in OFFPAGE_PILLARS:
                    continue

                # Suppress recs for pillars already scoring well
                pillar_score = scores.get(pillar, 0)
                if pillar_score >= SUPPRESS_THRESHOLD:
                    continue

                rec = dict(rule)
                rec["finding_code"] = finding
                rec["impact_score"] = IMPACT_SCORES.get(finding, 10)
                rec["fixable"] = finding not in MANUAL_FINDINGS

                # Priority engine: combine pillar weakness + impact + severity
                pillar_urgency = 100 - pillar_score  # lower score = higher urgency
                rec["_sort_score"] = (
                    pillar_urgency * 0.4 +
                    rec["impact_score"] * 0.4 +
                    (100 if rec["priority"] == "critical" else 60 if rec["priority"] == "high" else 30) * 0.2
                )

                # Add "why this matters"
                rec["why"] = PILLAR_WHY.get(pillar, "")

                # Add gamified steps metadata
                meta = STEP_META.get(finding, {})
                rec["steps"] = meta.get("steps", [])
                rec["xp_reward"] = meta.get("xp_reward", _default_xp(rec["priority"]))
                rec["difficulty"] = meta.get("difficulty", _default_difficulty(rec["priority"]))
                rec["estimated_minutes"] = meta.get("estimated_minutes", 10)

                candidates.append(rec)

    # Sort by combined priority score (highest first)
    candidates.sort(key=lambda r: -r["_sort_score"])

    # Cap per pillar — max 3 from any single pillar
    pillar_counts: dict[str, int] = {}
    filtered = []
    for rec in candidates:
        p = rec["pillar"]
        if pillar_counts.get(p, 0) >= MAX_PER_PILLAR:
            continue
        pillar_counts[p] = pillar_counts.get(p, 0) + 1
        filtered.append(rec)

    # Cap total
    top = filtered[:MAX_RECOMMENDATIONS]

    # Clean internal / non-model fields before DB create()
    for rec in top:
        rec.pop("impact_score", None)
        rec.pop("_sort_score", None)
        rec.pop("fixable", None)

    return top
