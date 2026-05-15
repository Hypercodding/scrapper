"""Company name normalization."""
import re
import unicodedata

LEGAL_SUFFIXES = re.compile(
    r",?\s*\b(Inc\.?|LLC\.?|Ltd\.?|Corp\.?|Corporation|Co\.?|Company)\s*$",
    re.IGNORECASE,
)


def normalize_company_name(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", name.strip())
    text = LEGAL_SUFFIXES.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    # Title case words unless all-caps acronym
    words = []
    for w in text.split():
        if w.isupper() and len(w) <= 4:
            words.append(w)
        else:
            words.append(w.capitalize() if w.islower() or w.isupper() else w)
    return " ".join(words)
