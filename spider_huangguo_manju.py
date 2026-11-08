# -*- coding: utf-8 -*-
# 黄果漫剧 (q8jw.ltfqnhxx.cc) T3 spider
# 与黄果短剧草稿同套前端/同封面 AES 密钥，但内容库独立（video id 不互通，2588 在旧站 404），故单独立源
# 站点结构（2026-11-08 沙盒实测打通）：
#   分类: /tag/{slug}/            分页 /tag/{slug}/page/{pg}/   （data-pages 给总页数）
#   频道: /ai-manju/              分页 /ai-manju/{pg}/          （data-pages 给总页数）
#   搜索: /search/?keyword={kw}   分页 /search/video/{kw}/{pg}/
#   详情: /video/{id}/  内嵌 <script id="videoInitialData" type="application/json">
#         -> epPlaySrcs {集号: m3u8直链}，tags / coverSrc / description 全有
#   列表: 主列表是 hg-comic-feed-card（每页 20 条）；hg-drama-card 只有 4 条精选
#         频道页有 最新/热播/随机 三个 tab，只取 hg-comic-card-grid is-active 那个
#   封面: AES-CBC 加密字节（key/iv 与黄果短剧草稿相同，2026-11-08 实测可解出 JPEG）
#         列表/详情的 vod_pic 走 localProxy，TV 端实时解密出图
#   播放: m3u8 直链（带时效 auth_key，详情页打开时现取现用）

import base64
import json
import re
import urllib.parse
import urllib.request
import urllib.error

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider:  # 桌面自测桩
        def getDependence(self): return []
        def init(self, extend=""): pass
        def destroy(self): pass

UA = ("Mozilla/5.0 (Linux; Android 13; moto g100) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")

HOSTS = ["https://q8jw.ltfqnhxx.cc"]

# 封面 AES-CBC 解密参数（与黄果短剧站同源，2026-11-08 实测本站封面可解）
IMG_KEY = b"f5d965df75336270"
IMG_IV = b"97b60394abc2fbe1"

# 频道：原创漫剧（/ai-manju/，分页 /ai-manju/{pg}/）
CHANNELS = [("ai-manju", "原创漫剧")]

# 默认分类：中性题材（均已在本站验证有数据）。站内另有成人频道
# duanju/riman/hanman/lifan-dongman 及成人向标签 luanlun/lvmao/muzi/ntr/3p/
# koujiao/juru-2/sm/nuelian/tianchong/huangdao/shunv/meimei/tongxue/jk/
# nvjiaoshi/qiangzhi/chihan/gongxi/xingzhuan，需要再加（只注释、不启用）
CATEGORIES = [
    ("dushi", "都市"), ("xiandai", "现代"), ("zhichang", "职场"),
    ("nixi", "逆袭"), ("gufeng", "古风"), ("xianxia", "仙侠"),
    ("hougong", "后宫"), ("qihuan", "奇幻"), ("xitong", "系统"),
    ("naodong", "脑洞"), ("dananzhu", "大男主"), ("yujie", "御姐"),
    ("minguo", "民国"), ("wuxia", "武侠"), ("xuanhuan", "玄幻"),
    ("xiuxian", "修仙"), ("youxi", "游戏"), ("danvzhu", "大女主"),
]


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
except ImportError:
    _aes_cbc_decrypt = _aes_cbc_decrypt_pure  # 无依赖兜底：慢但能用
# ---------- 纯 Python AES 结束 ----------


class Spider(_BaseSpider):
    def getName(self):
        return "黄果漫剧"

    def isVideoFormat(self, url):
        url = (url or "").lower()
        return any(url.split("?")[0].endswith(x) for x in (".m3u8", ".mp4"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def init(self, extend=""):
        self.host = HOSTS[0]
        self._extend = {}
        if isinstance(extend, str) and extend:
            try:
                self._extend = json.loads(extend)
            except Exception:
                self._extend = {}
        if isinstance(self._extend, dict) and self._extend.get("host"):
            self.host = self._extend["host"].rstrip("/")

    # ----- 网络 -----
    def _fetch(self, url, timeout=20):
        last = None
        for h in [self.host] + [x for x in HOSTS if x != self.host]:
            u = url if url.startswith("http") else h + url
            try:
                req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": h + "/"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "ignore"), u
            except Exception as e:
                last = e
        raise last

    def _fetch_bytes(self, url, timeout=25):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": self.host + "/"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

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
        return url  # 桌面自测时直链（加密字节，壳端才解密）

    # ----- 卡片解析 -----
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
                m = re.search(r'href="(/video/(\d+)/)"', part)
                if not m or m.group(2) in seen:
                    continue
                t = re.search(r"<h3>([^<]+)</h3>", part)
                img = re.search(r'data-src="([^"]+)"', part)
                ep = re.search(r'hg-comic-feed-card__episode"[^>]*>(.*?)</span>', part, re.S)
                seen.add(m.group(2))
                vods.append({
                    "vod_id": m.group(2),
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
                m = re.search(r'href="(/video/(\d+)/)"', part)
                if not m or m.group(2) in seen:
                    continue
                t = re.search(r'hg-drama-card__title"><a[^>]*>([^<]+)', part)
                img = re.search(r'data-src="([^"]+)"', part)
                ep = re.search(r'hg-drama-card__episode"[^>]*>(.*?)</span>', part, re.S)
                score = re.search(r'hg-drama-card__score">([^<]+)</span>', part)
                seen.add(m.group(2))
                vods.append({
                    "vod_id": m.group(2),
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

    # ----- 五接口 -----
    def homeContent(self, filter):
        classes = [{"type_id": tid, "type_name": name} for tid, name in CHANNELS + CATEGORIES]
        return {
            "class": classes,
            "list": [],
            "filters": {},
        }

    def homeVideoContent(self):
        try:
            html, _ = self._fetch("/")
            return {"list": self._parse_cards(html)[:20]}
        except Exception:
            return {"list": []}

    def _category_path(self, tid, pg):
        if tid in dict(CHANNELS):
            return "/ai-manju/" if pg <= 1 else f"/ai-manju/{pg}/"
        return f"/tag/{tid}/" if pg <= 1 else f"/tag/{tid}/page/{pg}/"

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if pg else 1
        try:
            html, _ = self._fetch(self._category_path(tid, pg))
        except Exception:
            return {"page": pg, "pagecount": 1, "limit": 20, "total": 0, "list": []}
        lst = self._parse_cards(html)
        pc = self._pagecount(html)
        if not lst:
            pc = pg  # 空页刹车
        return {"page": pg, "pagecount": pc, "limit": 20, "total": 0, "list": lst}

    def _video_data(self, vid):
        html, _ = self._fetch(f"/video/{vid}/")
        m = re.search(r'<script id="videoInitialData" type="application/json">(.*?)</script>', html, re.S)
        if not m:
            raise ValueError("videoInitialData missing")
        return json.loads(m.group(1))

    def detailContent(self, ids):
        vid = ids[0] if ids else ""
        try:
            o = self._video_data(vid)
        except Exception:
            return {"list": []}
        eps = o.get("epPlaySrcs") or {}
        plays = []
        for k in sorted(eps.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
            u = eps[k]
            if u:
                plays.append(f"第{k}集${u}")
        tags = o.get("tags") or []
        vod = {
            "vod_id": vid,
            "vod_name": (o.get("title") or "").replace("$", "＄").replace("#", "＃"),
            "vod_pic": self._pic(o.get("coverSrc") or o.get("posterSrc") or ""),
            "vod_content": (o.get("description") or "").strip(),
            "vod_tag": ",".join(tags),  # 标签可点搜
            "vod_play_from": "黄果",
            "vod_play_url": "#".join(plays),
        }
        return {"list": [vod]}

    def searchContent(self, key, quick, pg="1"):
        pg = int(pg) if pg else 1
        kw = urllib.parse.quote(key)
        path = f"/search/?keyword={kw}" if pg <= 1 else f"/search/video/{kw}/{pg}/"
        try:
            html, _ = self._fetch(path)
        except Exception:
            return {"list": []}
        return {"list": self._parse_cards(html)}

    def playerContent(self, flag, id, vipFlags):
        url = id or ""
        if url.startswith("http") and (".m3u8" in url or ".mp4" in url):
            return {"parse": 0, "url": url,
                    "header": {"User-Agent": UA, "Referer": self.host + "/"}}
        return {"parse": 0, "url": url}

    def localProxy(self, param=None):
        # 壳回调：解密封面。param 如 {"type":"img","url":"https://..."}
        try:
            p = param or {}
            if p.get("type") == "img" and p.get("url"):
                raw = self._fetch_bytes(p["url"])
                plain = _aes_cbc_decrypt(raw)
                return [200, "image/jpeg", plain]
        except Exception:
            pass
        return [404, "text/plain", b""]
