"""AvPlay - 基于 JavDB API + JavMenu 的 Python 爬虫。

逆向来源: AvPlay 2.0.0 (com.javrocket.playground)
  - 核心域名: https://jdforrepam.com (JavDB API)
  - 签名头:   jdSignature = {ts}.lpw6vgqzsp.{md5(ts+salt)}
  - 播放来源: https://javmenu.com 解析详情页 m3u8
"""

import hashlib
import json
import re
import time

import requests

API_BASE = "https://jdforrepam.com"
JAVMENU_BASE = "https://javmenu.com"

SIGN_MIDDLE = "lpw6vgqzsp"
SIGN_SALT = (
    "71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa"
)
# APK 真实 UA 系运行时从系统 WebView 抓取（AppUserAgentManager），无硬编码；
# 此处用标准 Android WebView UA 格式代替桌面 Chrome UA
UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/120.0.0.0 Mobile Safari/537.36"
)
PAGE_SIZE = 24
TIMEOUT = 15


def sign():
    ts = int(time.time())
    md5 = hashlib.md5((str(ts) + SIGN_SALT).encode("utf-8")).hexdigest()
    return "%d.%s.%s" % (ts, SIGN_MIDDLE, md5)


class Spider:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance:
            return cls._instance
        cls._instance = super().__new__(cls)
        return cls._instance

    def getDependence(self):
        return []

    def __init__(self):
        self.extend = ""
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": API_BASE,
            "Referer": API_BASE + "/",
        })

    def init(self, extend=""):
        self.extend = extend or ""

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass

    # ---------------- 网络层 ----------------

    def _api(self, path, params=None):
        headers = {"jdSignature": sign()}
        try:
            r = self.session.get(
                API_BASE + path, params=params, headers=headers, timeout=TIMEOUT
            )
            if r.status_code != 200:
                return {}
            j = r.json()
            if not j.get("success"):
                return {}
            return j.get("data") or {}
        except Exception:
            return {}

    def _html(self, url, referer=None):
        headers = {"User-Agent": UA}
        if referer:
            headers["Referer"] = referer
        try:
            r = self.session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                return ""
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception:
            return ""

    # ---------------- 字段转换 ----------------

    @staticmethod
    def _build_filter(extend, default_main="p,c"):
        """filter_by = {type}:t:{主筛选}:{标签}:{年}:{时长}:{月}"""
        main = str((extend or {}).get("main") or default_main).strip()
        tags = str((extend or {}).get("tag") or "").strip()
        year = str((extend or {}).get("year") or "").strip()
        duration = str((extend or {}).get("duration") or "").strip()
        month = str((extend or {}).get("month") or "").strip()
        return "0:t:%s:%s:%s:%s:%s" % (main, tags, year, duration, month)

    @staticmethod
    def _clean(value):
        return str(value or "").replace("$", "＄").replace("#", "＃").strip()

    @staticmethod
    def _cover(url):
        """tp.spfcas.com 返回的是加密流，换到真实直链 c0.jdbstatic.com。"""
        u = str(url or "").strip()
        if not u:
            return ""
        m = re.search(r"/covers/([a-z0-9]{2})/([A-Za-z0-9+/=_-]+)\.jpg", u)
        if m:
            return "https://c0.jdbstatic.com/covers/%s/%s.jpg" % (m.group(1), m.group(2))
        return u

    @staticmethod
    def _vod(movie):
        mid = str(movie.get("id") or "")
        number = movie.get("number") or mid
        title = movie.get("title") or number
        name = ("%s %s" % (number, title)).strip() if number not in title else title
        remark = []
        if movie.get("has_cnsub"):
            remark.append("中字")
        if movie.get("can_play"):
            remark.append("可播")
        if movie.get("has_preview_video"):
            remark.append("預覽")
        if movie.get("magnets_count"):
            remark.append("%d磁力" % movie["magnets_count"])
        score = movie.get("score")
        if score:
            remark.insert(0, "★%.1f" % float(score))
        return {
            "vod_id": mid,
            "vod_name": name,
            "vod_pic": Spider._cover(movie.get("cover_url") or movie.get("thumb_url") or ""),
            "vod_remarks": " ".join(remark) if remark else str(movie.get("release_date") or ""),
            "vod_year": str(movie.get("release_date") or "")[:4],
            "style": {"type": "rect", "ratio": 0.75},
        }

    # ---------------- 首页 ----------------

    def homeContent(self, filter):
        result = {
            "class": [
                {"type_id": "latest", "type_name": "最新"},
                {"type_id": "hot", "type_name": "熱門"},
                {"type_id": "cnsub", "type_name": "中文字幕"},
                {"type_id": "playable", "type_name": "可播放"},
                {"type_id": "rank_daily", "type_name": "日榜"},
                {"type_id": "rank_weekly", "type_name": "週榜"},
                {"type_id": "rank_monthly", "type_name": "月榜"},
            ]
        }
        if filter:
            rows = [
                {"key": "main", "name": "篩選",
                 "value": [
                     {"n": "全部", "v": ""},
                     {"n": "可播放", "v": "p"},
                     {"n": "可下載", "v": "m"},
                     {"n": "含字幕", "v": "c"},
                     {"n": "單體影片", "v": "s"},
                     {"n": "含預覽圖", "v": "i"},
                     {"n": "含預覽視頻", "v": "v"},
                 ]},
                {"key": "year", "name": "年份",
                 "value": [{"n": str(y), "v": str(y)} for y in range(2026, 2000, -1)] + [{"n": "全部", "v": ""}]},
                {"key": "sort", "name": "排序",
                 "value": [
                     {"n": "最新", "v": "release"},
                     {"n": "評分", "v": "score"},
                 ]},
            ]
            result["filters"] = {
                "latest": rows,
                "hot": rows,
                "cnsub": rows,
                "playable": rows,
            }
        return result

    def homeVideoContent(self):
        data = self._api(
            "/api/v1/movies/latest",
            {
                "type": 0,
                "filter_by": "0:t:p,c::::",
                "sort_by": "release",
                "order_by": "desc",
                "page": 1,
                "limit": PAGE_SIZE,
            },
        )
        movies = data.get("movies") or []
        return {"list": [self._vod(m) for m in movies]}

    # ---------------- 分类 ----------------

    def categoryContent(self, tid, pg, filter, extend):
        tid = str(tid or "latest")
        extend = extend or {}
        try:
            page = max(1, int(pg))
        except (TypeError, ValueError):
            page = 1

        if tid.startswith("rank_"):
            period = tid.split("_", 1)[1]
            data = self._api("/api/v1/rankings", {"type": 0, "period": period, "page": page, "limit": PAGE_SIZE})
            movies = data.get("movies") or []
        elif tid == "top250":
            data = self._api("/api/v1/movies/top", {"type": 0, "type_value": "", "page": page, "limit": PAGE_SIZE})
            movies = data.get("movies") or []
        else:
            sort_by = str(extend.get("sort") or "release").strip() or "release"
            if tid == "hot":
                sort_by = "watched"
            # hot/cnsub/playable 必须走 tags 端点才能按 main 段过滤
            if tid in ("hot", "cnsub", "playable"):
                main = "p,c"
                if tid == "cnsub":
                    main = "c"
                elif tid == "hot":
                    main = "p"
                filter_by = self._build_filter(dict(extend, main=main), main)
                path = "/api/v1/movies/tags"
            else:
                filter_by = self._build_filter(extend, "p,c")
                path = "/api/v1/movies/latest"
            data = self._api(path, {"filter_by": filter_by, "sort_by": sort_by, "order_by": "desc", "page": page, "limit": PAGE_SIZE})
            movies = data.get("movies") or []

        return {
            "list": [self._vod(m) for m in movies],
            "page": page,
            "pagecount": page + 1 if len(movies) >= PAGE_SIZE else page,
            "limit": PAGE_SIZE,
            "total": page * PAGE_SIZE,
        }

    # ---------------- 搜索 ----------------

    def searchContent(self, key, quick, pg="1"):
        try:
            page = max(1, int(pg))
        except (TypeError, ValueError):
            page = 1
        data = self._api("/api/v2/search", {"q": str(key or ""), "page": page, "limit": PAGE_SIZE})
        movies = data.get("movies") or []
        return {
            "list": [self._vod(m) for m in movies],
            "page": page,
            "pagecount": page + 1 if len(movies) >= PAGE_SIZE else page,
            "limit": PAGE_SIZE,
            "total": page * PAGE_SIZE,
        }

    # ---------------- 详情 ----------------

    def detailContent(self, ids):
        try:
            vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        except Exception:
            vid = ids
        vid = str(vid or "").strip()
        if not vid:
            return {"list": []}

        data = self._api("/api/v4/movies/" + vid)
        movie = data.get("movie") or {}
        if not movie:
            return {"list": []}

        number = movie.get("number") or vid
        title = movie.get("title") or number
        name = ("%s %s" % (number, title)).strip() if number not in title else title

        actors = ", ".join(str(a.get("name")) for a in (movie.get("actors") or []) if a.get("name"))
        tags = ", ".join(str(t.get("name")) for t in (movie.get("tags") or []) if t.get("name"))

        parts = []
        if movie.get("summary"):
            parts.append(str(movie["summary"]))
        if movie.get("maker_name"):
            parts.append("片商：%s" % movie["maker_name"])
        if movie.get("director_name"):
            parts.append("導演：%s" % movie["director_name"])
        if movie.get("series_name"):
            parts.append("系列：%s" % movie["series_name"])
        if movie.get("release_date"):
            parts.append("發行：%s" % movie["release_date"])
        if movie.get("duration"):
            parts.append("時長：%d 分鐘" % int(movie["duration"]))
        if movie.get("score"):
            parts.append("評分：%.2f（%s 人）" % (float(movie["score"]), movie.get("reviews_count") or 0))
        if tags:
            parts.append("標籤：%s" % tags)

        play_from = []
        play_url = []

        # 1. 磁力
        magnets = (self._api("/api/v1/movies/%s/magnets" % vid).get("magnets")) or []
        if magnets:
            magnets = sorted(
                magnets,
                key=lambda x: (bool(x.get("hd")), bool(x.get("cnsub")), int(x.get("size") or 0)),
                reverse=True,
            )
            eps = []
            for m in magnets:
                h = m.get("hash")
                if not h:
                    continue
                label = []
                if m.get("hd"):
                    label.append("HD")
                if m.get("cnsub"):
                    label.append("中字")
                label.append("%.1fG" % (int(m.get("size") or 0) / 1024.0))
                eps.append("%s$magnet:?xt=urn:btih:%s" % (self._clean(" ".join(label)), h))
            if eps:
                play_from.append("磁力")
                play_url.append("#".join(eps))

        # 2. JavMenu 在线播放
        eps = self._javmenu_episodes(number)
        if eps:
            play_from.append("在線")
            play_url.append(eps)

        # 3. 官方预览
        pv = movie.get("preview_video_url")
        if pv:
            play_from.append("預覽")
            play_url.append("預告片$" + self._clean(pv))

        item = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": Spider._cover(movie.get("cover_url") or movie.get("thumb_url") or ""),
            "vod_remarks": str(movie.get("release_date") or "").strip(),
            "vod_year": str(movie.get("release_date") or "")[:4],
            "vod_area": "日本",
            "vod_actor": actors,
            "vod_director": movie.get("director_name") or "",
            "vod_content": "\n".join(parts),
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        return {"list": [item]}

    def _javmenu_episodes(self, number):
        if not number:
            return ""
        code = str(number).strip().upper()
        url = "%s/zh/%s" % (JAVMENU_BASE, code)
        html = self._html(url, referer=JAVMENU_BASE + "/")
        if not html:
            return ""
        found = re.findall(r'https?://[^"\'\s<>\\]+\.m3u8[^"\'\s<>\\]*', html)
        uniq = []
        for u in found:
            u = u.replace("\\/", "/").replace("&amp;", "&")
            if u not in uniq:
                uniq.append(u)
        if not uniq:
            return ""
        return self._clean("正片") + "$" + uniq[0]

    # ---------------- 播放 ----------------

    def _fetch_m3u8(self, url):
        """抓取 m3u8，返回 (text, final_url)；cdn2020 节点不可用时自动换镜像节点。"""
        referer = JAVMENU_BASE + "/" if ("javmenu" in url or "cdn2020" in url or "xbv2608" in url) else (API_BASE + "/")
        headers = {"User-Agent": UA, "Referer": referer}
        text = self._try_m3u8(url, headers)
        if text:
            return text, url
        m = re.match(r"^(https?://)(t\d+)\.(cdn2020\.com)(/.*)$", url)
        if m:
            scheme, _, host, path = m.group(1), m.group(2), m.group(3), m.group(4)
            for node in ("t33", "t36", "t35", "t30", "t31"):
                alt = "%s%s.%s%s" % (scheme, node, host, path)
                text = self._try_m3u8(alt, headers)
                if text:
                    return text, alt
        return "", url

    def _try_m3u8(self, url, headers):
        try:
            r = self.session.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code == 200 and "#EXTM3U" in r.text:
                return r.text
        except Exception:
            pass
        try:
            import warnings
            warnings.filterwarnings("ignore")
            r = self.session.get(url, headers=headers, timeout=TIMEOUT, verify=False)
            if r.status_code == 200 and "#EXTM3U" in r.text:
                return r.text
        except Exception:
            pass
        return ""

    @staticmethod
    def _resolve_master(content, base_url):
        """master playlist（含相对 variant）→ 选最高带宽 variant 的绝对 URL"""
        if "#EXT-X-STREAM-INF" not in content:
            return base_url
        lines = content.splitlines()
        chosen = None
        best = -1
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                m = re.search(r"BANDWIDTH=(\d+)", line)
                bw = int(m.group(1)) if m else 0
                if bw >= best and i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith("#"):
                    chosen, best = lines[i + 1].strip(), bw
        if not chosen:
            return base_url
        if chosen.startswith("http"):
            return chosen
        # 相对路径：/ 开头基于站点根，否则基于 m3u8 所在目录
        m = re.match(r"^(https?://[^/]+)", base_url)
        host = m.group(1) if m else ""
        if chosen.startswith("/"):
            return host + chosen
        return base_url.rsplit("/", 1)[0] + "/" + chosen

    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if not url:
            return {"parse": 0, "url": ""}

        if url.startswith("magnet:"):
            return {"parse": 0, "url": url, "playUrl": ""}

        if ".m3u8" in url or url.startswith("http"):
            header = {"User-Agent": UA}
            if url.startswith(API_BASE):
                header["Referer"] = API_BASE + "/"
            else:
                header["Referer"] = JAVMENU_BASE + "/"
            # master playlist → 解析出真实分片索引；原节点失效 → 换镜像节点
            if ".m3u8" in url:
                body, final_url = self._fetch_m3u8(url)
                if not body:
                    return {"parse": 0, "jx": 0, "url": url, "header": header}
                if "#EXT-X-STREAM-INF" in body:
                    real = self._resolve_master(body, final_url)
                    if real and real != url:
                        header["Referer"] = JAVMENU_BASE + "/"
                        return {"parse": 0, "jx": 0, "url": real, "header": header}
                if final_url != url:
                    header["Referer"] = JAVMENU_BASE + "/"
                    return {"parse": 0, "jx": 0, "url": final_url, "header": header}
            return {"parse": 0, "jx": 0, "url": url, "header": header}

        return {"parse": 0, "url": url}

    def isVideoFormat(self, url):
        u = str(url or "").lower()
        return any(u.endswith(x) for x in (".m3u8", ".mp4", ".mpd", ".mkv", ".flv"))

    def manualVideoCheck(self):
        return False

    def liveContent(self, url):
        return {"class": [], "list": []}

    def localProxy(self, param):
        return [404, "text/plain", "not found"]

    def action(self, action):
        return {"msg": "AvPlay 源"}
