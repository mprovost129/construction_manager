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

## Foundation implemented (disabled)

`QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED` defaults to false. Keep it disabled on
Heritage until an approved test-company installation is ready. No connection
accounts or credentials are provisioned by a migration or by saving onboarding.

- `/integrations/quickbooks/desktop/connector/` accepts a restricted SOAP 1.1
  protocol only when enabled. Production requires HTTPS. Authentication uses a
  separate hashed password, database-backed throttling and a hashed 15-minute
  ticket; a new login invalidates the previous ticket and verification.
- The only generated qbXML is `CompanyQueryRq`. The current file's normalized
  Windows path hash must match before a query is returned, and its company name
  and request ID must match before verification is recorded. Responses with
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

## Remaining before connection testing and transfers

1. Confirm Desktop edition/year, hosting, test company file and Windows operator.
2. Complete controlled credential provisioning and a version-appropriate QWC
   installer/WSDL contract, then test against an actual supported Web Connector.
   The present tests use synthetic SOAP requests, not a running QuickBooks copy.
3. Strengthen durable company identity with the chosen QuickBooks file-identity
   mechanism and bind verification to connection configuration/credential
   revisions. Path plus company name is a preflight check, not sufficient to
   authorize production writes to a restored/replaced file at the same path.
4. Build and review customer/job, item/account/tax and numbering mappings, then
   implement invoice/credit payloads and reconciliation queries. Tie the exact
   payload/mapping revision to the existing ledger fingerprint before dispatch.
5. Validate PostgreSQL concurrent callbacks and exports, proxy/TLS behavior,
   throttling, credential rotation and reconciliation using a test company.
   Local automated tests currently use SQLite; they do not prove lock behavior
   across production workers. Disable the connection before changing its identity
   or credentials; clear verification and revoke its session when doing so.
6. Obtain the accounting administrator's review of test results and the first
   proposed live records before activating transfers. Nothing in this foundation
   sends financial records or changes Heritage's QuickBooks file.

Protocol reference: [Intuit Web Connector programmer's guide](https://static.developer.intuit.com/resources/QBWC_proguide.pdf).
