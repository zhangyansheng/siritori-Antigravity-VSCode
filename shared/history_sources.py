"""
shared/history_sources.py - 歴史データソース連携 (Game Rule v6.1 / 8章・付録A.5)
Wikipedia 日本語版 API ＋ 代替ソース（Wikidata、JapanSearch等）
"""

import os
import sys
import re
import time
import random
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
import requests

# vendor ディレクトリのロード
VENDOR_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vendor"))
if os.path.exists(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

from .schema import SUFFIXES, SUFFIX_CATEGORIES, VALID_CATEGORIES
from .reading import split_event_name, determine_identifier_reading, to_hiragana, verify_prefix_match

# パラメータ (11章)
WIKI_HTTP_TIMEOUT_SEC = 5
CANDIDATE_CHECK_TIMEOUT_SEC = 10
SOURCE_RETRY = 2
SOURCE_RETRY_INTERVAL_SEC = 1.0
WIKI_SLEEP_RANGE = (0.15, 0.5)
USER_AGENT = "SiritoriAgentSample/6.1 (contact: admin@local.test)"

@dataclass
class SourceRecord:
    source_id: str
    title: str
    url: str
    reading: Optional[str] = None
    types: List[str] = field(default_factory=list)
    extract: str = ""
    japan_related: bool = False
    is_disambiguation: bool = False

# 日本史関連キーワード (J4)
JAPAN_KEYWORDS = [
    "日本", "和名", "幕府", "朝廷", "天皇", "将軍", "大名", "武士", "藩", "元号",
    "旧国名", "国名", "平安", "鎌倉", "室町", "戦国", "安土桃山", "江戸", "明治", "大正", "昭和",
    "令制国", "畿内", "東海道", "東山道", "北陸道", "山陰道", "山陽道", "南海道", "西海道",
    "大和", "山城", "河内", "和泉", "摂津", "尾張", "三河", "遠江", "駿河", "甲斐", "相模", "武蔵",
    "信濃", "越後", "美濃", "近江", "播磨", "長門", "薩摩", "陸奥", "出羽"
]

# 元号および年代キーワード (J2)
ERA_KEYWORDS = [
    "年", "世紀", "大化", "天平", "延喜", "保元", "平治", "治承", "寿永", "建久", "承久",
    "文永", "弘安", "建武", "応仁", "文明", "明応", "永正", "天文", "永禄", "元亀", "天正",
    "文禄", "慶長", "元和", "寛永", "正徳", "享保", "宝暦", "天明", "寛政", "享和", "文化",
    "文政", "天保", "弘化", "嘉永", "安政", "万延", "文久", "元治", "慶応", "明治", "大正", "昭和", "平成", "令和"
]

# 歴史・事件カテゴリキーワード (J1)
HISTORICAL_CATEGORY_KEYWORDS = [
    "歴史", "日本史", "合戦", "戦い", "戦争", "事件", "変", "乱", "事変", "一揆",
    "条約", "人物", "武将", "大名", "政治家", "幕府", "朝廷", "藩", "合意", "紛争", "時代"
]

class BaseHistorySource:
    source_id: str = "base"

    def search(self, query: str, limit: int = 50) -> List[SourceRecord]:
        raise NotImplementedError

    def fetch(self, title: str) -> Optional[SourceRecord]:
        raise NotImplementedError

    def ping(self) -> bool:
        raise NotImplementedError

class WikipediaJaSource(BaseHistorySource):
    source_id = "wikipedia_ja"

    def __init__(self):
        self.api_url = "https://ja.wikipedia.org/w/api.php"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _sleep_rate_limit(self):
        time.sleep(random.uniform(*WIKI_SLEEP_RANGE))

    def ping(self) -> bool:
        try:
            params = {
                "action": "query",
                "meta": "siteinfo",
                "format": "json"
            }
            res = self.session.get(self.api_url, params=params, timeout=WIKI_HTTP_TIMEOUT_SEC)
            return res.status_code == 200 and "query" in res.json()
        except Exception:
            return False

    def search(self, query: str, limit: int = 50) -> List[SourceRecord]:
        results = []
        try:
            # 8.2節 最大3ページ（sroffset = 0, 50, 100）
            for offset in [0, 50, 100]:
                self._sleep_rate_limit()
                params = {
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": min(limit, 50),
                    "sroffset": offset,
                    "format": "json"
                }
                res = self.session.get(self.api_url, params=params, timeout=WIKI_HTTP_TIMEOUT_SEC)
                if res.status_code != 200:
                    break
                data = res.json()
                items = data.get("query", {}).get("search", [])
                if not items:
                    break
                for it in items:
                    t = it.get("title", "")
                    # 特別ページやファイル等は除外
                    if ":" in t:
                        continue
                    results.append(SourceRecord(
                        source_id=self.source_id,
                        title=t,
                        url=f"https://ja.wikipedia.org/wiki/{t}",
                        extract=it.get("snippet", "")
                    ))
                if len(results) >= limit or len(items) < 50:
                    break
        except Exception as e:
            # エラー発生時は呼び出し元でフォールバック
            raise e
        return results

    def fetch(self, title: str) -> Optional[SourceRecord]:
        records = self.fetch_many([title])
        return records.get(title)

    def fetch_many(self, titles: List[str]) -> Dict[str, SourceRecord]:
        """Wikipedia API で複数タイトルを一括取得 (最大50件)"""
        if not titles:
            return {}
        self._sleep_rate_limit()
        results: Dict[str, SourceRecord] = {}
        try:
            # パイプ区切りで連結（最大50件）
            batch = titles[:50]
            titles_param = "|".join(batch)
            params = {
                "action": "query",
                "prop": "extracts|categories",
                "redirects": "1",
                "titles": titles_param,
                "explaintext": "1",
                "exintro": "1",
                "cllimit": "500",
                "format": "json"
            }
            res = self.session.get(self.api_url, params=params, timeout=WIKI_HTTP_TIMEOUT_SEC)
            if res.status_code != 200:
                return {}
            data = res.json()
            pages = data.get("query", {}).get("pages", {})
            redirect_map = {}
            for red in data.get("query", {}).get("redirects", []):
                redirect_map[red.get("to")] = red.get("from")

            for pid, page in pages.items():
                if pid == "-1":
                    continue
                resolved_title = page.get("title", "")
                orig_title = redirect_map.get(resolved_title, resolved_title)
                extract = page.get("extract", "")
                cats = [c.get("title", "") for c in page.get("categories", [])]

                is_disambig = any("曖昧さ回避" in c for c in cats)
                reading = self._extract_reading_from_text(extract)
                japan_related = any(k in extract for k in JAPAN_KEYWORDS) or any(k in c for c in cats for k in JAPAN_KEYWORDS)

                rec = SourceRecord(
                    source_id=self.source_id,
                    title=resolved_title,
                    url=f"https://ja.wikipedia.org/wiki/{resolved_title}",
                    reading=reading,
                    types=cats,
                    extract=extract,
                    japan_related=japan_related,
                    is_disambiguation=is_disambig
                )
                results[resolved_title] = rec
                if orig_title != resolved_title:
                    results[orig_title] = rec
        except Exception as e:
            raise e
        return results

    def _extract_reading_from_text(self, text: str) -> Optional[str]:
        """抽出文の冒頭括弧内からひらがな読みを取得"""
        if not text:
            return None
        # 最初の100文字を対象
        head = text[:200]
        # 全角括弧（...）または半角括弧 (...)
        m = re.search(r"[（\(]([^）\)]+)[）\)]", head)
        if m:
            content = m.group(1)
            # 括弧内にひらがなが含まれているか
            hira_match = re.search(r"[\u3040-\u309F]{2,}", content)
            if hira_match:
                return hira_match.group(0)
        return None

class WikidataSource(BaseHistorySource):
    source_id = "wikidata"

    def __init__(self):
        self.api_url = "https://www.wikidata.org/w/api.php"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def ping(self) -> bool:
        try:
            params = {"action": "query", "meta": "siteinfo", "format": "json"}
            res = self.session.get(self.api_url, params=params, timeout=WIKI_HTTP_TIMEOUT_SEC)
            return res.status_code == 200
        except Exception:
            return False

    def search(self, query: str, limit: int = 50) -> List[SourceRecord]:
        results = []
        try:
            params = {
                "action": "wbsearchentities",
                "search": query,
                "language": "ja",
                "limit": min(limit, 50),
                "format": "json"
            }
            res = self.session.get(self.api_url, params=params, timeout=WIKI_HTTP_TIMEOUT_SEC)
            if res.status_code == 200:
                data = res.json()
                for it in data.get("search", []):
                    title = it.get("label", "")
                    desc = it.get("description", "")
                    japan_related = any(k in desc for k in JAPAN_KEYWORDS)
                    results.append(SourceRecord(
                        source_id=self.source_id,
                        title=title,
                        url=it.get("url", f"https://www.wikidata.org/wiki/{it.get('id')}"),
                        extract=desc,
                        japan_related=japan_related
                    ))
        except Exception as e:
            raise e
        return results

    def fetch(self, title: str) -> Optional[SourceRecord]:
        try:
            # まずラベル検索で ID を取得
            records = self.search(title, limit=1)
            if records and records[0].title == title:
                rec = records[0]
                rec.japan_related = any(k in rec.extract for k in JAPAN_KEYWORDS) or True
                return rec
        except Exception as e:
            raise e
        return None

class JapanSearchSource(BaseHistorySource):
    source_id = "japansearch"

    def __init__(self):
        self.api_url = "https://jpsearch.go.jp/api/item/search"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def ping(self) -> bool:
        try:
            res = self.session.get(f"{self.api_url}?keyword=日本", timeout=WIKI_HTTP_TIMEOUT_SEC)
            return res.status_code == 200
        except Exception:
            return False

    def search(self, query: str, limit: int = 50) -> List[SourceRecord]:
        results = []
        try:
            params = {"keyword": query, "size": min(limit, 50)}
            res = self.session.get(self.api_url, params=params, timeout=WIKI_HTTP_TIMEOUT_SEC)
            if res.status_code == 200:
                data = res.json()
                items = data.get("list", [])
                for it in items:
                    t = it.get("title", "")
                    results.append(SourceRecord(
                        source_id=self.source_id,
                        title=t,
                        url=it.get("linkurl", ""),
                        extract=it.get("description", ""),
                        japan_related=True
                    ))
        except Exception as e:
            raise e
        return results

    def fetch(self, title: str) -> Optional[SourceRecord]:
        records = self.search(title, limit=5)
        for r in records:
            if r.title == title:
                return r
        return None

# オフライン／バックアップ用マスターデータ（ネットワーク完全遮断環境でもテスト・動作を保証）
FALLBACK_HISTORY_DATA = [
    {"title": "姉川の戦い", "reading": "あねがわのたたかい", "category": "戦い名", "extract": "元亀元年の合戦。"},
    {"title": "安政の大獄", "reading": "あんせいのたいごく", "category": "事件名", "extract": "井伊直弼による弾圧事件。"},
    {"title": "青野原の戦い", "reading": "あおものはらのたたかい", "category": "戦い名", "extract": "南北朝時代の美濃国の合戦。"},
    {"title": "足利義満", "reading": "あしかがよしみつ", "category": "人物名", "extract": "室町幕府第3代将軍。"},
    {"title": "新井白石", "reading": "あらいはくせき", "category": "人物名", "extract": "正徳の治を行った儒学者。"},
    {"title": "足利尊氏", "reading": "あしかがたかうじ", "category": "人物名", "extract": "室町幕府初代将軍。"},
    {"title": "石山合戦", "reading": "いしやまかっせん", "category": "合戦名", "extract": "織田信長と本願寺の合戦。"},
    {"title": "伊勢暴動", "reading": "いせぼうどう", "category": "事件名", "extract": "地租改正反対一揆。"},
    {"title": "岩倉使節団", "reading": "いわくらしせつだん", "category": "組織名", "extract": "明治期の欧米使節団。"},
    {"title": "井伊直弼", "reading": "いいなおすけ", "category": "人物名", "extract": "江戸幕府大老。"},
    {"title": "伊藤博文", "reading": "いとうひろぶみ", "category": "人物名", "extract": "初代内閣総理大臣。"},
    {"title": "一向一揆", "reading": "いっこういっき", "category": "一揆", "extract": "浄土真宗の武装蜂起。"},
    {"title": "上田合戦", "reading": "うえだかっせん", "category": "合戦名", "extract": "真田氏と徳川氏の戦い。"},
    {"title": "宇治川の戦い", "reading": "うじがわのたたかい", "category": "戦い名", "extract": "源平合戦の戦い。"},
    {"title": "上杉謙信", "reading": "うえすぎけんしん", "category": "人物名", "extract": "越後の戦国大名。"},
    {"title": "浦賀来航", "reading": "うらがらいこう", "category": "事件名", "extract": "ペリーの黒船来航事件。"},
    {"title": "宇都宮城の戦い", "reading": "うつのみやじょうのたたかい", "category": "戦い名", "extract": "戊辰戦争の合戦。"},
    {"title": "江戸幕府", "reading": "えどばくふ", "category": "組織名", "extract": "徳川氏の武家政権。"},
    {"title": "延暦寺焼き討ち", "reading": "えんりゃくじやきうち", "category": "事件名", "extract": "織田信長による焼き討ち。"},
    {"title": "榎本武揚", "reading": "えのもとたけあき", "category": "人物名", "extract": "五稜郭の戦い指揮官。"},
    {"title": "恵美押勝の乱", "reading": "えみのおしかつのらん", "category": "乱", "extract": "奈良時代の内乱。"},
    {"title": "永享の乱", "reading": "えいきょうのらん", "category": "乱", "extract": "室町時代の関東内乱。"},
    {"title": "桶狭間の戦い", "reading": "おけはざまのたたかい", "category": "戦い名", "extract": "織田信長と今川義元の戦い。"},
    {"title": "応仁の乱", "reading": "おうにんのらん", "category": "乱", "extract": "室町時代の京都大乱。"},
    {"title": "大津事件", "reading": "おおつじけん", "category": "事件名", "extract": "ロシア皇太子暗殺未遂。"},
    {"title": "織田信長", "reading": "おだのぶなが", "category": "人物名", "extract": "天下布武の戦国武将。"},
    {"title": "大塩平八郎の乱", "reading": "おおしおへいはちろうのらん", "category": "乱", "extract": "天保年間の武装蜂起。"},
    {"title": "川中島の戦い", "reading": "かわなかじまのたたかい", "category": "戦い名", "extract": "武田信玄と上杉謙信の合戦。"},
    {"title": "鎌倉時代", "reading": "かまくらじだい", "category": "時代名", "extract": "源頼朝が始めた幕府の時代。"},
    {"title": "加賀一向一揆", "reading": "かがいっこういっき", "category": "一揆", "extract": "加賀国の一揆。"},
    {"title": "勝海舟", "reading": "かつかいしゅう", "category": "人物名", "extract": "江戸城無血開城の立役者。"},
    {"title": "甲州征伐", "reading": "こうしゅうせいばつ", "category": "戦い名", "extract": "武田氏滅亡の戦い。"},
    {"title": "清洲会議", "reading": "きよすかいぎ", "category": "事件名", "extract": "織田家後継会議。"},
    {"title": "北畠顕家", "reading": "きたばたけあきいえ", "category": "人物名", "extract": "南北朝の南朝武将。"},
    {"title": "享保の改革", "reading": "きょうほうのかいかく", "category": "事件名", "extract": "徳川吉宗の幕政改革。"},
    {"title": "金門の変", "reading": "きんもんのへん", "category": "事件名", "extract": "禁門の変。蛤御門の戦い。"},
    {"title": "木曽川の戦い", "reading": "きそがわのたたかい", "category": "戦い名", "extract": "濃尾国境の合戦。"},
    {"title": "黒船来航", "reading": "くろふねらいこう", "category": "事件名", "extract": "ペリー来航による開国。"},
    {"title": "黒田官兵衛", "reading": "くろだかんべえ", "category": "人物名", "extract": "豊臣秀吉の軍師。"},
    {"title": "楠木正成", "reading": "くすのきまさしげ", "category": "人物名", "extract": "後醍醐天皇に忠義を尽くした武将。"},
    {"title": "久坂玄瑞", "reading": "くさかげんずい", "category": "人物名", "extract": "松下村塾の長州藩志士。"},
    {"title": "熊野水軍", "reading": "くまのすいぐん", "category": "組織名", "extract": "紀伊半島の水軍勢力。"},
    {"title": "建武の新政", "reading": "けんむのしんせい", "category": "事件名", "extract": "後醍醐天皇による親政。"},
    {"title": "慶安の変", "reading": "けいあんのへん", "category": "事件名", "extract": "由井正雪の反乱事件。"},
    {"title": "元禄文化", "reading": "げんろくぶんか", "category": "時代名", "extract": "江戸中期の町人文化。"},
    {"title": "兼好法師", "reading": "けんこうほうし", "category": "人物名", "extract": "徒然草を著した吉田兼好。"},
    {"title": "慶応改革", "reading": "けいおうかいかく", "category": "事件名", "extract": "幕末の幕政改革。"},
    {"title": "小牧・長久手の戦い", "reading": "こまきながくてのたたかい", "category": "戦い名", "extract": "秀吉と家康の戦い。"},
    {"title": "衣川の戦い", "reading": "ころもがわのたたかい", "category": "戦い名", "extract": "源義経討死の合戦。"},
    {"title": "公武合体", "reading": "こうぶがったい", "category": "事件名", "extract": "朝廷と幕府の融和策。"},
    {"title": "小早川隆景", "reading": "こばやかわたかかげ", "category": "人物名", "extract": "毛利一族の戦国大名。"},
    {"title": "小西行長", "reading": "こにしゆきなが", "category": "人物名", "extract": "キリシタン戦国武将。"},
    {"title": "坂本龍馬", "reading": "さかもとりょうま", "category": "人物名", "extract": "薩長同盟周旋の志士。"},
    {"title": "桜田門外の変", "reading": "さくらだもんがいのへん", "category": "事件名", "extract": "井伊直弼暗殺事件。"},
    {"title": "薩長同盟", "reading": "さっちょうどうめい", "category": "条約名", "extract": "薩摩藩と長州藩の提携。"},
    {"title": "真田幸村", "reading": "さなだゆきむら", "category": "人物名", "extract": "大坂夏の陣の英雄。"},
    {"title": "三方ヶ原の戦い", "reading": "みかたがはらのたたかい", "category": "戦い名", "extract": "三方ヶ原合戦。"},
    {"title": "島原の乱", "reading": "しまばらのらん", "category": "乱", "extract": "天草四郎率いる一揆。"},
    {"title": "下関条約", "reading": "しものせきじょうやく", "category": "条約名", "extract": "日清戦争講和条約。"},
    {"title": "聖徳太子", "reading": "しょうとくたいし", "category": "人物名", "extract": "十七条憲法の制定者。"},
    {"title": "承久の乱", "reading": "じょうきゅうのらん", "category": "乱", "extract": "後鳥羽上皇の兵乱。"},
    {"title": "白河上皇", "reading": "しらかわじょうこう", "category": "人物名", "extract": "院政開始の天皇。"},
    {"title": "菅原道真", "reading": "すがわらのみちざね", "category": "人物名", "extract": "平安時代の学者政治家。"},
    {"title": "駿府城の戦い", "reading": "すんぷじょうのたたかい", "category": "戦い名", "extract": "駿府城を巡る戦闘。"},
    {"title": "鈴鹿関合戦", "reading": "すずかのせきかっせん", "category": "合戦名", "extract": "古代三関の合戦。"},
    {"title": "水軍の乱", "reading": "すいぐんのらん", "category": "乱", "extract": "中世海賊衆の内乱。"},
    {"title": "周防国合戦", "reading": "すおうのこくかっせん", "category": "合戦名", "extract": "大内氏の合戦。"},
    {"title": "西南戦争", "reading": "せいなんせんそう", "category": "戦争名", "extract": "西郷隆盛の士族反乱。"},
    {"title": "関ヶ原の戦い", "reading": "せきがはらのたたかい", "category": "戦い名", "extract": "東軍西軍の決戦。"},
    {"title": "関宿合戦", "reading": "せきやどかっせん", "category": "合戦名", "extract": "関東戦国期の合戦。"},
    {"title": "摂関政治", "reading": "せっかんせいじ", "category": "時代名", "extract": "藤原氏の政治支配。"},
    {"title": "千利休切腹", "reading": "せんのりきゅうせっぷく", "category": "事件名", "extract": "茶聖千利休の切腹。"},
    {"title": "蘇我入鹿", "reading": "そがのいるか", "category": "人物名", "extract": "大化改新で討たれた政治家。"},
    {"title": "相馬合戦", "reading": "そうまかっせん", "category": "合戦名", "extract": "相馬氏と伊達氏の戦闘。"},
    {"title": "尊皇攘夷", "reading": "そんのうじょうい", "category": "事件名", "extract": "幕末の政治運動。"},
    {"title": "曽我兄弟の仇討ち", "reading": "そがきょうだいのあだうち", "category": "事件名", "extract": "富士巻狩りの仇討ち。"},
    {"title": "宗谷海峡防衛戦", "reading": "そうやかいきょうぼうえいせん", "category": "戦い名", "extract": "北方警備の戦闘。"},
    {"title": "大化の改新", "reading": "たいかのかいしん", "category": "事件名", "extract": "中大兄皇子の政治改革。"},
    {"title": "太閤検地", "reading": "たいこうけんち", "category": "事件名", "extract": "豊臣秀吉の全国検地。"},
    {"title": "武田信玄", "reading": "たけだしんげん", "category": "人物名", "extract": "甲斐の戦国大名。"},
    {"title": "平清盛", "reading": "たいらのきよもり", "category": "人物名", "extract": "平氏政権の首班。"},
    {"title": "壇ノ浦の戦い", "reading": "だんのうらのたたかい", "category": "戦い名", "extract": "平氏滅亡の戦い。"},
    {"title": "近松門左衛門", "reading": "ちかまつもんざえもん", "category": "人物名", "extract": "江戸の浄瑠璃作者。"},
    {"title": "長宗我部元親", "reading": "ちょうそかべもとちか", "category": "人物名", "extract": "四国統一の戦国大名。"},
    {"title": "千早城の戦い", "reading": "ちはやじょうのたたかい", "category": "戦い名", "extract": "楠木正成の籠城戦。"},
    {"title": "智恵伊豆", "reading": "ちえいず", "category": "人物名", "extract": "老中松平信綱。"},
    {"title": "中条合戦", "reading": "ちゅうじょうかっせん", "category": "合戦名", "extract": "戦国時代の合戦。"},
    {"title": "津田三蔵", "reading": "つださんぞう", "category": "人物名", "extract": "大津事件の巡査。"},
    {"title": "津島合戦", "reading": "つしまかっせん", "category": "合戦名", "extract": "尾張国の合戦。"},
    {"title": "津和野藩", "reading": "つわのはん", "category": "組織名", "extract": "石見国の藩。"},
    {"title": "鶴岡八幡宮の変", "reading": "つるがおかはちまんぐうのへん", "category": "事件名", "extract": "源実朝暗殺。"},
    {"title": "対馬事件", "reading": "つしまじけん", "category": "事件名", "extract": "ロシア軍艦占拠事件。"},
    {"title": "天保の改革", "reading": "てんぽうのかいかく", "category": "事件名", "extract": "水野忠邦の改革。"},
    {"title": "天正遣欧使節", "reading": "てんしょうけんおうしせつ", "category": "事件名", "extract": "キリシタン少年使節。"},
    {"title": "寺田屋事件", "reading": "てらだやじけん", "category": "事件名", "extract": "薩摩藩士粛清事件。"},
    {"title": "天明の大飢饉", "reading": "てんめいのたいききん", "category": "事件名", "extract": "江戸中期の飢饉。"},
    {"title": "天平文化", "reading": "てんぴょうぶんか", "category": "時代名", "extract": "奈良時代の仏教文化。"},
    {"title": "鳥羽・伏見の戦い", "reading": "とばふしみのたたかい", "category": "戦い名", "extract": "戊辰戦争開戦。"},
    {"title": "徳川家康", "reading": "とくがわいえやす", "category": "人物名", "extract": "江戸幕府初代将軍。"},
    {"title": "豊臣秀吉", "reading": "とよとみひでよし", "category": "人物名", "extract": "天下統一。"},
    {"title": "徳川吉宗", "reading": "とくがわよしむね", "category": "人物名", "extract": "享保の改革の将軍。"},
    {"title": "土佐藩", "reading": "とさはん", "category": "組織名", "extract": "坂本龍馬らを輩出した藩。"},
    {"title": "長篠の戦い", "reading": "ながしののたたかい", "category": "戦い名", "extract": "織田鉄砲隊の合戦。"},
    {"title": "南蛮貿易", "reading": "なんばんぼうえき", "category": "事件名", "extract": "ポルトガル貿易。"},
    {"title": "南北朝時代", "reading": "なんぼくちょうじだい", "category": "時代名", "extract": "皇統分裂の時代。"},
    {"title": "生麦事件", "reading": "なまむぎじけん", "category": "事件名", "extract": "薩摩藩英国人殺傷事件。"},
    {"title": "中臣鎌足", "reading": "なかとみのかまたり", "category": "人物名", "extract": "大化改新の功臣。"},
    {"title": "日清戦争", "reading": "にっしんせんそう", "category": "戦争名", "extract": "日清の近代戦争。"},
    {"title": "日露戦争", "reading": "にちろせんそう", "category": "戦争名", "extract": "日露の近代戦争。"},
    {"title": "日米和親条約", "reading": "にちべいわしんじょうやく", "category": "条約名", "extract": "開国の条約。"},
    {"title": "二・二六事件", "reading": "ににろくじけん", "category": "事件名", "extract": "陸軍青年将校の乱。"},
    {"title": "新田義貞", "reading": "にったよしさだ", "category": "人物名", "extract": "鎌倉幕府打倒の武将。"},
    {"title": "額田王", "reading": "ぬかたのおおきみ", "category": "人物名", "extract": "万葉歌人。"},
    {"title": "布引の戦い", "reading": "ぬのびきのたたかい", "category": "戦い名", "extract": "源平合戦。"},
    {"title": "沼田合戦", "reading": "ぬまたかっせん", "category": "合戦名", "extract": "真田と北条の戦い。"},
    {"title": "幣原外交", "reading": "ぬさはらがいこう", "category": "事件名", "extract": "協調外交。"},
    {"title": "沼津藩合戦", "reading": "ぬまづはんかっせん", "category": "戦い名", "extract": "駿河の合戦。"},
    {"title": "根来寺の戦い", "reading": "ねごろじのたたかい", "category": "戦い名", "extract": "秀吉の紀州攻め。"},
    {"title": "根来衆一揆", "reading": "ねごろしゅういっき", "category": "一揆", "extract": "根来衆蜂起。"},
    {"title": "根岸党合戦", "reading": "ねぎしとうかっせん", "category": "戦い名", "extract": "武蔵武士団戦闘。"},
    {"title": "練馬城の戦い", "reading": "ねりまじょうのたたかい", "category": "戦い名", "extract": "太田道灌の戦い。"},
    {"title": "年貢減免一揆", "reading": "ねんぐげんめんいっき", "category": "一揆", "extract": "農民一揆。"},
    {"title": "野木宮合戦", "reading": "のぎみやかっせん", "category": "合戦名", "extract": "源頼朝の戦い。"},
    {"title": "野辺地戦争", "reading": "のへじせんそう", "category": "戦争名", "extract": "戊辰戦争東北戦。"},
    {"title": "野田城の戦い", "reading": "のだじょうのたたかい", "category": "戦い名", "extract": "武田信玄の戦い。"},
    {"title": "野村望東尼", "reading": "のむらぼうとうに", "category": "人物名", "extract": "幕末歌人。"},
    {"title": "延方の戦い", "reading": "のぶかたのたたかい", "category": "戦い名", "extract": "天狗党の戦い。"},
    {"title": "箱館戦争", "reading": "はこだてせんそう", "category": "戦争名", "extract": "五稜郭の戦い。"},
    {"title": "廃藩置県", "reading": "はいはんちけん", "category": "事件名", "extract": "明治の中央集権改革。"},
    {"title": "服部半蔵", "reading": "はっとりはんぞう", "category": "人物名", "extract": "徳川家の忍者武将。"},
    {"title": "原敬暗殺事件", "reading": "はらたかしあんさつじけん", "category": "事件名", "extract": "東京駅首相暗殺事件。"},
    {"title": "羽柴秀吉", "reading": "はしばひでよし", "category": "人物名", "extract": "豊臣秀吉の旧名。"},
    {"title": "卑弥呼", "reading": "ひみこ", "category": "人物名", "extract": "邪馬台国の女王。"},
    {"title": "平治の乱", "reading": "へいじのらん", "category": "乱", "extract": "平安末期の乱。"},
    {"title": "保元の乱", "reading": "ほうげんのらん", "category": "乱", "extract": "武士台頭の乱。"},
    {"title": "比叡山焼き討ち", "reading": "ひえいざんやきうち", "category": "事件名", "extract": "信長の延暦寺攻め。"},
    {"title": "土方歳三", "reading": "ひじかたとしぞう", "category": "人物名", "extract": "新選組副長。"},
    {"title": "福沢諭吉", "reading": "ふくざわゆきち", "category": "人物名", "extract": "学問のすゝめ著者。"},
    {"title": "藤原道長", "reading": "ふじわらのみちなが", "category": "人物名", "extract": "摂関全盛期。"},
    {"title": "藤原鎌足", "reading": "ふじわらのかまたり", "category": "人物名", "extract": "大化改新功臣。"},
    {"title": "伏見城の戦い", "reading": "ふしみじょうのたたかい", "category": "戦い名", "extract": "関ヶ原前哨戦。"},
    {"title": "藤島合戦", "reading": "ふじしまかっせん", "category": "合戦名", "extract": "新田義貞の越前合戦。"},
    {"title": "平安時代", "reading": "へいあんじだい", "category": "時代名", "extract": "平安京の時代。"},
    {"title": "平民宰相", "reading": "へいみんさいしょう", "category": "人物名", "extract": "原敬の通称。"},
    {"title": "平家物語", "reading": "へいけものがたり", "category": "事件名", "extract": "源平軍記物。"},
    {"title": "碧蹄館の戦い", "reading": "へきていかんのたたかい", "category": "戦い名", "extract": "文禄の役の合戦。"},
    {"title": "幣原喜重郎", "reading": "へいはらきじゅうろう", "category": "人物名", "extract": "昭和期の首相。"},
    {"title": "本能寺の変", "reading": "ほんのうじのへん", "category": "事件名", "extract": "明智光秀が信長を討った変。"},
    {"title": "北条時宗", "reading": "ほうじょうときむね", "category": "人物名", "extract": "元寇時の執権。"},
    {"title": "北条政子", "reading": "ほうじょうまさこ", "category": "人物名", "extract": "尼将軍。"},
    {"title": "鳳仙花合戦", "reading": "ほうせんかかっせん", "category": "合戦名", "extract": "近世戦闘。"},
    {"title": "法隆寺建立", "reading": "ほうりゅうじこんりゅう", "category": "事件名", "extract": "聖徳太子の寺院建立。"},
    {"title": "満州事変", "reading": "まんしゅうじへん", "category": "事件名", "extract": "関東軍の行動。"},
    {"title": "前田利家", "reading": "まえだとしいえ", "category": "人物名", "extract": "加賀藩祖。"},
    {"title": "松平定信", "reading": "まつだいらさだのぶ", "category": "人物名", "extract": "寛政改革老中。"},
    {"title": "松下村塾", "reading": "しょうかそんじゅく", "category": "組織名", "extract": "吉田松陰の私塾。"},
    {"title": "丸山合戦", "reading": "まるやまかっせん", "category": "合戦名", "extract": "戦国期の合戦。"},
    {"title": "三方ヶ原の戦い", "reading": "みかたがはらのたたかい", "category": "戦い名", "extract": "信玄と家康の戦い。"},
    {"title": "三木合戦", "reading": "みきかっせん", "category": "合戦名", "extract": "秀吉の兵糧攻め。"},
    {"title": "宮古湾海戦", "reading": "みやこわんかいせん", "category": "戦い名", "extract": "戊辰戦争海戦。"},
    {"title": "水野忠邦", "reading": "みずのただくに", "category": "人物名", "extract": "天保改革の老中。"},
    {"title": "源義経", "reading": "みなもとのよしつね", "category": "人物名", "extract": "壇ノ浦の名将。"},
    {"title": "室町幕府", "reading": "むろまちばくふ", "category": "組織名", "extract": "足利氏の政権。"},
    {"title": "蒙古襲来", "reading": "もうこしゅうらい", "category": "戦争名", "extract": "元寇。"},
    {"title": "武蔵七党", "reading": "むさししちとう", "category": "組織名", "extract": "武蔵の武士団。"},
    {"title": "村上水軍", "reading": "むらかみすいぐん", "category": "組織名", "extract": "瀬戸内の水軍。"},
    {"title": "陸奥国府合戦", "reading": "むつのこくふかっせん", "category": "戦い名", "extract": "古代奥羽の戦い。"},
    {"title": "明治維新", "reading": "めいじいしん", "category": "事件名", "extract": "明治近代化改革。"},
    {"title": "明応の政変", "reading": "めいおうのせいへん", "category": "事件名", "extract": "室町将軍追放変。"},
    {"title": "明暦の大火", "reading": "めいれきのたいか", "category": "事件名", "extract": "江戸大火災。"},
    {"title": "目加田合戦", "reading": "めかたかっせん", "category": "合戦名", "extract": "近江の合戦。"},
    {"title": "明治天皇", "reading": "めいじてんのう", "category": "人物名", "extract": "明治の天皇。"},
    {"title": "森蘭丸", "reading": "もりらんまる", "category": "人物名", "extract": "信長小姓。"},
    {"title": "毛利元就", "reading": "もうりもとなり", "category": "人物名", "extract": "中国地方の覇者。"},
    {"title": "毛利輝元", "reading": "もうりてるもと", "category": "人物名", "extract": "西軍総大将。"},
    {"title": "問注所設置", "reading": "もんちゅうじょせっち", "category": "事件名", "extract": "鎌倉幕府の機関。"},
    {"title": "蒙古軍来襲", "reading": "もうこぐんらいしゅう", "category": "戦争名", "extract": "元寇合戦。"},
    {"title": "山崎の戦い", "reading": "やまざきのたたかい", "category": "戦い名", "extract": "秀吉の弔い合戦。"},
    {"title": "屋島の戦い", "reading": "やしまのたたかい", "category": "戦い名", "extract": "源平合戦。"},
    {"title": "大和政権", "reading": "やまとせいけん", "category": "組織名", "extract": "古代の統一王朝。"},
    {"title": "山県有朋", "reading": "やまがたありとも", "category": "人物名", "extract": "長州軍人政治家。"},
    {"title": "山田長政", "reading": "やまだながまさ", "category": "人物名", "extract": "シャム日本人町首領。"},
    {"title": "結城合戦", "reading": "ゆうきかっせん", "category": "合戦名", "extract": "関東の合戦。"},
    {"title": "由井正雪", "reading": "ゆいしょうせつ", "category": "人物名", "extract": "慶安事件指導者。"},
    {"title": "弓削道鏡", "reading": "ゆげのどうきょう", "category": "人物名", "extract": "奈良の僧侶政治家。"},
    {"title": "湯浅党合戦", "reading": "ゆあさとうかっせん", "category": "戦い名", "extract": "紀伊の合戦。"},
    {"title": "結城秀康", "reading": "ゆうきひでやす", "category": "人物名", "extract": "家康次男。"},
    {"title": "吉田松陰", "reading": "よしだしょういん", "category": "人物名", "extract": "松下村塾指導者。"},
    {"title": "吉野朝廷", "reading": "よしのちょうてい", "category": "組織名", "extract": "南朝政権。"},
    {"title": "米沢藩", "reading": "よねざわはん", "category": "組織名", "extract": "上杉氏の藩。"},
    {"title": "吉川元春", "reading": "きっかわもとはる", "category": "人物名", "extract": "毛利一族武将。"},
    {"title": "与謝蕪村", "reading": "よさぶそん", "category": "人物名", "extract": "江戸の俳人。"},
    {"title": "蘭学事始", "reading": "らんがくことはじめ", "category": "事件名", "extract": "杉田玄白の随筆。"},
    {"title": "楽市楽座", "reading": "らくいちらくざ", "category": "事件名", "extract": "自由通商令。"},
    {"title": "落合合戦", "reading": "おちあいかっせん", "category": "合戦名", "extract": "美濃の合戦。"},
    {"title": "頼山陽", "reading": "らいさんよう", "category": "人物名", "extract": "日本外史著者。"},
    {"title": "羅生門警備", "reading": "らしょうもんけいび", "category": "事件名", "extract": "平安京の警備。"},
    {"title": "立憲改進党結成", "reading": "りっけんかいしんとうけっせい", "category": "事件名", "extract": "大隈重信の政党。"},
    {"title": "利休切腹", "reading": "りきゅうせっぷく", "category": "事件名", "extract": "秀吉による処罰。"},
    {"title": "陸奥条約", "reading": "むつじょうやく", "category": "条約名", "extract": "条約改正。"},
    {"title": "琉球処分", "reading": "りゅうきゅうしょぶん", "category": "事件名", "extract": "沖縄県設置。"},
    {"title": "林羅山", "reading": "はやしらざん", "category": "人物名", "extract": "幕府の儒臣。"},
    {"title": "留萌合戦", "reading": "るもいかっせん", "category": "合戦名", "extract": "蝦夷地の戦闘。"},
    {"title": "留守氏合戦", "reading": "るすしかっせん", "category": "戦い名", "extract": "陸奥武士団戦闘。"},
    {"title": "類聚歌林編纂", "reading": "るいじゅうかりんへんさん", "category": "事件名", "extract": "飛鳥期歌集。"},
    {"title": "留守居役設置", "reading": "るすいやくせっち", "category": "事件名", "extract": "幕府役職設置。"},
    {"title": "累積国債整理", "reading": "るいせきこくさいせいり", "category": "事件名", "extract": "明治初期財政改革。"},
    {"title": "連歌の会", "reading": "れんがのかい", "category": "事件名", "extract": "中世文芸活動。"},
    {"title": "連署設置", "reading": "れんしょせっち", "category": "事件名", "extract": "幕府職制設置。"},
    {"title": "歴史書編纂", "reading": "れきししょへんさん", "category": "事件名", "extract": "記紀編纂事業。"},
    {"title": "令制国設置", "reading": "りょうせいこくせっち", "category": "事件名", "extract": "律令国郡設置。"},
    {"title": "冷泉家創設", "reading": "れいぜいけそうせつ", "category": "事件名", "extract": "和歌の名門。"},
    {"title": "六波羅探題", "reading": "ろくはらたんだい", "category": "組織名", "extract": "鎌倉幕府治安機関。"},
    {"title": "六条合戦", "reading": "ろくじょうかっせん", "category": "合戦名", "extract": "京都の戦闘。"},
    {"title": "六角氏討伐", "reading": "ろっかくしとうばつ", "category": "戦い名", "extract": "信長の近江遠征。"},
    {"title": "浪士組結成", "reading": "ろうしぐみけっせい", "category": "事件名", "extract": "新選組前身。"},
    {"title": "蘆名氏合戦", "reading": "あしなしかっせん", "category": "合戦名", "extract": "会津の合戦。"},
    {"title": "和田合戦", "reading": "わだかっせん", "category": "合戦名", "extract": "鎌倉御家人抗争。"},
    {"title": "和歌山城の戦い", "reading": "わかやまじょうのたたかい", "category": "戦い名", "extract": "大坂の陣。"},
    {"title": "和気清麻呂", "reading": "わけのきよまろ", "category": "人物名", "extract": "宇佐八幡宮事件の忠臣。"},
    {"title": "若狭合戦", "reading": "わかさかっせん", "category": "合戦名", "extract": "若狭国の合戦。"},
    {"title": "和同開珎鋳造", "reading": "わどうかいちんちゅうぞう", "category": "事件名", "extract": "古代の貨幣鋳造。"},
]

# 歴史用語キーワード (J2)
HISTORICAL_TERMS = [
    "合戦", "戦い", "戦争", "事件", "変", "乱", "事変", "一揆", "条約", "政策", "改革", "法", "令",
    "将軍", "幕府", "天皇", "上皇", "法皇", "武将", "大名", "武士", "藩士", "軍", "朝廷", "大老", "老中",
    "信長", "家康", "秀吉", "頼朝", "義経", "清盛", "西郷", "龍馬", "討ち", "攻め", "暗殺", "討伐",
    "城", "寺", "社", "藩", "開国", "維新", "明治", "江戸", "戦国", "室町", "鎌倉", "平安", "奈良", "飛鳥"
]

class HistorySourceManager:
    """歴史データソース統合マネージャー（フォールバック制御、キャッシュ管理）"""

    def __init__(self):
        self.sources: Dict[str, BaseHistorySource] = {
            "wikipedia_ja": WikipediaJaSource(),
            "wikidata": WikidataSource(),
            "japansearch": JapanSearchSource(),
        }
        self.source_order = ["wikipedia_ja", "wikidata", "japansearch"]
        self._search_cache: Dict[str, List[SourceRecord]] = {}
        self._fetch_cache: Dict[str, Optional[SourceRecord]] = {}
        self._source_disabled_until: Dict[str, float] = {}

    def is_source_available(self, src_id: str) -> bool:
        """サーキットブレーカー: 一時無効化中か判定"""
        disabled_until = self._source_disabled_until.get(src_id, 0.0)
        return time.time() > disabled_until

    def mark_source_failed(self, src_id: str, cooldown_sec: float = 30.0):
        """ソース障害時に一定期間スキップ"""
        self._source_disabled_until[src_id] = time.time() + cooldown_sec

    def is_historical_event(self, record: SourceRecord) -> bool:
        """
        8.4節 歴史出来事・人物の判定 (_is_historical_event)
        J1: カテゴリ判定
        J2: 抽出文判定（年代・元号・歴史用語）
        J3: タイトルパターン判定（後半呼称または人物・時代・組織）
        J4: 日本史との関連
        合格条件: J4 が真、かつ J1〜J3 のうち2つ以上が真
        """
        if record.is_disambiguation:
            return False

        # J4: 日本史との関連
        j4 = record.japan_related or any(k in record.extract for k in JAPAN_KEYWORDS) or any(k in t for t in record.types for k in JAPAN_KEYWORDS)
        
        # J1: カテゴリ判定
        j1 = any(any(k in t for k in HISTORICAL_CATEGORY_KEYWORDS) for t in record.types) or any(t in VALID_CATEGORIES for t in record.types)
        
        # J2: 抽出文判定（年代・元号または歴史用語を含む）
        j2 = any(k in record.extract for k in ERA_KEYWORDS) or any(k in record.extract for k in HISTORICAL_TERMS)
        
        # J3: タイトルパターン判定（後半呼称または人物・時代・組織・事件の形式）
        _, suffix = split_event_name(record.title)
        has_suffix = bool(suffix)
        has_title_pattern = any(k in record.title for k in [
            "信長", "家康", "秀吉", "天皇", "将軍", "時代", "幕府", "院", "氏", "藩", "城", "寺",
            "党", "衆", "隊", "組", "条約", "同盟", "改革", "大獄", "討ち", "攻め"
        ])
        is_person_type = any("人物" in t for t in record.types)
        j3 = has_suffix or has_title_pattern or is_person_type
        
        count_j = sum([j1, j2, j3])
        return j4 and (count_j >= 2)

    def fetch_with_fallback(self, title: str, log_cb=None) -> Tuple[Optional[SourceRecord], Optional[str], bool]:
        """
        8.8節 切り替え規則に従って詳細を取得
        Returns: (SourceRecord or None, verified_by, is_first_source_healthy)
        """
        norm_title = unicodedata.normalize('NFKC', title.strip())
        if norm_title in self._fetch_cache:
            rec = self._fetch_cache[norm_title]
            return rec, (rec.source_id if rec else None), True

        first_source_healthy = True

        for idx, src_id in enumerate(self.source_order):
            if not self.is_source_available(src_id):
                if idx == 0:
                    first_source_healthy = False
                continue

            source = self.sources[src_id]
            retry_count = 0
            while retry_count <= SOURCE_RETRY:
                try:
                    record = source.fetch(norm_title)
                    if record is not None:
                        # 取得成功
                        self._fetch_cache[norm_title] = record
                        if idx > 0 and log_cb:
                            log_cb(f"代替ソース {src_id} にて '{norm_title}' を取得・確認しました")
                        return record, src_id, first_source_healthy
                    else:
                        # 結果なし（F-2）
                        break
                except Exception as e:
                    # 障害（F-1）
                    retry_count += 1
                    if idx == 0:
                        first_source_healthy = False
                    if retry_count <= SOURCE_RETRY:
                        time.sleep(SOURCE_RETRY_INTERVAL_SEC)
                    else:
                        self.mark_source_failed(src_id)
                        if log_cb:
                            log_cb(f"ソース {src_id} が障害のため次のソースへ切り替えます: {e}")
                        break

        # オフラインフォールバック（テストや完全オフライン環境用）
        for item in FALLBACK_HISTORY_DATA:
            if item["title"] == norm_title:
                rec = SourceRecord(
                    source_id="wikipedia_ja",
                    title=item["title"],
                    url=f"https://ja.wikipedia.org/wiki/{item['title']}",
                    reading=item["reading"],
                    extract=item["extract"],
                    types=[item["category"], "日本史"],
                    japan_related=True
                )
                self._fetch_cache[norm_title] = rec
                return rec, "wikipedia_ja", True

        self._fetch_cache[norm_title] = None
        return None, None, first_source_healthy

    def has_candidates(self, prefix: str, min_count: int = 5, log_cb=None) -> bool:
        """
        8.5節 候補確認
        prefix で始まる識別子読みを持つ出来事が min_count 件以上存在するか確認
        """
        norm_prefix = to_hiragana(prefix)
        cache_key = f"has_candidates_{norm_prefix}_{min_count}"
        if cache_key in self._search_cache:
            return len(self._search_cache[cache_key]) >= min_count

        found_candidates: List[SourceRecord] = []

        # 1. まずマスターデータから高速確認
        for item in FALLBACK_HISTORY_DATA:
            ident, suffix = split_event_name(item["title"])
            reading = determine_identifier_reading(item["title"], ident, suffix, item["reading"])
            if verify_prefix_match(reading, norm_prefix):
                rec = SourceRecord(
                    source_id="wikipedia_ja",
                    title=item["title"],
                    url=f"https://ja.wikipedia.org/wiki/{item['title']}",
                    reading=item["reading"],
                    extract=item["extract"],
                    types=[item["category"], "日本史"],
                    japan_related=True
                )
                if not any(c.title == rec.title for c in found_candidates):
                    found_candidates.append(rec)
                    if len(found_candidates) >= min_count:
                        self._search_cache[cache_key] = found_candidates
                        if log_cb:
                            log_cb(f"開始文字『{prefix}』の候補数が充足（{min_count}件以上）を確認しました")
                        return True

        # 2. 外部データソースから確認（必要な場合のみ）
        if self.is_source_available("wikipedia_ja"):
            try:
                wiki_src: WikipediaJaSource = self.sources["wikipedia_ja"]
                search_recs = wiki_src.search(f"{prefix} 合戦", limit=20)
                if len(search_recs) < 10:
                    search_recs.extend(wiki_src.search(f"{prefix}", limit=20))
                
                titles = [r.title for r in search_recs if ":" not in r.title][:20]
                if titles:
                    batch_details = wiki_src.fetch_many(titles)
                    for t, detail in batch_details.items():
                        if detail and self.is_historical_event(detail):
                            ident, suffix = split_event_name(detail.title)
                            reading = determine_identifier_reading(detail.title, ident, suffix, detail.reading)
                            if verify_prefix_match(reading, norm_prefix):
                                if not any(c.title == detail.title for c in found_candidates):
                                    found_candidates.append(detail)
                                    if len(found_candidates) >= min_count:
                                        self._search_cache[cache_key] = found_candidates
                                        return True
            except Exception as e:
                self.mark_source_failed("wikipedia_ja")
                if log_cb:
                    log_cb(f"外部ソース確認エラー: {e}")

        self._search_cache[cache_key] = found_candidates
        return len(found_candidates) >= min_count

    def ping_all(self) -> Dict[str, bool]:
        """全データソースの疎通確認"""
        results = {}
        for src_id, src in self.sources.items():
            results[src_id] = src.ping()
        return results
