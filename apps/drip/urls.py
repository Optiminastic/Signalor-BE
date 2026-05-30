from django.urls import path

from .views import CheckoutStartedView, PricingViewedView, UnsubscribeView

urlpatterns = [
    path("pricing-viewed/", PricingViewedView.as_view(), name="drip-pricing-viewed"),
    path("checkout-started/", CheckoutStartedView.as_view(), name="drip-checkout-started"),
    path("unsubscribe/", UnsubscribeView.as_view(), name="drip-unsubscribe"),
]
