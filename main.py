# -*- coding: UTF-8 -*-

import os
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

import requests
from requests import Response, Session
from requests.exceptions import RequestException


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


BASE_URL = "https://m.jlc.com"
SECRET_KEY_URL = f"{BASE_URL}/api/integrated/secret/update"
ASSETS_URL = f"{BASE_URL}/api/appPlatform/center/assets/selectPersonalAssetsInfo"
SIGN_CONFIG_URL = f"{BASE_URL}/api/activity/sign/getCurrentUserSignInConfig"
SIGN_IN_URL = f"{BASE_URL}/api/activity/sign/signIn"
RECEIVE_VOUCHER_URL = f"{BASE_URL}/api/activity/sign/receiveVoucher"

REQUEST_TIMEOUT = (10, 20)
SECRET_KEY_EXPIRED_CODES = {29001, 29003}


class JLCError(Exception):
    """嘉立创接口调用失败。"""


class JLCAuthError(JLCError):
    """AccessToken 已失效，或服务端拒绝了当前鉴权信息。"""


@dataclass
class AccountResult:
    status: str
    message: str
    notification: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.status == "failed"


def mask_value(value: str) -> str:
    """隐藏凭证或客编，只保留首尾两个字符。"""
    if len(value) >= 4:
        return value[:2] + "****" + value[-2:]
    return "****"


def response_message(response: Response, payload: Optional[dict] = None) -> str:
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    text = (getattr(response, "text", "") or "").strip()
    return text[:200] if text else "服务端未返回错误详情"


class JLCClient:
    """按嘉立创当前 Web 端协议调用签到接口。"""

    def __init__(
        self,
        access_token: str,
        session: Optional[Session] = None,
        retries: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.retries = max(1, retries)
        self.sleeper = sleeper
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE_URL}/mapp/pages-common/integral/index",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-JLC-AccessToken": access_token,
            "X-JLC-ClientType": "WEB",
        }

    def _send(self, method: str, url: str, **kwargs) -> Response:
        last_error: Optional[RequestException] = None
        for attempt in range(1, self.retries + 1):
            try:
                return self.session.request(
                    method,
                    url,
                    headers=dict(self.headers),
                    timeout=REQUEST_TIMEOUT,
                    **kwargs,
                )
            except RequestException as exc:
                last_error = exc
                if attempt < self.retries:
                    self.sleeper(2 * attempt)
        raise JLCError(f"网络请求连续失败 {self.retries} 次：{last_error}")

    @staticmethod
    def _parse_json(response: Response) -> dict:
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise JLCError(
                f"接口返回了非 JSON 内容（HTTP {response.status_code}）"
            ) from exc
        if not isinstance(payload, dict):
            raise JLCError("接口返回的数据格式不是 JSON 对象")
        return payload

    def _check_response(self, response: Response) -> dict:
        payload: Optional[dict] = None
        try:
            payload = self._parse_json(response)
        except JLCError:
            if response.status_code == 401:
                raise JLCAuthError(
                    "HTTP 401：AccessToken 已失效，或鉴权协议已变化"
                )
            raise

        if response.status_code == 401 or payload.get("code") == 401:
            raise JLCAuthError(f"HTTP 401：{response_message(response, payload)}")

        try:
            response.raise_for_status()
        except RequestException as exc:
            raise JLCError(
                f"HTTP {response.status_code}：{response_message(response, payload)}"
            ) from exc
        return payload

    def refresh_secret_key(self) -> str:
        response = self._send("POST", SECRET_KEY_URL)
        payload = self._check_response(response)
        key_id = (payload.get("data") or {}).get("keyId")
        if payload.get("code") != 200 or not key_id:
            raise JLCError(
                f"获取动态 secretkey 失败：{response_message(response, payload)}"
            )
        self.headers["secretkey"] = str(key_id)
        return str(key_id)

    def request_json(self, method: str, url: str, **kwargs) -> dict:
        if "secretkey" not in self.headers:
            self.refresh_secret_key()

        response = self._send(method, url, **kwargs)
        payload = self._check_response(response)

        if payload.get("code") in SECRET_KEY_EXPIRED_CODES:
            self.refresh_secret_key()
            response = self._send(method, url, **kwargs)
            payload = self._check_response(response)

        return payload


def require_success(payload: dict, operation: str) -> dict:
    if not payload.get("success"):
        message = payload.get("message") or "未知错误"
        raise JLCError(f"{operation}失败：{message}")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def fetch_assets(client: JLCClient) -> tuple[str, int]:
    data = require_success(client.request_json("GET", ASSETS_URL), "查询金豆")
    customer_code = str(data.get("customerCode") or "未知账号")
    try:
        integral_voucher = int(data.get("integralVoucher") or 0)
    except (TypeError, ValueError) as exc:
        raise JLCError("查询金豆失败：integralVoucher 格式异常") from exc
    return customer_code, integral_voucher


def sign_in(access_token: str, session: Optional[Session] = None) -> AccountResult:
    credential_label = mask_value(access_token)
    client = JLCClient(access_token, session=session)

    try:
        customer_code, before_balance = fetch_assets(client)
        account_label = mask_value(customer_code)

        config = require_success(
            client.request_json("GET", SIGN_CONFIG_URL), "查询签到状态"
        )
        if config.get("haveSignIn"):
            message = f"ℹ️ [账号{account_label}] 今日已签到"
            print(message)
            return AccountResult("already", message)

        sign_data = require_success(
            client.request_json(
                "GET",
                SIGN_IN_URL,
                params={"platformType": "WEB", "source": 4},
            ),
            "签到",
        )
        status = sign_data.get("status")
        gain_num = sign_data.get("gainNum")

        if status == 2:
            message = f"ℹ️ [账号{account_label}] 今日已签到"
            print(message)
            return AccountResult("already", message)
        if status != 1:
            raise JLCError(f"签到失败：接口返回状态 {status!r}")

        if gain_num in (None, 0):
            require_success(
                client.request_json("GET", RECEIVE_VOUCHER_URL), "领取签到奖励"
            )

        _, after_balance = fetch_assets(client)
        actual_gain = after_balance - before_balance
        if actual_gain > 0:
            notification = (
                f"✅ 账号({account_label})：获取{actual_gain}个金豆，"
                f"当前总数：{after_balance}"
            )
        else:
            notification = (
                f"✅ 账号({account_label})：签到成功，当前金豆总数：{after_balance}"
            )
        print(f"✅ [账号{account_label}] 今日签到成功")
        return AccountResult("success", "签到成功", notification)

    except JLCAuthError as exc:
        message = (
            f"❌ [凭证{credential_label}] 鉴权失败：{exc}。"
            "请重新登录嘉立创并更新 TOKEN_LIST"
        )
    except JLCError as exc:
        message = f"❌ [凭证{credential_label}] 请求失败：{exc}"
    except Exception as exc:  # 防止单个账号中断其他账号，但仍让任务最终失败
        message = f"❌ [凭证{credential_label}] 未知错误：{exc}"

    print(message)
    return AccountResult("failed", message, message)


def send_msg_by_server(send_key: str, title: str, content: str) -> bool:
    push_url = f"https://sctapi.ftqq.com/{send_key}.send"
    try:
        response = requests.post(
            push_url,
            data={"title": title, "desp": content},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("code") == 0
    except (RequestException, TypeError, ValueError) as exc:
        print(f"❌ Server酱通知失败：{exc}")
        return False


def main(
    token_list: Optional[str] = None,
    send_key_list: Optional[str] = None,
    sign_func: Callable[[str], AccountResult] = sign_in,
    send_func: Callable[[str, str, str], bool] = send_msg_by_server,
    sleep_func: Callable[[float], None] = time.sleep,
    randint_func: Callable[[int, int], int] = random.randint,
) -> int:
    tokens_value = os.getenv("TOKEN_LIST", "") if token_list is None else token_list
    keys_value = (
        os.getenv("SEND_KEY_LIST", "") if send_key_list is None else send_key_list
    )
    tokens = [token.strip() for token in tokens_value.split(",") if token.strip()]
    send_keys = [key.strip() for key in keys_value.split(",") if key.strip()]

    if not tokens:
        print("❌ 请设置 TOKEN_LIST")
        return 2
    if not send_keys:
        print("❌ 请设置 SEND_KEY_LIST")
        return 2
    if len(tokens) != len(send_keys):
        print(
            "❌ TOKEN_LIST 与 SEND_KEY_LIST 数量不一致："
            f"{len(tokens)} 个 Token，{len(send_keys)} 个 SendKey"
        )
        return 2

    print(f"🔧 共发现 {len(tokens)} 个账号需要签到")
    task_groups = defaultdict(list)
    for access_token, send_key in zip(tokens, send_keys):
        task_groups[send_key].append(access_token)
    print(f"📊 共分为 {len(task_groups)} 个通知组")

    all_results = []
    notification_failed = False

    for send_key, group_tokens in task_groups.items():
        print(f"\n🚀 开始处理 SendKey: {send_key[:5]}... 的 {len(group_tokens)} 个账号")
        group_results = []

        for index, token in enumerate(group_tokens, 1):
            print(f"📝 处理第 {index}/{len(group_tokens)} 个账号...")
            result = sign_func(token)
            group_results.append(result)
            all_results.append(result)

            if index < len(group_tokens):
                wait_time = randint_func(5, 15)
                print(f"⏳ 等待 {wait_time} 秒后处理下一个账号...")
                sleep_func(wait_time)

        notices = [item.notification for item in group_results if item.notification]
        if notices:
            title = (
                "嘉立创签到异常"
                if all(item.failed for item in group_results)
                else "嘉立创签到汇总"
            )
            if send_func(send_key, title, "\n\n".join(notices)):
                print(f"✅ 通知发送成功：SendKey {send_key[:5]}...")
            else:
                notification_failed = True
        else:
            print(f"⏭️ SendKey: {send_key[:5]}... 无新增金豆或异常，跳过通知")

    failure_count = sum(result.failed for result in all_results)
    if failure_count:
        print(f"❌ 共 {failure_count}/{len(all_results)} 个账号处理失败")
        return 1
    if notification_failed:
        print("❌ 签到完成，但至少一组通知发送失败")
        return 1

    print("✅ 所有账号处理完成")
    return 0


if __name__ == "__main__":
    print("🏁 嘉立创自动签到任务开始")
    exit_code = main()
    print("🏁 任务执行完毕")
    raise SystemExit(exit_code)
