# -*- coding: utf-8 -*-
# 站点: https://getav.net (另有 getav.me / getav.live / getav.co / getav.top)  类型: 自研 JSON API  时间: 2026-11-10
# 要点(2026-11-10 沙盒实测):
#  1. 首页 HTML 全域名被 Cloudflare 403 拦, 但 /api/* JSON 接口放行, 带 UA + Referer + Origin 即可.
#  2. 列表: GET /api/movies?category=<latest|hot|new-releases|uncensored|censored|subtitle|4k>
#     &sortBy=<latest|popular|rating|duration>&limit=40&page=N&locale=zh ; 分页在 data 顶层
#     (page/limit/total/totalPages, 如 latest total=58055, totalPages=1452@limit=40).
#     category=hot/new-releases 与 latest 基本同序; uncensored/censored/subtitle/4k 真过滤.
#  3. sortBy 实测生效(popular/duration 换序); subtitles=true / resolution=4k 作查询参数实测无效
#     (返回与不过滤完全一致), 仅保留原筛选入口, 见下方 FILTER_NOTE.
#  4. 子分类过滤不能走 /api/movies?starId=/genreId=/studioId=/code= (参数被忽略, 返回未过滤全量);
#     改走详情端点 /api/stars/{id} / /api/genres/{id} / /api/studios/{id} / /api/codes/{code}
#     ?page=N&limit=40&sortBy=... , 其 data.movies 为真过滤结果, data.pagination 给真 total/totalPages.
#     sortBy 在详情端点同样生效; subtitles=true 在详情端点亦无效.
#  5. 详情: GET /api/movies/{番号小写} ; videoSources[].type = raw_1080p/raw_240p... / uc_...,
#     url 为 static.worldstatic.com 直链(伪装成 index.txt 的 m3u8, HEAD 200); localM3u8Path 系列
#     字段现为 null, 仅作兜底. 预告 previewVideoUrl 为 /sprites/videos/*.mp4, 磁力在 magnets[].
#  6. 图片: localImg 为 /images/cover/*.jpg 相对路径, 拼 static.worldstatic.com, 直连 200, 后缀
#     @Referer=&User-Agent= 供壳传防盗链头.
#  7. 取流: 原文件赛跑超时 10s + 失败后串行重试, 保留; 多域名并发打, 首个返回有效数据的胜出,
#     空响应/限流响应不算赢. nav 发布页 https://getav.info/zh/ (200) 可解析出候选域名.
# profile: legacy (老壳: 继承 base.spider, getName/localProxy, 超时单位秒, Python 3.8)
import sys
import re
import json
import base64
import hashlib
import html as html_lib
import time
from urllib.parse import urlparse, quote, unquote, parse_qsl

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:  # 无 base.spider 的壳 / 本地调试用最小桩
    try:
        import requests as _req
    except Exception:
        _req = None

    class BaseSpider(object):
        def getProxyUrl(self, local=True):
            return 'http://127.0.0.1:9978/proxy?do=py'

        def fetch(self, url, params=None, headers=None, timeout=10, **kw):
            if _req is None:
                raise RuntimeError('requests unavailable')
            r = _req.get(url, params=params, headers=headers, timeout=timeout, **kw)
            r.encoding = 'utf-8'
            return r

        def post(self, url, params=None, data=None, json=None, headers=None, timeout=10, **kw):
            if _req is None:
                raise RuntimeError('requests unavailable')
            r = _req.post(url, params=params, data=data, json=json, headers=headers, timeout=timeout, **kw)
            r.encoding = 'utf-8'
            return r

        def getCache(self, key):
            return None

        def setCache(self, key, value):
            return 'fail'

        def delCache(self, key):
            return 'fail'

try:
    import requests
except Exception:
    requests = None  # -> _api_once 降级走 urllib
try:
    import threading
except Exception:
    threading = None  # -> 赛跑降级为串行
try:
    import socket
except Exception:
    socket = None

if socket is not None:
    _orig_gai = socket.getaddrinfo

    def _v4_gai(host, port, family=0, type=0, proto=0, flags=0):
        # 容器 DNS 只吐 AAAA 且部分域 AAAA 是脏地址(如 getav.me 指向 Facebook 段),
        # 连 IPv6 后超时/被 RST; 强制只走 IPv4
        try:
            return _orig_gai(host, port, socket.AF_INET, type, proto, flags)
        except Exception:
            return _orig_gai(host, port, family, type, proto, flags)

    socket.getaddrinfo = _v4_gai

# ---------------- CONFIG ----------------
# FILTER_NOTE: sortBy 实测生效; subtitles/resolution 查询参数实测无效(见文件头要点 3/4),
# 但保留原筛选入口与子分类切换卡, 不改 UX; 若站方恢复参数过滤, 入口无需改动.
HOSTS = [
    "https://getav.net",
    "https://getav.me",
    "https://getav.live",
    "https://getav.co",
    "https://getav.top",
]
NAV_SITE = "https://getav.info/zh/"
STATIC_HOST = "https://static.worldstatic.com"
TG_GROUP = "https://t.me/yingshifx1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
RACE_TIMEOUT = 10       # 赛跑单 host 超时(秒), 与原文件一致
LIST_LIMIT = 40         # 影片列表每页条数(接口 limit)
FOLDER_LIMIT = 100      # 类型/片商/番号文件夹每页条数
STAR_LIMIT = 24         # 演员文件夹每页条数
HOME_LIMIT = 20         # 首页推荐条数
# 画质档: videoSources[].type 后缀 -> 展示名, 及降序(高画质在前)
GV_QUALITY = {"4k": "4K", "1080p": "1080P", "720p": "720P", "480p": "480P", "240p": "240P"}
GV_ORDER = ("4k", "1080p", "720p", "480p", "240p")
# videoSources[].type 前缀 -> 版本(一条线路); raw=有码(有码), uc=破解
GV_VERSIONS = (("raw", "有码"), ("uc", "破解"))


def clean_html_text(raw_html):
    txt = re.sub(r'<[^>]+>', '', raw_html or '')
    txt = html_lib.unescape(txt)
    return re.sub(r'[\r\n\t\s]+', ' ', txt).strip()


def format_seconds(secs):
    try:
        s = int(secs)
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        if h > 0:
            return "%02d:%02d:%02d" % (h, m, sec)
        return "%02d:%02d" % (m, sec)
    except Exception:
        return ""


def generate_color_card(text, is_ctrl=False):
    palette = [
        ("#4f46e5", "#7c3aed"),
        ("#2563eb", "#06b6d4"),
        ("#059669", "#10b981"),
        ("#d97706", "#f59e0b"),
        ("#dc2626", "#ea580c"),
        ("#db2777", "#f43f5e"),
        ("#475569", "#334155"),
        ("#0891b2", "#0284c7"),
    ]
    name_str = (text or "GetAV").strip()
    if is_ctrl:
        c1, c2 = ("#e11d48", "#be123c")
    else:
        h_val = int(hashlib.md5(name_str.encode("utf-8")).hexdigest()[:4], 16)
        c1, c2 = palette[h_val % len(palette)]
    display_title = name_str[:12]
    font_size = "40" if len(display_title) <= 6 else ("32" if len(display_title) <= 9 else "26")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">'
        '<defs>'
        '<linearGradient id="g" x1="0%%" y1="0%%" x2="100%%" y2="100%%">'
        '<stop offset="0%%" stop-color="%s"/>'
        '<stop offset="100%%" stop-color="%s"/>'
        '</linearGradient>'
        '</defs>'
        '<rect width="640" height="360" rx="24" fill="url(#g)"/>'
        '<text x="50%%" y="54%%" font-size="%s" font-family="sans-serif" font-weight="bold" '
        'fill="#ffffff" text-anchor="middle" dominant-baseline="middle">'
        '%s'
        '</text>'
        '</svg>'
    ) % (c1, c2, font_size, display_title)
    b64_svg = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
    return "data:image/svg+xml;base64,%s" % b64_svg

class Spider(BaseSpider):
    def __init__(self):
        super(Spider, self).__init__()
        self.navSite = NAV_SITE
        self.hosts = list(HOSTS)
        self._host_idx = 0
        self.baseHost = self.hosts[0]
        self.staticHost = STATIC_HOST
        self.tgGroup = TG_GROUP
        self.brandActor = "🎬 TG群: @yingshifx1"
        self.brandDirector = "🎬 自建影视"
        self._ua = UA
        self.options = {}
        self._nav_cache = None
        self._nav_ts = 0
        self._nav_thread_started = False
        self._host_verified = False

    def init(self, extend=""):
        if isinstance(extend, dict):
            self.options = extend
        elif extend:
            try:
                self.options = json.loads(extend)
            except Exception:
                self.options = {}
        custom_host = self.options.get("host") or self.options.get("siteUrl")
        if custom_host:
            custom_host = custom_host.rstrip("/")
            if custom_host not in self.hosts:
                self.hosts.insert(0, custom_host)
            self.baseHost = custom_host
        else:
            # 不发网络: 命中缓存直接用, 否则交给首次真请求的并发赛跑(_fetch_api);
            # 缓存服务缺失(如本地调试)时不崩溃
            try:
                cached_host = self.getCache("getav_live_host")
            except Exception:
                cached_host = None
            if cached_host and str(cached_host).startswith("http"):
                if cached_host not in self.hosts:
                    self.hosts.insert(0, cached_host)
                self.baseHost = cached_host
                self._host_idx = 0
                self._host_verified = True
            else:
                self._host_idx = 0
                self.baseHost = self.hosts[0]
                self._host_verified = False

    def getName(self):
        return "GetAV"

    def isVideoFormat(self, url):
        low = (url or "").lower()
        return any(k in low for k in (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpd", "index.png", "index.txt"))

    def manualVideoCheck(self):
        return False

    # ---------- 网络: 赛跑 + 串行重试(保留原逻辑) ----------
    def _headers(self, host):
        return {
            "User-Agent": self._ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Referer": host + "/zh",
            "Origin": host,
        }

    def _nav_domains(self):
        # 抓 navSite 页面解析出全部 getav.* 候选域名(只取候选, 不定序); 结果缓存 5 分钟
        now = time.time()
        if self._nav_cache is not None and now - self._nav_ts < 300:
            return self._nav_cache
        found = []
        try:
            if requests is not None:
                r = requests.get(self.navSite, headers={
                    "User-Agent": self._ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }, timeout=5)
                html = r.text
            else:
                import urllib.request
                req = urllib.request.Request(self.navSite, headers={"User-Agent": self._ua})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
            for d in re.findall(r'getav\.[a-z0-9]+', html, re.I):
                h = ("https://%s" % d.lower()).strip()
                if "info" not in h and h not in found:
                    found.append(h)
        except Exception:
            pass
        self._nav_cache = found
        self._nav_ts = now
        return found

    def _bg_nav_refresh(self):
        # 后台异步抓 navSite, 发现新域名补进 hosts; 不阻塞任何请求
        try:
            for h in self._nav_domains():
                if h not in self.hosts:
                    self.hosts.append(h)
        except Exception:
            pass

    def _valid_api_data(self, api_path, data):
        # 赛跑胜出校验: 空响应/限流响应再快也不算赢
        if not isinstance(data, dict):
            return False
        if data.get("success") is False:
            return False
        d = data.get("data")
        if d is None:
            return False
        if "api/movies?" in api_path or api_path.startswith("/api/movies?"):
            movies = d.get("movies") if isinstance(d, dict) else None
            return bool(movies)
        if "/api/movies/" in api_path:
            return bool(d.get("title") or d.get("id"))
        if re.search(r'/api/(stars|genres|studios|codes)/[^/?]+', api_path):
            # 子分类详情端点: data.movies 为真过滤结果
            return bool(d.get("movies"))
        if "/api/genres" in api_path or "/api/stars" in api_path or "/api/studios" in api_path or "/api/codes" in api_path:
            if isinstance(d, dict):
                return any(d.get(k) for k in ("genres", "stars", "studios", "codes"))
            return bool(d)
        return bool(d)

    def _api_once(self, api_path, cur_host, headers, timeout):
        # 单发请求, 任一环节失败返回 None; 优先走基类 self.fetch, 失败降级 urllib
        if api_path.startswith("http"):
            url = re.sub(r"^https?://[^/]+", cur_host, api_path)
        else:
            url = cur_host + ("/" + api_path.lstrip("/"))
        h = dict(headers)
        h["Referer"] = cur_host + "/zh"
        h["Origin"] = cur_host
        try:
            rsp = self.fetch(url, headers=h, timeout=timeout)
            if getattr(rsp, "status_code", 200) != 200:
                return None
            raw = rsp.text if isinstance(getattr(rsp, "text", None), str) else rsp.content.decode("utf-8", errors="ignore")
            data = json.loads(raw)
            return data if data else None
        except Exception:
            pass
        # 降级: urllib(无 requests / 基类 fetch 不可用时)
        try:
            import urllib.request
            import gzip
            import zlib
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = getattr(resp, "headers", {}).get("Content-Encoding", "")
                if raw.startswith(b"\x1f\x8b") or enc == "gzip":
                    raw = gzip.decompress(raw)
                elif enc == "deflate":
                    try:
                        raw = zlib.decompress(raw)
                    except Exception:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                data = json.loads(raw.decode("utf-8", errors="ignore"))
                return data if data else None
        except Exception:
            return None

    def _race_api(self, api_path, headers, timeout=6):
        # 真请求并发打到硬编码 hosts, 第一个返回有效数据的胜出(独立连接);
        # 空响应/被限流的 host 再快也不算赢, 避免"越空越快越胜出"的陷阱
        if threading is None:
            for h in list(self.hosts):
                data = self._api_once(api_path, h, headers, timeout)
                if data and self._valid_api_data(api_path, data):
                    return h, data
            return None, None
        if not self._nav_thread_started:
            self._nav_thread_started = True
            nt = threading.Thread(target=self._bg_nav_refresh)
            nt.daemon = True
            nt.start()
        cands = list(self.hosts)
        hit = {}
        lock = threading.Lock()
        ev = threading.Event()

        def worker(h):
            if ev.is_set():
                return
            data = self._api_once(api_path, h, headers, timeout)
            if data and self._valid_api_data(api_path, data):
                with lock:
                    if not hit:
                        hit["host"] = h
                        hit["data"] = data
                ev.set()

        for h in cands:
            t = threading.Thread(target=worker, args=(h,))
            t.daemon = True
            t.start()
        ev.wait(timeout + 1)
        return hit.get("host"), hit.get("data")

    def _fetch_api(self, api_path, timeout=RACE_TIMEOUT):
        headers = self._headers(self.baseHost)
        # 真请求并发打到所有候选 host, 谁先成用谁(已验证 host 也参与, 避免在慢 host 上白等)
        host, data = self._race_api(api_path, headers, timeout=min(timeout, RACE_TIMEOUT))
        if data:
            if host not in self.hosts:
                self.hosts.insert(0, host)
            self._host_idx = self.hosts.index(host)
            self.baseHost = host
            self._host_verified = True
            try:
                self.setCache("getav_live_host", host)
            except Exception:
                pass
            return data
        # 兜底: 并发赛跑全员超时(沙盒实测偶发, 约 1/5 概率)时, 按已验证 host 优先逐个串行重试一次
        ordered = [self.baseHost] + [h for h in self.hosts if h != self.baseHost]
        for h in ordered:
            try:
                data = self._api_once(api_path, h, headers, timeout=RACE_TIMEOUT)
            except Exception:
                data = None
            if data and self._valid_api_data(api_path, data):
                self._host_idx = self.hosts.index(h) if h in self.hosts else 0
                self.baseHost = h
                self._host_verified = True
                try:
                    self.setCache("getav_live_host", h)
                except Exception:
                    pass
                return data
        return {}

    # ---------- 通用解析 ----------
    def _format_poster(self, raw_url):
        if not raw_url:
            return ""
        pic = raw_url.strip()
        if pic.startswith("//"):
            pic = "https:" + pic
        elif pic.startswith("/"):
            pic = self.staticHost + pic
        img_tail = "@Referer=%s/&User-Agent=%s" % (self.baseHost, quote(self._ua))
        return pic + img_tail

    def _movie_card(self, m):
        cid = str(m.get("id", "")).strip().lower()
        if not cid or cid.isdigit():
            return None
        dur = format_seconds(m.get("videoLength", 0))
        return {
            "vod_id": cid,
            "vod_name": m.get("title") or cid.upper(),
            "vod_pic": self._format_poster(m.get("localImg") or m.get("img")),
            "vod_remarks": "自建影视 | %s" % dur if dur else "自建影视",
            "style": {"type": "rect", "ratio": 1.42},
        }

    def _movie_cards(self, movies):
        v_list = []
        for m in movies or []:
            card = self._movie_card(m)
            if card:
                v_list.append(card)
        return v_list

    @staticmethod
    def _page_info(data, page, limit_req, n_items):
        # 翻页四件用站点真值: 影片列表 data 顶层 page/limit/total/totalPages;
        # 文件夹类 data.pagination 嵌套; 都没有时按本页是否满页估算, total 不填
        total = None
        pagecount = None
        limit = limit_req
        flat = data if isinstance(data, dict) else {}
        pag = flat.get("pagination") if isinstance(flat.get("pagination"), dict) else {}
        if isinstance(flat.get("total"), int):
            total = flat["total"]
        elif isinstance(pag.get("total"), int):
            total = pag["total"]
        if isinstance(flat.get("totalPages"), int):
            pagecount = flat["totalPages"]
        elif isinstance(pag.get("totalPages"), int):
            pagecount = pag["totalPages"]
        if isinstance(flat.get("limit"), int) and flat["limit"]:
            limit = flat["limit"]
        elif isinstance(pag.get("limit"), int) and pag["limit"]:
            limit = pag["limit"]
        if pagecount is None:
            if total is not None and limit:
                pagecount = (total + limit - 1) // limit
            else:
                pagecount = page + 1 if n_items >= limit_req else page
        out = {"page": page, "pagecount": pagecount, "limit": limit}
        if total is not None:
            out["total"] = total
        return out

    def _paged_movies(self, data, page, limit_req):
        movies = (data or {}).get("movies") or []
        v_list = self._movie_cards(movies)
        out = self._page_info(data, page, limit_req, len(movies))
        out["list"] = v_list
        return out

    # ---------- 入口 ----------
    def homeContent(self, filter):
        classes = [
            {"type_name": "🔥 最近更新", "type_id": "api@@latest"},
            {"type_name": "📈 热门影片", "type_id": "api@@hot"},
            {"type_name": "✨ 新片上市", "type_id": "api@@new-releases"},
            {"type_name": "🔞 无码影片", "type_id": "api@@uncensored"},
            {"type_name": "🈵 有码影片", "type_id": "api@@censored"},
            {"type_name": "🔤 字幕专区", "type_id": "api@@subtitle"},
            {"type_name": "💎 4K 超高清", "type_id": "api@@4k"},
            {"type_name": "📂 类型大全", "type_id": "folder@@genres"},
            {"type_name": "📂 演员大全", "type_id": "folder@@stars"},
            {"type_name": "📂 片商大全", "type_id": "folder@@studios"},
            {"type_name": "📂 番号系列", "type_id": "folder@@codes"},
        ]
        video_filters = [
            {
                "key": "sortBy",
                "name": "排序",
                "value": [
                    {"n": "最新", "v": "latest"},
                    {"n": "最热", "v": "popular"},
                    {"n": "评分", "v": "rating"},
                    {"n": "时长", "v": "duration"},
                ],
            },
            {
                "key": "subtitles",
                "name": "字幕",
                "value": [
                    {"n": "全部", "v": ""},
                    {"n": "中文字幕", "v": "true"},
                ],
            },
            {
                "key": "resolution",
                "name": "画质",
                "value": [
                    {"n": "全部", "v": ""},
                    {"n": "4K超高清", "v": "4k"},
                ],
            },
        ]
        folder_sort_filters = [
            {
                "key": "sort",
                "name": "排序",
                "value": [
                    {"n": "最热", "v": "popular"},
                    {"n": "名称", "v": "name"},
                    {"n": "数量", "v": "movies"},
                ],
            },
        ]
        filters = {
            "api@@latest": video_filters,
            "api@@hot": video_filters,
            "api@@new-releases": video_filters,
            "api@@uncensored": video_filters,
            "api@@censored": video_filters,
            "api@@subtitle": video_filters,
            "api@@4k": video_filters,
            "folder@@genres": folder_sort_filters,
            "folder@@studios": folder_sort_filters,
            "folder@@codes": folder_sort_filters,
            "folder@@stars": [
                {
                    "key": "gender",
                    "name": "性别",
                    "value": [
                        {"n": "女优", "v": "2"},
                        {"n": "男优", "v": "1"},
                    ],
                },
                {
                    "key": "sort",
                    "name": "排序",
                    "value": [
                        {"n": "最热", "v": "popular"},
                        {"n": "最新", "v": "latest"},
                        {"n": "数量", "v": "movies"},
                        {"n": "名称", "v": "name"},
                    ],
                },
            ],
        }
        return {"class": classes, "filters": filters}

    def homeVideoContent(self):
        res = self._fetch_api("/api/movies?category=latest&sortBy=latest&limit=%d&page=1&locale=zh" % HOME_LIMIT)
        movies = (res.get("data") or {}).get("movies") or []
        v_list = []
        for m in movies:
            card = self._movie_card(m)
            if card:
                card["style"] = {"type": "rect", "ratio": 1.42}
                v_list.append(card)
        return {"list": v_list}

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if str(pg).isdigit() else 1
        if page < 1:
            page = 1
        extend = extend or {}

        if str(tid).startswith("folder@@"):
            return self._folder_content(str(tid).replace("folder@@", ""), page, extend)
        if str(tid).startswith("subfolder@@"):
            return self._subfolder_content(str(tid), page)

        params = [
            "page=%d" % page,
            "limit=%d" % LIST_LIMIT,
            "locale=zh",
        ]
        sort_val = extend.get("sortBy", "latest")
        params.append("sortBy=%s" % sort_val)
        if extend.get("subtitles") == "true":
            params.append("subtitles=true")
        if extend.get("resolution") == "4k":
            params.append("resolution=4k")
        query_tail = "&".join(params)
        cat = tid.replace("api@@", "") if str(tid).startswith("api@@") else "latest"
        res = self._fetch_api("/api/movies?category=%s&%s" % (cat, query_tail))
        data_obj = res.get("data") or {}
        return self._paged_movies(data_obj, page, LIST_LIMIT)

    def _folder_content(self, f_type, page, extend):
        if f_type == "stars":
            gender = extend.get("gender", "2")
            sort_type = extend.get("sort", "popular")
            query_parts = [
                "page=%d" % page,
                "limit=%d" % STAR_LIMIT,
                "sort=%s" % sort_type,
                "locale=zh",
            ]
            if gender:
                query_parts.append("gender=%s" % gender)
            res = self._fetch_api("/api/stars?" + "&".join(query_parts))
            data_obj = res.get("data") or {}
            stars = data_obj.get("stars") or (res.get("data") if isinstance(res.get("data"), list) else [])
            v_list = []
            for s in stars:
                s_id = str(s.get("id", "")).strip()
                base_name = s.get("name") or s.get("originalName") or s.get("nameJp") or ""
                if not base_name:
                    continue
                count = s.get("movieCount") or s.get("movie_count") or ""
                display_name = "%s (%s部)" % (base_name, count) if count else base_name
                remarks = "自建影视 | %s部" % count if count else "自建影视"
                raw_avatar = s.get("localAvatar") or s.get("avatar") or s.get("localImg") or s.get("img")
                avatar = self._format_poster(raw_avatar) if raw_avatar else generate_color_card(base_name)
                v_list.append({
                    "vod_id": "subfolder@@star@@%s@@sortBy=popular@@subtitles=@@resolution=@@name=%s" % (s_id, quote(base_name)),
                    "vod_name": display_name,
                    "vod_pic": avatar,
                    "vod_remarks": remarks,
                    "vod_tag": "folder",
                    "style": {"type": "oval", "ratio": 1.0},
                })
            out = self._page_info(data_obj, page, STAR_LIMIT, len(v_list))
            out["list"] = v_list
            return out

        if f_type == "genres":
            sort_type = extend.get("sort", "popular")
            res = self._fetch_api("/api/genres?limit=%d&sort=%s&locale=zh-CN&page=%d" % (FOLDER_LIMIT, sort_type, page))
            items = (res.get("data") or {}).get("genres") or []
            v_list = []
            for g in items:
                g_id = str(g.get("id", ""))
                name = g.get("name") or g.get("originalName", "")
                count = g.get("movieCount", "")
                remarks = "自建影视 | %s部" % count if count else "自建影视"
                v_list.append({
                    "vod_id": "subfolder@@genre@@%s@@sortBy=latest@@subtitles=@@resolution=@@name=%s" % (g_id, quote(name)),
                    "vod_name": name,
                    "vod_pic": generate_color_card(name),
                    "vod_remarks": remarks,
                    "vod_tag": "folder",
                    "style": {"type": "rect", "ratio": 1.78},
                })
            out = self._page_info(res.get("data") or {}, page, FOLDER_LIMIT, len(v_list))
            out["list"] = v_list
            return out

        if f_type == "studios":
            sort_type = extend.get("sort", "popular")
            res = self._fetch_api("/api/studios?limit=%d&sort=%s&locale=zh-CN&page=%d" % (FOLDER_LIMIT, sort_type, page))
            studios = (res.get("data") or {}).get("studios") or []
            v_list = []
            for st in studios:
                st_id = str(st.get("id", ""))
                name = st.get("name", "")
                count = st.get("movieCount") or st.get("movie_count") or ""
                remarks = "自建影视 | %s部" % count if count else "自建影视"
                v_list.append({
                    "vod_id": "subfolder@@studio@@%s@@sortBy=latest@@subtitles=@@resolution=@@name=%s" % (st_id, quote(name)),
                    "vod_name": name,
                    "vod_pic": generate_color_card(name),
                    "vod_remarks": remarks,
                    "vod_tag": "folder",
                    "style": {"type": "rect", "ratio": 1.78},
                })
            out = self._page_info(res.get("data") or {}, page, FOLDER_LIMIT, len(v_list))
            out["list"] = v_list
            return out

        if f_type == "codes":
            sort_type = extend.get("sort", "popular")
            res = self._fetch_api("/api/codes?limit=%d&sort=%s&locale=zh-CN&page=%d" % (FOLDER_LIMIT, sort_type, page))
            codes = (res.get("data") or {}).get("codes") or []
            v_list = []
            for cd in codes:
                cd_code = str(cd.get("code") or cd.get("name", "")).strip().upper()
                count = cd.get("movieCount") or cd.get("movie_count") or ""
                remarks = "自建影视 | %s部" % count if count else "自建影视"
                v_list.append({
                    "vod_id": "subfolder@@code@@%s@@sortBy=latest@@subtitles=@@resolution=@@name=%s" % (cd_code, quote(cd_code)),
                    "vod_name": cd_code,
                    "vod_pic": generate_color_card(cd_code),
                    "vod_remarks": remarks,
                    "vod_tag": "folder",
                    "style": {"type": "rect", "ratio": 1.78},
                })
            out = self._page_info(res.get("data") or {}, page, FOLDER_LIMIT, len(v_list))
            out["list"] = v_list
            return out

        return {"list": [], "page": page, "pagecount": page, "limit": 0}

    def _subfolder_content(self, tid, page):
        parts = str(tid).split("@@")
        sub_type = parts[1]
        target_id = parts[2]
        p_dict = {
            "sortBy": "popular" if sub_type == "star" else "latest",
            "subtitles": "",
            "resolution": "",
            "name": target_id,
        }
        for p in parts[3:]:
            if "=" in p:
                k, v = p.split("=", 1)
                p_dict[k] = unquote(v)
        curr_sort = p_dict["sortBy"]
        curr_sub = p_dict["subtitles"]
        curr_res = p_dict["resolution"]
        sub_name = p_dict["name"]

        # 子分类详情端点(真过滤, 见文件头要点 4); sortBy 在此端点实测生效
        detail_path = {
            "star": "/api/stars/%s",
            "genre": "/api/genres/%s",
            "studio": "/api/studios/%s",
            "code": "/api/codes/%s",
        }.get(sub_type)
        if detail_path is None:
            return {"list": [], "page": page, "pagecount": page, "limit": LIST_LIMIT}
        api_params = [
            "page=%d" % page,
            "limit=%d" % LIST_LIMIT,
            "locale=zh",
            "sortBy=%s" % curr_sort,
        ]
        if curr_sub == "true":
            api_params.append("subtitles=true")
        if curr_res == "4k":
            api_params.append("resolution=4k")
        res = self._fetch_api((detail_path % quote(target_id, safe="")) + "?" + "&".join(api_params))
        data_obj = res.get("data") or {}

        v_list = []
        if page == 1:
            sort_next_map = {
                "popular": ("latest", "最热", "最新"),
                "latest": ("duration", "最新", "时长"),
                "duration": ("popular", "时长", "最热"),
            }
            next_sort, cur_s_n, next_s_n = sort_next_map.get(curr_sort, ("popular", curr_sort, "最热"))
            sort_tid = "subfolder@@%s@@%s@@sortBy=%s@@subtitles=%s@@resolution=%s@@name=%s" % (
                sub_type, target_id, next_sort, curr_sub, curr_res, quote(sub_name)
            )
            v_list.append({
                "vod_id": sort_tid,
                "vod_name": "🔀 排序: %s (点击切换)" % cur_s_n,
                "vod_pic": generate_color_card("排序:%s" % cur_s_n, is_ctrl=True),
                "vod_remarks": "切至 %s" % next_s_n,
                "vod_tag": "folder",
                "style": {"type": "rect", "ratio": 1.42},
            })
            next_sub = "" if curr_sub == "true" else "true"
            cur_sub_n = "中文字幕" if curr_sub == "true" else "全部版本"
            next_sub_n = "全部版本" if curr_sub == "true" else "中文字幕"
            sub_tid = "subfolder@@%s@@%s@@sortBy=%s@@subtitles=%s@@resolution=%s@@name=%s" % (
                sub_type, target_id, curr_sort, next_sub, curr_res, quote(sub_name)
            )
            v_list.append({
                "vod_id": sub_tid,
                "vod_name": "🔤 字幕: %s" % cur_sub_n,
                "vod_pic": generate_color_card("%s" % cur_sub_n, is_ctrl=True),
                "vod_remarks": "切至 %s" % next_sub_n,
                "vod_tag": "folder",
                "style": {"type": "rect", "ratio": 1.42},
            })
            next_res = "" if curr_res == "4k" else "4k"
            cur_res_n = "4K超清" if curr_res == "4k" else "全部画质"
            next_res_n = "全部画质" if curr_res == "4k" else "4K超清"
            res_tid = "subfolder@@%s@@%s@@sortBy=%s@@subtitles=%s@@resolution=%s@@name=%s" % (
                sub_type, target_id, curr_sort, curr_sub, next_res, quote(sub_name)
            )
            v_list.append({
                "vod_id": res_tid,
                "vod_name": "💎 画质: %s" % cur_res_n,
                "vod_pic": generate_color_card("%s" % cur_res_n, is_ctrl=True),
                "vod_remarks": "切至 %s" % next_res_n,
                "vod_tag": "folder",
                "style": {"type": "rect", "ratio": 1.42},
            })

        movies = data_obj.get("movies") or []
        v_list.extend(self._movie_cards(movies))
        # 切换卡占 3 位, 翻页真值按影片数算
        out = self._page_info(data_obj, page, LIST_LIMIT, len(movies))
        out["list"] = v_list
        return out

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        if isinstance(ids, str):
            ids = [ids]
        items = []
        for raw_id in ids:
            if str(raw_id).startswith("subfolder@@"):
                # 资料夹卡: 壳点卡后以该 vod_id 再调 categoryContent 出片单
                page = self.categoryContent(raw_id, 1, None, None)
                items.extend(page.get("list") or [])
                continue
            items.append(self._detail_one(str(raw_id).strip().lower()))
        return {"list": [it for it in items if it]}

    def _detail_one(self, code):
        res = self._fetch_api("/api/movies/%s" % code)
        data = res.get("data") or {}
        title = data.get("title", code.upper())
        poster = self._format_poster(data.get("localImg") or data.get("img"))
        actors_list = data.get("stars") or []
        actor_names = [clean_html_text(a.get("name", "")) for a in actors_list if a.get("name")]
        actor_str = ", ".join(actor_names) if actor_names else self.brandActor
        genres_list = data.get("genres") or []
        genre_names = [clean_html_text(g.get("name", "")) for g in genres_list if g.get("name")]
        type_str = " / ".join(genre_names) if genre_names else "情色"
        dur_str = format_seconds(data.get("videoLength", 0))
        remarks = "自建影视 | %s" % dur_str if dur_str else "自建影视"
        desc = data.get("description") or title
        full_content = (
            "【🔥 官方交流群: %s】\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "番号: %s\n"
            "片名: %s\n"
            "时长: %s\n"
            "发行: %s\n"
            "简介: %s"
        ) % (self.tgGroup, code.upper(), title, dur_str, str(data.get("date", ""))[:10], desc)

        video_sources = data.get("videoSources") or []
        # 画质/版本走选集: 详情页只铺占位 gv:前缀, playerContent 返扁平数组出清晰度菜单
        # 有码/破解各占一个选集; 未收录的版本不铺
        gv_items = []
        for pfx, vname in GV_VERSIONS:
            has = any(str(vs.get("type", "")).startswith(pfx + "_") and vs.get("url") for vs in video_sources)
            if has:
                gv_items.append("%s$gv:%s:%s" % (vname, pfx, code))
        play_from, play_urls = [], []
        if gv_items:
            play_from.append("getav")
            play_urls.append("#".join(gv_items))
        else:
            # 兜底: videoSources 缺字段时退回老字段, 直接铺成选集
            legacy = []
            if data.get("localM3u8Path4k"):
                legacy.append("4K$%s" % data["localM3u8Path4k"])
            if data.get("localM3u8Path"):
                legacy.append("1080P$%s" % data["localM3u8Path"])
            if data.get("localM3u8PathUc"):
                legacy.append("无码$%s" % data["localM3u8PathUc"])
            if legacy:
                play_from.append("getav")
                play_urls.append("#".join(legacy))
        preview_url = data.get("previewVideoUrl")
        if preview_url:
            full_preview = self.staticHost + preview_url if not preview_url.startswith("http") else preview_url
            play_from.append("精彩预告")
            play_urls.append("精彩预告$%s" % full_preview)
        # 磁力线路: 每条磁力 = 一个条目「原名$magnet链接」, 盒子显示原名而非「1集」; 按 BTIH hash 去重
        magnets = data.get("magnets") or []
        magnet_items = []
        seen_btih = set()
        for mg in magnets:
            mlink = mg.get("link") or ""
            if not mlink.startswith("magnet:"):
                continue
            btih = (mg.get("id") or "").strip().lower()
            if not btih:
                mh = re.search(r'btih:([0-9a-fA-F]{40}|[0-9a-zA-Z]{32})', mlink)
                btih = mh.group(1).lower() if mh else mlink.lower()
            if btih in seen_btih:
                continue
            seen_btih.add(btih)
            mname = clean_html_text(mg.get("title") or "").replace("$", " ").strip() or "磁力"
            mname = re.sub(r'^\s*\[getav\.net\]\s*', '', mname, flags=re.I)
            msize = (mg.get("size") or "").replace("$", " ").strip()
            label = ("%s %s" % (mname, msize)).strip()
            magnet_items.append("%s$%s" % (label, mlink))
        play_from = play_from or ["暂无可用线路"]
        play_urls = play_urls or ["暂无可用线路$http://127.0.0.1"]
        return {
            "vod_id": code,
            "vod_name": title,
            "vod_pic": poster,
            "type_name": type_str,
            "vod_year": str(data.get("date", ""))[:4],
            "vod_area": "日本",
            "vod_remarks": remarks,
            "vod_actor": actor_str,
            "vod_director": self.brandDirector,
            "vod_content": full_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
            "vod_play_from": "$$$".join(play_from + (["磁力下载"] if magnet_items else [])),
            "vod_play_url": "$$$".join(play_urls + (["#".join(magnet_items)] if magnet_items else [])),
        }

    def _gv_sources(self, code):
        # 番号 -> videoSources 按版本分组, 组内画质降序 + url 去重。返回 {"有码":[(label,url)..], "破解":[...]}
        res = self._fetch_api("/api/movies/%s" % str(code).strip().lower())
        srcs = (res.get("data") or {}).get("videoSources") or []
        out = {}
        for pfx, vname in GV_VERSIONS:
            got, seen = [], set()
            for q in GV_ORDER:
                for vs in srcs:
                    u = vs.get("url") or ""
                    if vs.get("type") == pfx + "_" + q and u and u not in seen:
                        seen.add(u)
                        got.append((GV_QUALITY[q], u))
            if got:
                out[vname] = got
        return out

    def playerContent(self, flag, id, vipFlags):
        # getav 选集: 有码/破解各一个选集, 集内多档画质以扁平数组返回 -> 壳出清晰度切换菜单
        header = {"User-Agent": self._ua, "Referer": self.baseHost + "/zh", "Origin": self.baseHost}
        if str(id).startswith("gv:"):
            parts = str(id).split(":", 2)
            pfx, code = (parts[1], parts[2]) if len(parts) == 3 else ("", parts[-1])
            gv = self._gv_sources(code).get(dict(GV_VERSIONS).get(pfx, ""), [])
            if not gv:
                return {"parse": 0, "url": ["该版本未收录", "http://127.0.0.1"], "header": header}
            if len(gv) == 1:
                return {"parse": 0, "url": gv[0][1], "header": header}
            flat = []
            for lab, u in gv:
                flat += [lab, u]
            return {"parse": 0, "url": flat, "header": header}
        play_url = str(id).strip()
        return {"parse": 0, "url": play_url, "header": header}

    def searchContent(self, key, quick, pg="1"):
        page = int(pg) if str(pg).isdigit() else 1
        if page < 1:
            page = 1
        kw = quote(key.strip())
        api_url = "/api/movies?q=%s&sortBy=latest&limit=%d&page=%d&locale=zh" % (kw, LIST_LIMIT, page)
        res = self._fetch_api(api_url)
        data_obj = res.get("data") or {}
        return self._paged_movies(data_obj, page, LIST_LIMIT)

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    def localProxy(self, param):
        # 本源图片/播放直链, 不需要代理; 参数可能是 dict 或字符串, 都接
        p = param if isinstance(param, dict) else dict(parse_qsl(str(param or "")))
        _t = str(p.get("type") or "")
        _u = unquote(str(p.get("url") or ""))
        return [404, "text/plain; charset=utf-8", "Proxy not configured"]

    def destroy(self):
        self.options = {}
