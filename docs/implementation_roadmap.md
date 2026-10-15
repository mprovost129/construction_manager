# Construction Manager Implementation Roadmap

Last updated: October 15, 2026

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
| Invoices and payment visibility | Partial | Drafts, allowance/change-order origination, immutable numbering, PDFs, issued-invoice totals, local payments/credits, outbound QuickBooks create/void, and inbound per-invoice payment/credit applications exist. Draft invoices are excluded from amounts billed/owed. Live sandbox acceptance and historical credit reconciliation remain. Online payment is deferred. |
| QuickBooks Online | Partial | The live sandbox pilot passed no-tax invoice create/void, payment/credit allocations, reversals, idempotent retry, and explicitly mapped US manual sales tax at 8% and 9.1%. Invoice/payment concurrency and interrupted-worker recovery passed isolated PostgreSQL tests. Opt-in automatic payment polling is implemented and running locally for the sandbox. Unsupported tax configurations remain blocked; production worker deployment, broader bidirectional sync, customer/item concurrency coverage, and webhook/CDC change detection remain. |
| SaaS subscription billing | Partial | Organization-level Stripe Billing, hosted Checkout, Customer Portal, signed webhook reconciliation, audit records, grace-period access rules, and staged entitlement enforcement exist. The Sandbox Product, monthly/yearly Prices, default Portal, and webhook are configured; a real end-to-end Sandbox test plus live-mode setup and validation remain. |
| Tasks and punch lists | Not started | Confirmed as required, but no models or workflow exist. |
| Two-factor authentication | Not started | Confirmed as optional per user/admin policy, but not implemented. |
| Public legal and help pages | Complete with launch action | Public EULA, privacy, and first-use help pages exist; a printable user guide is included. Real legal entity values, counsel review, and a staffed support process are still required before production submission. |
| Deployment foundation | Partial | Docker, Render-oriented startup, migrations, static assets, security settings, and environment examples exist; persistent uploads, availability, backups, and operations need production validation. |

## Confirmed product decisions

These decisions govern remaining implementation work:

- QuickBooks is the accounting source of truth.
- Stripe bills construction companies for access to Construction Manager; one subscription belongs to one Organization and its users inherit that entitlement.
- A tenant's Stripe subscription is separate from its connected QuickBooks company. Homeowner invoices and payments do not flow through the platform Stripe account.
- The interview requests bidirectional exchange of customers, invoices, credit memos, and payments. Current delivery is narrower: customer create/refresh/name update, outbound invoice create/void, and inbound payment/credit applications. Outbound payments/credit memos and broader invoice updates remain a scope decision and implementation gap.
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
- Company roles and project-specific internal assignments; the team-management screen exposes every supported internal role.
- Separate manage-project, manage-client, and notification permissions.
- Project-scoped queries and authorization checks across portal modules.
- Idempotent environment-backed Django superuser bootstrap for container startup.
- Brute-force login protection and production session/security configuration.

### Project portal

- Portfolio dashboard, project cards, search, status filtering, and action center, including internal-priority visibility for all supported non-accountant internal roles and client-safe action links.
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
  items and preserving a one-to-one source link. Negative changes use the separate local credit-memo workflow; zero changes cannot be invoiced.
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
- Public `/help/` first-use guidance for company administrators, project teams, and clients, plus Markdown and printable Word user guides under `docs/`.
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
  administrator-triggered invoice create/void orchestration exists; automatic local issue-time synchronization is not enabled.
- Stable CostCode-to-QuickBooks-Item mappings mirroring the Customer mapping pattern exactly:
  sync tokens, last-known values, tombstones, ownership/conflict policy, administrator manual
  match-by-ID and automated find-or-create sync actions, durable per-attempt retry/backoff/
  resolve handling sharing the same `retry_quickbooks_syncs` and admin queue as Customer sync.
  Mapped Item IDs are wired into outbound Invoice line-item payloads.
- Automated baseline on October 15, 2026: 402 tests and 13 subtests pass using isolated
  SQLite settings, including the integrated pilot workflow below. Live API acceptance and
  PostgreSQL concurrency behavior are not established by these tests. Ruff, migration-drift
  checks, Django system checks, and the production-style static build also pass. The isolated
  system check reports the expected warning that Stripe subscriptions are not configured.

## Pilot validation on October 15, 2026

The local Python environment was repaired to use the installed Python interpreter.
`integrations/tests/test_pilot_workflow.py` exercises estimate approval, homeowner selection,
explicit approval of an overage change order, allowance and overage invoicing, mapped
QuickBooks invoice creation, a receipt split across two invoices, repeat synchronization,
client financial visibility, and authenticated PDF download. External API responses are
simulated; the test does not change customer accounting data.

Verified corrections:

- Draft invoices no longer count as invoiced amounts or amounts owed in the project summary.
- Change-order conversion preserves cost codes and therefore QuickBooks Item mappings.
- Payment import uses matching Invoice line amounts, not the receipt's whole `TotalAmt`.
  Split receipts exclude unapplied cash and allocations to other invoices.
- Credit applications come through linked Payment records, including zero-cash payments.
  CreditMemo document totals are not invoice application amounts.
- Reverification updates changed amounts/dates and recalculates balances. Deleted, voided,
  or unapplied payments stop reducing balances while retaining ledger history and external
  identities. Reapplication restores the original mapping.
- Ledger entry and external mapping creation commit atomically. Invalid/ambiguous records
  fail with an admin-visible error; partial progress is retained. Repeated delivery does
  not duplicate payments. Imported payments must be corrected in QuickBooks.
- Background imports work without impersonating a user. Migration
  `billing.0006_allow_automated_payment_imports` makes `recorded_by` nullable.

Existing active `QuickBooksCreditMemoMapping` records from the former document-total import
path block new payment scans until explicitly reconciled/migrated. They are preserved to
avoid silently rewriting history or double-counting credit. Before rollout, confirm the
pilot has no legacy mappings, or reconcile them against actual Payment applications.
Old inactive imported records also need their invoice balances rechecked.

API interpretation: [Intuit Payment reference](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/payment).

### Live sandbox results on October 15, 2026

OAuth and real accounting API requests succeeded against **Sandbox Company US 132a**
(realm `9341457631635607`). The connection belongs to the local Heritage Realty & Custom
Homes organization. No production QuickBooks company was used.

Test records are isolated in local project **SANDBOX PILOT 2026-10-15** (project ID 2),
mapped to sandbox customer 58. Duvally Residence was not changed. Synthetic homeowner and
isolation-test accounts have unusable passwords; notification delivery was captured in
memory during the test. Customer creation, invoice export, payment import, and retry/void
used the application's actual services and the live sandbox. Accounting receipts and credit
memos were seeded through the sandbox API as test fixtures; this does not establish an
outbound payment/credit-memo feature in the product and did not collect any money.

| Scenario | Measured result |
| --- | --- |
| Estimate, selection, explicit overage approval | Passed through Django approval views on local PostgreSQL: $100,000 contract, $5,800 choice against $5,000 allowance, $800 change order, $100,800 project total. |
| Draft exclusion and existing Item mapping | Passed; drafts contributed $0 billed. Cost code QA-20261015 maps to existing non-taxable Services Item 1. New Service item creation is verified in the follow-up below. |
| Invoice creation | Passed for local invoices 1 and 2, QuickBooks invoices 145 and 146: $5,000 plus $800. No full-contract invoice was created. |
| Split receipt and repeated imports | Passed: one $5,800 receipt settled both invoices with only each invoice's allocation imported. Repeated imports did not duplicate ledger entries. |
| Amount/date changes and unapplied cash | Passed: a $6,000 receipt allocated $4,000 and $500, leaving $1,500 unapplied; local balances matched QuickBooks at $1,000 and $300. Payment dates updated. |
| Removal and reapplication | Passed: removing allocations reopened both balances; reapplication reused original local ledger records. |
| Voided/deleted receipts | Passed: voiding receipt 147 and deleting disposable receipt 148 restored the corresponding balances while retaining local history. |
| Zero-cash credit | Passed: $200 credit application reduced invoice 145 by $200 despite Payment TotalAmt being zero. |
| Mixed cash and credit | Passed: $300 cash plus $200 credit reduced invoice 146 by $500. |
| Final settlement | Passed: invoices 145/146 both show zero balance. $5,800 is settled through $5,400 cash and $400 credits; seven local ledger-history rows include the voided/deleted allocations. |
| Lost-response retry | Passed: the test deliberately discarded a successful real invoice-create response, then retried using the original request ID. QuickBooks returned the same invoice 155. The failure was injected; the create and retry responses were real. |
| Outbound invoice void | Passed for disposable tax/retry probe invoices; QuickBooks totals and balances were zero afterward. |
| Client visibility, PDF, tenant isolation | Passed through Django views: internal costs were absent, both PDFs returned PDF content, and a different company's administrator received 404 for financial details, invoice details, and PDF download. Visual browser layout was not revalidated. |

**The initial tax discrepancy is resolved for mapped US manual sales tax.** A synthetic invoice with a
$100 subtotal and $7.25 local tax was accepted by QuickBooks but returned $100 total and
$0 tax. Sending only `TxnTaxDetail.TotalTax` did not preserve the tax. The probe invoice
(local 3, QuickBooks 154) was voided in both systems. The organization's current default
tax rate remains zero. The correction uses an explicit QuickBooks transaction tax-code
reference and taxable line markers, then compares QuickBooks' calculated tax and total
with the issued local invoice. It does not force a tax-total override.

The administrator-only **Sales tax mapping** page verifies an existing code's component
rates and requires an exact percentage match. Rates are checked again before export.
Mappings are scoped to the connected company and changes/removals are audited. The admin
must choose the appropriate jurisdiction; the app does not infer it from the percentage.

Live follow-up used **SANDBOX TAX PILOT 2026-10-15** (project ID 3), isolated from Duvally:

| Scenario | Measured result |
| --- | --- |
| Mapping screen and mismatch rejection | Passed through administrator views with live tax data; 7.25% could not be mapped to the sandbox's 8% California code. |
| Single-rate code | Local invoice 5 / QuickBooks 157: $100 subtotal, $8 tax, $108 total. |
| Additive combined rate | Local invoice 6 / QuickBooks 158: 7.1% + 2% components produced $9.10 tax and $109.10 total. |
| Decimal response recovery | QuickBooks' JSON number 109.1 exposed binary-float conversion in currency storage. Fixed conversion through decimal text, then recovered invoice 158 with the persisted original request ID. No new create attempt was started. |
| Settlement and repeat import | Synthetic receipt 159 settled $217.10 across both invoices. Both balances match QuickBooks at zero; repeating the import retained exactly two ledger mappings. |

The California and Tucson codes are sandbox fixtures, not recommendations for Heritage's
tax jurisdiction. Business tax defaults and QuickBooks tax settings were not changed.
Supported scope is one flat rate across the whole invoice in a single-currency US company
using manual sales tax. Automated tax, non-US taxed invoices, compound/date-dependent
rates, and mixed taxable/exempt invoice calculations remain unsupported. Additive rates
can round differently on other subtotals; any discrepancy is flagged rather than accepted.

Protective fixes made from the live findings:

- Taxed exports require an active explicit mapping and supported current tax configuration.
  Legacy queued attempts with missing/different tax references stop before the API write.
- A differing returned invoice total or sales tax is now a nonretryable sync failure. The remote invoice
  identity is retained so correcting the discrepancy does not create another invoice.
- Intuit error 2020 (missing required information) is no longer mislabeled as a stale record
  or scheduled for automatic retry. Error 5010 retains stale-record retry behavior. This
  was observed when a test fixture omitted CustomerRef; the fixture was then corrected.

After these fixes, the complete isolated automated suite passed **424 tests and 31
subtests**. Coverage includes mapping permissions, unsupported modes, changed rates,
retry payload stability, decimal currency responses, and tax/total reconciliation. Ruff,
Django system checks, migration drift checks, and `git diff --check` passed (the isolated
test settings emitted the expected warning that Stripe paid access is unconfigured). Migrations
`integrations.0008` and `projects.0028` were applied locally. The local HTTPS server was
restarted with the changes. No production deployment was performed.
Browser security policy blocked the final visual inspection; application-level page,
permission, and live mapping checks passed, but browser layout is not newly verified.

Local connection setup required the exact registered callback
`https://localhost:8000/integrations/quickbooks/callback/`. The local Python HTTP client
also required a CA bundle combining its public roots with already-trusted Windows roots;
the local `.env` points to an ignored `.certs/` bundle. Certificate verification remains
enabled. This is local workstation configuration, not a production deployment change.

Still required before production: confirm the company's actual tax configuration and jurisdiction,
customer/item and token-refresh concurrency tests, full role/browser verification,
webhook/CDC change detection, and the operational release gates below. The live
pilot does not mark the broader QB-1 through QB-4 or production gates complete.

### Invoice/payment concurrency and recovery acceptance — October 15, 2026

Tests used a separately created local PostgreSQL database, independent connections and
child processes, and fake QuickBooks responses. These tests made no live QuickBooks calls
and did not alter business invoice/payment data. They supplement the live sandbox results.

Two regressions were reproduced before fixing them: a delayed second Sync click could
create another outbound request after the first completed, and a fresh Sync click after
an uncertain timeout could bypass the original request ID. Both now stop before a second
create. Final preparation checks run under the invoice row lock; dispatched requests retain
their identity even after an administrator marks an error resolved. Historical invoice
attempts are conservatively marked as dispatched by migration `integrations.0009`.

Additional protections and verified outcomes:

- One worker executes an invoice/payment attempt at a time. A PostgreSQL session advisory
  lock covers execution without holding an invoice-row transaction across HTTP requests.
  A different process cannot acquire the same attempt while the worker is alive.
- An actual child-process exit inside the fake create API boundary leaves a recoverable
  running attempt. Recovery retains the exact request ID and payload. When the response ID
  or invoice mapping was committed, recovery reads that invoice instead of creating one.
- Remote IDs are checkpointed before local mapping validation. A subsequent transient read
  failure preserves that checkpoint through the normal retry path.
- Competing retries produce one successor. Repeated recovery does not repeat completed work.
  A live worker is skipped even if its timestamp is old. Unexpected errors are sanitized and
  sent to the error queue; repeated interruptions eventually require review.
- Payment creation and its mapping roll back together if interrupted between them. A crash
  after commit preserves the ledger; recovery re-verifies it without adding another entry.
  Overlapping imports leave one entry, and simultaneous local payments cannot overpay.
- Local void is blocked during a running sync. Interrupted outbound void recognizes an
  already-zero remote invoice without sending another void request.

Operations:

- Preview abandoned work with `python manage.py recover_quickbooks_syncs --dry-run`.
- Run `python manage.py recover_quickbooks_syncs` to recover invoice/payment attempts idle
  for at least 15 minutes. `--stale-minutes` and `--limit` bound candidate selection. Active
  workers remain locked and are skipped; no new operation/request ID is generated.
- Schedule this command alongside `retry_quickbooks_syncs` on the production worker before
  launch. Scheduling is not installed by this change. Customer/item recovery is not covered.
- Sync workers require direct PostgreSQL connections or session pooling. Transaction-mode
  pooling is incompatible with these session locks and must not be used for sync workers.
  PostgreSQL releases session locks on connection termination; see the
  [PostgreSQL advisory-lock documentation](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS).
- A resolved error does not prove an uncertain remote invoice is absent. Reconcile/map the
  existing record or retry the saved request; do not discard its identity to unblock export.

Reproduce acceptance with `python -m pytest --ds=config.settings.test_postgres`. Those
settings require a loopback PostgreSQL server, clear integration credentials, create a
process-specific `test_cm_sync_*` database, and let the test runner remove it afterward.
The default SQLite suite skips the eight tests that require actual PostgreSQL concurrency.

Validation: the full PostgreSQL suite passed **444 tests and 31 subtests**, including
20 new concurrency/recovery tests. Ruff, Django system checks, migration drift checks,
and whitespace checks passed. Migration `integrations.0009` was applied locally, recovery
dry-run found zero abandoned candidates, and the local HTTPS app was restarted. No
production deployment or worker scheduling was performed.

### Service item income-account acceptance — October 15, 2026

Administrators can choose **Set up service item** beside an unmapped cost code, select
an existing income account in the connected company, and find/create the Service item.
No account is preselected. The supported account detail types are Service/Fee Income and
Sales of Product Income; inactive, special, expense, and other account types are excluded.
The selected account is re-read before matching/creating. Existing items/accounts are not
edited. Inventory and other product types continue to use explicit existing-item mapping.

The create payload includes `IncomeAccountRef`; item mapping/attempt snapshots preserve
the returned account, and the sync audit records its ID. Missing-account legacy creates
stop before the write. An existing name must identify exactly one active Service item;
a differing income account requires review. A returned account mismatch preserves the
remote ID and fails without linking it. Retries retain the original account/payload/request
ID, and a fresh create cannot bypass a dispatched request. Item execution now also uses
the shared worker lock; automatic stale-item recovery and the broader item concurrency
matrix remain follow-up work.

Live sandbox results in Sandbox Company US 132a:

- Created cost code **QA-ITEM-20261015** (local ID 2) and **QA sandbox service 20261015**
  (QuickBooks Item 19), using the sandbox's existing **Services** income account (ID 1).
  This is a synthetic test choice, not a business account recommendation.
- The administrator setup view loaded live choices. The create response was deliberately
  discarded after the real sandbox write. Retrying retained the original request ID and
  found the same compatible item; an exact-name query confirmed only one item existed.
- Item refresh preserved the same mapping and income account. Local invoice 7 / QuickBooks
  invoice 160 exported $12.34 using the new item, then was voided in both systems. Its final
  QuickBooks total and balance are zero. No payment was collected or notification sent.

Validation: **461 tests and 39 subtests passed on PostgreSQL**, including 17 new account
selection tests. Coverage includes permissions, company isolation, forged selections,
account deactivation, incompatible matches, retry identity, API pagination, and returned
account discrepancies. Ruff, Django checks, migration drift, and whitespace checks passed.
No schema migration was required. The local HTTPS app was restarted; no production
deployment was performed. Browser layout was not visually revalidated.

API contract: [Intuit Item reference](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/Item).

## Partial feature gaps

### Change orders

- Approved change-order voids retain a `requires_financial_reversal` flag. Invoice and
  credit-memo safeguards exist, but complete automatic change-order accounting reversal
  across local and QuickBooks records is not implemented. Validate staff follow-up.

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
- Payment application import and reversal reconciliation passed the documented live sandbox
  scenarios. Credits are discovered through Payment links. Local credit-memo application is
  separate from outbound QuickBooks CreditMemo synchronization, which is not implemented.

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
- Schedule interrupted invoice/payment recovery with `recover_quickbooks_syncs`, and verify
  the worker uses a direct or session-pooled PostgreSQL connection.
- Public liveness and database/cache readiness checks are implemented locally (see below).
  Configure production monitoring, alerting, and an incident-response procedure in the
  client's approved hosting workspace.
- Complete an end-to-end Stripe Sandbox subscription, configure and test Smart Retries, then create
  the equivalent live Prices, Customer Portal, and `/subscriptions/stripe/webhook/` endpoint. Enable
  subscription enforcement only after existing-company migration and live-mode validation.
- Establish an operational privacy-request, data-export, QuickBooks-disconnect, and deletion procedure matching the published Privacy Policy.

### Health checks implemented locally

- `/health/live/` returns HTTP 200 with `{"status":"ok"}` when Django can answer a
  request, without touching the database, cache, sessions, or third-party services.
- `/health/ready/` executes `SELECT 1` and verifies a unique, short-lived cache value.
  It returns HTTP 200 when both checks succeed or HTTP 503 with
  `{"status":"unavailable"}` otherwise. It never reads client records or calls QuickBooks.
  Probe keys expire after 15 seconds and are deleted after checking; existing cache data
  is not cleared. Connection details and raw exception text are omitted from responses/logs.
- Both routes accept GET/HEAD, require no login, prohibit response caching, and preserve
  the existing HTTPS/allowed-host protections. A future hosting readiness monitor should
  use `/health/ready/`; process-only monitoring can use `/health/live/`.
- Production database connection timeout defaults to 5 seconds; Redis connection and
  socket timeouts default to 3 seconds each. Environment overrides are documented in
  `.env.example`. These are connection/operation limits, not an overall probe deadline;
  configure a monitor request timeout as well.
- These checks do not verify migrations, backups, uploads, email, payment-worker progress,
  or external accounting/subscription availability. They complement the payment-worker
  check-in indicators. Local readiness verifies the locally configured cache backend;
  production Redis behavior still needs deployment acceptance in the client's workspace.
- No hosting monitor was provisioned and no billing or deployment changes were made.

Validation on October 15: **499 tests and 49 subtests passed on PostgreSQL**, including
10 health-probe tests covering outages, silent cache failure, cleanup, privacy, HTTP methods,
HTTPS redirects, and cache headers. Ruff, Django checks, migration consistency, and whitespace
checks passed. Production timeout configuration was loaded with database TLS still required.
Both routes returned HTTP 200 against the local app configuration, and the local HTTPS server
was restarted. Changes are published to the release branch only.

### Security and legal

- Implement optional two-factor authentication, authenticator-app enrollment, recovery codes, admin enforcement, and audit events.
- Provision a production-only QuickBooks token-encryption key in the Render secret store,
  document key rotation, and retain old keys during rotation until all stored tokens are re-encrypted.
- Configure real `LEGAL_*` values and obtain legal review of the EULA and Privacy Policy.
- Assign support ownership, publish a monitored support address, and document response targets; the public help route and first-use guide are delivered.
- Perform dependency, vulnerability, access-control, upload, and authorization testing before external users are admitted.

## QuickBooks Online roadmap

Current questionnaire truth as of this update:

| Question | Accurate answer today |
| --- | --- |
| API calls per customer | There is no periodic customer polling. Initial manual sync makes one exact-name Customer query plus, only when no match exists, one Customer create. A mapped manual refresh makes one Customer read. The separate outbound name update makes one Customer read and, only when the names differ, one sparse Customer update. A retry repeats the applicable operation; writes reuse the same Intuit `requestid`. Due retryable failures run only when `retry_quickbooks_syncs` is scheduled. Company refresh makes two reads (`CompanyInfo` and Preferences), and an expired access token adds one token-refresh call. A manual cost-code item mapping makes one Item read; service setup lists eligible accounts through a paginated Account query, then reads the selected account before the exact-name Item query and optional Item create. A mapped item refresh reads the existing Item. New creates include an explicit IncomeAccountRef, and retries preserve the saved payload and request ID. Local invoice draft, issue, and discard actions make zero Intuit calls. An admin-triggered invoice sync makes one Invoice create; an admin-triggered void re-reads the invoice (one Invoice read, for its current `SyncToken`) then makes one Invoice void call. An admin-triggered payment sync makes one Payment read per already-imported allocation, including inactive mappings (re-verify), plus a paginated customer-scoped Payment query to discover new applications — all in the same scan. When automatic payment updates are enabled and a worker is running, each due mapped invoice adds two Invoice reads around the same Payment scan, normally every 15 minutes. Calls can increase with pagination, existing allocations, retries, and token refresh. Local issue/void/payment actions do not themselves trigger these scans. |
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
  payment polling is now available per company with explicit opt-in. Outbound entity
  synchronization remains administrator-triggered; production scheduling is not deployed.

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
  The change order detail page includes a "Create credit memo" action. Outbound QuickBooks
  CreditMemo synchronization and automatic matching of local credits to QuickBooks Payment
  applications remain unfinished. Possible duplicates are held for administrator review.

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
  than a generic one). New Service items now require explicit administrator selection of an
  eligible existing income account. Account validity is checked against the same QuickBooks
  company immediately before matching/creating. The saved request includes `IncomeAccountRef`;
  returned account references are retained in mapping/attempt snapshots. Live create,
  lost-response retry, invoice usage, and void acceptance are recorded above. Inventory and
  other product types still use explicit existing-item mapping.
- [x] Add Invoice sync orchestration (`integrations/invoice_sync.py`) so outbound Invoice `Line`
  payloads reference mapped Item IDs: `start_invoice_sync` (CREATE, fails closed with the specific
  unmapped cost codes if any line lacks an active `QuickBooksItemMapping`) and
  `start_invoice_void_sync` (VOID, re-reads the invoice at execute time for a fresh `SyncToken`
  so retries are safe), both using the same durable-attempt/retry/backoff/resolve machinery as
  Customer and Item sync, wired into `retry_quickbooks_syncs` and an admin-triggered "Sync to
  QuickBooks" / "Void in QuickBooks" action on the invoice detail page (not the QuickBooks
  dashboard, since invoices are numerous and per-project). UPDATE is intentionally unsupported —
  issued invoice line items are immutable, so a correction is void-and-reissue. US manual
  sales tax uses an explicitly mapped `TxnTaxDetail.TxnTaxCodeRef` and `TAX` line markers;
  zero-rate US invoices use `NON` line markers. Taxed exports verify live code/component
  rates and compare returned tax and total. Missing mappings and unsupported configurations
  are blocked. Live 8% and 9.1% acceptance results and limitations are recorded above.
- [x] Add read-only Payment application import (`integrations/payment_sync.py`). Each
  mapping represents one (connection, Payment, invoice) allocation. Invoice-linked line
  amounts determine the local amount, including credit-funded applications. Reverification
  updates amounts/dates and reverses deleted/voided/unapplied allocations without deleting
  history. Reapplication reuses the existing mapping. Atomic ledger/mapping writes, partial
  progress, duplicate review, retry/backoff, and system-originated imports are tested.
  Live sandbox acceptance is recorded above; legacy credit-import reconciliation remains required.

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

Implemented first slice (2026-10-15): **automatic payment reconciliation for mapped
invoices**, including fully paid invoices so reversed payments can reopen a balance.
This uses targeted polling; the Intuit questionnaire's webhook and CDC answers remain **No**.

Validation: the full PostgreSQL suite passed **472 tests and 39 subtests**. After adding
extra input-validation and pause-during-scan coverage, the focused reconciliation/payment
suite passed **39 tests and 15 subtests**. Coverage includes two concurrent PostgreSQL workers,
due-order fairness, retry identity/backoff, stale-attempt recovery, paid-invoice reversal,
administrator/company/CSRF boundaries, and review-required invoice discrepancies. Ruff,
Django system checks, migration consistency, and whitespace checks passed. The live sandbox
reconciled all four active mapped invoices with matching zero balances and no unresolved
payment checks; an immediate second pass performed no duplicate work. Automatic updates are
enabled for Sandbox Company US 132a only. The local background worker independently picked up
a due sandbox invoice, completed it successfully, and updated the connection page's healthy
worker status. Page content was verified through Django; browser layout validation was not
available. Production remains undeployed.

Invoice payment-status follow-up (October 15): invoice managers can now see whether payment
checks are scheduled, queued, running, paused, delayed, awaiting retry, or require review.
The page shows the most recent successful **balance-verifying** payment check; an invoice
refresh or older manual import is not presented as a verified balance. An unresolved older
failure remains visible even after a later manual success, matching the worker's blocking
rules. Company administrators receive a company-scoped review/settings link; the error queue
links directly to each affected invoice. Client pages receive neither internal error details
nor these controls. Rendering uses local records only and does not contact QuickBooks.
Permission, escaping, company isolation, retry eligibility, and stale-worker cases have
regression coverage. No schema changes or deployment are needed for local use.
Validation: **489 tests and 47 subtests passed on PostgreSQL**, including 12 new payment-status
tests. Ruff, Django system checks, migration consistency, and whitespace checks passed.
The local sandbox invoice rendered the new status and scoped controls successfully; the
local HTTPS server was restarted. These updates are published only to the release branch,
leaving the auto-deploying `main` branch and client billing approval boundary unchanged.

- Company administrators can enable or pause updates from the QuickBooks connection page.
  New connections default to off. Only connected companies matching the configured
  sandbox/production environment are eligible. Pausing stops subsequent invoice checks;
  an already-running check may finish. Reconnection preserves the prior preference.
- `reconcile_quickbooks_payments --loop` checks for due work every minute. Each mapped
  invoice is normally eligible again 15 minutes after its previous check. Each pass handles
  at most 25 invoices per company, configurable with `--limit` (1–100), oldest due first.
  Large queues and long API responses increase latency; this is not a 15-minute SLA.
- Each automatic scan reads the invoice before importing, re-verifies existing Payment
  applications, queries for new customer-scoped applications, and reads the invoice again
  to compare its balance against the local ledger. Invoice/customer identity, currency when
  known, tax, and total must agree. Changed/voided/deleted invoices and balance discrepancies
  require review; issued local totals are never silently rewritten. Only confirmed payment
  applications change the local ledger. No accounting writes are sent to QuickBooks.
- Automatic transient failures retain their operation identity and bounded backoff. Permanent
  failures, exhausted retries, possible duplicates, and unresolved manual payment failures
  block new automatic attempts for that invoice until an administrator reviews/resolves them.
  Other invoices continue. Existing manual retry and resolution controls remain available.
- A per-company PostgreSQL session lock prevents overlapping workers. Stale running payment
  reads can resume after 15 minutes using the same durable attempt and existing execution lock.
  The worker does not recover or retry outbound customer/item/invoice writes.
- The connection page displays worker check-in time and unresolved payment-check count, with
  a warning after five minutes without a check-in. Attempt history retains individual results;
  mappings retain last-check and next-due timestamps. External uptime alerts, API-volume
  metrics, and automatic pruning of historical attempts remain production operations work.

**Worker operation:** apply migrations first, then run the following alongside the web service,
using the same database and QuickBooks configuration (including encryption keys and environment):

```text
python manage.py reconcile_quickbooks_payments --dry-run
python manage.py reconcile_quickbooks_payments --loop
```

Omit `--loop` for one bounded pass. The optional Docker Compose profile can be started with
`docker compose --profile payment-updates up -d payment-worker`. Production needs a supervised
worker with automatic restart and a direct/session-pooled PostgreSQL connection; transaction
pooling is incompatible with the session locks. Stop the process or pause a company to stop
polling. Resolve a flagged discrepancy in both ledgers, then document the resolution in the
error queue; the next due check will verify it again.

For Render, prepare a separate [background worker](https://render.com/docs/background-workers)
from the same release. Set its Docker Command to
`/app/docker-entrypoint.sh python manage.py reconcile_quickbooks_payments --loop`, set
`RUN_STARTUP_TASKS=false`, and supply the same database and QuickBooks environment as the web
service. The worker checks for unapplied migrations but does not migrate or bootstrap a user.
The web deployment retains its normal migration/bootstrap startup. Deploy it successfully
before starting the worker. Shell scripts are committed with LF endings; local certificates,
keys, and agent state are excluded from the Docker build context.

Release preparation on October 15: merged the deployed `ccb2143` onboarding/dashboard release
into the QuickBooks release branch, preserving its Help pages and role-aware changes.
The combined PostgreSQL suite passed **477 tests and 47 subtests**; Ruff, migration consistency,
Django checks, static asset collection, and shell syntax checks also passed. A Render worker
configuration was prepared but not submitted: `construction-manager-payments`, Ohio,
0.5 CPU / 512 MB, quoted at $7/month. The existing hosted web service has no QuickBooks
environment variables configured yet; no cloud worker has been created.

**Client deployment and billing decision (October 15, 2026):** this site is for a client.
Continue local development and publish source changes to the GitHub release branch
`codex/automatic-quickbooks-payments`. Keep `main` unchanged because it automatically
deploys to Mike's existing Render workspace. Do not deploy, provision paid services,
transfer credentials to Render, or charge Mike's card. Wait for the client's approval;
Mike will then open the client's workspace and arrange its payment method. Verify that
workspace, its database, and its billing ownership before any future deployment. The
previously prepared form in Mike's workspace is not an approved deployment destination.

Local execution stops when its host/process stops; enabling the switch alone does not launch
a worker. Docker image execution could not be verified locally because Docker Engine is not running.

Remaining QB-4 scope:

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
  linked to a QuickBooks Item; outbound Invoice sync now uses that mapping; live sandbox verification remains under QB-3.

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
- [x] Import QuickBooks credit applications through Payment Invoice links, including
  zero-cash payments. The old direct CreditMemo-to-invoice scan was removed because document
  totals are not applied amounts. Existing direct-credit mappings block new scans pending
  reviewed reconciliation; local credit-memo application remains supported.
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
