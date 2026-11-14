"""YouTube OAuth: one installed-app flow per named account.

The owner completes the consent screen by hand; the token lands under the state
home with mode 0600. Every later call checks the channel id stored on the
account so a token cannot silently publish to the wrong channel.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
from googleapiclient.discovery import build  # type: ignore[import-untyped]

from ...core.accounts import load_account, save_account
from ...core.errors import AuthExpired, SocialError, WrongAccount
from ...core.paths import client_secret_path, token_path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

FlowFactory = Callable[..., Any]
ServiceFactory = Callable[..., Any]
CredentialsFactory = Callable[..., Any]
RequestFactory = Callable[..., Any]


def _owner_setup_next_step() -> str:
    path = client_secret_path("youtube")
    return (
        "Google Cloud project -> enable YouTube Data API v3 and YouTube Analytics API -> "
        "OAuth consent screen (External, add yourself as a test user, then publish to "
        "production so tokens do not expire after 7 days) -> OAuth client 'Desktop app' -> "
        f"save as {path}."
    )


def _require_client_secret() -> Path:
    path = client_secret_path("youtube")
    if not path.is_file():
        raise SocialError(
            f"YouTube OAuth client secret is missing: {path}",
            code="missing_client_secret",
            next_step=_owner_setup_next_step(),
        )
    return path


def _write_token(path: Path, credentials: Credentials) -> None:
    """Write the token JSON with mode 0600, replacing any previous file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(credentials.to_json())
    os.chmod(path, 0o600)


def _channel_id(credentials: Credentials, service_factory: ServiceFactory) -> str:
    service = service_factory("youtube", "v3", credentials=credentials, cache_discovery=False)
    response = service.channels().list(mine=True, part="id").execute()
    items = response.get("items") or []
    channel_id = items[0].get("id") if items else None
    if not channel_id:
        raise SocialError(
            "the signed-in Google account has no YouTube channel",
            next_step="Sign in with the Google account that owns the channel, then retry.",
        )
    return str(channel_id)


def authorize(
    account: str,
    *,
    flow_factory: FlowFactory = InstalledAppFlow.from_client_secrets_file,
    service_factory: ServiceFactory = build,
) -> Credentials:
    """Run the installed-app consent flow and remember the account's channel id.

    The token and account files are only written after the signed-in channel has
    been read and matched against the id already stored on the account.
    """
    secret = _require_client_secret()
    flow = flow_factory(str(secret), SCOPES)
    credentials = flow.run_local_server(port=0)
    channel_id = _channel_id(credentials, service_factory)

    record = load_account("youtube", account)
    if record.platform_account_id and record.platform_account_id != channel_id:
        raise WrongAccount(
            f"the signed-in channel is {channel_id}, but account {account!r} is "
            f"{record.platform_account_id}."
        )

    _write_token(token_path("youtube", account), credentials)
    record.platform_account_id = channel_id
    save_account(record)
    return credentials


def load_credentials(
    account: str,
    *,
    credentials_factory: CredentialsFactory = Credentials.from_authorized_user_file,
    request_factory: RequestFactory = Request,
) -> Credentials:
    """Load an account's token, refreshing and rewriting it when it has expired."""
    _require_client_secret()
    path = token_path("youtube", account)
    if not path.is_file():
        raise AuthExpired(
            f"no YouTube token for account {account!r} at {path}.",
            next_step="Run `social auth youtube <account>` and consent in the browser.",
        )

    try:
        credentials = credentials_factory(str(path), SCOPES)
    except (ValueError, OSError) as exc:
        raise AuthExpired(
            f"the YouTube token for account {account!r} is unreadable: {path}",
            next_step="Run `social auth youtube <account>` again and consent in the browser.",
        ) from exc

    if credentials.expired:
        if not credentials.refresh_token:
            raise AuthExpired(
                f"the YouTube token for account {account!r} has expired and cannot refresh.",
                next_step="Run `social auth youtube <account>` again and consent in the browser.",
            )
        try:
            credentials.refresh(request_factory())
        except RefreshError as exc:
            raise AuthExpired(
                f"the YouTube token for account {account!r} could not be refreshed: {exc}",
            ) from exc
        _write_token(path, credentials)
    return credentials
