"""
shared/reading.py - 識別子分離・読み変換・必要頭文字列決定 (Game Rule v6.1 / 7章・6.4節)
"""

import os
import sys
import re
import unicodedata
from typing import Tuple, List, Optional

# vendor ディレクトリのロード
VENDOR_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vendor"))
if os.path.exists(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

from .schema import SUFFIXES, SUFFIX_READINGS, SUFFIX_CATEGORIES

# pykakasi の遅延インポート
_kakasi_instance = None

def get_kakasi():
    global _kakasi_instance
    if _kakasi_instance is None:
        try:
            import pykakasi
            _kakasi_instance = pykakasi.kakasi()
        except ImportError:
            _kakasi_instance = None
    return _kakasi_instance

# 清音化マップ（濁音・半濁音 -> 清音）
VOICED_TO_UNVOICED = {
    'が': 'か', 'ぎ': 'き', 'ぐ': 'く', 'げ': 'け', 'ご': 'こ',
    'ざ': 'さ', 'じ': 'し', 'ず': 'す', 'ぜ': 'せ', 'ぞ': 'そ',
    'だ': 'た', 'ぢ': 'ち', 'づ': 'つ', 'で': 'て', 'ど': 'と',
    'ば': 'は', 'び': 'ひ', 'ぶ': 'ふ', 'べ': 'へ', 'ぼ': 'ほ',
    'ぱ': 'は', 'ぴ': 'ひ', 'ぷ': 'ふ', 'ぺ': 'へ', 'ぽ': 'ほ',
    'ゔ': 'う'
}

# 小書き文字
SMALL_KANA = set('ぁぃぅぇぉゃゅょゎ')

def to_hiragana(text: str) -> str:
    """カタカナをひらがなに変換し、特殊文字（ゎ->わ、ゔ->う）を統一 (N1)"""
    if not text:
        return ""
    text = unicodedata.normalize('NFKC', text)
    res = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:  # カタカナ
            h_char = chr(code - 0x60)
        else:
            h_char = ch
        
        if h_char == 'ゎ':
            h_char = 'わ'
        elif h_char == 'ゔ':
            h_char = 'う'
        res.append(h_char)
    return "".join(res)

def normalize_reading_for_mora(reading: str) -> str:
    """N1〜N3 による読みの正規化"""
    # N1: カタカナ統一
    hira = to_hiragana(reading)
    # N2: 末尾の非かな文字（空白、記号等。「ー」は除く）および末尾の促音「っ」を除去
    while hira:
        last = hira[-1]
        if last == 'ー':
            break
        # かな文字かチェック
        if 'ぁ' <= last <= 'ん':
            if last == 'っ':
                hira = hira[:-1]
                continue
            break
        else:
            hira = hira[:-1]
    
    # N3: 末尾の「ー」を除去（連続する場合はすべて）
    while hira and hira.endswith('ー'):
        hira = hira[:-1]
        
    return hira

def split_into_moras(reading: str) -> List[str]:
    """読みを拍（モーラ）に分割する (N4)。小書きかなは直前と結合"""
    norm = normalize_reading_for_mora(reading)
    if not norm:
        return []
    
    moras = []
    for ch in norm:
        if ch in SMALL_KANA and moras:
            moras[-1] += ch
        else:
            moras.append(ch)
    return moras

def clean_voice_mora(mora: str) -> str:
    """1拍または文字列の先頭の濁音・半濁音を清音化する"""
    if not mora:
        return ""
    first = mora[0]
    unvoiced_first = VOICED_TO_UNVOICED.get(first, first)
    return unvoiced_first + mora[1:]

def clean_voice_all(text: str) -> str:
    """文字列全体の全濁音・半濁音を清音化する（頭文字照合用）"""
    return "".join(VOICED_TO_UNVOICED.get(c, c) for c in text)

# 7.3節 正規表現パターン: 後半呼称リストは長い順
_SUFFIX_PATTERN = re.compile(r"^(?P<id>.+?)(?:の)?(?P<suffix>" + "|".join(SUFFIXES) + r")$")

def split_event_name(full_name: str, is_person: bool = False) -> Tuple[str, str]:
    """
    7.3節 分離アルゴリズム
    出来事名を識別子と後半呼称に分離する。
    連結の「の」は識別子にも後半呼称にも含めず除去する。
    人物判定された場合は分割せず全体を識別子とする。
    Returns: (identifier, suffix)
    """
    clean_name = unicodedata.normalize('NFKC', full_name.strip())
    if is_person:
        return clean_name, ""
    
    m = _SUFFIX_PATTERN.match(clean_name)
    if m:
        ident = m.group("id")
        suffix = m.group("suffix")
        # idが空でなければ分離成功
        if ident:
            return ident, suffix
    
    return clean_name, ""

def convert_to_hiragana_kakasi(text: str) -> str:
    """pykakasi または簡易変換で漢字テキストをひらがなに変換"""
    k = get_kakasi()
    if k:
        result = k.convert(text)
        hira = "".join([item.get('hira', '') for item in result])
        if hira:
            return to_hiragana(hira)
    return to_hiragana(text)

def determine_identifier_reading(full_name: str, identifier: str, suffix: str, overall_reading: Optional[str] = None) -> str:
    """
    7.4節 識別子読みの確定
    1. 全体の読みを取得（Wikipedia冒頭括弧内等、なければ pykakasi）
    2. 後半呼称の読みを末尾から除去。
       フル表記に連結の「の」があった場合、その読みの「の」も除去。
       フル表記に「の」がない場合は「の」を除去しない。
    3. 全体の読みが後半呼称の読みで終わっていない場合、または除去後に空になる場合は識別子文字列のみを pykakasi で変換。
    4. N1 で正規化。
    """
    clean_full = unicodedata.normalize('NFKC', full_name.strip())
    clean_id = unicodedata.normalize('NFKC', identifier.strip())
    
    # 識別子のみの場合はそのまま読みを計算
    if not suffix:
        if overall_reading:
            reading = to_hiragana(overall_reading)
        else:
            reading = convert_to_hiragana_kakasi(clean_id)
        return normalize_reading_for_mora(reading)

    suffix_reading = SUFFIX_READINGS.get(suffix, "")
    
    cand_reading = None
    if overall_reading:
        cand_reading = to_hiragana(overall_reading)
    else:
        cand_reading = convert_to_hiragana_kakasi(clean_full)
    
    cand_reading = normalize_reading_for_mora(cand_reading)
    
    # フル表記に連結の「の」があるか判定
    has_linking_no = clean_full.endswith("の" + suffix)
    target_suffix_reading = ("の" + suffix_reading) if has_linking_no else suffix_reading
    
    if cand_reading and cand_reading.endswith(target_suffix_reading):
        id_reading = cand_reading[:-len(target_suffix_reading)]
        if id_reading:
            return normalize_reading_for_mora(id_reading)
            
    # 全体の読みからうまく除去できなかった場合は識別子のみを変換
    id_reading = convert_to_hiragana_kakasi(clean_id)
    return normalize_reading_for_mora(id_reading)

def generate_candidate_prefixes(identifier_reading: str) -> List[str]:
    """
    6.4節 手順2・手順3: 候補列の生成および清音化
    直前の識別子読みから、次ターンの必要頭文字列の候補列を優先順に生成する。
    """
    moras = split_into_moras(identifier_reading)
    if not moras:
        return []
    
    m1 = moras[-1]
    raw_candidates = []
    
    if m1 != "ん":
        # 条件: M1 が「ん」以外
        # ① M1
        raw_candidates.append(m1)
        # ② (M1が2文字拍なら) M1の先頭1文字
        if len(m1) > 1:
            raw_candidates.append(m1[0])
    else:
        # 条件: M1 が「ん」
        if len(moras) >= 2:
            m2 = moras[-2]
            # ① M2 + 「ん」
            raw_candidates.append(m2 + "ん")
            # ② M2
            raw_candidates.append(m2)
            # ③ (M2が2文字拍なら) M2の先頭1文字
            if len(m2) > 1:
                raw_candidates.append(m2[0])
        else:
            # 「ん」単独の場合はそのまま「ん」
            raw_candidates.append("ん")
            
    # 手順3: 清音化（各候補の先頭拍の濁音・半濁音を清音化）
    cleaned_candidates = []
    for cand in raw_candidates:
        c_mora = clean_voice_mora(cand)
        if c_mora not in cleaned_candidates:
            cleaned_candidates.append(c_mora)
            
    return cleaned_candidates

def verify_prefix_match(identifier_reading: str, required_prefix: str) -> bool:
    """
    6.4節 末尾: 頭文字の照合（回答の検証用）
    回答の識別子読みを N1〜N3 で正規化し、全文字を清音化した文字列が
    required_prefix で前方一致すれば適合とする。
    """
    norm = normalize_reading_for_mora(identifier_reading)
    cleaned_reading = clean_voice_all(norm)
    cleaned_prefix = clean_voice_all(required_prefix)
    return cleaned_reading.startswith(cleaned_prefix)
