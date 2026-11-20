# Selections and reusable templates

Project Selections offers a category- or location-grouped table with choice photos,
client prices, remaining allowance, deadlines, status, and files. Filters and card
view use the same server-side visibility rules. Print includes expanded choices.

Use **Load / manage templates** from any managed project's Selections page:

- **New template** creates an empty company template. Edit category, title,
  location, allowance, and order directly in the grid. Save rows before adding
  choices, photos, and specification files.
- The grid edits 50 existing rows per page, with room to add more rows. Save
  before switching pages. A version check prevents stale grid saves overwriting
  newer template changes.
- **Save as new template** copies selected project rows, descriptions, allowances,
  client prices, choices, photos, and files into a new template.
- **Copy template** creates an independent copy, including private file copies.
- **Load into project** selects a managed destination, then rows, an optional
  new-row deadline, and an action. **Amend** adds missing rows and choices while
  preserving existing choices and prices. **Add only** skips existing rows.
  **Replace** overwrites matching unreleased rows and all their choices, including
  photos and client prices. It resets internal costs on replaced choices and
  requires confirmation. Other project rows and existing deadlines remain.
- **Template replacement history** provides Undo for replaced rows and old-report
  note cleanup. It restores original choices, private photos and internal costs.
  Undo refuses to overwrite later edits or released rows; new rows added during
  the load remain. Replacing uses current project/template revision checks.
- **Delete template** removes it from the active library. **Deleted templates**
  provides Restore. Existing projects and other template copies are unchanged.

Template loads match the original row key first, then a case-insensitive match on
title, location, and category. Ambiguous matches are skipped and reported. Only
unreleased rows can be amended/replaced. They do not carry approvals, client comments,
old deadlines, imported totals, invoices, change orders, or internal costs.
Review client prices and enter internal costs before publishing project rows.
Project financial totals and approval workflows remain unchanged.

Imported report text is not a new approval. Recognized Buildertrend report blocks
(Selection Total / Remaining from Allowance / Status/Alerts) are removed from
reusable descriptions; ordinary instructions mentioning approval are retained.
Amend replaces such historical blocks in matched draft rows with the saved
template description and records an undo snapshot. Original imported job history
is retained on the source project. Newly copied or loaded rows are Unreleased.

Use **Add selection**, **Copy**, **Edit**, **Add choice**, and **Delete** in the
project list. Copy includes choices and private file copies, but no approvals,
old deadlines or internal costs. Delete moves unreleased rows to **Deleted
selections**, where Restore retains choices, photos and original costs.
**Delete loaded template** lets staff select template-sourced unreleased rows to
remove from that project. It does not delete shared templates, manual rows or
released selections. Restoring refuses to create an active matching duplicate.

Operational recovery: `repair_selection_template --project ID --template ID
--actor EMAIL` reports missing choices, historical-note counts and a revision
without writing. Add `--apply --expected-revision TOKEN` only after reviewing the
target/counts; stale revisions fail closed. Repair only amends matched draft rows,
adds no selection rows, and cleans recognized old report notes in draft copies.
It never changes the source template, original imported job, approvals or costs.
Retain private files referenced by SelectionTemplateReplacement undo snapshots;
do not prune files solely because a current SelectionOption no longer uses them.

Company administrators and internal users with active project-management access
can use their company's shared template library. Loading also requires management
access to the destination project. Clients, subcontractors, unassigned viewers,
and other companies cannot access templates or template media. All images and
files use authenticated private-storage routes; no public storage URLs are used.
Template actions are recorded in company activity. New forms use the existing
CSRF and Cloudflare form protection.

Implementation reference: reviewed Heritage's open Buildertrend Selections List,
choice detail, template creation, and template import screens on November 19,
2026. Its My Templates list was empty. No Buildertrend records were changed or
imported during this UI review. This implementation reproduces the requested
selection/template workflow, not Buildertrend's complete job-template suite,
shared allowances, schedule-linked deadlines, or multi-choice approvals.
