#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AvPlay 影视源 (T4)
逆向来源: AvPlay-2.0.0 APK (JavDB 客户端)
Base URL: https://jdforrepam.com
签名: 请求头 jdSignature = "{ts}.lpw6vgqzsp.{md5(ts+salt)}"
播放: JavDB 本身不提供直链, 用磁力链接 (magnet:) 播放
注意: API 可能已下线/返回404, 所有请求都做了异常保护, 失败时返回空列表
"""

import hashlib
import time
import json
import re
import gzip
import ssl
import urllib.request
import urllib.parse
import http.cookiejar

try:
    from base.spider import Spider as SpiderBase
except ImportError:
    # T4 框架外独立运行时兜底
    class SpiderBase(object):
        def getCache(self, key):
            return None

        def setCache(self, key, value):
            return "fail"

        def delCache(self, key):
            return "fail"


# ============ APK 反编译提取的签名配置 ============
_SIGN_SALT = "71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa"
_UA = "AvPlay/2.0.0"


def make_signature(ts=None):
    """生成 jdSignature 请求头: {时间戳}.lpw6vgqzsp.{md5(时间戳+盐)}"""
    if ts is None:
        ts = int(time.time())
    md5 = hashlib.md5(("%d%s" % (ts, _SIGN_SALT)).encode("utf-8")).hexdigest()
    return "%d.lpw6vgqzsp.%s" % (ts, md5)


class Spider(SpiderBase):
    def __init__(self):
        super(Spider, self).__init__()
        # API 主域名 (APK 反编译所得)
        self.base = "https://jdforrepam.com"
        self.options = {}

        # 忽略证书校验, 避免自签/过期证书导致请求失败
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=self.ctx)
        )

    def init(self, extend=""):
        # 解析扩展配置, 允许通过 extend 覆盖域名
        if isinstance(extend, dict):
            self.options = extend
        elif extend:
            try:
                self.options = json.loads(extend)
            except Exception:
                self.options = {}
        custom = self.options.get("host") or self.options.get("siteUrl")
        if custom:
            self.base = str(custom).rstrip("/")
        return True

    def getName(self):
        return "AvPlay"

    # ============ 网络层 ============
    def _get_json(self, path, params=None, timeout=8):
        """带 jdSignature 签名的 GET 请求, 任何异常返回空 dict, 不抛异常"""
        try:
            url = self.base + path
            if params:
                url += "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={
                "jdSignature": make_signature(),
                "User-Agent": _UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip"
            })
            with self.opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                # 解 gzip
                if raw.startswith(b"\x1f\x8b"):
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass
                data = json.loads(raw.decode("utf-8", errors="ignore"))
                return data if isinstance(data, dict) else {}
        except Exception:
            # API 下线/404/超时等全部吞掉, 上层按空数据处理
            return {}

    # ============ 解析层 ============
    def _pick_list(self, data):
        """从未知结构的 JSON 里尽量捞出列表"""
        try:
            if isinstance(data, list):
                return data
            if not isinstance(data, dict):
                return []
            # 先看 data 嵌套层
            inner = data.get("data", data)
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                for k in ("list", "videos", "items", "results", "rows", "records", "magnets", "topics"):
                    v = inner.get(k)
                    if isinstance(v, list):
                        return v
            # 再看顶层
            for k in ("list", "videos", "items", "results", "rows", "records", "magnets", "topics"):
                v = data.get(k)
                if isinstance(v, list):
                    return v
            return []
        except Exception:
            return []

    def _parse_video(self, m):
        """单条视频解析, 异常返回 None, 不影响整体"""
        try:
            if not isinstance(m, dict):
                return None
            # id 字段多种可能
            vid = m.get("movieId") or m.get("id") or m.get("movie_id") or m.get("code") or ""
            vid = str(vid).strip()
            if not vid:
                return None
            # 标题
            name = m.get("title") or m.get("name") or m.get("movieName") or vid
            # 封面
            pic = m.get("cover") or m.get("pic") or m.get("img") or m.get("poster") or m.get("image") or ""
            pic = str(pic).strip()
            if pic.startswith("//"):
                pic = "https:" + pic
            # 备注 (番号/时长等)
            remarks = m.get("code") or m.get("movieCode") or ""
            remarks = str(remarks).strip()
            return {
                "vod_id": vid,
                "vod_name": str(name).strip(),
                "vod_pic": pic,
                "vod_remarks": remarks,
                "style": {"type": "rect", "ratio": 1.42}
            }
        except Exception:
            return None

    def _video_list(self, api_path, params, page):
        """通用视频列表拉取: 请求 -> 捞列表 -> 逐条解析"""
        res = self._get_json(api_path, params)
        items = self._pick_list(res)
        v_list = []
        for m in items:
            v = self._parse_video(m)
            if v:
                v_list.append(v)
        # 分页总数尽量从响应里取, 取不到就按"满页则有下一页"估算
        pagecount = page
        try:
            inner = res.get("data") if isinstance(res.get("data"), dict) else {}
            for k in ("totalPages", "totalPage", "pages"):
                if inner.get(k):
                    pagecount = int(inner[k])
                    break
            else:
                if len(v_list) >= int(params.get("limit", 20) if isinstance(params, dict) else 20):
                    pagecount = page + 1
        except Exception:
            pass
        return v_list, pagecount

    # ============ T4 接口 ============
    def homeContent(self, filter):
        classes = [
            {"type_name": "🆕 最新影片", "type_id": "api@@latest"},
            {"type_name": "🔥 日榜", "type_id": "rank@@day"},
            {"type_name": "📈 周榜", "type_id": "rank@@week"},
            {"type_name": "🏆 月榜", "type_id": "rank@@month"},
            {"type_name": "📂 专题", "type_id": "folder@@topics"},
            {"type_name": "🔍 热门搜索", "type_id": "folder@@hotsearch"}
        ]
        return {"class": classes, "filters": {}}

    def homeVideoContent(self):
        # 首页推荐 = 最新影片
        v_list, _ = self._video_list("/api/videos", {"page": 1, "limit": 20}, 1)
        return {"list": v_list}

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if str(pg).isdigit() else 1
        tid = str(tid)

        # ---- 最新影片 ----
        if tid.startswith("api@@latest"):
            v_list, pagecount = self._video_list(
                "/api/videos", {"page": page, "limit": 20}, page)
            return {"page": page, "pagecount": pagecount, "limit": len(v_list),
                    "total": 9999, "list": v_list}

        # ---- 排行: 日榜/周榜/月榜 ----
        if tid.startswith("rank@@"):
            period = tid.replace("rank@@", "")
            v_list, pagecount = self._video_list(
                "/api/ranks/%s" % period, {"page": page, "limit": 20}, page)
            return {"page": page, "pagecount": pagecount, "limit": len(v_list),
                    "total": 9999, "list": v_list}

        # ---- 专题列表 (二级导航) ----
        if tid == "folder@@topics":
            items = self._pick_list(self._get_json("/api/topics"))
            v_list = []
            for t in items:
                try:
                    if not isinstance(t, dict):
                        continue
                    t_id = str(t.get("id") or t.get("topicId") or "").strip()
                    t_name = str(t.get("title") or t.get("name") or "").strip()
                    if not t_id or not t_name:
                        continue
                    v_list.append({
                        "vod_id": "tag@@%s" % t_id,
                        "vod_name": t_name,
                        "vod_pic": "",
                        "vod_remarks": "专题",
                        "vod_tag": "folder",
                        "style": {"type": "rect", "ratio": 1.78}
                    })
                except Exception:
                    continue
            return {"page": 1, "pagecount": 1, "limit": len(v_list),
                    "total": len(v_list), "list": v_list}

        # ---- 热门搜索词 (点击直接搜) ----
        if tid == "folder@@hotsearch":
            items = self._pick_list(self._get_json("/api/search/hot"))
            v_list = []
            for w in items:
                try:
                    if isinstance(w, str):
                        kw = w.strip()
                    elif isinstance(w, dict):
                        kw = str(w.get("keyword") or w.get("word") or w.get("text") or w.get("name") or "").strip()
                    else:
                        continue
                    if not kw:
                        continue
                    v_list.append({
                        "vod_id": "search@@%s" % kw,
                        "vod_name": "🔍 %s" % kw,
                        "vod_pic": "",
                        "vod_remarks": "热门搜索",
                        "vod_tag": "folder",
                        "style": {"type": "rect", "ratio": 1.78}
                    })
                except Exception:
                    continue
            return {"page": 1, "pagecount": 1, "limit": len(v_list),
                    "total": len(v_list), "list": v_list}

        # ---- 标签/专题下的视频 ----
        if tid.startswith("tag@@"):
            tag_id = tid.replace("tag@@", "")
            v_list, pagecount = self._video_list(
                "/api/videos/tag/%s" % urllib.parse.quote(tag_id),
                {"page": page, "limit": 20}, page)
            return {"page": page, "pagecount": pagecount, "limit": len(v_list),
                    "total": 9999, "list": v_list}

        # ---- 热门搜索词点击 -> 走搜索 ----
        if tid.startswith("search@@"):
            return self.searchContent(tid.replace("search@@", ""), False, pg)

        # 未知分类, 返回空
        return {"page": page, "pagecount": page, "limit": 0, "total": 0, "list": []}

    def detailContent(self, ids):
        raw_id = ids[0] if isinstance(ids, (list, tuple)) else str(ids)
        raw_id = str(raw_id).strip()

        # 二级导航的 folder 点击走分类
        if raw_id.startswith(("folder@@", "tag@@", "search@@")):
            return self.categoryContent(raw_id, 1, None, None)

        mid = raw_id
        # ---- 尽量抓详情 (端点为常见猜测, 失败无妨) ----
        title, pic, actors, genres, desc, year = "", "", "", "", "", ""
        for p in ("/api/videos/%s", "/api/video/%s", "/api/movies/%s"):
            try:
                info = self._get_json(p % urllib.parse.quote(mid))
                inner = info.get("data") if isinstance(info.get("data"), dict) else info
                if not isinstance(inner, dict) or not inner:
                    continue
                title = str(inner.get("title") or inner.get("name") or "").strip()
                pic = str(inner.get("cover") or inner.get("pic") or inner.get("img") or inner.get("poster") or "").strip()
                if pic.startswith("//"):
                    pic = "https:" + pic
                # 演员
                acts = inner.get("stars") or inner.get("actors") or []
                if isinstance(acts, list):
                    names = []
                    for a in acts:
                        try:
                            if isinstance(a, dict):
                                n = str(a.get("name") or "").strip()
                            else:
                                n = str(a).strip()
                            if n:
                                names.append(n)
                        except Exception:
                            continue
                    actors = ", ".join(names)
                # 类型
                gs = inner.get("genres") or inner.get("tags") or []
                if isinstance(gs, list):
                    gnames = []
                    for g in gs:
                        try:
                            if isinstance(g, dict):
                                n = str(g.get("name") or "").strip()
                            else:
                                n = str(g).strip()
                            if n:
                                gnames.append(n)
                        except Exception:
                            continue
                    genres = " / ".join(gnames)
                desc = str(inner.get("description") or inner.get("desc") or "").strip()
                year = str(inner.get("date") or inner.get("year") or "")[:4]
                if title:
                    break
            except Exception:
                continue

        # ---- 磁力链接 ----
        mag_items = []
        seen = set()
        for mg in self._pick_list(self._get_json("/magnets", {"movieId": mid})):
            try:
                if isinstance(mg, str):
                    link, label, size = mg.strip(), "磁力", ""
                elif isinstance(mg, dict):
                    link = str(mg.get("link") or mg.get("url") or mg.get("magnet") or "").strip()
                    label = str(mg.get("title") or mg.get("name") or "磁力").strip()
                    size = str(mg.get("size") or "").strip()
                else:
                    continue
                if not link.startswith("magnet:"):
                    continue
                if link in seen:
                    continue
                seen.add(link)
                # 按 btih 去重
                show = ("%s %s" % (label, size)).strip().replace("$", " ")
                mag_items.append("%s$%s" % (show, link))
            except Exception:
                continue

        play_from, play_url = [], []
        if mag_items:
            play_from.append("磁力下载")
            play_url.append("#".join(mag_items))
        else:
            play_from.append("暂无可用线路")
            play_url.append("暂无可用线路$http://127.0.0.1")

        vod_detail = {
            "vod_id": mid,
            "vod_name": title or mid,
            "vod_pic": pic,
            "type_name": genres or "日本AV",
            "vod_year": year,
            "vod_area": "日本",
            "vod_remarks": "",
            "vod_actor": actors,
            "vod_director": "",
            "vod_content": desc or (title or mid),
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url)
        }
        return {"list": [vod_detail]}

    def playerContent(self, flag, id, vipFlags):
        # id 即磁力链接 (详情页已按 "名称$magnet" 切好, 传到这里的是 magnet 部分)
        magnet = str(id).strip()
        # 兼容万一传进来的是 "名$magnet" 完整格式
        if "$" in magnet and not magnet.startswith("magnet:"):
            magnet = magnet.split("$")[-1].strip()
        return {
            "parse": 0,
            "jx": 0,
            "url": magnet,
            "header": {"User-Agent": _UA}
        }

    def searchContent(self, key, quick, pg="1"):
        page = int(pg) if str(pg).isdigit() else 1
        v_list, pagecount = self._video_list(
            "/api/search",
            {"keyword": str(key).strip(), "page": page, "limit": 20},
            page)
        return {"page": page, "pagecount": pagecount, "limit": len(v_list),
                "total": 9999, "list": v_list}

    def action(self, action):
        return {"msg": "ok"}

    def liveContent(self):
        return ""

    def localProxy(self, params):
        return [404, "text/plain; charset=utf-8", "Proxy not configured"]

    def destroy(self):
        self.options = {}
