from django.contrib import admin

from .models import PricingDripState, DripSendLog


@admin.register(PricingDripState)
class PricingDripStateAdmin(admin.ModelAdmin):
    list_display = ("email", "current_step", "suppressed", "entered_at", "last_sent_at")
    list_filter = ("suppressed", "current_step", "cms_platform")
    search_fields = ("email", "domain", "first_name")
    readonly_fields = ("entered_at", "updated_at")


@admin.register(DripSendLog)
class DripSendLogAdmin(admin.ModelAdmin):
    list_display = ("state", "step", "subject_variant", "sent_at", "success")
    list_filter = ("step", "subject_variant", "success")
    search_fields = ("state__email", "subject")
    readonly_fields = ("sent_at",)
