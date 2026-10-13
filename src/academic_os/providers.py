from __future__ import annotations

import base64
import json
import threading
import time
from email.message import EmailMessage
from urllib.parse import quote

import httpx
from .network import GoogleHTTP


class ProviderError(RuntimeError):
    def __init__(self, status, code="provider_error"):
        self.status, self.code = status, code
        super().__init__(f"{code} (HTTP {status})")


class Google:
    def __init__(self, secret_store, secret_ref, proxy=None, network_mode='auto'):
        self.secrets, self.secret_ref = secret_store, secret_ref
        self.http = GoogleHTTP(proxy=proxy, mode=network_mode, timeout=httpx.Timeout(15, connect=5))
        self._access, self._expires = None, 0
        self._lock = threading.Lock()

    def access_token(self):
        with self._lock:
            if self._access and time.time() < self._expires - 60:
                return self._access
            credential = json.loads(self.secrets.get(self.secret_ref))
            response = self.http.post("https://oauth2.googleapis.com/token", data={"client_id": credential["client_id"], "client_secret": credential["client_secret"], "refresh_token": credential["refresh_token"], "grant_type": "refresh_token"})
            if response.status_code != 200:
                code = 'google_reauthorization_required' if response.status_code == 400 and response.json().get('error') == 'invalid_grant' else 'google_authorization_failed'
                raise ProviderError(response.status_code, code)
            payload = response.json()
            self._access = payload["access_token"]
            self._expires = time.time() + payload.get("expires_in", 3600)
            return self._access

    def request(self, method, path, params=None, body=None, headers=None):
        origin = "https://gmail.googleapis.com" if path.startswith("/gmail/") else "https://www.googleapis.com"
        request_headers = {"Authorization": "Bearer " + self.access_token(), **(headers or {})}
        response = self.http.request(method, origin + path, params=params, json=body, headers=request_headers)
        if response.status_code == 401:
            self._expires = 0
            request_headers["Authorization"] = "Bearer " + self.access_token()
            response = self.http.request(method, origin + path, params=params, json=body, headers=request_headers)
        if response.status_code >= 400:
            raise ProviderError(response.status_code, "google_request_failed")
        return response.json() if response.content else {}

    def list_events(self, calendar_id, sync_token=None, page_token=None):
        params = {"maxResults": 250, "showDeleted": "true", "singleEvents": "false"}
        if sync_token:
            params["syncToken"] = sync_token
        if page_token:
            params["pageToken"] = page_token
        return self.request("GET", f"/calendar/v3/calendars/{quote(calendar_id, safe='')}/events", params=params)

    def put_event(self, calendar_id, event_id, body, etag=None, create=False):
        path = f"/calendar/v3/calendars/{quote(calendar_id, safe='')}/events"
        if create:
            try:
                return self.request("POST", path, params={"sendUpdates": "none"}, body={**body, "id": event_id})
            except ProviderError as exc:
                if exc.status != 409:
                    raise
                # Crash after remote acceptance: read the deterministic event rather than create another.
                return self.request("GET", path + "/" + event_id)
        return self.request("PATCH", path + "/" + event_id, params={"sendUpdates": "none"}, body=body, headers={"If-Match": etag} if etag else {})

    def delete_event(self, calendar_id, event_id, etag=None):
        try:
            return self.request("DELETE", f"/calendar/v3/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}", params={"sendUpdates": "none"}, headers={"If-Match": etag} if etag else {})
        except ProviderError as exc:
            if exc.status not in {404, 410}:
                raise

    def send_mail(self, document, key, recipients):
        if not recipients:
            raise ValueError("Notification recipients are not configured")
        message = EmailMessage()
        message["To"] = ", ".join(recipients)
        message["Subject"] = document["title"].replace("\n", " ").replace("\r", " ")
        message["Message-ID"] = f"<{key}@academic-os.local>"
        message.set_content(document["body"])
        result = self.request("POST", "/gmail/v1/users/me/messages/send", body={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()})
        return result["id"]


class Feishu:
    def __init__(self, secret_store, config):
        self.secrets, self.config = secret_store, config
        self.http = httpx.Client(trust_env=False, timeout=httpx.Timeout(15, connect=5))
        self._access, self._expires = None, 0

    def token(self):
        if time.time() < self._expires - 60:
            return self._access
        credential = json.loads(self.secrets.get(self.config.get("secret_ref", "feishu")))
        response = self.http.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", json={"app_id": credential["app_id"], "app_secret": credential["app_secret"]})
        if response.status_code >= 400:
            raise ProviderError(response.status_code, "feishu_authorization_failed")
        payload = response.json()
        if payload.get("code"):
            raise ProviderError(403, "feishu_authorization_failed")
        self._access, self._expires = payload["tenant_access_token"], time.time() + payload.get("expire", 7200)
        return self._access

    def request(self, method, path, body=None, params=None):
        response = self.http.request(method, "https://open.feishu.cn/open-apis" + path, json=body, params=params, headers={"Authorization": "Bearer " + self.token()})
        if response.status_code >= 400:
            raise ProviderError(response.status_code, "feishu_request_failed")
        payload = response.json()
        if payload.get("code"):
            raise ProviderError(400, f"feishu_code_{payload['code']}")
        return payload.get("data", {})

    def mirror_event(self, calendar_id, body, event_id=None, unique_key=None):
        path = f"/calendar/v4/calendars/{quote(calendar_id, safe='')}/events"
        if event_id:
            return self.request("PATCH", path + "/" + quote(event_id, safe=""), body=body).get("event", {})
        return self.request("POST", path, body=body, params={"idempotency_key": unique_key} if unique_key else None).get("event", {})

    def delete_event(self, calendar_id, event_id):
        try:
            return self.request("DELETE", f"/calendar/v4/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}")
        except ProviderError as exc:
            if exc.status not in {404, 410}:
                raise

    def send_message(self, document, key):
        chat_id = self.config.get("chat_id")
        if not chat_id:
            raise ValueError("Feishu chat_id is not configured")
        result = self.request("POST", "/im/v1/messages", params={"receive_id_type": "chat_id"}, body={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": document["title"] + "\n\n" + document["body"]}, ensure_ascii=False), "uuid": key[-32:]})
        return result["message_id"]
