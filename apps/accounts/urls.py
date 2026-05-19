from django.urls import path

from .views import (
    CancelTerminationView,
    CreateCheckoutSessionView,
    DeleteAccountView,
    DodoWebhookView,
    DownloadInvoiceView,
    InvoiceListView,
    PlanListView,
    PlanPricesView,
    ProfilePhotoView,
    ProfileView,
    SubscriptionStatusView,
    TerminateAccountView,
    UsageView,
)

app_name = "accounts"

urlpatterns = [
    path("plans/", PlanListView.as_view(), name="plan-list"),
    path("payments/create-checkout/", CreateCheckoutSessionView.as_view(), name="create-checkout"),
    path("payments/plan-prices/", PlanPricesView.as_view(), name="plan-prices"),
    path("payments/status/", SubscriptionStatusView.as_view(), name="subscription-status"),
    path("payments/usage/", UsageView.as_view(), name="usage"),
    path("payments/invoice/", DownloadInvoiceView.as_view(), name="download-invoice"),
    path("payments/invoices/", InvoiceListView.as_view(), name="invoice-list"),
    path("payments/webhook/", DodoWebhookView.as_view(), name="dodo-webhook"),
    path("account/terminate/", TerminateAccountView.as_view(), name="terminate-account"),
    path("account/cancel-termination/", CancelTerminationView.as_view(), name="cancel-termination"),
    path("account/delete/", DeleteAccountView.as_view(), name="delete-account"),
    path("account/profile/", ProfileView.as_view(), name="profile"),
    path("account/profile/photo/", ProfilePhotoView.as_view(), name="profile-photo"),
]
