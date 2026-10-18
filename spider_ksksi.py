#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视中心影院 (www.ksksi.com) T4 spider
CMS 站, 列表结构 ul.tv-list>li, 封面 img[data-src], 详情 /voddetail/{id}.html,
播放 /vodplay/{id}-{sid}-{nid}.html, m3u8 直接嵌在播放页 JSON 里

注意:
- /show/... 分类路径和 /search.html 被站方 JS 反爬拦死, 不用
- 分类走 /list/{movie,tv,varietyshow,cartoon,skits}.html 热播榜(单页无分页)
- 搜索口子全被拦, searchContent 返回空
"""

import base64
import gzip
import html as html_lib
import http.cookiejar
import json
import re
import ssl
import urllib.parse
import urllib.request

try:
    from base.spider import Spider as SpiderBase
except ImportError:
    class SpiderBase(object):
        def getCache(self, key): return None
        def setCache(self, key, value): return "fail"
        def delCache(self, key): return "fail"


BASE = "https://www.ksksi.com"
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Version/4.0 Chrome/120.0.0.0 Mobile Safari/537.36")
TIMEOUT = 20

# 分类: type_id -> (名称, /list/ 路径)
CATS = [
    ("movie", "电影"),
    ("tv", "连续剧"),
    ("varietyshow", "综艺"),
    ("cartoon", "动漫"),
    ("skits", "短剧"),
]


def _clean(s):
    s = re.sub(r"<[^>]+>", "", str(s or ""))
    return html_lib.unescape(s).replace("$", "＄").replace("#", "＃").strip()


class Spider(SpiderBase):
    def __init__(self):
        super(Spider, self).__init__()
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=self.ctx))

    # ---------------- 基础 ----------------
    def getName(self):
        return "视中心影院"

    def init(self, extend=""):
        return

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def _get(self, url, referer=""):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": referer or BASE + "/",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            resp = self.opener.open(req, timeout=TIMEOUT)
            data = resp.read()
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            enc = "utf-8"
            m = re.search(br'<meta[^>]+charset=["\']?([\w-]+)', data[:2000], re.I)
            if m:
                enc = m.group(1).decode("ascii", "ignore")
            return data.decode(enc, "ignore")
        except Exception:
            return ""

    def _parse_list(self, html):
        """解析 ul.tv-list 列表, 返回 vod 字典列表"""
        vods = []
        for m in re.finditer(r"<li>(.*?)</li>", html, re.S):
            li = m.group(1)
            if "v-pic" not in li:
                continue
            a = re.search(r'<a[^>]+href="(/voddetail/\d+\.html)"[^>]*title="([^"]+)"', li)
            if not a:
                a = re.search(r'<a[^>]+href="(/voddetail/\d+\.html)"[^>]*>', li)
                if not a:
                    continue
                t = re.search(r'title="([^"]+)"', li)
                title = t.group(1) if t else ""
            else:
                title = a.group(2)
            vid = a.group(1)
            img = re.search(r'<img[^>]+data-src="([^"]+)"', li)
            pic = img.group(1) if img else ""
            if pic.startswith("//"):
                pic = "https:" + pic
            tip = re.search(r'<div class="v-tips">([^<]*)</div>', li)
            remarks = _clean(tip.group(1)) if tip else ""
            vods.append({
                "vod_id": vid,
                "vod_name": _clean(title),
                "vod_pic": pic,
                "vod_remarks": remarks,
                "style": {"type": "rect", "ratio": 0.75},
            })
        return vods

    # ---------------- 首页 ----------------
    def homeContent(self, filter):
        result = {"class": [{"type_id": t, "type_name": n} for t, n in CATS]}
        return result

    def homeVideoContent(self):
        html = self._get(BASE + "/")
        return {"list": self._parse_list(html)}

    # ---------------- 分类 ----------------
    def categoryContent(self, tid, pg, filter, extend):
        tid = str(tid or "movie").strip()
        valid = [t for t, _ in CATS]
        if tid not in valid:
            tid = "movie"
        html = self._get("%s/list/%s.html" % (BASE, tid))
        vods = self._parse_list(html)
        return {
            "list": vods,
            "page": 1,
            "pagecount": 1,
            "limit": max(len(vods), 1),
            "total": len(vods),
        }

    # ---------------- 搜索 (站方反爬, 暂不可用) ----------------
    def searchContent(self, key, quick, pg="1"):
        return {"list": []}

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        try:
            vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        except Exception:
            vid = ids
        vid = str(vid or "").strip()
        if not vid.startswith("/"):
            vid = "/voddetail/%s.html" % vid.lstrip("/")
        html = self._get(BASE + vid)
        if not html:
            return {"list": []}

        # 标题
        t = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
        title = _clean(t.group(1)) if t else ""
        # 封面
        img = re.search(r'<div class="v-pic">.*?<img[^>]+data-src="([^"]+)"', html, re.S)
        pic = img.group(1) if img else ""
        if pic.startswith("//"):
            pic = "https:" + pic
        # 简介
        intro = re.search(r'<div class="txt_intro_con">(.*?)</div>\s*<div class="play_source"', html, re.S)
        content = _clean(intro.group(1)) if intro else ""
        # 主演/导演/地区/类型
        actor = re.search(r"主演：</em>([^<]+)", html)
        director = re.search(r"导演：</em>([^<&\s]+)", html)
        area = re.search(r"地区：</em>([^<&\s]+)", html)
        vtype = re.search(r"类型：</em>([^<]+)", html)

        # 播放线路: 详情页 -> 播放页 -> 选集列表
        play_from, play_url = [], []
        m = re.search(r'<div class="play_source_tab[^"]*">.*?<a[^>]+href="(/vodplay/[\d-]+\.html)"',
                      html, re.S)
        if m:
            play_page = BASE + m.group(1)
            ph = self._get(play_page, referer=BASE + vid)
            eps = re.findall(
                r'href="(/vodplay/\d+-\d+-(\d+)\.html)"[^>]*>([^<]{1,20})<', ph)
            if eps:
                items = []
                for url, _n, name in eps:
                    name = _clean(name) or ("第%s集" % _n)
                    items.append("%s$%s" % (name, BASE + url))
                play_from.append("在线")
                play_url.append("#".join(items))

        item = {
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": pic,
            "vod_actor": _clean(actor.group(1)) if actor else "",
            "vod_director": _clean(director.group(1)) if director else "",
            "vod_area": _clean(area.group(1)) if area else "",
            "vod_content": content,
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        if vtype:
            item["vod_tag"] = _clean(vtype.group(1)).replace(" ", ",")
        return {"list": [item]}

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if not url:
            return {"parse": 0, "url": ""}
        if not url.startswith("http"):
            url = BASE + url
        html = self._get(url)
        m3u8 = ""
        if html:
            m = re.search(r'"url":"(https?:[^"]+?\.m3u8[^"]*)"', html)
            if m:
                # 去转义: https:\/\/ -> https://
                m3u8 = json.loads('"%s"' % m.group(1))
        if not m3u8:
            return {"parse": 0, "url": ""}
        return {
            "parse": 0,
            "url": m3u8,
            "header": {"User-Agent": UA, "Referer": BASE + "/"},
        }
