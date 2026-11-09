# -*- coding: utf-8 -*-
# 站点：https://www.ksksi.com（视中心影院）  类型：自研前台（模板 moban-shizhongxin），无 JSON API
# profile：legacy（老壳，继承 base.spider）  重写日期：2026-11-10
# 要点（2026-11-10 沙盒实测）：
# ① 分类走 /list/{movie,tv,varietyshow,cartoon,skits}.html 热播榜，单页无分页、无分页器，
#    pagecount 真值就是 1；/show/… 与 /search.html 被站方 JS 反爬拦死（只回 277 字节
#    ge_js_validator 挑战页），分类不用 /show/，搜索不可用，searchContent 固定返回空。
# ② 列表结构 ul.tv-list>li，封面 img[data-src]（第三方 CDN 直链，无防盗链），备注 div.v-tips。
# ③ 详情 /voddetail/{id}.html：h1 标题、div.v-pic img[data-src]、div.txt_intro_con 简介、
#    主演/导演/地区/类型为“XX：</em>”文本；播放入口在 div.play_source_tab 的首个
#    /vodplay/{id}-{sid}-{nid}.html 链接（详情页通常只有 1 个入口）。
# ④ 播放 /vodplay/{id}-{sid}-{nid}.html：m3u8 直接嵌在页内 JSON 里，
#    "url":"https:\/\/hd.ijycnd.com\/play\/…\/index.m3u8"，去转义后即直链（parse 0）。
# ⑤ 播放域名 hd.ijycnd.com 从沙盒连不上（curl 空回复，2026-11-10），m3u8 本身未测通，
#    需盒子实测；不要据此判定失效，也不要“修”它。
# ⑥ ID 只存站内相对路径（/voddetail/{id}.html、/vodplay/…html），播放值即播放页 URL。
import html as html_lib
import json
import re
import time
from urllib.parse import parse_qsl, quote, unquote, urljoin

try:
    from base.spider import Spider as BaseSpider
except Exception:  # 无 base.spider 的壳 / 本地调试用最小桩；OK影视等按 base.spider 反射检查，所以优先继承真基类
    class BaseSpider(object):
        def getProxyUrl(self, local=True):
            return "http://127.0.0.1:9978/proxy?do=py"

        def fetch(self, url, params=None, headers=None, timeout=10, **kw):
            import requests
            r = requests.get(url, params=params, headers=headers, timeout=timeout, **kw)
            r.encoding = "utf-8"
            return r

        def post(self, url, params=None, data=None, json=None, headers=None, timeout=10, **kw):
            import requests
            r = requests.post(url, params=params, data=data, json=json, headers=headers, timeout=timeout, **kw)
            r.encoding = "utf-8"
            return r


# ---------------- CONFIG ----------------
HOST = "https://www.ksksi.com"
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Version/4.0 Chrome/120.0.0.0 Mobile Safari/537.36")
TIMEOUT = 15

# 分类：type_id -> 名称；/list/{type_id}.html，单页热播榜，无筛选维度（站方无可用筛选参数）
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


class Spider(BaseSpider):
    def getName(self):
        return "视中心影院"

    def init(self, extend=""):
        options = extend if isinstance(extend, dict) else {}
        if isinstance(extend, str) and extend.strip().startswith("{"):
            try:
                options = json.loads(extend)
            except Exception:
                options = {}
        self.host = str(options.get("host") or HOST).rstrip("/")

    # ---------------- 网络 ----------------
    def _headers(self, referer=""):
        return {
            "User-Agent": UA,
            "Referer": referer or self.host + "/",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    def _get(self, url, referer=""):
        """GET 取文本：判 status_code，有限次重试（2 次），失败返回空串，不抛到壳里。"""
        for _ in range(2):
            try:
                rsp = self.fetch(url, headers=self._headers(referer), timeout=TIMEOUT)
                if 200 <= rsp.status_code < 300:
                    return rsp.text or ""
            except Exception:
                pass
            time.sleep(0.3)
        return ""

    # ---------------- 列表解析 ----------------
    def _parse_list(self, html):
        """解析 ul.tv-list 列表，返回 vod 字典列表（站内相对路径作 vod_id）。"""
        vods = []
        for m in re.finditer(r"<li>(.*?)</li>", html or "", re.S):
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
        # 无筛选维度：/list/ 热播榜无可用筛选参数，filters 不下发
        return {"class": [{"type_id": t, "type_name": n} for t, n in CATS]}

    def homeVideoContent(self):
        html = self._get(self.host + "/")
        return {"list": self._parse_list(html)}

    # ---------------- 分类 ----------------
    def categoryContent(self, tid, pg, filter, extend):
        tid = str(tid or "movie").strip()
        valid = [t for t, _ in CATS]
        if tid not in valid:
            tid = "movie"
        try:
            pn = int(pg)
        except Exception:
            pn = 1
        if pn < 1:
            pn = 1
        html = self._get("%s/list/%s.html" % (self.host, tid))
        vods = self._parse_list(html)
        # 单页热播榜，无分页器：pagecount 真值 1；pg>1 无内容，返回空列表（不回第 1 页防死循环）
        if pn > 1:
            return {"list": [], "page": pn, "pagecount": 1,
                    "limit": max(len(vods), 1), "total": len(vods)}
        return {
            "list": vods,
            "page": 1,
            "pagecount": 1,
            "limit": max(len(vods), 1),
            "total": len(vods),
        }

    # ---------------- 搜索（站方 JS 反爬，暂不可用） ----------------
    def searchContent(self, key, quick, pg="1"):
        # /search.html 只回 ge_js_validator 挑战页，无结果；固定空列表，pagecount 0 表态
        try:
            pn = int(pg)
        except Exception:
            pn = 1
        if pn < 1:
            pn = 1
        return {"list": [], "page": pn, "pagecount": 0, "limit": 20, "total": 0}

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        if isinstance(ids, str):
            ids = [ids]
        items = []
        for vid in ids:
            item = self._detail(vid)
            if item:
                items.append(item)
        return {"list": items}

    def _detail(self, vid):
        vid = str(vid or "").strip()
        if not vid.startswith("/"):
            vid = "/voddetail/%s.html" % vid.lstrip("/")
        html = self._get(self.host + vid)
        if not html:
            return None

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

        # 播放线路：详情页首个播放入口 -> 播放页 -> 选集列表
        play_from, play_url = [], []
        m = re.search(r'<div class="play_source_tab[^"]*">.*?<a[^>]+href="(/vodplay/[\d-]+\.html)"',
                      html, re.S)
        if m:
            play_page = self.host + m.group(1)
            ph = self._get(play_page, referer=self.host + vid)
            eps = re.findall(
                r'href="(/vodplay/\d+-\d+-(\d+)\.html)"[^>]*>([^<]{1,20})<', ph)
            if eps:
                items = []
                for url, _n, name in eps:
                    name = _clean(name) or ("第%s集" % _n)
                    items.append("%s$%s" % (name, self.host + url))
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
        return item

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if not url:
            return {"parse": 0, "url": ""}
        if not url.startswith("http"):
            url = urljoin(self.host + "/", url.lstrip("/"))
        html = self._get(url)
        m3u8 = ""
        if html:
            m = re.search(r'"url":"(https?:[^"]+?\.m3u8[^"]*)"', html)
            if m:
                # 去转义：https:\/\/ -> https://
                try:
                    m3u8 = json.loads('"%s"' % m.group(1))
                except Exception:
                    m3u8 = m.group(1).replace("\\/", "/")
        if not m3u8:
            # 取不到直链时交壳嗅探播放页（原版此处回空串；按技能改为 parse 1）
            return {"parse": 1, "url": url,
                    "header": {"User-Agent": UA, "Referer": self.host + "/"}}
        return {
            "parse": 0,
            "url": m3u8,
            "header": {"User-Agent": UA, "Referer": self.host + "/"},
        }

    # ---------------- 老壳必须实现的抽象方法 ----------------
    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        # 本站图片为第三方 CDN 直链、无防盗链，m3u8 能直出就不走代理；保留入口供壳调用
        return [404, "text/plain", b""]

    def destroy(self):
        pass
