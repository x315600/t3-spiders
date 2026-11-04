# 网站名称: iQQTV (iqqab.com)
# 网站特征: 自定义站·登录取链·IP绑定会话·分段token·分页num参数·搜索kw参数
# m3u8特征: 无m3u8、直链.3gp、CDN签名18分钟过期、播放只带UA不带Referer
# 最后验证: 2026-11-04 + 列表/分类/搜索/详情实测通过，播放需盒端填账号后实测
import json
import re
from urllib.parse import quote

import requests
from lxml import etree

try:
    from base.spider import Spider as SpiderBase
except ImportError:
    class SpiderBase(object):
        pass

HOST = "https://iqqab.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
BLOCKED_WORDS = ["萝莉", "蘿莉", "ロリ", "loli", "幼女", "幼齿", "幼齒",
                 "小学生", "初中生", "中学生", "高中生", "未成年", "未满", "女童", "童女"]
QUALITIES = [2, 3, 4, 1, 5]
CLASSES = [
    ("free", "免费", "/cn/?cat=free", "single"),
    ("jp", "日本", "/cn/search.php?order=latest&s_type=jp&s_tid=37", "search"),
    ("scate", "无码", "/cn/search.php?order=latest&s_type=scate&s_tid=35", "search"),
    ("us", "欧美", "/cn/search.php?order=latest&s_type=us&s_tid=33", "search"),
    ("tag9", "中文", "/cn/search.php?s_type=tag&s_tid=9", "search"),
    ("event", "独家", "/cn/search.php?order=latest&s_type=event&s_tid=17", "search"),
    ("cate2", "衣着", "/cn/category.php?cate=2", "cate"),
    ("cate3", "职业", "/cn/category.php?cate=3", "cate"),
    ("cate4", "身材", "/cn/category.php?cate=4", "cate"),
    ("cate5", "身份", "/cn/category.php?cate=5", "cate"),
    ("cate6", "动作", "/cn/category.php?cate=6", "cate"),
]
ORDER_FILTER = [{"n": "全部", "v": ""}, {"n": "近期更新", "v": "latest"},
                {"n": "抢先发行", "v": "released"}, {"n": "週排行", "v": "weekhot"},
                {"n": "月排行", "v": "monthhot"}]


def blocked(text):
    t = (text or "").lower()
    return any(w in t for w in BLOCKED_WORDS)


class Spider(SpiderBase):
    def __init__(self):
        self.extend = ""
        self.username = ""
        self.password = ""
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})

    def getName(self):
        return "iQQTV"

    def getDependence(self):
        return []

    def init(self, extend=""):
        self.extend = extend or ""
        ext = self.extend
        if isinstance(ext, str) and ext.strip():
            try:
                ext = json.loads(ext)
            except Exception:
                m = re.match(r"^\s*([^:&\s]+)\s*[:&]\s*(\S+)\s*$", ext)
                ext = {"username": m.group(1), "password": m.group(2)} if m else {}
        if isinstance(ext, dict):
            self.username = str(ext.get("username") or ext.get("user") or "")
            self.password = str(ext.get("password") or ext.get("pass") or "")

    def _get(self, url, headers=None):
        try:
            r = self.s.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                return ""
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception:
            return ""

    def _fix(self, url):
        u = (url or "").strip()
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/"):
            return HOST + u
        return u

    def _parse_list(self, html):
        out, seen = [], set()
        if not html:
            return out
        try:
            tree = etree.HTML(html)
        except Exception:
            return out
        for a in tree.xpath('//a[contains(@href, "player.php?uuid=")]'):
            try:
                href = a.get("href") or ""
                m = re.search(r"uuid=([A-Za-z0-9_-]+)&cat=(\d+)", href)
                if not m:
                    continue
                uuid, cat = m.group(1), m.group(2)
                if uuid in seen:
                    continue
                seen.add(uuid)
                imgs = a.xpath(".//img")
                title, pic = "", ""
                if imgs:
                    title = (imgs[0].get("alt") or "").strip()
                    pic = imgs[0].get("src") or imgs[0].get("data-src") or ""
                if not title:
                    t2 = a.xpath("ancestor::div[1]//a[@class='ga_name']/@title")
                    title = t2[0].strip() if t2 else ""
                if not title or blocked(title):
                    continue
                dur = a.xpath("ancestor::div[1]//span[contains(@class,'video-time')]/text()")
                out.append({"vod_id": "%s@%s" % (uuid, cat),
                            "vod_name": title.replace("$", "＄").replace("#", "＃"),
                            "vod_pic": self._fix(pic),
                            "vod_remarks": (dur[0].strip() if dur else "")})
            except Exception:
                continue
        return out

    def _pagecount(self, html, pg, lst):
        m = re.search(r"共(\d+)部", html or "")
        if m:
            total = int(m.group(1))
            return max(1, (total + 47) // 48)
        return pg + 1 if len(lst) >= 48 else pg

    def homeContent(self, filter=None):
        classes = [{"type_id": tid, "type_name": name} for tid, name, _, _ in CLASSES]
        filters = {}
        for tid, _, _, kind in CLASSES:
            if kind in ("search", "single"):
                filters[tid] = [{"key": "order", "name": "排序", "value": ORDER_FILTER}]
        return {"class": classes, "list": self.homeVideoContent()["list"], "filters": filters}

    def homeVideoContent(self):
        return {"list": self._parse_list(self._get(HOST + "/cn/?cat=free"))}

    def _list_url(self, tid, pg, extend):
        for cid, _, path, kind in CLASSES:
            if cid != tid:
                continue
            if kind == "single":
                return HOST + path, 1
            url = HOST + path
            order = (extend or {}).get("order") or ""
            if kind == "search" and order:
                url = re.sub(r"order=[^&]*", "order=" + order, url) if "order=" in url else url + "&order=" + order
            sep = "&" if "?" in url else "?"
            return "%s%snum=%d" % (url, sep, pg), 0
        return HOST + "/cn/?cat=free", 1

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        extend = extend or {}
        if isinstance(extend, str):
            try:
                extend = json.loads(extend)
            except Exception:
                extend = {}
        url, single = self._list_url(str(tid), pg, extend)
        if single and pg > 1:
            return {"list": [], "page": pg, "pagecount": 1, "limit": 48, "total": 0}
        html = self._get(url)
        lst = self._parse_list(html)
        pc = 1 if single else self._pagecount(html, pg, lst)
        return {"list": lst, "page": pg, "pagecount": pc, "limit": 48, "total": 0}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        url = "%s/cn/search.php?cat=hd&kw_type=key&kw=%s&num=%d" % (HOST, quote(key or ""), pg)
        html = self._get(url)
        lst = self._parse_list(html)
        return {"list": lst, "page": pg, "pagecount": self._pagecount(html, pg, lst),
                "limit": 48, "total": 0}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = str(vid or "").strip()
        if not vid:
            return {"list": []}
        uuid, cat = (vid.split("@") + ["19"])[:2] if "@" in vid else (vid, "19")
        html = self._get("%s/cn/player.php?uuid=%s&cat=%s" % (HOST, uuid, cat))
        if not html:
            return {"list": []}
        try:
            tree = etree.HTML(html)
        except Exception:
            return {"list": []}
        og_title = tree.xpath('//meta[@property="og:title"]/@content')
        title = (og_title[0].split("|")[0].strip() if og_title else "")
        og_pic = tree.xpath('//meta[@property="og:image"]/@content')
        kw = tree.xpath('//meta[@name="keywords"]/@content')
        tags = (kw[0] if kw else "")
        if blocked(title) or blocked(tags):
            return {"list": []}
        links = set(a.strip() for a in tree.xpath('//a[contains(@href,"s_type=actor")]/text()'))
        toks = title.split()
        actor = ""
        if len(toks) >= 2 and re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+", toks[-1]) and toks[-2] in links:
            actor = toks[-2]
        segs = tree.xpath('//li[contains(@class,"vdplay01")]')
        eps = []
        for li in segs:
            idx = li.get("index") or str(len(eps) + 1)
            eps.append("第%s段$%s@%s@%s" % (idx, uuid, cat, idx))
        if not eps:
            return {"list": []}
        return {"list": [{"vod_id": vid,
                          "vod_name": title.replace("$", "＄").replace("#", "＃"),
                          "vod_pic": self._fix(og_pic[0] if og_pic else ""),
                          "vod_actor": actor,
                          "vod_tag": ",".join([t.strip() for t in tags.split(",") if t.strip()][:10]),
                          "vod_play_from": "720p",
                          "vod_play_url": "#".join(eps)}]}

    def _login_session(self):
        s = requests.Session()
        s.headers.update({"User-Agent": UA})
        try:
            r = s.post(HOST + "/cn/ajax/site/login.php?method=post",
                       data={"username": self.username, "password": self.password}, timeout=20)
            ok = r.status_code == 200 and r.json().get("code") == 0
        except Exception:
            ok = False
        return s if ok else None

    def playerContent(self, flag, id, vipFlags=None):
        pid = id[0] if isinstance(id, (list, tuple)) else id
        parts = str(pid or "").split("@")
        if len(parts) < 3:
            return {"parse": 0, "url": ""}
        uuid, cat, seg_idx = parts[0], parts[1], parts[2]
        if not self.username or not self.password:
            return {"parse": 0, "url": ""}
        s = self._login_session()
        if s is None:
            return {"parse": 0, "url": ""}
        try:
            r = s.get("%s/cn/player.php?uuid=%s&cat=%s" % (HOST, uuid, cat), timeout=20)
            r.encoding = r.apparent_encoding or "utf-8"
            html = r.text if r.status_code == 200 else ""
        except Exception:
            return {"parse": 0, "url": ""}
        try:
            tree = etree.HTML(html)
        except Exception:
            return {"parse": 0, "url": ""}
        og_title = tree.xpath('//meta[@property="og:title"]/@content')
        kw = tree.xpath('//meta[@name="keywords"]/@content')
        if blocked(og_title[0] if og_title else "") or blocked(kw[0] if kw else ""):
            return {"parse": 0, "url": ""}
        seg = None
        for li in tree.xpath('//li[contains(@class,"vdplay01")]'):
            if (li.get("index") or "") == seg_idx:
                seg = li
                break
        if seg is None:
            return {"parse": 0, "url": ""}
        ref = "%s/cn/player.php?uuid=%s&cat=%s" % (HOST, uuid, cat)
        for q in QUALITIES:
            url = ("%s/cn/subpage/getMv.php?num=%s&ms_id=%s&file_prefix=&start=%s&end=%s"
                   "&cat_id=%s&quality=%d&token=%s&playall=0" % (
                       HOST, uuid, seg.get("ms_id") or "o10", seg.get("start") or "0",
                       seg.get("end") or "600", seg.get("cat_id") or "3", q,
                       seg.get("token") or ""))
            try:
                r = s.get(url, headers={"Referer": ref}, timeout=20)
                j = r.json() if r.status_code == 200 else {}
            except Exception:
                j = {}
            if j.get("loginFail"):
                return {"parse": 0, "url": ""}
            vurl = j.get("vurl") or ""
            if vurl:
                return {"parse": 0, "url": vurl, "header": {"User-Agent": UA}}
            hls = j.get("vurl_hls") or ""
            if hls:
                return {"parse": 0, "url": hls, "header": {"User-Agent": UA}}
        return {"parse": 0, "url": ""}
