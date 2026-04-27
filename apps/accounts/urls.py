from django.urls import path

from .views import (
    CreateCheckoutSessionView,
    SubscriptionStatusView,
    UsageView,
    DownloadInvoiceView,
    DodoWebhookView,
    PlanListView,
    TerminateAccountView,
    CancelTerminationView,
    DeleteAccountView,
)

app_name = "accounts"

urlpatterns = [
    path("plans/", PlanListView.as_view(), name="plan-list"),
    path("payments/create-checkout/", CreateCheckoutSessionView.as_view(), name="create-checkout"),
    path("payments/status/", SubscriptionStatusView.as_view(), name="subscription-status"),
    path("payments/usage/", UsageView.as_view(), name="usage"),
    path("payments/invoice/", DownloadInvoiceView.as_view(), name="download-invoice"),
    path("payments/webhook/", DodoWebhookView.as_view(), name="dodo-webhook"),
    path("account/terminate/", TerminateAccountView.as_view(), name="terminate-account"),
    path("account/cancel-termination/", CancelTerminationView.as_view(), name="cancel-termination"),
    path("account/delete/", DeleteAccountView.as_view(), name="delete-account"),
]
