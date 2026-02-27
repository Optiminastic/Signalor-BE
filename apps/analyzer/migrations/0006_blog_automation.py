from datetime import time

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0002_alter_organization_options_organization_updated_at_and_more"),
        ("analyzer", "0005_usergamification_useraction"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlogAutomationConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_email", models.EmailField(db_index=True, max_length=254)),
                ("site_url", models.URLField(max_length=2048)),
                ("topic", models.CharField(default="AI search visibility strategy", max_length=255)),
                ("keywords", models.JSONField(blank=True, default=list)),
                ("frequency_per_day", models.PositiveSmallIntegerField(default=1)),
                ("publish_time", models.TimeField(default=time(9, 0))),
                ("mode", models.CharField(choices=[("auto_publish", "Auto Publish"), ("review_before_publish", "Review Before Publish")], default="review_before_publish", max_length=30)),
                ("publish_provider", models.CharField(choices=[("wordpress", "WordPress"), ("shopify", "Shopify"), ("none", "None")], default="none", max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("last_queued_for", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("analysis_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="blog_automation_configs", to="analyzer.analysisrun")),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="blog_automation_configs", to="organizations.organization")),
            ],
            options={
                "ordering": ["-updated_at"],
                "indexes": [models.Index(fields=["user_email", "is_active"], name="analyzer_bl_user_em_4897d8_idx")],
                "constraints": [models.UniqueConstraint(fields=("user_email", "site_url"), name="unique_blog_config_per_user_site")],
            },
        ),
        migrations.CreateModel(
            name="BlogAutomationJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_email", models.EmailField(db_index=True, max_length=254)),
                ("scheduled_for", models.DateTimeField(db_index=True)),
                ("provider", models.CharField(choices=[("wordpress", "WordPress"), ("shopify", "Shopify"), ("none", "None")], default="none", max_length=20)),
                ("mode", models.CharField(choices=[("auto_publish", "Auto Publish"), ("review_before_publish", "Review Before Publish")], default="review_before_publish", max_length=30)),
                ("status", models.CharField(choices=[("scheduled", "Scheduled"), ("draft", "Draft"), ("needs_review", "Needs Review"), ("published", "Published"), ("failed", "Failed")], default="scheduled", max_length=20)),
                ("topic", models.CharField(blank=True, default="", max_length=255)),
                ("keywords", models.JSONField(blank=True, default=list)),
                ("title", models.CharField(blank=True, default="", max_length=300)),
                ("slug", models.CharField(blank=True, default="", max_length=120)),
                ("meta_description", models.CharField(blank=True, default="", max_length=180)),
                ("excerpt", models.TextField(blank=True, default="")),
                ("content_markdown", models.TextField(blank=True, default="")),
                ("tags", models.JSONField(blank=True, default=list)),
                ("external_post_id", models.CharField(blank=True, default="", max_length=120)),
                ("external_post_url", models.URLField(blank=True, default="", max_length=2048)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("analysis_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="blog_automation_jobs", to="analyzer.analysisrun")),
                ("config", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="jobs", to="analyzer.blogautomationconfig")),
            ],
            options={
                "ordering": ["scheduled_for"],
                "indexes": [
                    models.Index(fields=["user_email", "status"], name="analyzer_bl_user_em_158e58_idx"),
                    models.Index(fields=["scheduled_for", "status"], name="analyzer_bl_schedul_d561a8_idx"),
                ],
                "constraints": [models.UniqueConstraint(fields=("config", "scheduled_for"), name="unique_scheduled_slot_per_blog_config")],
            },
        ),
    ]
