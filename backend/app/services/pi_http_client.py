"""访问树莓派的 HTTP 客户端工具。

设计目标
========

* 所有访问树莓派 API 的请求 **必须绕过系统代理**。
* 即使系统设置了 ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY``
  （例如 Clash / VPN），也不允许把这些请求路由到代理上。
* 树莓派是局域网设备，必须直连。
* 通过 :func:`requests.Session.trust_env = False` 实现。

注意事项
========

* ``trust_env = False`` 会让 requests 不再读取 ``HTTP_PROXY`` / ``HTTPS_PROXY`` 等
  环境变量；但 ``NO_PROXY`` 也会被忽略（不过对局域网直连来说没关系）。
* 不在这里做 mDNS 解析；解析由 :mod:`app.services.pi_endpoint_resolver` 负责。
* 这里只做 HTTP 层封装；上层应使用 :func:`pi_get` / :func:`pi_post` 而不是
  直接调用 :mod:`requests`。
"""

from __future__ import annotations

from typing import Any

import requests


def make_pi_session() -> requests.Session:
    """构造一个绕过系统代理的 requests Session。

    树莓派位于局域网，不能走 Clash/VPN 代理。
    """
    session = requests.Session()
    session.trust_env = False
    return session


def pi_get(url: str, *, timeout: float = 5.0, **kwargs: Any) -> requests.Response:
    """绕过系统代理的 GET。

    注意：如果显式传入 ``proxies`` 参数，调用方应自行确认目标不走代理。
    本函数默认不传任何 ``proxies`` 参数，仅通过 ``trust_env=False`` 屏蔽环境代理。
    """
    session = make_pi_session()
    return session.get(url, timeout=timeout, **kwargs)


def pi_post(
    url: str,
    *,
    json: Any = None,
    timeout: float = 10.0,
    **kwargs: Any,
) -> requests.Response:
    """绕过系统代理的 POST。"""
    session = make_pi_session()
    return session.post(url, json=json, timeout=timeout, **kwargs)


__all__ = ["make_pi_session", "pi_get", "pi_post"]
