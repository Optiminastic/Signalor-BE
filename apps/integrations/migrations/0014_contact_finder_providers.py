from django.db import migrations, models


class Migration(migrations.Migration):
    """Choices only (Hunter, Apollo): no database change."""

    dependencies = [
        ("integrations", "0013_gmail_provider"),
    ]

    operations = [
        migrations.AlterField(
            model_name="integration",
            name="provider",
            field=models.CharField(
                choices=[
                    ("google_analytics", "Google Analytics"),
                    ("google_search_console", "Google Search Console"),
                    ("shopify", "Shopify"),
                    ("wordpress", "WordPress"),
                    ("woocommerce", "WooCommerce"),
                    ("webflow", "Webflow"),
                    ("nextjs", "Next.js"),
                    ("framer", "Framer"),
                    ("github", "GitHub"),
                    ("slack", "Slack"),
                    ("gmail", "Gmail"),
                    ("hunter", "Hunter"),
                    ("apollo", "Apollo"),
                ],
                max_length=30,
            ),
        ),
    ]
