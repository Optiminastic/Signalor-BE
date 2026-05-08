from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Partner, PartnerAttribution, PartnerCommission, PartnerPayout


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = (
        "code", "email", "name", "status", "commission_percent",
        "total_earned", "pending_owed", "created_at",
    )
    list_filter = ("status", "payout_method")
    search_fields = ("code", "email", "name")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "name", "code", "status", "commission_percent")}),
        ("Payout", {"fields": ("payout_method", "payout_details")}),
        ("Internal", {"fields": ("notes", "created_at", "updated_at")}),
    )
    actions = ["generate_missing_codes"]

    def get_changeform_initial_data(self, request):
        return {"code": Partner.generate_unique_code()}

    @admin.display(description="Total earned")
    def total_earned(self, obj):
        agg = obj.commissions.filter(
            status__in=[PartnerCommission.Status.PENDING, PartnerCommission.Status.PAID]
        ).aggregate(s=Sum("commission_amount"))
        return agg["s"] or 0

    @admin.display(description="Pending owed")
    def pending_owed(self, obj):
        agg = obj.commissions.filter(
            status=PartnerCommission.Status.PENDING
        ).aggregate(s=Sum("commission_amount"))
        return agg["s"] or 0

    @admin.action(description="Generate missing codes for selected partners")
    def generate_missing_codes(self, request, queryset):
        n = 0
        for p in queryset.filter(code=""):
            p.code = Partner.generate_unique_code()
            p.save(update_fields=["code"])
            n += 1
        self.message_user(request, f"Generated codes for {n} partners.", messages.SUCCESS)


@admin.register(PartnerAttribution)
class PartnerAttributionAdmin(admin.ModelAdmin):
    list_display = ("email", "partner", "attributed_at", "expires_at", "is_active")
    search_fields = ("email", "partner__code", "partner__email")
    list_filter = ("partner",)
    readonly_fields = ("attributed_at",)

    @admin.display(boolean=True, description="Active")
    def is_active(self, obj):
        return obj.is_active


@admin.register(PartnerCommission)
class PartnerCommissionAdmin(admin.ModelAdmin):
    list_display = (
        "partner", "referee_email", "commission_amount", "currency",
        "status", "payout", "created_at",
    )
    list_filter = ("status", "currency", "partner")
    search_fields = ("partner__code", "referee_email", "payment_id")
    readonly_fields = (
        "partner", "attribution", "referee_email", "payment_id",
        "gross_amount", "post_discount_amount", "commission_percent_snapshot",
        "commission_amount", "currency", "created_at", "updated_at",
    )
    actions = ["mark_paid_and_create_payout", "mark_cancelled"]

    @admin.action(description="Mark selected as PAID and create a Payout row")
    def mark_paid_and_create_payout(self, request, queryset):
        eligible = queryset.filter(status=PartnerCommission.Status.PENDING)
        by_partner = {}
        for c in eligible:
            by_partner.setdefault((c.partner_id, c.currency), []).append(c)

        if not by_partner:
            self.message_user(request, "No PENDING commissions selected.", messages.WARNING)
            return

        created_payouts = 0
        with transaction.atomic():
            for (partner_id, currency), commissions in by_partner.items():
                partner = commissions[0].partner
                total = sum((c.commission_amount for c in commissions), start=type(commissions[0].commission_amount)("0"))
                payout = PartnerPayout.objects.create(
                    partner=partner,
                    amount=total,
                    currency=currency,
                    method=partner.payout_method,
                    paid_at=timezone.now(),
                    notes=f"Bulk payout for {len(commissions)} commission(s) — admin action.",
                )
                for c in commissions:
                    c.status = PartnerCommission.Status.PAID
                    c.payout = payout
                    c.save(update_fields=["status", "payout"])
                created_payouts += 1

        self.message_user(
            request,
            f"Marked commissions paid; created {created_payouts} payout row(s). "
            f"Open each Payout to add the bank/Wise reference.",
            messages.SUCCESS,
        )

    @admin.action(description="Mark selected as CANCELLED (refund/chargeback/fraud)")
    def mark_cancelled(self, request, queryset):
        n = queryset.filter(status=PartnerCommission.Status.PENDING).update(
            status=PartnerCommission.Status.CANCELLED,
        )
        self.message_user(request, f"Cancelled {n} commission(s).", messages.SUCCESS)


@admin.register(PartnerPayout)
class PartnerPayoutAdmin(admin.ModelAdmin):
    list_display = ("partner", "amount", "currency", "method", "paid_at", "reference")
    list_filter = ("method", "currency", "partner")
    search_fields = ("partner__code", "partner__email", "reference")
    readonly_fields = ("created_at",)
