from django.urls import path

from .views import BulkMetadataView, DeployView, RegisterView

app_name = "public_api_nextjs"

urlpatterns = [
    path("register/", RegisterView.as_view(), name="nextjs-register"),
    path("deploy/", DeployView.as_view(), name="nextjs-deploy"),
    path("metadata/bulk/", BulkMetadataView.as_view(), name="nextjs-metadata-bulk"),
]
