# -*- coding: utf-8 -*-
# 看剧AI (kanju1.ai) T3 spider
# 站点：AI 对话式影视发现，Vue SPA
# API：HMAC-SHA256 签名，key 硬编码于前端 bundle
#   msg = f"{METHOD}\n{path}{query}\n{timestamp_ms}\n{nonce_hex32}"
# 播放链：/v1/playback/resolve/{token} -> /v1/playback/resolve-line -> 真实地址(字节跳动TOS签名URL)

import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import urllib.request
import http.cookiejar

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider:  # 桌面自测桩
        def getDependence(self): return []
        def init(self, extend=""): pass
        def destroy(self): pass

API_BASE = "https://kanju1.ai"
SIGN_KEY = "557d0e4ae929f438da6bd84412374e6086b8af09b3fed54bf22601d5bf8c54a0"

CLIENT_HEADERS = {
    "x-ai-movie-client-name": "dianyingtiantang-frontend",
    "x-ai-movie-client-version": "1.0.0",
    "x-ai-movie-build-version": "dianyingtiantang-v2026.10.10.2-download-source-entry-aa4983cf2052-web",
    "x-ai-movie-protocol-version": "2026-07-05.library-v2.playback-v1",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# 播放器 UA：真机浏览器，覆盖 Media3 默认 UA
PLAYER_UA = ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

# 分类 intent 映射（/v1/browse/catalog 的 intent 参数）
# 实测只有 latest_catalog 有效，其他分类走 content_kind 客户端过滤
CATALOG_INTENTS = {
    "latest": "latest_catalog",   # 最新
    "movie": "latest_catalog",     # 电影（按 content_kind 过滤）
    "series": "latest_catalog",    # 电视剧（按 content_kind 过滤）
    "anime": "latest_catalog",     # 动漫（按 content_kind 过滤）
    "variety": "latest_catalog",   # 综艺（按 content_kind 过滤）
    "short": "latest_catalog",     # 短剧（按 content_kind 过滤）
}
# tid -> content_kind 过滤值
KIND_FILTER = {
    "movie": ("movie",),
    "series": ("series", "tv"),
    "anime": ("anime", "dongman"),
    "variety": ("variety", "zongyi"),
    "short": ("short_drama", "short"),
}


class Spider(_BaseSpider):
    def getName(self):
        return "看剧AI"

    def isVideoFormat(self, url):
        url = (url or "").lower()
        return any(url.split("?")[0].endswith(x) for x in (".m3u8", ".mp4", ".mkv", ".flv", ".ts"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def init(self, extend=""):
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cj))
        # 匿名会话（游客即可播放）
        try:
            self._api("POST", "/v1/users/anonymous", {})
        except Exception:
            pass

    # ---------- 签名 API ----------
    def _sign_headers(self, method, path_qs):
        ts = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        msg = f"{method}\n{path_qs}\n{ts}\n{nonce}"
        sig = hmac.new(SIGN_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
        h = dict(CLIENT_HEADERS)
        h.update({
            "x-ai-movie-timestamp": ts,
            "x-ai-movie-nonce": nonce,
            "x-ai-movie-signature": sig,
            "Accept": "application/json",
            "User-Agent": UA,
        })
        return h

    def _api(self, method, path_qs, body=None, timeout=25, retries=2):
        last_err = None
        for _ in range(retries + 1):
            try:
                headers = self._sign_headers(method, path_qs)
                data = json.dumps(body).encode("utf-8") if body is not None else None
                if data:
                    headers["Content-Type"] = "application/json"
                req = urllib.request.Request(API_BASE + path_qs, data=data, headers=headers, method=method)
                with self._opener.open(req, timeout=timeout) as r:
                    raw = r.read()
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as e:
                try:
                    err_body = e.read()[:500].decode("utf-8", "ignore")
                except Exception:
                    err_body = ""
                last_err = f"HTTP {e.code} {err_body[:120]}"
                if e.code in (400, 401, 402, 403):
                    break
                time.sleep(1)
            except Exception as e:
                last_err = str(e)[:120]
                time.sleep(1)
        raise RuntimeError(f"API {method} {path_qs} 失败: {last_err}")

    # ---------- 卡片 ----------
    def _card(self, c):
        title = c.get("title") or c.get("normalized_title") or ""
        remark = c.get("remark") or ""
        if remark:
            title = f"{title} [{remark}]"
        return {
            "vod_id": c.get("id", ""),
            "vod_name": title,
            "vod_pic": c.get("poster_url") or "",
            "vod_remarks": f"{c.get('year', '')} {c.get('area', '')}".strip(),
            "vod_year": str(c.get("year") or ""),
            "vod_area": c.get("area") or "",
            "type_name": "|".join(c.get("genres") or []),
        }

    # ---------- 六接口 ----------
    def homeContent(self, isFilter, filter):
        classes = [
            {"type_id": "latest", "type_name": "最新"},
            {"type_id": "movie", "type_name": "电影"},
            {"type_id": "series", "type_name": "电视剧"},
            {"type_id": "anime", "type_name": "动漫"},
            {"type_id": "variety", "type_name": "综艺"},
            {"type_id": "short", "type_name": "短剧"},
        ]
        return {"class": classes}

    def homeVideoContent(self):
        try:
            data = self._api("GET", "/v1/browse/catalog?" + urllib.parse.urlencode(
                {"page": 1, "limit": 20, "intent": "latest_catalog"}))
            vods = [self._card(c) for c in data.get("cards", [])]
            return {"list": vods}
        except Exception:
            return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        intent = CATALOG_INTENTS.get(tid, "latest_catalog")
        kinds = KIND_FILTER.get(tid)
        try:
            # 多拉几页做客户端过滤（API 不支持按类型过滤）
            vods = []
            fetch_pages = 3 if kinds else 1
            for p in range(pg, pg + fetch_pages):
                data = self._api("GET", "/v1/browse/catalog?" + urllib.parse.urlencode(
                    {"page": p, "limit": 20, "intent": intent}))
                for c in data.get("cards", []):
                    if kinds and (c.get("content_kind") or "") not in kinds:
                        continue
                    vods.append(self._card(c))
                if not kinds:
                    pagination = data.get("pagination", {}) or {}
                    pagecount = int(pagination.get("total_pages") or 1)
                    total = int(pagination.get("total") or len(vods))
                    return {"list": vods, "page": pg, "pagecount": max(pagecount, 1),
                            "limit": 20, "total": total}
            return {"list": vods[:20], "page": pg, "pagecount": 99,
                    "limit": 20, "total": 99 * 20}
        except Exception:
            return {"list": [], "page": pg, "pagecount": 1, "limit": 20, "total": 0}

    def searchContent(self, key, quick, pg="1"):
        try:
            qs = urllib.parse.urlencode({"q": key, "limit": 20, "mode": "home", "scope": "public"})
            data = self._api("GET", "/v1/suggest?" + qs)
            vods = []
            for s in data.get("suggestions", []):
                target = s.get("target") or {}
                vid = target.get("variant_id") or ""
                title = target.get("title") or s.get("label") or ""
                if not vid or not title:
                    continue
                vods.append({
                    "vod_id": vid,
                    "vod_name": title,
                    "vod_pic": "",
                    "vod_remarks": s.get("subtitle") or "",
                })
            return {"list": vods, "page": 1, "pagecount": 1, "limit": 20, "total": len(vods)}
        except Exception:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}

    def detailContent(self, ids):
        vid = ids[0] if ids else ""
        # 先从 catalog 按 id 精确找（suggest 的 variant_id 与 catalog 卡片 id 同格式）
        vod = {"vod_id": vid, "vod_name": "", "vod_pic": "",
               "vod_year": "", "vod_area": "", "vod_remarks": "",
               "vod_actor": "", "vod_director": "", "vod_content": "",
               "vod_play_from": "", "vod_play_url": ""}
        try:
            # 用 suggest 反查标题（variant_id -> title）
            # catalog 里按页找太慢，直接用已知的 vid 拼详情
            data = self._api("GET", "/v1/browse/catalog?" + urllib.parse.urlencode(
                {"page": 1, "limit": 20, "intent": "latest_catalog"}))
            card = None
            for c in data.get("cards", []):
                if c.get("id") == vid:
                    card = c
                    break
            if card:
                vod.update({
                    "vod_name": card.get("title") or "",
                    "vod_pic": card.get("poster_url") or "",
                    "vod_year": str(card.get("year") or ""),
                    "vod_area": card.get("area") or "",
                    "vod_remarks": card.get("remark") or "",
                    "vod_actor": " ".join(card.get("actors") or []),
                    "vod_director": " ".join(card.get("directors") or []),
                    "vod_content": (card.get("overview") or card.get("description") or "").strip(),
                })
                groups = card.get("playback_groups") or []
                play_from, play_url = [], []
                for g in groups:
                    label = (g.get("label") or g.get("id") or "线路").replace("$", "＄").replace("#", "＃")
                    ep_count = int(g.get("episode_count") or 1)
                    # catalog 未返回 episodes 明细，按集数生成占位分集
                    eps = [f"第{i + 1}集${vid}@@{g.get('id', '')}@@{i}" for i in range(min(ep_count, 200))]
                    if eps:
                        play_from.append(label)
                        play_url.append("#".join(eps))
                vod["vod_play_from"] = "$$$".join(play_from)
                vod["vod_play_url"] = "$$$".join(play_url)
        except Exception:
            pass
        return {"list": [vod]}

    def playerContent(self, flag, id, vipFlags=None):
        # id 格式：vid@@line_id@@episode_index（壳已切掉集名，只剩 play_id）
        try:
            parts = str(id).rsplit("$", 1)[-1].split("@@")
            vid = parts[0] if len(parts) > 0 else ""
            line_id = parts[1] if len(parts) > 1 else ""
            ep_idx = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
        except Exception:
            return {"parse": 0, "msg": "播放参数解析失败"}

        # 尝试 resolve 链拿真实地址
        try:
            token = urllib.parse.quote(vid, safe="")
            params = urllib.parse.urlencode({
                "view": "compact", "line": line_id, "episode_index": ep_idx,
            })
            r = self._api("GET", f"/v1/playback/resolve/{token}?" + params)
            line_options = r.get("line_options") or []
            ticket = ""
            for opt in line_options:
                if not line_id or opt.get("id") == line_id or opt.get("line") == line_id:
                    ticket = opt.get("ticket") or opt.get("resolve_ticket") or ""
                    if ticket:
                        break
            if not ticket and line_options:
                ticket = line_options[0].get("ticket") or ""
            if ticket:
                r2 = self._api("POST", "/v1/playback/resolve-line?view=compact", {"ticket": ticket})
                url = (((r2.get("a") or {}).get("line") or {}).get("url")
                       or (r2.get("line") or {}).get("url") or "")
                if url:
                    return {"parse": 0, "url": url,
                            "header": {"User-Agent": PLAYER_UA, "Referer": API_BASE + "/"}}
        except Exception:
            pass

        # 降级：交壳解析播放页
        proxy = None
        try:
            if callable(getattr(self, "getProxyUrl", None)):
                proxy = self.getProxyUrl()
        except Exception:
            proxy = None
        play_page = f"{API_BASE}/player/{urllib.parse.quote(vid, safe='')}"
        return {"parse": 1, "url": play_page,
                "header": {"User-Agent": PLAYER_UA}}

    def localProxy(self, param=None):
        return [200, "text/plain", b"", {}]
