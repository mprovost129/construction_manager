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
- **Save as template** copies selected project rows, descriptions, allowances,
  client prices, choices, photos, and files into a new template.
- **Copy template** creates an independent copy, including private file copies.
- **Load into project** selects a managed destination, then rows and an optional
  new deadline. It adds unreleased rows; it does not replace existing selections.
- **Delete template** removes it from the active library. **Deleted templates**
  provides Restore. Existing projects and other template copies are unchanged.

Template loads skip a previously loaded row key or a case-insensitive match on
title, location, and category. They do not carry approvals, client comments,
old deadlines, imported totals, invoices, change orders, or internal costs.
Review client prices and enter internal costs before publishing project rows.
Project financial totals and approval workflows remain unchanged.

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
