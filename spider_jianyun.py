# -*- coding: utf-8 -*-
# profile: legacy（老壳：继承 base.spider，self.fetch，Python 3.8）
# 站点：简云影视 https://jisuzhuiju.com（原 jianyunys.com 301 至此；自定义站，无加密）
# 要点（实测 2026-11-10，沙盒 curl）：
#  ① 列表：HTML 卡片 //a[contains(@href,"/detail/") and .//img]，/filter?channel=&type=&area=&year=&sort=&page=
#     每页 12 条；分页真值取分页器文本“共 N 页”（电视剧 hot 默认 1688 页），第 1/2 页零重叠
#  ② 筛选：类型 type / 地区 area / 年份 year / 排序 sort 四维均实测生效（改值后列表变化）；
#     “全部” v="" 即不传该维，服务端视为空=不过滤；sort 默认 hot
#  ③ 搜索：/search?keyword=&page=，分页生效（第 2 页 12 条新 id，零重叠）；结果 DOM 每条重复 3 次，需去重；
#     总数取“共 N 个结果”（如 狂飙 26 条/3 页）
#  ④ 详情：/detail/{id}.html；meta-label/meta-value 取年份/地区/主演/导演；简介取 .synopsis 或 JSON-LD；
#     线路为 button.source-tab + #source-* 面板，集链接 /vodplay/{vid}-{slug}-{idx}.html
#  ⑤ 播放：GET /api/play-url?vodId=&playFrom={slug}&index=，code=200 时 mode=native 给签名 m3u8 直链
#     （auth_key 时效短，播放时现取；实测 qq/covip/jsm3u8 三线均为 native），mode=iframe 时 parse=1 交壳嗅探；
#     签名 m3u8 本身 302 跳转一次后 200，首分片 200 可取
#  ⑥ 图片：封面为站外绝对地址（qpic.cn / ykimg / iqiyipic 等），直连；data: 占位图过滤
#  ⑦ 站点响应慢（单请求常 5~16s），超时取 20 秒、失败重试 2 次
import sys
import json
import re
import time
from urllib.parse import quote, unquote, urljoin, parse_qsl

try:
    from lxml import etree
except Exception:
    etree = None

try:
    import requests
except Exception:
    requests = None

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:  # 无 base.spider 的壳 / 本地调试用最小桩；OK影视等按 base.spider 反射检查，所以优先继承真基类
    class BaseSpider(object):
        def getProxyUrl(self, local=True):
            return 'http://127.0.0.1:9978/proxy?do=py'

        def fetch(self, url, params=None, headers=None, timeout=10, **kw):
            import requests as _rq
            r = _rq.get(url, params=params, headers=headers, timeout=timeout, **kw)
            r.encoding = 'utf-8'
            return r

        def post(self, url, params=None, data=None, json=None, headers=None, timeout=10, **kw):
            import requests as _rq
            r = _rq.post(url, params=params, data=data, json=json, headers=headers, timeout=timeout, **kw)
            r.encoding = 'utf-8'
            return r


# ---------------- CONFIG ----------------
HOST = 'https://jisuzhuiju.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
PAGE_SIZE = 12
TIMEOUT = 20

CLASSES = [
    ('电视剧', '1'),
    ('电影', '2'),
    ('动漫', '3'),
    ('综艺', '4'),
    ('短剧', '5'),
]

# 一级 tid -> [(type_id, type_name)]，取自原源保留
TYPES = {
    '1': [('7', '剧情'), ('9', '古装'), ('10', '战争'), ('11', '谍战'), ('12', '爱情'), ('13', '罪案'), ('14', '悬疑'), ('15', '家庭'), ('16', '军旅'), ('17', '喜剧'), ('18', '都市'), ('19', '武侠'), ('20', '言情'), ('21', '偶像'), ('22', '青春'), ('23', '农村'), ('24', '穿越'), ('25', '奇幻'), ('26', '历史'), ('27', '年代'), ('28', '科幻'), ('29', '生活'), ('30', '剧情'), ('31', '励志'), ('32', '婚姻'), ('33', '警匪'), ('34', '犯罪'), ('35', '推理'), ('36', '商战'), ('37', '宫廷'), ('38', '仙侠'), ('39', '神话'), ('40', '动作'), ('41', '复仇'), ('42', '惊悚')],
    '2': [('43', '动作'), ('44', '喜剧'), ('45', '爱情'), ('46', '科幻'), ('47', '恐怖'), ('48', '剧情'), ('49', '战争'), ('50', '犯罪'), ('51', '惊悚'), ('52', '冒险'), ('53', '悬疑'), ('54', '动画'), ('55', '武侠'), ('56', '古装'), ('57', '历史'), ('58', '传记'), ('59', '纪录片')],
    '3': [('60', '热血'), ('61', '恋爱'), ('62', '校园'), ('63', '搞笑'), ('64', '机甲'), ('65', '神魔'), ('66', '竞技'), ('67', '冒险'), ('68', '治愈'), ('69', '百合'), ('70', '萝莉'), ('71', '后宫'), ('72', '励志'), ('73', '泡面番'), ('74', '国产动漫'), ('75', '日本动漫'), ('76', '欧美动漫')],
    '4': [('77', '选秀'), ('78', '情感'), ('79', '访谈'), ('80', '播报'), ('81', '旅游'), ('82', '音乐'), ('83', '美食'), ('84', '纪实'), ('85', '曲艺'), ('86', '游戏'), ('87', '亲子'), ('88', '职场'), ('89', '脱口秀'), ('90', '真人秀'), ('91', '晚会')],
    '5': [],
}
AREAS = ['大陆', '香港', '台湾', '韩国', '日本', '美国', '泰国', '法国', '英国', '德国', '印度', '其他']
YEARS = ['2026', '2025', '2024', '2023', '2022', '2021', '2020', '2019', '2018', '-1']
SORTS = [('hot', '最热'), ('time', '最新'), ('score', '评分')]


class Spider(BaseSpider):
    def __init__(self):
        super(Spider, self).__init__()
        self.host = HOST
        self.headers = {'User-Agent': UA}
        self._session = None

    def getName(self):
        return '简云影视'

    def init(self, extend=''):
        options = {}
        if isinstance(extend, dict):
            options = extend
        elif isinstance(extend, str) and extend.strip().startswith('{'):
            try:
                options = json.loads(extend)
            except Exception:
                options = {}
        host = str(options.get('host') or '').strip().rstrip('/')
        if host.startswith('http'):
            self.host = host
        self.headers['Referer'] = self.host + '/'

    # ---------------- 网络 ----------------
    def _get(self, url, referer=''):
        """GET 取文本：优先 self.fetch，失败重试 2 次；无 requests 时退 urllib。返回 '' 表示失败。"""
        headers = dict(self.headers)
        if referer:
            headers['Referer'] = referer
        last = ''
        for _ in range(2):
            try:
                rsp = self.fetch(url, headers=headers, timeout=TIMEOUT)
                if rsp is not None and getattr(rsp, 'status_code', 0) == 200:
                    return rsp.text or ''
                last = ''
            except Exception:
                pass
            try:
                return self._urllib_get(url, headers)
            except Exception:
                last = ''
            time.sleep(0.5)
        return last

    def _urllib_get(self, url, headers):
        import urllib.request
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
        try:
            return raw.decode('utf-8')
        except Exception:
            return raw.decode('utf-8', 'ignore')

    def _bin(self, url, referer=''):
        """GET 取字节（localProxy 图片用）。"""
        headers = dict(self.headers)
        if referer:
            headers['Referer'] = referer
        try:
            rsp = self.fetch(url, headers=headers, timeout=TIMEOUT)
            if rsp is not None and getattr(rsp, 'status_code', 0) == 200:
                return rsp.content or b''
        except Exception:
            pass
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception:
            return b''

    def _tree(self, html):
        if not html or etree is None:
            return None
        try:
            return etree.HTML(html)
        except Exception:
            return None

    # ---------------- 解析 ----------------
    @staticmethod
    def _clean(s):
        return (s or '').replace('$', '＄').replace('#', '＃').strip()

    def _abs(self, url):
        url = (url or '').strip()
        if not url or url.startswith('data:'):
            return ''
        return urljoin(self.host + '/', url)

    def _parse_cards(self, tree):
        lst, seen = [], set()
        if tree is None:
            return lst
        for a in tree.xpath('//a[contains(@href,"/detail/") and .//img]'):
            try:
                href = a.get('href') or ''
                m = re.search(r'/detail/(\d+)\.html', href)
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                img = a.xpath('.//img')[0]
                name = ''
                t = a.xpath('.//p[contains(@class,"vod-title")]/text()')
                if t:
                    name = t[0].strip()
                if not name:
                    name = (img.get('alt') or '').replace('封面图片', '').strip()
                remarks = ''
                b = a.xpath('.//span[contains(@class,"vod-badge")]/text()')
                if b:
                    remarks = b[0].strip()
                if not remarks:
                    st = a.xpath('.//p[contains(@class,"vod-subtitle")]/text()')
                    if st:
                        remarks = st[0].strip()
                lst.append({
                    'vod_id': m.group(1),
                    'vod_name': self._clean(name),
                    'vod_pic': self._abs(img.get('src') or ''),
                    'vod_remarks': self._clean(remarks),
                })
            except Exception:
                continue
        return lst

    def _pagecount(self, tree, pg, n_items):
        """翻页真值：优先分页器文本“共 N 页”，其次 page= 链接最大值，最后按本页是否满页推断。"""
        pg = int(pg) if pg else 1
        if tree is not None:
            for el in tree.xpath('//*[contains(@class,"pagination")]'):
                txt = ' '.join(''.join(el.itertext()).split())
                m = re.search(r'共\s*(\d+)\s*页', txt)
                if m:
                    return max(1, int(m.group(1)))
            pages = [int(x) for x in re.findall(r'page=(\d+)', ' '.join(tree.xpath('//a[contains(@href,"page=")]/@href'))) if x.isdigit()]
            if pages:
                return max(1, max(pages))
        return pg + 1 if n_items >= PAGE_SIZE else pg

    def _total(self, tree):
        """搜索总数：页面“共 N 个结果”（不在 pagination 容器内，需全文找），拿不到返回 None。"""
        if tree is None:
            return None
        for el in tree.xpath('//*[contains(text(),"共")]'):
            txt = ' '.join(''.join(el.itertext()).split())
            m = re.search(r'共\s*(\d+)\s*个结果', txt)
            if m:
                return int(m.group(1))
        return None

    def _filters(self):
        f = {}
        for _, tid in CLASSES:
            groups = [{'key': 'type', 'name': '类型', 'value': [{'n': '全部', 'v': ''}] + [{'n': n, 'v': v} for v, n in TYPES.get(tid, [])]}]
            groups.append({'key': 'area', 'name': '地区', 'value': [{'n': '全部', 'v': ''}] + [{'n': a, 'v': a} for a in AREAS]})
            groups.append({'key': 'year', 'name': '年份', 'value': [{'n': '全部', 'v': ''}] + [{'n': ('其他' if y == '-1' else y), 'v': y} for y in YEARS]})
            groups.append({'key': 'sort', 'name': '排序', 'value': [{'n': n, 'v': v} for v, n in SORTS]})
            f[tid] = groups
        return f

    def _extend(self, extend):
        ext = extend or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except Exception:
                ext = {}
        if not isinstance(ext, dict):
            ext = {}
        return ext

    # ---------------- 入口 ----------------
    def homeContent(self, filter=None):
        try:
            tree = self._tree(self._get(self.host + '/'))
            lst = self._parse_cards(tree)
        except Exception:
            lst = []
        return {
            'class': [{'type_id': tid, 'type_name': name} for name, tid in CLASSES],
            'list': lst,
            'filters': self._filters(),
        }

    def homeVideoContent(self):
        try:
            tree = self._tree(self._get(self.host + '/'))
            lst = self._parse_cards(tree)
        except Exception:
            lst = []
        return {'list': lst}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        pg = int(pg) if pg else 1
        if pg < 1:
            pg = 1
        ext = self._extend(extend)
        q = {
            'channel': tid,
            'type': ext.get('type') or '',
            'area': ext.get('area') or '',
            'year': ext.get('year') or '',
            'sort': ext.get('sort') or 'hot',
            'page': pg,
        }
        url = self.host + '/filter?' + '&'.join('%s=%s' % (k, quote(str(v))) for k, v in q.items())
        try:
            tree = self._tree(self._get(url))
            lst = self._parse_cards(tree)
            pagecount = self._pagecount(tree, pg, len(lst))
        except Exception:
            lst, pagecount = [], pg
        return {'list': lst, 'page': pg, 'pagecount': max(1, pagecount), 'limit': PAGE_SIZE}

    def searchContent(self, key, quick=False, pg='1'):
        pg = int(pg) if pg else 1
        if pg < 1:
            pg = 1
        try:
            tree = self._tree(self._get(self.host + '/search?keyword=' + quote(key) + '&page=' + str(pg)))
        except Exception:
            return {'list': [], 'page': pg, 'pagecount': pg, 'limit': PAGE_SIZE}
        lst, seen = [], set()
        if tree is not None:
            for item in tree.xpath('//div[contains(@class,"search-item")]'):
                try:
                    a = item.xpath('.//a[contains(@href,"/detail/")]')[0]
                    m = re.search(r'/detail/(\d+)\.html', a.get('href') or '')
                    if not m or m.group(1) in seen:
                        continue
                    seen.add(m.group(1))
                    title = item.xpath('.//a[contains(@class,"search-item-title")]/text()')
                    img = item.xpath('.//img/@src')
                    typ = item.xpath('.//span[contains(@class,"search-item-type")]/text()')
                    metas = [x.strip() for x in item.xpath('.//span[contains(@class,"search-meta-value")]/text()') if x.strip()]
                    lst.append({
                        'vod_id': m.group(1),
                        'vod_name': self._clean(title[0]) if title else '',
                        'vod_pic': self._abs(img[0]) if img else '',
                        'vod_remarks': self._clean(' · '.join(([typ[0].strip()] if typ else []) + metas)),
                    })
                except Exception:
                    continue
        pagecount = self._pagecount(tree, pg, len(lst))
        total = self._total(tree)
        out = {'list': lst, 'page': pg, 'pagecount': max(1, pagecount), 'limit': PAGE_SIZE}
        if total is not None:
            out['total'] = total
        return out

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    def detailContent(self, ids):
        if not ids:
            return {'list': []}
        if isinstance(ids, str):
            ids = [ids]
        items = []
        for vid in ids:
            try:
                item = self._detail(str(vid).split('@@')[0])
            except Exception:
                item = None
            if item:
                items.append(item)
        return {'list': items}

    def _detail(self, vid):
        html = self._get(self.host + '/detail/' + vid + '.html')
        tree = self._tree(html)
        if tree is None:
            return None
        name = ''
        h1 = tree.xpath('//h1/text()')
        if h1:
            name = h1[0].strip()
        pic = ''
        for img in tree.xpath('//img'):
            if '海报' in (img.get('alt') or ''):
                pic = self._abs(img.get('src') or '')
                break
        if not pic:
            imgs = tree.xpath('//img/@src')
            pic = self._abs(imgs[0]) if imgs else ''
        # meta-label / meta-value 键值对：年份 / 地区 / 主演 / 导演 / 语言 / 更新 / 备注
        meta = {}
        for label in tree.xpath('//span[contains(@class,"meta-label")]'):
            k = (label.text or '').replace('：', '').strip()
            v = label.xpath('./following-sibling::span[contains(@class,"meta-value")][1]/text()')
            if k and v:
                meta[k] = v[0].strip()
        # 类型标签：详情信息区里“别名：”之前的短标签
        tags = []
        info = tree.xpath('//*[contains(@class,"detail-info")]')
        if info:
            texts = [x.strip() for x in info[0].xpath('.//text()') if x.strip()]
            if name in texts:
                texts = texts[texts.index(name) + 1:]
            for x in texts:
                if x.startswith('别名'):
                    break
                if len(x) <= 8 and '：' not in x:
                    tags.append(x)
        desc = ''
        syns = [''.join(x.itertext()).strip() for x in tree.xpath('//*[contains(@class,"synopsis")]')]
        syns = [s for s in syns if len(s) > 20]
        if syns:
            desc = max(syns, key=len)
        if not desc and html:
            m = re.search(r'"@type":"TVSeries".*?"description":"(.*?)"', html, re.S)
            if m:
                desc = m.group(1)
        tabs = tree.xpath('//button[contains(@class,"source-tab")]')
        panels = tree.xpath('//*[starts-with(@id,"source-")]')
        play_from, play_url = [], []
        for tab, panel in zip(tabs, panels):
            line = (tab.text or '').strip()
            eps = []
            for a in panel.xpath('.//a[contains(@href,"/vodplay/")]'):
                href = a.get('href') or ''
                m = re.search(r'/vodplay/(\d+)-([^-]+)-(\d+)\.html', href)
                if not m:
                    continue
                epname = self._clean((a.text or '').strip()) or '第%s集' % m.group(3)
                eps.append('%s$%s@@%s@@%s' % (epname, m.group(1), m.group(2), m.group(3)))
            if eps:
                play_from.append(self._clean(line))
                play_url.append('#'.join(eps))
        return {
            'vod_id': vid,
            'vod_name': self._clean(name),
            'vod_pic': pic,
            'type_name': '/'.join(tags),
            'vod_year': meta.get('年份', ''),
            'vod_area': meta.get('地区', ''),
            'vod_actor': meta.get('主演', ''),
            'vod_director': meta.get('导演', ''),
            'vod_remarks': meta.get('备注', '') or meta.get('更新', ''),
            'vod_content': self._clean(desc),
            'vod_play_from': '$$$'.join(play_from),
            'vod_play_url': '$$$'.join(play_url),
        }

    def playerContent(self, flag, id, vipFlags=None):
        pid = id[0] if isinstance(id, (list, tuple)) else id
        parts = str(pid).split('@@')
        if len(parts) != 3:
            return {'parse': 0, 'url': ''}
        vid, slug, idx = parts
        try:
            api = '%s/api/play-url?vodId=%s&playFrom=%s&index=%s' % (
                self.host, vid, quote(slug), idx)
            data = json.loads(self._get(api, referer='%s/vodplay/%s-%s-%s.html' % (self.host, vid, slug, idx)))
        except Exception:
            return {'parse': 0, 'url': ''}
        if data.get('code') == 200 and data.get('url'):
            header = dict(data.get('headers') or {})
            header.setdefault('User-Agent', UA)
            header.setdefault('Referer', self.host + '/')
            if data.get('mode') == 'iframe':
                return {'parse': 1, 'url': data['url'], 'header': header}
            return {'parse': 0, 'url': data['url'], 'header': header}
        return {'parse': 0, 'url': ''}

    # ---------------- 老壳必须实现的抽象方法 ----------------
    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        p = param if isinstance(param, dict) else dict(parse_qsl(str(param or '')))
        t = str(p.get('type') or '')
        u = unquote(str(p.get('url') or ''))
        if t == 'img' and u:
            body = self._bin(u)
            if not body:
                return [404, 'text/plain', b'']
            if body[:3] == b'\xff\xd8\xff':
                mime = 'image/jpeg'
            elif body[:8] == b'\x89PNG\r\n\x1a\n':
                mime = 'image/png'
            elif body[:6] in (b'GIF87a', b'GIF89a'):
                mime = 'image/gif'
            elif body[:4] == b'RIFF' and body[8:12] == b'WEBP':
                mime = 'image/webp'
            else:
                mime = 'image/jpeg'
            return [200, mime, body]
        return [404, 'text/plain', b'']

    def destroy(self):
        pass
