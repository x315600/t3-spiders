# 网站名称: 简云影视 (jianyunys.com 301 至 jisuzhuiju.com)
# 网站特征: 自定义站·无加密·requests·/filter?channel 分页·/search?keyword 搜索·/api/play-url 服务端解析
# m3u8特征: 有m3u8、服务端解析直链、签名 auth_key、native 直链 / iframe 线路
# 最后验证: 2026-10-31 沙盒实测
try:
    from base.spider import Spider as SpiderBase
except ImportError:
    class SpiderBase(object):
        def __init__(self):
            self.extend = ""
        def getCache(self, k):
            return ""
        def setCache(self, k, v):
            pass
        def delCache(self, k):
            pass
import json
import re
from urllib.parse import quote
from lxml import etree
import requests


class Spider(SpiderBase):
    host = "https://jisuzhuiju.com"
    header = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    classes = [("电视剧", "1"), ("电影", "2"), ("动漫", "3"), ("综艺", "4"), ("短剧", "5")]
    types = {
        "1": [("7", "剧情"), ("9", "古装"), ("10", "战争"), ("11", "谍战"), ("12", "爱情"), ("13", "罪案"), ("14", "悬疑"), ("15", "家庭"), ("16", "军旅"), ("17", "喜剧"), ("18", "都市"), ("19", "武侠"), ("20", "言情"), ("21", "偶像"), ("22", "青春"), ("23", "农村"), ("24", "穿越"), ("25", "奇幻"), ("26", "历史"), ("27", "年代"), ("28", "科幻"), ("29", "生活"), ("30", "剧情"), ("31", "励志"), ("32", "婚姻"), ("33", "警匪"), ("34", "犯罪"), ("35", "推理"), ("36", "商战"), ("37", "宫廷"), ("38", "仙侠"), ("39", "神话"), ("40", "动作"), ("41", "复仇"), ("42", "惊悚")],
        "2": [("43", "动作"), ("44", "喜剧"), ("45", "爱情"), ("46", "科幻"), ("47", "恐怖"), ("48", "剧情"), ("49", "战争"), ("50", "犯罪"), ("51", "惊悚"), ("52", "冒险"), ("53", "悬疑"), ("54", "动画"), ("55", "武侠"), ("56", "古装"), ("57", "历史"), ("58", "传记"), ("59", "纪录片")],
        "3": [("60", "热血"), ("61", "恋爱"), ("62", "校园"), ("63", "搞笑"), ("64", "机甲"), ("65", "神魔"), ("66", "竞技"), ("67", "冒险"), ("68", "治愈"), ("69", "百合"), ("70", "萝莉"), ("71", "后宫"), ("72", "励志"), ("73", "泡面番"), ("74", "国产动漫"), ("75", "日本动漫"), ("76", "欧美动漫")],
        "4": [("77", "选秀"), ("78", "情感"), ("79", "访谈"), ("80", "播报"), ("81", "旅游"), ("82", "音乐"), ("83", "美食"), ("84", "纪实"), ("85", "曲艺"), ("86", "游戏"), ("87", "亲子"), ("88", "职场"), ("89", "脱口秀"), ("90", "真人秀"), ("91", "晚会")],
        "5": [],
    }
    areas = ["大陆", "香港", "台湾", "韩国", "日本", "美国", "泰国", "法国", "英国", "德国", "印度", "其他"]
    years = ["2026", "2025", "2024", "2023", "2022", "2021", "2020", "2019", "2018", "-1"]
    sorts = [("hot", "最热"), ("time", "最新"), ("score", "评分")]

    def __init__(self):
        super().__init__()
        self.s = requests.Session()
        self.s.headers.update(self.header)

    def getName(self):
        return "简云影视"

    def init(self, extend=""):
        pass

    def _get(self, url, referer=""):
        h = dict(self.header)
        if referer:
            h["Referer"] = referer
        r = self.s.get(url, headers=h, timeout=20)
        r.encoding = "utf-8"
        return r.text

    def _tree(self, html):
        return etree.HTML(html)

    def _clean(self, s):
        return (s or "").replace("$", "＄").replace("#", "＃").strip()

    def _parse_cards(self, tree):
        lst, seen = [], set()
        for a in tree.xpath('//a[contains(@href,"/detail/") and .//img]'):
            try:
                href = a.get("href") or ""
                m = re.search(r"/detail/(\d+)\.html", href)
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                img = a.xpath('.//img')[0]
                pic = img.get("src") or ""
                if pic.startswith("data:"):
                    pic = ""
                name = ""
                t = a.xpath('.//p[contains(@class,"vod-title")]/text()')
                if t:
                    name = t[0].strip()
                if not name:
                    name = (img.get("alt") or "").replace("封面图片", "").strip()
                remarks = ""
                b = a.xpath('.//span[contains(@class,"vod-badge")]/text()')
                if b:
                    remarks = b[0].strip()
                if not remarks:
                    st = a.xpath('.//p[contains(@class,"vod-subtitle")]/text()')
                    if st:
                        remarks = st[0].strip()
                lst.append({"vod_id": m.group(1), "vod_name": self._clean(name), "vod_pic": pic, "vod_remarks": self._clean(remarks)})
            except Exception:
                continue
        return lst

    def _filters(self):
        f = {}
        for _, tid in self.classes:
            groups = [{"key": "type", "name": "类型", "value": [{"n": "全部", "v": ""}] + [{"n": n, "v": v} for v, n in self.types.get(tid, [])]}]
            groups.append({"key": "area", "name": "地区", "value": [{"n": "全部", "v": ""}] + [{"n": a, "v": a} for a in self.areas]})
            groups.append({"key": "year", "name": "年份", "value": [{"n": "全部", "v": ""}] + [{"n": ("其他" if y == "-1" else y), "v": y} for y in self.years]})
            groups.append({"key": "sort", "name": "排序", "value": [{"n": n, "v": v} for v, n in self.sorts]})
            f[tid] = groups
        return f

    def homeContent(self, filter=None):
        try:
            tree = self._tree(self._get(self.host + "/"))
            lst = self._parse_cards(tree)
        except Exception:
            lst = []
        return {"class": [{"type_id": tid, "type_name": name} for name, tid in self.classes], "list": lst, "filters": self._filters()}

    def homeVideoContent(self):
        try:
            tree = self._tree(self._get(self.host + "/"))
            lst = self._parse_cards(tree)
        except Exception:
            lst = []
        return {"list": lst}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        pg = int(pg) if pg else 1
        ext = extend or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except Exception:
                ext = {}
        q = {"channel": tid, "type": ext.get("type") or "", "area": ext.get("area") or "", "year": ext.get("year") or "", "sort": ext.get("sort") or "hot", "page": pg}
        url = self.host + "/filter?" + "&".join(f"{k}={quote(str(v))}" for k, v in q.items())
        try:
            tree = self._tree(self._get(url))
            lst = self._parse_cards(tree)
            pages = [int(x) for x in re.findall(r"page=(\d+)", " ".join(tree.xpath('//a[contains(@href,"page=")]/@href'))) if x.isdigit()]
            pagecount = max(pages) if pages else (pg + 1 if len(lst) >= 12 else pg)
        except Exception:
            lst, pagecount = [], pg
        return {"page": pg, "pagecount": max(1, pagecount), "list": lst, "count": len(lst), "limit": 12, "total": 0}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = str(vid).split("@@")[0]
        try:
            html = self._get(f"{self.host}/detail/{vid}.html")
            tree = self._tree(html)
        except Exception:
            return {"list": []}
        name = ""
        h1 = tree.xpath('//h1/text()')
        if h1:
            name = h1[0].strip()
        pic = ""
        for img in tree.xpath('//img'):
            if "海报" in (img.get("alt") or ""):
                pic = img.get("src") or ""
                break
        if not pic:
            imgs = tree.xpath('//img/@src')
            pic = imgs[0] if imgs else ""
        desc = ""
        syns = ["".join(x.itertext()).strip() for x in tree.xpath('//*[contains(@class,"synopsis")]')]
        syns = [s for s in syns if len(s) > 20]
        if syns:
            desc = max(syns, key=len)
        if not desc:
            m = re.search(r'"@type":"TVSeries".*?"description":"(.*?)"', html, re.S)
            if m:
                desc = m.group(1)
        tabs = tree.xpath('//button[contains(@class,"source-tab")]')
        panels = tree.xpath('//*[starts-with(@id,"source-")]')
        play_from, play_url = [], []
        for tab, panel in zip(tabs, panels):
            line = (tab.text or "").strip()
            eps = []
            for a in panel.xpath('.//a[contains(@href,"/vodplay/")]'):
                href = a.get("href") or ""
                m = re.search(r"/vodplay/(\d+)-([^-]+)-(\d+)\.html", href)
                if not m:
                    continue
                epname = self._clean((a.text or "").strip()) or f"第{m.group(3)}集"
                eps.append(f"{epname}${m.group(1)}@@{m.group(2)}@@{m.group(3)}")
            if eps:
                play_from.append(self._clean(line))
                play_url.append("#".join(eps))
        vod = {"vod_id": vid, "vod_name": self._clean(name), "vod_pic": pic, "vod_content": self._clean(desc), "vod_play_from": "$$$".join(play_from), "vod_play_url": "$$$".join(play_url)}
        return {"list": [vod]}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            tree = self._tree(self._get(self.host + "/search?keyword=" + quote(key)))
        except Exception:
            return {"list": []}
        lst, seen = [], set()
        for item in tree.xpath('//div[contains(@class,"search-item")]'):
            try:
                a = item.xpath('.//a[contains(@href,"/detail/")]')[0]
                m = re.search(r"/detail/(\d+)\.html", a.get("href") or "")
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                title = item.xpath('.//a[contains(@class,"search-item-title")]/text()')
                img = item.xpath('.//img/@src')
                typ = item.xpath('.//span[contains(@class,"search-item-type")]/text()')
                metas = [x.strip() for x in item.xpath('.//span[contains(@class,"search-meta-value")]/text()') if x.strip()]
                lst.append({"vod_id": m.group(1), "vod_name": self._clean(title[0]) if title else "", "vod_pic": img[0] if img else "", "vod_remarks": self._clean(" · ".join(([typ[0].strip()] if typ else []) + metas))})
            except Exception:
                continue
        return {"list": lst}

    def playerContent(self, flag, id, vipFlags=None):
        pid = id[0] if isinstance(id, (list, tuple)) else id
        parts = str(pid).split("@@")
        if len(parts) != 3:
            return {"parse": 0, "url": ""}
        vid, slug, idx = parts
        try:
            api = f"{self.host}/api/play-url?vodId={vid}&playFrom={quote(slug)}&index={idx}"
            data = json.loads(self._get(api, referer=f"{self.host}/vodplay/{vid}-{slug}-{idx}.html"))
        except Exception:
            return {"parse": 0, "url": ""}
        if data.get("code") == 200 and data.get("url"):
            if data.get("mode") == "iframe":
                return {"parse": 1, "url": data["url"]}
            return {"parse": 0, "url": data["url"], "header": data.get("headers") or {}}
        return {"parse": 0, "url": ""}
