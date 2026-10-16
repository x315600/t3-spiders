# 网站名称: personalmv (sae8视频)
# 网站特征: UIKit·无加密·普通请求·分类4个·分页?page=N·搜索/cn/search?q=
# m3u8特征: 有·/api/getmovie?type=640&id=取m3u8·AES-128加密·CDN分片·480P/2K双档
# 最后验证: 2026-10-14 · 分类/搜索/详情/播放真实验证
# 2K能力: 2026-10-14 · init(ext) 传 hls cookie 即解锁 2K 源（待实机验证，cookie 到位即测）

try:
    from base.spider import Spider as _SpiderBase
except ImportError:
    class _SpiderBase(object):
        def getCache(self): return {}
        def setCache(self, c): pass
        def delCache(self): pass

import re
import json
from urllib.parse import quote
try:
    from lxml import etree
    _HAS_LXML = True
except ImportError:
    _HAS_LXML = False


class Spider(_SpiderBase):
    def __init__(self):
        self.host = "https://personalmv.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": self.host + "/",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self.cats = [
            ("自拍黑料吃瓜", "自拍黑料吃瓜"),
            ("情色ai", "情色ai"),
            ("AV精选", "AV精选"),
            ("稀缺资源", "稀缺资源"),
        ]
        # 2K 登录态：ext 传 hls cookie（运行时传入，绝不写入源/日志）
        self._hls = ""

    def init(self, extend=""):
        # ext 形态：裸 cookie 值 / "hls=xxx" / '{"hls":"xxx"}'
        try:
            ext = str(extend or "").strip()
            if ext.startswith("{"):
                self._hls = str(json.loads(ext).get("hls", "")).strip()
            elif ext:
                self._hls = ext
            if self._hls and "=" not in self._hls:
                self._hls = "hls=" + self._hls
        except Exception:
            self._hls = ""

    def getName(self):
        return "personalmv"

    def getDependence(self):
        return []

    def _get(self, url, headers=None, with_cookie=False):
        import requests
        h = dict(self.headers)
        if headers:
            h.update(headers)
        if with_cookie and self._hls:
            h["Cookie"] = self._hls
        try:
            r = requests.get(url, headers=h, timeout=12)
            if r.status_code < 400:
                return r.text
        except Exception:
            pass
        return ""

    def _get_json(self, url, headers=None, with_cookie=False):
        try:
            return json.loads(self._get(url, headers, with_cookie))
        except Exception:
            return {}

    def _fix(self, u):
        if not u:
            return ""
        u = u.strip()
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("http"):
            return u
        if u.startswith("/"):
            return self.host + u
        return self.host + "/" + u

    def _parse_cards(self, html):
        out, seen = [], set()
        if _HAS_LXML:
            try:
                root = etree.HTML(html)
                for card in root.xpath('//div[contains(@class,"movie-card")]'):
                    try:
                        a = card.xpath('.//a[starts-with(@href,"/cn/movie/")]')[0]
                        href = a.get("href", "")
                        img = a.xpath('.//img')[0] if a.xpath('.//img') else None
                        name = (img.get("alt", "") if img is not None else "").strip()
                        pic = self._fix(img.get("src", "") if img is not None else "")
                        if not href or href in seen:
                            continue
                        seen.add(href)
                        out.append({"vod_id": href, "vod_name": name, "vod_pic": pic, "vod_remarks": ""})
                    except Exception:
                        continue
                if out:
                    return out
            except Exception:
                pass
        for m in re.finditer(r'<a href="(/cn/movie/[a-f0-9]+)">.*?<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)"', html, re.S):
            href, pic, name = m.group(1), m.group(2), m.group(3).strip()
            if href in seen:
                continue
            seen.add(href)
            out.append({"vod_id": href, "vod_name": name, "vod_pic": self._fix(pic), "vod_remarks": ""})
        return out

    def _pagecount(self, html):
        m = re.search(r'href="[^"]*\?[^"]*page=(\d+)"', html)
        pages = [int(x) for x in re.findall(r'[?&]page=(\d+)', html)]
        return max(pages) if pages else 1

    def homeContent(self, filter=None):
        classes = [{"type_id": quote(t, safe=""), "type_name": n} for t, n in self.cats]
        html = self._get(self.host + "/cn")
        return {"class": classes, "list": self._parse_cards(html)[:24], "filters": {}}

    def homeVideoContent(self):
        return {"list": self._parse_cards(self._get(self.host + "/cn"))[:24]}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        tid = str(tid)
        try:
            from urllib.parse import unquote
            cat = unquote(tid)
        except Exception:
            cat = tid
        url = "%s/cn/category/%s?page=%d" % (self.host, quote(cat, safe=""), pg)
        html = self._get(url)
        items = self._parse_cards(html)
        return {"list": items, "page": pg, "pagecount": self._pagecount(html), "limit": 36, "total": len(items)}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = str(vid or "").strip()
        url = self._fix(vid)
        html = self._get(url)
        if not html:
            return {"list": []}
        vod_id = re.search(r'/cn/movie/([a-f0-9]+)', url)
        mid = vod_id.group(1) if vod_id else ""
        name, pic, remarks = "", "", ""
        if _HAS_LXML:
            try:
                root = etree.HTML(html)
                h1 = root.xpath('//h1')
                name = "".join(h1[0].itertext()).strip() if h1 else ""
                og = root.xpath('//meta[@property="og:image"]/@content')
                pic = self._fix(og[0]) if og else ""
            except Exception:
                pass
        if not name:
            m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
            name = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ""
        if not pic:
            m = re.search(r'property="og:image" content="([^"]+)"', html)
            pic = self._fix(m.group(1)) if m else ""
        if not pic:
            m = re.search(r'src="(https?://[^"]*?/videos/[^"]*?/poster\d*\.webp)"', html) or re.search(r'src="(https?://[^"]*?/videos/[^"]*?/0\.webp)"', html)
            pic = m.group(1) if m else ""
        m = re.search(r'(\d+:\d+)', html)
        remarks = m.group(1) if m else ""
        tags = []
        if _HAS_LXML:
            try:
                root = etree.HTML(html)
                tags = [t.strip() for t in root.xpath('//a[starts-with(@href,"/cn/tag/")]/text()') if t.strip() and len(t.strip()) < 20]
            except Exception:
                pass
        vod = {
            "vod_id": vid, "vod_name": name, "vod_pic": pic,
            "vod_year": "", "vod_area": "", "vod_actor": "",
            "vod_director": "", "vod_remarks": remarks,
            "vod_content": " ".join(tags[:12]),
        }
        if mid:
            if self._hls:
                # 登录态已配置：同时给出 2K 源
                vod["vod_play_from"] = "480P$$$2K"
                vod["vod_play_url"] = "正片$%s$$$正片$%s" % (mid, mid)
            else:
                vod["vod_play_from"] = "480P"
                vod["vod_play_url"] = "正片$%s" % mid
        return {"list": [vod]}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        url = "%s/cn/search?q=%s&page=%d" % (self.host, quote(str(key)), pg)
        html = self._get(url)
        items = self._parse_cards(html)
        return {"list": items, "page": pg, "pagecount": self._pagecount(html), "limit": 36, "total": len(items)}

    def playerContent(self, flag, id, vipFlags=None):
        pid = str(id or "").strip()
        if "|" in pid:
            pid = pid.split("|", 1)[0]
        if not pid:
            return {"parse": 0, "url": ""}
        qtype = "2560" if str(flag) == "2K" else "640"
        api = "%s/api/getmovie?type=%s&id=%s" % (self.host, qtype, pid)
        data = self._get_json(api, {"Referer": self.host + "/cn/movie/" + pid},
                              with_cookie=(qtype == "2560"))
        m3u8 = data.get("m3u8", "") if isinstance(data, dict) else ""
        if not m3u8:
            return {"parse": 0, "url": ""}
        header = dict(self.headers)
        if qtype == "2560" and self._hls:
            # 2K 分片拉取需要登录态，cookie 随播放头下发（运行时内存，不落盘）
            header["Cookie"] = self._hls
        return {"parse": 0, "url": self._fix(m3u8), "header": header}
