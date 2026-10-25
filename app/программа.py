"""
Программа выполнения заданий.

Запуск:  python программа.py
Откроется окно со списком заданий из папки "Тестовые задания/Задания".
Выбираешь задание, решаешь, программа сама проверяет и говорит "верно" или "неверно".
Готовые ответы она не показывает.

Куда что пишется (папка "Тестовые задания/Результаты/<название задания>"):
  лог.txt        - каждая попытка и ошибки (пишется всегда);
  прогресс.json  - что уже решено (чтобы можно было закрыть и продолжить позже);
  результат.txt  - итог (появляется, когда решены все задачи задания).
Ошибки самой программы пишутся в "Результаты/программа_лог.txt".

Как работает: программа запускает маленький сервер только на твоём компьютере
(адрес 127.0.0.1, доступ по секретному токену) и открывает окно Edge/Chrome в режиме
приложения. Интернет не нужен, ничего не устанавливается. Когда закроешь окно,
программа сама завершится примерно через пару минут.
"""
import ast
import json
import logging
import operator
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------- Пути ----------
ЗАМОРОЖЕНО = bool(getattr(sys, "frozen", False))  # True, когда это готовый exe


def папка_документов():
    """Папка «Документы» пользователя (учитывает перенос в OneDrive и т. п.)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            guid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            путь = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(путь)) == 0:
                результат = Path(путь.value)
                ctypes.windll.ole32.CoTaskMemFree(путь)
                return результат
        except Exception:
            pass
    документы = Path.home() / "Documents"
    return документы if документы.exists() else Path.home()


if ЗАМОРОЖЕНО:
    ПАПКА_ПРОГРАММЫ = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))  # внутри exe лежат интерфейс и задания
    КОРЕНЬ = Path(os.environ.get("STUDY_ROOT") or (папка_документов() / "Практика Python"))
else:
    ПАПКА_ПРОГРАММЫ = Path(__file__).resolve().parent
    КОРЕНЬ = Path(os.environ.get("STUDY_ROOT") or ПАПКА_ПРОГРАММЫ.parent)  # "Тестовые задания"
ПАПКА_ЗАДАНИЙ = КОРЕНЬ / "Задания"
ПАПКА_РЕЗУЛЬТАТОВ = КОРЕНЬ / "Результаты"
ФАЙЛ_ИНТЕРФЕЙСА = ПАПКА_ПРОГРАММЫ / "интерфейс.html"

ОПЕРАТОРЫ = {
    "+": ast.Add, "-": ast.Sub, "*": ast.Mult, "/": ast.Div,
    "//": ast.FloorDiv, "%": ast.Mod, "**": ast.Pow,
}

БЛОКИРОВКА = threading.Lock()
КОНТЕКСТ = threading.local()  # язык текущего запроса (ru или en), приходит из окна в заголовке X-Lang


def язык():
    return getattr(КОНТЕКСТ, "lang", "ru")


def Л(ру, ан):
    """Возвращает текст на языке текущего запроса."""
    return ан if язык() == "en" else ру

ТОКЕН = secrets.token_urlsafe(24)
ПОРТ = 0
ПОСЛЕДНИЙ_ПИНГ = 0.0
ВРЕМЯ_СТАРТА = time.time()


# ====================== Чтение заданий ======================
def проверить_формат(данные):
    """Проверяет, что файл задания устроен правильно. При проблеме бросает ValueError."""
    if not isinstance(данные, dict):
        raise ValueError(Л("файл должен быть JSON-объектом", "the file must be a JSON object"))
    if not isinstance(данные.get("title"), str) or not данные["title"].strip():
        raise ValueError(Л('нет поля "title" (название задания)', 'missing field "title" (the name of the task set)'))
    if данные.get("lang", "ru") not in ("ru", "en"):
        raise ValueError(Л('"lang" должно быть "ru" или "en"', '"lang" must be "ru" or "en"'))
    if not isinstance(данные.get("general", ""), str):
        raise ValueError('"general" (общее для вкладки «О задании») должно быть строкой')
    вопросы = данные.get("questions")
    if not isinstance(вопросы, list) or not вопросы:
        raise ValueError(Л('нет списка "questions" (задач)', 'missing list "questions"'))
    for номер, вопрос in enumerate(вопросы, start=1):
        if not isinstance(вопрос, dict) or not isinstance(вопрос.get("text"), str):
            raise ValueError(Л(f"задача {номер}: нет текста условия", f"question {номер}: no task text"))
        тип = вопрос.get("type")
        if тип == "expression":
            if type(вопрос.get("expected")) not in (int, float, str):
                raise ValueError(f'задача {номер}: "expected" должно быть числом или строкой')
            for оператор in вопрос.get("required", []):
                if оператор not in ОПЕРАТОРЫ:
                    raise ValueError(Л(f"задача {номер}: неизвестный оператор {оператор!r}", f"question {номер}: unknown operator {оператор!r}"))
        elif тип == "text":
            принимаемые = вопрос.get("accepted")
            if not isinstance(принимаемые, list) or not принимаемые or not all(isinstance(x, str) for x in принимаемые):
                raise ValueError(f'задача {номер}: "accepted" должно быть непустым списком строк')
        elif тип == "code":
            if not isinstance(вопрос.get("expected_output"), str):
                raise ValueError(f'задача {номер}: "expected_output" должно быть строкой')
            for поле in ("inputs", "required_text"):
                значение = вопрос.get(поле, [])
                if not isinstance(значение, list) or not all(isinstance(x, str) for x in значение):
                    raise ValueError(Л(f'задача {номер}: "{поле}" должно быть списком строк', f'question {номер}: "{поле}" must be a list of strings'))
        elif тип == "script":
            проверить_формат_скрипта(номер, вопрос)
        elif тип == "shell":
            if not any(к in вопрос for к in ("expected", "expected_vars", "expected_error")):
                raise ValueError(f'задача {номер}: нужно "expected", "expected_vars" или "expected_error"')
            if not isinstance(вопрос.get("expected_vars", {}), dict):
                raise ValueError(f'задача {номер}: "expected_vars" должно быть словарём')
            for поле in ("uses_names", "required"):
                значение = вопрос.get(поле, [])
                if not isinstance(значение, list) or not all(isinstance(x, str) for x in значение):
                    raise ValueError(Л(f'задача {номер}: "{поле}" должно быть списком строк', f'question {номер}: "{поле}" must be a list of strings'))
            for оператор in вопрос.get("required", []):
                if оператор not in ОПЕРАТОРЫ:
                    raise ValueError(Л(f"задача {номер}: неизвестный оператор {оператор!r}", f"question {номер}: unknown operator {оператор!r}"))
        else:
            raise ValueError(Л(f"задача {номер}: неизвестный тип {тип!r} (нужен expression, text, code, script или shell)", f"question {номер}: unknown type {тип!r} (use script, or expression, text, code, shell)"))


def проверить_формат_скрипта(номер, вопрос):
    """Проверка полей задачи типа script (скрипт с готовыми частями до и после кода пользователя)."""
    if not isinstance(вопрос.get("expected_output"), str) and not isinstance(вопрос.get("expected_error"), str):
        raise ValueError(Л(f'задача {номер}: нужно "expected_output" или "expected_error" (строка)', f'question {номер}: "expected_output" or "expected_error" (a string) is required'))
    for поле in ("before", "after", "starter", "placeholder"):
        if not isinstance(вопрос.get(поле, ""), str):
            raise ValueError(Л(f'задача {номер}: "{поле}" должно быть строкой', f'question {номер}: "{поле}" must be a string'))
    минимум = вопрос.get("min_lines", 3)
    if type(минимум) is not int or not 1 <= минимум <= 30:
        raise ValueError(f'задача {номер}: "min_lines" должно быть целым числом от 1 до 30')
    for поле in ("inputs", "required_text", "uses_names", "assigns", "required"):
        значение = вопрос.get(поле, [])
        if not isinstance(значение, list) or not all(isinstance(x, str) for x in значение):
            raise ValueError(Л(f'задача {номер}: "{поле}" должно быть списком строк', f'question {номер}: "{поле}" must be a list of strings'))
    for оператор in вопрос.get("required", []):
        if оператор not in ОПЕРАТОРЫ:
            raise ValueError(Л(f"задача {номер}: неизвестный оператор {оператор!r}", f"question {номер}: unknown operator {оператор!r}"))
    константы = вопрос.get("consts", {})
    if not isinstance(константы, dict) or not all(isinstance(к, str) and type(з) in (int, float, str) for к, з in константы.items()):
        raise ValueError(f'задача {номер}: "consts" должно быть словарём «переменная: значение (число или текст)»')
    повторы = вопрос.get("min_assigns", {})
    if not isinstance(повторы, dict) or not all(isinstance(к, str) and type(з) is int for к, з in повторы.items()):
        raise ValueError(f'задача {номер}: "min_assigns" должно быть словарём «переменная: сколько раз ей присваивают»')
    предел = вопрос.get("max_text", {})
    if not isinstance(предел, dict) or not all(isinstance(к, str) and type(з) is int for к, з in предел.items()):
        raise ValueError(f'задача {номер}: "max_text" должно быть словарём «текст: сколько раз можно»')


def загрузить_задание(путь):
    данные = json.loads(путь.read_text(encoding="utf-8-sig"))
    проверить_формат(данные)
    данные["id"] = путь.stem
    return данные


def ключ_сортировки(текст):
    return [int(часть) if часть.isdigit() else часть.lower() for часть in re.split(r"(\d+)", текст)]


def все_задания():
    """Возвращает (словарь id -> задание, словарь id -> текст ошибки)."""
    ПАПКА_ЗАДАНИЙ.mkdir(parents=True, exist_ok=True)
    задания, ошибки = {}, {}
    for путь in sorted(ПАПКА_ЗАДАНИЙ.glob("*.json"), key=lambda p: ключ_сортировки(p.stem)):
        try:
            задания[путь.stem] = загрузить_задание(путь)
        except Exception as ошибка:  # файл битый - показываем, но не падаем
            ошибки[путь.stem] = str(ошибка)
            logging.warning("не удалось прочитать задание %s: %s", путь.name, ошибка)
    return задания, ошибки


# ====================== Прогресс, лог, результат ======================
def папка_результатов(id_задания):
    папка = ПАПКА_РЕЗУЛЬТАТОВ / id_задания
    папка.mkdir(parents=True, exist_ok=True)
    return папка


def записать_лог(id_задания, уровень, текст):
    with open(папка_результатов(id_задания) / "лог.txt", "a", encoding="utf-8") as файл:
        файл.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {уровень} | {текст}\n")


def прочитать_прогресс(id_задания):
    путь = ПАПКА_РЕЗУЛЬТАТОВ / id_задания / "прогресс.json"
    try:
        данные = json.loads(путь.read_text(encoding="utf-8"))
        return {"solved": dict(данные.get("solved", {})), "attempts": dict(данные.get("attempts", {}))}
    except (OSError, ValueError):
        return {"solved": {}, "attempts": {}}


def сохранить_прогресс(id_задания, прогресс):
    путь = папка_результатов(id_задания) / "прогресс.json"
    путь.write_text(json.dumps(прогресс, ensure_ascii=False, indent=1), encoding="utf-8")


def публичный_вид(задание):
    """То, что видит окно. Правильные ответы сюда НЕ попадают."""
    прогресс = прочитать_прогресс(задание["id"])
    всего = len(задание["questions"])
    решено = {к: v for к, v in прогресс["solved"].items() if к.isdigit() and int(к) < всего}
    return {
        "id": задание["id"],
        "title": задание["title"],
        "topic": задание.get("topic", ""),
        "intro": задание.get("intro", ""),
        "general": задание.get("general", ""),
        "help": задание.get("help", []),
        "questions": [
            {"type": в["type"], "text": в["text"], "starter": в.get("starter", ""), "about": в.get("about", ""),
             "before": в.get("before", ""), "after": в.get("after", ""),
             "min_lines": в.get("min_lines", 3), "placeholder": в.get("placeholder", "")}
            for в in задание["questions"]
        ],
        "solved": решено,
        "passed": len(решено) == всего,
    }


def записать_результат(задание, прогресс):
    попытки = [str(прогресс["solved"].get(str(и), "?")) for и in range(len(задание["questions"]))]
    строки = [
        f'{задание["id"]}. {задание["title"]}: ПРОЙДЕНО',
        f"Дата: {datetime.now():%Y-%m-%d %H:%M}",
        "Попыток по задачам: " + ", ".join(f"{н}: {п}" for н, п in enumerate(попытки, start=1)),
    ]
    (папка_результатов(задание["id"]) / "результат.txt").write_text("\n".join(строки) + "\n", encoding="utf-8")


# ====================== Проверка ответов ======================
ФУНКЦИИ_ОПЕРАТОРОВ = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
ПРЕДЕЛ_СТРОКИ = 10000  # длиннее строку не строим, чтобы случайно не зависнуть


def вычислить(узел, использованные):
    """Считает дерево выражения сам, без eval. Разрешены числа, строки и математические операторы."""
    if isinstance(узел, ast.Constant) and type(узел.value) in (int, float, str):
        return узел.value
    if isinstance(узел, ast.UnaryOp) and isinstance(узел.op, (ast.USub, ast.UAdd)):
        значение = вычислить(узел.operand, использованные)
        if isinstance(значение, str):
            raise TypeError("bad operand type for unary operator: 'str'")
        return -значение if isinstance(узел.op, ast.USub) else +значение
    if isinstance(узел, ast.BinOp) and type(узел.op) in ФУНКЦИИ_ОПЕРАТОРОВ:
        левое = вычислить(узел.left, использованные)
        правое = вычислить(узел.right, использованные)
        использованные.add(type(узел.op))
        if isinstance(узел.op, ast.Pow) and not (isinstance(правое, (int, float)) and abs(правое) <= 100):
            raise ValueError("слишком большая степень")
        if isinstance(узел.op, ast.Mult):
            for строка, число in ((левое, правое), (правое, левое)):
                if isinstance(строка, str) and isinstance(число, int) and len(строка) * max(число, 0) > ПРЕДЕЛ_СТРОКИ:
                    raise ValueError("слишком длинная строка")
        return ФУНКЦИИ_ОПЕРАТОРОВ[type(узел.op)](левое, правое)
    raise ValueError("допустимы только числа, строки и математические операторы")


def посчитать(текст):
    """Безопасно считает выражение. Возвращает (результат, набор использованных операторов)."""
    дерево = ast.parse(текст.strip(), mode="eval")  # SyntaxError, если запись неверная
    использованные = set()
    return вычислить(дерево.body, использованные), использованные


def показать(значение):
    """Как показать пользователю результат: строки в кавычках, чтобы были видны пробелы."""
    return repr(значение) if isinstance(значение, str) else str(значение)


def проверить_выражение(вопрос, ответ):
    if not ответ.strip():
        return {"ok": False, "message": "Введи выражение.", "reason": "пусто"}
    ждём_ошибку = вопрос.get("expected_error")
    try:
        результат, использованные = посчитать(ответ)
    except SyntaxError:
        return {"ok": False, "reason": "SyntaxError",
                "message": "Неверно: Python не понял запись (SyntaxError). Проверь, как записано выражение."}
    except ZeroDivisionError:
        return {"ok": False, "reason": "деление на ноль", "message": "Неверно: на ноль делить нельзя."}
    except TypeError as ошибка:
        if ждём_ошибку == "TypeError":
            return {"ok": True, "reason": "ВЕРНО", "result": f"TypeError: {ошибка}",
                    "message": "Верно! Python не смог это выполнить и показал ошибку TypeError."}
        return {"ok": False, "reason": f"TypeError: {ошибка}",
                "message": f"Python не смог выполнить это (TypeError): {ошибка}"}
    except ValueError as ошибка:
        return {"ok": False, "reason": f"ValueError: {ошибка}", "message": f"Неверно: {ошибка}."}
    if ждём_ошибку:
        return {"ok": False, "reason": "ошибки не было", "result": показать(результат),
                "message": "Неверно: Python выполнил это без ошибки. Нужно такое выражение, которое Python выполнить не сможет."}
    нужные = {ОПЕРАТОРЫ[о] for о in вопрос.get("required", [])}
    ожидается = вопрос["expected"]
    if isinstance(ожидается, str):
        значение_верно = isinstance(результат, str) and результат == ожидается
    else:
        значение_верно = isinstance(результат, (int, float)) and abs(результат - ожидается) < 1e-9
    показ = показать(результат)  # что получилось у пользователя (правильный ответ не показываем)
    if значение_верно and нужные <= использованные:
        return {"ok": True, "message": "Верно!", "reason": "ВЕРНО", "result": показ}
    if значение_верно:
        return {"ok": False, "reason": "значение верно, операторы не те", "result": показ,
                "message": "Результат верный, но программа ждёт вычисление, а не готовое значение. "
                           "Запиши, что и чем нужно посчитать, и используй подходящие операторы (они в справке)."}
    return {"ok": False, "reason": "неверный результат", "result": показ,
            "message": "Неверно. Попробуй ещё раз."}


def проверить_текст(вопрос, ответ):
    if not ответ.strip():
        return {"ok": False, "message": "Введи ответ.", "reason": "пусто"}
    принимаемые = {a.strip().casefold() for a in вопрос["accepted"]}
    if ответ.strip().casefold() in принимаемые:
        return {"ok": True, "message": "Верно!", "reason": "ВЕРНО"}
    return {"ok": False, "message": "Неверно. Попробуй ещё раз.", "reason": "неверный ответ"}


def команда_скрипта(скрипт, *аргументы):
    """Команда для запуска скрипта. В готовом exe Python отдельно нет, поэтому exe запускает сам себя в особом режиме."""
    if ЗАМОРОЖЕНО:
        return [sys.executable, "--выполнить", str(скрипт), *map(str, аргументы)]
    return [интерпретатор(), "-I", "-X", "utf8", str(скрипт), *map(str, аргументы)]


ОКРУЖЕНИЕ_ДОЧЕРНЕГО = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def интерпретатор():
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def запустить_процесс(код, ввод):
    """Запускает код в отдельном процессе. Возвращает (stdout, stderr, код_возврата); код_возврата None, если не уложился во время."""
    флаги = 0x08000000 if os.name == "nt" else 0  # не показывать чёрное окно в Windows
    with tempfile.TemporaryDirectory() as папка:
        файл = Path(папка) / "решение.py"
        файл.write_text(код, encoding="utf-8")
        try:
            процесс = subprocess.run(
                команда_скрипта(файл),
                input="\n".join(ввод) + "\n" if ввод else "",
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=5, cwd=папка, creationflags=флаги, env=ОКРУЖЕНИЕ_ДОЧЕРНЕГО,
            )
        except subprocess.TimeoutExpired:
            return "", "", None
    return процесс.stdout, процесс.stderr, процесс.returncode


def текст_таймаута():
    return Л("Программа работала дольше 5 секунд. Возможно, цикл не заканчивается.", "The program ran for more than 5 seconds. Maybe a loop never ends.")


def запустить_код(код, ввод):
    """Запускает код пользователя в отдельном процессе. Возвращает (stdout, текст_ошибки)."""
    вывод, ошибки, возврат = запустить_процесс(код, ввод)
    if возврат is None:
        return "", текст_таймаута()
    ошибка = ""
    if возврат != 0:
        строки = [с for с in ошибки.strip().splitlines() if с.strip()]
        ошибка = строки[-1] if строки else "программа завершилась с ошибкой"
    return вывод, ошибка


def нормализовать(текст):
    return [строка.rstrip() for строка in текст.strip().splitlines()]


def проверить_код(вопрос, код):
    if not код.strip():
        return {"ok": False, "message": "Напиши код.", "reason": "пусто"}
    for фрагмент in вопрос.get("required_text", []):
        if фрагмент not in код:
            return {"ok": False, "reason": f"нет «{фрагмент}»",
                    "message": Л(f"Неверно: в решении должно быть «{фрагмент}».", f"Incorrect: the solution must contain \"{фрагмент}\".")}
    вывод, ошибка = запустить_код(код, вопрос.get("inputs", []))
    if ошибка:
        return {"ok": False, "reason": f"ошибка: {ошибка}", "output": вывод,
                "message": f"Программа остановилась с ошибкой: {ошибка}"}
    if нормализовать(вывод) == нормализовать(вопрос["expected_output"]):
        return {"ok": True, "message": "Верно!", "reason": "ВЕРНО", "output": вывод}
    return {"ok": False, "reason": "неверный вывод", "output": вывод,
            "message": "Неверно: программа вывела не то, что требуется."}


# ---------- задачи типа script: настоящий скрипт с готовыми частями ----------
ФАЙЛ_В_ОШИБКЕ = re.compile(r'^\s*File "(.*?)", line (\d+)(?:, in (.*))?$')


def собрать_скрипт(вопрос, ответ):
    """Склеивает готовую часть «до», код пользователя и готовую часть «после».
    Возвращает (текст_скрипта, сколько_строк_до, сколько_строк_пользователя). Нумерация строк в окне и в ошибках совпадает."""
    до = вопрос.get("before", "").rstrip("\n")
    после = вопрос.get("after", "").rstrip("\n")
    строки_пользователя = ответ.split("\n")
    while len(строки_пользователя) < вопрос.get("min_lines", 3):
        строки_пользователя.append("")
    части = ([до] if до else []) + ["\n".join(строки_пользователя)] + ([после] if после else [])
    return "\n".join(части) + "\n", (до.count("\n") + 1 if до else 0), len(строки_пользователя)


def понятная_ошибка(текст_ошибки, скрипт, до, строк_пользователя):
    """Оставляет из сообщения Python главное: номер строки, саму строку и название ошибки. Внутренние пути убираются."""
    строки_кода = скрипт.split("\n")
    итог, заключение = [], []
    for строка in текст_ошибки.splitlines():
        if строка.startswith("Traceback (most recent call last)"):
            итог.append(строка)
        elif строка[:1] in (" ", "\t"):
            совпало = ФАЙЛ_В_ОШИБКЕ.match(строка)
            if совпало and совпало.group(1).replace("\\", "/").endswith("/решение.py"):
                номер = int(совпало.group(2))
                место = f", in {совпало.group(3)}" if совпало.group(3) else ""
                готовая = номер <= до or номер > до + строк_пользователя
                итог.append(f'  File "{Л("решение.py", "solution.py")}", line {номер}{место}' + ("   <- " + Л("готовая часть", "ready part") if готовая else ""))
                if 1 <= номер <= len(строки_кода):
                    итог.append("    " + строки_кода[номер - 1].strip())
        elif строка.strip():
            заключение.append(строка)
    if any(с.endswith(("<- готовая часть", "<- ready part")) for с in итог):
        заключение.append(Л("Ошибка найдена в готовой части, а её менять нельзя. Причина в коде рядом: проверь свои строки.",
                            "The error was found in the ready part, which cannot be changed. The cause is in nearby code: check your own lines."))
    return "\n".join(итог + заключение)


def название_ошибки(текст_ошибки):
    """Название ошибки из последней строки сообщения Python, например NameError."""
    строки = [с for с in текст_ошибки.strip().splitlines() if с.strip() and not с.startswith((" ", "\t"))]
    if not строки:
        return ""
    совпало = re.match(r"([A-Za-z_][\w.]*)", строки[-1])
    return совпало.group(1).split(".")[-1] if совпало else ""


def выполнить_скрипт_задачи(вопрос, ответ):
    """Запускает скрипт задачи. Возвращает словарь: console, error, stdout, stderr, type (название ошибки)."""
    скрипт, до, польз = собрать_скрипт(вопрос, ответ)
    вывод, ошибки, возврат = запустить_процесс(скрипт, вопрос.get("inputs", []))
    if возврат is None:
        return {"console": вывод + текст_таймаута(), "error": True, "stdout": вывод, "type": "Timeout"}
    if возврат == 0:
        return {"console": вывод if вывод.strip() else Л("(программа ничего не вывела)", "(the program printed nothing)"), "error": False,
                "stdout": вывод, "type": ""}
    красиво = понятная_ошибка(ошибки, скрипт, до, польз) or Л("программа завершилась с ошибкой", "the program ended with an error")
    консоль = вывод + ("\n" if вывод and not вывод.endswith("\n") else "") + красиво
    return {"console": консоль, "error": True, "stdout": вывод, "type": название_ошибки(ошибки)}


def имена_в_коде(код, ответ_пользователя):
    """Разбирает код пользователя (если не вышло, то весь скрипт). Возвращает дерево или None."""
    for текст in (ответ_пользователя, код):
        try:
            return ast.parse(текст)
        except SyntaxError:
            continue
    return None


def проверить_скрипт(вопрос, ответ):
    if not ответ.strip():
        return {"ok": False, "message": Л("Напиши код.", "Write some code."), "reason": "пусто", "console": Л("Напиши код.", "Write some code."), "console_error": True}
    for фрагмент in вопрос.get("required_text", []):
        if фрагмент not in ответ:
            return {"ok": False, "reason": f"нет «{фрагмент}»",
                    "message": Л(f"Неверно: в решении должно быть «{фрагмент}».", f"Incorrect: the solution must contain \"{фрагмент}\".")}
    результат = выполнить_скрипт_задачи(вопрос, ответ)
    итог = {"console": результат["console"], "console_error": bool(результат["error"])}
    ждём_ошибку = вопрос.get("expected_error")
    if ждём_ошибку:
        if результат["type"] == ждём_ошибку:
            итог.update(ok=True, reason="ВЕРНО", message=Л(f"Верно! Python остановился и показал ошибку {ждём_ошибку}.", f"Correct! Python stopped and showed the error {ждём_ошибку}."))
        elif результат["error"]:
            итог.update(ok=False, reason=f"другая ошибка {результат['type']}",
                        message=Л(f"Неверно: ошибка есть, но другая ({результат['type']}). Нужна ошибка, о которой сказано в условии.", f"Incorrect: there is an error, but a different one ({результат['type']}). The task asks for a specific error."))
        else:
            итог.update(ok=False, reason="ошибки не было",
                        message=Л("Неверно: Python выполнил код без ошибки. Нужен код, который Python выполнить не сможет.", "Incorrect: Python ran the code without an error. The task needs code that Python cannot run."))
        return итог
    if результат["error"]:
        итог.update(ok=False, reason=f"ошибка {результат['type']}",
                    message=Л("Python остановился с ошибкой. Прочитай последнюю строку в консоли: в ней написано, что именно не так.", "Python stopped with an error. Read the last line in the console: it says what is wrong."))
        return итог
    if нормализовать(результат["stdout"]) != нормализовать(вопрос["expected_output"]):
        сообщение = Л("Неверно: программа вывела не то, что требуется.", "Incorrect: the program printed something different from what is required.")
        if re.search(r"\bprint\b", ответ) and "print" in (вопрос.get("before", "") + вопрос.get("after", "")):
            сообщение += Л(" Вывод в этом задании уже подготовлен в готовой части, писать свой `print` не нужно.", " The output in this task is already prepared in the ready part, so there is no need to write your own `print`.")
        итог.update(ok=False, reason="неверный вывод", message=сообщение)
        return итог
    дерево = имена_в_коде(собрать_скрипт(вопрос, ответ)[0], ответ)
    if дерево is not None:
        созданные = {у.id for у in ast.walk(дерево) if isinstance(у, ast.Name) and isinstance(у.ctx, ast.Store)}
        загруженные = {у.id for у in ast.walk(дерево) if isinstance(у, ast.Name) and isinstance(у.ctx, ast.Load)}
        использованные = {type(у.op) for у in ast.walk(дерево) if isinstance(у, ast.BinOp)}
    else:
        созданные = загруженные = использованные = set()
    for имя in вопрос.get("assigns", []):
        if имя not in созданные:
            итог.update(ok=False, reason=f"нет переменной {имя}", message=Л(f"Неверно: переменная {имя} не создана.", f"Incorrect: the variable {имя} was not created."))
            return итог
    for имя, значение in вопрос.get("consts", {}).items():
        найдено = any(
            isinstance(у, ast.Assign) and isinstance(у.value, ast.Constant)
            and type(у.value.value) is type(значение) and у.value.value == значение
            and any(isinstance(т, ast.Name) and т.id == имя for т in у.targets)
            for у in (ast.walk(дерево) if дерево is not None else []))
        if not найдено:
            итог.update(ok=False, reason=f"{имя}: нет значения из условия",
                        message=Л(f"Вывод верный, но в переменную {имя} нужно положить значение из условия, записанное так, как в условии: число без кавычек, текст в кавычках.",
                                  f"The output is correct, but the variable {имя} must be given the value from the task, written as in the task: a number without quotes, text in quotes."))
            return итог
    for имя, сколько in вопрос.get("min_assigns", {}).items():
        раз = sum(1 for у in (ast.walk(дерево) if дерево is not None else []) if isinstance(у, ast.Name) and isinstance(у.ctx, ast.Store) and у.id == имя)
        if раз < сколько:
            итог.update(ok=False, reason=f"{имя}: присвоено {раз} раз",
                        message=Л(f"Вывод верный, но по условию в переменную {имя} нужно положить значение {сколько} раза подряд: сначала первое, потом замена. Сейчас в коде таких строк меньше.",
                                  f"The output is correct, but the task needs the variable {имя} to be assigned a value {сколько} times in a row: first one value, then a replacement. There are fewer such lines in the code."))
            return итог
    for имя in вопрос.get("uses_names", []):
        if имя not in загруженные:
            итог.update(ok=False, reason=f"{имя} не используется",
                        message=Л(f"Вывод верный, но переменная {имя} должна участвовать в вычислении, а не быть набрана готовым значением.",
                                  f"The output is correct, but the variable {имя} must take part in the calculation instead of being typed as a ready value."))
            return итог
    нужные = {ОПЕРАТОРЫ[о] for о in вопрос.get("required", [])}
    if not нужные <= использованные:
        итог.update(ok=False, reason="операторы не те",
                    message=Л("Вывод верный, но программа ждёт вычисление, а не готовое значение. "
                              "Запиши, что и чем нужно посчитать, и используй подходящие знаки (они в справке).",
                              "The output is correct, but the program expects a calculation, not a ready value. "
                              "Write what to calculate and use the right operators (they are in the help)."))
        return итог
    for фрагмент, предел in вопрос.get("max_text", {}).items():
        if ответ.count(фрагмент) > предел:
            итог.update(ok=False, reason=f"«{фрагмент}» чаще {предел} раз",
                        message=Л(f"Вывод верный, но нарушено ограничение из условия: «{фрагмент}» можно написать не больше {предел} раз.", f"The output is correct, but a rule of the task is broken: \"{фрагмент}\" may be written at most {предел} times."))
            return итог
    итог.update(ok=True, reason="ВЕРНО", message=Л("Верно!", "Correct!"))
    return итог


ЗАПУСКАТЕЛЬ_ОБОЛОЧКИ = r"""
import ast, io, json, sys, contextlib
src = open(sys.argv[1], encoding="utf-8").read()
real = sys.stdout
def out(d):
    real.write(json.dumps(d, ensure_ascii=False)); real.flush()
try:
    tree = ast.parse(src)
except SyntaxError as e:
    out({"transcript": ">>> " + (src.splitlines()[e.lineno - 1] if e.lineno and e.lineno <= len(src.splitlines()) else "") + "\nSyntaxError: " + str(e.msg),
         "error": "SyntaxError", "vars": {}, "last": None, "last_is_expr": False})
    sys.exit(0)
ns = {"__name__": "__main__"}
lines, error, last, last_is_expr = [], None, None, False
for node in tree.body:
    seg = ast.get_source_segment(src, node) or ""
    parts = seg.split("\n")
    lines.append(">>> " + parts[0] + "".join("\n... " + x for x in parts[1:]))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            if isinstance(node, ast.Expr):
                val = eval(compile(ast.Expression(node.value), "<код>", "eval"), ns)
                last, last_is_expr = val, True
            else:
                exec(compile(ast.Module([node], []), "<код>", "exec"), ns)
                last, last_is_expr = None, False
        if buf.getvalue():
            lines.append(buf.getvalue().rstrip("\n"))
        if last_is_expr and last is not None:
            lines.append(repr(last))
    except BaseException as e:
        if buf.getvalue():
            lines.append(buf.getvalue().rstrip("\n"))
        error = type(e).__name__
        lines.append(error + ": " + str(e))
        break
def simple(v):
    return v if type(v) in (int, float, str, bool) else {"repr": repr(v)}
variables = {k: simple(v) for k, v in ns.items() if not k.startswith("__")}
out({"transcript": "\n".join(lines), "error": error, "vars": variables,
     "last": simple(last) if last_is_expr else None, "last_is_expr": last_is_expr})
"""


def запустить_оболочку(код):
    """Выполняет код как в интерактивной оболочке. Возвращает словарь: transcript, error, vars, last, last_is_expr."""
    флаги = 0x08000000 if os.name == "nt" else 0
    with tempfile.TemporaryDirectory() as папка:
        (Path(папка) / "код.py").write_text(код, encoding="utf-8")
        (Path(папка) / "запуск.py").write_text(ЗАПУСКАТЕЛЬ_ОБОЛОЧКИ, encoding="utf-8")
        try:
            процесс = subprocess.run(
                команда_скрипта(Path(папка) / "запуск.py", Path(папка) / "код.py"),
                input="", capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=5, cwd=папка, creationflags=флаги, env=ОКРУЖЕНИЕ_ДОЧЕРНЕГО,
            )
        except subprocess.TimeoutExpired:
            return {"transcript": "Программа работала дольше 5 секунд.", "error": "Timeout",
                    "vars": {}, "last": None, "last_is_expr": False}
    try:
        return json.loads(процесс.stdout)
    except ValueError:
        return {"transcript": "Не удалось выполнить код.", "error": "Error", "vars": {}, "last": None, "last_is_expr": False}


def одинаково(значение, ожидается):
    if isinstance(ожидается, bool) or isinstance(значение, bool):
        return isinstance(значение, bool) and isinstance(ожидается, bool) and значение == ожидается
    if isinstance(ожидается, str):
        return isinstance(значение, str) and значение == ожидается
    return isinstance(значение, (int, float)) and abs(значение - ожидается) < 1e-9


def показ_значения(значение):
    if isinstance(значение, dict):
        return значение.get("repr", "?")
    return repr(значение)


def проверить_оболочку(вопрос, код):
    if not код.strip():
        return {"ok": False, "message": Л("Напиши код.", "Write some code."), "reason": "пусто", "console": Л("Напиши код.", "Write some code."), "console_error": True}
    try:
        дерево = ast.parse(код)
    except SyntaxError as ошибка:
        return {"ok": False, "reason": "SyntaxError", "console": f"SyntaxError: {ошибка.msg}", "console_error": True,
                "message": "Неверно: Python не понял запись (SyntaxError). Проверь кавычки, скобки и знаки."}
    результат = запустить_оболочку(код)
    итог = {"console": результат["transcript"] or "(пусто)", "console_error": bool(результат["error"])}
    ждём_ошибку = вопрос.get("expected_error")
    if ждём_ошибку:
        if результат["error"] == ждём_ошибку:
            итог.update(ok=True, reason="ВЕРНО", message=Л(f"Верно! Python остановился и показал ошибку {ждём_ошибку}.", f"Correct! Python stopped and showed the error {ждём_ошибку}."))
        elif результат["error"]:
            итог.update(ok=False, reason=f"другая ошибка {результат['error']}",
                        message=f"Неверно: ошибка есть, но другая ({результат['error']}). Нужна ошибка, о которой сказано в условии.")
        else:
            итог.update(ok=False, reason="ошибки не было", message=Л("Неверно: Python выполнил код без ошибки. Нужен код, который Python выполнить не сможет.", "Incorrect: Python ran the code without an error. The task needs code that Python cannot run."))
        return итог
    if результат["error"]:
        итог.update(ok=False, reason=f"ошибка {результат['error']}",
                    message=f"Python остановился с ошибкой ({результат['error']}). Прочитай последнюю строку в консоли и поправь код.")
        return итог
    использованные = {type(у.op) for у in ast.walk(дерево) if isinstance(у, ast.BinOp)}
    нужные = {ОПЕРАТОРЫ[о] for о in вопрос.get("required", [])}
    загруженные = {у.id for у in ast.walk(дерево) if isinstance(у, ast.Name) and isinstance(у.ctx, ast.Load)}
    for имя, ожидается in вопрос.get("expected_vars", {}).items():
        if имя not in результат["vars"]:
            итог.update(ok=False, reason=f"нет переменной {имя}", message=Л(f"Неверно: переменная {имя} не создана.", f"Incorrect: the variable {имя} was not created."))
            return итог
        if not одинаково(результат["vars"][имя], ожидается):
            итог.update(ok=False, reason=f"значение {имя} неверное",
                        message=f"Неверно: переменная {имя} создана, но в ней хранится {показ_значения(результат['vars'][имя])}, а нужно другое значение.")
            return итог
    if "expected" in вопрос:
        if not результат["last_is_expr"]:
            итог.update(ok=False, reason="последняя строка не выражение",
                        message="Не хватает последней команды: в условии нужно ещё показать значение. Добавь внизу ещё одну строку, которая показывает значение (см. справку, шаг 2).")
            return итог
        if not одинаково(результат["last"], вопрос["expected"]):
            итог.update(ok=False, reason="показано не то значение",
                        message=f"Неверно: последняя строка показала {показ_значения(результат['last'])}, а нужно показать другое значение.")
            return итог
    for имя in вопрос.get("uses_names", []):
        if имя not in загруженные:
            итог.update(ok=False, reason=f"{имя} не используется",
                        message=f"Верное значение, но переменная {имя} должна участвовать в вычислении, а не быть набрана готовым значением.")
            return итог
    if not нужные <= использованные:
        итог.update(ok=False, reason="операторы не те",
                    message="Значение верное, но программа ждёт вычисление. Используй подходящие знаки (они в справке).")
        return итог
    итог.update(ok=True, reason="ВЕРНО", message="Верно!")
    return итог


def консоль_выражения(ответ):
    """Как в оболочке Python: строка с >>> и результат или ошибка. Возвращает (текст, это_ошибка)."""
    строка = f">>> {ответ.strip()}\n"
    if not ответ.strip():
        return "Введи выражение.", True
    try:
        результат, _ = посчитать(ответ)
    except SyntaxError:
        return строка + "SyntaxError: Python не понял запись", True
    except ZeroDivisionError:
        return строка + "ZeroDivisionError: division by zero", True
    except TypeError as ошибка:
        return строка + f"TypeError: {ошибка}", True
    except ValueError as ошибка:
        return строка + f"Ошибка: {ошибка}", True
    return строка + показать(результат), False


def убрать_приглашение(текст):
    """Если вставили пример вместе со значками >>> и ..., убираем их."""
    строки = []
    for строка in текст.split("\n"):
        if строка.startswith(">>> ") or строка.startswith("... "):
            строка = строка[4:]
        elif строка.strip() in (">>>", "..."):
            строка = ""
        строки.append(строка)
    return "\n".join(строки)


def запуск(вопрос, ответ):
    """Просто запускает ответ и показывает консоль. Ничего не проверяет и не считает попытку."""
    ответ = убрать_приглашение(ответ)
    if вопрос["type"] == "code":
        if not ответ.strip():
            return {"console": "Напиши код.", "error": True}
        вывод, ошибка = запустить_код(ответ, вопрос.get("inputs", []))
        текст = вывод + (("\n" if вывод and not вывод.endswith("\n") else "") + ошибка if ошибка else "")
        return {"console": текст if текст.strip() else "(программа ничего не вывела)", "error": bool(ошибка)}
    if вопрос["type"] == "script":
        if not ответ.strip():
            return {"console": "Напиши код.", "error": True}
        результат = выполнить_скрипт_задачи(вопрос, ответ)
        return {"console": результат["console"], "error": результат["error"]}
    if вопрос["type"] == "shell":
        if not ответ.strip():
            return {"console": "Напиши код.", "error": True}
        результат = запустить_оболочку(ответ)
        return {"console": результат["transcript"] or "(пусто)", "error": bool(результат["error"])}
    текст, ошибка = консоль_выражения(ответ)
    return {"console": текст, "error": ошибка}


def получить_вопрос(id_задания, номер):
    задания, _ = все_задания()
    задание = задания.get(id_задания)
    if задание is None:
        raise KeyError(Л("такого задания нет", "no such task set"))
    if not (isinstance(номер, int) and 0 <= номер < len(задание["questions"])):
        raise KeyError(Л("такой задачи нет", "no such question"))
    return задание["questions"][номер]


def проверить_ответ(id_задания, номер, ответ):
    задания, _ = все_задания()
    задание = задания.get(id_задания)
    if задание is None:
        raise KeyError(Л("такого задания нет", "no such task set"))
    вопросы = задание["questions"]
    if not (isinstance(номер, int) and 0 <= номер < len(вопросы)):
        raise KeyError(Л("такой задачи нет", "no such question"))
    вопрос = вопросы[номер]
    ответ = убрать_приглашение(ответ)
    with БЛОКИРОВКА:
        прогресс = прочитать_прогресс(id_задания)
        if str(номер) in прогресс["solved"]:
            return {"ok": True, "message": Л("Эта задача уже решена.", "This question is already solved."), "finished": len(прогресс["solved"]) == len(вопросы)}
        if вопрос["type"] == "expression":
            итог = проверить_выражение(вопрос, ответ)
        elif вопрос["type"] == "text":
            итог = проверить_текст(вопрос, ответ)
        elif вопрос["type"] == "script":
            итог = проверить_скрипт(вопрос, ответ)
        elif вопрос["type"] == "shell":
            итог = проверить_оболочку(вопрос, ответ)
        else:
            итог = проверить_код(вопрос, ответ)
        попытки = int(прогресс["attempts"].get(str(номер), 0)) + 1
        прогресс["attempts"][str(номер)] = попытки
        записать_лог(id_задания, "INFO", f"задача {номер + 1} | попытка {попытки} | ввод={ответ!r} | {итог['reason']}"
                     + (f" | получилось {итог['result']}" if "result" in итог else ""))
        итог["attempts"] = попытки
        итог["finished"] = False
        if вопрос["type"] in ("expression", "text"):
            итог["console"], итог["console_error"] = консоль_выражения(ответ)
        if итог["ok"]:
            прогресс["solved"][str(номер)] = попытки
            if len(прогресс["solved"]) == len(вопросы):
                записать_результат(задание, прогресс)
                записать_лог(id_задания, "INFO", "задание пройдено, результат записан")
                итог["finished"] = True
        else:
            if попытки >= 6:
                итог["hint"] = Л("Если не получается, перечитай «Условие», «О задании» и «Справку» ещё раз или попроси помощи.", "If it does not work, read the Task, About and Help tabs once more or ask for help.")
            elif попытки >= 3 and (задание.get("help") or задание.get("reference")):
                итог["hint"] = Л("Загляни во вкладку «Справка» под условием: там есть то, что пригодится.", "Look at the Help tab under the task: it has what you need.")
        сохранить_прогресс(id_задания, прогресс)
    итог.pop("reason", None)
    return итог


def сбросить(id_задания):
    """Сбрасывает прогресс. Если Windows не даёт удалить файл (его держит другая программа), перезаписывает его пустым."""
    with БЛОКИРОВКА:
        папка = папка_результатов(id_задания)
        пустой = json.dumps({"solved": {}, "attempts": {}}, ensure_ascii=False, indent=1)
        for имя in ("прогресс.json", "результат.txt"):
            путь = папка / имя
            try:
                путь.unlink()
            except FileNotFoundError:
                continue
            except OSError as ошибка:
                logging.warning("не удалось удалить %s: %s, пробую перезаписать", путь, ошибка)
                try:
                    путь.write_text(пустой if имя == "прогресс.json" else "", encoding="utf-8")
                except OSError as ошибка2:
                    записать_лог(id_задания, "ERROR", f"сброс не удался: {имя} занят: {ошибка2}")
                    raise ValueError(f"файл «{имя}» занят другой программой, сбросить не получилось. "
                                     "Закрой программы, которые могут его держать (редактор, Проводник, архиватор, облако), и попробуй ещё раз.")
        записать_лог(id_задания, "INFO", "прогресс сброшен, задание начато заново")


def импортировать(имя_файла, содержимое):
    имя = Path(имя_файла).stem.strip()
    if not имя or not re.fullmatch(r"[\w\- ]+", имя):
        raise ValueError(Л("название файла может содержать только буквы, цифры, пробел, _ и -", "the file name may contain only letters, digits, space, _ and -"))
    данные = json.loads(содержимое)
    проверить_формат(данные)
    ПАПКА_ЗАДАНИЙ.mkdir(parents=True, exist_ok=True)
    путь = ПАПКА_ЗАДАНИЙ / f"{имя}.json"
    if путь.exists():
        raise ValueError(Л(f"задание «{имя}» уже есть в папке Задания", f"a task set named \"{имя}\" already exists in the tasks folder"))
    путь.write_text(json.dumps(данные, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("добавлено задание %s", путь.name)
    return имя


def открыть_в_системе(путь):
    try:
        if os.name == "nt":
            os.startfile(str(путь))  # noqa: открыть папку/файл в проводнике
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(путь)])
        else:
            subprocess.Popen(["xdg-open", str(путь)])
        return True
    except Exception:
        logging.exception("не удалось открыть %s", путь)
        return False


# ====================== Сервер ======================
class Обработчик(BaseHTTPRequestHandler):
    server_version = "StudyApp"

    def log_message(self, формат, *args):
        logging.debug("http: " + формат, *args)

    # --- вспомогательное ---
    def _ответ(self, код, тело, тип="application/json; charset=utf-8"):
        данные = тело if isinstance(тело, bytes) else json.dumps(тело, ensure_ascii=False).encode("utf-8")
        self.send_response(код)
        self.send_header("Content-Type", тип)
        self.send_header("Content-Length", str(len(данные)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(данные)

    def _хост_верный(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{ПОРТ}", f"localhost:{ПОРТ}")

    def _токен_верный(self, значение):
        return bool(значение) and secrets.compare_digest(значение, ТОКЕН)

    # --- GET ---
    def do_GET(self):
        КОНТЕКСТ.lang = "en" if self.headers.get("X-Lang") == "en" else "ru"
        адрес = urlparse(self.path)
        if not self._хост_верный():
            return self._ответ(403, {"error": "неверный адрес"})
        try:
            if адрес.path == "/":
                if not self._токен_верный(parse_qs(адрес.query).get("t", [""])[0]):
                    return self._ответ(403, "Откройте программу через запуск программа.py".encode("utf-8"),
                                       "text/plain; charset=utf-8")
                return self._ответ(200, ФАЙЛ_ИНТЕРФЕЙСА.read_bytes(), "text/html; charset=utf-8")
            if not self._токен_верный(self.headers.get("X-Token", "")):
                return self._ответ(403, {"error": Л("нет доступа", "access denied")})
            if адрес.path == "/api/tasks":
                задания, ошибки = все_задания()
                список = []
                for id_задания, задание in задания.items():
                    if задание.get("lang") not in (None, язык()):  # задание на другом языке не показываем
                        continue
                    вид = публичный_вид(задание)
                    список.append({"id": id_задания, "title": вид["title"], "topic": вид["topic"],
                                   "total": len(вид["questions"]), "solved": len(вид["solved"]),
                                   "passed": вид["passed"]})
                for id_задания, текст in ошибки.items():
                    список.append({"id": id_задания, "title": id_задания, "error": текст})
                список.sort(key=lambda з: ключ_сортировки(з["id"]))
                return self._ответ(200, {"tasks": список})
            if адрес.path == "/api/task":
                id_задания = parse_qs(адрес.query).get("id", [""])[0]
                задания, _ = все_задания()
                if id_задания not in задания:
                    return self._ответ(404, {"error": Л("такого задания нет", "no such task set")})
                return self._ответ(200, публичный_вид(задания[id_задания]))
            return self._ответ(404, {"error": Л("не найдено", "not found")})
        except Exception as ошибка:
            logging.exception("ошибка GET %s", self.path)
            return self._ответ(500, {"error": f"ошибка программы: {ошибка}"})

    # --- POST ---
    def do_POST(self):
        КОНТЕКСТ.lang = "en" if self.headers.get("X-Lang") == "en" else "ru"
        адрес = urlparse(self.path)
        if not self._хост_верный() or not self._токен_верный(self.headers.get("X-Token", "")):
            return self._ответ(403, {"error": Л("нет доступа", "access denied")})
        try:
            длина = int(self.headers.get("Content-Length", "0"))
            if длина > 1_000_000:
                return self._ответ(413, {"error": Л("слишком большой запрос", "request too large")})
            тело = json.loads(self.rfile.read(длина) or b"{}")
            global ПОСЛЕДНИЙ_ПИНГ
            if адрес.path == "/api/ping":
                ПОСЛЕДНИЙ_ПИНГ = time.time()
                return self._ответ(200, {"ok": True})
            if адрес.path == "/api/check":
                return self._ответ(200, проверить_ответ(тело.get("id", ""), тело.get("index"), str(тело.get("answer", ""))))
            if адрес.path == "/api/run":
                return self._ответ(200, запуск(получить_вопрос(тело.get("id", ""), тело.get("index")), str(тело.get("answer", ""))))
            if адрес.path == "/api/reset":
                задания, _ = все_задания()
                if тело.get("id") not in задания:
                    return self._ответ(404, {"error": Л("такого задания нет", "no such task set")})
                сбросить(тело["id"])
                return self._ответ(200, {"ok": True})
            if адрес.path == "/api/import":
                return self._ответ(200, {"id": импортировать(str(тело.get("name", "")), str(тело.get("content", "")))})
            if адрес.path == "/api/open":
                задания, _ = все_задания()
                id_задания = тело.get("id", "")
                if тело.get("what") == "log":
                    путь = ПАПКА_РЕЗУЛЬТАТОВ / id_задания / "лог.txt"
                    if id_задания not in задания or not путь.exists():
                        return self._ответ(200, {"ok": False, "message": Л("Лога пока нет: ещё не было ни одной попытки.", "No log yet: there have been no attempts.")})
                else:
                    путь = ПАПКА_РЕЗУЛЬТАТОВ
                    путь.mkdir(parents=True, exist_ok=True)
                return self._ответ(200, {"ok": открыть_в_системе(путь)})
            return self._ответ(404, {"error": Л("не найдено", "not found")})
        except (KeyError, ValueError) as ошибка:
            return self._ответ(400, {"error": str(ошибка.args[0]) if ошибка.args else "ошибка"})
        except Exception as ошибка:
            logging.exception("ошибка POST %s", self.path)
            return self._ответ(500, {"error": f"ошибка программы: {ошибка}"})


def открыть_окно(адрес):
    """Открывает окно приложения: Edge или Chrome в режиме --app, иначе обычный браузер."""
    if os.environ.get("STUDY_NO_BROWSER"):
        return
    кандидаты = []
    if os.name == "nt":
        for переменная in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
            база = os.environ.get(переменная)
            if база:
                кандидаты += [Path(база) / "Microsoft/Edge/Application/msedge.exe",
                              Path(база) / "Google/Chrome/Application/chrome.exe"]
    for exe in кандидаты:
        if exe.exists():
            subprocess.Popen([str(exe), f"--app={адрес}", "--window-size=1120,800"])
            return
    webbrowser.open(адрес)


def следить_за_окном(сервер):
    """Если окно закрыли (долго нет сигналов), программа завершается сама."""
    while True:
        time.sleep(5)
        сейчас = time.time()
        if ПОСЛЕДНИЙ_ПИНГ and сейчас - ПОСЛЕДНИЙ_ПИНГ > 180:
            logging.info("окно закрыто, завершаю программу")
            break
        if not ПОСЛЕДНИЙ_ПИНГ and сейчас - ВРЕМЯ_СТАРТА > 180:
            logging.info("окно так и не открылось, завершаю программу")
            break
    сервер.shutdown()


def сообщить(текст):
    """print, который не падает в exe без консоли."""
    try:
        if sys.stdout:
            print(текст)
    except Exception:
        pass


def подготовить_встроенные_задания():
    """В exe лежат стартовые задания. При первом запуске копируем их в «Документы/Практика Python/Задания»; дальше всё берётся оттуда."""
    встроенные = ПАПКА_ПРОГРАММЫ / "Задания"
    if not (ЗАМОРОЖЕНО and встроенные.is_dir()):
        return
    for файл in встроенные.glob("*.json"):
        цель = ПАПКА_ЗАДАНИЙ / файл.name
        try:
            if not цель.exists():  # то, что уже лежит в «Документах», не перезаписываем
                цель.write_bytes(файл.read_bytes())
        except OSError:
            logging.exception("не удалось скопировать задание %s", файл.name)


def выполнить_скрипт():
    """Особый режим для готового exe: запускает скрипт так, как это сделал бы обычный Python."""
    import runpy
    скрипт = sys.argv[2]
    sys.argv = [скрипт] + sys.argv[3:]
    sys.path.insert(0, str(Path(скрипт).parent))
    runpy.run_path(скрипт, run_name="__main__")


def main():
    global ПОРТ
    if len(sys.argv) >= 3 and sys.argv[1] == "--выполнить":
        return выполнить_скрипт()
    ПАПКА_РЕЗУЛЬТАТОВ.mkdir(parents=True, exist_ok=True)
    ПАПКА_ЗАДАНИЙ.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=ПАПКА_РЕЗУЛЬТАТОВ / "программа_лог.txt", level=logging.INFO, encoding="utf-8",
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    подготовить_встроенные_задания()
    if not ФАЙЛ_ИНТЕРФЕЙСА.exists():
        logging.error("не найден файл интерфейса %s", ФАЙЛ_ИНТЕРФЕЙСА)
        сообщить(f"Не найден файл {ФАЙЛ_ИНТЕРФЕЙСА.name} рядом с программой.")
        return
    сервер = ThreadingHTTPServer(("127.0.0.1", 0), Обработчик)
    ПОРТ = сервер.server_address[1]
    адрес = f"http://127.0.0.1:{ПОРТ}/?t={ТОКЕН}"
    logging.info("запуск, порт %s", ПОРТ)
    сообщить("Программа выполнения заданий запущена.")
    сообщить("Окно откроется само. Чтобы выключить программу, закрой окно или нажми Ctrl+C здесь.")
    сообщить(f"Если окно не открылось, вставь в браузер адрес: {адрес}")
    threading.Thread(target=следить_за_окном, args=(сервер,), daemon=True).start()
    открыть_окно(адрес)
    try:
        сервер.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        сервер.server_close()
        logging.info("программа остановлена")


if __name__ == "__main__":
    main()
