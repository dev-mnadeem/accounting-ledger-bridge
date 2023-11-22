"""URL configuration.

``/initiate_ledger_process/`` and the QuickBooks callback path are kept at the
addresses the original project registered them at, so an existing QuickBooks
app's configured redirect URI keeps working. Everything else is new surface for
the four read operations the ledger contract defines.
"""

from django.contrib import admin
from django.urls import path

from api import views

urlpatterns = [
    path("admin/", admin.site.urls),

    # Health / discovery
    path("healthz", views.health, name="health"),
    path("ledger/providers/", views.list_providers, name="ledger-providers"),

    # OAuth round trip
    path("initiate_ledger_process/", views.initiate_ledger_process,
         name="initiate_ledger_process"),
    path("accounts/quickbooks/login/callback/", views.ledger_callback,
         {"provider": "quickbooks"}, name="quickbooks_callback"),
    path("ledger/<str:provider>/callback/", views.ledger_callback,
         name="ledger_callback"),

    # Reads
    path("ledger/<str:provider>/bills/", views.list_bills, name="ledger-bills"),
    path("ledger/<str:provider>/bills/<str:bill_id>/", views.get_bill, name="ledger-bill"),
    path("ledger/<str:provider>/vendors/", views.list_vendors, name="ledger-vendors"),
    path("ledger/<str:provider>/vendors/<str:vendor_id>/", views.get_vendor,
         name="ledger-vendor"),
]
