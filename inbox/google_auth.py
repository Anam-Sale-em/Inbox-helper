"""Google sign-in for the optional Gmail and Calendar sources.

Read-only on purpose: the token can read mail and see when the recruiter is busy, but it can't
send, delete or change anything, and it can't see event titles or guests. Sign in once from a
terminal (`python -m inbox.google_auth`); after that the review screen reuses token.json.
credentials.json and token.json stay on your machine (both are git-ignored).
"""

import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/calendar.freebusy"]
CREDENTIALS_FILE = Path(os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"))
TOKEN_FILE = Path(os.getenv("GOOGLE_TOKEN_FILE", "token.json"))


class GoogleSetupError(RuntimeError):
    """Something the recruiter has to do once: install the libraries, add credentials.json, sign in."""


def credentials(interactive: bool = False):
    """A valid read-only token. interactive=True opens the browser to sign in when needed."""
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise GoogleSetupError("Gmail and Calendar need the Google libraries: "
                               "pip install -r requirements-google.txt") from exc

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES) if TOKEN_FILE.exists() else None
    if creds and not creds.has_scopes(SCOPES):
        creds = None  # signed in with other permissions before: ask again
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except RefreshError:
            creds = None  # revoked or expired (test apps expire after 7 days): sign in again

    if not interactive:
        raise GoogleSetupError("Not signed in to Google yet. In a terminal, run: python -m inbox.google_auth")
    if not CREDENTIALS_FILE.exists():
        raise GoogleSetupError(f"{CREDENTIALS_FILE} not found. Download the Desktop-app OAuth client from "
                               "Google Cloud and save it there (README: Gmail and Google Calendar).")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def build_service(name: str, version: str, interactive: bool = False):
    """A Google API client, e.g. build_service("gmail", "v1")."""
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GoogleSetupError("Gmail and Calendar need the Google libraries: "
                               "pip install -r requirements-google.txt") from exc
    return build(name, version, credentials=credentials(interactive), cache_discovery=False)


if __name__ == "__main__":
    credentials(interactive=True)
    print(f"Signed in. Read-only access saved to {TOKEN_FILE.resolve()}")
