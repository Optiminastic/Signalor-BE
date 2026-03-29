from django.urls import path

from .views import (
    CreateCheckoutSessionView,
    SubscriptionStatusView,
    DodoWebhookView,
    TerminateAccountView,
    CancelTerminationView,
    DeleteAccountView,
)

app_name = "accounts"

urlpatterns = [
    path("payments/create-checkout/", CreateCheckoutSessionView.as_view(), name="create-checkout"),
    path("payments/status/", SubscriptionStatusView.as_view(), name="subscription-status"),
    path("payments/webhook/", DodoWebhookView.as_view(), name="dodo-webhook"),
    path("account/terminate/", TerminateAccountView.as_view(), name="terminate-account"),
    path("account/cancel-termination/", CancelTerminationView.as_view(), name="cancel-termination"),
    path("account/delete/", DeleteAccountView.as_view(), name="delete-account"),
]
