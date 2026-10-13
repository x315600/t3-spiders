# -*- coding: utf-8 -*-
# 瓜子影视 gz360.tv · T3源
# 站型: Nuxt2 SPA + 独立API
# API: https://haiwaiapi.1fc8ab0.com/H5 (备: api.txxhuc.com / hyperf.718fd9f.com)
# 全部 POST JSON, body 带 phone_type:4, 无签名无token
# 分类映射取自前端JS: {1:电影,2:电视剧,3:综艺,4:动漫,64:短剧}
# 详情直给 m3u8 (play_url), 分集 /Resource/GetOnePlayList (page/page_size)
# 已知限制: 分类分页接口未找到(用latest按t_id过滤,pagecount=1);
#          搜索接口 /Search/GetList 在现网API返回"不存在的路由", searchContent 返回空
try:
    from base.spider import Spider as BaseSpider
except Exception:
    BaseSpider = object

import json

try:
    import requests
except Exception:
    requests = None


class Spider(BaseSpider):
    APIS = [
        "https://haiwaiapi.1fc8ab0.com",
        "https://api.txxhuc.com",
        "https://hyperf.718fd9f.com",
    ]
    CATES = [
        {"type_id": "1", "type_name": "电影"},
        {"type_id": "2", "type_name": "电视剧"},
        {"type_id": "3", "type_name": "综艺"},
        {"type_id": "4", "type_name": "动漫"},
        {"type_id": "64", "type_name": "短剧"},
    ]
    # 分类 pid 映射 (Category/GetChoiceList, 实测)
    # pid=3→电影 pid=4→电视剧 pid=6→综艺 pid=5→动漫 pid=1→精选(混)
    PID_MAP = {"1": 3, "2": 4, "3": 6, "4": 5}
    UA = ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
          "Chrome/131.0.0.0 Mobile Safari/537.36")

    def init(self, extend=""):
        apis = list(self.APIS)
        if extend:
            if isinstance(extend, dict):
                h = extend.get("api") or extend.get("host")
                if h:
                    apis = [h.rstrip("/")] + apis
            elif isinstance(extend, str):
                e = extend.strip()
                if e.startswith("{"):
                    try:
                        h = json.loads(e).get("api") or json.loads(e).get("host")
                        if h:
                            apis = [h.rstrip("/")] + apis
                    except Exception:
                        pass
                elif e.startswith("http"):
                    apis = [e.rstrip("/")] + apis
        self.apis = apis
        self.api_idx = 0
        self.session = None
        if requests:
            s = requests.Session()
            s.headers.update({
                "User-Agent": self.UA,
                "Origin": "https://gz360.tv",
                "Referer": "https://gz360.tv/",
            })
            self.session = s

    def _post(self, path, body):
        if not self.session:
            return None
        body = dict(body or {})
        body.setdefault("phone_type", 4)
        for i in range(len(self.apis)):
            idx = (self.api_idx + i) % len(self.apis)
            url = self.apis[idx] + "/H5" + path
            try:
                r = self.session.post(url, json=body, timeout=20)
                if r.status_code != 200:
                    continue
                d = r.json()
                if d.get("code") == 200:
                    self.api_idx = idx
                    return d.get("data")
            except Exception:
                continue
        return None

    def _clean(self, s):
        return str(s or "").replace("$", "＄").replace("#", "＃").strip()

    def _vod(self, it):
        vid = str(it.get("vod_id") or "")
        if not vid:
            return None
        name = self._clean(it.get("vod_name") or it.get("c_name") or "")
        pic = it.get("vod_pic") or it.get("c_pic") or ""
        return {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": self._clean(
                it.get("vod_continu") or it.get("cf_name") or ""),
        }

    def homeContent(self, filter):
        return {"class": self.CATES}

    def homeVideoContent(self):
        data = self._post("/Index/latestVideo", {}) or []
        lst = [v for v in (self._vod(x) for x in data) if v]
        return {"list": lst}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        pid = self.PID_MAP.get(str(tid))
        if pid:
            # 分页接口
            data = self._post("/Category/GetChoiceList",
                              {"pid": pid, "page": pg, "page_size": 30}) or {}
            lst = [v for v in (self._vod(x) for x in data.get("list", [])) if v]
            total = int(data.get("total") or 0)
            pagecount = max(1, (total + 29) // 30) if total else 1
            return {
                "list": lst,
                "page": pg,
                "pagecount": pagecount,
                "limit": 30,
                "total": total,
            }
        # 短剧无对应pid, 用latest按t_id过滤(单页)
        data = self._post("/Index/latestVideo", {}) or []
        lst = []
        for x in data:
            if str(x.get("t_id")) == str(tid):
                v = self._vod(x)
                if v:
                    lst.append(v)
        return {
            "list": lst if pg == 1 else [],
            "page": pg,
            "pagecount": 1,
            "limit": 20,
            "total": len(lst),
        }

    def searchContent(self, key, quick, pg="1"):
        # /Search/GetList 现网返回"不存在的路由",暂无可用搜索接口
        return {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}

    def _playlist(self, vid):
        eps = []
        pg = 1
        while True:
            data = self._post("/Resource/GetOnePlayList",
                              {"vod_id": str(vid), "page": pg, "page_size": 100})
            if not data:
                break
            for u in data.get("urls") or []:
                url = u.get("url") or ""
                if not url.startswith("http"):
                    continue
                nm = self._clean(str(u.get("name") or "")) or ("第%s集" % u.get("sort", len(eps) + 1))
                eps.append("%s$%s" % (nm, url))
            total = int(data.get("total_vod_vurl") or 0)
            if total and len(eps) >= total:
                break
            if data.get("is_end"):  # True=无更多
                break
            pg += 1
            if pg > 20:
                break
        # 去重保序
        seen, out = set(), []
        for e in eps:
            u = e.split("$", 1)[1]
            if u not in seen:
                seen.add(u)
                out.append(e)
        return out

    def detailContent(self, ids):
        vid = str(ids[0])
        data = self._post("/Resource/GetVodInfo", {"vod_id": vid}) or {}
        vi = data.get("vodInfo") or {}
        name = self._clean(vi.get("vod_name") or vid)
        pic = vi.get("pic") or ""
        eps = self._playlist(vid)
        vod = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_actor": self._clean(vi.get("vod_actor") or ""),
            "vod_director": self._clean(vi.get("vod_director") or ""),
            "vod_year": str(vi.get("vod_year") or ""),
            "vod_area": self._clean(vi.get("vod_area") or ""),
            "vod_content": self._clean(vi.get("vod_use_content") or ""),
            "vod_remarks": self._clean(vi.get("vod_continu") or ""),
            "vod_play_from": "瓜子" if eps else "",
            "vod_play_url": "#".join(eps),
        }
        return {"list": [vod]}

    def playerContent(self, flag, id, vipFlags):
        # 壳已按 $ 切掉集名, id 即 m3u8 直链
        url = str(id).rsplit("$", 1)[-1]
        if url.startswith("http"):
            return {"parse": 0, "url": url,
                    "header": {"User-Agent": self.UA}}
        return {"parse": 0, "url": url, "header": {}}

    def isVideoFormat(self, url):
        u = (url or "").lower()
        return any(u.split("?")[0].endswith(x)
                   for x in [".m3u8", ".mp4", ".mkv", ".flv", ".ts"])

    def localProxy(self, param=None):
        return [200, "text/plain", None, b""]
