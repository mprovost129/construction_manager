from django.urls import path

from . import views

app_name = 'billing'

urlpatterns = [
    path(
        'projects/<int:project_id>/invoices/',
        views.InvoiceListView.as_view(),
        name='invoice_list',
    ),
    path(
        'projects/<int:project_id>/invoices/new/',
        views.InvoiceCreateView.as_view(),
        name='invoice_create',
    ),
    path(
        'projects/<int:project_id>/invoices/from-change-order/<int:change_order_id>/',
        views.InvoiceFromChangeOrderCreateView.as_view(),
        name='invoice_from_change_order',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/',
        views.InvoiceDetailView.as_view(),
        name='invoice_detail',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/pdf/',
        views.InvoicePDFDownloadView.as_view(),
        name='invoice_pdf',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/edit/',
        views.InvoiceUpdateView.as_view(),
        name='invoice_edit',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/issue/',
        views.InvoiceIssueView.as_view(),
        name='invoice_issue',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/discard/',
        views.InvoiceDiscardView.as_view(),
        name='invoice_discard',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/void/',
        views.InvoiceVoidView.as_view(),
        name='invoice_void',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/payments/new/',
        views.PaymentCreateView.as_view(),
        name='payment_create',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/payments/<int:payment_id>/delete/',
        views.PaymentDeleteView.as_view(),
        name='payment_delete',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/lines/new/',
        views.InvoiceLineItemCreateView.as_view(),
        name='invoice_line_create',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/lines/<int:line_item_id>/edit/',
        views.InvoiceLineItemUpdateView.as_view(),
        name='invoice_line_edit',
    ),
    path(
        'projects/<int:project_id>/invoices/<int:invoice_id>/lines/<int:line_item_id>/delete/',
        views.InvoiceLineItemDeleteView.as_view(),
        name='invoice_line_delete',
    ),
]
