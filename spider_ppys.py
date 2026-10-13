# -*- coding: utf-8 -*-
# 皮皮影视 ppys.live · T3源
# 站型: 苹果CMS(stui模板)
# 列表: /vodshow/{tid}-{12段}.html
#   段位: [1]=地区 [3]=剧情 [4]=语言 [8]=页码 [11]=年份
# 详情: /voddetail/{id}.html · 播放: /vodplay/{id}-{sid}-{nid}.html
# 播放页 var player_aaaa JSON, encrypt:0 明文
# 注意: 视频CDN疑似墙境外IP,桌面无法验证m3u8直链,需真机(国内IP)验证播放
try:
    from base.spider import Spider as BaseSpider
except Exception:
    BaseSpider = object

import re
import json
import base64
import urllib.parse

try:
    import requests
except Exception:
    requests = None


class Spider(BaseSpider):
    CATES = [
        {"type_id": "1", "type_name": "电视剧"},
        {"type_id": "2", "type_name": "电影"},
        {"type_id": "5", "type_name": "爽剧"},
        {"type_id": "3", "type_name": "动漫"},
        {"type_id": "4", "type_name": "综艺"},
        {"type_id": "26", "type_name": "番剧"},
    ]
    # 各分类剧情选项(站内筛选实测)
    CLASSES = {
        "1": ["爱情", "古装", "悬疑", "都市", "喜剧", "战争", "剧情", "青春",
              "历史", "网剧", "奇幻", "冒险", "励志", "犯罪", "商战", "恐怖",
              "穿越", "农村", "人物", "商业", "生活", "其他"],
        "2": ["爱情", "古装", "动作", "伦理", "悬疑", "犯罪", "谍战", "历史",
              "喜剧", "奇幻", "家庭", "青春", "剧情", "冒险", "纪录", "动画",
              "人物", "文化", "其他"],
        "3": ["冒险", "战斗", "搞笑", "经典", "科幻", "玄幻", "魔幻", "武侠",
              "恋爱", "推理", "日常", "校园", "悬疑", "真人", "历史", "竞技", "其他"],
        "4": ["游戏", "脱口秀", "音乐", "情感", "生活", "职场", "真人秀", "搞笑",
              "公益", "艺术", "访谈", "益智", "体育", "少儿", "时尚", "人物", "其他"],
    }
    AREAS = ["中国大陆", "中国香港", "中国台湾", "日本", "韩国", "泰国",
             "欧美", "印度", "马来西亚", "其他"]
    YEARS = [str(y) for y in range(2026, 2014, -1)]
    LANGS = ["国语", "英语", "粤语", "闽南语", "韩语", "日语", "其它"]
    UA = ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
          "Chrome/131.0.0.0 Mobile Safari/537.36")

    def init(self, extend=""):
        host = "https://www.ppys.live"
        if extend:
            if isinstance(extend, dict):
                host = extend.get("host", host)
            elif isinstance(extend, str):
                e = extend.strip()
                if e.startswith("{"):
                    try:
                        host = json.loads(e).get("host", host)
                    except Exception:
                        pass
                elif e.startswith("http"):
                    host = e
        self.host = host.rstrip("/")
        self.session = None
        if requests:
            s = requests.Session()
            s.headers.update({"User-Agent": self.UA})
            self.session = s

    def _get(self, url, referer=None):
        if not self.session:
            return ""
        try:
            h = {"Referer": referer or self.host + "/"}
            r = self.session.get(url, headers=h, timeout=20)
            r.encoding = "utf-8"
            return r.text if r.status_code == 200 else ""
        except Exception:
            return ""

    def _abs(self, u):
        if not u:
            return ""
        if u.startswith("http"):
            return u
        if u.startswith("//"):
            return "https:" + u
        return self.host + (u if u.startswith("/") else "/" + u)

    def _clean(self, s):
        s = str(s or "")
        s = re.sub(r"<[^>]+>", "", s)
        return s.replace("$", "＄").replace("#", "＃").strip()

    def _opts(self, items):
        return [{"n": "全部", "v": ""}] + [{"n": x, "v": x} for x in items]

    def _filter_item(self, key, name, items):
        return {"key": key, "name": name,
                "value": [{"n": "全部", "v": ""}] +
                         [{"n": x, "v": x} for x in items]}

    def homeContent(self, filter):
        # TVBox 标准格式: filters[type_id] = [{key,name,value:[{n,v}]}]
        filters = {}
        for tid in ("1", "2", "3", "4"):
            fl = []
            if tid in self.CLASSES:
                fl.append(self._filter_item("class", "剧情", self.CLASSES[tid]))
            fl.append(self._filter_item("area", "地区", self.AREAS))
            fl.append(self._filter_item("year", "年份", self.YEARS))
            fl.append(self._filter_item("lang", "语言", self.LANGS))
            filters[tid] = fl
        return {"class": self.CATES, "filters": filters}

    def homeVideoContent(self):
        html = self._get(self.host + "/")
        return {"list": self._parse_list(html)[:24]}

    def _parse_list(self, html):
        vods = []
        for m in re.finditer(
                r'<a[^>]*href="(/voddetail/(\d+)\.html)"[^>]*title="([^"]+)"[^>]*>.*?'
                r'(?:data-original="([^"]+)"|src="([^"]+)")',
                html, re.S):
            vid, name = m.group(2), m.group(3)
            pic = m.group(4) or m.group(5) or ""
            if "load.gif" in pic:
                pic = ""
            # 备注: 更新至X集
            remark = ""
            rm = re.search(r'pic-text[^>]*>([^<]{1,20})<',
                           html[m.start():m.start() + 2000])
            if rm:
                remark = self._clean(rm.group(1))
            vods.append({
                "vod_id": vid,
                "vod_name": self._clean(name),
                "vod_pic": self._abs(pic),
                "vod_remarks": remark,
            })
        seen, out = set(), []
        for v in vods:
            if v["vod_id"] not in seen:
                seen.add(v["vod_id"])
                out.append(v)
        return out

    def _pagecount(self, html, pg):
        m = re.search(r'共(\d+)页', html)
        if m:
            try:
                return max(1, int(m.group(1)))
            except Exception:
                pass
        return pg + 1

    def _list_url(self, tid, pg, extend):
        # 12段: [1]=地区 [3]=剧情 [4]=语言 [8]=页码 [11]=年份
        seg = [""] * 12
        if isinstance(extend, dict):
            if extend.get("area"):
                seg[1] = extend["area"]
            if extend.get("class"):
                seg[3] = extend["class"]
            if extend.get("lang"):
                seg[4] = extend["lang"]
            if extend.get("year"):
                seg[11] = extend["year"]
        seg[8] = str(pg) if pg > 1 else ""
        parts = [urllib.parse.quote(s) if s else "" for s in seg]
        return "{0}/vodshow/{1}-{2}.html".format(
            self.host, tid, "-".join(parts))

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        ext = extend or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except Exception:
                ext = {}
        html = self._get(self._list_url(tid, pg, ext))
        lst = self._parse_list(html)
        return {
            "list": lst,
            "page": pg,
            "pagecount": self._pagecount(html, pg) if lst else 1,
            "limit": 48,
            "total": 999999,
        }

    def searchContent(self, key, quick, pg="1"):
        pg = int(pg) if str(pg).isdigit() else 1
        kw = urllib.parse.quote(key)
        url = "{0}/vodsearch/-------------.html?wd={1}".format(self.host, kw)
        html = self._get(url)
        lst = self._parse_list(html)
        return {
            "list": lst,
            "page": pg,
            "pagecount": 1,
            "limit": 48,
            "total": len(lst),
        }

    def _field(self, html, label):
        # <span ...>主演：</span>xxx 或 <a>xxx</a>
        m = re.search(
            r'<span[^>]*>' + label + r'[：:]</span>\s*(?:<a[^>]*>([^<]+)</a>\s*)+([^<]{1,80})',
            html)
        if m:
            return self._clean(m.group(2))
        m = re.search(r'<span[^>]*>' + label + r'[：:]</span>([^<]{1,80})', html)
        return self._clean(m.group(1)) if m else ""

    def _links_field(self, html, label):
        # 类型/地区/年份: 取其后所有 <a> 文本, 到下一个 <span 即止
        m = re.search(r'<span[^>]*>' + label + r'[：:]</span>(.*?)<span', html, re.S)
        if not m:
            return ""
        links = re.findall(r'<a[^>]*>([^<]+)</a>', m.group(1))
        return " ".join(self._clean(x) for x in links)

    def detailContent(self, ids):
        vid = str(ids[0])
        html = self._get("{0}/voddetail/{1}.html".format(self.host, vid),
                         referer=self.host + "/")
        name_m = re.search(r"<h1[^>]*>([^<]{1,60})", html)
        name = self._clean(name_m.group(1)) if name_m else vid
        pic_m = re.search(r'data-original="(https?://[^"]+)"', html)
        pic = pic_m.group(1) if pic_m else ""
        score_m = re.search(r'class="score[^"]*">([^<]{1,10})<', html)
        score = self._clean(score_m.group(1)) if score_m else ""

        actor = self._field(html, "主演")
        director = self._field(html, "导演")
        update = self._field(html, "更新")
        vtype = self._links_field(html, "类型")
        area = self._links_field(html, "地区")
        year = self._links_field(html, "年份")
        desc_m = re.search(
            r'<span[^>]*>简介[：:]</span>(.*?)</p>', html, re.S)
        desc = self._clean(desc_m.group(1)) if desc_m else ""
        if "敬请期待" in desc or "暂无简介" in desc:
            desc = ""

        # 真实线路名: tab头 (如 稳定-BF) -> pane id
        tabs = re.findall(r'<a href="#(down[\d-]+)"[^>]*>([^<]{1,20})</a>', html)
        tab_name = {tid_: self._clean(nm) for tid_, nm in tabs}

        # 按 pane 分线路, 每个 pane 内的分集归属该线路名
        play_from, play_url = [], []
        panes = re.findall(
            r'<div id="(down[\d-]+)" class="tab-pane[^"]*"(.*?)</div>\s*</div>',
            html, re.S)
        if not panes:
            panes = [("down1-1", html)]  # 兜底: 全页按 sid 分组
        for pane_id, pane_html in panes:
            eps = re.findall(
                r'href="/vodplay/(\d+)-(\d+)-(\d+)\.html"[^>]*>([^<]{1,30})</a>',
                pane_html)
            items, seen = [], set()
            for v, sid, nid, epname in eps:
                if v != vid or nid in seen or "立即播放" in epname:
                    continue
                seen.add(nid)
                nm = self._clean(epname) or ("第%s集" % nid)
                items.append((int(nid), "%s$%s@@%s@@%s" % (nm, vid, sid, nid)))
            if not items:
                continue
            items.sort()
            fname = tab_name.get(pane_id, "线路%d" % (len(play_from) + 1))
            play_from.append(fname)
            play_url.append("#".join(e for _, e in items))
        vod = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_actor": actor,
            "vod_director": director,
            "vod_year": year,
            "vod_area": area,
            "vod_content": desc,
            "vod_remarks": update,
            "type_name": vtype,
            "vod_score": score,
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        return {"list": [vod]}

    def _extract_player_json(self, html):
        m = re.search(r"var player_aaaa\s*=\s*(\{)", html)
        if not m:
            return None
        start = m.start(1)
        depth, j, instr, esc = 0, start, False, False
        while j < len(html):
            c = html[j]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        try:
            return json.loads(html[start:j + 1])
        except Exception:
            return None

    def playerContent(self, flag, id, vipFlags):
        # 壳已按 $ 切掉集名, id 形如 vid@@sid@@nid, 兼容带集名前缀
        pid = str(id).rsplit("$", 1)[-1]
        parts = pid.split("@@")
        if len(parts) < 3:
            return {"parse": 0, "url": "", "header": {}}
        vid, sid, nid = parts[0], parts[1], parts[2]
        play_url = "{0}/vodplay/{1}-{2}-{3}.html".format(
            self.host, vid, sid, nid)
        html = self._get(play_url,
                         referer="{0}/voddetail/{1}.html".format(self.host, vid))
        d = self._extract_player_json(html)
        if not d:
            return {"parse": 0, "url": play_url, "header": {}}
        url = d.get("url", "") or ""
        enc = str(d.get("encrypt", "0"))
        if enc == "2" and url:
            try:
                url = urllib.parse.unquote(base64.b64decode(url).decode("utf-8"))
            except Exception:
                pass
        elif enc == "1" and url:
            try:
                url = urllib.parse.unquote(url)
            except Exception:
                pass
        # 播放页真实信息(标题/集名)可用于展示
        if url.startswith("http"):
            return {"parse": 0, "url": url,
                    "header": {"User-Agent": self.UA}}
        return {"parse": 0, "url": play_url, "header": {}}

    def isVideoFormat(self, url):
        u = (url or "").lower()
        return any(u.split("?")[0].endswith(x)
                   for x in [".m3u8", ".mp4", ".mkv", ".flv", ".ts"])

    def localProxy(self, param=None):
        return [200, "text/plain", None, b""]
