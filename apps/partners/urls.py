from django.urls import path

from .views import (
    PartnerApplyView,
    PartnerAttributeView,
    PartnerExistsView,
    PartnerMeView,
    PartnerPublicStatsView,
    PartnerTrackView,
)

urlpatterns = [
    path("track/", PartnerTrackView.as_view(), name="partners-track"),
    path("attribute/", PartnerAttributeView.as_view(), name="partners-attribute"),
    path("apply/", PartnerApplyView.as_view(), name="partners-apply"),
    path("stats/", PartnerPublicStatsView.as_view(), name="partners-stats"),
    path("exists/", PartnerExistsView.as_view(), name="partners-exists"),
    path("me/", PartnerMeView.as_view(), name="partners-me"),
]
