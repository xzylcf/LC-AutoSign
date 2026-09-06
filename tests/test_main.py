import unittest

import requests

import main


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self):
        if self.payload is None:
            raise ValueError("not json")
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("没有为本次请求配置 FakeResponse")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def secret_response(key_id):
    return FakeResponse(
        200,
        {"success": True, "code": 200, "data": {"keyId": key_id}},
    )


class JLCClientTests(unittest.TestCase):
    def test_web_headers_secretkey_and_sign_params_are_sent(self):
        session = FakeSession(
            [
                secret_response("key-1"),
                FakeResponse(200, {"success": True, "code": 200, "data": {}}),
            ]
        )
        client = main.JLCClient("token-1", session=session, retries=1)

        client.request_json(
            "GET",
            main.SIGN_IN_URL,
            params={"platformType": "WEB", "source": 4},
        )

        _, _, secret_call = session.calls[0]
        _, sign_url, sign_call = session.calls[1]
        self.assertEqual(main.SECRET_KEY_URL, session.calls[0][1])
        self.assertEqual(main.SIGN_IN_URL, sign_url)
        self.assertEqual("WEB", sign_call["headers"]["X-JLC-ClientType"])
        self.assertEqual("token-1", sign_call["headers"]["X-JLC-AccessToken"])
        self.assertEqual("key-1", sign_call["headers"]["secretkey"])
        self.assertEqual({"platformType": "WEB", "source": 4}, sign_call["params"])
        self.assertNotIn("secretkey", secret_call["headers"])

    def test_expired_secretkey_is_refreshed_once(self):
        session = FakeSession(
            [
                secret_response("key-1"),
                FakeResponse(200, {"success": False, "code": 29001}),
                secret_response("key-2"),
                FakeResponse(200, {"success": True, "code": 200, "data": {}}),
            ]
        )
        client = main.JLCClient("token-1", session=session, retries=1)

        result = client.request_json("GET", main.ASSETS_URL)

        self.assertTrue(result["success"])
        self.assertEqual("key-2", session.calls[-1][2]["headers"]["secretkey"])

    def test_401_raises_clear_auth_error(self):
        session = FakeSession(
            [
                secret_response("key-1"),
                FakeResponse(
                    401,
                    {"success": False, "code": 401, "message": "用户未登录或会话失效"},
                ),
            ]
        )
        client = main.JLCClient("expired", session=session, retries=1)

        with self.assertRaisesRegex(main.JLCAuthError, "用户未登录或会话失效"):
            client.request_json("GET", main.ASSETS_URL)

    def test_complete_sign_flow_uses_live_balance_difference(self):
        session = FakeSession(
            [
                secret_response("key-1"),
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"customerCode": "C12345", "integralVoucher": 20},
                    },
                ),
                FakeResponse(
                    200,
                    {"success": True, "code": 200, "data": {"haveSignIn": False}},
                ),
                FakeResponse(
                    200,
                    {"success": True, "code": 200, "data": {"status": 1, "gainNum": 1}},
                ),
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"customerCode": "C12345", "integralVoucher": 21},
                    },
                ),
            ]
        )

        result = main.sign_in("token-1", session=session)

        self.assertEqual("success", result.status)
        self.assertIn("获取1个金豆", result.notification)
        sign_call = next(call for call in session.calls if call[1] == main.SIGN_IN_URL)
        self.assertEqual(
            {"platformType": "WEB", "source": 4}, sign_call[2]["params"]
        )

    def test_auth_failure_becomes_failed_account_result(self):
        session = FakeSession(
            [
                secret_response("key-1"),
                FakeResponse(
                    401,
                    {"success": False, "code": 401, "message": "用户未登录或会话失效"},
                ),
            ]
        )

        result = main.sign_in("expired-token", session=session)

        self.assertTrue(result.failed)
        self.assertIn("更新 TOKEN_LIST", result.notification)

    def test_zero_gain_sign_response_claims_reward_and_rechecks_balance(self):
        session = FakeSession(
            [
                secret_response("key-1"),
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"customerCode": "C12345", "integralVoucher": 20},
                    },
                ),
                FakeResponse(
                    200,
                    {"success": True, "code": 200, "data": {"haveSignIn": False}},
                ),
                FakeResponse(
                    200,
                    {"success": True, "code": 200, "data": {"status": 1, "gainNum": 0}},
                ),
                FakeResponse(200, {"success": True, "code": 200, "data": {}}),
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"customerCode": "C12345", "integralVoucher": 28},
                    },
                ),
            ]
        )

        result = main.sign_in("token-1", session=session)

        self.assertEqual("success", result.status)
        self.assertIn("获取8个金豆", result.notification)
        self.assertIn(main.RECEIVE_VOUCHER_URL, [call[1] for call in session.calls])


class MainExitCodeTests(unittest.TestCase):
    def test_any_failed_account_returns_nonzero_and_sends_failure_notice(self):
        sent = []

        def fake_sign(token):
            if token == "bad":
                return main.AccountResult("failed", "鉴权失败", "鉴权失败")
            return main.AccountResult("already", "今日已签到")

        def fake_send(key, title, content):
            sent.append((key, title, content))
            return True

        exit_code = main.main(
            token_list="bad,ok",
            send_key_list="same,same",
            sign_func=fake_sign,
            send_func=fake_send,
            sleep_func=lambda _: None,
            randint_func=lambda _a, _b: 0,
        )

        self.assertEqual(1, exit_code)
        self.assertEqual(1, len(sent))
        self.assertIn("鉴权失败", sent[0][2])

    def test_mismatched_secret_counts_return_configuration_error(self):
        exit_code = main.main(token_list="a,b", send_key_list="one")
        self.assertEqual(2, exit_code)


if __name__ == "__main__":
    unittest.main()
