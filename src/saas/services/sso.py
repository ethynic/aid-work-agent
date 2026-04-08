"""
IM 平台 SSO 登录服务

支持企业微信/钉钉/飞书 OAuth 登录，获取用户手机号后匹配 tenant_admins。

当前为骨架实现，完整 OAuth 集成需要在租户配置 IM 渠道凭证后生效。
"""

from typing import Optional, Dict, Any

from loguru import logger


class SSOProvider:
    """SSO 服务提供者基类"""

    provider_name: str = ""

    async def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """生成 OAuth 授权 URL"""
        raise NotImplementedError

    async def get_user_phone(self, code: str, config: dict) -> Optional[str]:
        """通过 OAuth code 获取用户手机号"""
        raise NotImplementedError


class WeComSSO(SSOProvider):
    """企业微信 SSO"""

    provider_name = "wecom"

    async def get_auth_url(self, redirect_uri: str, state: str) -> str:
        corp_id = ""  # 从租户渠道配置获取
        return (
            f"https://open.weixin.qq.com/connect/oauth2/authorize"
            f"?appid={corp_id}&redirect_uri={redirect_uri}"
            f"&response_type=code&scope=snsapi_base&state={state}#wechat_redirect"
        )

    async def get_user_phone(self, code: str, config: dict) -> Optional[str]:
        """
        通过企业微信 OAuth code 获取用户手机号

        流程：code → access_token → user_info (userid) → getUser (phone)
        """
        try:
            import httpx

            corp_id = config.get("corp_id", "")
            secret = config.get("secret", "")

            # 1. 获取 access_token
            async with httpx.AsyncClient() as client:
                token_resp = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={"corpid": corp_id, "corpsecret": secret},
                )
                token_data = token_resp.json()
                if token_data.get("errcode", 0) != 0:
                    logger.error(f"WeCom gettoken failed: {token_data}")
                    return None
                access_token = token_data["access_token"]

                # 2. 通过 code 获取 userid
                user_resp = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
                    params={"access_token": access_token, "code": code},
                )
                user_data = user_resp.json()
                userid = user_data.get("userid")
                if not userid:
                    logger.error(f"WeCom getuserid failed: {user_data}")
                    return None

                # 3. 通过 userid 获取用户详情（含手机号）
                detail_resp = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/user/get",
                    params={"access_token": access_token, "userid": userid},
                )
                detail_data = detail_resp.json()
                return detail_data.get("mobile")

        except Exception as e:
            logger.error(f"WeCom SSO error: {e}")
            return None


class DingtalkSSO(SSOProvider):
    """钉钉 SSO"""

    provider_name = "dingtalk"

    async def get_auth_url(self, redirect_uri: str, state: str) -> str:
        app_key = ""  # 从租户渠道配置获取
        return (
            f"https://login.dingtalk.com/oauth2/auth"
            f"?client_id={app_key}&redirect_uri={redirect_uri}"
            f"&response_type=code&scope=openid&state={state}&prompt=consent"
        )

    async def get_user_phone(self, code: str, config: dict) -> Optional[str]:
        """通过钉钉 OAuth code 获取用户手机号"""
        try:
            import httpx

            app_key = config.get("app_key", "")
            app_secret = config.get("app_secret", "")

            async with httpx.AsyncClient() as client:
                # 1. 获取 userAccessToken
                token_resp = await client.post(
                    "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
                    json={
                        "clientId": app_key,
                        "clientSecret": app_secret,
                        "code": code,
                        "grantType": "authorization_code",
                    },
                )
                token_data = token_resp.json()
                access_token = token_data.get("accessToken")
                if not access_token:
                    logger.error(f"Dingtalk getAccessToken failed: {token_data}")
                    return None

                # 2. 获取用户信息
                user_resp = await client.get(
                    "https://api.dingtalk.com/v1.0/contact/users/me",
                    headers={"x-acs-dingtalk-access-token": access_token},
                )
                user_data = user_resp.json()
                # 钉钉可能返回 mobile 字段
                return user_data.get("mobile")

        except Exception as e:
            logger.error(f"Dingtalk SSO error: {e}")
            return None


class FeishuSSO(SSOProvider):
    """飞书 SSO"""

    provider_name = "feishu"

    async def get_auth_url(self, redirect_uri: str, state: str) -> str:
        app_id = ""  # 从租户渠道配置获取
        return (
            f"https://open.feishu.cn/open-apis/authen/v1/authorize"
            f"?app_id={app_id}&redirect_uri={redirect_uri}&state={state}"
        )

    async def get_user_phone(self, code: str, config: dict) -> Optional[str]:
        """通过飞书 OAuth code 获取用户手机号"""
        try:
            import httpx

            app_id = config.get("app_id", "")
            app_secret = config.get("app_secret", "")

            async with httpx.AsyncClient() as client:
                # 1. 获取 app_access_token
                token_resp = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                    json={"app_id": app_id, "app_secret": app_secret},
                )
                token_data = token_resp.json()
                app_access_token = token_data.get("app_access_token")
                if not app_access_token:
                    logger.error(f"Feishu getAppToken failed: {token_data}")
                    return None

                # 2. 通过 code 获取 user_access_token
                user_token_resp = await client.post(
                    "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
                    headers={"Authorization": f"Bearer {app_access_token}"},
                    json={"grant_type": "authorization_code", "code": code},
                )
                user_token_data = user_token_resp.json().get("data", {})
                user_access_token = user_token_data.get("access_token")
                if not user_access_token:
                    logger.error(f"Feishu getUserToken failed: {user_token_data}")
                    return None

                # 3. 获取用户信息
                user_resp = await client.get(
                    "https://open.feishu.cn/open-apis/authen/v1/user_info",
                    headers={"Authorization": f"Bearer {user_access_token}"},
                )
                user_data = user_resp.json().get("data", {})
                # 飞书需要额外调用获取手机号
                mobile = user_data.get("mobile")
                return mobile

        except Exception as e:
            logger.error(f"Feishu SSO error: {e}")
            return None


# SSO Provider 注册表
SSO_PROVIDERS: Dict[str, SSOProvider] = {
    "wecom": WeComSSO(),
    "dingtalk": DingtalkSSO(),
    "feishu": FeishuSSO(),
}


def get_sso_provider(provider: str) -> Optional[SSOProvider]:
    """获取 SSO Provider 实例"""
    return SSO_PROVIDERS.get(provider)
