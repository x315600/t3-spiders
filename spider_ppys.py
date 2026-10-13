# -*- coding: utf-8 -*-
# 皮皮影视 ppys.live · T3源
# 站型: 苹果CMS(stui模板) · 列表/vodshow 12段(页码第9段) · 详情/voddetail/{id}.html
# 播放页 var player_aaaa JSON, encrypt:0 明文 / 1/2 需解码
# 注意: 视频CDN疑似墙境外IP,桌面无法验证m3u8,需真机(国内IP)验证播放
try:
    from base.spider import Spider as BaseSpider
except Exception:
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
        return (s or "").replace("$", "＄").replace("#", "＃").strip()

    def _parse_list(self, html):
        vods = []
        # stui 列表: <a href="/voddetail/xxx.html" title="xxx"> + data-original
        for m in re.finditer(
                r'<a[^>]*href="(/voddetail/(\d+)\.html)"[^>]*title="([^"]+)"[^>]*>.*?'
                r'(?:data-original="([^"]+)"|src="([^"]+)")',
                html, re.S):
            vid, name = m.group(2), m.group(3)
            pic = m.group(4) or m.group(5) or ""
            if "load.gif" in pic:
                pic = ""
            vods.append({
                "vod_id": vid,
                "vod_name": self._clean(name),
                "vod_pic": self._abs(pic),
                "vod_remarks": "",
            })
        # 去重保序
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
        m = re.search(r'page[=/](\d+)', html)
        if m:
            try:
                return max(pg, int(m.group(1)))
            except Exception:
                pass
        return pg + 1

    def homeContent(self, filter):
        return {"class": self.CATES}

    def homeVideoContent(self):
        html = self._get(self.host + "/")
        return {"list": self._parse_list(html)[:24]}

    def _list_url(self, tid, pg):
        if pg <= 1:
            return "{0}/vodshow/{1}-----------.html".format(self.host, tid)
        # 12段,页码第9段
        return "{0}/vodshow/{1}--------{2}---.html".format(self.host, tid, pg)

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        html = self._get(self._list_url(tid, pg))
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
        if pg <= 1:
            url = "{0}/vodsearch/-------------.html?wd={1}".format(self.host, kw)
        else:
            url = "{0}/vodsearch/{1}----------{2}---.html".format(
                self.host, kw, pg)
        html = self._get(url)
        lst = self._parse_list(html)
        return {
            "list": lst,
            "page": pg,
            "pagecount": self._pagecount(html, pg) if lst else 1,
            "limit": 48,
            "total": 999999,
        }

    def detailContent(self, ids):
        vid = str(ids[0])
        html = self._get("{0}/voddetail/{1}.html".format(self.host, vid),
                         referer=self.host + "/")
        name_m = re.search(r"<h1[^>]*>([^<]{1,60})", html)
        name = self._clean(name_m.group(1)) if name_m else vid
        pic_m = re.search(r'data-original="(https?://[^"]+)"', html)
        pic = pic_m.group(1) if pic_m else ""
        desc_m = re.search(r"简介[：:]\s*([^<]{4,200})", html)
        desc = (desc_m.group(1).strip() if desc_m else "")
        if "敬请期待" in desc:
            desc = ""

        # 分集: /vodplay/{id}-{sid}-{nid}.html, 按 sid 分线路
        eps = re.findall(r'href="(/vodplay/(\d+)-(\d+)-(\d+)\.html)"[^>]*>([^<]{1,30})</a>', html)
        by_sid = {}
        seen_ep = set()
        for _, v, sid, nid, epname in eps:
            if v != vid or (sid, nid) in seen_ep:
                continue
            if "立即播放" in epname:
                continue
            seen_ep.add((sid, nid))
            by_sid.setdefault(sid, []).append(
                (int(nid), self._clean(epname) or ("第%s集" % nid), nid))
        # 线路名: 取 playlist 块标题, 兜底 bfzym3u8
        froms = ["bfzym3u8"] * max(1, len(by_sid))
        play_from, play_url = [], []
        for i, sid in enumerate(sorted(by_sid.keys(), key=int)):
            items = sorted(by_sid[sid])
            epstr = "#".join(
                "{0}${1}@@{2}@@{3}".format(nm, vid, sid, nid)
                for _, nm, nid in items)
            fname = froms[i] if i < len(froms) else "线路%d" % (i + 1)
            play_from.append(self._clean(fname))
            play_url.append(epstr)
        vod = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_content": desc,
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
        if url.startswith("http"):
            # CDN直链只带UA,不带Referer
            return {"parse": 0, "url": url,
                    "header": {"User-Agent": self.UA}}
        # 拿不到直链交App嗅探
        return {"parse": 0, "url": play_url, "header": {}}

    def isVideoFormat(self, url):
        u = (url or "").lower()
        return any(u.split("?")[0].endswith(x)
                   for x in [".m3u8", ".mp4", ".mkv", ".flv", ".ts"])

    def localProxy(self, param=None):
        return [200, "text/plain", None, b""]
