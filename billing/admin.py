from django.contrib import admin

from .models import Invoice, InvoiceLineItem


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0
    fields = ('category', 'description', 'quantity', 'unit_price', 'sort_order')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'display_number',
        'title',
        'project',
        'status',
        'total_amount',
        'amount_paid',
        'due_date',
    )
    list_filter = ('status', 'organization')
    search_fields = ('title', 'project__name')
    readonly_fields = tuple(field.name for field in Invoice._meta.fields)
    inlines = (InvoiceLineItemInline,)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
