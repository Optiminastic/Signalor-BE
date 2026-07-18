"""AI crawler user-agent detection for the Crawler Logs feature.

The ingest endpoint re-derives the bot from the reported user agent with
`detect_bot`, so clients can never claim arbitrary bot identities and ordinary
human traffic is never stored.
"""

# Ordered: first substring match wins. Keys are stable bot ids.
AI_CRAWLERS: list[tuple[str, str, str]] = [
    # (bot key, UA substring lowercase, display label)
    ("gptbot", "gptbot", "GPT Bot (OpenAI)"),
    ("oai-searchbot", "oai-searchbot", "SearchBot (OpenAI)"),
    ("chatgpt-user", "chatgpt-user", "ChatGPT User (OpenAI)"),
    ("claudebot", "claudebot", "Claude Bot (Anthropic)"),
    ("claude-user", "claude-user", "Claude User (Anthropic)"),
    ("claude-searchbot", "claude-searchbot", "Claude SearchBot (Anthropic)"),
    ("anthropic-ai", "anthropic-ai", "Anthropic AI"),
    ("perplexitybot", "perplexitybot", "Perplexity Bot"),
    ("perplexity-user", "perplexity-user", "Perplexity User"),
    ("google-extended", "google-extended", "Google Extended (Gemini)"),
    ("googleother", "googleother", "GoogleOther"),
    ("bytespider", "bytespider", "Bytespider (ByteDance)"),
    ("ccbot", "ccbot", "CCBot (Common Crawl)"),
    ("meta-externalagent", "meta-externalagent", "Meta External Agent"),
    ("amazonbot", "amazonbot", "Amazonbot"),
    ("applebot-extended", "applebot-extended", "Applebot Extended"),
    ("mistral", "mistralai", "Mistral Bot"),
    ("deepseek", "deepseekbot", "DeepSeek Bot"),
    ("grok", "grokbot", "Grok Bot (xAI)"),
]

BOT_LABELS = {key: label for key, _needle, label in AI_CRAWLERS}


def detect_bot(user_agent: str) -> str | None:
    """Bot key for an AI crawler user-agent, or None for ordinary traffic."""
    ua = (user_agent or "").lower()
    if not ua:
        return None
    for key, needle, _label in AI_CRAWLERS:
        if needle in ua:
            return key
    return None
