"""Is this message an EXPLICIT instruction to act now, as is? (2026-11-09)

"upload this video to TikTok drafts, just do it, as is" got content
questions (what does it show? caption? purpose?). When the owner or a client
gives a direct order, the agent resolves the file by name and creates the
card — it never asks about content. Code decides the flag (cheap, no model);
the prompts (support_agent.EXPLICIT_RULE) and youtube_metadata read it.
"""
import re

_EXPLICIT = re.compile("|".join((
    # Hebrew
    r"פשוט\s+(תעשה|תעשי|תעלה|תעלי|תשלח|תשלחי|תפרסם|תכין|תכיני|לעשות|להעלות|לשלוח)",
    r"כמו\s+ש(הוא|היא|זה|הם)\b", r"כמות\s+שהוא", r"בלי\s+(שאלות|לשאול|שינויים|לשנות)",
    r"אל\s+ת(שאל|שאלי|שנה|שני)", r"תעשה\s+את\s+זה", r"תעשי\s+את\s+זה", r"פשוט\s+ככה",
    r"עכשיו\s+בלי", r"רק\s+(תעלה|תעלי|תשלח|תשלחי)", r"תעלה\s+כבר", r"תשלח\s+כבר",
    r"לא\s+צריך\s+(כיתוב|תיאור|כותרת|לשאול)",
    # English
    r"\bjust\s+(do|upload|send|post|put|make)\b", r"\bas\s+is\b", r"\bdon'?t\s+ask\b",
    r"\bno\s+questions\b", r"\bwithout\s+(asking|changes|questions)\b", r"\bdo\s+it\s+now\b",
    r"\bno\s+caption\s+needed\b",
    # French / Arabic / Russian (the dashboard's other languages)
    r"\btel\s+quel(le)?\b", r"\bfais[- ]le\b", r"\bsans\s+(question|poser)",
    r"كما\s+هو", r"بدون\s+أسئلة", r"فقط\s+(ارفع|أرسل|ارسل)",
    r"\bкак\s+есть\b", r"\bбез\s+вопросов\b", r"\bпросто\s+(загрузи|отправь|сделай)",
)), re.IGNORECASE)


def is_explicit(message) -> bool:
    return bool(_EXPLICIT.search(str(message or "")))
