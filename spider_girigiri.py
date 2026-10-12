# -*- coding: utf-8 -*-
"""
girigiri愛動漫 (ani.girigirilove.com) T3 源
站型: MacCMS 魔改 (DSN2 模板), SSR HTML, api.php 已关
- 列表: /show/{tid}-{月份}-{排序}-{类型}-{语言}----{页}-{改编}--{年份}/ (12 段, 页在第 9 段)
- 详情: /GV{id}/  分集: /playGV{id}-{sid}-{nid}/
- 播放: 播放页 var player_aaaa, encrypt=2 -> base64 解码后再 unquote 得 m3u8 直链
- 搜索: /search/ 有图形验证码墙; 改走 /index.php/ajax/suggest (免验证码, JSON)
"""
import base64
import json
import re
import urllib.parse
import urllib.request

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

try:
    import requests
except Exception:
    requests = None

HOST = "https://ani.girigirilove.com"
UA = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
PLAYER_UA = UA

CLASSES = [
    {"type_id": "2", "type_name": "日番"},
    {"type_id": "21", "type_name": "劇場版"},
    {"type_id": "3", "type_name": "美番"},
    {"type_id": "20", "type_name": "真人番劇"},
    {"type_id": "24", "type_name": "BD副音軌"},
    {"type_id": "26", "type_name": "演唱會周邊"},
]

_CLASS_OPTS = ["喜剧", "爱情", "恐怖", "动作", "科幻", "剧情", "战争", "奇幻",
               "冒险", "悬疑", "日常", "校园", "热血", "机甲", "后宫", "乙女",
               "百合", "萌", "职场", "运动", "音乐", "魔法少女", "异世界", "爱抖露"]
_YEAR_OPTS = [str(y) for y in range(2026, 2000, -1)] + ["2000至90年代"]
_MONTH_OPTS = ["一月", "二月", "三月", "四月", "五月", "六月",
               "七月", "八月", "九月", "十月", "十一月", "十二月"]


def _opts(values):
    return [{"n": "全部", "v": ""}] + [{"n": v, "v": v} for v in values]


FILTERS = {
    "by": {"key": "by", "name": "排序", "value": [
        {"n": "按最新", "v": "time"}, {"n": "按最热", "v": "hits"}, {"n": "按评分", "v": "score"}]},
    "class": {"key": "class", "name": "类型", "value": _opts(_CLASS_OPTS)},
    "lang": {"key": "lang", "name": "语言", "value": _opts(["日语", "国语"])},
    "area": {"key": "area", "name": "月份", "value": _opts(_MONTH_OPTS)},
    "state": {"key": "state", "name": "改编", "value": _opts(["原创", "小说改", "漫画改", "游戏改"])},
    "year": {"key": "year", "name": "年份", "value": _opts(_YEAR_OPTS)},
}

FROM_NAME = {"cht": "繁体", "chs": "简体"}


class Spider(BaseSpider):
    def __init__(self):
        super().__init__()
        self.host = HOST
        self._session = None

    def getDependence(self):
        return []

    def init(self, extend=""):
        try:
            if isinstance(extend, dict):
                ext = extend
            elif isinstance(extend, str) and extend.strip().startswith("{"):
                ext = json.loads(extend)
            else:
                ext = {}
        except Exception:
            ext = {}
        host = str(ext.get("host") or "").rstrip("/")
        if host.startswith("http"):
            self.host = host

    def destroy(self):
        pass

    # ---------------- 网络 ----------------
    def _get(self, url, referer=None):
        # 站点偶发 HTTP 200 空正文(实测), 空响应重试至多 3 次
        headers = {"User-Agent": UA, "Referer": referer or (self.host + "/")}
        for _ in range(3):
            try:
                if requests is not None:
                    if self._session is None:
                        self._session = requests.Session()
                    r = self._session.get(url, headers=headers, timeout=15)
                    r.encoding = "utf-8"
                    if r.status_code == 200 and r.text:
                        return r.text
                    continue
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    text = resp.read().decode("utf-8", "ignore")
                    if text:
                        return text
            except Exception:
                continue
        return ""

    def _abs(self, url):
        url = (url or "").strip()
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return self.host + url
        return url

    # ---------------- 解析 ----------------
    def _parse_cards(self, html):
        """锚点窗口法解析卡片: /GV{id}/ 链接 + 附近 img data-src + prb 备注"""
        vods, seen = [], set()
        for m in re.finditer(r'href="/GV(\d+)/"', html or ""):
            vid = m.group(1)
            if vid in seen:
                continue
            win = html[max(0, m.start() - 300): m.start() + 1200]
            im = re.search(r'data-src="([^"]+)"', win)
            if not im:
                continue
            pic = im.group(1)
            if "/vod/" not in pic and "akua" not in pic:
                continue
            nm = re.search(r'title="([^"]+)"', win) or re.search(r'alt="([^"]+)"', win)
            name = (nm.group(1) if nm else "").strip()
            if not name:
                continue
            rm = re.search(r'public-list-prb[^"]*">([^<]+)</span>', win)
            remarks = rm.group(1).strip() if rm else ""
            remarks = remarks.split("/")[0].strip() if remarks else ""
            seen.add(vid)
            vods.append({
                "vod_id": vid,
                "vod_name": name.replace("$", "＄").replace("#", "＃"),
                "vod_pic": self._abs(pic),
                "vod_remarks": remarks,
            })
        return vods

    def _pagecount(self, html, tid, pg):
        pages = [int(x) for x in re.findall(r'/show/' + re.escape(str(tid)) + r'-[^"]*?-(\d+)---/', html or "")]
        if pages:
            return max(max(pages), int(pg))
        m = re.search(r'(\d+)\s*/\s*(\d+)\s*页', html or "")
        if m:
            return max(int(m.group(2)), int(pg))
        return int(pg)

    # ---------------- 接口 ----------------
    def homeContent(self, filter=False):
        result = {"class": CLASSES}
        if filter:
            result["filters"] = {c["type_id"]: [FILTERS[k] for k in ("by", "class", "lang", "area", "state", "year")]
                                 for c in CLASSES}
        return result

    def homeVideoContent(self):
        html = self._get(self.host + "/")
        return {"list": self._parse_cards(html)[:48]}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg or 1)
        ext = extend if isinstance(extend, dict) else {}
        parts = [str(tid),
                 str(ext.get("area") or ""), str(ext.get("by") or ""),
                 str(ext.get("class") or ""), str(ext.get("lang") or ""),
                 "", "", "", str(pg),
                 str(ext.get("state") or ""), "", str(ext.get("year") or "")]
        seg = "-".join(urllib.parse.quote(p, safe="") for p in parts)
        html = self._get("%s/show/%s/" % (self.host, seg))
        vods = self._parse_cards(html)
        pagecount = self._pagecount(html, tid, pg)
        if not vods:
            pagecount = max(pagecount, pg) if pg > 1 else 1
        elif len(vods) < 48 and pg >= pagecount:
            pagecount = pg
        limit = len(vods) if vods else 48
        return {"list": vods, "page": pg, "pagecount": pagecount,
                "limit": limit, "total": pagecount * limit}

    def searchContent(self, key, quick, pg="1"):
        pg = int(pg or 1)
        url = "%s/index.php/ajax/suggest?mid=1&wd=%s&limit=20&page=%d" % (
            self.host, urllib.parse.quote(str(key)), pg)
        text = self._get(url)
        vods, pagecount, total = [], 1, 0
        try:
            data = json.loads(text)
            total = int(data.get("total") or 0)
            pagecount = max(int(data.get("pagecount") or 1), 1)
            for it in data.get("list") or []:
                vods.append({
                    "vod_id": str(it.get("id")),
                    "vod_name": str(it.get("name") or "").replace("$", "＄").replace("#", "＃"),
                    "vod_pic": self._abs(str(it.get("pic") or "")),
                    "vod_remarks": "",
                })
        except Exception:
            pass
        return {"list": vods, "page": pg, "pagecount": pagecount,
                "limit": 20, "total": total}

    def _player_data(self, html):
        i = (html or "").find("var player_aaaa=")
        if i < 0:
            return {}
        j = html.find("</script>", i)
        raw = html[i + len("var player_aaaa="): j if j > i else len(html)].strip().rstrip(";")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def detailContent(self, ids):
        vid = str(ids[0]) if ids else ""
        html = self._get("%s/GV%s/" % (self.host, vid))
        if not html:
            return {"list": []}
        nm = re.search(r'data-name="([^"]+)"', html)
        name = nm.group(1) if nm else (re.search(r'<title>([^<_]+)', html).group(1) if '<title>' in html else vid)
        pic = ""
        for im in re.finditer(r'<img[^>]+>', html):
            tag = im.group(0)
            if 'alt="%s"' % name in tag:
                dm = re.search(r'data-src="([^"]+)"', tag)
                if dm:
                    pic = self._abs(dm.group(1))
                    break
        if not pic:
            dm = re.search(r'data-src="([^"]*/upload/vod/[^"]+)"', html)
            pic = self._abs(dm.group(1)) if dm else ""
        dm = re.search(r'<meta name="description" content="([^"]*)"', html)
        content = dm.group(1) if dm else ""
        ym = re.search(r'/search/-------------(\d{4})/', html)
        year = ym.group(1) if ym else ""

        def _row(label):
            m = re.search(r'<strong[^>]*>%s\s*:</strong>(.*?)</div>' % label, html, re.S)
            if not m:
                return ""
            return ",".join(x.strip() for x in re.findall(r'<a[^>]*>([^<]+)</a>', m.group(1)) if x.strip())

        actor = _row("演员").replace(",", " / ")
        director = _row("导演").replace(",", " / ")
        vclass = _row("类型")

        lines = {}
        order = []
        for m in re.finditer(r'href="/playGV%s-(\d+)-(\d+)/"[^>]*>([^<]*)</a>' % re.escape(vid), html):
            sid, nid, label = m.group(1), m.group(2), m.group(3).strip()
            if sid not in lines:
                lines[sid] = []
                order.append(sid)
            label = label or nid
            label = label.replace("$", "＄").replace("#", "＃")
            lines[sid].append((label, nid))

        froms, plays = [], []
        for sid in order:
            eps = lines[sid]
            code = ""
            first = self._get("%s/playGV%s-%s-%s/" % (self.host, vid, sid, eps[0][1]))
            code = str(self._player_data(first).get("from") or "")
            froms.append(FROM_NAME.get(code, code or ("线路%s" % sid)))
            plays.append("#".join("%s$%s@@%s@@%s" % (label, vid, sid, nid) for label, nid in eps))
        max_nid = max((int(n) for _, n in lines[order[0]]) ) if order else 0
        vod = {
            "vod_id": vid,
            "vod_name": name.replace("$", "＄").replace("#", "＃"),
            "vod_pic": pic,
            "vod_year": year,
            "vod_area": "",
            "vod_class": vclass,
            "vod_actor": actor,
            "vod_director": director,
            "vod_content": content,
            "vod_remarks": ("更新至%d集" % max_nid) if max_nid else "",
            "vod_play_from": "$$$".join(froms),
            "vod_play_url": "$$$".join(plays),
        }
        return {"list": [vod]}

    def playerContent(self, flag, id, vipFlags):
        pid = str(id or "").rsplit("$", 1)[-1]
        vid = sid = nid = ""
        m = re.search(r'playGV(\d+)-(\d+)-(\d+)', pid)
        if m:
            vid, sid, nid = m.group(1), m.group(2), m.group(3)
        else:
            parts = pid.split("@@")
            if len(parts) == 3:
                vid, sid, nid = parts
        if not vid:
            return {"parse": 0, "url": "", "msg": "缺少播放参数"}
        play_url = "%s/playGV%s-%s-%s/" % (self.host, vid, sid, nid)
        html = self._get(play_url)
        data = self._player_data(html)
        raw = str(data.get("url") or "")
        url = ""
        try:
            enc = int(data.get("encrypt") or 0)
        except Exception:
            enc = 0
        try:
            if enc == 2:
                url = urllib.parse.unquote(base64.b64decode(raw).decode("utf-8", "ignore"))
            elif enc == 1:
                url = urllib.parse.unquote(raw)
            else:
                url = raw.replace("\\/", "/")
        except Exception:
            url = ""
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = self._abs(url)
        if not url:
            # 兜底: 交回播放页给壳嗅探
            return {"parse": 0, "url": play_url, "header": {"User-Agent": PLAYER_UA}}
        return {"parse": 0, "url": url, "header": {"User-Agent": PLAYER_UA}}

    def isVideoFormat(self, url):
        u = str(url or "").lower()
        return any(u.split("?")[0].endswith(e) for e in (".m3u8", ".mp4", ".mkv", ".flv", ".ts"))
