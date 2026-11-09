# -*- coding: utf-8 -*-
# 站点：https://personalmv.com（sae8视频）  类型：UIKit 纯 HTML 站 + JSON 取流接口  profile：legacy
# 要点（实测 2026-11-10，沙盒 curl）：
#  ① 列表：/cn（首页，无分页）、/cn/category/<分类名>?page=N（每页 36 条，分页器有“尾页”链接即真总页数）
#     /cn/search?q=<词>&page=N（每页 36 条，同样有尾页）；卡片 div.movie-card，a[href^=/cn/movie/]，img src 绝对地址
#  ② 详情：/cn/movie/<24 位 hex id>；标题 h1；时长第一个 \d+:\d+；标签 a[href^=/cn/tag/]
#     详情页无 og:image、无本片 poster，只有本片剧照 /videos/<年月>/<日>/<id>/0.jpg 等（旧版 fallback 会误取关联视频的 poster，已改成本片剧照）
#  ③ 取流：GET /api/getmovie?type=640&id=<id>（480P，无需登录）返回 {"m3u8": "/videos/.../index.m3u8?random=..."}，
#     type=2560 为 2K，需 init(ext) 传入 hls cookie（ext 形态：裸值 / "hls=xxx" / '{"hls":"xxx"}'，运行时传入，绝不写入源/日志）；
#     m3u8 实测 200、标准 #EXTM3U、分片为绝对 https 地址，首分片实测 200 可取；播放直链 parse:0，不走代理
#  ④ 图片：卡片封面与剧照均为 CDN 绝对地址，可直连，无防盗链，不走 localProxy
#  ⑤ 站点响应慢（单请求约 3~6s），超时 15s、失败重试 1 次；分类 4 个全覆盖，无筛选（filterable=0）

import sys
import re
import json
import time
from urllib.parse import quote, unquote, urljoin, parse_qsl

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:  # 无 base.spider 的壳 / 本地调试用最小桩；OK影视等按 base.spider 反射检查，所以优先继承真基类
    class BaseSpider(object):
        def getProxyUrl(self, local=True):
            return 'http://127.0.0.1:9978/proxy?do=py'

        def fetch(self, url, params=None, headers=None, timeout=10, **kw):
            import requests
            r = requests.get(url, params=params, headers=headers, timeout=timeout, **kw)
            r.encoding = 'utf-8'
            return r

        def post(self, url, params=None, data=None, json=None, headers=None, timeout=10, **kw):
            import requests
            r = requests.post(url, params=params, data=data, json=json, headers=headers, timeout=timeout, **kw)
            r.encoding = 'utf-8'
            return r

try:
    from lxml import etree
    _HAS_LXML = True
except Exception:
    _HAS_LXML = False

# ---------------- CONFIG ----------------
HOST = 'https://personalmv.com'
UA = ('Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36')
PAGE_SIZE = 36  # 实测：分类/搜索每页 36 条（去重后）
TIMEOUT = 15    # 站点响应慢，超时单位秒
# 分类：(type_id, type_name)，type_id 即分类名本身，请求时 quote
CATS = [
    ('自拍黑料吃瓜', '自拍黑料吃瓜'),
    ('情色ai', '情色ai'),
    ('AV精选', 'AV精选'),
    ('稀缺资源', '稀缺资源'),
]


class Spider(BaseSpider):
    def getName(self):
        return 'personalmv'

    def init(self, extend=''):
        self.host = HOST
        self.headers = {
            'User-Agent': UA,
            'Referer': HOST + '/',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        # 2K 登录态：ext 传 hls cookie（运行时传入，绝不写入源/日志）
        self._hls = ''
        try:
            ext = str(extend or '').strip()
            if ext.startswith('{'):
                self._hls = str(json.loads(ext).get('hls', '')).strip()
            elif ext:
                self._hls = ext
            if self._hls and '=' not in self._hls:
                self._hls = 'hls=' + self._hls
        except Exception:
            self._hls = ''

    def getDependence(self):
        return []

    # ---------- helpers ----------
    def _get(self, url, headers=None, with_cookie=False):
        """GET 返回 text；失败有限次重试，最终失败返回 ''，不抛到壳里。"""
        h = dict(self.headers)
        if headers:
            h.update(headers)
        if with_cookie and self._hls:
            h['Cookie'] = self._hls
        for _ in range(2):
            try:
                r = self.fetch(url, headers=h, timeout=TIMEOUT)
                if 200 <= r.status_code < 300:
                    return r.text
            except Exception:
                pass
            time.sleep(0.3)
        return ''

    def _get_json(self, url, headers=None, with_cookie=False):
        try:
            return json.loads(self._get(url, headers, with_cookie))
        except Exception:
            return {}

    def _fix(self, u):
        if not u:
            return ''
        u = u.strip()
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('http'):
            return u
        if u.startswith('/'):
            return self.host + u
        return self.host + '/' + u

    def _parse_cards(self, html):
        """解析 movie-card 列表；lxml 为主，正则兜底；按 href 去重。"""
        out, seen = [], set()
        if _HAS_LXML:
            try:
                root = etree.HTML(html)
                if root is not None:
                    for card in root.xpath('//div[contains(@class,"movie-card")]'):
                        try:
                            a = card.xpath('.//a[starts-with(@href,"/cn/movie/")]')
                            if not a:
                                continue
                            href = a[0].get('href', '')
                            imgs = a[0].xpath('.//img')
                            img = imgs[0] if imgs else None
                            name = (img.get('alt', '') if img is not None else '').strip()
                            pic = ''
                            if img is not None:
                                pic = self._fix(img.get('src', '') or img.get('data-src', ''))
                            if not href or href in seen:
                                continue
                            seen.add(href)
                            out.append({'vod_id': href, 'vod_name': name,
                                        'vod_pic': pic, 'vod_remarks': ''})
                        except Exception:
                            continue
                    if out:
                        return out
            except Exception:
                pass
        for m in re.finditer(
                r'<a href="(/cn/movie/[a-f0-9]+)">.*?<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)"',
                html, re.S):
            href, pic, name = m.group(1), m.group(2), m.group(3).strip()
            if href in seen:
                continue
            seen.add(href)
            out.append({'vod_id': href, 'vod_name': name,
                        'vod_pic': self._fix(pic), 'vod_remarks': ''})
        return out

    def _pagecount(self, html, pn, n_items):
        """翻页真值：优先分页器“尾页”链接；其次当页所有 page= 的最大值；最后满页 pn+1、否则 pn。"""
        m = re.search(r'href="[^"]*[?&]page=(\d+)"[^>]*>尾页</a>', html)
        if m:
            return max(int(m.group(1)), pn)
        pages = [int(x) for x in re.findall(r'[?&]page=(\d+)', html)]
        if pages:
            return max(max(pages), pn)
        return pn + 1 if n_items >= PAGE_SIZE else pn

    # ---------- entries ----------
    def homeContent(self, filter=None):
        classes = [{'type_id': quote(t, safe=''), 'type_name': n} for t, n in CATS]
        html = self._get(self.host + '/cn')
        return {'class': classes, 'list': self._parse_cards(html)[:24], 'filters': {}}

    def homeVideoContent(self):
        return {'list': self._parse_cards(self._get(self.host + '/cn'))[:24]}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        try:
            cat = unquote(str(tid))
        except Exception:
            cat = str(tid)
        url = '%s/cn/category/%s?page=%d' % (self.host, quote(cat, safe=''), pg)
        html = self._get(url)
        items = self._parse_cards(html)
        return {'list': items, 'page': pg,
                'pagecount': self._pagecount(html, pg, len(items)), 'limit': PAGE_SIZE}

    def detailContent(self, ids):
        if not ids:
            return {'list': []}
        if isinstance(ids, str):
            ids = [ids]
        return {'list': [self._detail(str(vid)) for vid in ids if str(vid or '').strip()]}

    def _detail(self, vid):
        vid = str(vid or '').strip()
        url = self._fix(vid)
        html = self._get(url)
        if not html:
            return {'vod_id': vid, 'vod_name': '', 'vod_pic': ''}
        m = re.search(r'/cn/movie/([a-f0-9]+)', url)
        mid = m.group(1) if m else ''
        name, pic = '', ''
        if _HAS_LXML:
            try:
                root = etree.HTML(html)
                if root is not None:
                    h1 = root.xpath('//h1')
                    name = ''.join(h1[0].itertext()).strip() if h1 else ''
                    og = root.xpath('//meta[@property="og:image"]/@content')
                    pic = self._fix(og[0]) if og else ''
            except Exception:
                pass
        if not name:
            m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
            name = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ''
        if not pic:
            # 本片剧照：/videos/.../<mid>/0.jpg（详情页无 og:image、无本片 poster；
            # 旧版无 mid 限定的 poster 正则会误取关联视频的封面，已改成本片剧照）
            if mid:
                stills = re.findall(
                    r'(?:src|data-src)="(https?://[^"]*?/%s/(?:0\.jpg|[0-9]+\.(?:jpg|gif)))"' % mid, html)
                still0 = [s for s in stills if s.endswith('/0.jpg')]
                if still0:
                    pic = still0[0]
                elif stills:
                    pic = stills[0]
        m = re.search(r'(\d+:\d+)', html)
        remarks = m.group(1) if m else ''
        tags = []
        if _HAS_LXML:
            try:
                root = etree.HTML(html)
                if root is not None:
                    tags = [t.strip() for t in root.xpath('//a[starts-with(@href,"/cn/tag/")]/text()')
                            if t.strip() and len(t.strip()) < 20]
            except Exception:
                pass
        vod = {
            'vod_id': vid, 'vod_name': name, 'vod_pic': pic,
            'vod_year': '', 'vod_area': '', 'vod_actor': '',
            'vod_director': '', 'vod_remarks': remarks,
            'vod_content': ' '.join(tags[:12]),
        }
        if mid:
            if self._hls:
                # 登录态已配置：同时给出 2K 源
                vod['vod_play_from'] = '480P$$$2K'
                vod['vod_play_url'] = '正片$%s$$$正片$%s' % (mid, mid)
            else:
                vod['vod_play_from'] = '480P'
                vod['vod_play_url'] = '正片$%s' % mid
        return vod

    def searchContent(self, key, quick=False, pg='1'):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        url = '%s/cn/search?q=%s&page=%d' % (self.host, quote(str(key)), pg)
        html = self._get(url)
        items = self._parse_cards(html)
        return {'list': items, 'page': pg,
                'pagecount': self._pagecount(html, pg, len(items)), 'limit': PAGE_SIZE}

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    def playerContent(self, flag, id, vipFlags=None):
        pid = str(id or '').strip()
        if '|' in pid:
            pid = pid.split('|', 1)[0]
        if not pid:
            return {'parse': 0, 'url': ''}
        qtype = '2560' if str(flag) == '2K' else '640'
        api = '%s/api/getmovie?type=%s&id=%s' % (self.host, qtype, pid)
        data = self._get_json(api, {'Referer': self.host + '/cn/movie/' + pid},
                              with_cookie=(qtype == '2560'))
        m3u8 = data.get('m3u8', '') if isinstance(data, dict) else ''
        if not m3u8:
            return {'parse': 0, 'url': ''}
        header = dict(self.headers)
        if qtype == '2560' and self._hls:
            # 2K 分片拉取需要登录态，cookie 随播放头下发（运行时内存，不落盘）
            header['Cookie'] = self._hls
        return {'parse': 0, 'url': self._fix(m3u8), 'header': header}

    # ---------- 老壳必须实现的抽象方法 ----------
    def isVideoFormat(self, url):
        u = str(url or '').split('?')[0].lower()
        return u.endswith('.m3u8') or u.endswith('.mp4')

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        # 本站图片/m3u8 均可直连，不需要代理；保留入口，参数 dict 或字符串都接
        if isinstance(param, dict):
            p = param
        else:
            try:
                p = dict(parse_qsl(str(param or '')))
            except Exception:
                p = {}
        _t = str(p.get('type') or '')
        _u = unquote(str(p.get('url') or ''))
        return [404, 'text/plain', b'']

    def destroy(self):
        pass
