# -*- coding: utf-8 -*-
# 黄果漫剧 (q8jw.ltfqnhxx.cc) T3 spider — source-authoring 1.3.0 重写版
# 与黄果短剧草稿同套前端/同封面 AES 密钥，但内容库独立（video id 不互通，2588 在旧站 404），故单独立源
# 站点结构（2026-11-08 取证，见 analyze-v3.json）：
#   真实分类 = 导航栏 5 个频道（标签只是视频上的属性，不是分类）：
#     视频类：ai-manju 原创漫剧、lifan-dongman 里番动漫、duanju 成人短剧
#       走 /api/videos/category/{ch}?sort=latest|hot|random&page={pg}&size=18，
#       响应 data.items + data.pagination{page,pages,size,total}；
#       不带 sort 默认等于 hot；latest 与 HTML latest 面板同序
#     漫画类：riman 成人日漫、hanman 成人韩漫
#       走 /api/comics/category/{ch}?sort=new|hot|end&page={pg}&size=18，
#       无 random，sort=latest 无效；item 字段 id/title/cover/status_line/tags
#   标签筛选：HTML 单一 grid，无排序；/tag/{slug}/ 首页，/tag/{slug}/page/{pg}/ 分页
#     （data-pages 给总页数；tag API 返回空，不可用；标签页只有视频，无漫画）
#   搜索: /search/?keyword={kw}   分页 /search/video/{kw}/{pg}/
#   详情视频: /video/{id}/  内嵌 <script id="videoInitialData" type="application/json">
#         -> epPlaySrcs {集号: m3u8直链}，tags / coverSrc / description 全有
#   详情漫画: /comic/{id}/  纯图片章节，无视频/m3u8；详情只给信息，不给播放链接
#         （漫画 id 在列表里加 "c" 前缀，如 c2919，与视频 id 区分）
#   封面: pic.drleao.cn 的图是 AES-CBC 加密字节（key/iv 与黄果短剧草稿相同），
#         列表/详情的 vod_pic 走 localProxy，TV 端实时解密出图；
#         个别图（如漫画详情 og:image 在 imgpublic.ycomesc.live）本身就是 JPEG，
#         localProxy 检测到 JPEG 魔数直接原样返回，不解密
#   播放: m3u8 直链（带时效 auth_key，详情页打开时现取现用）
"""Buffered JSON GET for native net.req or observed legacy fetch.
No source imports or network on import. Native timeout is ms; fetch is seconds.
A request error never triggers a second transport. Proxy/stream contracts are separate.
The body cap is checked after host buffering, not a transport-level memory bound.
"""
import json
import math

MAX_JSON_BYTES = 2 * 1024 * 1024


class HostTransportError(RuntimeError):
    pass


class ResponseFormatError(ValueError):
    pass


def transport_name(host):
    net = getattr(host, 'net', None)
    if callable(getattr(net, 'req', None)):
        return 'native-net'
    if callable(getattr(host, 'fetch', None)):
        return 'legacy-fetch'
    raise HostTransportError('Host requires callable net.req or observed fetch')


def get_json(host, url, *, headers=None, timeout_ms=10000, max_bytes=MAX_JSON_BYTES):
    """GET one JSON body. Validate business envelopes in the site adapter.

    Requires observed fetch(url, headers=dict, timeout=seconds) -> response with
    status_code/content/close, or native req(url, options) -> code/content/error.
    Redirect, Cookie, TLS and decompression behavior belong to the chosen host.
    Do not invoke before the host has bound its runtime resources.
    """
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, (int, float)):
        raise TypeError('timeout_ms must be a number')
    if not math.isfinite(timeout_ms) or not 0 < timeout_ms <= 60000:
        raise ValueError('timeout_ms outside (0, 60000]')
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise TypeError('max_bytes must be an integer')
    if not 0 < max_bytes <= MAX_JSON_BYTES:
        raise ValueError('max_bytes may only tighten the hard cap')
    if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
        raise ValueError('url must be an absolute HTTP(S) URL')
    headers = {} if headers is None else headers
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                            for k, v in headers.items()):
        raise TypeError('headers must map strings to strings')
    mode = transport_name(host)
    if mode == 'native-net':
        result = host.net.req(url, {'headers': dict(headers), 'timeout': timeout_ms,
                                    'retries': 0, 'buffer': 0})
        if not isinstance(result, dict):
            raise HostTransportError('Native response must be dict')
        if result.get('error') or type(result.get('code')) is not int or result['code'] != 200:
            raise HostTransportError('Native transport returned an error or non-200 status')
        raw = result.get('content')
    else:
        response = host.fetch(url, headers=dict(headers), timeout=timeout_ms / 1000.0)
        try:
            if type(response.status_code) is not int or response.status_code != 200:
                raise HostTransportError('Legacy transport returned non-200 status')
            raw = response.content
        finally:
            response.close()
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > max_bytes:
            raise ResponseFormatError('JSON body exceeds cap')
        try:
            raw = bytes(raw).decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ResponseFormatError('JSON body is not UTF-8') from exc
    elif isinstance(raw, str):
        if len(raw.encode('utf-8')) > max_bytes:
            raise ResponseFormatError('JSON body exceeds cap')
        raw = raw.lstrip('\ufeff')
    else:
        raise ResponseFormatError('JSON content must be text or bytes')
    def reject_constant(value):
        raise ResponseFormatError('Non-finite JSON number: ' + value)
    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise ResponseFormatError('Response is not JSON; inspect status/final URL/body separately') from exc
    if not isinstance(value, (dict, list)):
        raise ResponseFormatError('JSON root must be object or array')
    return value


# Trusted optional component: embedded-json
"""Strict, bounded JSON decoding for an already located script payload.

This module is not an HTML/JavaScript parser or a decryptor. It performs no
HTML entity decoding, code execution, file access, or network access.
"""

import json
import math


_MAX_LAYERS = 8
_MAX_DEPTH = 64


def _utf8_size(text: str) -> int:
    try:
        return len(text.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise ValueError("payload contains invalid UTF-8 (unpaired surrogate)") from exc


def _check_depth(text: str) -> None:
    """Bound container nesting before parsing; delimiters in strings are inert."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > _MAX_DEPTH:
                raise ValueError(f"JSON container depth exceeds {_MAX_DEPTH}")
        elif char in "]}":
            depth -= 1


def _object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number: {token}")


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("JSON number overflows to a non-finite float")
    return value


def _check_strings(value: object) -> None:
    """Check decoded escapes too, including object keys, without recursion."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            _utf8_size(item)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def decode_json_payload(
    text: str, *, max_bytes: int = 1048576, max_layers: int = 3
) -> dict | list:
    """Decode JSON, unwrapping only top-level JSON strings.

    Contract:
    * ``text`` must be a str. Configurations must be exact ints (not bools);
      max_bytes >= 1 and 1 <= max_layers <= 8.
    * Each parsing layer must encode as strict UTF-8 and fit max_bytes,
      inclusive, counting whitespace and escape syntax. No stripping,
      BOM removal, HTML decoding, or Unicode normalization is performed.
    * One layer means one json.loads call, including the final object/array.
      Top-level strings are decoded again; strings inside containers are
      left untouched. Only a final dict or list is accepted.
    * Each layer permits at most 64 nested JSON containers (root counts as
      one). Duplicate decoded object keys, NaN/Infinity, float overflow,
      invalid Unicode surrogates anywhere, and trailing non-whitespace fail.
    * TypeError signals argument type errors. ValueError signals invalid
      configuration values, malformed JSON/Unicode, disallowed values, or
      exhausted byte/layer/depth limits. No partial result is returned.

    Uses only the standard library; never evaluates JavaScript or performs I/O.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if type(max_bytes) is not int:
        raise TypeError("max_bytes must be an int, not bool")
    if type(max_layers) is not int:
        raise TypeError("max_layers must be an int, not bool")
    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")
    if not 1 <= max_layers <= _MAX_LAYERS:
        raise ValueError("max_layers must be between 1 and 8")

    current = text
    for layer in range(1, max_layers + 1):
        if _utf8_size(current) > max_bytes:
            raise ValueError(f"UTF-8 payload exceeds max_bytes at layer {layer}")
        _check_depth(current)
        try:
            value = json.loads(
                current,
                object_pairs_hook=_object,
                parse_constant=_reject_constant,
                parse_float=_finite_float,
            )
        except RecursionError as exc:
            raise ValueError("JSON depth exceeds parser capacity") from exc
        except ValueError as exc:
            raise ValueError(f"invalid JSON at layer {layer}: {exc}") from exc
        _check_strings(value)
        if isinstance(value, (dict, list)):
            return value
        if not isinstance(value, str):
            raise ValueError("final JSON value must be an object or array")
        current = value
    raise ValueError(f"JSON decoding exceeds max_layers={max_layers}")


# Trusted optional component: site-mapping
"""Python 3.10+ standard-library mappings; no I/O or protocol inference.

Type mismatches raise TypeError; malformed/duplicate/unknown data raises
ValueError. Required labels/identifiers must be nonblank; accepted strings
are preserved verbatim. Adapters own all protocol-specific transformations.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

__all__ = ["FilterSpec", "filter_ui", "filter_params", "pair_lines"]


def _text(value, field, *, blank=False):
    if not isinstance(value, str):
        raise TypeError(f"{field} must be str")
    if not blank and not value.strip():
        raise ValueError(f"{field} must be nonblank")


def _pair(value, field):
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be tuple")
    if len(value) != 2:
        raise ValueError(f"{field} must contain exactly two items")


@dataclass(frozen=True)
class FilterSpec:
    """Immutable declaration. Empty values is valid; display labels are nonblank.

    Option values are literal strings (including ''). Duplicate values within
    one declaration are invalid; duplicate displays across options are valid.
    """

    key: str
    name: str
    param: str
    values: tuple[tuple[str, str], ...]

    def __post_init__(self):
        for field in ("key", "name", "param"):
            _text(getattr(self, field), field)
        if not isinstance(self.values, tuple):
            raise TypeError("values must be tuple")
        seen = set()
        for option in self.values:
            _pair(option, "option")
            display, value = option
            _text(display, "option display")
            _text(value, "option value", blank=True)
            if value in seen:
                raise ValueError(f"duplicate option value: {value!r}")
            seen.add(value)


def _specs(specs):
    if isinstance(specs, (str, bytes, bytearray, Mapping)):
        raise TypeError("specs must be an iterable of FilterSpec")
    specs = tuple(specs)
    keys, params = set(), set()
    for spec in specs:
        if not isinstance(spec, FilterSpec):
            raise TypeError("specs items must be FilterSpec")
        if spec.key in keys:
            raise ValueError(f"duplicate filter key: {spec.key!r}")
        if spec.param in params:
            raise ValueError(f"duplicate filter param: {spec.param!r}")
        keys.add(spec.key)
        params.add(spec.param)
    return specs


def filter_ui(specs: Iterable[FilterSpec]) -> list[dict]:
    """Return fresh host UI data in declaration/option order; empty specs -> []."""
    return [
        {"key": s.key, "name": s.name,
         "value": [{"n": n, "v": v} for n, v in s.values]}
        for s in _specs(specs)
    ]


def filter_params(specs: Iterable[FilterSpec],
                  selected: dict[str, str]) -> dict[str, str]:
    """Return only selected, declared literal values under their real params.

    For a known key, '' always clears (omits) the param, even when no empty
    option is declared. Missing selections add no defaults. Unknown keys,
    including ones selected as '', and unknown nonempty values raise
    ValueError. Empty specs/selection -> {}; inputs are never modified.
    """
    declared = {s.key: s for s in _specs(specs)}
    if not isinstance(selected, dict):
        raise TypeError("selected must be dict[str, str]")
    result = {}
    for key, value in selected.items():
        _text(key, "selected key", blank=True)
        _text(value, "selected value", blank=True)
        if key not in declared:
            raise ValueError(f"unknown filter key: {key!r}")
        spec = declared[key]
        if value == "":
            continue
        if value not in {v for _, v in spec.values}:
            raise ValueError(f"unknown value for {key!r}: {value!r}")
        result[spec.param] = value
    return result


def pair_lines(names: list[str],
               panels: list[list[tuple[str, str]]]) -> list[dict]:
    """Align counts BEFORE inspecting items; never prefilter names.

    Empty inputs -> []. Empty panels skip the whole aligned pair (their name
    must still be str, but may be blank). Nonempty panels require nonblank
    names, titles and IDs. Preserve order, duplicates and literal strings;
    return fresh episode lists of (title, id) tuples, never playback strings.
    """
    if not isinstance(names, list) or not isinstance(panels, list):
        raise TypeError("names and panels must be lists")
    if len(names) != len(panels):
        raise ValueError("names/panels count mismatch")
    result = []
    for name, panel in zip(names, panels):
        _text(name, "line name", blank=True)
        if not isinstance(panel, list):
            raise TypeError("panel must be list")
        if not panel:
            continue
        _text(name, "line name")
        episodes = []
        for episode in panel:
            _pair(episode, "episode")
            title, episode_id = episode
            _text(title, "episode title")
            _text(episode_id, "episode id")
            episodes.append((title, episode_id))
        result.append({"name": name, "episodes": episodes})
    return result


try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider:  # 桌面自测桩
        def getDependence(self): return []
        def init(self, extend=""): pass
        def destroy(self): pass

import re
import urllib.parse
import urllib.request
import urllib.error

UA = ("Mozilla/5.0 (Linux; Android 13; moto g100) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")

HOSTS = ["https://q8jw.ltfqnhxx.cc"]

# 封面 AES-CBC 解密参数（与黄果短剧站同源，2026-11-08 实测本站封面可解）
IMG_KEY = b"f5d965df75336270"
IMG_IV = b"97b60394abc2fbe1"

# 禁词过滤：命中标题或标签的条目直接丢弃（列表层 + 详情层）。
# 词以 unicode 转义存放，避免字面出现在文件里；运行时解码后比对。
# 背景：2026-11-08 取证发现漫画侧 API 有条目标签含禁词（如 riman hot p2、
# hanman new p2），视频侧 p1/p2 干净；过滤器保证盒子上永远不出现这类条目。
_BANNED = (
    "\u5e7c\u5973", "\u5c0f\u5b66\u751f", "\u521d\u4e2d\u751f",
    "\u841d\u8389", "\u5e7c\u9f7f", "\u672a\u6210\u5e74",
)


def _banned(text):
    s = str(text or "")
    return any(w in s for w in _BANNED)


# 真实频道：导航栏 5 个（2026-11-08 取证，见 analyze-v3.json）。
# 标签只是视频上的属性，不是分类，旧版把 18 个标签冒充分类是错的。
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

# 标签筛选：站内真实存在的 slug，按 5 个频道页出现频次取前 24（中性 + 成人向混合）
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


class Spider(BaseSpider):
    PROFILE = 'fongmi-sync-20261107'
    KIND = 'mixed'

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
        self.host = HOSTS[0]
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
        self._ready = True

    def _require_init(self):
        if not getattr(self, '_ready', False):
            raise RuntimeError('init must complete before business calls')

    def _unbound(self, stage):
        raise NotImplementedError(self.KIND + ': bind ' + stage + ' using local redacted evidence')

    # ----- 网络（已验证方式：urllib + UA/Referer；无 net.req 的壳同样工作） -----
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

    def _fetch_json(self, url, timeout=20):
        # 站内 JSON API：/api/videos|comics/category/{ch}?sort=&page=&size=
        req = urllib.request.Request(url if url.startswith("http") else self.host + url,
                                     headers={"User-Agent": UA, "Referer": self.host + "/",
                                              "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
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
        try:
            if isinstance(extend, dict):
                return extend
            if isinstance(extend, str) and extend.strip().startswith("{"):
                parsed = json.loads(extend)
                return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        return {}

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
        return f"/tag/{tid}/" if pg <= 1 else f"/tag/{tid}/page/{pg}/"

    def _video_dict(self, html):
        m = re.search(r'<script id="videoInitialData" type="application/json">(.*?)</script>', html, re.S)
        if not m:
            raise ValueError("videoInitialData missing")
        try:
            return decode_json_payload(m.group(1))
        except ValueError:
            return json.loads(m.group(1))

    def _detail_vod(self, vid, o):
        tags = o.get("tags") or []
        if _banned(o.get("title")) or _banned(",".join(tags)):
            raise ValueError("banned content")
        eps = o.get("epPlaySrcs") or {}
        keys = sorted(eps.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)
        panel = [(f"第{k}集", eps[k]) for k in keys if eps[k]]
        paired = pair_lines(["黄果"], [panel]) if panel else []
        plays = []
        if paired:
            plays = [f"{t}${u}" for t, u in paired[0]["episodes"]]
        tags = o.get("tags") or []
        return {
            "vod_id": vid,
            "vod_name": (o.get("title") or "").replace("$", "＄").replace("#", "＃"),
            "vod_pic": self._pic(o.get("coverSrc") or o.get("posterSrc") or ""),
            "vod_content": (o.get("description") or "").strip(),
            "vod_tag": ",".join(tags),
            "vod_play_from": "黄果",
            "vod_play_url": "#".join(plays),
        }

    # ----- mixed 管线：步骤 = (kind, payload) -----
    def _mixed_plan(self, operation, arguments):
        if operation == 'home':
            return [('static-home', None)]
        if operation == 'homeVideo':
            return [('fetch-list', '/')]
        if operation == 'category':
            pg = int(arguments['pg']) if arguments['pg'] else 1
            tid = str(arguments['tid'])
            ext = self._parse_extend(arguments.get('extend'))
            tag = (ext.get('tag') or '').strip()
            if tag:
                # 选了标签：标签页是 HTML、无排序，排序选择此时忽略
                return [('fetch-list', self._category_path(tag, pg))]
            kind = CHANNEL_KIND.get(tid, 'videos')
            sort = (ext.get('sort') or '').strip() or SORT_DEFAULT[kind]
            if sort not in {v for _n, v in SORTS[kind]}:
                sort = SORT_DEFAULT[kind]
            api = 'videos' if kind == 'videos' else 'comics'
            url = f"/api/{api}/category/{tid}?sort={sort}&page={pg}&size={API_SIZE}"
            return [('fetch-api', url)]
        if operation == 'search':
            pg = int(arguments['pg']) if arguments['pg'] else 1
            kw = urllib.parse.quote(arguments['key'])
            path = f"/search/?keyword={kw}" if pg <= 1 else f"/search/video/{kw}/{pg}/"
            return [('fetch-list', path)]
        if operation == 'detail':
            vid = str(arguments['id'])
            if vid.startswith('c') and vid[1:].isdigit():
                return [('fetch-comic-detail', f"/comic/{vid[1:]}/")]
            return [('fetch-detail', f"/video/{vid}/")]
        if operation == 'player':
            return [('static-player', None)]
        self._unbound('mixed step order, depends_on and ID handoff')

    def _fetch_mixed_step(self, step, arguments, previous):
        kind, payload = step
        if kind in ('fetch-list', 'fetch-detail', 'fetch-comic-detail'):
            return self._fetch(payload)[0]
        if kind == 'fetch-api':
            return self._fetch_json(payload)
        if kind in ('static-home', 'static-player'):
            return None
        self._unbound('mixed per-step endpoint/method and separate header scope')

    def _parse_mixed_step(self, step, response, arguments, previous):
        kind, payload = step
        if kind == 'static-home':
            return {'classes': [{"type_id": tid, "type_name": name} for tid, name, _k in CHANNELS]}
        if kind == 'fetch-list':
            return {'cards': self._parse_cards(response), 'pagecount': self._pagecount(response)}
        if kind == 'fetch-api':
            kind = CHANNEL_KIND.get(str(arguments.get('tid')), 'videos')
            pages = (response.get('pagination') or {}).get('pages') or 1
            return {'cards': self._api_cards(response, kind), 'pagecount': int(pages)}
        if kind == 'fetch-detail':
            return {'detail': self._video_dict(response)}
        if kind == 'fetch-comic-detail':
            return {'detail': self._comic_dict(response), 'comic': True}
        if kind == 'static-player':
            return {'url': arguments['id']}
        self._unbound('mixed per-step HTML selectors/API paths and dependency IDs')

    def _merge_mixed_results(self, operation, completed, arguments):
        parsed = dict(completed[0][1]) if completed else {}
        if operation == 'home':
            return {'classes': parsed.get('classes', [])}
        if operation == 'homeVideo':
            return {'cards': parsed.get('cards', [])}
        if operation == 'category':
            pg = int(arguments['pg']) if arguments['pg'] else 1
            cards = parsed.get('cards', [])
            pc = parsed.get('pagecount', 1)
            if not cards:
                pc = pg  # 空页刹车
            return {'page': pg, 'pagecount': pc, 'cards': cards}
        if operation == 'search':
            return {'cards': parsed.get('cards', [])}
        if operation == 'detail':
            if parsed.get('comic'):
                return {'vod': self._comic_vod(arguments['id'], parsed.get('detail', {}))}
            return {'vod': self._detail_vod(arguments['id'], parsed.get('detail', {}))}
        if operation == 'player':
            return {'url': parsed.get('url', '')}
        self._unbound('mixed merge ownership, conflicts and pagination owner')

    def _host_dict(self, operation, parsed, arguments):
        # detail 返回 ONE vod 行，detailContent 负责包外层 list
        if operation == 'home':
            filters = {}
            for tid, _name, kind in CHANNELS:
                filters[tid] = filter_ui([
                    FilterSpec(key='sort', name='排序', param='sort', values=SORTS[kind]),
                    FilterSpec(key='tag', name='标签', param='tag', values=tuple(TAG_FILTERS)),
                ])
            return {"class": parsed.get('classes', []), "list": [], "filters": filters}
        if operation == 'homeVideo':
            return {"list": parsed.get('cards', [])[:20]}
        if operation == 'category':
            return {"page": parsed.get('page', 1), "pagecount": parsed.get('pagecount', 1),
                    "limit": 20, "total": 0, "list": parsed.get('cards', [])}
        if operation == 'search':
            return {"list": parsed.get('cards', [])}
        if operation == 'detail':
            return parsed.get('vod') or {}
        if operation == 'player':
            url = parsed.get('url', '')
            if url.startswith("http") and (".m3u8" in url or ".mp4" in url):
                return {"parse": 0, "url": url, "header": {"User-Agent": UA, "Referer": self.host + "/"}}
            return {"parse": 0, "url": url}
        self._unbound('host schema and ID/pagination/player mapping for ' + operation)

    def _site_data(self, operation, arguments):
        steps = self._mixed_plan(operation, arguments)
        if not isinstance(steps, list) or not steps:
            raise ValueError('mixed plan must be a nonempty ordered list')
        completed = []
        for step in steps:
            previous = tuple(completed)
            response = self._fetch_mixed_step(step, arguments, previous)
            parsed = self._parse_mixed_step(step, response, arguments, previous)
            completed.append((step, parsed))
        return self._merge_mixed_results(operation, completed, arguments)

    def _run(self, operation, arguments):
        self._require_init()
        parsed = self._site_data(operation, arguments)
        result = self._host_dict(operation, parsed, arguments)
        if type(result) is not dict:
            raise TypeError('host adapter must return a native dict')
        return result

    def homeContent(self, filter):
        return self._run('home', {'filter': filter})

    def homeVideoContent(self):
        try:
            return self._run('homeVideo', {})
        except Exception:
            return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            return self._run('category', {'tid': tid, 'pg': pg, 'filter': filter, 'extend': extend})
        except Exception as e:
            # 失败必须看得见：旧写法静默返回空列表 + pagecount=1，盒子上
            # 超时和解析错都长得跟“没数据、没下一页”一样，门禁也抓不到。
            # 现在返回一条报错项，点开无害（detail 对 __error__ 返回空）。
            pg = int(pg) if pg else 1
            return {"page": pg, "pagecount": 1, "limit": 20, "total": 0, "list": [{
                "vod_id": "__error__",
                "vod_name": "加载失败：" + type(e).__name__ + " " + str(e)[:80],
                "vod_pic": "",
                "vod_remarks": "分类 " + str(tid) + " 第 " + str(pg) + " 页",
            }]}

    def searchContent(self, key, quick, pg="1"):
        try:
            return self._run('search', {'key': key, 'quick': quick, 'pg': pg})
        except Exception:
            return {"list": []}

    def detailContent(self, ids):
        self._require_init()
        if not isinstance(ids, list):
            raise TypeError('ids must be a list of strings')
        if any(not isinstance(vid, str) for vid in ids):
            raise TypeError('each detail id must be a string')
        result = []
        for vid in ids:
            try:
                result.append(self._run('detail', {'id': vid}))
            except Exception:
                continue
        return {'list': result}

    def playerContent(self, flag, id, vipFlags):
        return self._run('player', {'flag': flag, 'id': id, 'vipFlags': vipFlags})

    def action(self, action):
        self._require_init()
        self._unbound('action consumer and dispatch')

    def _proxy_img(self, param):
        try:
            p = param or {}
            if isinstance(p, dict) and p.get("type") == "img" and p.get("url"):
                raw = self._fetch_bytes(p["url"])
                if raw[:3] == b"\xff\xd8\xff":
                    # 本身就是 JPEG（如漫画详情 og:image 在 imgpublic 域），原样返回不解密
                    return [200, "image/jpeg", raw]
                plain = _aes_cbc_decrypt(raw)
                return [200, "image/jpeg", plain]
        except Exception:
            pass
        return [404, "text/plain", b""]

    def proxy(self, params):
        self._require_init()
        return self._proxy_img(params)

    def localProxy(self, param=None):
        return self._proxy_img(param)

    def destroy(self):
        self._ready = False
        self.config = {}
