"""Small built-in gazetteers for the local rule-based NER (no model download needed).

First names only — surnames are recognised by suffix, by context (initials, patronymic,
cue words, legal forms) and by the project dictionary. Lookups go through :func:`stem`,
so declined forms («Івана», «Олені», «Марією») hit the same entry.
"""

from __future__ import annotations

import re

_UK_MALE = """
Андрій Антон Анатолій Арсен Артем Артур Богдан Борис Вадим Валентин Валерій Василь Віктор
Віталій Володимир Вʼячеслав Вячеслав Геннадій Георгій Григорій Давид Данило Денис Дмитро
Євген Едуард Захар Іван Ігор Ілля Йосип Кирило Костянтин Леонід Лев Максим Марко Матвій
Микита Микола Михайло Назар Олег Олександр Олексій Остап Павло Петро Роман Ростислав
Руслан Сергій Святослав Семен Станіслав Степан Тарас Тимофій Тимур Федір Юрій Ярослав
Яків Зиновій Мирослав Орест Любомир Всеволод Гліб Устим Нестор Омелян Харитон Філіп Пилип
"""
_UK_FEMALE = """
Аліна Алла Анастасія Анна Ганна Антоніна Валентина Валерія Вікторія Віра Галина Дарина
Діана Євгенія Єлизавета Жанна Зоя Зоряна Ірина Інна Катерина Кіра Лариса Леся Лідія Лілія
Людмила Любов Марина Марія Маргарита Мирослава Надія Наталія Наталя Ніна Оксана Олена
Ольга Олександра Поліна Раїса Римма Світлана Софія Соломія Тамара Тетяна Уляна Христина
Юлія Яна Ярослава Богдана Василина Віталіна Злата Мілана Мар'яна Маряна Роксолана Емілія
"""
_RU = """
Александр Алексей Анатолий Андрей Антон Аркадий Артём Артем Борис Вадим Валентин Валерий
Василий Виктор Виталий Владимир Владислав Вячеслав Геннадий Георгий Григорий Даниил Денис
Дмитрий Евгений Егор Иван Игорь Илья Кирилл Константин Леонид Максим Михаил Никита Николай
Олег Павел Пётр Петр Роман Руслан Семён Семен Сергей Станислав Степан Тимур Фёдор Федор
Юрий Ярослав Яков Анастасия Анна Валентина Валерия Вера Виктория Галина Дарья Евгения
Екатерина Елена Елизавета Ирина Инна Карина Кристина Ксения Лариса Лидия Любовь Людмила
Марина Мария Надежда Наталья Нина Оксана Ольга Полина Светлана София Софья Тамара Татьяна
Ульяна Юлия Яна Алёна Алена Василиса Зинаида Маргарита Олеся Таисия Эльвира Эдуард
"""
_EN = """
James John Robert Michael William David Richard Joseph Thomas Charles Christopher Daniel
Matthew Anthony Mark Donald Steven Paul Andrew Joshua Kenneth Kevin Brian George Timothy
Ronald Edward Jason Jeffrey Ryan Jacob Gary Nicholas Eric Jonathan Stephen Larry Justin
Scott Brandon Benjamin Samuel Gregory Alexander Frank Patrick Raymond Jack Dennis Jerry
Tyler Aaron Jose Adam Nathan Henry Douglas Zachary Peter Kyle Noah Ethan Jeremy Walter
Christian Keith Roger Terry Austin Sean Gerald Carl Harold Dylan Arthur Lawrence Jordan
Jesse Bryan Billy Bruce Gabriel Joe Logan Alan Juan Albert Willie Elijah Wayne Randy Vincent
Liam Lucas Oliver Owen Luke Isaac Ian Graham Colin Neil Simon Martin Philip Maxim Maksym
Mary Patricia Jennifer Linda Elizabeth Barbara Susan Jessica Sarah Karen Lisa Nancy Betty
Sandra Margaret Ashley Kimberly Emily Donna Michelle Carol Amanda Melissa Deborah Stephanie
Dorothy Rebecca Sharon Laura Cynthia Amy Kathleen Angela Shirley Brenda Emma Anna Pamela
Nicole Samantha Katherine Christine Helen Debra Rachel Carolyn Janet Maria Catherine Heather
Diane Olivia Julie Joyce Victoria Ruth Virginia Lauren Kelly Christina Joan Evelyn Judith
Andrea Hannah Megan Cheryl Jacqueline Martha Madison Teresa Gloria Sara Janice Ann Kathryn
Abigail Sophia Frances Jean Alice Judy Isabella Julia Grace Amber Denise Danielle Marilyn
Beverly Charlotte Natalie Theresa Diana Brittany Doris Kayla Alexis Lori Marie Chloe Claire
Sophie Ava Mia Zoe Lily Ella Avery Harper Aria Nora Riley Leah Hazel Violet Aurora
"""

_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
        "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
        "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
        "ю": "iu", "я": "ia", "'": "", "’": "", "ʼ": "", "ё": "e", "ы": "y", "э": "e",
        "ъ": "",
    }
)
_TRANSLIT_RU = str.maketrans({"г": "g", "и": "i", "й": "y", "х": "kh", "ю": "yu", "я": "ya"})

_ENDINGS = sorted(
    "ові еві єві ою ею єю ого ому ими ій ої ий им ом ем єм ах ям ам ым ой ей ую а я у ю і и е о ї ь й ы".split(),
    key=len,
    reverse=True,
)
_APOS = re.compile(r"['’ʼ`]")


def norm_token(token: str) -> str:
    t = _APOS.sub("", token.casefold()).replace("ё", "е")
    return t


def stem(token: str) -> str:
    """Strip one Ukrainian/Russian case ending (keeps a stem of >= 3 letters)."""
    t = norm_token(token)
    if not t or not ("а" <= t[0] <= "я" or t[0] in "іїєґ"):
        # Latin: drop possessive only
        return t[:-2] if t.endswith("'s") else t
    for e in _ENDINGS:
        if t.endswith(e) and len(t) - len(e) >= 3:
            return t[: -len(e)]
    return t


def translit(token: str, ru: bool = False) -> str:
    t = norm_token(token)
    if ru:
        t = t.translate(_TRANSLIT_RU)
    return t.translate(_TRANSLIT)


def _build() -> tuple[frozenset[str], frozenset[str]]:
    cyr_words = (_UK_MALE + _UK_FEMALE + _RU).split()
    cyr = {stem(w) for w in cyr_words}
    lat = {w.casefold() for w in _EN.split()}
    for w in _UK_MALE.split() + _UK_FEMALE.split():
        lat.add(translit(w))
    for w in _RU.split():
        lat.add(translit(w, ru=True))
        lat.add(translit(w))
    lat.discard("")
    return frozenset(cyr), frozenset(lat)


FIRST_NAME_STEMS, FIRST_NAMES_LATIN = _build()
NOMINATIVE_FIRST = frozenset(norm_token(w) for w in (_UK_MALE + _UK_FEMALE + _RU + _EN).split())


def is_first_name(token: str) -> bool:
    t = norm_token(token)
    if not t:
        return False
    if "a" <= t[0] <= "z":
        return t in FIRST_NAMES_LATIN
    return stem(token) in FIRST_NAME_STEMS


# Capitalised words that look like a name part but never are (stems / casefolded).
NOT_NAME_STEMS = frozenset(
    stem(w)
    for w in """
    Україна Україні України Київ Києва Львів Одеса Харків Дніпро Канада Торонто Оттава Монреаль
    Банк Договір Договору Рахунок Сума Дата Отримувач Платник Директор Бухгалтер Товариство
    Приватне Акціонерне Державне Комунальне Публічне Обмеженою Відповідальністю Додаток Акт
    Виписка Залишок Період Призначення Оплата Податок Послуги Січень Лютий Березень Квітень
    Травень Червень Липень Серпень Вересень Жовтень Листопад Грудень Понеділок Вівторок
    Россия Москва Договор Счет Сумма Итого Всего Общество Ограниченной Ответственностью
    Январь Февраль Март Апрель Май Июнь Июль Август Сентябрь Октябрь Ноябрь Декабрь
    Слава Героям Нова Пошта Укрпошта Мінфін Мінекономіки Національний Державна Податкова
    Служба Казначейство Реєстр Єдиний Аудитор Аудиторський Висновок Звіт Баланс
    """.split()
) | frozenset(
    w.casefold()
    for w in """
    Bank Agreement Invoice Statement Account Balance Total Date Amount Payment Services Tax
    Canada Ontario Toronto Ottawa Street Avenue Road Suite January February March April May
    June July August September October November December Monday Tuesday Wednesday Thursday
    Friday Saturday Sunday Inc Ltd Corp LLC LLP Limited Company Royal Dear Sincerely Regards
    The This That From Page Report Summary Audit Auditor Opinion Note Notes Schedule
    """.split()
)
