# -*- coding: utf-8 -*-
"""Grok Web SSO -> xAI OAuth，本机代理回退实现。"""

import base64
import json
import time
from datetime import datetime, timezone

from curl_cffi import requests as curl_requests

XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_SCOPE = (
    "openid profile email offline_access grok-cli:access api:access "
    "conversations:read conversations:write"
)
XAI_CLI_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
XAI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _jwt_claims(token):
    try:
        segment = str(token or "").split(".")[1]
        segment += "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment))
    except Exception:
        return {}


def _request(session, method, url, **kwargs):
    response = session.request(
        method, url, timeout=45, allow_redirects=True, **kwargs
    )
    if response.status_code >= 400:
        raise RuntimeError(f"xAI OAuth HTTP {response.status_code}")
    return response


def convert_grok_sso_local(sso, proxy, account_email="", timeout=90):
    """通过本机代理完成 xAI Device Flow，返回 SUB2API credentials。"""
    sso = str(sso or "").strip()
    proxy = str(proxy or "").strip()
    if not sso:
        raise ValueError("缺少 grok sso")
    if not proxy:
        raise ValueError("缺少本机 Grok OAuth 代理")

    session = curl_requests.Session(impersonate="chrome131", http_version="v2")
    session.proxies = {"http": proxy, "https": proxy}
    session.headers.update({
        "User-Agent": XAI_USER_AGENT,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    session.cookies.set("sso", sso, domain=".x.ai", path="/")
    session.cookies.set("sso-rw", sso, domain=".x.ai", path="/")

    try:
        response = _request(session, "GET", "https://accounts.x.ai/")
        if "sign-in" in response.url or "sign-up" in response.url:
            raise RuntimeError("Grok Web SSO 已失效")

        response = _request(
            session,
            "POST",
            "https://auth.x.ai/oauth2/device/code",
            data={"client_id": XAI_CLIENT_ID, "scope": XAI_SCOPE},
        )
        device = response.json()
        device_code = str(device.get("device_code") or "")
        user_code = str(device.get("user_code") or "")
        verify_url = str(device.get("verification_uri_complete") or "")
        if not device_code or not user_code or not verify_url.startswith("https://"):
            raise RuntimeError("xAI Device Flow 返回不完整")

        _request(session, "GET", verify_url)
        response = _request(
            session,
            "POST",
            "https://auth.x.ai/oauth2/device/verify",
            data={"user_code": user_code},
        )
        if "consent" not in response.url:
            raise RuntimeError("xAI Device Flow 未进入授权确认页")

        response = _request(
            session,
            "POST",
            "https://auth.x.ai/oauth2/device/approve",
            data={
                "user_code": user_code,
                "action": "allow",
                "principal_type": "User",
                "principal_id": "",
            },
        )
        if "done" not in response.url:
            raise RuntimeError("xAI Device Flow 授权未完成")

        interval = max(1, int(device.get("interval") or 5))
        deadline = time.time() + max(15, int(timeout))
        token = None
        while time.time() < deadline:
            time.sleep(interval)
            response = session.post(
                "https://auth.x.ai/oauth2/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": XAI_CLIENT_ID,
                    "device_code": device_code,
                },
                timeout=45,
            )
            payload = response.json()
            if response.status_code < 300 and payload.get("access_token"):
                token = payload
                break
            error = payload.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise RuntimeError(f"xAI OAuth 换取 token 失败: {error or response.status_code}")
        if not token:
            raise RuntimeError("xAI Device Flow 等待 token 超时")
        if not token.get("refresh_token"):
            raise RuntimeError("xAI OAuth 未返回 refresh_token")

        claims = {}
        claims.update(_jwt_claims(token.get("access_token")))
        claims.update(_jwt_claims(token.get("id_token")))
        email = str(claims.get("email") or account_email or "").strip()
        expires_at = int(time.time()) + int(token.get("expires_in") or 21600)
        credentials = {
            "access_token": token["access_token"],
            "refresh_token": token["refresh_token"],
            "token_type": token.get("token_type") or "Bearer",
            "client_id": XAI_CLIENT_ID,
            "scope": token.get("scope") or XAI_SCOPE,
            "expires_at": datetime.fromtimestamp(
                expires_at, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "base_url": XAI_CLI_BASE_URL,
        }
        if email:
            credentials["email"] = email
        if token.get("id_token"):
            credentials["id_token"] = token["id_token"]
        for key in ("sub", "team_id"):
            if claims.get(key):
                credentials[key] = claims[key]
        return credentials, email
    finally:
        session.close()
