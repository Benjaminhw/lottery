from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


AUTHORIZE_ENDPOINT = "https://open.weixin.qq.com/connect/oauth2/authorize"
TOKEN_ENDPOINT = "https://api.weixin.qq.com/sns/oauth2/access_token"
USERINFO_ENDPOINT = "https://api.weixin.qq.com/sns/userinfo"


class WechatOAuthError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WechatProfile:
    openid: str
    nickname: str
    avatar_url: str | None


def authorization_url(app_id: str, callback_url: str, state: str) -> str:
    query = urlencode(
        {
            "appid": app_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "scope": "snsapi_userinfo",
            "state": state,
        }
    )
    return f"{AUTHORIZE_ENDPOINT}?{query}#wechat_redirect"


async def fetch_wechat_profile(
    app_id: str, app_secret: str, code: str
) -> WechatProfile:
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token_response = await client.get(
            TOKEN_ENDPOINT,
            params={
                "appid": app_id,
                "secret": app_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        if "errcode" in token_data or not token_data.get("access_token"):
            raise WechatOAuthError(token_data.get("errmsg", "微信授权失败"))

        profile_response = await client.get(
            USERINFO_ENDPOINT,
            params={
                "access_token": token_data["access_token"],
                "openid": token_data["openid"],
                "lang": "zh_CN",
            },
        )
        profile_response.raise_for_status()
        profile_data = profile_response.json()
        if "errcode" in profile_data or not profile_data.get("openid"):
            raise WechatOAuthError(profile_data.get("errmsg", "读取微信资料失败"))

    avatar_url = profile_data.get("headimgurl") or None
    if avatar_url and avatar_url.startswith("http://"):
        avatar_url = f"https://{avatar_url.removeprefix('http://')}"
    return WechatProfile(
        openid=profile_data["openid"],
        nickname=profile_data.get("nickname") or "微信用户",
        avatar_url=avatar_url,
    )
