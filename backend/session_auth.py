"""为公网状态页提供低资源、无服务器端会话存储的登录服务。"""

from __future__ import annotations

import argparse
import base64
import crypt
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SESSION_COOKIE = "lab_session"
SECURE_SESSION_COOKIE = "__Host-lab_session"
MAX_REQUEST_BYTES = 4096
SHORT_SESSION_SECONDS = 12 * 60 * 60
REMEMBER_SESSION_SECONDS = 30 * 24 * 60 * 60
FAILURE_WINDOW_SECONDS = 10 * 60
LOCKOUT_SECONDS = 15 * 60
MAX_FAILURES = 5


@dataclass(frozen=True)
class Credential:
    """保存唯一允许登录的用户名与系统密码哈希。"""

    username: str
    password_hash: str
    version: str


@dataclass(frozen=True)
class AuthSettings:
    """保存会话认证服务的运行配置。"""

    host: str
    port: int
    htpasswd_path: str
    secret: bytes
    force_secure_cookie: bool


class LoginRateLimiter:
    """按客户端地址限制连续登录失败次数。"""

    def __init__(self) -> None:
        """创建线程安全的内存失败记录表。

        Args:
            无。

        Returns:
            无返回值。
        """
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, client_ip: str, now: float | None = None) -> int:
        """返回客户端仍需等待的锁定秒数。

        Args:
            client_ip: 由本机 Nginx 转发的客户端地址。
            now: 可选的当前单调时钟值，主要用于测试。

        Returns:
            未锁定时返回 0，否则返回向上取整后的剩余秒数。
        """
        current = time.monotonic() if now is None else now
        with self._lock:
            locked_until = self._locked_until.get(client_ip, 0.0)
            if locked_until <= current:
                self._locked_until.pop(client_ip, None)
                return 0
            return max(1, int(locked_until - current + 0.999))

    def record_failure(self, client_ip: str, now: float | None = None) -> int:
        """记录一次失败，并在达到阈值时锁定客户端。

        Args:
            client_ip: 由本机 Nginx 转发的客户端地址。
            now: 可选的当前单调时钟值，主要用于测试。

        Returns:
            触发锁定时返回锁定秒数，否则返回 0。
        """
        current = time.monotonic() if now is None else now
        cutoff = current - FAILURE_WINDOW_SECONDS
        with self._lock:
            failures = [
                value for value in self._failures.get(client_ip, []) if value >= cutoff
            ]
            failures.append(current)
            self._failures[client_ip] = failures
            if len(failures) < MAX_FAILURES:
                return 0
            self._failures.pop(client_ip, None)
            self._locked_until[client_ip] = current + LOCKOUT_SECONDS
            return LOCKOUT_SECONDS

    def clear(self, client_ip: str) -> None:
        """在登录成功后清除客户端失败记录。

        Args:
            client_ip: 由本机 Nginx 转发的客户端地址。

        Returns:
            无返回值。
        """
        with self._lock:
            self._failures.pop(client_ip, None)
            self._locked_until.pop(client_ip, None)


def load_secret(secret_path: str) -> bytes:
    """读取至少 32 字节的 HMAC 会话密钥。

    Args:
        secret_path: 仅认证服务用户可读的密钥文件路径。

    Returns:
        原始会话密钥字节。

    Raises:
        ValueError: 密钥文件内容短于 32 字节。
        OSError: 密钥文件无法读取。
    """
    secret = Path(secret_path).read_bytes().strip()
    if len(secret) < 32:
        raise ValueError("会话密钥至少需要 32 字节")
    return secret


def load_credential(htpasswd_path: str) -> Credential:
    """从 htpasswd 文件读取唯一登录账号。

    Args:
        htpasswd_path: Apache htpasswd 文件路径。

    Returns:
        用户名、系统 crypt 哈希及其版本摘要。

    Raises:
        ValueError: 文件为空、格式错误或包含多个账号。
        OSError: 文件无法读取。
    """
    lines = [
        line.strip()
        for line in Path(htpasswd_path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(lines) != 1 or ":" not in lines[0]:
        raise ValueError("htpasswd 文件必须只包含一个有效账号")
    username, password_hash = lines[0].split(":", 1)
    if not username or not password_hash.startswith(("$5$", "$6$", "$2")):
        raise ValueError("只允许 SHA-256、SHA-512 crypt 或 bcrypt 密码哈希")
    version = hashlib.sha256(lines[0].encode("utf-8")).hexdigest()[:20]
    return Credential(username, password_hash, version)


def verify_password(username: str, password: str, credential: Credential) -> bool:
    """使用系统 crypt 实现校验用户名和强哈希密码。

    Args:
        username: 登录表单提交的用户名。
        password: 登录表单提交的明文密码。
        credential: htpasswd 文件中的可信账号记录。

    Returns:
        用户名和密码都正确时返回 ``True``。
    """
    calculated = crypt.crypt(password, credential.password_hash) or ""
    password_valid = secrets.compare_digest(calculated, credential.password_hash)
    username_valid = secrets.compare_digest(username, credential.username)
    return password_valid and username_valid


def encode_base64url(value: bytes) -> str:
    """把字节编码为无填充的 URL 安全 Base64。

    Args:
        value: 需要编码的原始字节。

    Returns:
        可安全放入 Cookie 的 Base64URL 字符串。
    """
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: str) -> bytes:
    """解码无填充的 URL 安全 Base64 字符串。

    Args:
        value: Cookie 中的 Base64URL 字符串。

    Returns:
        解码后的原始字节。

    Raises:
        ValueError: 输入不是有效 Base64URL。
    """
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError("会话编码无效") from error


def create_session_token(
    username: str,
    credential_version: str,
    secret: bytes,
    lifetime_seconds: int,
    now: int | None = None,
) -> str:
    """创建带过期时间和凭据版本的 HMAC 签名会话。

    Args:
        username: 已完成密码验证的用户名。
        credential_version: 当前 htpasswd 内容的摘要版本。
        secret: HMAC-SHA256 服务端密钥。
        lifetime_seconds: 会话有效秒数。
        now: 可选的当前 Unix 时间，主要用于测试。

    Returns:
        ``载荷.签名`` 形式的会话令牌。
    """
    issued_at = int(time.time()) if now is None else now
    payload = json.dumps(
        {
            "v": 1,
            "u": username,
            "cv": credential_version,
            "iat": issued_at,
            "exp": issued_at + lifetime_seconds,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload_text = encode_base64url(payload)
    signature = hmac.new(secret, payload_text.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_text}.{encode_base64url(signature)}"


def verify_session_token(
    token: str,
    credential: Credential,
    secret: bytes,
    now: int | None = None,
) -> bool:
    """校验会话签名、过期时间、用户名和密码版本。

    Args:
        token: 浏览器 Cookie 中的完整会话令牌。
        credential: 当前 htpasswd 账号记录。
        secret: HMAC-SHA256 服务端密钥。
        now: 可选的当前 Unix 时间，主要用于测试。

    Returns:
        会话完整、未过期且对应当前密码时返回 ``True``。
    """
    try:
        payload_text, signature_text = token.split(".", 1)
        expected = hmac.new(
            secret, payload_text.encode("ascii"), hashlib.sha256
        ).digest()
        provided = decode_base64url(signature_text)
        if not hmac.compare_digest(provided, expected):
            return False
        payload = json.loads(decode_base64url(payload_text).decode("utf-8"))
        current = int(time.time()) if now is None else now
        return (
            payload.get("v") == 1
            and secrets.compare_digest(str(payload.get("u", "")), credential.username)
            and secrets.compare_digest(
                str(payload.get("cv", "")), credential.version
            )
            and int(payload.get("iat", 0)) <= current + 60
            and int(payload.get("exp", 0)) > current
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
        return False


def parse_cookie_token(cookie_header: str) -> str:
    """优先从 HTTPS 专用 Cookie 中提取会话令牌。

    Args:
        cookie_header: 浏览器发送的原始 Cookie 请求头。

    Returns:
        会话令牌；没有可用 Cookie 时返回空字符串。
    """
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return ""
    for name in (SECURE_SESSION_COOKIE, SESSION_COOKIE):
        if name in cookie:
            return cookie[name].value
    return ""


def origin_matches_host(origin: str, original_host: str) -> bool:
    """检查写操作的 Origin 是否与浏览器访问主机完全一致。

    Args:
        origin: 浏览器提交的 Origin 请求头。
        original_host: 可信 Nginx 转发的原始 Host 请求头。

    Returns:
        协议为 HTTP/HTTPS 且主机和端口一致时返回 ``True``。
    """
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and hmac.compare_digest(parsed.netloc.lower(), original_host.lower())
    )


class AuthRequestHandler(BaseHTTPRequestHandler):
    """处理 Nginx auth_request、登录状态和登录写操作。"""

    settings: AuthSettings
    rate_limiter: LoginRateLimiter
    server_version = "LabSessionAuth"
    sys_version = ""

    def log_message(self, format_string: str, *args: Any) -> None:
        """仅记录不包含请求正文和 Cookie 的简要访问日志。

        Args:
            format_string: ``BaseHTTPRequestHandler`` 提供的日志格式。
            args: 日志格式参数。

        Returns:
            无返回值。
        """
        print(f"auth {self.client_address[0]} {format_string % args}")

    def send_json(
        self,
        status: int,
        document: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """发送禁止缓存的紧凑 JSON 响应。

        Args:
            status: HTTP 状态码。
            document: 需要序列化的响应对象。
            extra_headers: 可选的附加响应头。

        Returns:
            无返回值。
        """
        body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def client_ip(self) -> str:
        """读取仅由本机 Nginx 注入的原始客户端地址。

        Args:
            无。

        Returns:
            客户端地址；缺少转发头时回退为 TCP 对端地址。
        """
        return self.headers.get("X-Real-IP", self.client_address[0]).strip()[:64]

    def request_is_secure(self) -> bool:
        """判断当前请求是否应签发 Secure Cookie。

        Args:
            无。

        Returns:
            强制安全 Cookie 或原始协议为 HTTPS 时返回 ``True``。
        """
        return self.settings.force_secure_cookie or self.headers.get(
            "X-Forwarded-Proto", ""
        ).lower() == "https"

    def current_credential(self) -> Credential:
        """重新读取密码记录，使改密可以立即使旧会话失效。

        Args:
            无。

        Returns:
            当前唯一有效账号记录。
        """
        return load_credential(self.settings.htpasswd_path)

    def authenticated(self) -> bool:
        """校验当前请求携带的 HttpOnly 会话 Cookie。

        Args:
            无。

        Returns:
            会话有效时返回 ``True``。
        """
        token = parse_cookie_token(self.headers.get("Cookie", ""))
        return bool(token) and verify_session_token(
            token, self.current_credential(), self.settings.secret
        )

    def write_origin_is_valid(self) -> bool:
        """校验登录和退出请求的同源性。

        Args:
            无。

        Returns:
            Origin 与 Nginx 传递的原始 Host 匹配时返回 ``True``。
        """
        return origin_matches_host(
            self.headers.get("Origin", ""),
            self.headers.get("X-Original-Host", ""),
        )

    def read_json_body(self) -> dict[str, Any]:
        """读取有大小限制的 JSON 请求正文。

        Args:
            无。

        Returns:
            解析后的 JSON 对象。

        Raises:
            ValueError: 长度、类型或 JSON 结构不合法。
        """
        if self.headers.get_content_type() != "application/json":
            raise ValueError("请求必须使用 application/json")
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("请求长度无效") from error
        if not 1 <= content_length <= MAX_REQUEST_BYTES:
            raise ValueError("请求正文大小不合法")
        document = json.loads(self.rfile.read(content_length).decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("请求正文必须是 JSON 对象")
        return document

    def set_session_cookie(self, token: str, remember: bool) -> str:
        """构建符合当前协议安全等级的会话 Cookie。

        Args:
            token: 已签名的会话令牌。
            remember: 是否给 Cookie 设置 30 天 Max-Age。

        Returns:
            可直接写入 Set-Cookie 响应头的字符串。
        """
        secure = self.request_is_secure()
        cookie_name = SECURE_SESSION_COOKIE if secure else SESSION_COOKIE
        attributes = [f"{cookie_name}={token}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if secure:
            attributes.append("Secure")
        if remember:
            attributes.append(f"Max-Age={REMEMBER_SESSION_SECONDS}")
        return "; ".join(attributes)

    def expired_cookie_headers(self) -> list[str]:
        """生成同时清除 HTTP 与 HTTPS 会话的 Cookie 头。

        Args:
            无。

        Returns:
            两个过期 Set-Cookie 值组成的列表。
        """
        return [
            f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0",
            f"{SECURE_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Secure; Max-Age=0",
        ]

    def do_GET(self) -> None:
        """处理会话校验、登录状态和本机健康检查。

        Args:
            无。

        Returns:
            无返回值。
        """
        path = urlsplit(self.path).path
        if path == "/health":
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        authenticated = self.authenticated()
        if path == "/verify":
            self.send_response(
                HTTPStatus.NO_CONTENT if authenticated else HTTPStatus.UNAUTHORIZED
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/session":
            self.send_json(HTTPStatus.OK, {"authenticated": authenticated})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        """处理同源登录和退出请求。

        Args:
            无。

        Returns:
            无返回值。
        """
        path = urlsplit(self.path).path
        if path not in {"/login", "/logout"}:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self.write_origin_is_valid():
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "invalid_origin"})
            return
        if path == "/logout":
            self.send_response(HTTPStatus.NO_CONTENT)
            for cookie_header in self.expired_cookie_headers():
                self.send_header("Set-Cookie", cookie_header)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        client_ip = self.client_ip()
        retry_after = self.rate_limiter.retry_after(client_ip)
        if retry_after:
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "too_many_attempts", "retry_after": retry_after},
                {"Retry-After": str(retry_after)},
            )
            return
        try:
            document = self.read_json_body()
        except (ValueError, json.JSONDecodeError, UnicodeError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return

        username = document.get("username")
        password = document.get("password")
        remember = document.get("remember", False)
        if (
            not isinstance(username, str)
            or not isinstance(password, str)
            or not isinstance(remember, bool)
            or not 1 <= len(username) <= 128
            or not 1 <= len(password) <= 512
        ):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return

        credential = self.current_credential()
        if not verify_password(username, password, credential):
            lockout = self.rate_limiter.record_failure(client_ip)
            headers = {"Retry-After": str(lockout)} if lockout else None
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS if lockout else HTTPStatus.UNAUTHORIZED,
                {
                    "error": "too_many_attempts" if lockout else "invalid_credentials",
                    "retry_after": lockout,
                },
                headers,
            )
            return

        self.rate_limiter.clear(client_ip)
        lifetime = REMEMBER_SESSION_SECONDS if remember else SHORT_SESSION_SECONDS
        token = create_session_token(
            credential.username,
            credential.version,
            self.settings.secret,
            lifetime,
        )
        self.send_json(
            HTTPStatus.OK,
            {"authenticated": True, "expires_in": lifetime},
            {"Set-Cookie": self.set_session_cookie(token, remember)},
        )


def load_settings(arguments: argparse.Namespace) -> AuthSettings:
    """根据命令行参数和环境变量构建服务配置。

    Args:
        arguments: ``argparse`` 解析后的命令行参数。

    Returns:
        已读取密钥的不可变认证服务配置。
    """
    force_secure = os.environ.get("LAB_AUTH_FORCE_SECURE_COOKIE", "0").strip().lower()
    return AuthSettings(
        host=arguments.host,
        port=arguments.port,
        htpasswd_path=arguments.htpasswd,
        secret=load_secret(arguments.secret_file),
        force_secure_cookie=force_secure in {"1", "true", "yes", "on"},
    )


def create_server(settings: AuthSettings) -> ThreadingHTTPServer:
    """创建只绑定本机地址的多线程认证 HTTP 服务。

    Args:
        settings: 完整认证服务配置。

    Returns:
        已绑定但尚未进入循环的 HTTP 服务器。

    Raises:
        ValueError: 监听地址不是本机回环地址。
    """
    if settings.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("认证服务只允许绑定回环地址")
    load_credential(settings.htpasswd_path)
    handler = type(
        "ConfiguredAuthRequestHandler",
        (AuthRequestHandler,),
        {"settings": settings, "rate_limiter": LoginRateLimiter()},
    )
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    """解析启动参数并持续提供本机会话认证接口。

    Args:
        无。

    Returns:
        无返回值。
    """
    parser = argparse.ArgumentParser(description="提供 GPU 状态页安全会话认证")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=28458, type=int)
    parser.add_argument(
        "--htpasswd", default="/etc/nginx/.htpasswd-lab-monitor"
    )
    parser.add_argument("--secret-file", default="/etc/lab-status/session-secret")
    settings = load_settings(parser.parse_args())
    server = create_server(settings)
    print(f"会话认证服务已启动：{settings.host}:{settings.port}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
