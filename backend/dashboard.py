# 中转后端，用于代理前端请求到内网的服务器
import os
import json
import requests
import secrets
from typing import Any
from flask import Flask, jsonify, request, make_response, send_from_directory, Response
from flask_cors import CORS
from deployment_mode import (
    LAN_MODE,
    PUBLIC_MODE,
    load_boolean_setting,
    load_deployment_mode,
)

# 设置Flask
app = Flask(__name__)

# --- 路径配置 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DEPLOYMENT_MODE = load_deployment_mode()
DEFAULT_CONFIG_FILE = (
    os.path.join(CURRENT_DIR, '..', 'front', f'config.{DEPLOYMENT_MODE}.json')
    if 'GPU_MONITOR_DEPLOYMENT_MODE' in os.environ
    else os.path.join(CURRENT_DIR, '..', 'front', 'config.json')
)
CONFIG_FILE = os.environ.get(
    'GPU_MONITOR_CONFIG_FILE',
    DEFAULT_CONFIG_FILE,
)
DASHBOARD_USERNAME = os.environ.get('GPU_MONITOR_DASHBOARD_USERNAME', '')
DASHBOARD_PASSWORD = os.environ.get('GPU_MONITOR_DASHBOARD_PASSWORD', '')
AGENT_TOKEN = os.environ.get('GPU_MONITOR_AGENT_TOKEN', '')
DASHBOARD_HOST = os.environ.get('GPU_MONITOR_DASHBOARD_HOST', '0.0.0.0')
DASHBOARD_PORT = int(os.environ.get('GPU_MONITOR_DASHBOARD_PORT', '28456'))
VERIFY_AGENT_TLS = load_boolean_setting(
    'GPU_MONITOR_VERIFY_AGENT_TLS',
    DEPLOYMENT_MODE == PUBLIC_MODE,
)

if DEPLOYMENT_MODE == LAN_MODE:
    # Match the original GitHub LAN deployment and allow intranet origins.
    CORS(app, resources={r"/api/*": {"origins": "*"}})


@app.before_request
def require_dashboard_login() -> Any:
    """在公网模式强制校验 Dashboard Basic Auth。

    Args:
        无。

    Returns:
        校验通过或局域网模式时返回 ``None``；失败时返回认证响应。
    """
    if DEPLOYMENT_MODE == LAN_MODE:
        return None

    if not DASHBOARD_USERNAME or not DASHBOARD_PASSWORD or not AGENT_TOKEN:
        return jsonify({
            "code": 503,
            "msg": "Public Dashboard security is not configured",
        }), 503

    auth = request.authorization
    valid = (
        auth is not None
        and secrets.compare_digest(auth.username or '', DASHBOARD_USERNAME)
        and secrets.compare_digest(auth.password or '', DASHBOARD_PASSWORD)
    )
    if valid:
        return None
    return Response(
        'Authentication required',
        401,
        {'WWW-Authenticate': 'Basic realm="GPU Cluster Monitor", charset="UTF-8"'},
    )

def load_config() -> dict[str, Any]:
    """读取包含节点元数据和 Agent 地址的配置文件。

    Args:
        无。

    Returns:
        配置字典；文件不存在或解析失败时返回空节点配置。
    """
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config from {CONFIG_FILE}: {e}")
            return {"servers": [], "error": str(e)}
    else:
        print(f"Config file not found at: {CONFIG_FILE}")
    return {"servers": []}


def no_cache_response(response: Any) -> Any:
    """为响应添加禁止缓存的 HTTP 头。

    Args:
        response: 需要修改响应头的 Flask 响应对象。

    Returns:
        已添加禁止缓存响应头的原响应对象。
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route('/')
def serve_index() -> Any:
    """提供 GPU Dashboard 的前端入口页面。

    Args:
        无。

    Returns:
        禁止缓存的 ``index.html`` Flask 响应。
    """
    response = make_response(send_from_directory(os.path.join(CURRENT_DIR, '..', 'front'), 'index.html'))
    return no_cache_response(response)

@app.route('/<path:filename>')
def serve_static(filename: str) -> Any:
    """按安全后缀白名单提供前端静态资源。

    Args:
        filename: ``front`` 目录下的相对文件名。

    Returns:
        静态文件响应、配置 JSON 或 HTTP 403 错误响应。
    """
    if filename == 'config.json' and DEPLOYMENT_MODE == LAN_MODE:
        return no_cache_response(jsonify(load_config()))
    if filename.endswith('.json') and DEPLOYMENT_MODE == PUBLIC_MODE:
        return jsonify({"error": "File not allowed"}), 403
    # 安全限制：只允许特定后缀，防止路径遍历
    if filename.endswith(('.js', '.css', '.html', '.json', '.png', '.jpg', '.ico')):
        response = make_response(send_from_directory(os.path.join(CURRENT_DIR, '..', 'front'), filename))
        return no_cache_response(response)
    else:
        return jsonify({"error": "File not allowed"}), 403

@app.route('/api/config')
def get_config() -> Any:
    """向前端返回与部署模式匹配的节点列表。

    公网模式会隐藏 Agent URL，局域网模式保留原项目完整配置。

    Args:
        无。

    Returns:
        Flask JSON 节点配置响应。
    """
    config = load_config()
    if DEPLOYMENT_MODE == LAN_MODE:
        # Compatibility with the original GitHub version, which returned the
        # full intranet Agent configuration and did not require a login.
        return jsonify({**config, "deployment_mode": LAN_MODE})

    # Public browsers only need display metadata. Keep Agent URLs private.
    public_servers = [
        {"id": server.get("id"), "name": server.get("name")}
        for server in config.get("servers", [])
    ]
    return jsonify({"deployment_mode": PUBLIC_MODE, "servers": public_servers})

def proxy_server_request(
    server_id: str | None,
    resource: str,
) -> Any:
    """把指定节点的状态或历史请求转发给对应 Agent。

    Args:
        server_id: 前端选择的节点 ID；为空时返回参数错误。
        resource: Agent 资源名称，只允许 ``status`` 或 ``history``。

    Returns:
        Flask 可以直接返回的 JSON 响应或 ``(响应, 状态码)`` 元组。
    """
    if not server_id:
        return jsonify({"code": 400, "msg": "缺少参数: id"}), 400
    if resource not in {"status", "history"}:
        return jsonify({"code": 404, "msg": "不支持的 Agent 资源"}), 404

    config = load_config()
    servers = config.get('servers', [])
    
    # 根据 ID 查找对应的服务器配置
    target_server = next((s for s in servers if s['id'] == server_id), None)
    
    if not target_server:
        return jsonify({"code": 404, "msg": "未找到该服务器配置"}), 404
    
    base_url = target_server.get('url', '').rstrip('/')
    if not base_url:
        return jsonify({"code": 500, "msg": "该服务器配置缺少 URL"}), 500

    # 拼接目标 Agent 的 API 地址
    if resource == "history":
        limit = request.args.get('limit', '100')
        target_api = f"{base_url}/api/history?limit={limit}"
    else:
        target_api = f"{base_url}/api/status"

    try:
        # Public mode verifies HTTPS Agents by default. LAN mode keeps the
        # original self-signed-certificate compatibility unless overridden.
        print(f"Proxying request to: {target_api}")
        headers = {}
        if DEPLOYMENT_MODE == PUBLIC_MODE:
            headers['Authorization'] = f'Bearer {AGENT_TOKEN}'
        resp = requests.get(
            target_api,
            timeout=10,
            verify=VERIFY_AGENT_TLS,
            headers=headers,
        )

        # 返回数据
        return jsonify(resp.json()), resp.status_code

    except requests.exceptions.Timeout:
        return jsonify({"code": 504, "msg": "连接目标服务器超时"}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"code": 502, "msg": "无法连接到目标服务器 (Connection Refused)"}), 502
    except Exception as e:
        print(f"Proxy Error: {e}")
        return jsonify({"code": 500, "msg": f"代理服务内部错误: {str(e)}"}), 500


@app.route('/api/nodes/<server_id>/<resource>')
def proxy_node_resource(server_id: str, resource: str) -> Any:
    """提供适合 Nginx 节点直连架构的稳定 API 路径。

    Args:
        server_id: URL 中的节点唯一 ID。
        resource: 请求的 Agent 资源名称。

    Returns:
        对应节点的 Agent JSON 响应。
    """
    return proxy_server_request(server_id, resource)


@app.route('/api/proxy')
def proxy_request() -> Any:
    """兼容旧版基于查询参数的 Dashboard 代理接口。

    Args:
        无。

    Returns:
        对应节点的状态或历史 JSON 响应。
    """
    server_id = request.args.get('id')
    resource = "history" if request.args.get('history') == '1' else "status"
    return proxy_server_request(server_id, resource)

if __name__ == '__main__':
    print(f"Dashboard Proxy running on {DASHBOARD_HOST}:{DASHBOARD_PORT} ({DEPLOYMENT_MODE})")
    print(f"Looking for config at: {CONFIG_FILE}")
    app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False)
