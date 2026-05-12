from django.urls import path

from .views import PartnerAttributeView, PartnerTrackView

urlpatterns = [
    path("track/", PartnerTrackView.as_view(), name="partners-track"),
    path("attribute/", PartnerAttributeView.as_view(), name="partners-attribute"),
]
