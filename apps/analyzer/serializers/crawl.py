"""Sitemap and schema audit payloads."""

from rest_framework import serializers

from ..models import (
    SchemaWatch,
    SchemaWatchPage,
    SitemapAudit,
    SitemapAuditPage,
)


class SitemapAuditPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SitemapAuditPage
        fields = [
            "id",
            "url",
            "path",
            "final_url",
            "state",
            "status_code",
            "redirect_count",
            "title",
            "meta_description",
            "h1_count",
            "word_count",
            "text_ratio",
            "content_length",
            "lcp_ms",
            "fcp_ms",
            "ttfb_ms",
            "server_ms",
            "resource_count",
            "resource_bytes",
            "link_count_total",
            "link_count_internal",
            "link_count_external",
            "jsonld_count",
            "has_canonical",
            "has_og",
            "is_noindex",
            "robots_allows_gptbot",
            "robots_allows_claudebot",
            "robots_allows_perplexitybot",
            "ai_score",
            "severity",
            "findings",
            "error_message",
            "checked_at",
        ]


class SitemapAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = SitemapAudit
        fields = [
            "id",
            "status",
            "progress",
            "sitemap_url",
            "crawl_limit",
            "total_urls",
            "indexed_count",
            "redirect_count",
            "queued_count",
            "failed_count",
            "avg_lcp_ms",
            "avg_fcp_ms",
            "avg_ttfb_ms",
            "avg_ai_score",
            "truncated",
            "discovered_url_count",
            "started_at",
            "finished_at",
            "created_at",
            "error_message",
        ]


class SchemaWatchPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchemaWatchPage
        fields = [
            "id",
            "url",
            "path",
            "page_kind",
            "status_code",
            "schema_types",
            "jsonld_count",
            "raw_jsonld",
            "severity",
            "issues",
            "fix_targets",
            "error_message",
            "checked_at",
        ]


class SchemaWatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchemaWatch
        fields = [
            "id",
            "status",
            "progress",
            "total_urls",
            "healthy_count",
            "warn_count",
            "broken_count",
            "discovered_from_sitemap",
            "started_at",
            "finished_at",
            "created_at",
            "error_message",
        ]

