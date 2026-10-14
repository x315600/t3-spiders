# -*- coding: utf-8 -*-
# 看剧AI (kanju1.ai) T3 spider
# 站点：AI 对话式影视发现，Vue SPA
# API：HMAC-SHA256 签名，key 硬编码于前端 bundle
#   msg = f"{METHOD}\n{path}{query}\n{timestamp_ms}\n{nonce_hex32}"
# 播放链（2026-10-13 实测打通）：
#   /v1/catalog/{variant_id}/episodes -> episodes[i].token（单集 token，
#     注意不是 variant_id！variant_id 调 resolve 报 invalid_public_playback_id）
#   -> GET /v1/playback/resolve/{episode_token}?view=compact
#     -> line_options[]（id/label/url=resolve://{ticket}/selected）
#   -> POST /v1/playback/resolve-line {"ticket": ticket} -> 真实地址(字节跳动TOS签名URL)

import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import urllib.request
import urllib.error
import http.cookiejar

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider:  # 桌面自测桩
        def getDependence(self): return []
        def init(self, extend=""): pass
        def destroy(self): pass

# API 域名三级自愈：kanju48.com 为真实内容站，kanju1.ai 只是门户（2026-10-13 沙盒直连 kanju1.ai 曾被拒）
API_HOSTS = ["https://kanju48.com", "https://kanju1.ai"]
API_BASE = API_HOSTS[0]
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
        return any(url.split("?")[0].endswith(x) for x in (
            ".m3u8", ".mp4", ".mkv", ".flv", ".ts"))

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
        for host in API_HOSTS:
            for _ in range(retries + 1):
                try:
                    headers = self._sign_headers(method, path_qs)
                    data = json.dumps(body).encode("utf-8") if body is not None else None
                    if data:
                        headers["Content-Type"] = "application/json"
                    req = urllib.request.Request(host + path_qs, data=data, headers=headers, method=method)
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
                        return self._api_fallback_4xx(host, method, path_qs, body, e.code, err_body)
                    time.sleep(1)
                except Exception as e:
                    # 连接层失败（DNS/隧道被拒等）：换下一个 host
                    last_err = f"{host} {str(e)[:100]}"
                    break
        raise RuntimeError(f"API {method} {path_qs} 失败: {last_err}")

    def _api_fallback_4xx(self, host, method, path_qs, body, code, err_body):
        # 4xx 说明签名/host 组合不对：换下一个 host 重试一次，仍 4xx 则直接抛
        idx = API_HOSTS.index(host)
        if idx + 1 < len(API_HOSTS):
            try:
                headers = self._sign_headers(method, path_qs)
                data = json.dumps(body).encode("utf-8") if body is not None else None
                if data:
                    headers["Content-Type"] = "application/json"
                req = urllib.request.Request(API_HOSTS[idx + 1] + path_qs, data=data,
                                             headers=headers, method=method)
                with self._opener.open(req, timeout=25) as r:
                    raw = r.read()
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                pass
        raise RuntimeError(f"API {method} {path_qs} 失败: HTTP {code} {err_body[:120]}")

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
        vod = {"vod_id": vid, "vod_name": "", "vod_pic": "",
               "vod_year": "", "vod_area": "", "vod_remarks": "",
               "vod_actor": "", "vod_director": "", "vod_content": "",
               "vod_play_from": "", "vod_play_url": ""}
        try:
            q = urllib.parse.quote(vid, safe="")
            # 详情直调（不再依赖 catalog 第 1 页匹配，搜索结果也能出详情）
            d = self._api("GET", f"/v1/catalog/{q}/detail")
            vod.update({
                "vod_name": d.get("title") or d.get("normalized_title") or "",
                "vod_pic": d.get("poster_url") or "",
                "vod_year": str(d.get("year") or ""),
                "vod_area": d.get("area") or "",
                "vod_remarks": d.get("remarks") or "",
                "vod_actor": " ".join(d.get("actors") or []),
                "vod_director": " ".join(d.get("directors") or []),
                "vod_content": (d.get("description") or "").strip(),
            })
            total = int(d.get("episode_count") or d.get("total_episode_count") or 0)
            # 标题 quote 后塞进 play_id：vid 几分钟就过期，播放时凭标题可重搜拿新 vid 自救
            tq = urllib.parse.quote(vod.get("vod_name") or "", safe="")
            # 用第 1 集的单集 token 反解出真实线路列表
            lines = []
            try:
                e = self._api("GET", f"/v1/catalog/{q}/episodes?limit=1")
                eps = e.get("episodes") or []
                total = int((e.get("episode_pagination") or {}).get("total_count")
                            or total or len(eps))
                if eps and eps[0].get("token"):
                    r = self._api("GET", "/v1/playback/resolve/" +
                                  urllib.parse.quote(eps[0]["token"], safe="") + "?view=compact")
                    lines = r.get("line_options") or []
            except Exception:
                lines = []
            ep_n = min(max(total, 1), 500)
            play_from, play_url = [], []
            if lines:
                for opt in lines:
                    lid = str(opt.get("id") or opt.get("play_from") or "")
                    label = (str(opt.get("label") or lid or "线路")).replace("$", "＄").replace("#", "＃")
                    eps_list = [f"第{i + 1}集${vid}@@{lid}@@{i}@@{tq}" for i in range(ep_n)]
                    play_from.append(label)
                    play_url.append("#".join(eps_list))
            else:
                # resolve 失败也先列出分集，播放时再试
                eps_list = [f"第{i + 1}集${vid}@@@@{i}@@{tq}" for i in range(ep_n)]
                play_from.append("默认线路")
                play_url.append("#".join(eps_list))
            vod["vod_play_from"] = "$$$".join(play_from)
            vod["vod_play_url"] = "$$$".join(play_url)
        except Exception:
            pass
        return {"list": [vod]}

    def _try_resolve(self, vid, line_id, ep_idx):
        """真实播放链：单集 token -> resolve -> ticket -> resolve-line -> 直链。失败回空串。"""
        try:
            q = urllib.parse.quote(vid, safe="")
            base = (ep_idx // 200) * 200
            e = self._api("GET", f"/v1/catalog/{q}/episodes?limit=200&offset={base}")
            eps = e.get("episodes") or []
            li = ep_idx - base
            if li < 0 or li >= len(eps):
                return ""
            ep_token = eps[li].get("token") or ""
            if not ep_token:
                return ""
            r = self._api("GET", "/v1/playback/resolve/" +
                          urllib.parse.quote(ep_token, safe="") + "?view=compact")
            opts = r.get("line_options") or []
            if not opts:
                return ""
            pick = None
            for o in opts:
                if line_id and line_id in (str(o.get("id") or ""),
                                           str(o.get("play_from") or ""),
                                           str(o.get("label") or "")):
                    pick = o
                    break
            if pick is None:
                pick = next((o for o in opts if o.get("selected")), opts[0])
            url = str(pick.get("url") or "")
            ticket = url[len("resolve://"):] if url.startswith("resolve://") else url
            if not ticket:
                return ""
            r2 = self._api("POST", "/v1/playback/resolve-line?view=compact", {"ticket": ticket})
            return (((r2.get("a") or {}).get("line") or {}).get("url")
                    or (r2.get("line") or {}).get("url")
                    or r2.get("url") or "")
        except Exception:
            return ""

    def playerContent(self, flag, id, vipFlags=None):
        # id 格式：vid@@line_id@@episode_index@@title(quote)，title 供 vid 过期时自救
        try:
            parts = str(id).rsplit("$", 1)[-1].split("@@")
            vid = parts[0] if len(parts) > 0 else ""
            line_id = parts[1] if len(parts) > 1 else ""
            ep_idx = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
            title = urllib.parse.unquote(parts[3]) if len(parts) > 3 else ""
        except Exception:
            return {"parse": 0, "msg": "播放参数解析失败"}
        if not vid:
            return {"parse": 0, "msg": "缺少 vid"}

        real = self._try_resolve(vid, line_id, ep_idx)
        if not real and title:
            # vid 疑似过期（variant id 几分钟就轮换）：用标题重搜拿新 vid 再试一次
            try:
                for s in self.searchContent(title, "", "1").get("list", []):
                    nvid = s.get("vod_id") or ""
                    if nvid and nvid != vid:
                        real = self._try_resolve(nvid, line_id, ep_idx)
                        if real:
                            break
            except Exception:
                pass
        if real:
            return {"parse": 0, "url": real,
                    "header": {"User-Agent": PLAYER_UA, "Referer": API_BASE + "/"}}

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
