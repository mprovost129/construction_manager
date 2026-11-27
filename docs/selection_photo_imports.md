# Selection reference photo transfers

Project managers with manage access can open **Selections → Manage reference photos**.
Import packages add private supporting photos to existing imported-history rows. They do not create choices, prices, approvals, signatures, notifications, or accounting transactions.

Packages are ZIP files under 50 MB containing `manifest.json` and PNG, JPEG, or WebP images. The manifest has `version: 1`, the destination `project_id`, and a `photos` list. Each entry requires `selection_id`, the exact stored `selection_title`, a plain `caption`, a flat `file` name, the file's `sha256`, and a stable `source_key` formatted `buildertrend:job_id:choice_id:file_id`. Keep source packages and financial/project data out of Git.

The whole package is checked before any upload: project and imported-row identity, bounded archive/image sizes, image decoding, checksums, and source keys. Archives are never extracted to the filesystem. Imports lock the project and skip a repeated source or identical content on the same selection. A changed source photo is rejected rather than overwritten. New storage objects are cleaned up on a failed transaction. Activity records identify counts and visibility changes.

Photos start as **internal references**. Managers can review each photo and explicitly share it with that project's client using its checkbox. Both the image route and the selection board enforce visibility; private media has no public storage URL and responses use `private, no-store`. Historical source descriptions remain staff-only, including in expanded board rows.

Photo captions are references, not approval claims. Unmatched or ambiguous selections must be reviewed separately. Never infer a new approval or match a photo to a different job just to complete a transfer.
