# -*- coding: utf-8 -*-
# 黄果漫剧 (https://q8jw.ltfqnhxx.cc) — legacy profile（老壳：继承 base.spider）
# 重写日期：2026-11-10（按 catvod-py-spider 技能 + field-practice.md）
# 站点要点（2026-11-10 实测）：
#   ① 真实分类 = 导航栏 5 个频道（标签只是视频属性，不是分类）：
#      视频类 ai-manju 原创漫剧 / lifan-dongman 里番动漫 / duanju 成人短剧：
#        GET /api/videos/category/{ch}?sort=latest|hot|random&page=&size=18，
#        响应 data.items + data.pagination{page,pages,size,total}；
#        不带 sort 默认等于 hot；第 1/2 页零重叠。
#      漫画类 riman 成人日漫 / hanman 成人韩漫：
#        GET /api/comics/category/{ch}?sort=new|hot|end&page=&size=18，无 random，
#        sort=latest 无效；item 字段 id/title/cover/status_line/tags。
#   ② 标签筛选：HTML /tag/{slug}/，分页 /tag/{slug}/page/{pg}/，
#      每页 20 条，data-pages 给总页数；tag API 返回空，不可用；标签页只有视频。
#   ③ 搜索：/search/?keyword={kw}，分页 /search/video/{kw}/{pg}/，每页 20 条，
#      data-pages 给总页数；卡片与频道页同结构。
#   ④ 详情视频 /video/{id}/：内嵌 <script id="videoInitialData" type="application/json">
#      -> epPlaySrcs {集号: m3u8 直链，带时效 auth_key，详情页打开时现取现用}。
#   ⑤ 详情漫画 /comic/{id}/：纯图片章节，无视频/m3u8；只给信息，不给播放链接
#      （不伪造播放地址）；漫画 id 在列表里加 "c" 前缀，与视频 id 区分。
#   ⑥ 封面：pic.drleao.cn 的图是 AES-128-CBC 加密字节（key/iv 见下），
#      列表/详情的 vod_pic 走 localProxy 实时解密；imgpublic.ycomesc.live 的图
#      本身就是图片（JPEG/WEBP），localProxy 检测到图片魔数直接原样返回，不解密。
#   ⑦ 播放：m3u8 直链，parse 0；分片 AES-128 加密（EXT-X-KEY），由播放器处理。
#   ⑧ 禁词过滤：标题/标签命中禁词的条目在列表层与详情层直接丢弃。
import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.append('..')
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

try:
    import requests
    _SESSION = requests.Session()
    _SESSION.mount("http://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16))
    _SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16))
except Exception:
    requests = None  # → urllib.request 兜底
    _SESSION = None

UA = ("Mozilla/5.0 (Linux; Android 13; moto g100) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")
HOST = "https://q8jw.ltfqnhxx.cc"

# 封面 AES-CBC 解密参数（与黄果短剧站同源，2026-11-10 实测本站封面可解）
IMG_KEY = b"f5d965df75336270"
IMG_IV = b"97b60394abc2fbe1"

# 禁词过滤：命中标题或标签的条目直接丢弃（列表层 + 详情层）。
# 词以 unicode 转义存放，避免字面出现在文件里；运行时解码后比对。
_BANNED = (
    "\u5e7c\u5973", "\u5c0f\u5b66\u751f", "\u521d\u4e2d\u751f",
    "\u841d\u8389", "\u5e7c\u9f7f", "\u672a\u6210\u5e74",
)


def _banned(text):
    s = str(text or "")
    return any(w in s for w in _BANNED)


# 真实频道：导航栏 5 个（2026-11-08 取证，2026-11-10 复测仍有效）。
# 标签只是视频上的属性，不是分类。
CHANNELS = [
    ("ai-manju", "原创漫剧", "videos"),
    ("lifan-dongman", "里番动漫", "videos"),
    ("duanju", "成人短剧", "videos"),
    ("riman", "成人日漫", "comics"),
    ("hanman", "成人韩漫", "comics"),
]
CHANNEL_KIND = {tid: kind for tid, _name, kind in CHANNELS}

# 排序选项（显示名，值）：视频类三档，漫画类三档（漫画无随机，latest 无效）
SORTS = {
    "videos": (("最新更新", "latest"), ("当前热播", "hot"), ("随机推荐", "random")),
    "comics": (("最新推荐", "new"), ("人气漫画", "hot"), ("完结漫画", "end")),
}
SORT_DEFAULT = {"videos": "latest", "comics": "new"}
API_SIZE = 18  # 站内 JS 的 SIZE
HTML_PAGE_SIZE = 20  # 标签页 / 搜索页实测每页 20 条

# 标签筛选：站内真实存在的 slug，按 5 个频道页出现频次取前 24
# 六个禁词不在其中，全站复扫 0 命中
TAG_FILTERS = [
    ("全部", ""),
    ("都市", "dushi"), ("中文字幕", "zhongwen-zimu"), ("大男主", "dananzhu"),
    ("现代", "xiandai"), ("无码", "wuma"), ("奇幻", "qihuan"),
    ("后宫", "hougong"), ("ntr", "ntr"), ("系统", "xitong"),
    ("男同", "nantong"), ("虐恋", "nuelian"), ("JK", "jk"),
    ("职场", "zhichang"), ("H动漫", "hdongman"), ("大女主", "danvzhu"),
    ("穿越", "chuanyue"), ("绿帽", "lvmao"), ("校园", "xiaoyuan"),
    ("强制", "qiangzhi"), ("同学", "tongxue"), ("甜宠", "tianchong"),
    ("sm", "sm"), ("古风", "gufeng"), ("仙侠", "xianxia"),
]

# 标签显示名 → slug（壳传显示名时映射回 slug；“全部”→ 空，即不过滤）
_TAG_BY_NAME = {n: v for n, v in TAG_FILTERS}


def _img_mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---------- 纯 Python AES-128（TV 端无 pycryptodome 时的兜底） ----------
def _gf_mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = ((a << 1) ^ 0x11B) & 0xFF if a & 0x80 else (a << 1) & 0xFF
        b >>= 1
    return r


def _build_sboxes():
    sbox = [0] * 256
    inv = [0] * 256
    for i in range(256):
        # GF(2^8) 求逆：a^254
        p, x = 1, i
        for _ in range(254):
            p = _gf_mul(p, x) if x else 0
            if x == 0:
                break
        inv_i = p if i else 0
        # 仿射变换
        v = inv_i
        s = v ^ ((v << 1) | (v >> 7)) & 0xFF ^ ((v << 2) | (v >> 6)) & 0xFF \
            ^ ((v << 3) | (v >> 5)) & 0xFF ^ ((v << 4) | (v >> 4)) & 0xFF ^ 0x63
        sbox[i] = s & 0xFF
        inv[s & 0xFF] = i
    return sbox, inv


_SBOX, _INV_SBOX = _build_sboxes()


def _expand_key(key):
    rcon = [0x01]
    for _ in range(9):
        rcon.append(_gf_mul(rcon[-1], 0x02))
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = w[i - 1][:]
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= rcon[i // 4 - 1]
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [b for word in w for b in word]


_RK = _expand_key(list(IMG_KEY))


def _add_round_key(s, rk, rnd):
    off = rnd * 16
    return [b ^ rk[off + i] for i, b in enumerate(s)]


def _inv_shift_rows(s):
    # s 为 16 字节列优先（索引 r+4c）；第 r 行右移 r 位：new[r][c] = old[r][(c-r)%4]
    return [s[r + 4 * ((c - r) % 4)] for c in range(4) for r in range(4)]


def _inv_sub_bytes(s):
    return [_INV_SBOX[b] for b in s]


def _inv_mix_columns(s):
    out = [0] * 16
    for c in range(4):
        a = s[4 * c:4 * c + 4]
        out[4 * c] = _gf_mul(a[0], 0x0E) ^ _gf_mul(a[1], 0x0B) ^ _gf_mul(a[2], 0x0D) ^ _gf_mul(a[3], 0x09)
        out[4 * c + 1] = _gf_mul(a[0], 0x09) ^ _gf_mul(a[1], 0x0E) ^ _gf_mul(a[2], 0x0B) ^ _gf_mul(a[3], 0x0D)
        out[4 * c + 2] = _gf_mul(a[0], 0x0D) ^ _gf_mul(a[1], 0x09) ^ _gf_mul(a[2], 0x0E) ^ _gf_mul(a[3], 0x0B)
        out[4 * c + 3] = _gf_mul(a[0], 0x0B) ^ _gf_mul(a[1], 0x0D) ^ _gf_mul(a[2], 0x09) ^ _gf_mul(a[3], 0x0E)
    return out


def _aes_cbc_decrypt_pure(data):
    # 转列优先
    def to_cols(blk):
        return [blk[r + 4 * c] for c in range(4) for r in range(4)]

    def to_blk(cols):
        return bytes(cols[r + 4 * c] for c in range(4) for r in range(4))

    out = bytearray()
    prev = list(IMG_IV)
    for off in range(0, len(data), 16):
        blk = to_cols(list(data[off:off + 16]))
        s = _add_round_key(blk, _RK, 10)
        for rnd in range(9, 0, -1):
            s = _inv_shift_rows(s)
            s = _inv_sub_bytes(s)
            s = _add_round_key(s, _RK, rnd)
            s = _inv_mix_columns(s)
        s = _inv_shift_rows(s)
        s = _inv_sub_bytes(s)
        s = _add_round_key(s, _RK, 0)
        plain = to_blk(s)
        out += bytes(b ^ p for b, p in zip(plain, prev))
        prev = list(data[off:off + 16])
    return bytes(out)


try:
    from Crypto.Cipher import AES as _AES

    def _aes_cbc_decrypt(data):
        return _AES.new(IMG_KEY, _AES.MODE_CBC, IMG_IV).decrypt(bytes(data))
except Exception:
    _aes_cbc_decrypt = _aes_cbc_decrypt_pure  # 无依赖兜底：慢但能用
# ---------- 纯 Python AES 结束 ----------


class Spider(BaseSpider):
    def getName(self):
        return "黄果漫剧"

    def isVideoFormat(self, url):
        url = (url or "").lower()
        return any(url.split("?")[0].endswith(x) for x in (".m3u8", ".mp4"))

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        self._ready = False
        self.config = {}
        self.host = HOST
        # ext 形态不固定：字符串 JSON / 已解析 dict / None / 空串都要能进，
        # 绝不抛错（2026-11-08 教训：旧版生成器只认 str，壳传 dict 时 init
        # 抛 TypeError，全部分类变空 + pagecount=1，盒子上就是“没数据、翻不了页”）
        try:
            if isinstance(extend, dict):
                config = extend
            elif isinstance(extend, str) and extend.strip():
                config = json.loads(extend)
            else:
                config = {}
            if not isinstance(config, dict):
                config = {}
        except Exception:
            config = {}
        self.config = config
        if isinstance(config.get("host"), str) and config["host"].strip():
            self.host = config["host"].rstrip("/")
        self._headers = {"User-Agent": UA, "Referer": self.host + "/"}
        self._ready = True

    def destroy(self):
        self._ready = False
        self.config = {}

    # ----- 网络：模块级 requests.Session，缺 requests 时 urllib 兜底；超时单位秒 -----
    def _http(self, url, headers=None, timeout=15, binary=False):
        if not url.startswith("http"):
            url = self.host + url
        hd = dict(self._headers)
        if headers:
            hd.update(headers)
        last = None
        for _ in range(2):  # 有限次重试
            try:
                if _SESSION is not None:
                    r = _SESSION.get(url, headers=hd, timeout=timeout)
                    if r.status_code != 200:
                        raise RuntimeError("HTTP %s" % r.status_code)
                    return r.content if binary else r.content.decode("utf-8", "ignore")
                req = urllib.request.Request(url, headers=hd)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    return raw if binary else raw.decode("utf-8", "ignore")
            except Exception as e:
                last = e
                time.sleep(0.3)
        raise last

    def _get_html(self, url, timeout=15):
        return self._http(url, timeout=timeout)

    def _fetch_bytes(self, url, timeout=25):
        return self._http(url, timeout=timeout, binary=True)

    def _get_json(self, url, timeout=15):
        # 站内 JSON API：/api/videos|comics/category/{ch}?sort=&page=&size=
        raw = self._http(url, headers={"Accept": "application/json"}, timeout=timeout)
        try:
            data = json.loads(raw)
        except Exception:
            raise ValueError("bad json from " + url)
        if not isinstance(data, dict):
            raise ValueError("bad api envelope from " + url)
        if data.get("status") in (0, "0", False) or not isinstance(data.get("data"), dict):
            # status=0 + 空 data：分类不存在 / 排序无效等，带上站方 msg
            raise ValueError(str(data.get("msg") or "api error") + " @ " + url)
        return data["data"]

    def _parse_extend(self, extend):
        # 壳传筛选：dict（FongMi/TVBox 常规）或 JSON 字符串；其它形态当空
        # 值要做容错：有的壳传列表（取首项）、有的传非字符串（转字符串），
        # 否则下游 .strip() 直接抛错，整分类变“加载失败”
        try:
            if isinstance(extend, dict):
                raw = extend
            elif isinstance(extend, str) and extend.strip().startswith("{"):
                parsed = json.loads(extend)
                raw = parsed if isinstance(parsed, dict) else {}
            else:
                return {}
        except Exception:
            return {}
        out = {}
        for k, v in raw.items():
            if isinstance(v, (list, tuple)):
                v = v[0] if v else ""
            if not isinstance(v, str):
                v = "" if v is None else str(v)
            out[str(k)] = v
        return out

    # ----- 列表解析 -----
    def _episode_remark(self, item):
        # 与站内 JS 的 card() 同款文案：全N集 / 更新至N集 / 更新至1集
        try:
            if item.get("is_finished") and int(item.get("total_episodes") or 0) > 0:
                return "全%d集" % int(item["total_episodes"])
            if int(item.get("episode_count") or 0) > 0:
                return "更新至%d集" % int(item["episode_count"])
        except Exception:
            pass
        return "更新至1集"

    def _api_cards(self, data, kind):
        vods = []
        seen = set()
        for item in data.get("items") or []:
            try:
                vid = str(item.get("id") or "")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                tags = item.get("tags") or []
                if _banned(item.get("title")) or _banned(",".join(str(t) for t in tags)):
                    continue
                if kind == "comics":
                    vods.append({
                        "vod_id": "c" + vid,
                        "vod_name": str(item.get("title") or "").replace("$", "＄").replace("#", "＃"),
                        "vod_pic": self._pic(item.get("cover") or ""),
                        "vod_remarks": str(item.get("status_line") or "").strip(),
                        "vod_tag": ",".join(str(t) for t in tags),
                    })
                else:
                    vods.append({
                        "vod_id": vid,
                        "vod_name": str(item.get("title") or "").replace("$", "＄").replace("#", "＃"),
                        "vod_pic": self._pic(item.get("cover") or ""),
                        "vod_remarks": self._episode_remark(item),
                        "vod_tag": ",".join(str(t) for t in tags),
                    })
            except Exception:
                continue
        return vods

    def _scope_active_grid(self, html):
        # 频道页有 最新/热播/随机 三个 tab，只取 is-active 那个；其它页只有一个 grid，直接全页解析
        grids = re.split(r'<div class="hg-comic-card-grid([^"]*)"', html)
        if len(grids) < 3:
            return html
        for i in range(1, len(grids), 2):
            if "is-active" in grids[i] and i + 1 < len(grids):
                return grids[i + 1]
        return html

    def _parse_cards(self, html):
        body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
        body = self._scope_active_grid(body)
        vods = []
        seen = set()
        for part in re.split(r'<div class="hg-comic-feed-card"', body)[1:]:
            try:
                # 视频 /video/{id}/ 与漫画 /comic/{id}/ 都可能出现；漫画 id 加 "c" 前缀区分
                m = re.search(r'href="/(video|comic)/(\d+)/"', part)
                if not m:
                    continue
                vid = m.group(2) if m.group(1) == "video" else "c" + m.group(2)
                if vid in seen:
                    continue
                t = re.search(r"<h3>([^<]+)</h3>", part)
                title = t.group(1).strip() if t else ""
                if _banned(title):
                    continue
                img = re.search(r'data-src="([^"]+)"', part)
                ep = re.search(r'hg-comic-feed-card__episode"[^>]*>(.*?)</span>', part, re.S)
                seen.add(vid)
                vods.append({
                    "vod_id": vid,
                    "vod_name": (t.group(1).strip() if t else "").replace("$", "＄").replace("#", "＃"),
                    "vod_pic": self._pic(img.group(1)) if img else "",
                    "vod_remarks": re.sub(r"<[^>]+>", "", ep.group(1)).strip() if ep else "",
                })
            except Exception:
                continue
        if vods:
            return vods
        # 兜底：只有 4 条精选 hg-drama-card 的页面（如旧站形态），才解析它
        for part in re.split(r'<div class="hg-drama-card"', body)[1:]:
            try:
                m = re.search(r'href="/(video|comic)/(\d+)/"', part)
                if not m:
                    continue
                vid = m.group(2) if m.group(1) == "video" else "c" + m.group(2)
                if vid in seen:
                    continue
                t = re.search(r'hg-drama-card__title"><a[^>]*>([^<]+)', part)
                img = re.search(r'data-src="([^"]+)"', part)
                ep = re.search(r'hg-drama-card__episode"[^>]*>(.*?)</span>', part, re.S)
                score = re.search(r'hg-drama-card__score">([^<]+)</span>', part)
                seen.add(vid)
                vods.append({
                    "vod_id": vid,
                    "vod_name": (t.group(1).strip() if t else "").replace("$", "＄").replace("#", "＃"),
                    "vod_pic": self._pic(img.group(1)) if img else "",
                    "vod_remarks": (re.sub(r"<[^>]+>", "", ep.group(1)).strip() if ep else "")
                                   + (" " + score.group(1) if score else ""),
                })
            except Exception:
                continue
        return vods

    def _pagecount(self, html):
        m = re.search(r'data-pages="(\d+)"', html)
        if m:
            return int(m.group(1))
        pages = [int(x) for x in re.findall(r'hg-pager__page"[^>]*>(\d+)<', html)]
        return max(pages) if pages else 1

    def _category_path(self, tid, pg):
        # 标签筛选专用：/tag/{slug}/ 与 /tag/{slug}/page/{pg}/（HTML 单一 grid）
        # slug 做 URL 编码，非 ASCII 标签不再让 urllib 直接抛错
        tid = urllib.parse.quote(str(tid), safe='')
        return "/tag/%s/" % tid if pg <= 1 else "/tag/%s/page/%d/" % (tid, pg)

    # ----- 详情解析 -----
    def _video_dict(self, html):
        m = re.search(r'<script id="videoInitialData" type="application/json">(.*?)</script>', html, re.S)
        if not m:
            raise ValueError("videoInitialData missing")
        try:
            data = json.loads(m.group(1))
        except Exception:
            raise ValueError("bad videoInitialData json")
        if not isinstance(data, dict):
            raise ValueError("bad videoInitialData envelope")
        return data

    def _detail_vod(self, vid, o):
        tags = o.get("tags") or []
        if _banned(o.get("title")) or _banned(",".join(str(t) for t in tags)):
            raise ValueError("banned content")
        eps = o.get("epPlaySrcs") or {}
        keys = sorted(eps.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)
        plays = ["第%s集$%s" % (k, eps[k]) for k in keys if eps[k]]
        return {
            "vod_id": vid,
            "vod_name": (o.get("title") or "").replace("$", "＄").replace("#", "＃"),
            "vod_pic": self._pic(o.get("coverSrc") or o.get("posterSrc") or ""),
            "vod_content": (o.get("description") or "").strip(),
            "vod_tag": ",".join(str(t) for t in tags),
            "vod_play_from": "黄果",
            "vod_play_url": "#".join(plays),
        }

    def _comic_dict(self, html):
        # 漫画详情页：纯图片章节，无视频；只取信息，不取播放
        def _meta(prop):
            m = re.search(r'<meta property="%s" content="([^"]*)"' % re.escape(prop), html)
            return (m.group(1).strip() if m else "")
        title = _meta("og:title")
        if title.endswith(" - 黄果漫剧"):
            title = title[:-len(" - 黄果漫剧")]
        m = re.search(r'<meta name="description" content="([^"]*)"', html)
        desc = m.group(1).strip() if m else ""
        tags = [n for _s, n in re.findall(r'href="/tag/([a-z0-9\-]+)/"[^>]*>([^<]+)</a>', html)]
        chapters = sorted(set(re.findall(r'/chapter/(\d+)/', html)), key=lambda x: int(x))
        status = ""
        m = re.search(r'更新至\d+话|已完结|连载中', html)
        if m:
            status = m.group(0)
        return {"title": title, "cover": _meta("og:image"), "description": desc,
                "tags": tags, "chapters": chapters, "status": status}

    def _comic_vod(self, vid, o):
        # 漫画在视频盒子上无法播放：只给信息，不给播放链接（不伪造 m3u8）
        if _banned(o.get("title")) or _banned(",".join(o.get("tags") or [])):
            raise ValueError("banned content")
        ch = o.get("chapters") or []
        remark = o.get("status") or (("共%d话" % len(ch)) if ch else "漫画")
        return {
            "vod_id": vid,
            "vod_name": (o.get("title") or "").replace("$", "＄").replace("#", "＃"),
            "vod_pic": self._pic(o.get("cover") or ""),
            "vod_content": (o.get("description") or "").strip(),
            "vod_tag": ",".join(o.get("tags") or []),
            "vod_remarks": remark,
            "vod_play_from": "漫画",
            "vod_play_url": "",
        }

    # ----- 封面：走 localProxy 解密 -----
    def _pic(self, url):
        if not url:
            return ""
        try:
            base = self.getProxyUrl() if callable(getattr(self, "getProxyUrl", None)) else None
        except Exception:
            base = None
        if base:
            return base + "&type=img&url=" + urllib.parse.quote(url, safe="")
        return url

    # ----- 入口 -----
    def homeContent(self, filter):
        classes = [{"type_id": tid, "type_name": name} for tid, name, _k in CHANNELS]
        filters = {}
        for tid, _name, kind in CHANNELS:
            tag_values = [{"n": "全部", "v": tid}]  # “全部”的 v = 一级 tid
            tag_values += [{"n": n, "v": v} for n, v in TAG_FILTERS[1:]]
            filters[tid] = [
                {"key": "sort", "name": "排序",
                 "value": [{"n": n, "v": v} for n, v in SORTS[kind]]},
                {"key": "tag", "name": "标签", "value": tag_values},
            ]
        return {"class": classes, "filters": filters}

    def homeVideoContent(self):
        try:
            html = self._get_html("/")
            return {"list": self._parse_cards(html)[:20]}
        except Exception:
            return {"list": []}

    def _tag_page(self, tag, pn):
        html = self._get_html(self._category_path(tag, pn))
        cards = self._parse_cards(html)
        pc = self._pagecount(html)
        if not cards:
            pc = pn  # 空页刹车
        # total 不填：HTML 页只有总页数，没有精确总条数
        return {"list": cards, "page": pn, "pagecount": pc, "limit": HTML_PAGE_SIZE}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pn = int(pg) if pg else 1
        except Exception:
            pn = 1
        if pn < 1:
            pn = 1
        try:
            ext = self._parse_extend(extend)
            tag = (ext.get("tag") or "").strip()
            tag = _TAG_BY_NAME.get(tag, tag)  # 壳传显示名（都市/全部）时映射回 slug/空
            if tag == str(tid):
                tag = ""  # “全部”v=一级 tid，等于不过滤
            if tag:
                # 选了标签：标签页是 HTML、无排序，排序选择此时忽略
                return self._tag_page(tag, pn)
            kind = CHANNEL_KIND.get(str(tid), "videos")
            sort = (ext.get("sort") or "").strip() or SORT_DEFAULT[kind]
            if sort not in [v for _n, v in SORTS[kind]]:
                sort = SORT_DEFAULT[kind]
            api = "videos" if kind == "videos" else "comics"
            url = "/api/%s/category/%s?sort=%s&page=%d&size=%d" % (api, tid, sort, pn, API_SIZE)
            data = self._get_json(url)
            cards = self._api_cards(data, kind)
            pginfo = data.get("pagination") or {}
            pc = int(pginfo.get("pages") or 1)
            total = int(pginfo.get("total") or 0)
            if not cards:
                pc = pn  # 空页刹车
            return {"list": cards, "page": pn, "pagecount": pc,
                    "limit": int(pginfo.get("size") or API_SIZE), "total": total}
        except Exception as e:
            # 失败必须看得见：静默返回空列表 + pagecount=1 时，盒子上
            # 超时和解析错都长得跟“没数据、没下一页”一样，门禁也抓不到。
            # 现在返回一条报错项，点开无害（detail 对 __error__ 返回空）。
            return {"page": pn, "pagecount": 1, "limit": API_SIZE, "total": 0, "list": [{
                "vod_id": "__error__",
                "vod_name": "加载失败：" + type(e).__name__ + " " + str(e)[:80],
                "vod_pic": "",
                "vod_remarks": "分类 " + str(tid) + " 第 " + str(pn) + " 页",
            }]}

    def searchContent(self, key, quick, pg="1"):
        try:
            pn = int(pg) if pg else 1
        except Exception:
            pn = 1
        if pn < 1:
            pn = 1
        try:
            kw = urllib.parse.quote(str(key or ""))
            path = "/search/?keyword=%s" % kw if pn <= 1 else "/search/video/%s/%d/" % (kw, pn)
            html = self._get_html(path)
            cards = self._parse_cards(html)
            pc = self._pagecount(html)
            if not cards:
                pc = pn
            return {"list": cards, "page": pn, "pagecount": pc, "limit": HTML_PAGE_SIZE}
        except Exception:
            return {"list": [], "page": pn, "pagecount": 1, "limit": HTML_PAGE_SIZE}

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, list):
            return {"list": []}
        result = []
        for vid in ids:
            try:
                vid = str(vid)
                if vid.startswith("c") and vid[1:].isdigit():
                    html = self._get_html("/comic/%s/" % vid[1:])
                    result.append(self._comic_vod(vid, self._comic_dict(html)))
                else:
                    html = self._get_html("/video/%s/" % vid)
                    result.append(self._detail_vod(vid, self._video_dict(html)))
            except Exception:
                continue  # 单个 id 失败跳过，不拖垮整批
        return {"list": result}

    def playerContent(self, flag, id, vipFlags):
        url = str(id or "")
        direct = url.split("?")[0].lower().endswith((".m3u8", ".mp4", ".flv", ".mkv"))
        if direct:
            return {"parse": 0, "url": url, "header": dict(self._headers)}
        if url.startswith("http"):
            return {"parse": 1, "url": url, "header": dict(self._headers)}
        return {"parse": 0, "url": url, "header": dict(self._headers)}

    # ----- 本地代理：封面解密 -----
    def localProxy(self, param):
        try:
            p = param if isinstance(param, dict) else dict(urllib.parse.parse_qsl(str(param or "")))
            if str(p.get("type") or "") == "img" and p.get("url"):
                raw = self._fetch_bytes(urllib.parse.unquote(str(p["url"])))
                mime = _img_mime(raw)
                if mime:
                    # 本身就是图片（如漫画详情 og:image 在 imgpublic 域，实为 WEBP），原样返回不解密
                    return [200, mime, raw]
                plain = _aes_cbc_decrypt(raw)
                mime = _img_mime(plain)
                if mime:
                    return [200, mime, plain]
        except Exception:
            pass
        return [404, "text/plain", b""]
