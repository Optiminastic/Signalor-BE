from django.urls import path

from .views import (
    CreateCheckoutSessionView,
    SubscriptionStatusView,
    StripeWebhookView,
)

app_name = "accounts"

urlpatterns = [
    path("payments/create-checkout/", CreateCheckoutSessionView.as_view(), name="create-checkout"),
    path("payments/status/", SubscriptionStatusView.as_view(), name="subscription-status"),
    path("payments/webhook/", StripeWebhookView.as_view(), name="stripe-webhook"),
]
