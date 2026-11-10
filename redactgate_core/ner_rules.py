"""Local rule-based NER for people, companies, addresses, accounts, phones, amounts.

RedactGate's regex packs cover regulated identifiers (IBAN, ЄДРПОУ, ІПН, SIN, SSN…);
names and companies were the job of Presidio/LLM-NER — which the offline desktop app can't
rely on (no model download, no cloud). These rules are tuned for the documents an auditor
or lawyer handles in Ukrainian, Russian and English: bank statements, contracts, invoices.

Signals, strongest first:
* legal forms — «ТОВ «Альфа»», «Приватне підприємство "Бета"», «Northwind Ltd.»;
* patronymics — «Петренко Іван Іванович», «ІВАНЕНКО ОЛЕНА ПЕТРІВНА»;
* initials — «Петренко І.І.», «І. І. Петренко»;
* cue words — «ФОП», «в особі», «Отримувач:», «ПІБ:», «Mr.», «Account holder:»;
* first-name gazetteer / surname suffixes (-енко, -чук, -юк…) for two-word names.

Every span carries ``source="rules"``-style metadata through ``EntitySpan.source``.
"""

from __future__ import annotations

import re

from .gazetteer import NOT_NAME_STEMS, is_first_name, stem
from .entities import EntitySpan

UP = "А-ЯІЇЄҐЁ"
LO = "а-яіїєґё"
AP = "'’ʼ"
CW = rf"[{UP}][{LO}{AP}]+(?:-[{UP}][{LO}{AP}]+)?"  # Cyrillic Capitalised word
CU = rf"[{UP}][{UP}{AP}]+(?:-[{UP}][{UP}{AP}]+)?"  # Cyrillic UPPERCASE word
LW = r"(?:Mc|Mac|O'|O’|D'|De|Van|Di)?[A-Z][a-z'’]+(?:-[A-Z][a-z'’]+)?"  # Latin Capitalised (MacLeod, O'Neil)
LU = r"[A-Z][A-Z'’]+(?:-[A-Z][A-Z'’]+)?"  # Latin UPPER
NB = rf"(?<![\w{AP}-])"  # left word boundary (Unicode-aware, apostrophe/hyphen aware)
NE = rf"(?![\w{AP}])"

PATR = rf"[{UP}][{LO}{AP}]+?(?:ович|евич|йович|ьович|івн|ївн|овн|евн|ічн|ичн|ич)[{LO}]{{0,3}}"
PATR_U = rf"[{UP}][{UP}{AP}]+?(?:ОВИЧ|ЕВИЧ|ЙОВИЧ|ЬОВИЧ|ІВН|ЇВН|ОВН|ЕВН|ІЧН|ИЧН|ИЧ)[{UP}]{{0,3}}"
INIT = rf"[{UP}]\.\s?(?:[{UP}]\.)?"
INIT_L = r"[A-Z]\.\s?(?:[A-Z]\.)?"

_SURNAME_SUFFIX = re.compile(
    r"(?:енко|енка|енку|чук|щук|юк|ук|ишин|ський|ська|цький|цька|ського|ської|ов|ова|ев|ева|"
    r"ін|іна|ин|ина|ець|яну|ян|ич|вич|ко|ак|як|ук|ар|ій)[а-яіїєґ]{0,3}$"
)
_STRONG_SURNAME = re.compile(r"(?:енк|чук|щук|юк|ишин|ович|евич)[а-яіїєґ]{0,3}$")


def _span(m: re.Match[str], etype: str, score: float, group: int | str = 0) -> EntitySpan:
    s, e = m.span(group)
    return EntitySpan(start=s, end=e, entity_type=etype, text=m.string[s:e], score=score, source="regex")


def _name_token_ok(tok: str) -> bool:
    return stem(tok) not in NOT_NAME_STEMS and tok.casefold() not in NOT_NAME_STEMS


# ── people ──────────────────────────────────────────────────────────────────

_P_FULL = [
    # Surname Name Patronymic / Name Patronymic Surname (title case and UPPER)
    re.compile(rf"{NB}{CW}\s+{CW}\s+{PATR}{NE}"),
    re.compile(rf"{NB}{CW}\s+{PATR}\s+{CW}{NE}"),
    re.compile(rf"{NB}{CU}\s+{CU}\s+{PATR_U}{NE}"),
    re.compile(rf"{NB}{CU}\s+{PATR_U}\s+{CU}{NE}"),
    # Name Patronymic (e.g. «Шановний Іване Петровичу»)
    re.compile(rf"{NB}{CW}\s+{PATR}{NE}"),
    re.compile(rf"{NB}{CU}\s+{PATR_U}{NE}"),
]
_P_INIT = [
    re.compile(rf"{NB}(?:{CW}|{CU})\s+{INIT}(?![{UP}{LO}])"),
    re.compile(rf"{NB}{INIT}\s?(?:{CW}|{CU}){NE}"),
    re.compile(rf"{NB}(?:{LW}|{LU}),?\s+{INIT_L}(?![A-Za-z])"),
    re.compile(rf"{NB}{INIT_L}\s?{LW}{NE}"),
]
_P_TWO = [
    re.compile(rf"{NB}(?P<a>{CW})\s+(?P<b>{CW}){NE}"),
    re.compile(rf"{NB}(?P<a>{CU})\s+(?P<b>{CU}){NE}"),
    re.compile(rf"{NB}(?P<a>{LW})\s+(?:[A-Z]\.\s?)?(?P<b>{LW}){NE}"),
    re.compile(rf"{NB}(?P<a>{LU})\s+(?P<b>{LU}){NE}"),
    re.compile(rf"{NB}(?P<a>{LW}),\s(?P<b>{LW}){NE}"),
]
_PERSON_CUE = re.compile(
    rf"{NB}(?i:ФОП|ФЛП|ФО-П|СПД|СПДФО|ФО|гр\.|громадян(?:ин|ина|ка|ки|ці)|граждан(?:ин|ина|ка)|"
    rf"ПІБ|П\.І\.Б\.|ФИО|Ф\.И\.О\.|в\s+особі|в\s+лице|Підписант|Mr\.?|Mrs\.?|Ms\.?|Dr\.?|"
    rf"Account\s+holder|Cardholder|Full\s+name|Client\s+name|Prepared\s+by|Signed\s+by|Attn\.?|Attention|Dear|"
    rf"Contact|Employee|Customer|Tenant|Landlord|Applicant|Claimant|Witness|Beneficiary|Payee|Borrower|Guarantor|"
    rf"Заявник|Заявитель|Позичальник|Заемщик|Поручитель|Орендар|Арендатор|Свідок|Свидетель|Працівник|Сотрудник|Контактна\s+особа|Контактное\s+лицо)"
    rf"[\s:,—–-]+(?:(?:Mr|Mrs|Ms|Miss|Dr|Prof|пан|пані|г-н|г-жа)\.?\s+)?(?P<v>(?:(?:{CW}|{CU}|{LW}|{LU}|{INIT}|{INIT_L}),?\s?){{1,3}})",
)
_SOLE_TRADER = re.compile(rf"{NB}(?:ФОП|ФЛП|ФО-П|СПД|ИП)\s+(?P<v>(?:{CW}|{CU})(?:\s+(?:{CW}|{CU}|{INIT})){{0,2}})")


_LONE_FIRST = re.compile(rf"{NB}[{UP}][{LO}{AP}]+{NE}")
_SINGLE_SURNAME = re.compile(
    rf"{NB}(?P<s>[{UP}][{LO}{AP}]+(?:енк[оаиуі]|енком|чук\w{{0,3}}|щук\w{{0,3}}|юк\w{{0,3}}|ишин\w{{0,3}})){NE}"
)


def detect_people(text: str) -> list[EntitySpan]:
    out: list[EntitySpan] = []
    for m in _SINGLE_SURNAME.finditer(text):
        if _name_token_ok(m.group("s")) and len(m.group("s")) >= 5:
            out.append(_span(m, "PERSON", 0.7, "s"))
    for rx in _P_FULL:
        for m in rx.finditer(text):
            toks = m.group(0).split()
            if all(_name_token_ok(t) for t in toks):
                out.append(_span(m, "PERSON", 0.93))
    for rx in _P_INIT:
        for m in rx.finditer(text):
            words = [t for t in re.split(r"[\s.,]+", m.group(0)) if len(t) > 1]
            if words and all(_name_token_ok(w) for w in words):
                out.append(_span(m, "PERSON", 0.9))
    for m in _SOLE_TRADER.finditer(text):
        out.append(_span(m, "PERSON", 0.96, "v"))
    for m in _PERSON_CUE.finditer(text):
        v = m.group("v").rstrip()
        s = m.start("v")
        words = [w for w in re.split(r"\s+", v) if w]
        # trim trailing words that are clearly not name parts
        while words and not _name_token_ok(words[-1].rstrip(".,")):
            words.pop()
        if words:
            words[-1] = words[-1].rstrip(",")
        if not words:
            continue
        val = " ".join(words) if " ".join(words) in v else v
        end = s + v.find(words[-1]) + len(words[-1])
        val = text[s:end]
        if any(c.isdigit() for c in val):
            continue
        out.append(EntitySpan(start=s, end=end, entity_type="PERSON", text=val, score=0.88, source="regex"))
    for rx in _P_TWO:
        pos = 0
        while True:
            m = rx.search(text, pos)
            if not m:
                break
            pos = m.start("b")  # overlapping: «Пенсионер Сергеев Николай» also tries «Сергеев Николай»
            a, b = m.group("a"), m.group("b")
            if not (_name_token_ok(a) and _name_token_ok(b)):
                continue
            fa, fb = is_first_name(a), is_first_name(b)
            sa = bool(_STRONG_SURNAME.search(a.casefold()))
            sb = bool(_STRONG_SURNAME.search(b.casefold()))
            if (fa or fb) or (sa or sb):
                out.append(_span(m, "PERSON", 0.8 if (fa or fb) else 0.72))
    # a lone first name («Ваш менеджер — Олена») is still a person
    for m in _LONE_FIRST.finditer(text):
        w = m.group(0)
        if len(w) >= 4 and is_first_name(w) and _name_token_ok(w):
            out.append(_span(m, "PERSON", 0.6))
    return out


# ── companies ───────────────────────────────────────────────────────────────

_FORMS_SHORT = (
    r"ТОВ|ТзОВ|ТДВ|ПП|ПрАТ|ПАТ|АТ|ДП|КП|ФГ|СГ|КТ|ПТ|ОСББ|ГО|БФ|МП|ВАТ|ЗАТ|"
    r"ООО|ОАО|ЗАО|ПАО|АО|НАО|ОДО|ЧП|ЧУП|ГП|LLC|LTD|Ltd|Inc|JSC|PJSC|PrJSC|LLP"
)
_FORMS_LONG = (
    r"Товариств\w+\s+з\s+обмеженою\s+відповідальністю|Товариств\w+\s+з\s+додатковою\s+відповідальністю|"
    r"Приватн\w+\s+підприємств\w+|Приватн\w+\s+акціонерн\w+\s+товариств\w+|Публічн\w+\s+акціонерн\w+\s+товариств\w+|"
    r"Акціонерн\w+\s+товариств\w+|Акціонерн\w+\s+банк\w*|Комерційн\w+\s+банк\w*|Державн\w+\s+підприємств\w+|"
    r"Комунальн\w+\s+підприємств\w+|Фермерськ\w+\s+господарств\w+|Громадськ\w+\s+організаці\w+|"
    r"Благодійн\w+\s+фонд\w*|Общество\s+с\s+ограниченной\s+ответственностью|Общества\s+с\s+ограниченной\s+ответственностью|"
    r"Акционерн\w+\s+обществ\w+|Частн\w+\s+предприяти\w+|Публичн\w+\s+акционерн\w+\s+обществ\w+"
)
_QUOTED = r"[«\"“„](?P<name>[^»\"”“\n]{1,80}?)[»\"”“]"
_C_QUOTED = re.compile(
    rf"(?:{NB}(?:{_FORMS_SHORT})\.?|(?i:{_FORMS_LONG}))(?:\s+(?:КБ|АКБ|«КБ»|\"КБ\"))?\s*{_QUOTED}",
)
_C_BARE = re.compile(
    rf"{NB}(?:{_FORMS_SHORT})(?:\s+(?:КБ|АКБ))?\s+(?!(?:{_FORMS_SHORT}|КБ|АКБ){NE})(?P<name>(?:{CW}|{CU}|{LW}|{LU})(?:[- ](?:{CW}|{CU}|{LW}|{LU})){{0,2}}){NE}"
)
_C_EN = re.compile(
    r"(?<![\w&'-])(?P<name>(?:[A-Z][\w&'’.-]*\s+){0,4}[A-Z][\w&'’.-]*?),?\s+"
    r"(?:LLC|L\.L\.C\.|LLP|L\.L\.P\.|Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|GmbH|AG|"
    r"S\.A\.|S\.r\.l\.|B\.V\.|N\.V\.|PLC|plc|ULC|LP|L\.P\.|Co\.|Group|Holdings)(?![\w])"
)
_C_BANK = re.compile(
    rf"{NB}(?P<name>(?:[{UP}][\w{AP}-]*[Бб]анк\w*|[A-Z][\w&-]*Bank|(?:[A-Z][\w&.-]+\s){{1,3}}Bank|Bank\s+of\s+[A-Z]\w+(?:\s[A-Z]\w+)?|"
    rf"(?:Royal|National|Scotia)\s?[Bb]ank(?:\s+of\s+[A-Z]\w+)?))(?![\w])"
)
_C_CUE_QUOTED = re.compile(
    rf"(?i:компані\w*|компани\w*|фірм\w*|фирм\w*|підприємств\w*|предприяти\w*|контрагент\w*|"
    rf"отримувач\w*|платник\w*|получател\w*|плательщик\w*|постачальник\w*|поставщик\w*|покупець|покупатель)"
    rf"[\s:]*{_QUOTED}"
)
_C_CUE_BARE = re.compile(
    rf"(?i:компані[яюїєі]\w*|компани[яиюей]\w*|фірм[аиоуі]\w*|фирм[аыоуе]\w*|company|firm|vendor|supplier|постачальник\w*|поставщик\w*)"
    rf"\s+(?P<name>(?:{CW}|{CU}|{LW}|{LU})(?:[- ](?:{CW}|{CU}|{LW}|{LU})){{0,2}}){NE}"
)
_EN_STOP_LEAD = re.compile(r"^(?:The|This|That|By|And|Of|For|To|With|From|Between|Dear|An?)\s+")


def detect_companies(text: str) -> list[EntitySpan]:
    out: list[EntitySpan] = []
    for rx, score in ((_C_QUOTED, 0.97), (_C_CUE_QUOTED, 0.9)):
        for m in rx.finditer(text):
            name = m.group("name").strip()
            if not name or not any(ch.isalpha() for ch in name):
                continue
            s = m.start("name") + (len(m.group("name")) - len(m.group("name").lstrip()))
            out.append(EntitySpan(start=s, end=s + len(name), entity_type="ORG", text=name, score=score, source="regex"))
    for m in _C_BARE.finditer(text):
        name = m.group("name")
        if stem(name.split()[0]) in NOT_NAME_STEMS:
            continue
        out.append(_span(m, "ORG", 0.9, "name"))
    for m in _C_EN.finditer(text):
        name = m.group("name")
        lead = _EN_STOP_LEAD.match(name)
        s = m.start("name") + (lead.end() if lead else 0)
        name = text[s : m.end("name")]
        if len(name) < 2 or name.casefold() in NOT_NAME_STEMS:
            continue
        out.append(EntitySpan(start=s, end=m.end("name"), entity_type="ORG", text=name, score=0.9, source="regex"))
    for m in _C_BANK.finditer(text):
        out.append(_span(m, "ORG", 0.85, "name"))
    for m in _C_CUE_BARE.finditer(text):
        name = m.group("name")
        if stem(name.split()[0]) in NOT_NAME_STEMS or name.split()[0].casefold() in NOT_NAME_STEMS:
            continue
        out.append(_span(m, "ORG", 0.8, "name"))
    return out


# ── addresses ───────────────────────────────────────────────────────────────

_STREET_CUE = (
    r"вул\.|вулиця|вулиці|ул\.|улица|улице|просп\.|проспект|пр-т|пр\.|бульв\.|бул\.|бульвар|"
    r"пров\.|провулок|переулок|пер\.|пл\.|площа|площадь|шосе|шоссе|наб\.|набережна|набережная|"
    r"узвіз|мкр\.|мікрорайон|микрорайон|майдан"
)
_ADDR_ANCHORS = [
    re.compile(
        rf"(?i:{_STREET_CUE})\s?(?:[{UP}0-9][\w{AP}.-]*)(?:\s(?:[{UP}][\w{AP}.-]*|\d+-?[а-яі]?))*"
        rf"(?:,?\s*(?:(?i:буд\.|будинок|д\.|дом)\s?)?\d{{1,4}}(?:[-/]?[{UP}{LO}A-Za-z0-9])?)?"
    ),
    re.compile(
        r"\b\d{1,6}[A-Za-z]?\s+(?:[A-Z][a-z]+\.?\s){1,3}(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|"
        r"Drive|Dr\.?|Lane|Ln\.?|Way|Court|Ct\.?|Crescent|Cres\.?|Place|Pl\.?|Parkway|Pkwy\.?|Terrace|Circle)(?![\w])"
        r"(?:\s+(?:N|S|E|W|NE|NW|SE|SW)\b)?"
    ),
]
_ADDR_PART = re.compile(
    "|".join(
        [
            r"\d{5}(?!\d)",  # UA / US postal code
            r"[A-Z]\d[A-Z]\s?\d[A-Z]\d",  # Canadian postal code
            r"(?:Україна|Украина|Ukraine|Canada|Канада|USA|США)",
            rf"[{UP}][\w{AP}-]+\s(?:обл\.|область|області|р-н|район|району)",
            rf"(?:м\.|місто|г\.|город|с\.|село|смт|сел\.|пгт)\s?[{UP}][\w{AP}-]+(?:[ -][{UP}][\w{AP}-]+)?",
            r"(?i:буд\.|будинок|д\.|дом|корп\.|корпус|секція|под\.|під\.)\s?\d+[\w/-]*",
            r"(?i:кв\.|квартира|оф\.|офіс|офис|прим\.|приміщення|помещение|suite|apt\.?|unit)\s?№?\s?\d+[\w/-]*",
            r"#\s?\d+",
            r"\d{1,4}(?:[-/]?[А-Яа-яA-Za-z])?(?:/\d+)?(?![\d.,])",
            r"(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU|Ontario|Quebec|Québec|NY|CA|TX|FL|WA|IL|MA|NJ)(?:\s[A-Z]\d[A-Z]\s?\d[A-Z]\d|\s\d{5})?",
            r"(?:Toronto|Ottawa|Montreal|Montréal|Vancouver|Calgary|Edmonton|Winnipeg|Mississauga|Hamilton|Kingston|Kanata|Nepean|Gatineau|New York|Chicago|Boston)",
        ]
    )
)
_SEP = re.compile(r"\s*,\s*|\s+")
_BARE_NUM = re.compile(r"(?:\d{1,4}(?:[-/]?[А-Яа-яA-Za-z])?(?:/\d+)?|#\s?\d+)$")


def _extend(text: str, s: int, e: int) -> tuple[int, int]:
    # extend right
    while True:
        m = _SEP.match(text, e)
        if not m or "\n" in m.group(0):
            break
        p = _ADDR_PART.match(text, m.end())
        if not p or p.end() == p.start():
            break
        e = p.end()
    # extend left
    while True:
        prefix = text[max(0, s - 80) : s]
        # the closest part that ends right before a separator
        cand = None
        for p in _ADDR_PART.finditer(prefix):
            tail = prefix[p.end() :]
            if _SEP.fullmatch(tail) and "\n" not in tail and not _BARE_NUM.fullmatch(p.group(0)):
                cand = p
        if cand is None:
            break
        s = max(0, s - 80) + cand.start()
    return s, e


def detect_addresses(text: str) -> list[EntitySpan]:
    out: list[EntitySpan] = []
    for rx in _ADDR_ANCHORS:
        for m in rx.finditer(text):
            s, e = _extend(text, m.start(), m.end())
            val = text[s:e].rstrip(" ,.")
            e = s + len(val)
            out.append(EntitySpan(start=s, end=e, entity_type="ADDRESS", text=val, score=0.86, source="regex"))
    return out


# ── accounts / phones / cards / passports / amounts ─────────────────────────

_ACCOUNT_CUE = re.compile(
    r"(?i:р/р|п/р|р/с|IBAN|рахун(?:ок|ку|ка)|расч[её]тный\s+сч[её]т|сч[её]т[а-я]*|"
    r"account(?:\s+number|\s+no\.?|\s+#)?|acct\.?(?:\s+no\.?)?|transit)"
    r"\s*[:№#]?\s*(?:№\s*)?(?P<value>[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,30}|\d(?:[\d -]{4,30})\d)(?![\w])"
)
_PHONE = [
    re.compile(r"(?<![\w+])(?:\+?38\s?)?\(?0\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"),
    re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s-]?\(?\d{1,4}\)?){2,5}(?!\d)"),
]
_MASKED_CARD = re.compile(r"(?<!\d)\d{4}[ -]?(?:\*{2,4}|X{2,4}|x{2,4})(?:[ -]?(?:\*{2,4}|X{2,4}|x{2,4}))?[ -]?\d{4}(?!\d)|(?<!\d)\d{6}\*{6}\d{4}(?!\d)")
_CARD_CUE = re.compile(
    r"(?i:картк\w*|карт[аиуіы]\w*|card(?:\s+number|\s+no\.?)?)\s*[:№#]?\s*(?P<value>\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}(?:\d{1,3})?)(?!\d)"
)
_PASSPORT = [
    re.compile(r"(?i:паспорт\w*|passport)[^\n\d]{0,30}?(?P<value>(?-i:[А-ЯІЇЄA-Z]{2})\s?№?\s?\d{6}(?!\d))"),
    re.compile(r"(?i:ID-?картк\w*|ID-?карт\w*|ID\s+card|паспорт\w*\s+громадянина\s+України\s+у\s+формі\s+картки)[^\n\d]{0,30}?(?P<value>\d{9})(?!\d)"),
    re.compile(r"(?i:passport)\s*(?:no\.?|number|#)?\s*[:#]?\s*(?P<value>[A-Z]{1,2}\d{6,7})(?!\d)"),
]
_CUR = r"(?:грн\.?|гривень|гривні|гривня|UAH|USD|EUR|CAD|GBP|дол\.?|доларів|долларов|євро|евро|руб\.?|рублей|₴|\$|€|£)"
_NUM = r"\d{1,3}(?:[   .,']\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT = [
    re.compile(rf"(?<![\w.,])(?:{_NUM})\s?{_CUR}(?![\w])"),
    re.compile(rf"(?<![\w])(?:[$€£₴]|CAD\$?|US\$|C\$)\s?(?:{_NUM})(?![\d])"),
]


def detect_misc(text: str, *, amounts: bool = False) -> list[EntitySpan]:
    out: list[EntitySpan] = []
    for m in _ACCOUNT_CUE.finditer(text):
        v = m.group("value").strip()
        if sum(c.isdigit() for c in v) < 6:
            continue
        s = m.start("value")
        out.append(EntitySpan(start=s, end=s + len(v), entity_type="BANK_ACCOUNT", text=v, score=0.9, source="regex"))
    for rx in _PHONE:
        for m in rx.finditer(text):
            digits = sum(c.isdigit() for c in m.group(0))
            if 9 <= digits <= 15:
                out.append(_span(m, "PHONE", 0.8))
    for m in _MASKED_CARD.finditer(text):
        out.append(_span(m, "CREDIT_CARD", 0.9))
    for m in _CARD_CUE.finditer(text):
        out.append(_span(m, "CREDIT_CARD", 0.92, "value"))
    for rx in _PASSPORT:
        for m in rx.finditer(text):
            out.append(_span(m, "PASSPORT", 0.93, "value"))
    if amounts:
        for rx in _AMOUNT:
            for m in rx.finditer(text):
                out.append(_span(m, "AMOUNT", 0.7))
    return out


def detect_all(text: str, *, amounts: bool = False) -> list[EntitySpan]:
    return (
        detect_companies(text)
        + detect_people(text)
        + detect_addresses(text)
        + detect_misc(text, amounts=amounts)
    )


# ── table columns: header text -> category ──────────────────────────────────

_HEADER_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)ЄДРПОУ|ЕДРПОУ|ЕГРПОУ|EDRPOU|ІПН|ИНН|РНОКПП|tax\s*id|код\s+(?:контрагента|отримувача|платника)"), "TAXCODE"),
    (re.compile(r"(?i)IBAN|рахун|сч[её]т|account|р/р|acct"), "ACCOUNT"),
    (re.compile(r"(?i)адрес|address"), "ADDRESS"),
    (re.compile(r"(?i)телефон|phone|тел\.?$|mobile"), "PHONE"),
    (re.compile(r"(?i)e-?mail|пошт|почт"), "EMAIL"),
    (re.compile(r"(?i)дата\s+народж|дата\s+рожд|date\s+of\s+birth|\bdob\b|birth"), "DOB"),
    (re.compile(r"(?i)паспорт|passport"), "PASSPORT"),
    (re.compile(r"(?i)^\s*(?:SIN|NAS)\b"), "SIN"),
    (re.compile(r"(?i)^\s*SSN\b"), "SSN"),
    (re.compile(r"(?i)ПІБ|П\.І\.Б|ФИО|Ф\.И\.О|прізвище|фамилия|ім'я|ім’я|имя|full\s*name|^name$|employee|працівник|сотрудник|клієнт|клиент|client"), "PERSON"),
    (re.compile(r"(?i)контрагент|counterpart|payee|payer|beneficiar|отримувач|одержувач|платник|получател|плательщик|постачальник|поставщик|vendor|supplier|customer|покупець|покупатель|компанія|компания|company|організац|организац"), "PARTY"),
    (re.compile(r"(?i)сума|сумма|amount|дебет|кредит|debit|credit|залишок|остаток|balance|оборот"), "AMOUNT"),
]


def header_category(header: str) -> str | None:
    h = (header or "").strip()
    if not h or len(h) > 60:
        return None
    for rx, cat in _HEADER_RULES:
        if rx.search(h):
            return cat
    return None


def classify_party(value: str) -> str:
    """A counterparty cell: company or a person?"""
    v = value.strip()
    if _C_QUOTED.search(v) or _C_EN.search(v) or _C_BANK.search(v) or _C_BARE.search(v):
        return "ORG"
    if _SOLE_TRADER.search(v):
        return "PERSON"
    toks = v.split()
    if 2 <= len(toks) <= 3 and all(re.fullmatch(rf"{CW}|{CU}|{LW}|{LU}|{INIT}|{INIT_L}", t) for t in toks):
        if any(is_first_name(t) for t in toks) or any(re.search(PATR + "|" + PATR_U, t) for t in toks):
            return "PERSON"
        if any(_STRONG_SURNAME.search(t.casefold()) for t in toks) or any(re.fullmatch(INIT, t) for t in toks):
            return "PERSON"
    return "ORG"
