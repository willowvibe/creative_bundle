"""Typed publish errors with a plain-English next step.

Every error carries a stable ``code`` for callers to branch on and a
``next_step`` a person can act on without reading the source.
"""

from __future__ import annotations


class SocialError(Exception):
    """Base error for every platform and both publish lanes."""

    code = "social_error"
    next_step = "Read the message and retry."

    def __init__(
        self,
        message: str,
        *,
        next_step: str | None = None,
        code: str | None = None,
        screenshot: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.next_step = next_step or type(self).next_step
        self.screenshot = screenshot


class QuotaExceeded(SocialError):
    code = "quota_exceeded"
    next_step = (
        "Wait for the daily quota to reset (midnight Pacific), or publish through "
        "the browser lane with `social publish <manifest> --via browser`."
    )


class UploadLimitExceeded(SocialError):
    code = "upload_limit_exceeded"
    next_step = (
        "Wait 24 hours before uploading again, or use the browser lane "
        "(`social publish <manifest> --via browser`)."
    )


class PrivateLocked(SocialError):
    code = "private_locked"
    next_step = (
        "The Google Cloud project is unaudited, so the upload stays private. Use the "
        "browser lane until Google approves the API compliance audit, then set "
        "`api_audited: true` on the account."
    )


class AuthExpired(SocialError):
    code = "auth_expired"
    next_step = "Run `social auth <platform> <account>` again and consent in the browser."


class WrongAccount(SocialError):
    code = "wrong_account"
    next_step = (
        "Run `social auth <platform> <account>` again and sign in with the owner "
        "account of the channel this account names."
    )


class ThumbnailNotAllowed(SocialError):
    code = "thumbnail_not_allowed"
    next_step = (
        "Verify the channel by phone at https://www.youtube.com/verify (custom "
        "thumbnails need a verified channel), then retry."
    )


class BrowserNeedsUser(SocialError):
    code = "browser_needs_user"
    next_step = (
        "Complete the step in the open Chrome window (sign-in, 2FA, CAPTCHA or an "
        "account chooser), then rerun the same command."
    )


class BrowserUnexpectedScreen(SocialError):
    code = "browser_unexpected_screen"
    next_step = (
        "Open the saved screenshot, finish or close the unexpected browser screen, "
        "then rerun the same command."
    )


class AlreadyPublished(SocialError):
    code = "already_published"
    next_step = (
        "Run `social update <manifest>` to change the live video instead of "
        "uploading it again."
    )
