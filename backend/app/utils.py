import re
import json
import hashlib
import unicodedata

def normalize_text(text: str) -> str:
    if not text:
        return ""
    # 1. Unicode NFKC normalization
    normalized = unicodedata.normalize('NFKC', text)
    # 2. Trim leading/trailing whitespace
    trimmed = normalized.strip()
    # 3. Collapse repeated whitespace
    collapsed = re.sub(r'\s+', ' ', trimmed)
    return collapsed

def get_card_fingerprint(english: str, chinese_meaning: str, part_of_speech: str) -> str:
    normalized_english = normalize_text(english).lower()
    normalized_chinese = normalize_text(chinese_meaning)
    normalized_pos = normalize_text(part_of_speech or "")

    # Canonical structured serialization
    data = {
        "english": normalized_english,
        "chinese_meaning": normalized_chinese,
        "part_of_speech": normalized_pos
    }
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    # SHA-256 fingerprint
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()

def is_multi_meaning(chinese_meaning: str) -> bool:
    if not chinese_meaning:
        return False
    # Check for multi-meaning punctuation: , ， 、 ; ； / ／
    pattern = r'[,，、;；/／]'
    return bool(re.search(pattern, chinese_meaning))
