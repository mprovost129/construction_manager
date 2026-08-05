# Construction Manager Implementation Roadmap

Last updated: August 5, 2026

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
| Change orders | Partial | Draft, submission, configurable multi-approval decisions, edit, optional cost-coded line items, revise/replace with a revision chain, void (including approved orders), and selection-allowance-credit application (a change order sourced from a selection's unused credit, validated and capped against that credit) exist; automated financial reversal on void does not — voiding still only sets a manual-follow-up flag. |
| Finish selections | Complete | Options, allowance math, publishing, client choice, overage flag, reopening, package grouping for multi-area choices, vendor/link/spec/image/lead-time option metadata, client custom-option requests routed to a change order, credit disposition tracking, and manual + scheduler-driven overdue reminders exist. |
| Schedule | Partial | Internal milestone/calendar workflow exists; dependencies, recurrence, and external calendar integration do not. |
| Notifications | Partial | Transactional email exists with project-level recipient preferences; per-event settings, reminders, and digest delivery do not. |
| Project pricing and financials | Partial | An organization-scoped cost-code catalog (now also mappable to a QuickBooks Item), an estimate/proposal workflow (with a revision chain) and client approval that sets a project's fixed, tax-inclusive contract amount, an organization-level tax-rate engine applied to invoices and estimates, a staff+client-safe financial rollup view (contract, change orders, selection overage/credit, invoicing, payments), a role-gated budget/committed/actual-cost/profitability view backed by a staff-recorded job-costing ledger, and a manual invoice payment ledger exist. Every confirmed APP-1 item is now delivered; remaining accounting integration work (live sandbox verification) lives under QB-3. |
| Invoices and payment visibility | Partial | Local drafts, approved-change-order conversion, immutable company numbering, line items, totals, client-visible issued invoices, authenticated PDF downloads, balances, status fields, notification, questions, unpaid voiding, a manual payment ledger (status transitions from issued to partially paid to paid), outbound QuickBooks Invoice create/void synchronization (wiring mapped Item IDs into outbound `Line` payloads, admin-triggered from the invoice detail page), QuickBooks payment import/reconciliation (per-invoice scan, soft duplicate detection against manually-recorded payments, admin-triggered), a local `CreditMemo` document bridging an approved selection credit to a real invoice (applying one creates an ordinary `Payment` tagged `method=credit_memo`, reusing the existing balance/status machine unchanged), selected-finish invoice origination (an approved selection bills its allowance amount directly, additive with the change-order-based overage/credit path), and QuickBooks-sourced credit memo import (folded into the existing per-invoice payment scan — a QuickBooks credit memo becomes an ordinary `Payment` the same way a QuickBooks payment does) exist. Online payment is deferred. |
| QuickBooks Online | Partial | Company-scoped OAuth, encrypted tokens, capability/subscription discovery, stable customer/invoice/item/payment mappings, customer sync, cost-code item sync, tested Invoice API primitives, Invoice sync orchestration (create/void, retry/backoff, admin error queue), read-only Payment/Credit-Memo import and reconciliation (per-invoice scan discovers both QuickBooks payments and QuickBooks credit memos in one pass, re-verify, retry/backoff, admin error queue), and a local Credit Memo document bridging an approved selection credit to a real invoice exist. Live sandbox acceptance, an Automated-Sales-Tax compatibility check, and change-detection work remain. |
| SaaS subscription billing | Partial | Organization-level Stripe Billing, hosted Checkout, Customer Portal, signed webhook reconciliation, audit records, grace-period access rules, and staged entitlement enforcement exist. The Sandbox Product, monthly/yearly Prices, default Portal, and webhook are configured; a real end-to-end Sandbox test plus live-mode setup and validation remain. |
| Tasks and punch lists | Not started | Confirmed as required, but no models or workflow exist. |
| Two-factor authentication | Not started | Confirmed as optional per user/admin policy, but not implemented. |
| Public legal pages | Complete with launch action | Public EULA and privacy pages exist; real legal entity values and counsel review are still required before production submission. |
| Deployment foundation | Partial | Docker, Render-oriented startup, migrations, static assets, security settings, and environment examples exist; persistent uploads, availability, backups, and operations need production validation. |

## Confirmed product decisions

These decisions govern remaining implementation work:

- QuickBooks is the accounting source of truth.
- Stripe bills construction companies for access to Construction Manager; one subscription belongs to one Organization and its users inherit that entitlement.
- A tenant's Stripe subscription is separate from its connected QuickBooks company. Homeowner invoices and payments do not flow through the platform Stripe account.
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
- A project's contract amount is fixed once a client approves an estimate; only one estimate
  per project may reach approved status. Approved change-order price deltas add to a running
  total project cost without altering the contract amount, matching current practice.
- Clients see contract amount, change orders, selection allowances/overages/credits, invoices,
  and payments; they do not see budget, committed-cost, or internal margin data.
- Tax is a single organization-wide rate (not per-jurisdiction/per-product), defaulted onto new
  invoice and estimate drafts and overridable per draft; margin/profitability figures always
  exclude tax since it is collected on the client's behalf, not revenue.
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

### Project pricing and financials

- Organization-scoped `CostCode` catalog (code, name, description, active flag) managed by
  company admins, referenced from change order, estimate, and invoice line items.
- `Estimate`/`EstimateLineItem`/`EstimateDecision` workflow mirroring change orders: sequential
  numbering, draft/pending/approved/declined/voided states, category/cost-code/quantity/
  unit-price/unit-cost line items with automatic price/cost recalculation, client approval
  (configurable required-approval count), void, and revise-in-place/create-replacement actions
  after a decline with a visible, linked revision chain (a voided estimate stays terminal).
- Approving an estimate sets `Project.contract_amount` exactly once; a database constraint
  guarantees only one estimate per project can ever reach approved status, matching "the
  contract stays fixed."
- A pure `project_financial_summary()` rollup: contract amount, approved/pending change-order
  totals, total project cost (contract plus approved change-order deltas), selection overage/
  credit totals, and invoiced/paid/balance totals for every internal/client viewer permitted to
  open the page; and, additionally restricted to a new `can_view_project_budget` permission
  (Admin, Manager, Project Manager, and Accountant only — narrower than the general
  `can_view_project_financials` gate), approved change-order cost, approved estimate cost
  (budget), committed cost (budget plus approved change-order cost), a staff-recorded actual
  cost total, estimated final cost, estimated margin, profitability, and a credit-disposition
  breakdown.
- A `ProjectCostEntry` job-costing ledger: staff (project managers) record actual costs
  incurred (category, optional cost code, description, amount, date), feeding the actual-cost
  and profitability figures above; management can remove incorrect entries.
- A staff-and-client-safe "Project financials" page replacing the former disabled placeholder,
  branching on viewer role to withhold cost/margin/budget data from clients and from internal
  roles outside management/accounting (Staff, Office Manager, Real Estate Agent).
- A shared `money()`/`MONEY_QUANTUM` rounding helper (moved out of the invoicing module) used
  consistently by change orders, estimates, and invoices.
- A local `Payment` model and `record_payment`/`delete_payment` services: staff record payments
  against an issued invoice (rejecting overpayment), the invoice's paid amount and status
  (issued/partially paid/paid) recompute automatically, and clients see payment date/amount on
  the invoice detail page.
- A tax-rate engine: `Organization.default_tax_rate` (admin-editable at
  `companies/<slug>/tax-settings/`) seeds new invoice and estimate drafts; each draft can
  override its own `tax_rate`, and `tax_amount`/the tax-inclusive total recompute automatically
  from the line-item subtotal whenever line items or the rate change. An invoice's tax rate is
  immutable once issued, matching its other totals. Estimate `margin_total` and the financial
  rollup's `estimated_margin`/`profitability` are computed from the pre-tax subtotal so sales
  tax collected on the client's behalf never inflates them, while the contract amount and
  "total project cost" remain correctly tax-inclusive. Change orders continue to express tax
  only through their existing per-line `Tax` category, not a rate field.

### Schedule

- Internal milestone list and calendar views.
- Start/end dates, status, notes, ordering, create, and update workflows.
- Schedule creation and updates are restricted to Admin, Manager, and Project Manager roles.
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
- Authenticated, non-cacheable invoice PDF downloads with company/project/client identity,
  itemization, totals, payment status, notes, page numbering, and draft/void markings.
- Unpaid issued-invoice voiding and draft disposal with audit events. A referenced change order
  cannot be voided until its draft is discarded or its unpaid issued invoice is voided.

### Legal and deployment

- Public `/legal/eula/` and `/legal/privacy/` pages with footer links.
- Environment-driven legal entity, contact, address, governing law, and effective date.
- Development/production environment examples, production HTTPS controls, database SSL option, and console-first logging.
- Docker static build settings, runtime migrations, and superuser bootstrap.
- Organization-level Stripe subscription records, hosted subscription Checkout, hosted Customer
  Portal sessions, signed/replay-safe webhook processing, current-state Subscription refresh,
  test/live isolation, configurable past-due grace, and project entitlement filtering. Enforcement
  is staged behind `STRIPE_ENFORCE_SUBSCRIPTIONS` so existing companies can be migrated before the
  production gate is enabled.
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
- Durable local-to-QuickBooks Invoice identity, sync-token, document-number, customer, amount,
  balance, date, currency, and linked-transaction snapshots. Read, create, sparse-update, and void
  API primitives enforce required references, stable request IDs, and current external identity;
  no live invoice orchestration or automatic local issue-time call is enabled yet.
- Stable CostCode-to-QuickBooks-Item mappings mirroring the Customer mapping pattern exactly:
  sync tokens, last-known values, tombstones, ownership/conflict policy, administrator manual
  match-by-ID and automated find-or-create sync actions, durable per-attempt retry/backoff/
  resolve handling sharing the same `retry_quickbooks_syncs` and admin queue as Customer sync.
  Not yet wired into outbound Invoice line-item payloads (separate Invoice sync orchestration).
- Current automated baseline: 329 passing tests, Ruff clean, no pending migrations,
  and build-settings `collectstatic` passing as of this update. Django's expected
  development warning remains when QuickBooks credentials are intentionally unset.

## Partial feature gaps

### Change orders

- Voiding an approved change order sets a `requires_financial_reversal` flag for staff to action
  manually; there is no automated accounting reversal because no payment-reconciliation ledger
  exists yet against QuickBooks (blocked on [QB-3 Invoice
  synchronization](#phase-qb-3-entity-synchronization---p0)). Revisit once invoice sync exists so
  a void after invoicing can be rejected or trigger a credit memo instead of only a flag.

### Schedule and notifications

- Add milestone dependencies and dependency-aware date changes.
- Add recurring schedule items.
- Add external calendar integration.
- Add per-event notification settings rather than one project-wide email preference.
- Add immediate versus digest delivery and overdue reminder preferences.

### Mobile experience

- Complete a phone-size workflow audit for client approvals, uploads, messaging, selections, and field use.
- Define whether offline access is required; the interview answer does not provide a testable offline requirement.

### Invoices and payment visibility

- Selected-finish invoice origination is delivered (see APP-2's entry) — an approved selection
  bills its allowance amount directly, and change orders (positive or, via `CreditMemo`,
  negative) remain the other automated invoice source. Staff can also create manual drafts.
- Outbound Invoice create/void synchronization now exists (see QB-3's Invoice sync orchestration
  entry), wiring mapped Item IDs into outbound `Line` payloads.
- QuickBooks payment import now exists (see QB-3's Payment import entry): local payment-status
  fields reflect QuickBooks-sourced records for a synchronized invoice, not only staff-recorded
  entries. Selection allowance credits can now be applied to reduce a change order and, from
  there, a real invoice via a local `CreditMemo` document (see QB-3's credit entry). Distinct
  from that, and still open: importing *QuickBooks-sourced* credit applications — credit memos
  an accountant creates directly in QuickBooks, independent of anything this app originates.

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
- Complete an end-to-end Stripe Sandbox subscription, configure and test Smart Retries, then create
  the equivalent live Prices, Customer Portal, and `/subscriptions/stripe/webhook/` endpoint. Enable
  subscription enforcement only after existing-company migration and live-mode validation.
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
| API calls per customer | There is no periodic customer polling. Initial manual sync makes one exact-name Customer query plus, only when no match exists, one Customer create. A mapped manual refresh makes one Customer read. The separate outbound name update makes one Customer read and, only when the names differ, one sparse Customer update. A retry repeats the applicable operation; writes reuse the same Intuit `requestid`. Due retryable failures run only when `retry_quickbooks_syncs` is scheduled. Company refresh makes two reads (`CompanyInfo` and Preferences), and an expired access token adds one token-refresh call. A manual cost-code item mapping makes one Item read; an item sync makes one exact-name Item query plus, only when no match exists, one Item create — the same request pattern, idempotency, and retry/backoff as Customer sync. Local invoice draft, issue, and discard actions make zero Intuit calls. An admin-triggered invoice sync makes one Invoice create; an admin-triggered void re-reads the invoice (one Invoice read, for its current `SyncToken`) then makes one Invoice void call. An admin-triggered payment sync makes one Payment read per already-imported payment and one CreditMemo read per already-imported credit memo on that invoice (re-verify), plus one Payment query and one CreditMemo query (both paginated) to discover new ones — all in the same scan. None of this is automatic or triggered by local issue/void/payment actions themselves. |
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
- [x] Implement invoices and payments (see Invoice synchronization preparation below).
- [x] Let a selection allowance credit (e.g. Tile comes in $500 under its $5,000 allowance) be
  applied to reduce a `ChangeOrder`, via a new `source_selection` FK plus a negative `price_delta`
  (negative `price_delta` was already a supported "client credit" pattern, `projects/forms.py`:
  *"Use a negative amount for a client credit"*). `ChangeOrder.clean()` validates the source
  selection has an unapplied credit with disposition "Apply to another selection," the credit
  amount isn't exceeded, and at most one active (non-voided) change order can draw on a given
  selection's credit at a time. A credit change order correctly reduces
  `project_financial_summary`'s `total_project_cost` the moment it's approved (via the normal
  `approved_change_order_total` sum, no special-casing needed there), and the flagged/unapplied
  credit total now excludes selections with an active credit change order, so staff aren't
  double-nagged.
- [x] Bridge an approved credit change order to a real bill via a new local `CreditMemo`
  document (`billing/models.py`), separate from `Invoice` since a credit change order can never
  satisfy `Invoice`/`InvoiceLineItem`'s non-negative `CheckConstraint`s. `create_credit_memo_from_
  change_order` mirrors `create_invoice_from_change_order`'s lifecycle (draft → `issue_credit_memo`
  assigns an immutable company-wide `CM-000006`-style number). Applying a credit memo
  (`apply_credit_memo`) creates an ordinary `Payment` tagged with a new `Payment.Method.
  CREDIT_MEMO` and a `credit_memo` FK, reusing `record_payment`/`_apply_payment_state`
  **completely unchanged** — the existing `Invoice` balance/status machine (issued → partially
  paid → paid) already does exactly what "an amount reduced what's owed" needs, so applying a
  credit correctly drives an invoice's status with zero new logic on `Invoice` itself. Partial and
  multi-invoice application works for free (`CreditMemo.remaining_balance` is computed from the
  linked `Payment`s), an application is capped at both the credit memo's remaining balance and the
  invoice's own balance due, and voiding is blocked once anything has been applied (staff reverse
  an application first via the existing `delete_payment`, which — since `remaining_balance` is
  computed, not stored — automatically and correctly restores the credit with no special-casing).
  The change order detail page's "Accounting handoff" section gained the mirrored "Create credit
  memo" action next to the existing "Create invoice draft" one. Still separate and out of scope:
  importing *QuickBooks-sourced* credit memos (ones an accountant creates directly in QuickBooks)
  and reconciling this app's `Payment(method=credit_memo)` records against a QuickBooks credit
  memo object.

Invoice synchronization preparation:

- [x] Add a local Invoice and InvoiceLineItem foundation with immutable issue numbering, totals,
  source links, client visibility, balance/status fields, and safe draft/void lifecycles.
- [x] Require a client and at least one positive line before issue; preserve draft isolation and
  prevent source change-order reversal while an active invoice exists.
- [x] Add durable QuickBooks Invoice identity/sync-token mappings and tested create/read/sparse-
  update/void API primitives with stable request IDs.
- [x] Add a `CostCode`-to-QuickBooks-Item (Product/Service) mapping layer: `QuickBooksItemMapping`,
  `get_item`/`create_item`/`find_items_by_name`/`iter_items` API primitives, manual match-by-ID
  mapping plus an automated find-or-create sync action, and the same durable-attempt/retry/
  backoff/resolve machinery as Customer sync (a dedicated `QuickBooksSyncAttempt.EntityType.ITEM`
  with its own retry/resolve functions, since the attempt model uses per-entity FK slots rather
  than a generic one). The automated create path sends a minimal payload and has not been
  verified against a live sandbox company — most QuickBooks company configurations require an
  `IncomeAccountRef` this app does not yet collect, so matching an existing item (by ID or name)
  is the reliable path; creating a brand-new item may need further account-mapping support.
- [x] Add Invoice sync orchestration (`integrations/invoice_sync.py`) so outbound Invoice `Line`
  payloads reference mapped Item IDs: `start_invoice_sync` (CREATE, fails closed with the specific
  unmapped cost codes if any line lacks an active `QuickBooksItemMapping`) and
  `start_invoice_void_sync` (VOID, re-reads the invoice at execute time for a fresh `SyncToken`
  so retries are safe), both using the same durable-attempt/retry/backoff/resolve machinery as
  Customer and Item sync, wired into `retry_quickbooks_syncs` and an admin-triggered "Sync to
  QuickBooks" / "Void in QuickBooks" action on the invoice detail page (not the QuickBooks
  dashboard, since invoices are numerous and per-project). UPDATE is intentionally unsupported —
  issued invoice line items are immutable, so a correction is void-and-reissue. Tax is sent as an
  explicit `TxnTaxDetail.TotalTax` override using this app's own computed `tax_amount`
  (`GlobalTaxCalculation: TaxExcluded`), since Construction Manager is the source of truth for the
  organization's flat rate — **not yet verified against a live sandbox company with Automated
  Sales Tax enabled**, where QuickBooks may reject or recompute this instead.
- [x] Add QuickBooks Payment import (`integrations/payment_sync.py`), the one entity in this
  phase that is import-only (`FROM_QUICKBOOKS`) rather than outbound — this app never writes
  payments to QuickBooks. A `QuickBooksPaymentMapping` model tracks identity per
  (connection, QuickBooks payment, invoice) pair, since a single QuickBooks payment can apply to
  several invoices via multiple `LinkedTxn` entries. An admin-triggered "Sync payments from
  QuickBooks" scan on a synchronized invoice both re-verifies already-imported payments
  (tombstones on delete, marks voided on a zero `TotalAmt`) and discovers new ones via a
  customer-scoped Payment query filtered client-side by `LinkedTxn`, reusing the existing
  `record_payment` service so balance/status recalculation is identical to a staff-recorded
  payment. A soft duplicate check (same invoice, same amount, paid date within 3 days, not
  already QuickBooks-mapped) blocks auto-creating a payment that might already exist locally;
  when found, other clean payments in the same scan are still created, but the attempt is marked
  failed and non-retryable so it surfaces in the admin queue for a human decision. Uses the same
  durable-attempt/retry/backoff/resolve machinery as Customer/Item/Invoice sync, with a
  deliberate departure from their one-attempt-per-entity convention: `response_snapshot` holds a
  `created`/`reverified`/`possible_duplicates` summary instead, since one scan can affect zero to
  several payments.

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

- [x] Add a project pricing catalog (organization-scoped cost codes) and category/cost-code
  line items on change orders, estimates, and invoices.
- [x] Add estimate/proposal generation and client-visible approval (configurable required-
  approval count, mirroring change orders).
- [x] Calculate base project total (contract amount, set once from an approved estimate),
  selection overages/credits (flagged, not merged), change orders, invoices, payments, and
  remaining balance, without treating estimates as accounting truth after approval.
- [x] Add staff financial views (contract, change orders, selections, invoicing, and
  internal-only cost/margin) and a client-safe financial summary that withholds budget and
  margin data.
- [x] Add a local payment ledger so invoice balances are backed by real payment records
  instead of a bare field.
- [x] Define rounding (shared `money()`/`ROUND_HALF_UP` helper used consistently across change
  orders, estimates, and invoices) and void/audit rules (estimate void, payment
  record/delete with activity events).
- [x] Define a tax-rate calculation engine: an organization-level default tax rate
  (admin-editable), a `tax_rate` field on invoices and estimates that computes `tax_amount`
  automatically from the line-item subtotal (`subtotal * rate / 100`, rounded via the shared
  `money()` helper), a per-draft override, and immutability of the rate once an invoice is
  issued. Margin and profitability are computed from the pre-tax subtotal so sales tax
  collected on the client's behalf never inflates them. Change orders are intentionally left
  out of the rate-based engine; their existing per-line `Tax` category remains adequate for
  the smaller, discrete adjustments they represent.
- [x] Add an estimate revision/replacement chain equivalent to change orders' revise/replace
  workflow: a declined estimate can be revised in place (reopened as a draft) or replaced by
  a new estimate that copies its title, description, required-approval count, and line items,
  preserving a visible, linked chain between the original and its replacement. A voided
  estimate remains terminal, matching change-order behavior.
- [x] Add staff budget/committed-cost/actual-cost/profitability visibility: an approved
  estimate's cost total is the budget baseline, committed cost adds approved change-order
  cost deltas, a staff-recorded `ProjectCostEntry` job-costing ledger supplies actual cost,
  and estimated final cost/profitability are derived from both. Gated by a new
  `can_view_project_budget` permission restricted to Admin/Manager/Project Manager/
  Accountant — narrower than the general internal `can_view_project_financials` gate,
  since Staff/Office Manager/Real Estate Agent should not see budget or margin data. The
  client interview left staff-side budget visibility unanswered; this implements the
  standard job-costing pattern (budget vs. committed vs. actual vs. profitability) rather
  than leaving it unbuilt.
- [x] Add QuickBooks Item/CostCode mapping (`QuickBooksItemMapping`, mirroring the Customer
  mapping pattern, with manual match and automated find-or-create sync). Cost codes can now be
  linked to a QuickBooks Item; actually driving outbound QuickBooks Invoice line items from that
  mapping is separate Invoice sync orchestration work, still open under QB-3.

### Phase APP-2: Invoices and client visibility - P0

- [x] Add local manual invoice drafts and drafts originating from approved positive change orders.
- [x] Add selected-finish invoice origination. Resolved via the original client interview
  (`docs/Construction Manager.md`, II.3: *"invoices are from approved selection or change
  order"*; VII.4: overages/credits are deviations handled by change order) rather than a guess:
  `create_invoice_from_selection` (`billing/services.py`) originates a draft `Invoice` directly
  from a `SELECTED` `FinishSelection` via the previously-dead `Invoice.source_selection` field,
  billing the **allowance amount** (one `InvoiceLineItem` with the already-existing but
  previously-unused `ALLOWANCE` category) rather than the chosen option's actual price — this
  keeps it strictly additive with the change-order path, which already bills the variance
  (overage or, via the local `CreditMemo`, credit) separately, so the two never double-bill each
  other. Works independent of whether the selection has an overage/credit, one invoice per
  selection (enforced by the existing `OneToOneField`). The selection detail page gained an
  "Invoice this selection"-equivalent panel mirroring the existing overage/credit panels.
- [x] Add invoice status, immutable organization-wide numbering, line items, totals, and balances.
- [x] Add QuickBooks invoice identity and sync-token mapping.
- [x] Add credit application (local `ChangeOrder`/`CreditMemo` mechanism — see QB-3's credit
  entry and this phase's selected-finish invoice origination entry above).
- [x] Add QuickBooks credit-memo mapping. A QuickBooks-sourced credit memo (one an accountant
  creates directly in QuickBooks, independent of anything this app originates) reduces an
  invoice's balance exactly the same way a QuickBooks payment does, so it's folded into the
  *existing* per-invoice payment scan (`integrations/payment_sync.py`) rather than getting a
  parallel entity type, sync attempt, or button: the same "Sync payments from QuickBooks" scan
  now also calls `find_credit_memos_for_invoice` (mirroring `find_payments_for_invoice`'s
  customer-scoped-query-plus-client-side-`LinkedTxn`-filter pattern) and imports each as an
  ordinary `Payment` (`method=CREDIT_MEMO`, `credit_memo=None` — no local `CreditMemo` document
  backs it, since it wasn't created via this app's change-order flow). QuickBooks identity lives
  entirely in a new `integrations.QuickBooksCreditMemoMapping`, mirroring
  `QuickBooksPaymentMapping` field-for-field. The duplicate-detection heuristic now excludes
  local payments already linked to *either* mapping type, so a locally-applied credit and an
  imported QuickBooks one can't shadow each other.
- [x] Let clients view invoice details, balances/payment status, and ask questions.
- [x] Add authenticated downloadable invoice PDFs.
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
