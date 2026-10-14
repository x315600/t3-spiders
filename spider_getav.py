#!/usr/bin/python
# -*- coding: utf-8 -*-
# GetAV(spider_getav.py) - 日本AV站
# host: 自动从发布页(getav.info)轮询最快域名, 失败自愈切换
# 列表: HTML /zh/genres/{tid}?page={pg}   详情: JSON /api/movies/{id}
# 搜索: /zh/search?q={key}   播放: videoSources[].url 直链
import re, json, time, requests
from urllib.parse import quote
from lxml import etree
from base.spider import Spider

class Spider(Spider):
    def getName(self):
        return "GetAV"
    def init(self, extend=""):
        self.landing = "https://getav.info/zh/"
        self.fallback_hosts = [
            "https://getav.live",
            "https://getav.net",
            "https://getav.co",
            "https://getav.me",
            "https://getav.top",
        ]
        self.host = "https://getav.live"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        }
        try:
            self._auto_heal_host()
        except Exception as e:
            print(f"[{self.getName()}] init自愈跳过: {e}")
        self.headers["Referer"] = self.host + "/"
        self.categories = [
            {"type_id": "latest", "type_name": "最新上传"},
            {"type_id": "hot", "type_name": "热门"},
            {"type_id": "new-releases", "type_name": "新作"},
            {"type_id": "uncensored", "type_name": "无码"},
            {"type_id": "1", "type_name": "巨乳"},
            {"type_id": "148", "type_name": "内射"},
            {"type_id": "4", "type_name": "单人作品"},
            {"type_id": "5", "type_name": "DMM独家"},
            {"type_id": "35", "type_name": "已婚妇女"},
            {"type_id": "187", "type_name": "熟女"},
            {"type_id": "28", "type_name": "口交"},
            {"type_id": "25", "type_name": "剧情"},
            {"type_id": "3", "type_name": "多P"},
            {"type_id": "59", "type_name": "颜射"},
            {"type_id": "16", "type_name": "潮吹"},
            {"type_id": "18", "type_name": "苗条"},
        ]
    def _discover_hosts(self):
        try:
            r = requests.get(self.landing, headers=self.headers, timeout=10)
            r.encoding = r.apparent_encoding or "utf-8"
            found = re.findall(r'"(https?://getav\.[a-z]+/)"', r.text)
            hosts = []
            for h in dict.fromkeys(found):
                h = h.rstrip("/")
                if h not in hosts:
                    hosts.append(h)
            if hosts:
                print(f"[{self.getName()}] 发布页发现 {len(hosts)} 个镜像: {hosts}")
                return hosts
        except Exception as e:
            print(f"[{self.getName()}] 发布页抓取失败: {e}")
        return []
    def _pick_fastest(self, hosts):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def _probe(h):
            try:
                t0 = time.time()
                r = requests.head(h + "/", headers=self.headers, timeout=3, allow_redirects=True)
                dt = time.time() - t0
                if r.status_code < 400:
                    return h, dt
            except Exception:
                pass
            return None
        best, best_time = None, float("inf")
        try:
            with ThreadPoolExecutor(max_workers=min(len(hosts), 5)) as ex:
                futs = {ex.submit(_probe, h): h for h in hosts}
                for f in as_completed(futs, timeout=8):
                    res = f.result()
                    if res and res[1] < best_time:
                        best, best_time = res
        except Exception as e:
            print(f"[{self.getName()}] 域名探测异常: {e}")
        if best:
            print(f"[{self.getName()}] 最快域名 {best} ({best_time:.2f}s)")
        return best
    def _auto_heal_host(self):
        try:
            hosts = self._discover_hosts() or self.fallback_hosts
            best = self._pick_fastest(hosts)
            if best and best != self.host:
                print(f"[{self.getName()}] 域名自愈: {self.host} -> {best}")
                self.host = best
            else:
                print(f"[{self.getName()}] 当前域名可用: {self.host}")
        except Exception as e:
            print(f"[{self.getName()}] 自愈跳过: {e}")
        return self.host
    def _get(self, url, timeout=15):
        try:
            r = requests.get(url, headers=self.headers, timeout=timeout)
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:
            print(f"[ERROR] 请求失败: {url} - {e}")
        print(f"[{self.getName()}] 触发域名自愈...")
        old = self.host
        self._auto_heal_host()
        self.headers["Referer"] = self.host + "/"
        if self.host != old:
            try:
                url2 = url.replace(old, self.host)
                r = requests.get(url2, headers=self.headers, timeout=timeout)
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            except Exception as e:
                print(f"[ERROR] 自愈后重试失败: {url2} - {e}")
        return None
    def _get_json(self, url, timeout=15):
        try:
            r = requests.get(url, headers=self.headers, timeout=timeout)
            return r.json()
        except Exception as e:
            print(f"[ERROR] JSON请求失败: {url} - {e}")
        print(f"[{self.getName()}] 触发域名自愈...")
        old = self.host
        self._auto_heal_host()
        self.headers["Referer"] = self.host + "/"
        if self.host != old:
            try:
                r = requests.get(url.replace(old, self.host), headers=self.headers, timeout=timeout)
                return r.json()
            except Exception as e:
                print(f"[ERROR] 自愈后重试失败: {e}")
        return {}
    def _fix_url(self, u):
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/"):
            return self.host + u
        return u
    def _parse_list(self, html):
        if not html:
            return []
        tree = etree.HTML(html)
        results, seen = [], set()
        items = tree.xpath('//a[contains(@href,"/zh/videos/")]')
        for item in items:
            try:
                href = item.get("href", "")
                m = re.search(r"/zh/videos/([a-zA-Z0-9-]+)", href)
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                img = item.xpath('.//img')
                pic = ""
                if img:
                    pic = self._fix_url(img[0].get("data-original") or img[0].get("data-src") or img[0].get("src", ""))
                title = "".join(item.xpath('.//img/@alt')).strip()
                if not title:
                    title = "".join(item.xpath('.//text()')).strip().split("\n")[0].strip()
                if not title:
                    title = m.group(1)
                results.append({"vod_id": m.group(1), "vod_name": title, "vod_pic": pic})
            except:
                continue
        print(f"[{self.getName()}] 列表匹配到 {len(results)} 个")
        return results
    def _cat_url(self, tid, pg):
        if tid == "latest":
            return f"{self.host}/zh/latest?sort=uploadDate&page={pg}"
        if tid in ("hot", "new-releases", "uncensored"):
            return f"{self.host}/zh/{tid}?page={pg}"
        return f"{self.host}/zh/genres/{tid}?page={pg}"
    def homeContent(self, filter):
        html = self._get(self.host + "/zh/")
        return {"class": self.categories, "list": self._parse_list(html) if html else [], "filters": {}}
    def categoryContent(self, tid, pg, filter, extend):
        html = self._get(self._cat_url(tid, pg))
        items = self._parse_list(html) if html else []
        return {"page": int(pg), "pagecount": 999, "limit": 40, "total": 99999, "list": items}
    def detailContent(self, ids):
        result = {"list": []}
        for vid in ids:
            try:
                data = self._get_json(f"{self.host}/api/movies/{quote(vid)}").get("data", {})
                if not data:
                    continue
                name = data.get("title", "") or vid
                pic = self._fix_url(data.get("localImg", "") or data.get("img", ""))
                sources, episodes = [], []
                vs = data.get("videoSources", []) or []
                by_type = {}
                for v in vs:
                    t = v.get("type", "raw")
                    by_type.setdefault(t, v.get("url", ""))
                order = ["raw_1080p", "raw_720p", "raw_480p", "raw_240p"]
                ep_list = []
                for t in order:
                    if t in by_type and by_type[t]:
                        label = t.replace("raw_", "")
                        ep_list.append(f"{label}${by_type[t]}")
                for t, u in by_type.items():
                    if t not in order and u:
                        ep_list.append(f"{t}${u}")
                if ep_list:
                    sources.append("默认")
                    episodes.append("#".join(ep_list))
                print(f"[{self.getName()}] 详情 {vid} 提取到 {len(sources)} 个播放源")
                result["list"].append({
                    "vod_id": vid,
                    "vod_name": name,
                    "vod_pic": pic,
                    "vod_play_from": "$$$".join(sources),
                    "vod_play_url": "$$$".join(episodes),
                })
            except:
                continue
        return result
    def searchContent(self, key, quick, pg="1"):
        html = self._get(f"{self.host}/zh/search?q={quote(key)}&page={pg}")
        return {"list": self._parse_list(html) if html else [], "page": int(pg)}
    def playerContent(self, flag, id, vipFlags):
        url = self._fix_url(id)
        return {"parse": 0, "url": url, "header": json.dumps(self.headers)}
