from dataclasses import dataclass
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.utils import timezone


class QuickBooksOAuthError(Exception):
    def __init__(self, code, public_message):
        self.code = code
        self.public_message = public_message
        super().__init__(public_message)


@dataclass(frozen=True)
class QuickBooksTokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_token_expires_in: int | None
    scopes: tuple[str, ...]


class QuickBooksOAuthClient:
    def authorization_url(self, state):
        query = urlencode(
            {
                'client_id': settings.QUICKBOOKS_CLIENT_ID,
                'redirect_uri': settings.QUICKBOOKS_REDIRECT_URI,
                'response_type': 'code',
                'scope': ' '.join(settings.QUICKBOOKS_SCOPES),
                'state': state,
            }
        )
        return f'{settings.QUICKBOOKS_AUTHORIZATION_URL}?{query}'

    def exchange_code(self, code):
        return self._token_request(
            {
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': settings.QUICKBOOKS_REDIRECT_URI,
            }
        )

    def refresh(self, refresh_token):
        return self._token_request(
            {
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            }
        )

    def revoke(self, token):
        try:
            response = requests.post(
                settings.QUICKBOOKS_REVOKE_URL,
                json={'token': token},
                auth=(
                    settings.QUICKBOOKS_CLIENT_ID,
                    settings.QUICKBOOKS_CLIENT_SECRET,
                ),
                headers={'Accept': 'application/json'},
                timeout=settings.QUICKBOOKS_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise QuickBooksOAuthError(
                'revoke_unavailable',
                'QuickBooks could not be reached to revoke this connection.',
            ) from exc
        if response.status_code != 200:
            raise QuickBooksOAuthError(
                'revoke_failed',
                'QuickBooks did not accept the disconnect request.',
            )

    def _token_request(self, data):
        try:
            response = requests.post(
                settings.QUICKBOOKS_TOKEN_URL,
                data=data,
                auth=(
                    settings.QUICKBOOKS_CLIENT_ID,
                    settings.QUICKBOOKS_CLIENT_SECRET,
                ),
                headers={'Accept': 'application/json'},
                timeout=settings.QUICKBOOKS_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise QuickBooksOAuthError(
                'token_endpoint_unavailable',
                'QuickBooks could not be reached to complete authorization.',
            ) from exc
        if response.status_code != 200:
            raise QuickBooksOAuthError(
                'token_exchange_failed',
                'QuickBooks did not accept the authorization request.',
            )
        try:
            payload = response.json()
            access_token = payload['access_token']
            refresh_token = payload['refresh_token']
            expires_in = int(payload['expires_in'])
            refresh_expires = payload.get('x_refresh_token_expires_in')
            if refresh_expires is not None:
                refresh_expires = int(refresh_expires)
            scope_value = payload.get('scope', '')
            scopes = tuple(scope_value.split())
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise QuickBooksOAuthError(
                'invalid_token_response',
                'QuickBooks returned an incomplete authorization response.',
            ) from exc
        if (
            not isinstance(access_token, str)
            or not isinstance(refresh_token, str)
            or not access_token
            or not refresh_token
            or expires_in <= 0
        ):
            raise QuickBooksOAuthError(
                'invalid_token_response',
                'QuickBooks returned an incomplete authorization response.',
            )
        return QuickBooksTokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            refresh_token_expires_in=refresh_expires,
            scopes=scopes,
        )


class QuickBooksAPIError(Exception):
    def __init__(
        self,
        code,
        public_message,
        *,
        status_code=None,
        retryable=False,
    ):
        self.code = str(code)
        self.public_message = public_message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(public_message)

    @property
    def is_feature_unsupported(self):
        return self.code == '5030'

    @property
    def is_not_found(self):
        return self.code in {'610', '404'} or self.status_code == 404


class QuickBooksAccountingClient:
    HOSTS = {
        'sandbox': 'https://sandbox-quickbooks.api.intuit.com',
        'production': 'https://quickbooks.api.intuit.com',
    }

    def get_company_info(self, connection):
        return self._get_entity(
            connection,
            f'companyinfo/{connection.realm_id}',
            'CompanyInfo',
        )

    def get_preferences(self, connection):
        return self._get_entity(connection, 'preferences', 'Preferences')

    def get_customer(self, connection, customer_id):
        return self._get_entity(
            connection,
            f'customer/{quote(str(customer_id), safe="")}',
            'Customer',
        )

    def create_customer(self, connection, customer, *, request_id):
        return self._write_entity(
            connection,
            'customer',
            'Customer',
            customer,
            request_id=request_id,
        )

    def update_customer(self, connection, customer, *, request_id):
        if not customer.get('Id') or customer.get('SyncToken') is None:
            raise QuickBooksAPIError(
                'missing_sync_identity',
                'A QuickBooks customer update requires its ID and latest sync token.',
            )
        payload = dict(customer)
        payload['sparse'] = True
        return self._write_entity(
            connection,
            'customer',
            'Customer',
            payload,
            request_id=request_id,
        )

    def find_customers_by_display_name(self, connection, display_name):
        escaped = str(display_name).replace('\\', '\\\\').replace("'", "\\'")
        query = (
            "SELECT * FROM Customer WHERE DisplayName = "
            f"'{escaped}' MAXRESULTS 2"
        )
        return self._query_customers(connection, query)

    def iter_customers(self, connection, *, page_size=100):
        if page_size < 1 or page_size > 1000:
            raise ValueError('page_size must be between 1 and 1000.')
        start_position = 1
        while True:
            query = (
                'SELECT * FROM Customer WHERE Active IN (true, false) '
                f'STARTPOSITION {start_position} MAXRESULTS {page_size}'
            )
            customers = self._query_customers(connection, query)
            yield from customers
            if len(customers) < page_size:
                break
            start_position += len(customers)

    def _query_customers(self, connection, query):
        payload = self._request(
            connection,
            'query',
            params={'query': query},
        )
        query_response = payload.get('QueryResponse') if isinstance(payload, dict) else None
        if not isinstance(query_response, dict):
            raise QuickBooksAPIError(
                'invalid_api_response',
                'QuickBooks returned an incomplete customer query response.',
            )
        customers = query_response.get('Customer') or []
        if not isinstance(customers, list) or not all(
            isinstance(customer, dict) for customer in customers
        ):
            raise QuickBooksAPIError(
                'invalid_api_response',
                'QuickBooks returned an invalid customer list.',
            )
        return customers

    def _write_entity(
        self,
        connection,
        resource,
        entity_name,
        entity,
        *,
        request_id,
    ):
        payload = self._request(
            connection,
            resource,
            method='post',
            json_body=entity,
            params={'requestid': request_id},
        )
        result = payload.get(entity_name) if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise QuickBooksAPIError(
                'invalid_api_response',
                f'QuickBooks returned incomplete {entity_name} information.',
            )
        return result

    def _get_entity(self, connection, resource, entity_name):
        payload = self._request(connection, resource)
        entity = payload.get(entity_name) if isinstance(payload, dict) else None
        if not isinstance(entity, dict):
            raise QuickBooksAPIError(
                'invalid_api_response',
                f'QuickBooks returned incomplete {entity_name} information.',
            )
        return entity

    def _request(
        self,
        connection,
        resource,
        *,
        method='get',
        json_body=None,
        params=None,
        allow_refresh=True,
    ):
        if connection.environment != settings.QUICKBOOKS_ENVIRONMENT:
            raise QuickBooksAPIError(
                'environment_mismatch',
                'This QuickBooks connection belongs to a different environment.',
            )
        if connection.status == connection.Status.DISCONNECTED:
            raise QuickBooksAPIError(
                'connection_disconnected',
                'Reconnect QuickBooks before requesting company information.',
            )
        if (
            connection.access_token_expires_at
            and connection.access_token_expires_at <= timezone.now()
        ):
            connection = self._refresh(connection)

        host = self.HOSTS[connection.environment]
        url = f'{host}/v3/company/{connection.realm_id}/{resource}'
        request_params = {'minorversion': settings.QUICKBOOKS_MINOR_VERSION}
        request_params.update(params or {})
        request_kwargs = {
            'params': request_params,
            'headers': {
                'Accept': 'application/json',
                'Authorization': f'Bearer {connection.access_token}',
            },
            'timeout': settings.QUICKBOOKS_HTTP_TIMEOUT_SECONDS,
        }
        if json_body is not None:
            request_kwargs['json'] = json_body
        try:
            request_method = requests.post if method == 'post' else requests.get
            response = request_method(url, **request_kwargs)
        except requests.RequestException as exc:
            raise QuickBooksAPIError(
                'api_unavailable',
                'QuickBooks could not be reached. Try again later.',
                retryable=True,
            ) from exc

        if response.status_code == 401 and allow_refresh:
            connection = self._refresh(connection)
            return self._request(
                connection,
                resource,
                method=method,
                json_body=json_body,
                params=params,
                allow_refresh=False,
            )

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise QuickBooksAPIError(
                'invalid_api_response',
                'QuickBooks returned an unreadable response.',
                status_code=response.status_code,
            ) from exc

        if response.status_code >= 400 or (
            isinstance(payload, dict) and payload.get('Fault')
        ):
            raise self._api_error(response.status_code, payload)
        return payload

    def _refresh(self, connection):
        from .services import refresh_connection

        try:
            return refresh_connection(connection.pk)
        except QuickBooksOAuthError as exc:
            raise QuickBooksAPIError(
                exc.code,
                exc.public_message,
                status_code=401,
            ) from exc

    @staticmethod
    def _api_error(status_code, payload):
        error = {}
        if isinstance(payload, dict):
            fault = payload.get('Fault') or {}
            errors = fault.get('Error') or []
            if errors and isinstance(errors[0], dict):
                error = errors[0]
        code = str(error.get('code') or status_code or 'api_error')
        if status_code == 401 or code in {'120', '3200'}:
            message = 'QuickBooks authorization is no longer valid. Reconnect the company.'
        elif status_code == 429:
            message = 'QuickBooks is temporarily rate-limiting requests. Try again later.'
        elif code == '5030':
            message = 'This feature is not available for the connected QuickBooks company.'
        elif code == '6190':
            message = 'This QuickBooks subscription is currently read-only.'
        elif code in {'5010', '2020'}:
            message = 'The QuickBooks record changed. Refresh it before retrying.'
        elif code == '6240':
            message = 'That name is already used by a QuickBooks customer, vendor, or employee.'
        elif code in {'610', '404'} or status_code == 404:
            message = 'The requested QuickBooks record no longer exists.'
        else:
            message = 'QuickBooks could not complete the requested operation.'
        return QuickBooksAPIError(
            code,
            message,
            status_code=status_code,
            retryable=(
                status_code in {429, 500, 502, 503, 504}
                or code in {'5010', '2020'}
            ),
        )
