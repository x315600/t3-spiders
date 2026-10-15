#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 网站名称: 134X (134x.com)
# 网站特征: TanStack Start SSR·无加密·CF前无验证墙·requests·SSR内嵌Flight数据正则解析·分页?page=
# m3u8特征: 有m3u8、hlsUrls直链、video.twimg.com CDN、无签名头要求
# 最后验证: 2026-10-16 五接口真实验证(列表20/20·详情变体+m3u8·分片styp·封面JPEG)
import re, json, base64
from urllib.parse import quote
try:
    import requests
    from base.spider import Spider as SpiderBase
except ImportError:
    SpiderBase = object

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Referer": "https://134x.com/"}
TW_RE = re.compile(r'\{id:"[a-f0-9]{24}",name:"((?:[^"\\]|\\.)*)",screenName:"([^"]*)",profileImage:"([^"]*)",tweetId:"(\d+)",text:"((?:[^"\\]|\\.)*)",media:"([^"]*)",threadsCount:\d+,durationMillis:(\d+),playTimes:(\d+),longPlayTimes:(\d+),favoTimes:(\d+),shareTimes:(\d+),postAt:"([^"]*)",mediaMetadataLoaded:![01],posterUrl:"([^"]*)"')
HLS_RE = re.compile(r'hlsUrls:\$R\[\d+\]=\{"[^"]+":"([^"]+\.m3u8)"\}')
VAR_RE = re.compile(r'\{url:"(https://video\.twimg\.com/[^"]+\.mp4)",width:(\d+),height:(\d+),bitrate:(\d+)\}')
ESC_RE = re.compile(r'\\(.)')
ESC_MAP = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}

def _unesc(s): return ESC_RE.sub(lambda m: ESC_MAP.get(m.group(1), m.group(1)), s or "")
def _pack(d): return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")
def _unpack(s): return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode())
def _safe(s): return (s or "").replace("$", "＄").replace("#", "＃").strip()
def _pic(u): return (u or "").replace("_normal.", "_400x400.")
def _num(n):
    n = int(n or 0)
    return f"{n/10000:.1f}万" if n >= 10000 else str(n)

class Spider(SpiderBase):
    def getName(self): return "134X"
    def getDependence(self): return []
    def init(self, extend=""):
        o = extend if isinstance(extend, dict) else {}
        if isinstance(extend, str) and extend.strip() not in ("", "[]", "null"):
            try: o = json.loads(extend)
            except Exception: o = {}
        if not isinstance(o, dict): o = {}
        self.host = str(o.get("host") or "https://134x.com").rstrip("/")
        self.categories = [{"type_id": "featured", "type_name": "精選"}, {"type_id": "popular", "type_name": "熱門"}]
    def _get(self, url):
        try:
            r = requests.get(url, headers=UA, timeout=15)
            return r.text if r.status_code == 200 else None
        except Exception: return None
    def _title(self, text, screen):
        t = _unesc(text).split("\n")[0]
        t = re.sub(r"\s*https?://\S+\s*$", "", t).strip()
        t = (t[:42] + "…") if len(t) > 42 else t
        return _safe(t) or ("@" + screen)
    def _card(self, m):
        name, screen, pic, tid, text, media, dur, plays = m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7]
        mins = int(dur or 0) // 60000
        return {"vod_id": tid, "vod_name": self._title(text, screen), "vod_pic": _pic(pic),
                "vod_remarks": f"{mins}分钟 · {_num(plays)}播放" if mins else f"{_num(plays)}播放"}
    def _parse_list(self, html):
        if not html: return []
        out, seen = [], set()
        for m in TW_RE.finditer(html):
            try:
                if m.group(4) in seen: continue
                seen.add(m.group(4)); out.append(self._card(m.groups()))
            except Exception: continue
        return out
    def _page(self, url, pg):
        pg = int(pg) if pg else 1
        lst = self._parse_list(self._get(url))
        return {"list": lst, "page": pg, "pagecount": pg + 1 if len(lst) >= 20 else max(pg, 1), "limit": 20, "total": 0}
    def homeContent(self, filter=None):
        return {"class": self.categories, "list": [], "filters": {}}
    def homeVideoContent(self):
        return {"list": self._parse_list(self._get(f"{self.host}/featured?page=1"))[:10]}
    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        tid = str(tid or "featured")
        if tid not in ("featured", "popular"): tid = "featured"
        return self._page(f"{self.host}/{tid}?page={int(pg) if pg else 1}", pg)
    def searchContent(self, key, quick=False, pg="1"):
        key = (key or "").strip()
        if not key: return {"list": [], "page": 1, "pagecount": 1}
        return self._page(f"{self.host}/search?q={quote(key)}&page={int(pg) if pg else 1}", pg)
    def detailContent(self, ids):
        tid = ids[0] if isinstance(ids, (list, tuple)) else ids
        html = self._get(f"{self.host}/video/{tid}")
        if not html: return {"list": [], "msg": "获取详情失败"}
        m = TW_RE.search(html)
        if not m: return {"list": [], "msg": "解析详情失败"}
        g = m.groups()
        text = _unesc(g[4])
        content = re.sub(r"\s*https?://\S+", "", text).strip()
        hls = HLS_RE.search(html)
        variants = sorted(VAR_RE.findall(html), key=lambda v: int(v[1]), reverse=True)
        best_mp4 = variants[0][0] if variants else g[5]
        poster = re.search(r'posterUrl:"(https?://[^"]+)"', html)
        pic = poster.group(1) if poster else _pic(g[2])
        flags, urls = [], []
        if hls:
            flags.append("m3u8"); urls.append(f"m3u8${_pack({'u': hls.group(1)})}")
        if best_mp4:
            flags.append("MP4"); urls.append(f"MP4${_pack({'u': best_mp4})}")
        if not flags: return {"list": [], "msg": "无可用播放源"}
        mins = int(g[6] or 0) // 60000
        vod = {"vod_id": tid, "vod_name": self._title(g[4], g[1]), "vod_pic": pic,
               "vod_year": (g[11] or "")[:4], "vod_remarks": f"{mins}分钟" if mins else "",
               "vod_content": _safe(content)[:500], "vod_play_from": "$$$".join(flags), "vod_play_url": "$$$".join(urls)}
        return {"list": [vod]}
    def playerContent(self, flag, id, vipFlags=None):
        pid = id[0] if isinstance(id, (list, tuple)) else id
        try:
            u = _unpack(pid).get("u", "")
            if u.startswith("http"): return {"parse": 0, "url": u, "header": {"User-Agent": UA["User-Agent"]}}
        except Exception: pass
        return {"parse": 0, "url": "", "msg": "播放地址解析失败"}
