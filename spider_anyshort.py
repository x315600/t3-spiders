# 网站名称: AnyShort (anyshort.net)
# 网站特征: 自研JSON API·POST /v1/auth/session取会话令牌(30分钟有效)·401自动重登·无反爬·内容语言=繁体中文(zh-Hant，封面走 media.anyshort.net 快)
# m3u8特征: 无m3u8，mp4签名直链(tiktokcdn，约1.6小时有效)，streams多清晰度
# 最后验证: 2026-10-14 五接口实测通过（zh-Hans）
try:
    from base.spider import Spider as BaseSpider
except Exception:
    BaseSpider = object

import json
import time
import re
from urllib.parse import quote

try:
    import requests
except Exception:
    requests = None

class Spider(BaseSpider):
    # 来源集合（对应官网三个 kho 集合；pinedrama 的 browse 按来源过滤返回空，已剔除）
    SOURCES = [("繽紛萬象", "netshort"), ("時尚上頭", "reelshort")]
    # 僵尸题材：有 title_count 但 zh-Hant 下无标题（2026-10-14 实测），rep 3 上流社會、rep 8 虐戀情深
    _DEAD_TAGS = {3, 8}

    # 站点名称，T4 框架展示用
    def getName(self): return "AnyShort"

    # 初始化：定域名、请求头、内容语言（繁体中文：封面走站内 media.anyshort.net，比 tiktokcdn 快），顺手拿会话令牌
    # UA 用移动端：API 按客户端类型下发播放流，桌面 UA 可能拿到 h265，手机播不了
    def init(self, extend=""):
        self.host = "https://anyshort.net"
        self.lang = "zh-Hant"
        self.headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36", "Accept": "application/json", "Accept-Language": self.lang + ",zh;q=0.9", "Origin": self.host, "Referer": self.host + "/" + self.lang + "/"}
        self.token = ""
        self._auth()

    # 拿会话令牌：POST /v1/auth/session，body 只需 {"platform": "web"}，返回 access_token（约30分钟有效）
    def _auth(self):
        try: self.token = requests.post(self.host + "/v1/auth/session", json={"platform": "web"}, headers=self.headers, timeout=15).json().get("data", {}).get("access_token", "")
        except Exception: self.token = ""

    # 带令牌的 GET：401 就重登一次再试，最多两轮，失败回空字典不抛异常
    def _api(self, path):
        for _ in range(2):
            try:
                h = dict(self.headers); h["authorization"] = "Bearer " + self.token
                r = requests.get(self.host + path, headers=h, timeout=15)
                if r.status_code == 401: self._auth(); continue
                return r.json()
            except Exception: time.sleep(0.5)
        return {}

    # 条目归一：API 的 title_id/name/cover_url 转成框架要的 vod_ 字段，单条异常不连累整体
    def _item(self, v):
        try: return {"vod_id": str(v.get("title_id", "")), "vod_name": v.get("name", ""), "vod_pic": v.get("cover_url", "") or ""}
        except Exception: return None

    # 首页：分类 = 最新更新 + 熱門榜 + 来源集合 + 热门中文题材
    # 题材按 rep_id 去重（同名马甲只留作品数最多的）、过滤非中文与僵尸 ID，按作品数取前 18
    def homeContent(self, filter):
        classes = [{"type_id": "newest", "type_name": "最新更新"}, {"type_id": "popular", "type_name": "熱門榜"}]
        classes += [{"type_id": "src:" + code, "type_name": name} for name, code in self.SOURCES]
        best = {}
        for t in self._api(f"/v1/tags?lang={self.lang}").get("data", []):
            rep, name = t.get("rep_id"), t.get("name") or ""
            if not rep or rep in self._DEAD_TAGS or not re.search(r"[\u4e00-\u9fff]", name): continue
            if rep not in best or (t.get("title_count") or 0) > (best[rep].get("title_count") or 0): best[rep] = t
        tags = sorted(best.values(), key=lambda t: -(t.get("title_count") or 0))[:18]
        classes += [{"type_id": "tag:" + str(t.get("rep_id")), "type_name": t.get("name")} for t in tags]
        return {"class": classes, "list": [], "filters": {}}

    # 首页推荐：/v1/home 的 rails 推荐位全部摊平成列表
    def homeVideoContent(self):
        data = self._api(f"/v1/home?lang={self.lang}").get("data", {}).get("rails", [])
        items = [it for rail in data for v in rail.get("items", []) for it in [self._item(v)] if it and it["vod_id"]]
        return {"list": items}

    # 分类浏览：newest/popular 走 /v1/browse?sort=；src: 走 ?source=；tag: 走 /v1/tags/{id}/titles；都支持 ?page=
    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if pg else 1
        if tid in ("newest", "popular"): data = self._api(f"/v1/browse?lang={self.lang}&page={pg}&sort={tid}")
        elif tid.startswith("src:"): data = self._api(f"/v1/browse?lang={self.lang}&page={pg}&source={tid[4:]}")
        elif tid.startswith("tag:"): data = self._api(f"/v1/tags/{tid[4:]}/titles?lang={self.lang}&page={pg}")
        else: data = self._api(f"/v1/browse?lang={self.lang}&page={pg}&sort=newest")
        items = [it for v in data.get("data", []) for it in [self._item(v)] if it and it["vod_id"]]
        p = data.get("page", {})
        return {"page": pg, "pagecount": p.get("pages", 1) or 1, "limit": p.get("limit", 18), "total": p.get("total", 0), "list": items}

    # 详情：/v1/titles/{id} 取简介封面，/v1/titles/{id}/episodes?offset=N 翻完所有剧集（每页60，offset 步进）
    # 播放列表拼成「第N集$episode_id」用 # 连接，线路名 AnyShort
    def detailContent(self, ids):
        result = {"list": []}
        for vid in ids:
            try:
                v = self._api(f"/v1/titles/{vid}?lang={self.lang}").get("data", {})
                eps, offset = [], 0
                while True:
                    d = self._api(f"/v1/titles/{vid}/episodes?lang={self.lang}&offset={offset}")
                    eps += d.get("data", [])
                    if not d.get("page", {}).get("has_more"): break
                    offset += 60
                play = "#".join(f"第{e.get('episode_no')}${e.get('episode_id')}" for e in eps if e.get("episode_id"))
                result["list"].append({"vod_id": str(vid), "vod_name": v.get("name", ""), "vod_pic": v.get("cover_url", "") or "", "vod_content": (v.get("description", "") or "").strip(), "vod_play_from": "AnyShort", "vod_play_url": play})
            except Exception: continue
        return result

    # 搜索：/v1/search?q=关键词，关键词要 quote 编码
    def searchContent(self, key, quick, pg="1"):
        pg = int(pg) if pg else 1
        data = self._api(f"/v1/search?q={quote(key)}&lang={self.lang}&page={pg}").get("data", [])
        return {"list": [it for v in data for it in [self._item(v)] if it and it["vod_id"]], "page": pg}

    # 播放：/v1/episodes/{episode_id}/play 取 streams
    # 优先用 API 推荐的 selected（官方客户端默认播的，一般是 h264，兼容性最好）；没有再按画质数字选最高
    # 注意：别无脑选 1080p，那个多是 h265，很多手机壳播不了
    # parse 规则：mp4 直链用 parse=0 秒播；m3u8 必须 parse=1 交给壳内解析，否则播不了
    # 直链是签名地址，约 1.6 小时有效，每次现取现用
    def playerContent(self, flag, id, vipFlags):
        data = self._api(f"/v1/episodes/{id}/play?lang={self.lang}").get("data", {})
        url = (data.get("selected") or {}).get("url", "")
        if not url:
            streams = data.get("streams", [])
            def _q(s):
                mm = re.search(r"(\d+)", str(s.get("quality") or ""))
                return int(mm.group(1)) if mm else 0
            h264 = [s for s in streams if "264" in str(s.get("codec", ""))]
            pool = h264 or streams
            url = sorted(pool, key=_q)[-1].get("url", "") if pool else ""
        return {"parse": 0 if ".m3u8" not in url else 1, "url": url, "header": json.dumps(self.headers)}
