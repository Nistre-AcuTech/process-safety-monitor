import logging
import time

import requests

import config

logger = logging.getLogger(__name__)


class ZohoClient:
    """Zoho CRM client with OAuth2 token refresh and account search."""

    def __init__(self):
        self.client_id = config.ZOHO_CLIENT_ID
        self.client_secret = config.ZOHO_CLIENT_SECRET
        self.refresh_token = config.ZOHO_REFRESH_TOKEN
        region = config.ZOHO_API_REGION

        self.accounts_url = f"https://accounts.zoho.{region}"
        self.api_url = f"https://www.zohoapis.{region}/crm/v7"

        self.access_token: str | None = None
        self.token_expiry: float = 0

        # In-memory cache of all account names (loaded once per run)
        self._account_cache: dict[str, dict] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _refresh_access_token(self):
        """Exchange refresh token for a new access token."""
        resp = requests.post(
            f"{self.accounts_url}/oauth/v2/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"Zoho token refresh failed: {data}")

        self.access_token = data["access_token"]
        self.token_expiry = time.time() + data.get("expires_in", 3600) - 60
        logger.info("Zoho access token refreshed")

    def _headers(self) -> dict[str, str]:
        if not self.access_token or time.time() >= self.token_expiry:
            self._refresh_access_token()
        return {"Authorization": f"Zoho-oauthtoken {self.access_token}"}

    def _load_all_accounts(self) -> dict[str, dict]:
        """Fetch all account names from Zoho CRM (paginated). Returns dict keyed by lowercase name."""
        accounts: dict[str, dict] = {}
        page = 1

        while True:
            resp = requests.get(
                f"{self.api_url}/Accounts",
                headers=self._headers(),
                params={
                    "fields": "Account_Name,Website,Industry",
                    "per_page": 200,
                    "page": page,
                },
                timeout=15,
            )

            if resp.status_code == 204:
                break

            resp.raise_for_status()
            data = resp.json()

            for record in data.get("data", []):
                name = record.get("Account_Name", "")
                if name:
                    accounts[name.strip().lower()] = record

            if not data.get("info", {}).get("more_records"):
                break
            page += 1

        logger.info("Loaded %d accounts from Zoho CRM", len(accounts))
        return accounts

    def get_account_cache(self) -> dict[str, dict]:
        """Return cached account dict, loading on first call."""
        if self._account_cache is None:
            self._account_cache = self._load_all_accounts()
        return self._account_cache

    def find_matching_account(self, text: str) -> str | None:
        """Check if any Zoho account name appears in the given text.

        Returns the account name if found, None otherwise.
        Uses simple substring matching against the cached account list.
        """
        if not self.configured:
            return None

        cache = self.get_account_cache()
        text_lower = text.lower()

        for account_name_lower, record in cache.items():
            # Skip very short names (< 4 chars) to avoid false positives
            if len(account_name_lower) < 4:
                continue
            if account_name_lower in text_lower:
                return record.get("Account_Name", account_name_lower)

        return None
