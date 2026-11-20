from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from bili_api import check_login
from bili_common import (
    AUTH_JSON,
    CHROME_PROFILE,
    YUTTO_AUTH,
    clear_auth,
    load_cookies,
    save_auth,
)

DEFAULT_PORT = 9223
BILI_URL = "https://www.bilibili.com/"
LOGIN_URL = "https://passport.bilibili.com/login"


def _find_chrome() -> str:
    candidates = [
        os.environ.get("BILIWATCH_CHROME", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("找不到 Chrome/Edge，可设置 BILIWATCH_CHROME 指向浏览器 exe")


def _http_json(url: str, method: str = "GET") -> dict:
    req = Request(url, method=method, headers={"User-Agent": "bilibili-watch"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _debug_ready(port: int) -> bool:
    try:
        _http_json(f"http://127.0.0.1:{port}/json/version")
        return True
    except Exception:
        return False


def _wait_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _debug_ready(port):
            return True
        time.sleep(0.5)
    return False


def _launch_chrome(port: int) -> str:
    chrome = _find_chrome()
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={CHROME_PROFILE}",
        "--remote-allow-origins=*",
        "--enable-automation",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        LOGIN_URL,
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return chrome


def _ensure_page(port: int) -> str:
    def pages() -> list[dict]:
        try:
            targets = _http_json(f"http://127.0.0.1:{port}/json/list")
        except Exception:
            return []
        return [
            item
            for item in targets
            if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
        ]

    current = pages()
    for item in current:
        if "passport.bilibili.com/login" in str(item.get("url") or ""):
            return str(item["webSocketDebuggerUrl"])
    for item in current:
        if "bilibili.com" in str(item.get("url") or ""):
            return str(item["webSocketDebuggerUrl"])

    try:
        _http_json(f"http://127.0.0.1:{port}/json/new?{quote(LOGIN_URL, safe='')}", method="PUT")
    except Exception:
        pass
    deadline = time.time() + 15
    while time.time() < deadline:
        time.sleep(0.5)
        current = pages()
        for item in current:
            if "passport.bilibili.com/login" in str(item.get("url") or ""):
                return str(item["webSocketDebuggerUrl"])
        for item in current:
            if "bilibili.com" in str(item.get("url") or ""):
                return str(item["webSocketDebuggerUrl"])
        if current:
            return str(current[-1]["webSocketDebuggerUrl"])
    raise RuntimeError("Chrome 调试口已开，但找不到页面")


def _norm_path(value: str) -> str:
    try:
        return str(Path(value).resolve()).rstrip("\\/").lower()
    except Exception:
        return value.rstrip("\\/").lower()


def _is_our_profile(port: int) -> bool:
    try:
        version = _http_json(f"http://127.0.0.1:{port}/json/version")
        ws_url = str(version.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            return False
        with _connect(ws_url, port) as ws:
            result = _cdp_call(ws, "Browser.getBrowserCommandLine", timeout=5)
        for arg in result.get("arguments") or []:
            if str(arg).lower().startswith("--user-data-dir="):
                value = str(arg).split("=", 1)[1].strip('"')
                return _norm_path(value) == _norm_path(str(CHROME_PROFILE))
    except Exception:
        return False
    return False


def _select_port(preferred: int) -> int:
    for offset in range(0, 20):
        port = preferred + offset
        if not _debug_ready(port):
            return port
        if _is_our_profile(port):
            return port
    raise RuntimeError(f"{preferred}~{preferred + 19} 都被其他调试 Chrome 占用，找不到可用端口")


def _connect(ws_url: str, port: int = DEFAULT_PORT):
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError("缺少 websockets 包，请使用安装该依赖的 Python 环境运行") from exc
    return connect(
        ws_url,
        additional_headers={"Origin": f"http://127.0.0.1:{port}"},
        open_timeout=10,
        max_size=30 * 1024 * 1024,
    )


def _cdp_call(ws, method: str, params: dict | None = None, timeout: float = 15.0):
    call_id = int(time.time() * 1000) % 1_000_000_000
    ws.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        raw = ws.recv(timeout=remaining)
        message = json.loads(raw)
        if message.get("id") != call_id:
            continue
        if "error" in message:
            raise RuntimeError(f"CDP {method} 失败：{message['error']}")
        return message.get("result") or {}
    raise TimeoutError(f"CDP {method} 超时")


def _browser_cookies(ws) -> list[dict]:
    try:
        _cdp_call(ws, "Network.enable", timeout=5)
    except Exception:
        pass
    try:
        result = _cdp_call(ws, "Network.getAllCookies")
    except Exception:
        result = _cdp_call(ws, "Storage.getCookies")
    return list(result.get("cookies") or [])


def _pick_cookies(raw_cookies: list[dict]) -> dict[str, str]:
    picked: dict[str, str] = {}
    for cookie in raw_cookies:
        domain = str(cookie.get("domain") or "").lower()
        if "bilibili.com" not in domain:
            continue
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name:
            continue
        if not value and name in picked:
            continue
        picked[name] = value
    return picked


def login_via_chrome(port: int = DEFAULT_PORT, timeout: int = 300) -> int:
    def say(message: str) -> None:
        print(message, flush=True)

    selected = _select_port(port)
    if selected != port:
        say(f"端口 {port} 被其他调试 Chrome 占用，改用 {selected}（本 skill 专用 profile）")
    port = selected

    if not _debug_ready(port):
        chrome = _launch_chrome(port)
        say(f"已启动独立 Chrome：{chrome}")
        say(f"profile：{CHROME_PROFILE}")
        say(f"调试端口：{port}")
        if not _wait_port(port, 25):
            say(f"ERROR: Chrome 调试口没有起来（{port} 未监听）")
            return 1
    else:
        say(f"复用本 skill 的调试 Chrome：127.0.0.1:{port}")

    say("已打开 B 站登录页：https://passport.bilibili.com/login")
    say("请在窗口里扫码或密码登录；如果页面空白/没有登录框，点地址栏回车重开上面的链接。")
    say(f"登录成功后自动继续，最长等待 {timeout} 秒。")

    deadline = time.time() + timeout
    last_note = 0.0
    while time.time() < deadline:
        try:
            ws_url = _ensure_page(port)
            with _connect(ws_url, port) as ws:
                while time.time() < deadline:
                    cookies = _pick_cookies(_browser_cookies(ws))
                    sessdata = cookies.get("SESSDATA", "")
                    if sessdata:
                        login = check_login(cookies)
                        if login:
                            save_auth(cookies, login.get("mid"), login.get("uname"))
                            say(f"AUTH_OK 用户={login.get('uname')} mid={login.get('mid')}")
                            say(f"已写入：{AUTH_JSON}")
                            say(f"已写入：{YUTTO_AUTH}")
                            return 0
                        now = time.time()
                        if now - last_note > 10:
                            say("检测到 SESSDATA，但 B 站还没确认登录，继续等待…")
                            last_note = now
                    else:
                        now = time.time()
                        if now - last_note > 10:
                            remain = int(deadline - now)
                            say(f"等待登录…（剩余 {remain}s）")
                            last_note = now
                    time.sleep(2)
        except Exception as exc:
            if time.time() >= deadline:
                break
            say(f"调试连接中断，重试：{exc}")
            time.sleep(2)

    print("AUTH_TIMEOUT: 没有等到有效登录。可重跑 auth，或检查 Chrome 窗口里的登录状态。")
    return 1


def check_auth() -> int:
    cookies = load_cookies()
    if not cookies:
        print("AUTH=none 尚未登录（可运行 auth）")
        return 1
    login = check_login(cookies)
    if not login:
        print("AUTH=expired 本地有 cookie，但 B 站判定未登录/已过期，请重跑 auth")
        return 1
    print(f"AUTH=ok 用户={login.get('uname')} mid={login.get('mid')}")
    return 0


def logout() -> int:
    removed = clear_auth()
    if removed:
        print("已删除：")
        for path in removed:
            print(f"  {path}")
    else:
        print("没有本地登录文件")
    return 0