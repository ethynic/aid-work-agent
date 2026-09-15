"""Approximate OCR identity; keep original stored text and non-text identities."""
import re
import unicodedata

MAX_LENGTH = 20_000
JS_SPACE = set("\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")
SPACE_PATTERN = "[" + re.escape("".join(sorted(JS_SPACE))) + "]"

def _parts(text):
    folded = unicodedata.normalize("NFKC", text).lower().replace("−", "-")
    key = "".join(c for c in folded if c not in JS_SPACE and not unicodedata.category(c).startswith("P"))
    digits = "".join(sorted({c for c in folded if unicodedata.category(c).startswith("N")}))
    digit_pattern = "[" + re.escape(digits) + "]" if digits else r"(?!)"
    number_pattern = r"[+−-]?" + SPACE_PATTERN + "*" + digit_pattern + "+(?:[.,]" + digit_pattern + "+)*"
    numbers = ["".join(c for c in n if c not in JS_SPACE) for n in re.findall(number_pattern, folded)]
    negatives = re.findall(r"[不没无未勿别]|\b(?:not|no|never)\b", folded, re.ASCII)
    symbols = [c for c in folded if unicodedata.category(c).startswith("S") or c in "\u200d\ufe0e\ufe0f\u20e3"]
    return key, numbers, negatives, symbols


def ocr_text_matches(first: str, current: str) -> bool:
    if not isinstance(first, str) or not isinstance(current, str) or max(len(first.encode("utf-16-le", errors="surrogatepass")) // 2, len(current.encode("utf-16-le", errors="surrogatepass")) // 2) > MAX_LENGTH:
        return False
    if first == current:
        return bool(first)
    a, *guards_a = _parts(first)
    b, *guards_b = _parts(current)
    if guards_a != guards_b or not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < 10 or max(len(a), len(b)) > MAX_LENGTH:
        return False
    budget = max(len(a), len(b)) // 10
    if abs(len(a) - len(b)) > budget:
        return False
    start = 0
    while start < min(len(a), len(b)) and a[start] == b[start]:
        start += 1
    a, b = a[start:], b[start:]
    end = 0
    while end < min(len(a), len(b)) and a[-end-1] == b[-end-1]:
        end += 1
    if end:
        a, b = a[:-end], b[:-end]
    if not a or not b:
        return max(len(a), len(b)) <= budget
    # Myers bit-vector Levenshtein. Python big integers process the character
    # columns together, avoiding millions of Python dictionary operations while
    # preserving exactly the same distance threshold as the client.
    masks = {}
    for j, char in enumerate(b):
        masks[char] = masks.get(char, 0) | (1 << j)
    positive, negative = (1 << len(b)) - 1, 0
    distance, last = len(b), 1 << (len(b) - 1)
    for char in a:
        equal = masks.get(char, 0)
        vertical = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        plus = negative | ~(horizontal | positive)
        minus = positive & horizontal
        distance += bool(plus & last) - bool(minus & last)
        plus, minus = (plus << 1) | 1, minus << 1
        positive = minus | ~(vertical | plus)
        negative = plus & vertical
    return distance <= budget
