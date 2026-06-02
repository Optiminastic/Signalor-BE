"""Drop the orphaned `mentioned_competitors` jsonb column on analyzer_prompttrack.

The column was added by an earlier model iteration that has since been removed
from `apps/analyzer/models.PromptTrack`, but no migration was generated to drop
it. Because it's NOT NULL with no default, any new PromptTrack insert via the
ORM fails with `null value in column 'mentioned_competitors' ... violates not-null
constraint`. The competitor-mention surface now lives in PromptCitation
(is_competitor flag) and is computed at read time by CompetitorPromptListView.
"""

from django.db import migrations


class Migration(migrations.Migration):

    # Re-pinned onto the current leaf: this migration came over from tushar-05
    # depending on 0036_drop_orphaned_tables, which is NOT part of staging's
    # lineage (that branch's 0036 drops tables that are live models here). The
    # only operation we want is the column drop below, so we chain it as a new
    # tail after the latest merge.
    dependencies = [
        ("analyzer", "0045_merge_20260514_1425"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE analyzer_prompttrack "
                "DROP COLUMN IF EXISTS mentioned_competitors;"
            ),
            reverse_sql=(
                "ALTER TABLE analyzer_prompttrack "
                "ADD COLUMN IF NOT EXISTS mentioned_competitors jsonb "
                "NOT NULL DEFAULT '[]'::jsonb;"
            ),
        ),
    ]
