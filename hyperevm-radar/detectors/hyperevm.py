from config import STOCK_WORDS, INFRA_WORDS

def extract_words(code: bytes):
    words = set()
    current = []

    for b in code:
        if 32 <= b <= 126:
            current.append(chr(b))
        else:
            if len(current) >= 3:
                text = "".join(current).lower()
                for part in text.replace("_", " ").replace("-", " ").split():
                    if len(part) >= 3:
                        words.add(part)
            current = []

    if len(current) >= 3:
        text = "".join(current).lower()
        for part in text.replace("_", " ").replace("-", " ").split():
            if len(part) >= 3:
                words.add(part)

    return words

def classify(words):
    stock_hits = sorted(words & STOCK_WORDS)
    infra_hits = sorted(words & INFRA_WORDS)

    if stock_hits and infra_hits:
        level = "A_CANDIDATE"
    else:
        level = "B"

    return {
        "level": level,
        "stock_hits": stock_hits,
        "infra_hits": infra_hits,
    }
