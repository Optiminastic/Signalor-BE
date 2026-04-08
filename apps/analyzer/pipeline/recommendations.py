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

MAX_RECOMMENDATIONS = 12
MAX_PER_PILLAR = 3

# Off-page findings — filtered out, only on-page + technical shown
OFFPAGE_FINDINGS = {
    "brand_not_in_ai", "no_medium_ai_presence", "no_medium_presence",
    "no_reddit_ai_presence", "no_reddit_presence", "no_social_profiles",
    "no_wikipedia_presence", "not_in_google_ai", "weak_brand_site",
}
OFFPAGE_PILLARS = {"entity", "ai_visibility"}

# Findings that require manual action — cannot be auto-fixed by plugin/app
MANUAL_FINDINGS = {
    "no_sitemap", "no_https", "slow_load_time", "crawl_failed",
    "crawl_blocked_403", "crawl_timeout",
    "brand_not_in_ai", "no_wikipedia_presence", "no_social_profiles",
    "no_reddit_presence", "no_medium_presence",
    "not_in_google_ai", "no_reddit_ai_presence", "no_medium_ai_presence",
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
            {"n": 1, "title": "Find your page title", "detail": "Identify the main topic of the page — this becomes your H1.", "xp": 5},
            {"n": 2, "title": "Add the H1 tag", "detail": "Wrap your title in <h1>Your Page Title</h1>. Place it as the first heading on the page.", "code": "<h1>Your Page Title Here</h1>", "xp": 15},
            {"n": 3, "title": "Verify only one H1 exists", "detail": "Search your page source for '<h1' — there should be exactly one.", "xp": 10},
        ],
    },
    "multiple_h1": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Find all H1 tags", "detail": "Search your page source for '<h1'. Note every instance.", "xp": 5},
            {"n": 2, "title": "Keep the best one", "detail": "Choose the H1 that best describes the entire page topic.", "xp": 10},
            {"n": 3, "title": "Demote the rest to H2", "detail": "Change all other <h1> tags to <h2> or <h3> based on their importance.", "xp": 10},
        ],
    },
    "broken_heading_hierarchy": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Map your heading structure", "detail": "List all headings in order. Look for gaps (e.g., H1 → H3 skipping H2).", "xp": 10},
            {"n": 2, "title": "Fill the gaps", "detail": "Add missing heading levels so the hierarchy flows: H1 → H2 → H3. Never skip levels.", "xp": 15},
            {"n": 3, "title": "Test with a screen reader", "detail": "Use browser dev tools to verify the heading outline makes logical sense.", "xp": 5},
        ],
    },
    "no_faq_section": {
        "xp_reward": 60, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {"n": 1, "title": "Brainstorm 5-8 questions", "detail": "Think about what your customers ask most. Check Google's 'People Also Ask' for your topic.", "xp": 10},
            {"n": 2, "title": "Write concise answers", "detail": "Each answer should be 2-4 sentences. Start with a direct answer, then elaborate.", "xp": 15},
            {"n": 3, "title": "Add the FAQ HTML", "detail": "Add an FAQ section with <h2>Frequently Asked Questions</h2> followed by Q&A pairs.", "code": "<h2>Frequently Asked Questions</h2>\n<h3>What is [your topic]?</h3>\n<p>[Direct answer in 2-3 sentences]</p>", "xp": 15},
            {"n": 4, "title": "Add FAQPage schema", "detail": "Add JSON-LD FAQPage markup so AI engines can parse your Q&As directly.", "code": '<script type="application/ld+json">\n{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{"@type":"Question","name":"Your question?","acceptedAnswer":{"@type":"Answer","text":"Your answer."}}]}\n</script>', "xp": 20},
        ],
    },
    "no_lists": {
        "xp_reward": 20, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Identify list-worthy content", "detail": "Find paragraphs that list features, benefits, steps, or comparisons.", "xp": 5},
            {"n": 2, "title": "Convert to HTML lists", "detail": "Use <ul> for unordered lists and <ol> for step-by-step sequences.", "code": "<ul>\n  <li>Feature one — description</li>\n  <li>Feature two — description</li>\n  <li>Feature three — description</li>\n</ul>", "xp": 15},
        ],
    },
    "no_answer_first": {
        "xp_reward": 40, "difficulty": "medium", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Identify the main question", "detail": "What question does someone searching for your page want answered?", "xp": 10},
            {"n": 2, "title": "Write a 1-2 sentence direct answer", "detail": "Start with '[Topic] is...' or 'The answer is...' — no preamble.", "xp": 15},
            {"n": 3, "title": "Place it as the first paragraph", "detail": "Move your direct answer to the very top of the content, before any background or context.", "xp": 15},
        ],
    },
    "few_internal_links": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "List your related pages", "detail": "Find 3-5 pages on your site that relate to this page's topic.", "xp": 5},
            {"n": 2, "title": "Add contextual links", "detail": "Link to them naturally within your content using descriptive anchor text (not 'click here').", "xp": 15},
            {"n": 3, "title": "Check for broken links", "detail": "Click each link to verify it works and goes to the right page.", "xp": 5},
        ],
    },
    "no_citations": {
        "xp_reward": 80, "difficulty": "medium", "estimated_minutes": 25,
        "steps": [
            {"n": 1, "title": "Find 3-5 authoritative sources", "detail": "Search Google Scholar, industry reports, or .gov/.edu sites for data supporting your claims.", "xp": 15},
            {"n": 2, "title": "Add inline citations", "detail": "Insert citations naturally: 'According to a 2024 McKinsey report, AI adoption grew by 72%.'", "code": "According to a <a href='https://source.com/report'>2024 McKinsey report</a>, AI adoption grew by 72% (McKinsey, 2024).", "xp": 25},
            {"n": 3, "title": "Add a References section", "detail": "Create a 'Sources' or 'References' section at the bottom listing all cited works.", "xp": 20},
            {"n": 4, "title": "Link to original sources", "detail": "Make each citation a clickable link to the original research or report.", "xp": 20},
        ],
    },
    "no_statistics": {
        "xp_reward": 70, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {"n": 1, "title": "Identify claimable statements", "detail": "Find sentences where you make general claims that could be backed by data.", "xp": 10},
            {"n": 2, "title": "Research specific numbers", "detail": "Find real statistics from reports, surveys, or studies that support your points.", "xp": 20},
            {"n": 3, "title": "Insert data points", "detail": "Replace vague claims with specific numbers: '67% of companies now use...' not 'many companies use...'", "xp": 25},
            {"n": 4, "title": "Cite every statistic", "detail": "Always mention the source: '(Gartner, 2024)' or link to the report.", "xp": 15},
        ],
    },
    "no_expert_quotes": {
        "xp_reward": 50, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Identify quotable experts", "detail": "Find 2-3 recognized experts in your field (CEOs, researchers, authors).", "xp": 10},
            {"n": 2, "title": "Find or create quotes", "detail": "Pull quotes from interviews, books, or conference talks. Or quote your own team's experts.", "xp": 15},
            {"n": 3, "title": "Add with proper attribution", "detail": "Use blockquote tags with the expert's name, title, and organization.", "code": '<blockquote>\n  <p>"AI will transform how small businesses compete globally."</p>\n  <cite>— Dr. Jane Smith, Head of AI Research at Stanford</cite>\n</blockquote>', "xp": 25},
        ],
    },
    "weak_authoritative_tone": {
        "xp_reward": 45, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Find hedging language", "detail": "Search for words like 'might', 'maybe', 'I think', 'possibly', 'could be'. List every instance.", "xp": 10},
            {"n": 2, "title": "Replace with confident statements", "detail": "'This might help' → 'This demonstrably improves'. Back claims with data.", "xp": 20},
            {"n": 3, "title": "Add evidence after strong claims", "detail": "Every confident claim should have a data point or citation within the same paragraph.", "xp": 15},
        ],
    },
    "poor_readability": {
        "xp_reward": 40, "difficulty": "medium", "estimated_minutes": 20,
        "steps": [
            {"n": 1, "title": "Check your reading level", "detail": "Paste your content into hemingwayapp.com — aim for Grade 8-12.", "xp": 5},
            {"n": 2, "title": "Shorten long sentences", "detail": "Break any sentence over 25 words into two. Target 15-20 words average.", "xp": 15},
            {"n": 3, "title": "Replace jargon", "detail": "Define technical terms on first use: 'RAG (Retrieval-Augmented Generation) works like...'", "xp": 10},
            {"n": 4, "title": "Break up long paragraphs", "detail": "No paragraph should exceed 3-4 sentences. Each paragraph = one idea.", "xp": 10},
        ],
    },
    "no_technical_terms": {
        "xp_reward": 30, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "List domain terms", "detail": "Write down 5-10 technical terms specific to your industry.", "xp": 10},
            {"n": 2, "title": "Weave them into content", "detail": "Use each term naturally, and define it on first use for accessibility.", "xp": 15},
            {"n": 3, "title": "Balance technical and plain language", "detail": "Alternate between expert terminology and simple explanations.", "xp": 5},
        ],
    },
    "low_vocabulary_diversity": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find repeated words", "detail": "Use Ctrl+F to find words you use more than 5 times.", "xp": 5},
            {"n": 2, "title": "Replace with synonyms", "detail": "Swap repeated terms: 'AI' → 'artificial intelligence' → 'machine learning systems'.", "xp": 15},
            {"n": 3, "title": "Vary sentence structure", "detail": "Mix short punchy sentences with longer explanatory ones.", "xp": 5},
        ],
    },
    "low_word_count": {
        "xp_reward": 60, "difficulty": "hard", "estimated_minutes": 45,
        "steps": [
            {"n": 1, "title": "Outline missing subtopics", "detail": "Research what competitors cover that you don't. List 3-5 subtopics to add.", "xp": 10},
            {"n": 2, "title": "Expand each section", "detail": "Add examples, case studies, and data to each section. Target 1,500+ words total.", "xp": 25},
            {"n": 3, "title": "Add an FAQ section", "detail": "Add 5-8 frequently asked questions with detailed answers.", "xp": 15},
            {"n": 4, "title": "Add a summary/conclusion", "detail": "End with a concise summary that recaps key takeaways.", "xp": 10},
        ],
    },
    "poor_paragraph_structure": {
        "xp_reward": 20, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Find long paragraphs", "detail": "Any paragraph over 80 words needs splitting.", "xp": 5},
            {"n": 2, "title": "Split into focused chunks", "detail": "Each paragraph = one idea. 2-3 sentences, 20-80 words.", "xp": 10},
            {"n": 3, "title": "Add topic sentences", "detail": "Start each paragraph with its main point.", "xp": 5},
        ],
    },
    "keyword_stuffing": {
        "xp_reward": 50, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Find the repeated keyword", "detail": "Use Ctrl+F to find the most repeated keyword. Count occurrences.", "xp": 5},
            {"n": 2, "title": "Remove unnatural repetitions", "detail": "Keep the keyword in title, first paragraph, and 2-3 natural mentions. Remove the rest.", "xp": 20},
            {"n": 3, "title": "Replace with synonyms", "detail": "Use variations and related terms instead of repeating the exact same phrase.", "xp": 15},
            {"n": 4, "title": "Read aloud test", "detail": "Read your content aloud. If any phrase sounds forced or repetitive, rewrite it.", "xp": 10},
        ],
    },

    # ── Schema ──
    "no_jsonld": {
        "xp_reward": 75, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Choose your schema type", "detail": "Organization for homepage, Article for blog posts, Product for product pages.", "xp": 10},
            {"n": 2, "title": "Generate the JSON-LD", "detail": "Use the template below and fill in your details.", "code": '<script type="application/ld+json">\n{\n  "@context": "https://schema.org",\n  "@type": "Organization",\n  "name": "Your Company",\n  "url": "https://yoursite.com",\n  "logo": "https://yoursite.com/logo.png",\n  "description": "What your company does",\n  "sameAs": ["https://linkedin.com/company/you", "https://twitter.com/you"]\n}\n</script>', "xp": 30},
            {"n": 3, "title": "Add to your page <head>", "detail": "Paste the script tag inside your <head> section, before </head>.", "xp": 20},
            {"n": 4, "title": "Validate with Google", "detail": "Test at search.google.com/test/rich-results — fix any errors shown.", "xp": 15},
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
            {"n": 1, "title": "Add visible author byline", "detail": "Add your name and title below the page title.", "code": '<p class="author">By <strong>Your Name</strong>, Your Title at Your Brand</p>', "xp": 15},
            {"n": 2, "title": "Add author meta tag", "detail": "Add to your page <head> section.", "code": '<meta name="author" content="Your Name">', "xp": 15},
            {"n": 3, "title": "Add to Article schema", "detail": "If you have Article/BlogPosting schema, add the author property.", "code": '"author": {"@type": "Person", "name": "Your Name", "jobTitle": "Your Title", "url": "https://yoursite.com/about"}', "xp": 20},
            {"n": 4, "title": "Use your real identity", "detail": "AI cross-references author names across the web. Use your real name for maximum E-E-A-T.", "xp": 10},
        ],
    },
    "no_author_bio": {
        "xp_reward": 50, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Write a 2-3 sentence bio", "detail": "Include: name, role, years of experience, key credential, and a link to LinkedIn.", "xp": 15},
            {"n": 2, "title": "Add the bio section", "detail": "Place it at the bottom of your article, before comments.", "code": '<div class="author-bio">\n  <img src="/images/author.jpg" alt="Name" width="64" height="64">\n  <div>\n    <strong>Your Name</strong>\n    <p>Your Name is a [title] with [X] years in [field]. They have [credential]. <a href="https://linkedin.com/in/you">LinkedIn</a></p>\n  </div>\n</div>', "xp": 25},
            {"n": 3, "title": "Add a profile photo", "detail": "A real photo builds trust. Avoid stock images or avatars.", "xp": 10},
        ],
    },
    "no_publish_date": {
        "xp_reward": 20, "difficulty": "easy", "estimated_minutes": 2,
        "steps": [
            {"n": 1, "title": "Add visible date", "detail": "Show the publish date near the title or author byline.", "code": '<time datetime="2025-01-15">January 15, 2025</time>', "xp": 10},
            {"n": 2, "title": "Add meta tag", "detail": "Add article:published_time to your <head>.", "code": '<meta property="article:published_time" content="2025-01-15T00:00:00Z">', "xp": 10},
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
            {"n": 2, "title": "Find authoritative sources", "detail": "Search Google Scholar, .gov, .edu sites for supporting evidence.", "xp": 20},
            {"n": 3, "title": "Add 3+ external links", "detail": "Link to the sources inline, using descriptive anchor text.", "xp": 25},
        ],
    },
    "no_trust_links": {
        "xp_reward": 50, "difficulty": "medium", "estimated_minutes": 15,
        "steps": [
            {"n": 1, "title": "Find high-authority sources", "detail": "Search for .gov, .edu, Wikipedia, or major publications that support your claims.", "xp": 15},
            {"n": 2, "title": "Link to them in your content", "detail": "Add 2-3 links to authoritative domains within your article body.", "xp": 25},
            {"n": 3, "title": "Use descriptive anchor text", "detail": "Link text should describe the source: 'according to the FDA guidelines' not 'click here'.", "xp": 10},
        ],
    },
    "low_source_diversity": {
        "xp_reward": 25, "difficulty": "easy", "estimated_minutes": 10,
        "steps": [
            {"n": 1, "title": "Audit your external links", "detail": "List all domains you link to. Are there fewer than 3 unique domains?", "xp": 5},
            {"n": 2, "title": "Add links to new domains", "detail": "Find 2-3 additional authoritative sources from different domains.", "xp": 20},
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
            {"n": 1, "title": "Create the file", "detail": "Create a file called llms.txt at your website root.", "xp": 10},
            {"n": 2, "title": "Add your content", "detail": "Use this template — customize with your brand info.", "code": "# Your Brand Name\n\n## About\nOne paragraph about what your company does.\n\n## Key Pages\n- Homepage: https://yoursite.com/\n- About: https://yoursite.com/about\n- Products: https://yoursite.com/products\n- Blog: https://yoursite.com/blog\n\n## Contact\n- Email: hello@yoursite.com\n- Twitter: @yourbrand", "xp": 30},
            {"n": 3, "title": "Upload to your server", "detail": "Upload so it's accessible at https://yoursite.com/llms.txt", "xp": 20},
            {"n": 4, "title": "Verify it's live", "detail": "Open https://yoursite.com/llms.txt in your browser — it should show as plain text.", "xp": 15},
        ],
    },
    "ai_bots_blocked": {
        "xp_reward": 80, "difficulty": "easy", "estimated_minutes": 5,
        "steps": [
            {"n": 1, "title": "Open robots.txt", "detail": "Go to https://yoursite.com/robots.txt and review the contents.", "xp": 5},
            {"n": 2, "title": "Add AI crawler rules", "detail": "Add allow rules for all major AI crawlers.", "code": "User-agent: GPTBot\nAllow: /\n\nUser-agent: Google-Extended\nAllow: /\n\nUser-agent: anthropic-ai\nAllow: /\n\nUser-agent: ClaudeBot\nAllow: /\n\nUser-agent: PerplexityBot\nAllow: /", "xp": 35},
            {"n": 3, "title": "Remove blocking rules", "detail": "Find and remove any Disallow rules targeting these bot names.", "xp": 20},
            {"n": 4, "title": "Check your CDN/WAF", "detail": "If using Cloudflare, check Security > Bots to ensure AI bots aren't blocked at the WAF level.", "xp": 20},
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
            {"n": 4, "title": "Post on Reddit and Medium", "detail": "Create valuable content mentioning your brand on both platforms.", "xp": 25},
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
    "no_medium_presence": {
        "xp_reward": 55, "difficulty": "medium", "estimated_minutes": 45,
        "steps": [
            {"n": 1, "title": "Create Medium account", "detail": "Use your real name + company title. Write a bio mentioning your brand.", "xp": 10},
            {"n": 2, "title": "Write a high-value article", "detail": "Title: '[Year] Guide to [Your Topic]'. Include stats, citations, and data.", "xp": 20},
            {"n": 3, "title": "Submit to a publication", "detail": "Submit to 'The Startup', 'Better Programming', or your niche's top publication.", "xp": 15},
            {"n": 4, "title": "Cross-link both ways", "detail": "Link from Medium → your site AND your site → Medium article.", "xp": 10},
        ],
    },

    # ── AI Visibility ──
    "not_in_google_ai": {
        "xp_reward": 65, "difficulty": "hard", "estimated_minutes": 60,
        "steps": [
            {"n": 1, "title": "Optimize for featured snippets", "detail": "Structure content as Q&A: question in H2, direct answer immediately below.", "xp": 20},
            {"n": 2, "title": "Target 'People Also Ask'", "detail": "Search your keywords on Google, note PAA questions, create content answering each.", "xp": 20},
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
    "no_medium_ai_presence": {
        "xp_reward": 50, "difficulty": "medium", "estimated_minutes": 45,
        "steps": [
            {"n": 1, "title": "Create your profile", "detail": "Use real name + credentials. Bio should establish expertise.", "xp": 10},
            {"n": 2, "title": "Publish your first article", "detail": "Include statistics, citations, expert insights. Mention brand 2-3 times naturally.", "xp": 20},
            {"n": 3, "title": "Submit to a publication", "detail": "Publications multiply reach 5-10x.", "xp": 10},
            {"n": 4, "title": "Cross-link for AI impact", "detail": "Link Medium → your site. Link your site → Medium. Add to social profiles.", "xp": 10},
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
