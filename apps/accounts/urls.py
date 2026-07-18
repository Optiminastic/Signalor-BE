from django.urls import path

from .agency_views import (
    AgencyMemberDetailView,
    AgencyMemberListView,
    AgencyRoleView,
)
from .enterprise import EnterpriseLeadCreateView
from .views import (
    AccountTypeView,
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
    path("account/type/", AccountTypeView.as_view(), name="account-type"),
    path("account/profile/", ProfileView.as_view(), name="profile"),
    path("account/profile/photo/", ProfilePhotoView.as_view(), name="profile-photo"),
    path("enterprise/lead/", EnterpriseLeadCreateView.as_view(), name="enterprise-lead"),
    # Agency team management (role-based access)
    path("agency/role/", AgencyRoleView.as_view(), name="agency-role"),
    path("agency/members/", AgencyMemberListView.as_view(), name="agency-members"),
    path("agency/members/invite/", AgencyMemberListView.as_view(), name="agency-members-invite"),
    path("agency/members/<int:member_id>/", AgencyMemberDetailView.as_view(), name="agency-member-detail"),
]
