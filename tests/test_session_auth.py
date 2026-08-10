"""验证公网状态页会话认证的签名、Cookie 和 HTTP 行为。"""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import session_auth


class SessionAuthUnitTests(unittest.TestCase):
    """验证不依赖网络的会话认证核心函数。"""

    def test_signed_session_rejects_tampering_expiry_and_password_change(self) -> None:
        """验证签名、过期时间和凭据版本都会参与会话校验。

        Args:
            无。

        Returns:
            无返回值。
        """
        credential = session_auth.Credential("monitor", "$6$test", "version-a")
        secret = b"s" * 48
        token = session_auth.create_session_token(
            credential.username,
            credential.version,
            secret,
            120,
            now=1000,
        )
        self.assertTrue(
            session_auth.verify_session_token(token, credential, secret, now=1050)
        )
        self.assertFalse(
            session_auth.verify_session_token(f"{token}x", credential, secret, now=1050)
        )
        self.assertFalse(
            session_auth.verify_session_token(token, credential, secret, now=1121)
        )
        changed = session_auth.Credential("monitor", "$6$changed", "version-b")
        self.assertFalse(
            session_auth.verify_session_token(token, changed, secret, now=1050)
        )

    def test_origin_and_rate_limit_are_strict(self) -> None:
        """验证写操作同源检查和连续失败锁定规则。

        Args:
            无。

        Returns:
            无返回值。
        """
        self.assertTrue(
            session_auth.origin_matches_host(
                "http://203.0.113.10:28456", "203.0.113.10:28456"
            )
        )
        self.assertFalse(
            session_auth.origin_matches_host(
                "https://attacker.example", "203.0.113.10:28456"
            )
        )
        limiter = session_auth.LoginRateLimiter()
        for attempt in range(session_auth.MAX_FAILURES - 1):
            self.assertEqual(limiter.record_failure("192.0.2.1", now=attempt), 0)
        self.assertEqual(
            limiter.record_failure(
                "192.0.2.1", now=session_auth.MAX_FAILURES - 1
            ),
            session_auth.LOCKOUT_SECONDS,
        )
        self.assertGreater(limiter.retry_after("192.0.2.1", now=5), 0)
        limiter.clear("192.0.2.1")
        self.assertEqual(limiter.retry_after("192.0.2.1", now=5), 0)


class SessionAuthHttpTests(unittest.TestCase):
    """通过真实回环 HTTP 请求验证完整登录流程。"""

    def setUp(self) -> None:
        """创建临时强哈希账号、密钥和认证服务器。

        Args:
            无。

        Returns:
            无返回值。
        """
        self.directory = tempfile.TemporaryDirectory()
        self.htpasswd_path = Path(self.directory.name) / "htpasswd"
        password_hash = "$6$test-salt$test-sha512-crypt-hash"
        self.htpasswd_path.write_text(
            f"monitor:{password_hash}\n", encoding="utf-8"
        )
        self.password_patch = mock.patch.object(
            session_auth,
            "verify_password",
            side_effect=lambda username, password, credential: (
                username == credential.username and password == "correct-password"
            ),
        )
        self.password_patch.start()
        self.secret = b"integration-test-secret-material-48-bytes-minimum!!"
        settings = session_auth.AuthSettings(
            host="127.0.0.1",
            port=0,
            htpasswd_path=str(self.htpasswd_path),
            secret=self.secret,
            force_secure_cookie=False,
        )
        self.server = session_auth.create_server(settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = int(self.server.server_address[1])

    def tearDown(self) -> None:
        """停止认证服务器并清理临时文件。

        Args:
            无。

        Returns:
            无返回值。
        """
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.password_patch.stop()
        self.directory.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        cookie: str = "",
        origin: str = "http://status.test",
    ) -> tuple[int, dict[str, Any] | None, list[tuple[str, str]]]:
        """向临时认证服务发送一次带可信转发头的请求。

        Args:
            method: HTTP 方法。
            path: 认证服务路径。
            body: 可选的 JSON 请求正文。
            cookie: 可选的 Cookie 请求头。
            origin: 写操作使用的 Origin 请求头。

        Returns:
            状态码、解析后的 JSON 和完整响应头列表。
        """
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {
            "X-Original-Host": "status.test",
            "X-Forwarded-Proto": "http",
            "X-Real-IP": "192.0.2.8",
        }
        payload: str | None = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if method == "POST":
            headers["Origin"] = origin
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = response.getheaders()
        connection.close()
        document = json.loads(response_body) if response_body else None
        return response.status, document, response_headers

    def test_login_verify_session_logout_and_credential_rotation(self) -> None:
        """验证登录、会话查询、退出及改密失效的完整链路。

        Args:
            无。

        Returns:
            无返回值。
        """
        status, _, _ = self.request("GET", "/verify")
        self.assertEqual(status, 401)

        status, document, _ = self.request(
            "POST",
            "/login",
            {"username": "monitor", "password": "wrong", "remember": False},
        )
        self.assertEqual(status, 401)
        self.assertEqual(document["error"], "invalid_credentials")

        status, document, headers = self.request(
            "POST",
            "/login",
            {
                "username": "monitor",
                "password": "correct-password",
                "remember": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(document["authenticated"])
        cookie_value = next(
            value for name, value in headers if name.lower() == "set-cookie"
        )
        self.assertIn("HttpOnly", cookie_value)
        self.assertIn("SameSite=Strict", cookie_value)
        self.assertIn("Max-Age=", cookie_value)
        cookie = cookie_value.split(";", 1)[0]

        status, _, _ = self.request("GET", "/verify", cookie=cookie)
        self.assertEqual(status, 204)
        status, document, _ = self.request("GET", "/session", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(document["authenticated"])

        replacement_hash = "$6$replacement$changed-sha512-crypt-hash"
        self.htpasswd_path.write_text(
            f"monitor:{replacement_hash}\n", encoding="utf-8"
        )
        status, _, _ = self.request("GET", "/verify", cookie=cookie)
        self.assertEqual(status, 401)

        status, _, logout_headers = self.request("POST", "/logout", cookie=cookie)
        self.assertEqual(status, 204)
        expired = [
            value
            for name, value in logout_headers
            if name.lower() == "set-cookie"
        ]
        self.assertEqual(len(expired), 2)
        self.assertTrue(all("Max-Age=0" in value for value in expired))

    def test_login_rejects_cross_origin_request(self) -> None:
        """验证跨站页面不能提交登录请求。

        Args:
            无。

        Returns:
            无返回值。
        """
        status, document, _ = self.request(
            "POST",
            "/login",
            {
                "username": "monitor",
                "password": "correct-password",
                "remember": False,
            },
            origin="https://attacker.example",
        )
        self.assertEqual(status, 403)
        self.assertEqual(document["error"], "invalid_origin")


if __name__ == "__main__":
    unittest.main()
