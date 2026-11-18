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

## Required decisions before live setup

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

## Verification connection implemented

`QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED` defaults to true for verification only.
Setting it false disables both machine callbacks and browser provisioning. No connection
accounts or credentials are provisioned by a migration or by saving onboarding.

- `/integrations/quickbooks/desktop/connector/` accepts a restricted SOAP 1.1
  protocol only when enabled. Production requires HTTPS. Authentication uses a
  separate hashed password, database-backed throttling and a hashed 15-minute
  ticket; a login for a queued check invalidates the previous ticket and verification.
  Idle logins receive no work. Callbacks are bound to configuration revisions.
- The only generated qbXML is `CompanyQueryRq`. The current file's normalized
  Windows path hash must match before a query is returned, and its company name
  and request ID, OwnerID and FileID must match before verification is recorded. Responses with
  errors, mismatches, unsolicited or conflicting results fail closed. XML has
  byte/node limits and rejects DTDs and entities. Raw SOAP and company paths are
  not stored by this integration or included in returned errors.
- A private export ledger reserves each issued invoice or credit once, binds it
  to a company file and content fingerprint, and records request ID, TxnID and
  EditSequence. Preparing/starting a transfer requires a company administrator
  and a recent verification. A started delivery becomes uncertain immediately;
  it cannot be retried automatically. Matching receipts are idempotent and
  conflicting receipts need reconciliation. There is no HTTP export endpoint,
  accounting write request generator, or transfer worker in this release.

## Installation and remaining validation

The shared [accountant onboarding flow](accountant_onboarding.md) includes the
private QWC download, restricted SOAP/WSDL contract, public generic support page,
password provisioning with reauthentication, shared status, queued checks and
disconnect/reset. Separate per-person view/manage/check grants are enforced on
the server. Checklist completion does not provision credentials.

The supplied photo identifies Desktop Pro Plus 2024 R22P (64-bit). The operator
must confirm storage/sync, administrator availability and the intended file.
A test-copy registration is bound to that copy; moving to another file requires
site-owner review. One company connection is active at a time. No migration
creates an active connector account or chooses its password.

1. Install the generated QWC using the accountant's actual Web Connector and
   confirm the first company verification. Automated tests use synthetic SOAP;
   they do not establish compatibility with the installed Windows environment.
2. Validate PostgreSQL concurrent callbacks and credential revocation. SQLite
   tests cover stale revisions and constraints, not production lock contention.
3. Review restored/copied-file handling before permitting writes. FileID can be
   copied with a backup; name, path and registration checks alone are not an
   absolute guarantee that a restored file is the original accounting database.
4. Build customer/job, item/account/tax and numbering mappings, then implement
   invoice/credit payloads and reconciliation queries. Bind the exact mapping
   and payload revision to the ledger fingerprint before dispatch.
5. Validate transfers and uncertain-delivery reconciliation in a separate test
   company. Obtain accountant review of results and proposed live records before
   enabling transfers. There is still no financial write generator or worker.

Protocol reference: [Intuit Web Connector programmer's guide](https://static.developer.intuit.com/resources/QBWC_proguide.pdf).
