from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from bili_common import format_time

API = "https://api.bilibili.com"
NAV_URL = f"{API}/x/web-interface/nav"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)
SORT_MAP = {"time": 0, "hot": 1, "reply": 2}
SORT_LABEL = {"time": "按时间", "hot": "按点赞", "reply": "按回复数"}
RISK_CODES = {-352, -412, -403}
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")


class ApiError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.url = url

    def __str__(self) -> str:
        extra = []
        if self.code is not None:
            extra.append(f"code={self.code}")
        if self.status is not None:
            extra.append(f"http={self.status}")
        return f"{self.args[0]} ({', '.join(extra)})" if extra else str(self.args[0])


class RiskControlError(ApiError):
    pass


def _as_list(value) -> list:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if "rpid" in value:
            return [value]
        return [item for item in value.values() if isinstance(item, dict)]
    return []


def request_json(
    url: str,
    params: dict | None = None,
    cookies: dict[str, str] | None = None,
    referer: str | None = None,
    timeout: int = 25,
    retries: int = 2,
) -> dict:
    if params:
        clean = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = int(value)
            clean[key] = value
        query = urlencode(clean, doseq=True)
        url = url + ("&" if "?" in url else "?") + query

    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": referer or "https://www.bilibili.com/",
    }
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            break
        except HTTPError as exc:
            raw = exc.read()
            code = None
            message = ""
            try:
                doc = json.loads(raw.decode("utf-8", errors="replace"))
                code = doc.get("code")
                message = str(doc.get("message") or "")
            except Exception:
                message = raw[:300].decode("utf-8", errors="replace")
            if exc.code == 412 or code in RISK_CODES:
                raise RiskControlError(
                    f"触发 B 站风控：{message or exc.reason}", code=code, status=exc.code, url=url
                ) from exc
            raise ApiError(f"B 站接口失败：{message or exc.reason}", code=code, status=exc.code, url=url) from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise ApiError(f"网络请求失败：{exc}") from exc

    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ApiError("B 站返回的不是 JSON，可能被风控或需要验证", url=url) from exc
    if not isinstance(doc, dict):
        raise ApiError("B 站返回格式异常", url=url)
    return doc


def nav_payload(cookies: dict[str, str] | None = None) -> dict:
    doc = request_json(NAV_URL, cookies=cookies)
    if doc.get("code") != 0:
        raise ApiError(f"读取登录状态失败：{doc.get('message')}", code=doc.get("code"))
    return doc.get("data") or {}


def check_login(cookies: dict[str, str] | None = None) -> dict | None:
    if not cookies or not cookies.get("SESSDATA"):
        return None
    try:
        data = nav_payload(cookies)
    except ApiError:
        return None
    return data if data.get("isLogin") else None


def _resolve_redirect(url: str) -> str:
    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=15) as resp:
            return resp.geturl()
    except Exception:
        return url


def parse_target(target: str) -> dict:
    raw = (target or "").strip()
    if not raw:
        raise ApiError("缺少视频地址/BV 号")

    page = 1
    if raw.startswith(("http://", "https://")):
        url = raw
        host = urlparse(url).netloc.lower()
        if "b23.tv" in host or "bili2233" in host:
            url = _resolve_redirect(url)
        query = parse_qs(urlparse(url).query)
        if query.get("p"):
            try:
                page = max(1, int(query["p"][0]))
            except ValueError:
                page = 1
        match = BV_RE.search(url)
        if match:
            bvid = match.group(0)
            return {"bvid": bvid, "aid": None, "page": page,
                    "url": f"https://www.bilibili.com/video/{bvid}"}
        match = re.search(r"/video/av(\d+)", url, re.I)
        if match:
            aid = int(match.group(1))
            return {"bvid": None, "aid": aid, "page": page,
                    "url": f"https://www.bilibili.com/video/av{aid}"}
        raise ApiError("链接中没有找到 BV 号或 av 号")

    match = BV_RE.search(raw)
    if match:
        return {"bvid": match.group(0), "aid": None, "page": page,
                "url": f"https://www.bilibili.com/video/{match.group(0)}"}
    match = re.fullmatch(r"(?:av)?(\d+)", raw, re.I)
    if match:
        aid = int(match.group(1))
        return {"bvid": None, "aid": aid, "page": page,
                "url": f"https://www.bilibili.com/video/av{aid}"}
    raise ApiError("无法识别输入，请给 BV 号、av 号或视频链接")


_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
_wbi_cache: dict = {"ts": 0.0, "keys": None}


def _wbi_keys(cookies: dict[str, str] | None = None) -> tuple[str, str]:
    now = time.time()
    if _wbi_cache["keys"] and now - _wbi_cache["ts"] < 600:
        return _wbi_cache["keys"]
    data = nav_payload(cookies)
    wbi = data.get("wbi_img") or {}
    img_url = wbi.get("img_url") or ""
    sub_url = wbi.get("sub_url") or ""
    img_key = Path(urlparse(img_url).path).stem
    sub_key = Path(urlparse(sub_url).path).stem
    if not img_key or not sub_key:
        raise ApiError("无法获取 WBI 签名密钥")
    _wbi_cache["ts"] = now
    _wbi_cache["keys"] = (img_key, sub_key)
    return img_key, sub_key


def sign_wbi(params: dict, cookies: dict[str, str] | None = None) -> dict:
    img_key, sub_key = _wbi_keys(cookies)
    values = {key: str(value) for key, value in params.items() if value is not None}
    values["wts"] = str(int(time.time()))
    items = sorted(values.items())
    cleaned = {key: re.sub(r"[!'()*]", "", value) for key, value in items}
    query = urlencode(cleaned)
    mixin = "".join((img_key + sub_key)[index] for index in _MIXIN_TAB)[:32]
    signed = dict(cleaned)
    signed["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return signed


def video_info(target: str, cookies: dict[str, str] | None = None) -> dict:
    parsed = parse_target(target)
    referer = f"https://www.bilibili.com/video/{parsed['bvid']}/" if parsed.get("bvid") else parsed["url"]
    params = {"bvid": parsed["bvid"]} if parsed.get("bvid") else {"aid": parsed["aid"]}
    doc = None
    try:
        doc = request_json(f"{API}/x/web-interface/view", params, cookies, referer)
    except RiskControlError:
        doc = None
    if doc is None or doc.get("code") in RISK_CODES:
        doc = request_json(f"{API}/x/web-interface/wbi/view", sign_wbi(params, cookies), cookies, referer)
    if doc.get("code") != 0 or not doc.get("data"):
        raise ApiError(f"读取视频信息失败：{doc.get('message')}", code=doc.get("code"))
    info = doc["data"]
    info.setdefault("bvid", parsed.get("bvid"))
    info.setdefault("aid", parsed.get("aid"))
    info["_selected_page"] = parsed.get("page", 1)
    return info


def _duration_seconds(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    if ":" in text:
        try:
            parts = [int(part) for part in text.split(":")]
        except ValueError:
            return 0
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _clean_text(value) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).replace("&amp;", "&").strip()


def search_videos(
    keyword: str,
    cookies: dict[str, str] | None = None,
    page: int = 1,
    page_size: int = 20,
    order: str = "totalrank",
) -> tuple[list[dict], int]:
    params = {
        "search_type": "video",
        "keyword": keyword,
        "page": page,
        "page_size": page_size,
        "order": order,
    }
    doc = request_json(
        f"{API}/x/web-interface/wbi/search/type",
        sign_wbi(params, cookies),
        cookies,
        "https://search.bilibili.com/",
    )
    if doc.get("code") != 0:
        raise ApiError(f"搜索失败：{doc.get('message')}", code=doc.get("code"))
    data = doc.get("data") or {}
    results = []
    for index, item in enumerate(_as_list(data.get("result")), 1):
        bvid = str(item.get("bvid") or "")
        if not bvid:
            continue
        results.append(
            {
                "bvid": bvid,
                "aid": int(item.get("aid") or 0) or None,
                "title": _clean_text(item.get("title")),
                "up": str(item.get("author") or ""),
                "up_mid": item.get("mid"),
                "duration": _duration_seconds(item.get("duration")),
                "play": int(item.get("play") or 0),
                "reply": int(item.get("review") or 0),
                "like": int(item.get("like") or 0),
                "pubdate": int(item.get("pubdate") or 0),
                "desc": _clean_text(item.get("description")),
                "url": f"https://www.bilibili.com/video/{bvid}/",
                "search_rank": index,
            }
        )
    return results, int(data.get("numResults") or 0)


def related_videos(bvid: str, cookies: dict[str, str] | None = None) -> list[dict]:
    referer = f"https://www.bilibili.com/video/{bvid}/"
    doc = request_json(
        f"{API}/x/web-interface/archive/related",
        {"bvid": bvid},
        cookies,
        referer,
    )
    if doc.get("code") != 0:
        raise ApiError(f"读取相关视频失败：{doc.get('message')}", code=doc.get("code"))
    data = doc.get("data") or []
    if isinstance(data, dict):
        data = data.get("list") or []
    results = []
    for index, item in enumerate(_as_list(data), 1):
        bvid = str(item.get("bvid") or "")
        if not bvid:
            continue
        stat = item.get("stat") or {}
        owner = item.get("owner") or {}
        results.append(
            {
                "bvid": bvid,
                "aid": int(item.get("aid") or 0) or None,
                "title": _clean_text(item.get("title")),
                "up": str(owner.get("name") or ""),
                "up_mid": owner.get("mid"),
                "duration": _duration_seconds(item.get("duration")),
                "play": int(stat.get("view") or 0),
                "reply": int(stat.get("reply") or 0),
                "like": int(stat.get("like") or 0),
                "pubdate": int(item.get("pubdate") or 0),
                "desc": _clean_text(item.get("desc")),
                "url": f"https://www.bilibili.com/video/{bvid}/",
                "related_rank": index,
            }
        )
    return results


def _normalize_reply(reply: dict, up_mid: int | str | None) -> dict:
    member = reply.get("member") or {}
    content = reply.get("content") or {}
    control = reply.get("reply_control") or {}
    mid = str(member.get("mid") or "")
    message = str(content.get("message") or "")
    has_image = bool(content.get("picture") or content.get("pictures") or content.get("image_urls"))
    return {
        "rpid": str(reply.get("rpid") or ""),
        "root": str(reply.get("root") or "0"),
        "parent": str(reply.get("parent") or "0"),
        "dialog": str(reply.get("dialog") or "0"),
        "mid": mid,
        "uname": str(member.get("uname") or ""),
        "level": (member.get("level_info") or {}).get("current_level"),
        "is_up": bool(up_mid is not None and mid == str(up_mid)),
        "likes": int(reply.get("like") or 0),
        "ctime": int(reply.get("ctime") or 0),
        "time": format_time(reply.get("ctime")),
        "message": message,
        "has_image": has_image,
        "rcount": int(reply.get("rcount") or 0),
        "location": str(control.get("location") or ""),
        "is_pinned": bool(control.get("is_pinned")),
        "replies": [],
        "subs_fetched": 0,
        "subs_complete": False,
        "subs_truncated": False,
    }


def _fetch_old_page(aid: int, pn: int, sort: str, cookies, referer: str) -> dict:
    return request_json(
        f"{API}/x/v2/reply",
        {"type": 1, "oid": aid, "pn": pn, "ps": 20, "sort": SORT_MAP.get(sort, 1), "nohot": 0},
        cookies,
        referer,
    )


def _collect_old(
    aid: int,
    cookies,
    referer: str,
    limit: int,
    sort: str,
    delay: float,
    logged_in: bool,
    progress,
) -> dict:
    roots: OrderedDict[str, dict] = OrderedDict()
    pinned: OrderedDict[str, dict] = OrderedDict()
    warnings: list[str] = []
    total_count = 0
    pages = 0
    login_limited = False

    for pn in range(1, 201):
        if pn > 1:
            time.sleep(delay + random.uniform(0.0, 0.4))
        doc = _fetch_old_page(aid, pn, sort, cookies, referer)
        if doc.get("code") != 0:
            raise ApiError(f"读取评论失败：{doc.get('message')}", code=doc.get("code"))
        data = doc.get("data") or {}
        page_info = data.get("page") or {}
        total_count = max(total_count, int(page_info.get("count") or 0))
        replies = _as_list(data.get("replies"))
        for item in _as_list(data.get("top_replies")):
            rpid = str(item.get("rpid") or "")
            if rpid and rpid not in pinned:
                pinned[rpid] = item
        for item in _as_list(data.get("hots")):
            rpid = str(item.get("rpid") or "")
            if rpid and rpid not in roots:
                roots[rpid] = item
        for item in replies:
            rpid = str(item.get("rpid") or "")
            if rpid and rpid not in roots:
                roots[rpid] = item
        pages = pn
        if progress:
            progress(f"主评论第 {pn} 页：累计 {len(roots)} 条 / 总数 {total_count or '?'}")

        if not replies:
            break
        if not logged_in and pn == 1 and total_count > len(roots) and len(roots) <= 3:
            login_limited = True
            warnings.append(
                "未登录或登录已失效：匿名接口只返回 3 条热评，分页拿不到。需要登录后重跑。"
            )
            break
        if len(roots) >= limit:
            break
        if total_count and pn * 20 >= total_count:
            break

    return {
        "roots": roots,
        "pinned": pinned,
        "warnings": warnings,
        "total_count": total_count,
        "pages": pages,
        "login_limited": login_limited,
        "backend": "x/v2/reply",
    }


def _collect_wbi(
    aid: int,
    cookies,
    referer: str,
    limit: int,
    sort: str,
    delay: float,
    logged_in: bool,
    progress,
) -> dict:
    if sort == "reply":
        raise ApiError("wbi/main 不支持按回复数排序")
    roots: OrderedDict[str, dict] = OrderedDict()
    pinned: OrderedDict[str, dict] = OrderedDict()
    warnings: list[str] = []
    total_count = 0
    pages = 0
    login_limited = False
    offset = ""
    mode = 2 if sort == "time" else 3

    for pn in range(1, 201):
        if pn > 1:
            time.sleep(delay + random.uniform(0.0, 0.4))
        params = {
            "type": 1,
            "oid": aid,
            "mode": mode,
            "pagination_str": json.dumps({"offset": offset}, separators=(",", ":")),
            "plat": 1,
            "web_location": 1315875,
        }
        doc = request_json(f"{API}/x/v2/reply/wbi/main", sign_wbi(params, cookies), cookies, referer)
        if doc.get("code") == -400 and pn == 1:
            params["pagination_str"] = ""
            doc = request_json(f"{API}/x/v2/reply/wbi/main", sign_wbi(params, cookies), cookies, referer)
        if doc.get("code") != 0:
            raise ApiError(f"读取评论失败：{doc.get('message')}", code=doc.get("code"))
        data = doc.get("data") or {}
        cursor = data.get("cursor") or {}
        total_count = max(total_count, int(cursor.get("all_count") or 0))
        replies = _as_list(data.get("replies"))
        for item in _as_list(data.get("top_replies")):
            rpid = str(item.get("rpid") or "")
            if rpid and rpid not in pinned:
                pinned[rpid] = item
        for item in _as_list(data.get("hots")):
            rpid = str(item.get("rpid") or "")
            if rpid and rpid not in roots:
                roots[rpid] = item
        for item in replies:
            rpid = str(item.get("rpid") or "")
            if rpid and rpid not in roots:
                roots[rpid] = item
        pages = pn
        if progress:
            progress(f"主评论第 {pn} 页：累计 {len(roots)} 条 / 总数 {total_count or '?'}")

        if not logged_in and pn == 1 and total_count > len(roots) and len(roots) <= 3:
            login_limited = True
            warnings.append(
                "未登录或登录已失效：匿名接口只返回 3 条热评，分页拿不到。需要登录后重跑。"
            )
            break
        if len(roots) >= limit:
            break
        pagination = cursor.get("pagination_reply") or {}
        offset = str(pagination.get("next_offset") or "")
        if cursor.get("is_end") or not offset:
            break

    return {
        "roots": roots,
        "pinned": pinned,
        "warnings": warnings,
        "total_count": total_count,
        "pages": pages,
        "login_limited": login_limited,
        "backend": "x/v2/reply/wbi/main",
    }


def _fetch_subs(
    aid: int,
    root_rpid: str,
    rcount: int,
    max_sub: int,
    budget: int,
    cookies,
    referer: str,
    delay: float,
) -> tuple[list[dict], bool, bool, bool]:
    target = min(rcount, max_sub, budget)
    output: OrderedDict[str, dict] = OrderedDict()
    pn = 1
    while len(output) < target:
        if pn > 1:
            time.sleep(delay + random.uniform(0.0, 0.3))
        doc = request_json(
            f"{API}/x/v2/reply/reply",
            {"type": 1, "oid": aid, "root": root_rpid, "pn": pn, "ps": 20},
            cookies,
            referer,
        )
        if doc.get("code") != 0:
            break
        data = doc.get("data") or {}
        replies = _as_list(data.get("replies"))
        for item in replies:
            rpid = str(item.get("rpid") or "")
            if rpid and rpid not in output:
                output[rpid] = item
        if not replies:
            break
        pn += 1
        if pn > 50:
            break
    subs = list(output.values())[:target]
    complete = len(output) >= rcount
    capped = max_sub < rcount and len(output) >= max_sub
    budget_hit = budget < rcount and len(output) >= budget
    return subs, complete, capped, budget_hit


def fetch_comments(
    target: str,
    cookies: dict[str, str] | None = None,
    limit: int = 100,
    max_sub: int = 100,
    max_sub_total: int = 600,
    sort: str = "hot",
    delay: float = 1.5,
    include_subs: bool = True,
    progress=None,
) -> dict:
    parsed = parse_target(target)
    info = video_info(parsed["url"], cookies)
    aid = int(info["aid"])
    bvid = str(info.get("bvid") or parsed.get("bvid") or "")
    up_mid = (info.get("owner") or {}).get("mid")
    selected_page = int(info.get("_selected_page") or parsed.get("page") or 1)
    referer = f"https://www.bilibili.com/video/{bvid}/?p={selected_page}" if bvid else parsed["url"]
    login = check_login(cookies)
    logged_in = bool(login)

    if progress:
        progress(f"评论接口：{'已登录 ' + str(login.get('uname')) if logged_in else '未登录（只会有 3 条热评）'}")

    collected = None
    error = None
    try:
        collected = _collect_old(aid, cookies, referer, limit, sort, delay, logged_in, progress)
    except ApiError as exc:
        error = exc
        if sort == "reply" or isinstance(exc, RiskControlError):
            raise
        if progress:
            progress(f"旧接口失败（{exc}），改用 wbi/main 重试")
    if collected is None:
        collected = _collect_wbi(aid, cookies, referer, limit, sort, delay, logged_in, progress)

    roots = collected["roots"]
    pinned_raw = collected["pinned"]
    warnings = list(collected["warnings"])
    up_mid_value = up_mid

    normalized_roots: list[dict] = []
    budget = max(0, max_sub_total)
    subs_fetched = 0
    subs_total = 0
    truncated_roots = 0

    root_items = list(roots.values())[: max(0, limit)]
    for index, raw in enumerate(root_items, 1):
        root = _normalize_reply(raw, up_mid_value)
        rcount = root["rcount"]
        if include_subs:
            subs_total += rcount
            if rcount > 0 and budget > 0:
                if progress:
                    progress(f"楼中楼 {index}/{len(root_items)}：rpid={root['rpid']}，{rcount} 条")
                subs, complete, capped, budget_hit = _fetch_subs(
                    aid, root["rpid"], rcount, max_sub, budget, cookies, referer, delay
                )
                normalized = [_normalize_reply(item, up_mid_value) for item in subs]
                normalized.sort(key=lambda item: item["ctime"])
                root["replies"] = normalized
                root["subs_fetched"] = len(normalized)
                root["subs_complete"] = complete
                root["subs_truncated"] = bool(capped or budget_hit)
                subs_fetched += len(normalized)
                budget -= len(normalized)
                if not complete:
                    truncated_roots += 1
            elif rcount > 0:
                root["subs_truncated"] = True
                truncated_roots += 1
        else:
            root["subs_truncated"] = True
        normalized_roots.append(root)

    pinned = []
    pinned_ids = set()
    for raw in pinned_raw.values():
        item = _normalize_reply(raw, up_mid_value)
        pinned.append(item)
        pinned_ids.add(item["rpid"])

    if len(normalized_roots) < collected["total_count"]:
        warnings.append(
            f"主评论未抓全：本次 {len(normalized_roots)} / 总数 {collected['total_count']}，可调大 --limit。"
        )
    if include_subs and subs_total > subs_fetched:
        warnings.append(
            f"楼中楼未抓全：本次 {subs_fetched} / 已抓主评论下的 {subs_total}，可调大 --max-sub / --max-sub-total。"
        )
    if not include_subs:
        warnings.append("已按 --no-subs 跳过楼中楼。")
    if collected["login_limited"]:
        warnings.append("LOGIN_REQUIRED")

    result = {
        "meta": {
            "bvid": bvid,
            "aid": aid,
            "title": str(info.get("title") or ""),
            "up": str((info.get("owner") or {}).get("name") or ""),
            "up_mid": str(up_mid_value or ""),
            "page": selected_page,
            "url": f"https://www.bilibili.com/video/{bvid}/" if bvid else parsed["url"],
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sort": SORT_LABEL.get(sort, sort),
            "backend": collected["backend"],
        },
        "login": {
            "logged_in": logged_in,
            "uname": str(login.get("uname") or "") if login else "",
            "mid": str(login.get("mid") or "") if login else "",
        },
        "stats": {
            "total_count": collected["total_count"],
            "roots_fetched": len(normalized_roots),
            "subs_fetched": subs_fetched,
            "subs_total_in_fetched_roots": subs_total,
            "truncated_roots": truncated_roots,
            "pages_fetched": collected["pages"],
            "login_limited": collected["login_limited"],
        },
        "warnings": warnings,
        "pinned": pinned,
        "roots": normalized_roots,
    }
    return result


def _quote(text: str) -> str:
    lines = (text or "").splitlines() or [""]
    return "\n".join("> " + line for line in lines)


def render_markdown(result: dict) -> str:
    meta = result["meta"]
    stats = result["stats"]
    login = result["login"]
    lines = [
        f"# 评论区：{meta['title']}",
        "",
        f"- 视频：{meta['url']}" + (f"?p={meta['page']}" if meta.get("page", 1) > 1 else ""),
        f"- BV / av：{meta['bvid'] or meta['aid']}",
        f"- UP：{meta['up']}",
        f"- 评论总数：{stats['total_count']}",
        f"- 本次抓取：主评论 {stats['roots_fetched']} 条，楼中楼 {stats['subs_fetched']} 条",
        f"- 排序：{meta['sort']}；接口：{meta['backend']}",
        f"- 登录：{login['uname'] or '未登录'}",
        f"- 抓取时间：{meta['fetched_at']}",
    ]
    if result["warnings"]:
        lines.append("")
        for warning in result["warnings"]:
            if warning == "LOGIN_REQUIRED":
                lines.append("> [!WARNING]")
                lines.append("> 未登录只能拿到 3 条热评。请先登录后重跑，才能拿到完整分页与楼中楼。")
            else:
                lines.append(f"> [!NOTE] {warning}")

    if result["pinned"]:
        lines += ["", "## 置顶评论", ""]
        for item in result["pinned"]:
            lines += [
                f"### 置顶 · {item['uname']}" + ("（UP主）" if item["is_up"] else ""),
                f"{item['likes']} 赞 · {item['time']}" + (f" · {item['location']}" if item["location"] else ""),
                "",
                _quote(item["message"] + ("\n[含图片]" if item["has_image"] else "")),
                "",
            ]

    pinned_ids = {item["rpid"] for item in result["pinned"]}
    lines += ["", "## 评论", ""]
    for index, root in enumerate(result["roots"], 1):
        if root["rpid"] in pinned_ids:
            continue
        tags = []
        if root["is_up"]:
            tags.append("UP主")
        if root["location"]:
            tags.append(root["location"])
        suffix = f" · {' · '.join(tags)}" if tags else ""
        lines += [
            f"### {index}. {root['uname']}{suffix}",
            f"{root['likes']} 赞 · {root['time']} · rpid={root['rpid']}"
            + (f" · 回复 {root['rcount']}" if root["rcount"] else ""),
            "",
            _quote(root["message"] + ("\n[含图片]" if root["has_image"] else "")),
            "",
        ]
        if root["replies"]:
            status = ""
            if root["subs_fetched"] < root["rcount"]:
                status = f"（已抓 {root['subs_fetched']}/{root['rcount']}"
                status += "，未抓全）" if root["subs_truncated"] else "）"
            lines.append(f"楼中楼{status}：")
            lines.append("")
            for sub in root["replies"]:
                sub_tags = []
                if sub["is_up"]:
                    sub_tags.append("UP主")
                if sub["location"]:
                    sub_tags.append(sub["location"])
                sub_suffix = f" · {' · '.join(sub_tags)}" if sub_tags else ""
                lines.append(
                    f"- **{sub['uname']}**{sub_suffix} · {sub['likes']} 赞 · {sub['time']}："
                    f"{sub['message']}" + (" [图片]" if sub["has_image"] else "")
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_comments(result: dict, out_dir: str | Path) -> dict:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "comments.json"
    md_path = directory / "comments.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
