# 网站名称: 初见影视 (cjysw.cc)
# 网站特征: MacCMS v10 伪静态·mxpro 模板·requests·/vodshow 11 段筛选·/vodsearch 路径搜索·player_aaaa 直链
# m3u8特征: 有m3u8、播放页 player_aaaa.url 直链、部分线路为第三方站外页（iqiyi 等）需 parse:1、CDN 有地域限制（沙盒 403）
# 最后验证: 2026-11-08 沙盒实测
import json
import re
import requests
from urllib.parse import quote
from lxml import etree
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    def getName(self):
        return "初见影视"

    def init(self, extend=""):
        # 解析 extend 必须容错且绝不抛错：str/dict/None/非法 JSON 全接受，失败当空配置
        self.host = "https://cjysw.cc"
        try:
            cfg = self._parse_extend(extend)
            if isinstance(cfg, dict) and cfg.get("host"):
                self.host = str(cfg["host"]).rstrip("/")
        except Exception:
            pass
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", "Referer": self.host + "/"}
        self.categories = [{"type_id": "60", "type_name": "Netflix"}, {"type_id": "1", "type_name": "电影"}, {"type_id": "15", "type_name": "剧集"}, {"type_id": "30", "type_name": "动漫"}, {"type_id": "24", "type_name": "综艺"}, {"type_id": "63", "type_name": "纪录片"}, {"type_id": "47", "type_name": "短剧"}]
        self._type_map = {"1": {"动作片": "2", "喜剧片": "3", "爱情片": "4", "科幻片": "5", "恐怖片": "6", "剧情片": "7", "战争片": "8", "悬疑片": "10", "动画片": "11", "犯罪片": "12", "奇幻片": "13", "其他片": "67"}, "60": {"自制剧": "61", "电影": "62"}, "15": {"国产剧": "16", "香港剧": "17", "台湾剧": "18", "美国剧": "19", "韩国剧": "20", "日本剧": "21", "海外剧": "22", "泰剧": "23", "其他剧": "68"}, "30": {"国产动漫": "31", "日韩动漫": "32", "欧美动漫": "33", "港台动漫": "34", "海外动漫": "35"}, "24": {"大陆综艺": "25", "日韩综艺": "26", "港台综艺": "27", "欧美综艺": "28", "演唱会": "29"}, "47": {"有声动漫": "48", "女频恋爱": "49", "反转爽剧": "50", "脑洞悬疑": "51", "年代穿越": "52", "古装仙侠": "53", "现代都市": "54", "漫剧": "66"}}
        self._area_map = {"1": ["大陆", "香港", "台湾", "美国", "法国", "英国", "日本", "韩国", "德国", "泰国", "印度", "意大利", "西班牙", "加拿大", "其他"], "60": ["大陆", "香港", "台湾", "韩国", "美国", "法国", "英国", "日本", "德国", "泰国", "印度", "意大利", "西班牙", "加拿大", "其他"], "15": ["内地", "韩国", "香港", "台湾", "日本", "美国", "泰国", "英国", "新加坡", "其他"], "30": ["内地", "日本", "欧美", "其他"], "24": ["内地", "港台", "日韩", "欧美"], "63": ["大陆", "香港", "台湾", "美国", "法国", "英国", "日本", "韩国", "德国", "泰国", "印度", "意大利", "西班牙", "加拿大", "其他"]}
        self._lang_map = {"1": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "法语", "德语", "其它"], "60": ["国语", "英语", "韩语", "日语", "法语", "德语", "其它"], "15": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "其它"], "30": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "其它"], "24": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "其它"], "63": ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "法语", "德语", "其它"]}
        self._by_map = {"时间排序": "time", "人气排序": "hits", "评分排序": "score"}
        self.filters = self._build_filters()

    def _build_filters(self):
        filters = {}
        letters = [{"n": "全部", "v": ""}] + [{"n": c, "v": c} for c in [chr(i) for i in range(65, 91)] + ["0-9"]]
        years = [{"n": "全部", "v": ""}] + [{"n": str(y), "v": str(y)} for y in range(2026, 2013, -1)]
        bys = [{"n": "全部", "v": ""}, {"n": "时间排序", "v": "time"}, {"n": "人气排序", "v": "hits"}, {"n": "评分排序", "v": "score"}]
        for cat in self.categories:
            tid = cat["type_id"]
            f = {}
            if tid in self._type_map:
                f["type"] = {"name": "类型", "value": [{"n": "全部", "v": ""}] + [{"n": n, "v": v} for n, v in self._type_map[tid].items()]}
            if tid in self._area_map:
                f["area"] = {"name": "地区", "value": [{"n": "全部", "v": ""}] + [{"n": a, "v": a} for a in self._area_map[tid]]}
            f["by"] = {"name": "排序", "value": bys}
            if tid in self._lang_map:
                f["lang"] = {"name": "语言", "value": [{"n": "全部", "v": ""}] + [{"n": a, "v": a} for a in self._lang_map[tid]]}
            f["letter"] = {"name": "字母", "value": letters}
            f["year"] = {"name": "年份", "value": years}
            filters[tid] = f
        return filters

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

    def _get(self, url):
        try:
            r = requests.get(url, headers=self.headers, timeout=15)
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text if r.status_code == 200 else None
        except Exception as e:
            print(f"[ERROR] 请求失败: {url} - {e}")
            return None

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

    def _pagecount(self, html):
        m = re.search(r'href="[^"]*--------(\d+)---\.html"[^>]*>尾页</a>', html or "")
        return int(m.group(1)) if m else 1

    def homeContent(self, filter):
        html = self._get(self.host + "/")
        return {"class": self.categories, "list": self._parse_list(html) if html else [], "filters": self.filters}

    def homeVideoContent(self):
        return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        try:
            ext = self._parse_extend(extend)
            raw_type = self._ext_one(ext, "type")
            child = self._type_map.get(str(tid), {}).get(raw_type, "")
            if not child and raw_type and raw_type.isdigit():
                child = raw_type
            use_tid = child or str(tid)
            area = self._seg(self._ext_one(ext, "area"))
            byv = self._seg(self._ext_one(ext, "by"), self._by_map)
            lang = self._seg(self._ext_one(ext, "lang"))
            letter = self._ext_one(ext, "letter")
            if letter in ("全部", "all"):
                letter = ""
            year = self._ext_one(ext, "year")
            if year in ("全部", "all"):
                year = ""
            segs = [area, byv, "", lang, letter, "", "", str(pg), "", "", year]
            url = f"{self.host}/vodshow/{use_tid}-" + "-".join(segs) + ".html"
            html = self._get(url)
            if not html:
                raise RuntimeError("请求失败")
            return {"page": pg, "pagecount": self._pagecount(html), "limit": 45, "list": self._parse_list(html)}
        except Exception as e:
            return {"page": pg, "pagecount": 1, "limit": 45, "list": [{"vod_id": "__error__", "vod_name": f"加载失败：{type(e).__name__} {str(e)[:80]}", "vod_pic": "", "vod_remarks": ""}]}

    def detailContent(self, ids):
        result = {"list": []}
        for vid in ids:
            try:
                html = self._get(f"{self.host}/voddetail/{vid}.html")
                if not html:
                    continue
                tree = etree.HTML(html)
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
                    src = tabs[i] if i < len(tabs) else f"线路{i+1}"
                    eps = []
                    for a in p.xpath('.//a[contains(@href,"/vodplay/")]'):
                        title = (a.get("title", "") or "".join(a.xpath(".//text()")).strip()).strip()
                        ep = title[2:] if title.startswith("播放") else title
                        if name and ep.startswith(name):
                            ep = ep[len(name):]
                        ep = ep.strip() or f"第{len(eps)+1}集"
                        eps.append(f"{ep}${self._fix_url(a.get('href', ''))}")
                    if eps:
                        sources.append(src)
                        episodes.append("#".join(eps))
                # 自营线是站外嵌页（iqiyi 等）需 parse:1 嗅探、盒子上最易播不了，后置；直链线在前
                order = sorted(range(len(sources)), key=lambda i: 1 if sources[i].startswith("自营") else 0)
                sources = [sources[i] for i in order]
                episodes = [episodes[i] for i in order]
                result["list"].append({"vod_id": str(vid), "vod_name": name, "vod_pic": pic, "vod_remarks": remarks, "vod_director": director, "vod_actor": actor, "vod_year": year, "vod_area": area, "vod_class": vclass, "vod_content": content, "vod_play_from": "$$$".join(sources), "vod_play_url": "$$$".join(episodes)})
            except Exception:
                continue
        return result

    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        wd = quote(key or "")
        if pg <= 1:
            url = f"{self.host}/vodsearch/{wd}-------------.html"
        else:
            url = f"{self.host}/vodsearch/{wd}" + "-" * 10 + str(pg) + "-" * 3 + ".html"
        html = self._get(url)
        if not html:
            return {"list": [], "page": pg, "pagecount": 1}
        return {"list": self._parse_search_list(html), "page": pg, "pagecount": self._pagecount(html)}

    def playerContent(self, flag, id, vipFlags):
        url = self._fix_url(id)
        try:
            html = self._get(url)
            if html:
                m = re.search(r'player_aaaa\s*=\s*(\{.*?\})</script>', html, re.S)
                if m:
                    d = json.loads(m.group(1))
                    u = (d.get("url") or "").strip()
                    if u:
                        if ".m3u8" in u or ".mp4" in u:
                            return {"parse": 0, "url": u, "header": json.dumps(self.headers)}
                        return {"parse": 1, "url": url, "header": json.dumps(self.headers)}
        except Exception:
            pass
        return {"parse": 1, "url": url, "header": json.dumps(self.headers)}
