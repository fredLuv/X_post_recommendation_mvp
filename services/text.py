from __future__ import annotations

import re
from collections import Counter


DOMAIN_PATTERN = re.compile(r"\b[\w-]+\.(?:com|io|org|app|xyz|co|net)\b", re.IGNORECASE)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "will",
    "you",
    "your",
    "all",
    "anyway",
    "after",
    "back",
    "build",
    "can",
    "company",
    "didn",
    "every",
    "first",
    "from",
    "get",
    "got",
    "have",
    "here",
    "into",
    "itself",
    "just",
    "keep",
    "land",
    "more",
    "new",
    "not",
    "now",
    "one",
    "out",
    "our",
    "per",
    "really",
    "set",
    "still",
    "takes",
    "that’s",
    "their",
    "there",
    "they",
    "this",
    "those",
    "two",
    "used",
    "using",
    "us",
    "what",
    "we",
    "where",
    "who",
    "were",
    "week",
    "re",
}


def preprocess_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = DOMAIN_PATTERN.sub(" ", value)
    value = re.sub(r"\bhttps?\b", " ", value)
    value = re.sub(r"[@#](\w+)", r" \1 ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_text(value: str) -> str:
    value = preprocess_text(value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def extract_hashtags(value: str) -> list[str]:
    return sorted({tag.lower() for tag in re.findall(r"#(\w+)", value)})


def extract_mentions(value: str) -> list[str]:
    return sorted({mention.lower() for mention in re.findall(r"@(\w+)", value)})


def extract_urls(value: str) -> list[str]:
    return re.findall(r"https?://\S+", value)


def normalize_keyword(token: str) -> str:
    token = token.lower().strip()
    token = token.replace("’", "").replace("'", "")
    token = re.sub(r"[^a-z0-9_]", "", token)
    if token.endswith("ies") and len(token) > 4:
        token = token[:-3] + "y"
    elif token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
        token = token[:-1]
    return token


def extract_keywords(value: str, limit: int = 8) -> list[str]:
    raw = preprocess_text(value)
    words = []
    for token in re.findall(r"[a-z0-9_']+", raw):
        normalized = normalize_keyword(token)
        if normalized in STOPWORDS or len(normalized) <= 2:
            continue
        words.append(normalized)
    counts = Counter(words)
    return [term for term, _ in counts.most_common(limit)]
