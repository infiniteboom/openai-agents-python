import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector


class AsyncHZConnector:
    """异步 HZ 连接器 - 使用 aiohttp 实现并发友好的 HTTP 调用。"""

    def __init__(
        self,
        username: str,
        password: str,
        address: str,
        max_connections: int = 50,
        timeout_seconds: int = 30,
    ) -> None:
        # 认证与基础地址
        self.username = username
        self.password = password
        self.address = (address or "").strip()

        if not self.address:
            raise ValueError(
                "HZ 基础地址未配置，请在 connections 配置中设置 "
                "hz.address（需包含 http/https 协议）"
            )
        if self.address.endswith("/"):
            self.address = self.address.rstrip("/")
        if not (self.address.startswith("http://") or self.address.startswith("https://")):
            raise ValueError(
                f"HZ 地址缺少协议前缀: '{self.address}'，请以 http:// 或 https:// 开头"
            )

        # 连接池与超时
        self.max_connections = max_connections
        self.timeout = ClientTimeout(total=timeout_seconds)

        # Session 与 Token 管理
        self._session: Optional[ClientSession] = None
        self._token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        # _token_lock 会在 _ensure_session 中按需绑定到当前事件循环
        self._token_lock = asyncio.Lock()

        self.logger = logging.getLogger(__name__)

    # ========= Session 生命周期 =========
    async def _ensure_session(self) -> None:
        """
        确保 aiohttp Session 已创建并可用。

        注意：
        - AsyncHZConnector 可能在不同的事件循环中被复用（例如同步壳通过 asyncio.run 调用），
          因此当发现 _session 为 None 或已关闭时，需要在当前事件循环上重新创建：
          * aiohttp.ClientSession
          * 用于 Token 刷新的 asyncio.Lock
        - 这样可以避免跨事件循环复用旧的 Session/Lock 引发
          "Event loop is closed" 或 "attached to a different loop" 等错误。
        """
        if self._session is None or self._session.closed:
            # 重新绑定 Token lock 到当前事件循环
            self._token_lock = asyncio.Lock()

            connector = TCPConnector(
                limit=self.max_connections,
                limit_per_host=self.max_connections,
                ttl_dns_cache=300,
            )
            self._session = ClientSession(
                connector=connector,
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            self.logger.info(
                "Async HZ HTTP session created",
                extra={"max_connections": self.max_connections},
            )

    async def close(self) -> None:
        """关闭底层 HTTP Session。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("Async HZ HTTP session closed")

    async def __aenter__(self) -> "AsyncHZConnector":
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # ========= Token 管理 =========
    async def _login_async(self) -> None:
        """使用密码模式登录，获取 access_token / refresh_token。"""
        await self._ensure_session()

        login_url = f"{self.address}/auth/oauth/token"
        headers = {
            "Authorization": "Basic YXBpOkthbWluYW4=",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        form_data = {
            "username": self.username,
            "password": self.password,
            "grant_type": "password",
            "user_type": "user",
        }

        assert self._session is not None  # 仅为类型检查

        self.logger.info("AsyncHZConnector logging in...")
        async with self._session.post(login_url, headers=headers, data=form_data) as resp:
            resp.raise_for_status()
            result = await resp.json()

        if result.get("code") != 0:
            raise RuntimeError(f"HZ 登录失败: {result.get('message', '未知错误')}")

        data = result.get("data") or {}
        token = data.get("access_token")
        if not token:
            raise RuntimeError("HZ 登录返回中缺少 access_token")

        self._token = token
        self._refresh_token = data.get("refresh_token")

        expires_in = data.get("expires_in") or data.get("expiresIn")
        if isinstance(expires_in, int) and expires_in > 0:
            # 预留 120s 安全窗口
            self._token_expires_at = datetime.now() + timedelta(seconds=max(0, expires_in - 120))
        else:
            # 默认 55 分钟
            self._token_expires_at = datetime.now() + timedelta(minutes=55)

        self.logger.info(
            "AsyncHZConnector login success",
            extra={
                "expires_at": self._token_expires_at.isoformat() if self._token_expires_at else None
            },
        )

    async def _refresh_token_async(self) -> None:
        """使用 refresh_token 刷新访问令牌，如失败则回退为重新登录。"""
        await self._ensure_session()
        if not self._refresh_token:
            # 无 refresh_token，直接重新登录
            await self._login_async()
            return

        url = f"{self.address}/auth/oauth/token"
        headers = {
            "Authorization": "Basic YXBpOkthbWluYW4=",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        form_data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }

        assert self._session is not None

        self.logger.info("AsyncHZConnector refreshing token with refresh_token...")
        async with self._session.post(url, headers=headers, data=form_data) as resp:
            resp.raise_for_status()
            result = await resp.json()

        if result.get("code") != 0:
            raise RuntimeError(result.get("message", "刷新失败"))

        data = result.get("data") or {}
        token = data.get("access_token")
        if not token:
            raise RuntimeError("刷新未返回 access_token")

        self._token = token
        self._refresh_token = data.get("refresh_token") or self._refresh_token
        expires_in = data.get("expires_in") or data.get("expiresIn")
        if isinstance(expires_in, int) and expires_in > 0:
            self._token_expires_at = datetime.now() + timedelta(seconds=max(0, expires_in - 120))
        else:
            self._token_expires_at = datetime.now() + timedelta(minutes=55)

        self.logger.info("AsyncHZConnector token refresh success")

    async def _ensure_token(self) -> str:
        """确保当前 token 有效，如过期则刷新或重新登录。"""
        async with self._token_lock:
            now = datetime.now()
            if self._token and self._token_expires_at and now < self._token_expires_at:
                return self._token

            # 过期或不存在，尝试刷新或重新登录
            try:
                if self._refresh_token:
                    await self._refresh_token_async()
                else:
                    await self._login_async()
            except Exception as e:  # noqa: BLE001
                # 刷新失败则强制重新登录一次
                self.logger.warning(f"AsyncHZConnector refresh failed, relogin: {e}")
                await self._login_async()

            if not self._token:
                raise RuntimeError("AsyncHZConnector 未能获取到有效的 access_token")
            return self._token

    # ========= 通用 HTTP 调用 =========
    async def _make_request_async(
        self,
        endpoint: str,
        method: str = "POST",
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
        retry: int = 1,
    ) -> dict[str, Any]:
        """带 Token 管理与 401 重试的一般性 HTTP 调用封装。"""
        await self._ensure_session()
        assert self._session is not None

        url = f"{self.address}/{endpoint.lstrip('/')}"
        method_upper = method.upper()

        for attempt in range(retry + 1):
            token = await self._ensure_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            timeout_ctx = ClientTimeout(total=timeout) if timeout is not None else self.timeout

            try:
                async with self._session.request(
                    method_upper,
                    url,
                    headers=headers,
                    params=params,
                    json=data if data is not None else None,
                    timeout=timeout_ctx,
                ) as resp:
                    # 401 或业务码 401 → 强制刷新并重试
                    if resp.status == 401:
                        self.logger.warning(
                            "AsyncHZConnector unauthorized (401), refreshing token",
                            extra={"endpoint": endpoint, "method": method_upper},
                        )
                        # 下次循环中重新获取 token
                        self._token_expires_at = datetime.now() - timedelta(seconds=1)
                        if attempt < retry:
                            continue

                    resp.raise_for_status()
                    raw_result = await resp.json()
                    if not isinstance(raw_result, dict):
                        raise RuntimeError("HZ 接口返回格式异常：期望 JSON object")
                    result: dict[str, Any] = raw_result

                    # 业务码级别的 401
                    code = (result or {}).get("code")
                    if code == 401 and attempt < retry:
                        self.logger.warning(
                            "AsyncHZConnector business 401 detected, refreshing token",
                            extra={"endpoint": endpoint, "method": method_upper},
                        )
                        self._token_expires_at = datetime.now() - timedelta(seconds=1)
                        continue

                    return result

            except ClientError as e:
                if attempt < retry:
                    self.logger.warning(
                        "AsyncHZConnector request failed, will retry",
                        extra={"endpoint": endpoint, "method": method_upper, "error": str(e)},
                    )
                    await asyncio.sleep(1.0)
                    continue
                self.logger.error(
                    "AsyncHZConnector request failed",
                    extra={"endpoint": endpoint, "method": method_upper, "error": str(e)},
                )
                raise

        raise RuntimeError(f"AsyncHZConnector request failed after {retry + 1} attempts")

    async def get_listed_trade_info_by_date_async(
        self,
        date: str,
        version: str = "sss",
        investor_id: str = "场外01",
    ) -> list[dict[str, Any]]:
        """异步获取场内交易信息（过滤指定 investor_id）。"""
        endpoint = "/exchange-business/exchange/getExchangeInfosByDate"
        data = {"date": date}
        params = {"version": version}
        resp = await self._make_request_async(
            endpoint=endpoint,
            method="GET",
            data=data,
            params=params,
        )
        raw_list = (resp.get("data") or {}).get("exchangePositionList") or []
        return [p for p in raw_list if p.get("investorId") == investor_id]

    async def get_contract_list_ongoing_by_wei_async(self) -> list[dict[str, Any]]:
        """异步获取当前在途合约列表。"""
        endpoint = "/otc-business/hzotcContract/listOngoingByWei"
        resp = await self._make_request_async(endpoint=endpoint)
        return resp.get("data") or []

    async def get_contract_list_ongoing_async(self) -> list[dict[str, Any]]:
        """异步获取当前在途合约列表。"""
        endpoint = "/otc-business/hzotcContract/listOngoing"
        resp = await self._make_request_async(endpoint=endpoint)
        return resp.get("data") or []

    async def get_variety_code_variety_name_map_async(self) -> dict[str, str]:
        """异步获取在途合约的 `{variatyCode: varietyName}` 映射。"""
        contract_list = await self.get_contract_list_ongoing_async()
        out: dict[str, str] = {}

        for contract in contract_list:
            contract_code = contract.get("varietyCode")
            if not isinstance(contract_code, str) or not contract_code:
                continue

            variety_name: Any = contract.get("varietyName")
            if not isinstance(variety_name, str) or not variety_name:
                variety_info = contract.get("varietyInfo")
                if isinstance(variety_info, dict):
                    nested_variety_name = variety_info.get("varietyName")
                    if isinstance(nested_variety_name, str) and nested_variety_name:
                        variety_name = nested_variety_name

            if isinstance(variety_name, str) and variety_name:
                out[contract_code] = variety_name

        return out

    async def get_history_price_async(
        self,
        contract_list: list[str],
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """异步获取历史价格原始列表。"""
        endpoint = "/otc-business/hzotcContractDayPrice/weiQueryContractDayPriceList"
        data = {
            "contractCodeList": contract_list,
            "endDate": end_date,
            "startDate": start_date,
        }
        resp = await self._make_request_async(endpoint=endpoint, data=data)
        return resp.get("data") or []

    async def get_no_trade_date_list_async(
        self,
        start_date: str,
        end_date: str,
    ) -> list[str]:
        """异步获取非交易日列表，返回 YYYY-MM-DD 或带时间的字符串列表。"""
        endpoint = "/otc-business/hzotcTradeCalendar/listNoTradeDate"
        data = {
            "endTime": end_date,
            "startTime": start_date,
        }
        resp = await self._make_request_async(endpoint=endpoint, data=data)
        # Most HZ endpoints wrap payload under "data" with a top-level "code".
        payload: Any = resp
        if isinstance(resp, dict) and "data" in resp:
            payload = resp.get("data")

        if isinstance(payload, dict):
            date_list = (
                payload.get("dateList") or payload.get("date_list") or payload.get("dates") or []
            )
            if isinstance(date_list, list):
                return [str(d) for d in date_list]

        if isinstance(payload, list):
            return [str(d) for d in payload]

        return []
