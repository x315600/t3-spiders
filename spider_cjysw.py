# -*- coding: utf-8 -*-
# 站点：初见影视 https://cjysw.cc（配置 key=cjysw） 类型：MacCMS v10 伪静态 + mxpro 模板
# 要点（2026-11-10 沙盒实测）：
#  ① 列表/详情/搜索全是 HTML，无登录/付费墙；采集接口 api.php/provide/vod/ 已关闭（返回 "closed"），
#     所以不用 api 模板，走 HTML 解析；首页 200，分类/搜索/详情/播放页均 200。
#  ② 分类：7 个一级（Netflix 60、电影 1、剧集 15、动漫 30、综艺 24、纪录片 63、短剧 47），
#     每页 45 条；分类 URL：/vodshow/{tid}-{area}-{by}--{lang}-{letter}---{pg}---{year}.html，
#     空段 = 全部；第 1 页与第 2 页零重叠；电影（tid=1）尾页 238。
#  ③ 筛选：地区/年份/语言/字母/排序/子类型 6 个维度实测生效（大陆+2026 一组返回 45 条）；
#     "全部" 的 v 用空串——vodshow 空段即全部，填 tid 反而会错进地区段。
#  ④ 搜索：/vodsearch/{quote(wd)}-------------.html（第 1 页 13 横线），
#     第 N 页：/vodsearch/{quote(wd)}----------{pg}---.html（10 横线）；每页 16 条，
#     "爱" 的尾页 92；搜索页与分类页尾页链接横线数不同，取尾页页码用横线数无关的正则。
#  ⑤ 详情：线路 tab 在 data-dropdown-value，集列表在 id 以 panel 开头的 module-list；
#     自营* 线是站外嵌页（iqiyi 等）只能 parse:1 嗅探，必须排最后，直链线在前。
#  ⑥ 播放：播放页 var player_aaaa={...}，encrypt 0 直取 url（实测为直链 m3u8，
#     如 https://cdn.yzzyvip-29.com/.../index.m3u8）；encrypt 1 unquote、2 base64 后 unquote。
#     直链回 parse:0，非直链回 parse:1 交给壳嗅探。CDN 有地域限制，沙盒曾见 403。
#  ⑦ 图片：封面 data-original/src 绝对地址直连，无防盗链；占位 load.gif 过滤。
# profile：legacy（老壳：继承 base.spider，self.fetch，超时单位秒，Python 3.8 语法）
import base64
import json
import re
import sys
import time
from urllib.parse import parse_qsl, quote, unquote

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:  # 无 base.spider 的壳 / 本地调试用最小桩；OK影视等按 base.spider 反射检查，所以优先继承真基类
    class BaseSpider(object):
        def getProxyUrl(self, local=True):
            return "http://127.0.0.1:9978/proxy?do=py"

        def fetch(self, url, params=None, headers=None, timeout=10, **kw):
            import requests
            r = requests.get(url, params=params, headers=headers, timeout=timeout, **kw)
            r.encoding = "utf-8"
            return r

        def post(self, url, params=None, data=None, json=None, headers=None, timeout=10, **kw):
            import requests
            r = requests.post(url, params=params, data=data, json=json, headers=headers, timeout=timeout, **kw)
            r.encoding = "utf-8"
            return r

try:
    import requests
except Exception:
    requests = None  # OK 壳可能没有 requests → _request 内降级 urllib

from lxml import etree

# ---------------- CONFIG ----------------
HOST = "https://cjysw.cc"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
PAGE_SIZE = 45        # 分类每页 45 条（实测）
SEARCH_PAGE_SIZE = 16  # 搜索每页 16 条（实测）
TIMEOUT = 15          # 秒
RETRY = 2             # 有限次重试

CATEGORIES = [
    {"type_id": "60", "type_name": "Netflix"},
    {"type_id": "1", "type_name": "电影"},
    {"type_id": "15", "type_name": "剧集"},
    {"type_id": "30", "type_name": "动漫"},
    {"type_id": "24", "type_name": "综艺"},
    {"type_id": "63", "type_name": "纪录片"},
    {"type_id": "47", "type_name": "短剧"},
]

# 子类型：子类型名 -> 子分类 tid（vodshow 第一段用子 tid）
TYPE_MAP = {
    "1": {"动作片": "2", "喜剧片": "3", "爱情片": "4", "科幻片": "5", "恐怖片": "6", "剧情片": "7",
          "战争片": "8", "悬疑片": "10", "动画片": "11", "犯罪片": "12", "奇幻片": "13", "其他片": "67"},
    "60": {"自制剧": "61", "电影": "62"},
    "15": {"国产剧": "16", "香港剧": "17", "台湾剧": "18", "美国剧": "19", "韩国剧": "20",
           "日本剧": "21", "海外剧": "22", "泰剧": "23", "其他剧": "68"},
    "30": {"国产动漫": "31", "日韩动漫": "32", "欧美动漫": "33", "港台动漫": "34", "海外动漫": "35"},
    "24": {"大陆综艺": "25", "日韩综艺": "26", "港台综艺": "27", "欧美综艺": "28", "演唱会": "29"},
    "47": {"有声动漫": "48", "女频恋爱": "49", "反转爽剧": "50", "脑洞悬疑": "51",
           "年代穿越": "52", "古装仙侠": "53", "现代都市": "54", "漫剧": "66"},
}
AREA_MAP = {
    "1": ["大陆", "香港", "台湾", "美国", "法国", "英国", "日本", "韩国", "德国", "泰国", "印度", "意大利", "西班牙", "加拿大", "其他"],
    "60": ["大陆", "香港", "台湾", "韩国", "美国", "法国", "英国", "日本", "德国", "泰国", "印度", "意大利", "西班牙", "加拿大", "其他"],
    "15": ["内地", "韩国", "香港", "台湾", "日本", "美国", "泰国", "英国", "新加坡", "其他"],
    "30": ["内地", "日本", "欧美", "其他"],
    "24": ["内地", "港台", "日韩", "欧美"],
    "63": ["大陆", "香港", "台湾", "美国", "法国", "英国", "日本", "韩国", "德国", "泰国", "印度", "意大利", "西班牙", "加拿大", "其他"],
}
LANG_MAP = {
    "1": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "法语", "德语", "其它"],
    "60": ["国语", "英语", "韩语", "日语", "法语", "德语", "其它"],
    "15": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "其它"],
    "30": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "其它"],
    "24": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "其它"],
    "63": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "法语", "德语", "其它"],
}
BY_MAP = {"时间排序": "time", "人气排序": "hits", "评分排序": "score"}


class Spider(BaseSpider):
    def getName(self):
        return "初见影视"

    def init(self, extend=""):
        # init 绝不抛错：extend 可能是 str/dict/None/非法 JSON，全接受，失败当空配置
        self.host = HOST
        try:
            cfg = self._parse_extend(extend)
            if isinstance(cfg, dict) and cfg.get("host"):
                self.host = str(cfg["host"]).rstrip("/")
        except Exception:
            pass
        self.headers = {
            "User-Agent": UA,
            "Referer": self.host + "/",
        }
        self.categories = CATEGORIES
        self.filters = self._build_filters()

    # ---------------- helpers ----------------
    def _parse_extend(self, ext):
        try:
            if ext is None:
                return {}
            if isinstance(ext, str):
                ext = ext.strip()
                return json.loads(ext) if ext else {}
            if isinstance(ext, dict):
                return ext
            return {}
        except Exception:
            return {}

    def _build_filters(self):
        # key = 一级 type_id；每行 {"key", "name", "value"}；"全部" v=""（vodshow 空段即全部）
        letters = [{"n": "全部", "v": ""}] + [{"n": c, "v": c} for c in [chr(i) for i in range(65, 91)] + ["0-9"]]
        years = [{"n": "全部", "v": ""}] + [{"n": str(y), "v": str(y)} for y in range(2026, 2013, -1)]
        bys = [{"n": "全部", "v": ""}, {"n": "时间排序", "v": "time"},
               {"n": "人气排序", "v": "hits"}, {"n": "评分排序", "v": "score"}]
        filters = {}
        for cat in CATEGORIES:
            tid = cat["type_id"]
            rows = []
            if tid in TYPE_MAP:
                rows.append({"key": "type", "name": "类型",
                             "value": [{"n": "全部", "v": ""}] + [{"n": n, "v": v} for n, v in TYPE_MAP[tid].items()]})
            if tid in AREA_MAP:
                rows.append({"key": "area", "name": "地区",
                             "value": [{"n": "全部", "v": ""}] + [{"n": a, "v": a} for a in AREA_MAP[tid]]})
            rows.append({"key": "by", "name": "排序", "value": bys})
            if tid in LANG_MAP:
                rows.append({"key": "lang", "name": "语言",
                             "value": [{"n": "全部", "v": ""}] + [{"n": a, "v": a} for a in LANG_MAP[tid]]})
            rows.append({"key": "letter", "name": "字母", "value": letters})
            rows.append({"key": "year", "name": "年份", "value": years})
            filters[tid] = rows
        return filters

    def _request(self, url, binary=False):
        # self.fetch 优先（老壳 requests 实现）；失败则 requests 直连，再降级 urllib；有限次重试
        last_err = None
        for _ in range(RETRY):
            try:
                rsp = self.fetch(url, headers=self.headers, timeout=TIMEOUT)
                if rsp is None:
                    raise RuntimeError("fetch 返回空")
                if rsp.status_code != 200:
                    raise RuntimeError("HTTP %s" % rsp.status_code)
                return rsp.content if binary else rsp.text
            except Exception as e:
                last_err = e
            try:
                if requests is not None:
                    r = requests.get(url, headers=self.headers, timeout=TIMEOUT)
                    if r.status_code == 200:
                        return r.content if binary else r.text
                    last_err = RuntimeError("HTTP %s" % r.status_code)
                else:
                    import urllib.request
                    req = urllib.request.Request(url, headers=self.headers)
                    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                        data = r.read()
                        return data if binary else data.decode("utf-8", "ignore")
            except Exception as e:
                last_err = e
            time.sleep(0.3)
        print("[cjysw] 请求失败: %s - %s" % (url, last_err))
        return None

    def _get(self, url):
        return self._request(url, binary=False)

    def _fix_url(self, u):
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/"):
            return self.host + u
        return u

    def _ext_one(self, ext, key):
        v = ext.get(key, "") if isinstance(ext, dict) else ""
        if isinstance(v, list):
            v = v[0] if v else ""
        if v is None:
            return ""
        return str(v).strip()

    def _seg(self, raw, mapping=None):
        if not raw or raw in ("全部", "all"):
            return ""
        if mapping:
            raw = mapping.get(raw, raw)
        if raw.startswith("%"):
            return raw
        return quote(raw)

    def _parse_list(self, html):
        if not html:
            return []
        tree = etree.HTML(html)
        if tree is None:
            return []
        results, seen = [], set()
        for a in tree.xpath('//a[contains(@class,"module-poster-item")]'):
            try:
                m = re.search(r"/voddetail/(\d+)\.html", a.get("href", ""))
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                img = a.xpath('.//img')
                pic = ""
                if img:
                    pic = self._fix_url(img[0].get("data-original") or img[0].get("src", ""))
                if "load.gif" in pic:
                    pic = ""
                title = (a.get("title", "") or (img[0].get("alt", "") if img else "")).strip()
                note = "".join(a.xpath('.//div[contains(@class,"module-item-note")]//text()')).strip()
                results.append({"vod_id": m.group(1), "vod_name": title, "vod_pic": pic, "vod_remarks": note})
            except Exception:
                continue
        return results

    def _parse_search_list(self, html):
        if not html:
            return []
        tree = etree.HTML(html)
        if tree is None:
            return []
        results, seen = [], set()
        for item in tree.xpath('//div[contains(@class,"module-card-item") and contains(@class,"module-item")]'):
            try:
                a = item.xpath('.//a[contains(@class,"module-card-item-poster")]')
                if not a:
                    continue
                m = re.search(r"/voddetail/(\d+)\.html", a[0].get("href", ""))
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                img = item.xpath('.//img')
                pic = ""
                if img:
                    pic = self._fix_url(img[0].get("data-original") or img[0].get("src", ""))
                if "load.gif" in pic:
                    pic = ""
                title = (img[0].get("alt", "") if img else "").strip()
                note = "".join(item.xpath('.//div[contains(@class,"module-item-note")]//text()')).strip()
                results.append({"vod_id": m.group(1), "vod_name": title, "vod_pic": pic, "vod_remarks": note})
            except Exception:
                continue
        return results

    def _pagecount(self, html, pg):
        # 尾页链接：分类 8 横线、搜索 10 横线，用横线数无关的正则；拿不到真值按本页是否满页推断
        if html:
            m = re.search(r'-(\d+)---\.html"[^>]*>尾页', html)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    pass
        return pg

    # ---------------- entries ----------------
    def homeContent(self, filter):
        html = self._get(self.host + "/")
        return {"class": self.categories, "list": self._parse_list(html) if html else [], "filters": self.filters}

    def homeVideoContent(self):
        html = self._get(self.host + "/")
        return {"list": self._parse_list(html) if html else []}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        try:
            ext = self._parse_extend(extend)
            raw_type = self._ext_one(ext, "type")
            child = TYPE_MAP.get(str(tid), {}).get(raw_type, "")
            if not child and raw_type and raw_type.isdigit():
                child = raw_type
            use_tid = child or str(tid)
            area = self._seg(self._ext_one(ext, "area"))
            byv = self._seg(self._ext_one(ext, "by"), BY_MAP)
            lang = self._seg(self._ext_one(ext, "lang"))
            letter = self._ext_one(ext, "letter")
            if letter in ("全部", "all"):
                letter = ""
            year = self._ext_one(ext, "year")
            if year in ("全部", "all"):
                year = ""
            segs = [area, byv, "", lang, letter, "", "", str(pg), "", "", year]
            url = "%s/vodshow/%s-" % (self.host, use_tid) + "-".join(segs) + ".html"
            html = self._get(url)
            if not html:
                raise RuntimeError("请求失败")
            items = self._parse_list(html)
            pc = self._pagecount(html, pg)
            if pc <= pg and len(items) >= PAGE_SIZE:
                pc = pg + 1
            return {"page": pg, "pagecount": pc, "limit": PAGE_SIZE, "list": items}
        except Exception as e:
            # 加载失败要让壳里看得见，不静默空列表
            return {"page": pg, "pagecount": 1, "limit": PAGE_SIZE,
                    "list": [{"vod_id": "__error__", "vod_name": "加载失败：%s %s" % (type(e).__name__, str(e)[:80]),
                              "vod_pic": "", "vod_remarks": ""}]}

    def detailContent(self, ids):
        result = {"list": []}
        for vid in ids:
            vid = str(vid)
            if vid.startswith("folder:") or vid.startswith("cat:"):
                continue  # 本站无两级资料夹，此分支只作防御
            try:
                html = self._get("%s/voddetail/%s.html" % (self.host, vid))
                if not html:
                    continue
                tree = etree.HTML(html)
                if tree is None:
                    continue
                name = "".join(tree.xpath('//h1/text()')).strip()
                _pic = tree.xpath('//img[contains(@class,"gen-movie-img")]/@src')
                pic = self._fix_url(_pic[0] if _pic else "")
                remarks = "".join(tree.xpath('//span[contains(text(),"备注")]/following-sibling::div//text()')).strip()
                director = ",".join([t.strip() for t in tree.xpath('//span[contains(text(),"导演")]/following-sibling::div//a/text()') if t.strip()])
                actor = ",".join([t.strip() for t in tree.xpath('//span[contains(text(),"主演")]/following-sibling::div//a/text()') if t.strip()])
                year, area, vclass = "", "", ""
                m = re.search(r'<meta name="description" content="[^"]*（(\d{4})） / ([^，,]+)', html)
                if m:
                    year, area = m.group(1), m.group(2)
                m = re.search(r'<meta name="keywords" content="([^"]+)"', html)
                if m:
                    parts = [p.strip() for p in re.split(r"[，,]", m.group(1))]
                    if len(parts) > 5:
                        vclass = "，".join(parts[4:-1])
                content = "".join(tree.xpath('//div[contains(@class,"module-info-introduction-content")]//p//text()')).strip()
                tabs = tree.xpath('//div[contains(@class,"module-tab-item")]/@data-dropdown-value')
                panels = tree.xpath('//div[contains(@class,"module-list") and starts-with(@id,"panel")]')
                sources, episodes = [], []
                for i, p in enumerate(panels):
                    src = tabs[i] if i < len(tabs) else "线路%d" % (i + 1)
                    eps = []
                    for a in p.xpath('.//a[contains(@href,"/vodplay/")]'):
                        title = (a.get("title", "") or "".join(a.xpath(".//text()")).strip()).strip()
                        ep = title[2:] if title.startswith("播放") else title
                        if name and ep.startswith(name):
                            ep = ep[len(name):]
                        ep = ep.strip() or "第%d集" % (len(eps) + 1)
                        eps.append("%s$%s" % (ep, self._fix_url(a.get("href", ""))))
                    if eps:
                        sources.append(src)
                        episodes.append("#".join(eps))
                # 自营线是站外嵌页（iqiyi 等）需 parse:1 嗅探、盒子上最易播不了，后置；直链线在前
                order = sorted(range(len(sources)), key=lambda i: 1 if sources[i].startswith("自营") else 0)
                sources = [sources[i] for i in order]
                episodes = [episodes[i] for i in order]
                result["list"].append({"vod_id": vid, "vod_name": name, "vod_pic": pic, "vod_remarks": remarks,
                                       "vod_director": director, "vod_actor": actor, "vod_year": year,
                                       "vod_area": area, "vod_class": vclass, "vod_content": content,
                                       "vod_play_from": "$$$".join(sources),
                                       "vod_play_url": "$$$".join(episodes)})
            except Exception:
                continue
        return result

    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        wd = quote(key or "")
        if pg <= 1:
            url = "%s/vodsearch/%s-------------.html" % (self.host, wd)
        else:
            url = "%s/vodsearch/%s----------%d---.html" % (self.host, wd, pg)
        html = self._get(url)
        if not html:
            return {"list": [], "page": pg, "pagecount": 1, "limit": SEARCH_PAGE_SIZE}
        items = self._parse_search_list(html)
        pc = self._pagecount(html, pg)
        if pc <= pg and len(items) >= SEARCH_PAGE_SIZE:
            pc = pg + 1
        return {"list": items, "page": pg, "pagecount": pc, "limit": SEARCH_PAGE_SIZE}

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    def playerContent(self, flag, id, vipFlags):
        url = self._fix_url(id)
        low = url.split("?")[0].lower()
        if low.endswith((".m3u8", ".mp4", ".flv", ".mkv")):
            return {"parse": 0, "url": url, "header": dict(self.headers)}
        try:
            html = self._get(url)
            if html:
                m = re.search(r'player_aaaa\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.S)
                if m:
                    d = json.loads(m.group(1))
                    u = (d.get("url") or "").strip()
                    enc = d.get("encrypt", 0)
                    try:
                        enc = int(enc)
                    except Exception:
                        enc = 0
                    if enc == 1:
                        u = unquote(u)
                    elif enc == 2:
                        try:
                            u = unquote(base64.b64decode(u).decode("utf-8"))
                        except Exception:
                            pass
                    if u:
                        if u.split("?")[0].lower().endswith((".m3u8", ".mp4", ".flv", ".mkv")):
                            return {"parse": 0, "url": u, "header": dict(self.headers)}
                        return {"parse": 1, "url": url, "header": dict(self.headers)}
        except Exception:
            pass
        return {"parse": 1, "url": url, "header": dict(self.headers)}

    # ---------------- 老壳必须实现的抽象方法 ----------------
    def isVideoFormat(self, url):
        return str(url or "").split("?")[0].lower().endswith((".m3u8", ".mp4", ".flv", ".mkv", ".ts"))

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        # 参数可能是 dict 或字符串；只代理图片（防盗链/裂图兜底），其余 404
        try:
            p = param if isinstance(param, dict) else dict(parse_qsl(str(param or "")))
        except Exception:
            return [404, "text/plain", b""]
        t = str(p.get("type") or "")
        u = unquote(str(p.get("url") or ""))
        if t == "img" and u:
            try:
                data = self._request(u, binary=True)
            except Exception:
                data = None
            if data:
                if data[:3] == b"\xff\xd8\xff":
                    mime = "image/jpeg"
                elif data[:8] == b"\x89PNG\r\n\x1a\n":
                    mime = "image/png"
                elif data[:6] in (b"GIF87a", b"GIF89a"):
                    mime = "image/gif"
                elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                    mime = "image/webp"
                else:
                    mime = "image/jpeg"
                return [200, mime, data]
        return [404, "text/plain", b""]

    def destroy(self):
        pass
