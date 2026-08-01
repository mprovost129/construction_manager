# Construction Manager Implementation Roadmap

Last updated: August 1, 2026

This is the authoritative implementation-status document for Construction Manager.
It translates the answers in [Construction Manager.md](./Construction%20Manager.md)
into delivered capabilities, remaining work, and release gates. Update this file in
the same change that materially changes a feature's status.

## Status definitions

- **Complete**: Implemented, exposed through the application, and covered by tests.
- **Partial**: A useful portion exists, but one or more confirmed requirements are missing.
- **Not started**: No operational implementation exists.
- **Deferred**: Intentionally excluded from the current release based on confirmed requirements.
- **Blocked**: Work cannot be represented as production-ready until the listed dependency or gate is satisfied.

Configuration keys, copy, mock screens, and legal descriptions do not make an
integration operational. A feature is not complete until its end-to-end behavior is
implemented and verified.

## Executive status

| Area | Status | Current position |
| --- | --- | --- |
| Authentication and company administration | Complete | Email authentication, invitations, roles, project assignments, password reset, and startup superuser bootstrap exist. |
| Project and client portal foundation | Complete | Projects, scoped access, dashboard/action center, messaging, activity history, and client invitations exist. |
| Documents and client uploads | Complete | Private documents, versions, downloads, configurable-approval-count decisions, client uploads, and email notifications exist. |
| Change orders | Partial | Draft, submission, configurable multi-approval decisions, edit, optional line items, revise/replace with a revision chain, and void (including approved orders) exist; automated financial reversal on void and a formal cost-code catalog do not (blocked on APP-1/APP-2). |
| Finish selections | Complete | Options, allowance math, publishing, client choice, overage flag, reopening, package grouping for multi-area choices, vendor/link/spec/image/lead-time option metadata, client custom-option requests routed to a change order, credit disposition tracking, and manual + scheduler-driven overdue reminders exist. |
| Schedule | Partial | Internal milestone/calendar workflow exists; dependencies, recurrence, and external calendar integration do not. |
| Notifications | Partial | Transactional email exists with project-level recipient preferences; per-event settings, reminders, and digest delivery do not. |
| Project pricing and financials | Not started | No product/material/labor/commission pricing engine, job-costing ledger, estimate, proposal, or project financial rollup exists. |
| Invoices and payment visibility | Partial | Local drafts, approved-change-order conversion, immutable company numbering, line items, totals, client-visible issued invoices, balances, status fields, notification, questions, and unpaid voiding exist. PDF download, selection-origin rules, QuickBooks synchronization, and payment import remain. Online payment is deferred. |
| QuickBooks Online | Partial | Company-scoped OAuth, encrypted tokens, capability/subscription discovery, stable project-to-customer mappings, and the first customer-sync slice exist. Live sandbox acceptance plus invoice, credit-memo, payment, and change-detection work remain. |
| Tasks and punch lists | Not started | Confirmed as required, but no models or workflow exist. |
| Two-factor authentication | Not started | Confirmed as optional per user/admin policy, but not implemented. |
| Public legal pages | Complete with launch action | Public EULA and privacy pages exist; real legal entity values and counsel review are still required before production submission. |
| Deployment foundation | Partial | Docker, Render-oriented startup, migrations, static assets, security settings, and environment examples exist; persistent uploads, availability, backups, and operations need production validation. |

## Confirmed product decisions

These decisions govern remaining implementation work:

- QuickBooks is the accounting source of truth.
- Exchange customers, invoices, credit memos, and payments in both directions.
- Invoices may originate in Construction Manager.
- An approved change order is sent or marked ready for QuickBooks when invoiced, not merely when approved.
- Admins resolve synchronization errors and conflicts.
- Construction projects map to QuickBooks customers; do not depend on QuickBooks Jobs.
- Begin with active projects and new transactions; no historical migration is required.
- QuickBooks sandbox credentials are strictly for development. Production must use production credentials and companies.
- Clients may view/download invoices, balances, and payment status, but cannot pay online in the first release.
- One authenticated client approval is sufficient for change orders by default; the required
  approval count is configurable per change order (and per document) for cases that need more.
- Clients can upload project files and photos; assigned internal users are notified.
- Email is sufficient for initial notifications.
- Internal users see only assigned projects, except company administrators who retain company-wide control.
- Supported internal roles are Admin, Manager, Project Manager, Staff, Office Manager, Real Estate Agent, and Accountant.
- Clients do not use the project schedule. Admins, managers, and project managers operate it.
- Selection overages are flagged and suggest a change order; they do not automatically create one.
- Completed selections can be reopened by authorized internal users, not by clients.
- Subcontractor login, subcontractor financial access, daily logs, and online payments are deferred.

## Delivered baseline

### Authentication, roles, and access

- Custom email-based user model and password-reset flow.
- Company and project invitation acceptance, resend, revoke, and access restoration.
- Company roles and project-specific internal assignments.
- Separate manage-project, manage-client, and notification permissions.
- Project-scoped queries and authorization checks across portal modules.
- Idempotent environment-backed Django superuser bootstrap for container startup.
- Brute-force login protection and production session/security configuration.

### Project portal

- Portfolio dashboard, project cards, search, status filtering, and action center.
- Project activity timeline and CSV export.
- Client-visible project detail and explicit project membership.
- Threaded project messages with replies and status management.
- Email notifications for implemented workflows.

### Documents and photos

- Private file storage, validated extensions, and configurable 25 MB limit.
- Document categories, client visibility, version history, and secure download authorization.
- Client approve/decline decisions and activity audit events.
- Configurable required-approval count per document, resolved from a per-client decision log.
- Dedicated client upload workflow for documents and images.
- Assigned internal-recipient notification behavior.

### Change orders

- Sequential project numbering.
- Draft creation/editing, price delta, cost delta, schedule delta, and reason fields.
- Optional itemization by category (product, material, labor, subcontractor, commission/markup,
  tax, allowance, other) with a free-text cost-code tag; totals recalculate automatically from
  line items whenever any exist.
- Client submission, authenticated approve/decline, comment, and notifications.
- Configurable required client approval count, resolved from a per-client decision log (a single
  approval remains sufficient by default).
- Revise-in-place and create-replacement actions after a decline, with a visible, linked revision
  chain between the original and its replacement.
- Void workflow and audit history, extended to approved change orders with a mandatory reason and
  a `requires_financial_reversal` flag for manual QuickBooks handling.
- QuickBooks handoff language aligned to the invoice event.

### Finish selections

- Sequential numbering, allowance, due date, option price/cost, recommendation, and ordering.
- Draft, publish, client choice, selected, reopened, and voided states.
- Client-facing allowance variance with internal-only estimated cost and margin.
- Overage flag and prefilled change-order call to action.
- Reopening by an authorized internal user with client re-notification.
- Optional `SelectionPackage` grouping so related area choices (e.g. all Kitchen selections)
  share a package view, mirroring Buildertrend's Group-of-Choices structure; each choice keeps
  its own allowance, options, and client pick.
- Option vendor, product link, specification, lead time, image, and attachment fields, served
  through authenticated download/inline views scoped to selection visibility.
- Client custom-option requests, with staff review routing directly into a prefilled change-order
  draft (no change order is created automatically).
- Credit disposition tracking (apply elsewhere, return at closing, retain as margin) for
  under-allowance choices, settable by authorized internal users and reset on reopen.
- Manual "Send reminder" action plus a `send_overdue_selection_reminders` management command for
  overdue open selections; the command needs an external scheduler in production (see Production
  blockers).

### Schedule

- Internal milestone list and calendar views.
- Start/end dates, status, notes, ordering, create, and update workflows.
- Schedule is unavailable to clients, accountants, and subcontractors.

### Invoices

- Local invoice drafts with due dates, client notes, itemized quantities/prices, tax, totals,
  amount paid, and balance due.
- Explicit conversion of an approved positive change order into a draft invoice, copying its line
  items and preserving a one-to-one source link. Zero/negative changes are reserved for the future
  credit-memo workflow.
- Company-wide sequential numbering assigned only at issue, with database uniqueness and
  application-level immutability for issued details, totals, and line items.
- Client-safe invoice list/detail views, issue email, payment-status display, and a prefilled
  invoice-question conversation link. No online-payment action is exposed.
- Unpaid issued-invoice voiding and draft disposal with audit events. A referenced change order
  cannot be voided until its draft is discarded or its unpaid issued invoice is voided.

### Legal and deployment

- Public `/legal/eula/` and `/legal/privacy/` pages with footer links.
- Environment-driven legal entity, contact, address, governing law, and effective date.
- Development/production environment examples, production HTTPS controls, database SSL option, and console-first logging.
- Docker static build settings, runtime migrations, and superuser bootstrap.
- Company-scoped QuickBooks OAuth foundation with rotatable Fernet token encryption,
  one-time state validation, authorization-code exchange, serialized token refresh,
  revoke-before-disconnect behavior, connection-state UI, and audit events.
- Live `CompanyInfo` and Preferences discovery, subscription-aware read/write capability
  flags, stable Project-to-QuickBooks-Customer mappings, sync tokens, last-known values,
  tombstones, and explicit entity ownership/conflict policies. QuickBooks Projects/Jobs
  are rejected as mapping targets.
- Administrator-triggered QuickBooks Customer create-or-match, QuickBooks-authoritative refresh,
  and explicit project-name-to-customer update actions; durable attempts; serialized per-company
  synchronization; stable write request IDs; current-sync-token updates; query pagination;
  throttling-aware retry scheduling; inactive-record tombstones; and an administrator
  retry/resolution queue. Deferred retries are exposed through the
  `retry_quickbooks_syncs` management command.
- Current automated baseline: 235 passing tests, Ruff clean, no pending migrations,
  and build-settings `collectstatic` passing as of this update. Django's expected
  development warning remains when QuickBooks credentials are intentionally unset.

## Partial feature gaps

### Change orders

- Voiding an approved change order sets a `requires_financial_reversal` flag for staff to action
  manually; there is no automated accounting reversal because no invoicing/financial ledger exists
  yet (blocked on [Phase APP-1](#phase-app-1-project-pricing-and-financial-ledger---p0) and
  [APP-2](#phase-app-2-invoices-and-client-visibility---p0)). Revisit once invoices exist so a void
  after invoicing can be rejected or trigger a credit memo instead of only a flag.
- Change order line items use a free-text cost-code tag, not a formal pricing/cost-code catalog;
  that catalog is also APP-1 scope.

### Schedule and notifications

- Restrict schedule creation and updates to the confirmed Admin, Manager, and Project Manager policy; the current assigned-internal-user access is broader.
- Add milestone dependencies and dependency-aware date changes.
- Add recurring schedule items.
- Add external calendar integration.
- Add per-event notification settings rather than one project-wide email preference.
- Add immediate versus digest delivery and overdue reminder preferences.

### Mobile experience

- Complete a phone-size workflow audit for client approvals, uploads, messaging, selections, and field use.
- Define whether offline access is required; the interview answer does not provide a testable offline requirement.

### Invoices and payment visibility

- Add downloadable invoice PDFs; the current portal provides authenticated HTML details only.
- Define the billable amount and credit behavior for selected finishes after APP-1 establishes the
  base-contract and allowance ledger. Approved positive change orders are the only automated
  invoice source today; staff can also create manual drafts.
- Add QuickBooks Invoice identity/sync-token mapping and both-direction synchronization before
  treating local payment-status fields as live accounting data.
- Import QuickBooks payments and credit applications. Until then, locally issued invoices display
  their full balance as due.

## Production blockers

The following must be resolved before representing the application as production-ready.

### Hosting and data operations

- Replace container-local private document storage with durable object storage. Render container files are not a safe system of record for customer documents.
- Confirm persistent production PostgreSQL, automated backups, restore testing, and retention.
- Confirm a production Redis service or remove runtime dependency on it.
- Configure and verify SMTP delivery, sender-domain authentication, bounce handling, and support/privacy mailboxes.
- Validate the selected hosting tier against Intuit's availability expectations; free-tier spin-down must not be represented as continuous production availability.
- Schedule `retry_quickbooks_syncs` on a persistent production worker or cron service; the
  Render free web service does not run this command automatically.
- Add production error monitoring, health checks, alerting, and an incident-response procedure.
- Establish an operational privacy-request, data-export, QuickBooks-disconnect, and deletion procedure matching the published Privacy Policy.

### Security and legal

- Implement optional two-factor authentication, authenticator-app enrollment, recovery codes, admin enforcement, and audit events.
- Provision a production-only QuickBooks token-encryption key in the Render secret store,
  document key rotation, and retain old keys during rotation until all stored tokens are re-encrypted.
- Configure real `LEGAL_*` values and obtain legal review of the EULA and Privacy Policy.
- Add a public support/help route and documented response process.
- Perform dependency, vulnerability, access-control, upload, and authorization testing before external users are admitted.

## QuickBooks Online roadmap

Current questionnaire truth as of this update:

| Question | Accurate answer today |
| --- | --- |
| API calls per customer | There is no periodic customer polling. Initial manual sync makes one exact-name Customer query plus, only when no match exists, one Customer create. A mapped manual refresh makes one Customer read. The separate outbound name update makes one Customer read and, only when the names differ, one sparse Customer update. A retry repeats the applicable operation; writes reuse the same Intuit `requestid`. Due retryable failures run only when `retry_quickbooks_syncs` is scheduled. Company refresh makes two reads (`CompanyInfo` and Preferences), and an expired access token adds one token-refresh call. Local invoice draft, issue, discard, and void actions currently make zero Intuit calls because Invoice synchronization is deliberately disabled. |
| Handles QBO edition feature gains/losses | Not yet a portal “Yes.” Subscription/preference capability changes and feature-not-supported errors are handled without deleting data, but the sandbox edition matrix remains. |
| Uses webhooks | No |
| Uses CDC | No |
| Operational connect/reconnect URL | Implemented and automated-tested; HTTPS deployment verification remains. |
| Operational disconnect URL | Implemented and automated-tested; HTTPS deployment verification remains. |
| Operational OAuth callback | Implemented and automated-tested; Intuit sandbox verification remains. |

Do not change an answer to "Yes" until the corresponding acceptance criteria below pass.

### Phase QB-1: Connection and security foundation - P0

- [x] Add one or more QuickBooks company connections per organization, including realm ID, environment, connection status, granted scopes, token expiry, and audit timestamps.
- [x] Encrypt access and refresh tokens at rest and prevent them from appearing in logs, admin pages, exceptions, or exports.
- [x] Implement OAuth state generation/validation and authorization-code exchange.
- [x] Implement authenticated connect/reconnect and callback routes.
- [x] Implement in-app disconnect through Intuit's revoke endpoint.
- [x] Implement a public disconnect landing page that confirms disconnection and explains reconnection.
- Add the exact production URLs to Intuit only after they return the intended response over HTTPS.
- [x] Prevent sandbox credentials or realm IDs from operating when `APP_ENVIRONMENT=production`.

Acceptance gate:

- Automated coverage passes for connect, token refresh, revoke, failed refresh/revoke,
  denied consent, expired/invalid state, key rotation, role enforcement, and realm conflicts.
- Still required: complete the same matrix with real Intuit sandbox companies, then verify
  the final HTTPS URLs after deployment.
- The UI displays environment, realm ID, connection state, timestamps, expiration, and
  safe error copy. Displaying the QuickBooks company name requires the QB-2 company-info read.

### Phase QB-2: Capability and mapping layer - P0

- [x] Read company/subscription information and detect supported capabilities without hard-coding an edition assumption.
- [x] Mark capability data stale after reconnect and re-evaluate through an administrator action; record observed feature-not-supported errors.
- [x] Gracefully disable unsupported/write-restricted operations while preserving existing local data.
- [x] Define stable mappings for Organization/Project to QuickBooks company/customer without using QuickBooks Jobs.
- [x] Persist customer external IDs, sync tokens, tombstones, last-synced values, ownership, and conflict policy. Define ownership/deletion policies for future invoice, credit-memo, and payment sync records.
- [x] Preserve QuickBooks as accounting source of truth while allowing invoices to originate only as local drafts before first sync.

Acceptance gate:

- Automated tests cover active/read-only subscription states, preference differences,
  feature-not-supported errors, mapping conflicts, QuickBooks Project/Job rejection,
  external deletion/tombstones, and history-preserving unlink.
- Still required: exercise representative Simple Start, Essentials, Plus, and Advanced
  sandbox companies and confirm gain/loss behavior against live responses.
- The implemented downgrade/error paths preserve local data and avoid automatic retry loops;
  scheduled synchronization is not enabled yet.

### Phase QB-3: Entity synchronization - P0

Implement in this order:

1. Customers
2. Invoices
3. Credit memos
4. Payments

Customer slice delivered:

- [x] Let an administrator create a QuickBooks customer from a local project or safely match an
  existing customer with the same display name.
- [x] Refresh an active customer mapping from QuickBooks, preserving QuickBooks as the source of
  truth after the first mapping.
- [x] Preserve inactive or missing external customers as tombstoned mappings instead of deleting
  accounting history.
- [x] Use stable per-operation `requestid` values for duplicate-safe writes, fetch the current
  `SyncToken` before sparse updates, paginate Customer queries, and serialize Customer operations
  per QuickBooks company and direction.
- [x] Record durable attempts and sanitized failures, defer retryable throttling/server failures
  with bounded exponential backoff, and provide administrator retry/resolution actions.
- [x] Provide manual Customer sync and the `retry_quickbooks_syncs` scheduled-command boundary.
- [ ] Verify create, existing-name match, refresh, inactive/missing customer, duplicate request,
  stale token, subscription downgrade, and throttling behavior against live Intuit sandbox
  companies.
- [x] Add an explicit administrator action that sends the current local project name to an already
  mapped QuickBooks customer. Normal sync remains QuickBooks-authoritative; the outbound action
  re-reads the customer, uses its latest `SyncToken`, and skips the write when the names match.
- [ ] Implement invoices, credit memos, and payments. These remain disabled until their local
  accounting models and reconciliation rules exist.

Invoice synchronization preparation:

- [x] Add a local Invoice and InvoiceLineItem foundation with immutable issue numbering, totals,
  source links, client visibility, balance/status fields, and safe draft/void lifecycles.
- [x] Require a client and at least one positive line before issue; preserve draft isolation and
  prevent source change-order reversal while an active invoice exists.
- [ ] Add QuickBooks Invoice mappings and create/read/update/void API operations. External Invoice
  writes remain disabled pending sandbox credentials and product/service Item mapping.

For each entity:

- Implement both-direction create/update behavior where supported by the confirmed workflow.
- Add idempotency, sync-token/concurrency handling, pagination, retry with backoff, and rate-limit handling.
- Record durable sync attempts, results, external IDs, and sanitized error details.
- Provide an admin error queue with retry and resolution actions.
- Ensure change orders become eligible for handoff only through the invoice workflow.
- Add a manual Sync action plus a safe scheduled/event-driven path.

Acceptance gate:

- Create, update, delete/void, duplicate delivery, stale version, partial failure, retry, and conflict tests pass for every entity.
- Reconciliation proves local and QuickBooks totals agree for test scenarios.

### Phase QB-4: Change detection and reconciliation - P1

- Choose and document the production change-detection design before answering Intuit's webhook/CDC questions.
- Preferred design: verified webhooks for prompt notification plus CDC or targeted queries for scheduled reconciliation.
- If using webhooks, implement a public endpoint, Intuit signature verification, replay protection, idempotent queueing, fast acknowledgement, and safe processing retries.
- If using CDC, persist high-water marks, respect the 30-day look-back constraint, split queries to stay below response limits, and process deletions.
- Add a nightly reconciliation job and manual recovery action.

Acceptance gate:

- Missed, delayed, duplicated, reordered, and replayed events do not lose or duplicate accounting records.
- Operational metrics report API call volume, success rate, throttling, sync lag, and unresolved errors per company.

## Application roadmap after the QuickBooks foundation

### Phase APP-1: Project pricing and financial ledger - P0

- Add a project pricing catalog and line items for products, materials, labor, commission/markup, tax, allowances, and cost codes.
- Add estimate/proposal generation and client-visible approval where required.
- Calculate base project total, selection overages/credits, change orders, invoices, payments, and remaining balance without treating estimates as accounting truth.
- Add staff financial views and client-safe financial summaries.
- Define rounding, tax, void/reversal, and audit rules.

### Phase APP-2: Invoices and client visibility - P0

- [x] Add local manual invoice drafts and drafts originating from approved positive change orders.
- [ ] Add selected-finish invoice origination after APP-1 defines whether to bill full option value,
  allowance variance, or an approved change order.
- [x] Add invoice status, immutable organization-wide numbering, line items, totals, and balances.
- [ ] Add credit application and QuickBooks mapping.
- [x] Let clients view invoice details, balances/payment status, and ask questions.
- [ ] Add authenticated downloadable invoice PDFs.
- Do not add online payment collection in this release.

### Phase APP-3: Tasks and punch lists - P1

- Add assignees, due dates, statuses, attachments, comments, reminders, and completion verification.
- Reuse project-scoped access and notification settings.
- Optimize create/update/complete/photo workflows for phones.

### Phase APP-4: Remaining workflow depth - P1

- Complete selection metadata, credit disposition, and reminders.
- Complete change-order line items and revision chains.
- Complete schedule dependencies, recurrence, and external calendar support.
- Complete configurable multi-client approval rules.
- Add notification event preferences and digests.
- Add CRM customer/contact management without a lead pipeline.

## Deferred scope

The following are intentionally outside the current release unless requirements change:

- Subcontractor portal and subcontractor financial visibility.
- Daily logs.
- Online payment processing.
- Historical Buildertrend or QuickBooks import.
- Formal e-signatures/DocuSign; authenticated approval remains sufficient initially.
- QuickBooks Desktop integration.
- Payroll integration.
- QuickBooks Self-Employed/Solopreneur and Intuit Enterprise Suite support.

## Release gates

### Internal pilot gate

- All migrations apply cleanly to a production-like database.
- Full automated suite, Ruff, Django system checks, and `collectstatic` pass.
- Durable document storage, SMTP, backups, monitoring, and restore procedures are verified.
- Permission matrix is manually tested for every role.
- Legal environment values are real and public pages return HTTP 200 without authentication.
- No known critical/high security findings remain.

### QuickBooks sandbox gate

- QB-1 through QB-4 acceptance criteria pass with sandbox credentials only.
- Supported entities reconcile across create/update/void/error scenarios.
- Connect, reconnect, disconnect, callback, webhook/CDC, and edition-change behaviors are documented for reviewers.
- Questionnaire answers match measured implementation behavior.

### Production/Intuit submission gate

- Production credentials are not requested or enabled as a substitute for unfinished integration work.
- Hosting meets Intuit availability and security expectations.
- Production OAuth URLs, EULA, Privacy Policy, support route, and disconnect instructions are public and stable.
- Security assessment evidence and incident-response contacts are ready.
- A controlled first production company can be connected, monitored, reconciled, and disconnected without developer intervention.

## Maintenance rule

Every feature change must update the relevant status, acceptance criteria, and current
questionnaire truth in this document. "Complete" requires code, user-facing behavior,
authorization coverage, tests, and operational support--not configuration alone.
