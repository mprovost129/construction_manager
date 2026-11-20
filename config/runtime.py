"""Bound web database waits without imposing web limits on migrations/imports."""


def database_runtime_settings(database, *, management_command=False):
    configured = {**database, 'OPTIONS': dict(database.get('OPTIONS', {}))}
    configured['CONN_MAX_AGE'] = 60
    configured['CONN_HEALTH_CHECKS'] = True
    configured['OPTIONS']['connect_timeout'] = 3
    if not management_command:
        existing = configured['OPTIONS'].get('options', '')
        configured['OPTIONS']['options'] = (
            f'{existing} -c statement_timeout=15000 -c lock_timeout=5000'
        ).strip()
    return configured
