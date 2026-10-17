"""Secret redaction — ONE definition of "this looks like a key, token or
password", used everywhere text is stored or logged.

`redact(text)` replaces each likely secret with "[REDACTED]". Applied before
storing any chat message (client chat, the sales chat transcript and answers,
the admin agent chat) and inside the shared log/alert helpers
(core/agent_base), so a key pasted into a chat never lands in the DB, a log
line or an admin alert. `looks_like_secret(text)` is the yes/no form the chat
guard uses to answer the client warmly instead of calling a model.

Deliberately biased toward catching: a redacted false positive costs a word
in a transcript; a missed key costs a client's account.
"""
import re

REDACTED = "[REDACTED]"

# Well-known credential shapes — redacted even inside URLs (e.g. ?key=AIza…)
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),            # OpenAI/Anthropic-style keys
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),           # Google API keys
    re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}"),         # Google OAuth access tokens
    re.compile(r"\b1//[0-9A-Za-z_\-]{20,}"),            # Google refresh tokens
    re.compile(r"\bEAA[A-Za-z0-9]{40,}"),               # Meta access tokens
    re.compile(r"\b(?:ghp|gho|xox[abp])[-_][A-Za-z0-9\-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    # WordPress Application Passwords: six groups of four, with a digit in
    # them (six ordinary 4-letter words must not match)
    re.compile(r"\b(?=[A-Za-z0-9 ]{29}\b)(?=[A-Za-z ]*\d)(?:[A-Za-z0-9]{4} ){5}[A-Za-z0-9]{4}\b"),
]
# "password: x" / "הסיסמה היא x" in our five languages — the value is redacted
_PASSWORD_PHRASE = re.compile(
    r"((?:password|passwd|pwd|סיסמ[הא]|הסיסמ[הא]|mot de passe|пароль|كلمة\s*(?:ال)?(?:مرور|سر)"
    r"|api[\s_-]?key|מפתח)\s*(?:[:=]|\bis\b|היא|הוא|est|это|هي)\s*)(\S{4,})",
    re.IGNORECASE)
# A 28+ char run of letters AND digits with no spaces reads as a key, not a word.
# Not applied inside URLs (Drive ids, UTM values are long tokens too).
_LONG_TOKEN = re.compile(
    r"(?<![\w/.])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{28,}(?![\w/.])")
_URL = re.compile(r"(https?://\S+)")


def redact(text) -> str:
    """The text with every likely secret replaced by [REDACTED]. Non-strings
    pass through unchanged."""
    if not isinstance(text, str) or not text:
        return text
    out = []
    for part in _URL.split(text):
        for pattern in _SECRET_PATTERNS:
            part = pattern.sub(REDACTED, part)
        part = _PASSWORD_PHRASE.sub(lambda m: m.group(1) + REDACTED, part)
        if not _URL.fullmatch(part):
            part = _LONG_TOKEN.sub(REDACTED, part)
        out.append(part)
    return "".join(out)


def looks_like_secret(text) -> bool:
    return isinstance(text, str) and redact(text) != text


def redact_obj(value):
    """redact() over every string inside a dict/list (e.g. sales-chat answers)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    return value
