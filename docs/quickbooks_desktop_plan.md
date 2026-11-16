# QuickBooks Desktop integration plan

Heritage uses QuickBooks Desktop. The launch does not require an accounting
connection. When implemented, the integration is limited to invoices and
credits.

## Recommended transport

Use Intuit QuickBooks Web Connector on the Windows computer or local network
where the QuickBooks company file is available. Web Connector initiates the
connection to the hosted application and exchanges qbXML through a SOAP web
service. The Render application must not attempt to open the company file
directly.

## Planned record flow

- Send issued invoices from the application to QuickBooks Desktop.
- Send or reconcile credit memos from the application to QuickBooks Desktop.
- Store the QuickBooks `TxnID`, `EditSequence`, company-file identity, and last
  successful synchronization time for every mapped record.
- Never send draft, voided, or unapproved records.
- Make every synchronization idempotent so retries cannot create duplicates.
- Preserve a complete status and error history without storing credentials or
  unredacted qbXML in ordinary application logs.

## Required decisions before development

- Exact QuickBooks Desktop product, edition, and year.
- Whether one company file or multiple company files are used.
- Customer/job structure used for construction projects.
- Product/service items and accounts used on invoice and credit lines.
- Sales-tax behavior.
- Whether QuickBooks or the application owns invoice numbering.
- Whether credits are created locally, imported from QuickBooks, or both.
- Which Windows computer will run Web Connector and who maintains it.

The existing QuickBooks Online OAuth screens remain disabled for Heritage and
must not be presented as compatible with QuickBooks Desktop.
