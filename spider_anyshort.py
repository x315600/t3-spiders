# -*- coding: utf-8 -*-
# 站点：https://anyshort.net（AnyShort 短剧） 类型：自研 JSON API  profile：legacy
# 要点（实测 2026-11-10，沙盒 curl）：
# ① 会话：POST /v1/auth/session body {"platform": "web"} 取 access_token（expires_in 1800），
#    其余接口 Bearer 鉴权，401 重登一次再试；无反爬。
# ② 列表：/v1/browse?sort=newest|popular&source=netshort|reelshort&page=N（每页 18，
#    page 含 pages/total 真值，第 2 页与第 1 页零重叠）；/v1/tags/{rep_id}/titles（同上）。
# ③ 首页：/v1/home 的 rails 摊平；/v1/tags 取题材，按 rep_id 去重（同名只留 title_count 最大），
#    只留含中文名的题材，按作品数取前 18；rep 3（上流社會）、rep 8（虐戀情深）实测 titles 为空（total 0），已剔除。
# ④ 详情：/v1/titles/{id}；剧集 /v1/titles/{id}/episodes?offset=N，每页 60，has_more 翻页；播放值=episode_id。
# ⑤ 播放：/v1/episodes/{id}/play，优先 selected（实测 540p h264 HLS m3u8），无 selected 时在 h264 流里按画质数字取最高；
#    播放地址为签名直链，每次现取。m3u8 回 parse=1，mp4 回 parse=0。
# ⑥ 搜索：/v1/search?q=&page=，每页 20，只回 limit + has_more；实测 page 参数被站方忽略（第 2 页与第 1 页完全相同），
#    故 searchContent 只回一页（pagecount=1），不编造分页。
# ⑦ 「最新更新」整页播不了是站方逐集未入库流（has_streams=false、play 接口无 streams），与代码无关；
#    本轮抽查 newest 前 5 部第 1 集均有流——是否可播按集、按时间由站方决定，源只如实返回接口结果，不编造地址。
# ⑧ 内容语言 zh-Hant（封面走 media.anyshort.net）；UA 用移动端，桌面 UA 可能拿到 h265，手机壳播不了。
# 配置 key：anyshort，name：AnyShort短劇，ext：{}
import sys
import re
import json
import time
from urllib.parse import quote, unquote, parse_qsl

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:  # 无 base.spider 的壳 / 本地调试用最小桩
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


# ---------------- CONFIG ----------------
HOST = 'https://anyshort.net'
LANG = 'zh-Hant'
UA = ('Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36')
PAGE_SIZE = 18        # /v1/browse 与 /v1/tags/{id}/titles 每页条数（接口 page.limit）
SEARCH_SIZE = 20      # /v1/search 每页条数（接口 page.limit）
EPISODE_PAGE = 60     # /v1/titles/{id}/episodes 每页条数（接口 page.limit）
TAG_LIMIT = 18        # 首页题材分类取前 N
SOURCES = [('繽紛萬象', 'netshort'), ('時尚上頭', 'reelshort')]  # 官网两个来源集合
DEAD_TAGS = {3, 8}    # 僵尸题材：titles 接口 total=0（2026-11-10 实测）


class Spider(BaseSpider):
    def getName(self):
        return 'AnyShort'

    def init(self, extend=''):
        self.host = HOST
        self.lang = LANG
        self.headers = {
            'User-Agent': UA,
            'Accept': 'application/json',
            'Accept-Language': self.lang + ',zh;q=0.9',
            'Origin': self.host,
            'Referer': self.host + '/' + self.lang + '/',
        }
        self.token = ''
        self._auth()

    # ---------- 网络 ----------
    def _auth(self):
        # POST /v1/auth/session，body 只需 {"platform": "web"}，取 access_token（约 30 分钟有效）
        try:
            r = self.post(self.host + '/v1/auth/session', json={'platform': 'web'},
                          headers=self.headers, timeout=15)
            self.token = r.json().get('data', {}).get('access_token', '')
        except Exception:
            self.token = ''

    def _api(self, path):
        # 带令牌的 GET：401 就重登一次再试，最多两轮，失败回空字典不抛异常
        for _ in range(2):
            try:
                h = dict(self.headers)
                h['authorization'] = 'Bearer ' + self.token
                r = self.fetch(self.host + path, headers=h, timeout=15)
                if r.status_code == 401:
                    self._auth()
                    continue
                return r.json()
            except Exception:
                time.sleep(0.5)
        return {}

    # ---------- 解析 ----------
    def _item(self, v):
        # API 的 title_id/name/cover_url 转框架 vod_ 字段，单条异常不连累整体
        try:
            return {'vod_id': str(v.get('title_id', '')),
                    'vod_name': v.get('name', ''),
                    'vod_pic': v.get('cover_url', '') or ''}
        except Exception:
            return None

    def _paged(self, data, pg):
        # browse / tag-titles 接口：page 含 pages/limit/total 真值；缺失时按是否满页推断
        items = [it for v in data.get('data', []) for it in [self._item(v)] if it and it['vod_id']]
        p = data.get('page', {}) or {}
        pc = p.get('pages')
        if not pc:
            pc = pg + 1 if len(items) >= PAGE_SIZE else pg
        return {'list': items, 'page': pg, 'pagecount': int(pc),
                'limit': int(p.get('limit') or PAGE_SIZE),
                'total': int(p.get('total') or 0)}

    # ---------- 入口 ----------
    def homeContent(self, filter):
        # 分类 = 最新更新 + 熱門榜 + 来源集合 + 热门中文题材；无实测生效的筛选维度，filters 为空
        classes = [{'type_id': 'newest', 'type_name': '最新更新'},
                   {'type_id': 'popular', 'type_name': '熱門榜'}]
        classes += [{'type_id': 'src:' + code, 'type_name': name} for name, code in SOURCES]
        best = {}
        for t in self._api('/v1/tags?lang=' + self.lang).get('data', []):
            rep, name = t.get('rep_id'), t.get('name') or ''
            if not rep or rep in DEAD_TAGS or not re.search(r'[\u4e00-\u9fff]', name):
                continue
            if rep not in best or (t.get('title_count') or 0) > (best[rep].get('title_count') or 0):
                best[rep] = t
        tags = sorted(best.values(), key=lambda t: -(t.get('title_count') or 0))[:TAG_LIMIT]
        classes += [{'type_id': 'tag:' + str(t.get('rep_id')), 'type_name': t.get('name')} for t in tags]
        return {'class': classes, 'list': [], 'filters': {}}

    def homeVideoContent(self):
        # /v1/home 的 rails 推荐位全部摊平成列表
        data = self._api('/v1/home?lang=' + self.lang).get('data', {}).get('rails', [])
        items = [it for rail in data for v in rail.get('items', [])
                 for it in [self._item(v)] if it and it['vod_id']]
        return {'list': items}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if pg else 1
        if pg < 1:
            pg = 1
        if tid in ('newest', 'popular'):
            data = self._api('/v1/browse?lang=%s&page=%d&sort=%s' % (self.lang, pg, tid))
        elif tid.startswith('src:'):
            data = self._api('/v1/browse?lang=%s&page=%d&source=%s' % (self.lang, pg, tid[4:]))
        elif tid.startswith('tag:'):
            data = self._api('/v1/tags/%s/titles?lang=%s&page=%d' % (tid[4:], self.lang, pg))
        else:
            data = self._api('/v1/browse?lang=%s&page=%d&sort=newest' % (self.lang, pg))
        return self._paged(data, pg)

    def detailContent(self, ids):
        result = {'list': []}
        for vid in ids:
            try:
                v = self._api('/v1/titles/%s?lang=%s' % (vid, self.lang)).get('data', {})
                eps, offset = [], 0
                while True:
                    d = self._api('/v1/titles/%s/episodes?lang=%s&offset=%d' % (vid, self.lang, offset))
                    eps += d.get('data', [])
                    if not d.get('page', {}).get('has_more'):
                        break
                    offset += EPISODE_PAGE
                play = '#'.join('第%s$%s' % (e.get('episode_no'), e.get('episode_id'))
                                for e in eps if e.get('episode_id'))
                total = v.get('total_episodes') or v.get('episode_count') or len(eps)
                remarks = ''
                if total:
                    remarks = '共%s集' % total
                    if v.get('is_completed'):
                        remarks += ' · 已完结'
                result['list'].append({
                    'vod_id': str(vid),
                    'vod_name': v.get('name', ''),
                    'vod_pic': v.get('cover_url', '') or '',
                    'vod_remarks': remarks,
                    'vod_content': (v.get('description', '') or '').strip(),
                    'vod_play_from': 'AnyShort',
                    'vod_play_url': play,
                })
            except Exception:
                continue
        return result

    def searchContent(self, key, quick, pg='1'):
        # 搜索关键词 quote 编码；站方忽略 page 参数（第 2 页与第 1 页完全相同，2026-11-10 实测），只回一页
        pg = int(pg) if pg else 1
        if pg < 1:
            pg = 1
        data = self._api('/v1/search?q=%s&lang=%s&page=%d' % (quote(key), self.lang, pg)).get('data', [])
        items = [it for v in data for it in [self._item(v)] if it and it['vod_id']]
        return {'list': items, 'page': pg, 'pagecount': 1,
                'limit': SEARCH_SIZE, 'total': len(items)}

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    def playerContent(self, flag, id, vipFlags):
        # /v1/episodes/{episode_id}/play：优先 API 推荐的 selected（一般是 h264，兼容性最好）；
        # 没有再在 h264 流里按画质数字选最高。别无脑选 1080p，那个多是 h265，很多手机壳播不了。
        # 签名地址约 1.6 小时有效，每次现取现用；只回 parse/url/header。
        data = self._api('/v1/episodes/%s/play?lang=%s' % (id, self.lang)).get('data', {})
        url = (data.get('selected') or {}).get('url', '')
        if not url:
            streams = data.get('streams', [])

            def _q(s):
                mm = re.search(r'(\d+)', str(s.get('quality') or ''))
                return int(mm.group(1)) if mm else 0

            h264 = [s for s in streams if '264' in str(s.get('codec', ''))]
            pool = h264 or streams
            url = sorted(pool, key=_q)[-1].get('url', '') if pool else ''
        return {'parse': 0 if '.m3u8' not in url else 1, 'url': url, 'header': dict(self.headers)}

    # ---------- 老壳必须实现的抽象方法 ----------
    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        # 本站图片/播放均可直连，不需要代理；参数可能是 dict 或字符串，都接
        return [404, 'text/plain', b'']

    def destroy(self):
        pass
