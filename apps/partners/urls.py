from django.urls import path

from .views import (
    PartnerApplyView,
    PartnerAttributeView,
    PartnerPublicStatsView,
    PartnerTrackView,
)

urlpatterns = [
    path("track/", PartnerTrackView.as_view(), name="partners-track"),
    path("attribute/", PartnerAttributeView.as_view(), name="partners-attribute"),
    path("apply/", PartnerApplyView.as_view(), name="partners-apply"),
    path("stats/", PartnerPublicStatsView.as_view(), name="partners-stats"),
]
