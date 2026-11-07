# -*- coding: utf-8 -*-
"""
girigiri愛動漫 (ani.girigirilove.com) T3 源
2026-11-07 重写: 站点已改为 React SPA, 列表/详情页只剩 5.5KB 空壳, 旧 SSR 解析(/GV{id}/ 卡片)全部失效。
新链路: 前端 JS 逆向得到 API 域名 https://m3u8.girigirilove.com (VITE_API_ORIGIN),
接口均为 GET /api.php/Vod/*, 返回 {code:1, info:{...}}:
- 列表: /Vod/get_list?type_id=&offset=&limit=20&orderby=(空/hits/score)&vod_class=&vod_lang=&vod_area=&vod_year=&vod_state=&vod_version=&vod_name=(搜索)
- 详情: /Vod/get_detail?vod_id=  (vod_play_url 内含 m3u8 直链, 多线路以 $$$ 分隔)
- 筛选枚举: /Vod/getAllTagOption (vod_lang/vod_version/vod_state/vod_class)
  /Vod/get_class?type_id= /Vod/get_area?type_id_1=0 /Vod/get_year?type_id_1=0
- 播放: vod_play_url 里的就是直链 m3u8 (akua.girigirilove.com), parse=0
实测: 2026-11-07 列表/详情/搜索/播放 m3u8 HTTP 200 全通。
"""
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

API_HOST = "https://m3u8.girigirilove.com/api.php"
WEB_HOST = "https://ani.girigirilove.com"
PIC_HOST = "https://jpg.girigirilove.com"
UA = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
LIMIT = 20

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
        {"n": "按最新", "v": ""}, {"n": "按最热", "v": "hits"}, {"n": "按评分", "v": "score"}]},
    "class": {"key": "class", "name": "类型", "value": _opts(_CLASS_OPTS)},
    "lang": {"key": "lang", "name": "语言", "value": _opts(["日语", "国语"])},
    "area": {"key": "area", "name": "月份", "value": _opts(_MONTH_OPTS)},
    "state": {"key": "state", "name": "改编", "value": _opts(["原创", "小说改", "漫画改", "游戏改"])},
    "version": {"key": "version", "name": "版本", "value": _opts(["TV番", "泡面番", "OVA"])},
    "year": {"key": "year", "name": "年份", "value": _opts(_YEAR_OPTS)},
}

FROM_NAME = {"cht": "繁体", "chs": "简体"}


class Spider(BaseSpider):
    def __init__(self):
        super().__init__()
        self.api = API_HOST
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
        api = str(ext.get("api") or "").rstrip("/")
        if api.startswith("http"):
            self.api = api

    def destroy(self):
        pass

    # ---------------- 网络 ----------------
    def _api(self, path, params=None):
        url = self.api + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v != "" and v is not None})
        headers = {"User-Agent": UA, "Accept": "application/json", "Referer": WEB_HOST + "/"}
        for _ in range(3):
            try:
                if requests is not None:
                    if self._session is None:
                        self._session = requests.Session()
                    r = self._session.get(url, headers=headers, timeout=15)
                    if r.status_code == 200 and r.text:
                        return r.json()
                    continue
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8", "ignore"))
            except Exception:
                continue
        return {}

    # ---------------- 归一 ----------------
    def _abs_pic(self, pic):
        pic = (pic or "").strip()
        if not pic:
            return ""
        if pic.startswith("http"):
            return pic
        if pic.startswith("//"):
            return "https:" + pic
        if pic.startswith("upload/"):
            return PIC_HOST + "/" + pic
        if pic.startswith("/"):
            return PIC_HOST + pic
        return pic

    def _item(self, row):
        try:
            return {
                "vod_id": str(row.get("vod_id")),
                "vod_name": str(row.get("vod_name") or "").replace("$", "＄").replace("#", "＃"),
                "vod_pic": self._abs_pic(str(row.get("vod_pic") or "")),
                "vod_remarks": str(row.get("vod_remarks") or ""),
            }
        except Exception:
            return None

    def _list(self, params, pg):
        pg = int(pg or 1)
        params = dict(params or {})
        params["offset"] = (pg - 1) * LIMIT
        params["limit"] = LIMIT
        data = self._api("/Vod/get_list", params)
        info = data.get("info") or {}
        rows = info.get("rows") or []
        total = int(info.get("total") or 0)
        vods = [it for r in rows for it in [self._item(r)] if it]
        pagecount = max(1, (total + LIMIT - 1) // LIMIT) if total else 1
        return {"list": vods, "page": pg, "pagecount": pagecount,
                "limit": LIMIT, "total": total}

    # ---------------- 接口 ----------------
    def homeContent(self, filter=False):
        result = {"class": CLASSES}
        if filter:
            result["filters"] = {c["type_id"]: [FILTERS[k] for k in ("by", "class", "lang", "area", "state", "version", "year")]
                                 for c in CLASSES}
        return result

    def homeVideoContent(self):
        return self._list({"orderby": "hits"}, 1)

    def categoryContent(self, tid, pg, filter, extend):
        ext = extend if isinstance(extend, dict) else {}
        params = {
            "type_id": str(tid),
            "orderby": str(ext.get("by") or ""),
            "vod_class": str(ext.get("class") or ""),
            "vod_lang": str(ext.get("lang") or ""),
            "vod_area": str(ext.get("area") or ""),
            "vod_year": str(ext.get("year") or ""),
            "vod_state": str(ext.get("state") or ""),
            "vod_version": str(ext.get("version") or ""),
        }
        return self._list(params, pg)

    def searchContent(self, key, quick, pg="1"):
        return self._list({"vod_name": str(key)}, pg)

    def detailContent(self, ids):
        result = {"list": []}
        for vid in ids:
            try:
                data = self._api("/Vod/get_detail", {"vod_id": str(vid)})
                info = data.get("info") or {}
                if not info:
                    continue
                play_from = str(info.get("vod_play_from") or "")
                play_url = str(info.get("vod_play_url") or "")
                # 多线路: from/url 均以 $$$ 分隔, 逐对映射
                froms = play_from.split("$$$") if play_from else [""]
                urls = play_url.split("$$$") if play_url else [""]
                names, url_list = [], []
                for i, u in enumerate(urls):
                    f = froms[i] if i < len(froms) else (froms[0] if froms else "")
                    names.append(FROM_NAME.get(f, f) or "在线")
                    url_list.append(u)
                content = re.sub(r"<[^>]+>", "", str(info.get("vod_content") or "")).strip()
                result["list"].append({
                    "vod_id": str(info.get("vod_id") or vid),
                    "vod_name": str(info.get("vod_name") or ""),
                    "vod_pic": self._abs_pic(str(info.get("vod_pic") or "")),
                    "vod_year": str(info.get("vod_year") or ""),
                    "vod_area": str(info.get("vod_area") or ""),
                    "vod_lang": str(info.get("vod_lang") or ""),
                    "vod_actor": str(info.get("vod_actor") or ""),
                    "vod_director": str(info.get("vod_director") or ""),
                    "vod_content": content,
                    "vod_remarks": str(info.get("vod_remarks") or ""),
                    "vod_play_from": "$$$".join(names),
                    "vod_play_url": "$$$".join(url_list),
                })
            except Exception:
                continue
        return result

    def playerContent(self, flag, id, vipFlags):
        url = (id or "").strip()
        return {"parse": 0, "url": url,
                "header": json.dumps({"User-Agent": UA, "Referer": WEB_HOST + "/"})}
