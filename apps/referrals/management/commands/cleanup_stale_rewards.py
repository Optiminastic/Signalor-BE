"""
Mark long-PENDING ReferralReward rows as REVOKED.

Rewards stay PENDING until the referrer's next subscription.renewed webhook
fires and consumes them via partial refund. If the referrer cancelled or never
subscribed, those rewards never fire and accumulate forever. This command is
the safety net.

Usage:
    python manage.py cleanup_stale_rewards
    python manage.py cleanup_stale_rewards --days 90
    python manage.py cleanup_stale_rewards --dry-run
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Subscription
from apps.referrals.models import ReferralReward


class Command(BaseCommand):
    help = "Mark stale PENDING referral rewards as REVOKED."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=180,
            help="PENDING rewards older than this many days are candidates (default: 180).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be revoked without changing anything.",
        )

    def handle(self, *args, **opts):
        days = opts["days"]
        dry = opts["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        candidates = list(
            ReferralReward.objects
            .filter(status=ReferralReward.Status.PENDING, created_at__lt=cutoff)
            .select_related("referral")
        )
        if not candidates:
            self.stdout.write(self.style.SUCCESS(f"No PENDING rewards older than {days} days."))
            return

        # Group by referrer to look up subscription status once per email.
        by_email: dict[str, list[ReferralReward]] = {}
        for r in candidates:
            by_email.setdefault(r.referrer_email, []).append(r)

        active_subs = set(
            Subscription.objects
            .filter(email__in=by_email.keys())
            .filter(Q(status="active") | Q(status="past_due"))
            .values_list("email", flat=True)
        )

        revoke_ids: list[int] = []
        kept = 0
        for email, rewards in by_email.items():
            if email in active_subs:
                # Referrer is still paying — keep their queue intact.
                kept += len(rewards)
                continue
            revoke_ids.extend(r.pk for r in rewards)

        if not revoke_ids:
            self.stdout.write(self.style.SUCCESS(
                f"All {kept} stale rewards belong to active subscribers — nothing to revoke."
            ))
            return

        if dry:
            self.stdout.write(self.style.WARNING(
                f"[dry-run] would revoke {len(revoke_ids)} rewards (kept {kept} for active subscribers)"
            ))
            for rid in revoke_ids[:20]:
                self.stdout.write(f"  reward_id={rid}")
            if len(revoke_ids) > 20:
                self.stdout.write(f"  ... and {len(revoke_ids) - 20} more")
            return

        now = timezone.now()
        ReferralReward.objects.filter(pk__in=revoke_ids).update(
            status=ReferralReward.Status.REVOKED,
            revoked_at=now,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Revoked {len(revoke_ids)} stale rewards. Kept {kept} for active subscribers."
        ))
