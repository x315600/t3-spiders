# -*- coding: utf-8 -*-
# 站点：看剧AI（kanju1.ai 门户 / kanju48.com 内容站）  profile：legacy（老壳）
# 接口：自研 /v1 API，HMAC-SHA256 签名，key 硬编码于前端 bundle
#   msg = f"{METHOD}\n{path}{query}\n{timestamp_ms}\n{nonce_hex32}"
#   匿名会话 POST /v1/users/anonymous（游客即可播放；实测不调也能拉列表/搜索，
#   但调一次会下发 ai_movie_session cookie，保留原逻辑）
# 要点（2026-11-10 沙盒实测）：
#   ① 列表 GET /v1/browse/catalog?page&limit=20&intent=latest_catalog&kind=
#      kind 实测生效：movie/series/anime/variety/short_drama；分页真值
#      pagination.total=1000 → pagecount=50；第 2 页与第 1 页零重叠。
#      卡片字段是 remarks（复数），不是 remark；poster 直连百度 gimg，无防盗链。
#   ② 搜索 GET /v1/suggest?q&limit=20&mode=home&scope=public，无分页参数，
#      suggestions[].target.variant_id 即详情 id；搜索结果无封面图。
#   ③ 详情 GET /v1/catalog/{vid}/detail；集数取 episodes 接口的
#      episode_pagination.total_count（可播集数，如 10）而非 episode_count（计划总集数 25）。
#   ④ 播放链：/v1/catalog/{vid}/episodes?limit&offset -> episodes[i].token
#      -> GET /v1/playback/resolve/{token}?view=compact -> line_options[]
#      2026-11-10 实测：line_options[].url 已是 m3u8 直链
#      （resolve_required=false, resolved=true, url_kind=m3u8, resolve_mode=direct），
#      GET 直链 200 #EXTM3U；旧 resolve://{ticket} + POST resolve-line 会 400，
#      只保留作兜底（url 以 resolve:// 开头或 resolve_required 时才走 resolve-line）。
#      variant_id（vid）几分钟就过期，播放失败时凭标题重搜拿新 vid 自救（保留原逻辑）。
#   ⑤ 网络：urllib 在此站上频繁 IncompleteRead（截断，约三四成），
#      requests/curl 12/12 全通，故主走 requests.Session，缺 requests 时才 urllib 兜底。
#   ⑥ 无实测生效的筛选维度（kind 即分类本身），homeContent 不下发 filters。
import sys
import json
import time
import hashlib
import hmac
import secrets
from urllib.parse import quote, urlencode, parse_qsl, unquote

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:  # 无 base.spider 的壳 / 本地调试用最小桩
    class BaseSpider(object):
        def getProxyUrl(self, local=True):
            return 'http://127.0.0.1:9978/proxy?do=py'

        def fetch(self, url, params=None, headers=None, timeout=10, **kw):
            import requests
            r = requests.get(url, params=params, headers=headers, timeout=timeout, **kw)
            r.encoding = 'utf-8'
            return r

        def post(self, url, params=None, data=None, json=None, headers=None, timeout=10, **kw):
            import requests
            r = requests.post(url, params=params, data=data, json=json, headers=headers, timeout=timeout, **kw)
            r.encoding = 'utf-8'
            return r

try:
    import requests
except Exception:
    requests = None  # OK 壳可能没有 requests → urllib 兜底

# ---------- CONFIG ----------
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
PAGE_SIZE = 20
TIMEOUT = 15
RETRIES = 2

# 分类 tid -> 服务端 kind 参数（API 原生支持 kind 过滤，实测 5 种全有效）
KIND_PARAM = {
    "movie": "movie",
    "series": "series",
    "anime": "anime",
    "variety": "variety",
    "short": "short_drama",
}
CLASSES = [
    {"type_id": "latest", "type_name": "最新"},
    {"type_id": "movie", "type_name": "电影"},
    {"type_id": "series", "type_name": "电视剧"},
    {"type_id": "anime", "type_name": "动漫"},
    {"type_id": "variety", "type_name": "综艺"},
    {"type_id": "short", "type_name": "短剧"},
]
VIDEO_SUFFIXES = (".m3u8", ".mp4", ".mkv", ".flv", ".ts")


class Spider(BaseSpider):
    def getName(self):
        return "看剧AI"

    def isVideoFormat(self, url):
        url = (url or "").lower()
        return any(url.split("?")[0].endswith(x) for x in VIDEO_SUFFIXES)

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def init(self, extend=""):
        if requests is not None:
            self._sess = requests.Session()
            self._sess.headers.update({"User-Agent": UA})
            self._opener = None
        else:
            import urllib.request
            import http.cookiejar
            self._sess = None
            self._cj = http.cookiejar.CookieJar()
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self._cj))
        # 匿名会话（游客即可播放；失败不阻塞）
        try:
            self._api("POST", "/v1/users/anonymous", {})
        except Exception:
            pass

    # ---------- 签名 API ----------
    def _sign_headers(self, method, path_qs):
        ts = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        msg = "{0}\n{1}\n{2}\n{3}".format(method, path_qs, ts, nonce)
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

    def _api(self, method, path_qs, body=None, timeout=TIMEOUT, retries=RETRIES):
        """签名请求：逐 host 试，每 host 有限次重试；4xx 换 host，全败才抛。"""
        last_err = "no host tried"
        for host in API_HOSTS:
            for attempt in range(retries + 1):
                try:
                    headers = self._sign_headers(method, path_qs)
                    data = None
                    if body is not None:
                        data = json.dumps(body).encode("utf-8")
                        headers["Content-Type"] = "application/json"
                    if self._sess is not None:
                        r = self._sess.request(method, host + path_qs,
                                               data=data, headers=headers,
                                               timeout=timeout)
                        if r.status_code == 200:
                            return r.json() if r.content else {}
                        last_err = "HTTP {0} {1}".format(
                            r.status_code, r.text[:120])
                        if 400 <= r.status_code < 500:
                            break  # 4xx 换 host
                    else:
                        raw = self._urllib_request(method, host + path_qs,
                                                   headers, data, timeout)
                        return json.loads(raw.decode("utf-8")) if raw else {}
                except Exception as e:
                    last_err = "{0} {1}".format(host, str(e)[:100])
                    if attempt < retries:
                        time.sleep(0.5)
        raise RuntimeError("API {0} {1} 失败: {2}".format(method, path_qs, last_err))

    def _urllib_request(self, method, url, headers, data, timeout):
        """无 requests 时的兜底（urllib 在此站上偶发 IncompleteRead，仅作降级）。"""
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError("HTTP {0}".format(e.code))

    # ---------- 卡片 ----------
    @staticmethod
    def _card(c):
        remarks = c.get("remarks") or ""
        year = c.get("year") or ""
        area = c.get("area") or ""
        if not remarks:
            remarks = "{0} {1}".format(year, area).strip()
        return {
            "vod_id": c.get("id", ""),
            "vod_name": c.get("title") or c.get("normalized_title") or "",
            "vod_pic": c.get("poster_url") or "",
            "vod_remarks": remarks,
            "vod_year": str(year),
            "vod_area": area,
            "type_name": "|".join(c.get("genres") or []),
        }

    def _catalog(self, pg, kind=None):
        params = {"page": pg, "limit": PAGE_SIZE, "intent": "latest_catalog"}
        if kind:
            params["kind"] = kind
        data = self._api("GET", "/v1/browse/catalog?" + urlencode(params))
        vods = [self._card(c) for c in data.get("cards", [])]
        pagination = data.get("pagination", {}) or {}
        total = pagination.get("total")
        if total is None:
            total = len(vods)
            pagecount = pg + 1 if len(vods) >= PAGE_SIZE else pg
        else:
            total = int(total)
            pagecount = max(1, -(-total // PAGE_SIZE))
        return {"list": vods, "page": pg, "pagecount": pagecount,
                "limit": PAGE_SIZE, "total": total}

    # ---------- 六入口 ----------
    def homeContent(self, filter=False):
        return {"class": list(CLASSES)}

    def homeVideoContent(self):
        try:
            return {"list": self._catalog(1).get("list", [])}
        except Exception:
            return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        if pg < 1:
            pg = 1
        try:
            return self._catalog(pg, KIND_PARAM.get(tid))
        except Exception:
            return {"list": [], "page": pg, "pagecount": 1,
                    "limit": PAGE_SIZE, "total": 0}

    def searchContent(self, key, quick, pg="1"):
        try:
            qs = urlencode({"q": key, "limit": PAGE_SIZE,
                            "mode": "home", "scope": "public"})
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
            return {"list": vods, "page": 1, "pagecount": 1,
                    "limit": PAGE_SIZE, "total": len(vods)}
        except Exception:
            return {"list": [], "page": 1, "pagecount": 1,
                    "limit": PAGE_SIZE, "total": 0}

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    def detailContent(self, ids):
        items = []
        for vid in (ids or []):
            items.append(self._detail_one(vid))
        return {"list": items}

    def _detail_one(self, vid):
        vod = {"vod_id": vid, "vod_name": "", "vod_pic": "",
               "vod_year": "", "vod_area": "", "vod_remarks": "",
               "vod_actor": "", "vod_director": "", "vod_content": "",
               "vod_play_from": "", "vod_play_url": ""}
        try:
            q = quote(vid, safe="")
            d = self._api("GET", "/v1/catalog/{0}/detail".format(q))
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
            # 用第 1 集的单集 token 反解出真实线路列表
            lines = []
            try:
                e = self._api("GET", "/v1/catalog/{0}/episodes?limit=1".format(q))
                eps = e.get("episodes") or []
                total = int((e.get("episode_pagination") or {}).get("total_count")
                            or total or len(eps))
                if eps and eps[0].get("token"):
                    r = self._api("GET", "/v1/playback/resolve/" +
                                  quote(eps[0]["token"], safe="") + "?view=compact")
                    lines = r.get("line_options") or []
            except Exception:
                lines = []
            ep_n = min(max(total, 1), 500)
            # 标题 quote 后塞进 play_id：vid 几分钟就过期，播放时凭标题可重搜拿新 vid 自救
            tq = quote(vod.get("vod_name") or "", safe="")
            play_from, play_url = [], []
            if lines:
                for opt in lines:
                    lid = str(opt.get("play_from") or opt.get("id") or
                              opt.get("label") or "")
                    label = (str(opt.get("label") or lid or "线路")
                             ).replace("$", "＄").replace("#", "＃")
                    eps_list = ["第{0}集${1}@@{2}@@{3}@@{4}".format(
                        i + 1, vid, lid, i, tq) for i in range(ep_n)]
                    play_from.append(label)
                    play_url.append("#".join(eps_list))
            else:
                # resolve 失败也先列出分集，播放时再试
                eps_list = ["第{0}集${1}@@@@{2}@@{3}".format(
                    i + 1, vid, i, tq) for i in range(ep_n)]
                play_from.append("默认线路")
                play_url.append("#".join(eps_list))
            vod["vod_play_from"] = "$$$".join(play_from)
            vod["vod_play_url"] = "$$$".join(play_url)
        except Exception:
            pass
        return vod

    # ---------- 播放 ----------
    def _line_url(self, opt):
        """单线路取真实地址：直链直接回；resolve:// 或需二次解析才走 resolve-line。"""
        url = str(opt.get("url") or "")
        if not url:
            return ""
        if self.isVideoFormat(url):
            return url
        if url.startswith("resolve://"):
            ticket = url[len("resolve://"):]
            if ticket.endswith("/selected"):
                ticket = ticket[:-len("/selected")]
            try:
                r2 = self._api("POST", "/v1/playback/resolve-line?view=compact",
                               {"ticket": ticket})
                return (((r2.get("a") or {}).get("line") or {}).get("url")
                        or (r2.get("line") or {}).get("url")
                        or r2.get("url") or "")
            except Exception:
                return ""
        if opt.get("resolve_required"):
            try:
                r2 = self._api("POST", "/v1/playback/resolve-line?view=compact",
                               {"ticket": url})
                return (((r2.get("a") or {}).get("line") or {}).get("url")
                        or (r2.get("line") or {}).get("url")
                        or r2.get("url") or "")
            except Exception:
                return ""
        return url

    def _try_resolve(self, vid, line_id, ep_idx):
        """真实播放链：单集 token -> resolve -> 逐线取直链（先指定线路，再试其余线）。"""
        try:
            q = quote(vid, safe="")
            base = (ep_idx // 200) * 200
            e = self._api("GET", "/v1/catalog/{0}/episodes?limit=200&offset={1}".format(q, base))
            eps = e.get("episodes") or []
            li = ep_idx - base
            if li < 0 or li >= len(eps):
                return ""
            ep_token = eps[li].get("token") or ""
            if not ep_token:
                return ""
            r = self._api("GET", "/v1/playback/resolve/" +
                          quote(ep_token, safe="") + "?view=compact")
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
            ordered = [pick] + [o for o in opts if o is not pick]
            for o in ordered:
                real = self._line_url(o)
                if real:
                    return real
            return ""
        except Exception:
            return ""

    def playerContent(self, flag, id, vipFlags=None):
        # id 格式：vid@@line_id@@episode_index@@title(quote)，title 供 vid 过期时自救
        try:
            parts = str(id).rsplit("$", 1)[-1].split("@@")
            vid = parts[0] if len(parts) > 0 else ""
            line_id = parts[1] if len(parts) > 1 else ""
            ep_idx = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
            title = unquote(parts[3]) if len(parts) > 3 else ""
        except Exception:
            return {"parse": 0, "url": "", "header": {}}
        if not vid:
            return {"parse": 0, "url": "", "header": {}}

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
            header = {"User-Agent": PLAYER_UA, "Referer": API_BASE + "/"}
            if self.isVideoFormat(real):
                return {"parse": 0, "url": real, "header": header}
            return {"parse": 1, "url": real, "header": header}

        # 降级：交壳解析播放页
        play_page = "{0}/player/{1}".format(API_BASE, quote(vid, safe=""))
        return {"parse": 1, "url": play_page,
                "header": {"User-Agent": PLAYER_UA}}

    def localProxy(self, param):
        p = param if isinstance(param, dict) else dict(parse_qsl(str(param or "")))
        return [404, "text/plain", b""]
