"""Shared synchronization errors, independent of individual entity executors."""


class QuickBooksSyncError(Exception):
    pass


class QuickBooksSyncBusy(QuickBooksSyncError):
    pass
