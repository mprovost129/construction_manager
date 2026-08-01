from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from django.conf import settings


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
