#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
91porn 短视频源 (da8ttmrfqqhqf.cloudfront.net)
Vue3 SPA, 接口经 /api/app/* 加载, 请求参数与响应体均为 AES 加密

逆向要点 (2026-10-18, /tmp/main.js):
- 请求参数: JSON -> AES-CBC(key=BxJand%xf5h3sycH, iv=BxJand%xf5h3sycH) -> base64,
  GET 以 ?data= 传递, POST 以 {"data": ...} body 传递
- 请求头: temp: test, X-User-Agent: BuildID=com.abc.Butterfly;... , Authorization: <token>(登录后)
- 响应: {"code":200,"data":"<base64>","hash":true} ; hash 为真时 data 需解密:
  t=base64(data); o=t[:12]; l=interfaceKey+o; i=len(l)>>1;
  n=sha256(l)[8:24]; r=n+l[:i]; s=l[i:]+n;
  c=sha256(r); d=sha256(s);
  key=c[:8]+d[8:24]+c[24:] (32B); iv=d[:4]+c[12:20]+d[28:32] (16B);
  JSON=AES-CBC解密(t[12:], key, iv)
- interfaceKey = "65dc07d1b7915c6b2937432b091837a7"
- 主域名 da8ttmrfqqhqf.cloudfront.net 的 CloudFront 把 /api/app/* GET 也回了 SPA,
  实际可用 API 域名: d2huxeowqcrhxe.cloudfront.net / dp539615g9mv7.cloudfront.net
- 视频类接口需登录 token (code 5005 = no token); token 配在 init extend 里: {"token": "..."}

分类/接口 (qt.get/qt.post 提取):
  GET  /vid/home/new/list      首页新片
  GET  /vid/list               视频列表
  GET  /vid/module/            模块(分类)
  GET  /vid/section/list       专区列表
  GET  /recommend/vid/list/discover  推荐发现
  GET  /search/hotVid/list     热门搜索
  POST /vid/library/search     搜索
  POST /section/details        专区详情
"""

import base64
import hashlib
import html as html_lib
import http.cookiejar
import json
import re
import ssl
import time
import urllib.parse
import urllib.request

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

try:
    from base.spider import Spider as SpiderBase
except ImportError:
    class SpiderBase(object):
        def getCache(self, key): return None
        def setCache(self, key, value): return "fail"
        def delCache(self, key): return "fail"


# ---------------- 加密 ----------------
_PARAM_KEY = b"BxJand%xf5h3sycH"
_PARAM_IV = b"BxJand%xf5h3sycH"
_IFACE_KEY = b"65dc07d1b7915c6b2937432b091837a7"


def _enc_params(d):
    """zt(): 请求参数加密"""
    raw = json.dumps(d or {}, separators=(",", ":")).encode("utf-8")
    ct = AES.new(_PARAM_KEY, AES.MODE_CBC, _PARAM_IV).encrypt(pad(raw, 16))
    return base64.b64encode(ct).decode()


def _dec_data(enc_str):
    """响应 data 解密 (hash=true 时)"""
    t = base64.b64decode((enc_str or "").strip())
    o = t[:12]
    l = _IFACE_KEY + o
    i = len(l) >> 1
    n = hashlib.sha256(l).digest()[8:24]
    r = n + l[:i]
    s = l[i:] + n
    c = hashlib.sha256(r).digest()
    d = hashlib.sha256(s).digest()
    key = c[:8] + d[8:24] + c[24:]
    iv = d[:4] + c[12:20] + d[28:32]
    pt = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(t[12:]), 16)
    return json.loads(pt.decode("utf-8"))


def _clean(s):
    s = re.sub(r"<[^>]+>", "", str(s or ""))
    return html_lib.unescape(s).replace("$", "＄").replace("#", "＃").strip()


class Spider(SpiderBase):
    # 可用 API 域名 (主域名 CloudFront 把 GET /api/* 回了 SPA, 用这俩)
    APIS = [
        "https://d2huxeowqcrhxe.cloudfront.net/api/app",
        "https://dp539615g9mv7.cloudfront.net/api/app",
    ]

    def __init__(self):
        super(Spider, self).__init__()
        self.token = ""
        self.api = self.APIS[0]
        self._ua = ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=self.ctx))

    # ---------------- 基础 ----------------
    def init(self, extend=""):
        # extend 可传 {"token": "登录token"} 或 {"api": "自定义api域名"}
        opts = {}
        if isinstance(extend, dict):
            opts = extend
        elif extend:
            try:
                opts = json.loads(extend)
            except Exception:
                opts = {}
        if opts.get("token"):
            self.token = str(opts["token"])
        if opts.get("api"):
            self.api = str(opts["api"]).rstrip("/")
        return True

    def getName(self):
        return "91porn短视频"

    def isVideoFormat(self, url):
        u = (url or "").lower()
        return any(k in u for k in (".m3u8", ".mp4", ".flv", ".mkv", ".ts", ".mpd"))

    def manualVideoCheck(self):
        return False

    # ---------------- 网络 ----------------
    def _headers(self):
        h = {
            "User-Agent": self._ua,
            "temp": "test",
            "Accept": "application/json, text/plain, */*",
        }
        if self.token:
            h["Authorization"] = self.token
        return h

    def _api(self, path, params=None, method="GET", timeout=15):
        """调接口, 返回解密后的 data; 失败返回 None"""
        if not _HAS_CRYPTO:
            return None
        enc = _enc_params(params or {})
        last_err = None
        for base in [self.api] + [a for a in self.APIS if a != self.api]:
            try:
                if method == "GET":
                    url = base + path + "?data=" + urllib.parse.quote(enc)
                    req = urllib.request.Request(url, headers=self._headers())
                    with self.opener.open(req, timeout=timeout) as r:
                        j = json.loads(r.read().decode("utf-8", errors="ignore"))
                else:
                    url = base + path
                    body = json.dumps({"data": enc}).encode()
                    h = self._headers()
                    h["Content-Type"] = "application/json"
                    req = urllib.request.Request(url, data=body, headers=h)
                    with self.opener.open(req, timeout=timeout) as r:
                        j = json.loads(r.read().decode("utf-8", errors="ignore"))
                if not isinstance(j, dict):
                    continue
                if j.get("code") != 200:
                    last_err = "code=%s %s" % (j.get("code"), j.get("tip") or j.get("msg"))
                    continue
                # 换个好用的域名置顶
                self.api = base
                data = j.get("data")
                if j.get("hash") and data:
                    try:
                        return _dec_data(data)
                    except Exception:
                        return None
                return data
            except Exception as e:
                last_err = str(e)[:80]
                continue
        return None

    # ---------------- 字段 ----------------
    def _vod(self, it):
        """单条视频转 vod"""
        if not isinstance(it, dict):
            return None
        try:
            vid = str(it.get("id") or it.get("vid") or it.get("videoId") or "")
            name = _clean(it.get("title") or it.get("name") or it.get("videoName") or "")
            pic = (it.get("cover") or it.get("coverUrl") or it.get("pic") or
                   it.get("image") or it.get("thumbnail") or "")
            if pic and pic.startswith("//"):
                pic = "https:" + pic
            remark = _clean(it.get("duration") or it.get("playTime") or "")
            if not vid or not name:
                return None
            return {
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": remark,
            }
        except Exception:
            return None

    def _pick_list(self, data):
        """兼容多种返回结构取列表"""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("list", "records", "data", "videos", "items"):
                v = data.get(k)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict):
                    for k2 in ("list", "records"):
                        if isinstance(v.get(k2), list):
                            return v[k2]
        return []

    def _parse_vods(self, data):
        out, seen = [], set()
        for it in self._pick_list(data):
            v = self._vod(it)
            if v and v["vod_id"] not in seen:
                seen.add(v["vod_id"])
                out.append(v)
        return out

    # ---------------- 首页 ----------------
    def homeContent(self, filter):
        cates = [
            {"type_id": "new", "type_name": "最新"},
            {"type_id": "hot", "type_name": "热门"},
            {"type_id": "discover", "type_name": "发现"},
        ]
        return {"class": cates, "filters": {}}

    def homeVideoContent(self):
        data = self._api("/vid/home/new/list", {"page": 1, "pageSize": 24})
        return {"list": self._parse_vods(data)}

    # ---------------- 分类 ----------------
    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        tid = str(tid or "new")
        if tid == "discover":
            data = self._api("/recommend/vid/list/discover",
                             {"page": pg, "pageSize": 24})
        elif tid == "hot":
            data = self._api("/search/hotVid/list", {"page": pg, "pageSize": 24})
            if not data:
                data = self._api("/vid/list", {"page": pg, "pageSize": 24, "sort": "hot"})
        else:  # new
            data = self._api("/vid/home/new/list", {"page": pg, "pageSize": 24})
        lst = self._parse_vods(data)
        return {
            "list": lst,
            "page": pg,
            "pagecount": pg + 1 if len(lst) >= 24 else pg,
            "limit": 24,
            "total": 999999,
        }

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = str(vid or "").strip()
        if not vid:
            return {"list": []}
        info = None
        for path in ("/vid/detail", "/videoInfo", "/publish/details"):
            info = self._api(path, {"id": vid, "vid": vid})
            if info:
                break
        if not isinstance(info, dict):
            # 兜底: 用列表里的数据拼详情
            return {"list": [{"vod_id": vid, "vod_name": vid,
                              "vod_play_from": "播放",
                              "vod_play_url": "正片$%s" % vid}]}
        if isinstance(info.get("data"), dict):
            info = info["data"]
        v = self._vod(info) or {"vod_id": vid, "vod_name": vid, "vod_pic": "", "vod_remarks": ""}
        play_url = info.get("playUrl") or info.get("videoUrl") or info.get("url") or ""
        v.update({
            "vod_actor": _clean(info.get("actor") or info.get("author") or ""),
            "vod_content": _clean(info.get("desc") or info.get("description") or info.get("content") or ""),
            "vod_play_from": "播放",
            "vod_play_url": "正片$%s" % (play_url or vid),
        })
        return {"list": [v]}

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if not url:
            return {"parse": 0, "url": ""}
        # id 可能是纯 vid, 再取一次真实播放地址
        if not url.startswith("http"):
            info = None
            for path in ("/vid/detail", "/videoInfo"):
                info = self._api(path, {"id": url, "vid": url})
                if info:
                    break
            if isinstance(info, dict):
                if isinstance(info.get("data"), dict):
                    info = info["data"]
                real = (info.get("playUrl") or info.get("videoUrl") or
                        info.get("url") or info.get("m3u8") or "")
                if real:
                    url = real
        return {"parse": 0, "url": url,
                "header": {"User-Agent": self._ua, "Referer": self.api + "/"}}

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg="1"):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        key = str(key or "").strip()
        if not key:
            return {"list": []}
        data = self._api("/vid/library/search",
                         {"keyword": key, "page": pg, "pageSize": 24}, method="POST")
        if not data:
            data = self._api("/search/hotVid/list", {"keyword": key, "page": pg, "pageSize": 24})
        lst = self._parse_vods(data)
        return {
            "list": lst,
            "page": pg,
            "pagecount": pg + 1 if len(lst) >= 24 else pg,
            "limit": 24,
            "total": 999999,
        }
