"""يحوّل نصًا متصلًا (سرد + مشاهد + حوار في فقرات طويلة بلا أسطر) إلى سيناريو منظّم.

مثال على المدخل:
    ...قصة قصيرة تُروى... المشهد الأول: خارجي، نهاري، مجموعة منازل... قطع المشهد الثاني : نهاري ...
    عبد الله : هادا جارنا الجديد عامر : حسب ما فهمت ...

المخرج: النص بالتنسيق الذي يفهمه make_video.py:
    سرد (فقرات منفصلة) + سطر مكان + "الاسم:" والكلام بعده.
"""
from __future__ import annotations

import re

ORD = (r"(?:الحادي عشر|الثاني عشر|الثالث عشر|الرابع عشر|الخامس عشر|السادس عشر|السابع عشر|الثامن عشر|"
       r"التاسع عشر|العشرون|الاول|الأول|الثاني|الثالث|الرابع|الخامس|السادس|السابع|الثامن|التاسع|العاشر|\d+)")
MARKER = re.compile(rf"(?:قطع\s*)?المشهد\s+{ORD}\s*[:：،\-–]?", re.U)
CUT = re.compile(r"(?<!\S)قطع(?!\S)")

PREFIXES = {"ابو", "أبو", "ام", "أم", "زوجة", "معلم", "صاحب", "الحاج", "الشيخ", "عبد", "بنت", "ابن",
            "خال", "عم", "جار", "اخو", "أخو", "اخت", "أخت", "والد", "والدة", "المعلم", "الدكتور",
            "الاستاذ", "الأستاذ", "الشاب"}
STOP_SPEAKER = {"قائلا", "قائلاً", "يجيب", "تجيب", "يقول", "تقول", "يرد", "ترد", "فقال", "وقال",
                "فأجابه", "وأجاب", "سائلا", "سائلاً", "يسأل", "تسأل"}
NOT_NAME = {"العصر", "الصباح", "المساء", "الليل", "النهار", "الظهر", "الفجر", "صباحي", "نهاري", "ليلي",
            "ليلى", "مساء", "داخلي", "خارجي", "الاول", "الأول", "الثاني", "الثالث", "الرابع", "الخامس",
            "السادس", "السابع", "الثامن", "التاسع", "العاشر", "عشر", "ملاحظة", "المشهد", "ايضا", "مثلا",
            "منهم", "مثل"}
GROUP = {"الجالسون", "الجميع", "الكل", "الحضور", "الجيران"}
SLUG_KEY = re.compile(r"(داخلي|خارجي|ليلي|ليلى|نهاري|نهار|صباحي|ليل|مساء|مساءً|مساءا|عصر|ظهر)")
CAST_START = re.compile(r"^\s*(بطل المسلسل|ابطال|أبطال|الشخصيات|الممثلون|شخصيات)")
DESCRIPTOR = re.compile(r"^(رجل|شاب|امرأة|امراة|فتاة|بنت|ولد|طفل|عجوز|شيخ|ابن خال|الجار)\s")
NARR_STARTERS = {"نصادف", "اليوم", "في", "كان", "يحكي", "تدور", "قصة", "حكاية", "بدأت", "عاش", "هذه", "هذا"}


def looks_flat(raw: str) -> bool:
    """نص متصل: فيه علامات مشاهد داخل السطر، أو سطور طويلة جدًا بلا فواصل."""
    lines = [ln for ln in raw.replace("\r", "").split("\n") if ln.strip()]
    if not lines:
        return False
    markers_inline = len(MARKER.findall(raw))
    longest = max(len(ln) for ln in lines)
    if markers_inline >= 2 and longest > 400:
        return True
    return longest > 900 and len(lines) < 8


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\s*[:：]\s*", " : ", text)
    return text.split()


def _speaker_spans(tokens: list[str]) -> list[tuple[int, int, str, bool]]:
    """(بداية الاسم، موضع النقطتين، الاسم، هل هو فعل قول مثل 'يجيب')."""
    spans = []
    for k, tok in enumerate(tokens):
        if tok != ":" or k == 0:
            continue
        last = tokens[k - 1]
        start = k - 1
        if last in NOT_NAME or last.isdigit() or len(last) > 14:
            continue
        stop = last in STOP_SPEAKER
        if last.startswith("ال") and last not in GROUP and not (k >= 2 and tokens[k - 2] in PREFIXES):
            continue
        if k >= 2 and tokens[k - 2] in PREFIXES and not stop:
            start = k - 2
        name = " ".join(tokens[start:k])
        # "ابو محمد قائلا :" ← المتحدث ابو محمد
        if stop and k >= 3 and tokens[k - 3] in PREFIXES and tokens[k - 2] not in STOP_SPEAKER \
                and tokens[k - 2] not in NOT_NAME and len(tokens[k - 2]) <= 14:
            start = k - 2
            if k >= 3 and tokens[k - 3] in PREFIXES:
                start = k - 3
            name, stop = " ".join(tokens[start:k - 1]), False
        # "علي يقول لاخاه خالد :" ← المتحدث علي لا خالد
        elif k >= 3 and tokens[k - 2].startswith("ل") and tokens[k - 3] in STOP_SPEAKER:
            who = tokens[k - 4] if k >= 4 else ""
            if who and who not in NOT_NAME:
                start, name = k - 4, who
                if k >= 5 and tokens[k - 5] in PREFIXES:
                    start, name = k - 5, f"{tokens[k - 5]} {who}"
        # لا نقبل نصف جملة كاسم متحدث
        if len(name.split()) > 3:
            continue
        name = re.sub(r"^(ابو|أبو)(?=\S{3,})", r"\1 ", name)
        spans.append((start, k, name, stop))
    return spans


def _split_slug(pre: str) -> tuple[str, str]:
    """يفصل أول المشهد إلى سطر مكان ووصف حركة."""
    pre = re.sub(r"\s*[:：]\s*", " ، ", pre).strip(" ،,.")
    parts = [p.strip() for p in re.split(r"[،,]", pre) if p.strip()]
    keys, place, rest = [], "", []
    i = 0
    while i < len(parts):
        p = parts[i]
        m = SLUG_KEY.search(p)
        words = p.split()
        if m and len(words) <= 4:
            keys.append(m.group(1))
            i += 1
            continue
        if m and len(words) > 4:
            keys.append(m.group(1))
            leftover = (p[:m.start()] + " " + p[m.end():]).strip()
            if leftover:
                rest.append(leftover)
            i += 1
            break
        break
    if i < len(parts) and not rest:
        p = parts[i]
        if len(p.split()) <= 6:
            place = p
            i += 1
    rest += parts[i:]
    while rest and re.match(r"^(ليلي|ليلى|نهاري|داخلي|خارجي|صباحي)\s+", rest[0]):
        keys.append(rest[0].split()[0])
        rest[0] = rest[0].split(" ", 1)[1].strip()
    if rest and rest[0].startswith("حيث "):
        rest[0] = rest[0][4:].strip()
    if not place and rest and 0 < len(rest[0].split()) <= 6:
        place = rest.pop(0)
    text = " ".join(keys)
    head = pre[:70]  # كلمات المكان والزمان تأتي في أول المشهد حتى لو بلا فواصل
    io = "داخلي" if re.search(r"داخلي", text + " " + head) or re.search(r"(القبو|غرفة|داخل)", place) else "خارجي"
    if re.search(r"(ليل|ليلي|ليلى)", text + " " + head):
        tod = "ليل"
    elif re.search(r"(مساء|عصر)", text):
        tod = "مساء"
    else:
        tod = "نهار"
    place = re.sub(r"^(?:ليلي|ليلى|نهاري|داخلي|خارجي|صباحي)\s+", "", place)
    place = re.sub(r"^حيث\s+", "", place)
    slug = " – ".join(x for x in (io, place, tod) if x)
    return slug, "، ".join(rest).strip()


def _chunk(text: str, limit: int = 200) -> list[str]:
    """يقسم السرد إلى مقاطع قصيرة عند الفواصل."""
    text = re.sub(r"\s+", " ", text).strip()
    out, cur = [], ""
    pieces = re.split(r"(?<=[،,.؟!])\s+| ، ", text)
    for piece in pieces:
        piece = piece.strip(" ،")
        if not piece:
            continue
        while len(piece) > limit * 1.4:
            cut = piece.rfind(" ", 0, limit)
            cut = cut if cut > 40 else limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if cur and len(cur) + 2 + len(piece) > limit:
            out.append(cur)
            cur = piece
        else:
            cur = f"{cur}، {piece}" if cur else piece
    if cur:
        out.append(cur)
    return out


NARR_CUT = re.compile(
    r"(?:(?<=\s)|^)(?:و\s?)?(?:يذهب|يقف|يصمت|يخرج|ينظر|يجلس|يدخل|ينصرف|يغادر|ينهض|يظهر|يتوجه|يبتسم|يضحك|يبكي|"
    r"يمضي|يرحل|يعود|يسير)(?=\s|$)|(?:(?<=\s)|^)بعد (?:دقائق|لحظات|قليل|ساعة) (?:اتى|أتى|جاء|يأتي)(?=\s)")


def split_tail(text: str) -> tuple[str, str]:
    """يفصل وصف الحركة الملصوق بآخر الحوار (أفعال بصيغة الغائب الفصحى) عن الكلام نفسه."""
    m = NARR_CUT.search(text)
    if m and m.start() >= 6:
        return text[:m.start()].strip(" ،"), text[m.start():].strip()
    return text, ""


def _convert_scene(body: str) -> list[str]:
    body = CUT.sub(" ", body)
    tokens = _tokens(body)
    spans = _speaker_spans(tokens)
    first = spans[0][0] if spans else len(tokens)
    pre = " ".join(tokens[:first])
    lines: list[str] = []
    slug, action = _split_slug(pre)
    lines.append(slug)
    if action:
        lines.extend(["", action])
    last_speakers: list[str] = []
    for j, (start, k, name, stop) in enumerate(spans):
        end = spans[j + 1][0] if j + 1 < len(spans) else len(tokens)
        text = " ".join(tokens[k + 1:end]).strip(" ،")
        if stop:
            others = [s for s in last_speakers if s != (last_speakers[-1] if last_speakers else "")]
            name = others[-1] if others else (last_speakers[-1] if last_speakers else name)
        elif name.endswith("قائلا") or name.endswith("قائلاً"):
            name = name.rsplit(" ", 1)[0]
        if not text:
            continue
        if DESCRIPTOR.match(text):
            lines.extend(["", f"{name} {text}"])
            continue
        last_speakers.append(name)
        text, tail = split_tail(text)
        if text:
            lines.extend(["", f"{name}:", text])
        if tail:
            lines.extend(["", tail])
    return lines


def flat_to_screenplay(raw: str) -> str:
    raw = raw.replace("\r", "")
    matches = list(MARKER.finditer(raw))
    if not matches:
        return raw
    narration = raw[:matches[0].start()].strip()
    out: list[str] = []

    # فقرة قائمة الأبطال في أول النص (أسماء بلا جمل) تُحذف من السرد
    paras = [p.strip() for p in re.split(r"\n\s*\n", narration) if p.strip()]
    if paras and CAST_START.match(paras[0]):
        toks = paras[0].split()
        cut = next((i for i, t in enumerate(toks) if i > 3 and t in NARR_STARTERS), None)
        paras[0] = " ".join(toks[cut:]) if cut else ""
    for p in paras:
        for chunk in _chunk(p):
            out.extend([chunk, ""])

    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        out.extend(_convert_scene(raw[m.end():end]))
        out.append("")
    return "\n".join(out).strip() + "\n"


def normalize(raw: str) -> str:
    """يعيد النص منسّقًا إن كان متصلًا، وإلا كما هو."""
    return flat_to_screenplay(raw) if looks_flat(raw) else raw
