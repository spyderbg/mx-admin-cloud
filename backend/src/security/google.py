"""Verification of the Google ID tokens behind "Log in with Google"."""

from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

# Tolerated clock difference between this host and Google when checking a token's times.
_CLOCK_SKEW_SECONDS = 10


class GoogleCredentialError(Exception):
    """Raised when a Google credential is invalid, expired, or carries no verified email."""


class GoogleVerificationUnavailableError(Exception):
    """Raised when Google's signing certificates cannot be fetched to verify a credential."""


def verify_google_credential(credential: str, client_id: str) -> str:
    """Return the verified email of the Google account that signed in with *credential*.

    *credential* is the ID token Google Identity Services hands the browser. Its signature,
    issuer and expiry are checked, as is that it was issued for *client_id* and that Google
    verified the account's email.
    """
    try:
        claims = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            audience=client_id,
            clock_skew_in_seconds=_CLOCK_SKEW_SECONDS,
        )
    except google_exceptions.TransportError as exc:
        raise GoogleVerificationUnavailableError("Could not reach Google to verify the sign-in.") from exc
    except (ValueError, google_exceptions.GoogleAuthError) as exc:
        raise GoogleCredentialError("Invalid Google sign-in.") from exc

    email = claims.get("email")
    if not email or claims.get("email_verified") is not True:
        raise GoogleCredentialError("The Google account has no verified email address.")
    return str(email)
