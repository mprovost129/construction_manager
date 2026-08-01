"""Accounting ownership rules used by future entity synchronization services."""

ENTITY_SYNC_POLICIES = {
    'customer': {
        'source_of_truth': 'quickbooks',
        'local_origin': 'allowed_before_first_sync',
        'conflict_resolution': 'quickbooks_wins',
        'deletion': 'retain_local_and_tombstone_mapping',
    },
    'invoice': {
        'source_of_truth': 'quickbooks_after_first_sync',
        'local_origin': 'drafts_allowed',
        'conflict_resolution': 'quickbooks_wins_after_first_sync',
        'deletion': 'retain_local_and_mark_external_state',
    },
    'credit_memo': {
        'source_of_truth': 'quickbooks',
        'local_origin': 'not_allowed',
        'conflict_resolution': 'quickbooks_wins',
        'deletion': 'retain_local_and_mark_external_state',
    },
    'payment': {
        'source_of_truth': 'quickbooks',
        'local_origin': 'not_allowed',
        'conflict_resolution': 'quickbooks_wins',
        'deletion': 'retain_local_and_mark_external_state',
    },
}
