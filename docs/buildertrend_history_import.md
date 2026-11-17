# Reviewed Buildertrend history imports

Use `python manage.py import_buildertrend_history --file /private/staged-records.json --organization COMPANY_SLUG --actor EXISTING_ADMIN_EMAIL` to validate a reviewed version 1 staging file. This runs model validation and reconciliation inside a transaction that is rolled back. Add `--apply` only after the preview matches the approved source counts and signed totals.

The importer accepts historical approved change orders and selected selections. The source approval remains distinct from an authenticated Heritage 360 decision. Imported records are read-only in the project interface, and cannot be invoiced, reopened or approved through the native workflows. Original exports and source references are evidence; OCR specifications and dates require verification against the original PDF.

Approved imported change orders contribute to the approved change-order rollup. Signed selection totals are stored separately, including allowance credits, and do not create invoices, credits or automatic changes to the contract. Unknown original contract amounts remain blank. Historical invoicing/payment status is not established by these exports.

An active company administrator must be the import actor. No users, invitations, PM assignments, notifications, invoices, payments, signatures or QuickBooks records are created. The audit identifies the import actor and time, not a historical approver. Source PM names are retained pending account matching and assignment.

The organization is locked while the entire batch is imported. Each source project has a stable key and source fingerprint. A repeat of the exact source verifies the existing record counts, keys and totals without duplicating records. Changed sources, name/code collisions, malformed amounts, failed validation and reconciliation mismatches abort the entire transaction. There is no automatic overwrite or delete mode.

Keep staging JSON, original documents and reconciliation receipts out of Git. Upload the original project exports to private project Documents, with client visibility chosen after reviewing the files. A company-wide jobsite export contains other clients and must not be shared through an individual client's portal. Referenced plans, vendor attachments and images not included in the source exports need separate uploads.
