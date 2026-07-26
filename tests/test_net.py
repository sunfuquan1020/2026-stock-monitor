"""国内数据源 IPv4 强制解析的单元测试。"""

import socket

import src.net as net


def test_domestic_host_matching():
    assert net._is_domestic("push2.eastmoney.com")
    assert net._is_domestic("datacenter-web.eastmoney.com")
    assert net._is_domestic("eastmoney.com")
    assert net._is_domestic("PUSH2.EASTMONEY.COM")  # 大小写不敏感
    assert net._is_domestic("push2.eastmoney.com.")  # 末尾点
    assert net._is_domestic("vip.stock.finance.sina.com.cn")
    assert net._is_domestic("qt.gtimg.cn")


def test_non_domestic_hosts_untouched():
    assert not net._is_domestic("finnhub.io")
    assert not net._is_domestic("query2.finance.yahoo.com")
    # 后缀必须落在域名边界上, 不能被仿冒域名命中
    assert not net._is_domestic("evil-eastmoney.com.attacker.net")
    assert not net._is_domestic("notgtimg.cn")
    assert not net._is_domestic("")
    assert not net._is_domestic(None)


def test_patch_forces_ipv4_for_domestic_only(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        calls.append((host, family))
        return [(family or socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(net, "_installed", False)
    net.force_ipv4_for_domestic_hosts()

    socket.getaddrinfo("push2.eastmoney.com", 443)
    socket.getaddrinfo("finnhub.io", 443)

    assert calls == [
        ("push2.eastmoney.com", socket.AF_INET),  # 强制 IPv4
        ("finnhub.io", 0),                        # 保持双栈
    ]


def test_patch_falls_back_when_no_ipv4(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        calls.append(family)
        if family == socket.AF_INET:
            raise socket.gaierror("no A record")
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (host, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(net, "_installed", False)
    net.force_ipv4_for_domestic_hosts()

    result = socket.getaddrinfo("push2.eastmoney.com", 443)

    assert calls == [socket.AF_INET, 0]  # 先试 IPv4, 失败后回退双栈
    assert result[0][0] == socket.AF_INET6


def test_install_is_idempotent(monkeypatch):
    monkeypatch.setattr(net, "_installed", False)
    net.force_ipv4_for_domestic_hosts()
    first = socket.getaddrinfo
    net.force_ipv4_for_domestic_hosts()
    assert socket.getaddrinfo is first
