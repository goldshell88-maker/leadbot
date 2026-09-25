# -*- coding: utf-8 -*-
"""МУТАЦИОННЫЙ СТОРОЖ: какие правила можно выключить, и никто не заметит.

ЗАЧЕМ. Регулярки верхнего уровня в brain/ — это и есть правила регламента, переведённые
в код: «отказ», «поломка названа», «клиент торопит», «просьба номера». Почти каждая
поставлена ПОСЛЕ боевого дефекта. Вопрос, на который до сих пор никто не отвечал: если
такая регулярка однажды сломается, покраснеет ли хоть что-нибудь?

Замер 24.08 говорит, что часто НЕТ. Это не про «нет теста рядом» — тесты гоняют боевой
путь целиком, и большинство регулярок под ними исполняется. Это про то, что ни одна
проверка не ЗАВИСИТ от их работы: правило можно выключить, и вся офлайн-регрессия
останется зелёной. Такое правило живёт на честном слове.

КАК ПРОВЕРЯЕМ. По одной регулярке за раз подменяем на «никогда не совпадает» и гоняем
быстрый костяк наборов. Ничего не покраснело — правило НЕМОЕ, имя в отчёт.

⚠ МУТАЦИЯ ОДНОСТОРОННЯЯ. «Никогда не совпадает» ловит недобор (правило перестало
срабатывать), но не перебор (правило стало жадным). Обратную сторону закрывает
analysis/test_boundaries.py — он бьёт по границам слова.

Запуск:  python3 analysis/lint_silent_guards.py [модуль=prefilter,server] [--полный]
Выход 0 всегда: это инструмент разведки, а не привратник. Привратником он станет, когда
список немых будет закрыт и зафиксирован.
"""
import importlib
import io
import os
import re
import subprocess
import sys
import time

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(КОРЕНЬ, "brain"))

#: костяк: самые плотные наборы, которые вместе гоняют почти все роутеры и гарды.
#: Полный прогон (--полный) точнее, но 21 секунда × сотня регулярок — это полчаса.
КОСТЯК = ["test_router_guards", "test_boundaries", "test_funnel_rules", "test_callouts",
          "test_escalation", "test_three_msg_rule", "test_junk", "test_batch1", "test_batch3"]
ПОЛНЫЙ = ["_run_offline.sh"]


class _Немая:
    """Регулярка, которая не совпадает ни с чем. Интерфейс тот же, что у re.Pattern."""

    def __init__(self, шаблон):
        self.pattern = getattr(шаблон, "pattern", "")

    def search(self, *a, **k):
        return None

    def match(self, *a, **k):
        return None

    def fullmatch(self, *a, **k):
        return None

    def findall(self, *a, **k):
        return []

    def finditer(self, *a, **k):
        return iter(())

    def sub(self, repl, string, count=0):
        return string

    def subn(self, repl, string, count=0):
        return string, 0

    def split(self, string, maxsplit=0):
        return [string]


def регулярки(модуль):
    """Имена регулярок верхнего уровня — в порядке объявления."""
    м = importlib.import_module(модуль)
    имена = []
    for имя, зн in vars(м).items():
        if isinstance(зн, re.Pattern):
            имена.append(имя)
    return sorted(имена), м


def прогон(наборы, окружение):
    """True — всё зелёное (то есть мутация НИКЕМ не замечена)."""
    for набор in наборы:
        путь = os.path.join(КОРЕНЬ, "analysis", набор)
        cmd = (["bash", путь] if набор.endswith(".sh")
               else [sys.executable, путь + ".py" if not набор.endswith(".py") else путь])
        r = subprocess.run(cmd, cwd=КОРЕНЬ, capture_output=True, env=окружение)
        if r.returncode != 0:
            return False, набор
    return True, ""


def _страж_текст(модуль, имя):
    """Текст sitecustomize, который глушит РОВНО ОДНО правило в чужом процессе.

    ⚠ sys ИМПОРТИРУЕТСЯ НА ВЕРХНЕМ УРОВНЕ, А НЕ ВНУТРИ ПЕРЕХВАТЧИКА. `import sys` внутри
    _imp зовёт сам _imp — бесконечная рекурсия, и каждый прогон падал мгновенно с
    RecursionError. Снаружи это выглядело как отчёт «немых нет»: все наборы красные
    всегда, значит мутацию всегда кто-то «замечал». Тихий способ получить бессмысленный
    отчёт, и первая редакция этого файла именно его и выдала.
    """
    return (
        "import builtins, sys\n"
        "class _Н:\n"
        "    def __init__(s,p=None): s.pattern=''\n"
        "    def search(s,*a,**k): return None\n"
        "    def match(s,*a,**k): return None\n"
        "    def fullmatch(s,*a,**k): return None\n"
        "    def findall(s,*a,**k): return []\n"
        "    def finditer(s,*a,**k): return iter(())\n"
        "    def sub(s,r,x,count=0): return x\n"
        "    def subn(s,r,x,count=0): return x,0\n"
        "    def split(s,x,maxsplit=0): return [x]\n"
        "_и=builtins.__import__\n"
        "def _imp(name,*a,**k):\n"
        "    m=_и(name,*a,**k)\n"
        "    t=sys.modules.get(%r)\n"
        "    if t is not None and not isinstance(getattr(t,%r,None),_Н):\n"
        "        try: setattr(t,%r,_Н())\n"
        "        except Exception: pass\n"
        "    return m\n"
        "builtins.__import__=_imp\n" % (модуль, имя, имя))


def main():
    модули = [x for x in sys.argv[1:] if not x.startswith("--")] or ["prefilter", "server"]
    наборы = ПОЛНЫЙ if "--полный" in sys.argv else КОСТЯК
    # ⚠ кэш ответов — свой: наборы гоняют боевой путь и дописывают заглушки
    окр = dict(os.environ, LEADBOT_ANSWER_CACHE=os.path.join(КОРЕНЬ, "analysis", ".мутация-кэш"))
    окр["PYTHONPATH"] = os.path.join(КОРЕНЬ, "brain") + os.pathsep + окр.get("PYTHONPATH", "")

    # ⚠ МУТАЦИЯ ЖИВЁТ В ОТДЕЛЬНОМ ПРОЦЕССЕ, а не в нашем: наборы запускаются
    # подпроцессами, и подмена в нашей памяти до них бы не доехала. Подкладываем
    # sitecustomize, который глушит ровно одно имя.
    страж = os.path.join(КОРЕНЬ, "analysis", ".мутация")
    os.makedirs(страж, exist_ok=True)
    окр["PYTHONPATH"] = страж + os.pathsep + окр["PYTHONPATH"]

    немые, проверено = [], 0
    начало = time.time()
    for модуль in модули:
        имена, _ = регулярки(модуль)
        print("── %s: регулярок верхнего уровня %d" % (модуль, len(имена)))
        for имя in имена:
            io.open(os.path.join(страж, "sitecustomize.py"), "w", encoding="utf-8").write(
                _страж_текст(модуль, имя))
            зелено, кто = прогон(наборы, окр)
            проверено += 1
            if зелено:
                немые.append("%s.%s" % (модуль, имя))
                print("   НЕМАЯ  %-34s выключил — никто не заметил" % имя)
    # ⚠ ВТОРОЙ ПРОХОД — ПОЛНОЙ РЕГРЕССИЕЙ, И ТОЛЬКО ПО КАНДИДАТАМ. Костяк из девяти наборов
    # быстр, но неполон: правило, которое он не заметил, может ловить какой-нибудь из
    # остальных тридцати. Гнать полный прогон по всем 132 правилам — сорок минут; по
    # кандидатам это пять-двадцать, и список получается честным, а не пугающим.
    если_полный = "--полный" in sys.argv
    if немые and not если_полный:
        print("\n── второй проход: полная регрессия по %d кандидатам ──" % len(немые))
        подтверждённые = []
        for полное in немые:
            модуль, имя = полное.split(".", 1)
            io.open(os.path.join(страж, "sitecustomize.py"), "w", encoding="utf-8").write(
                _страж_текст(модуль, имя))
            зелено, _ = прогон(ПОЛНЫЙ, окр)
            if зелено:
                подтверждённые.append(полное)
            else:
                print("   ловится полной регрессией: %s" % полное)
        немые = подтверждённые
    try:
        io.open(os.path.join(страж, "sitecustomize.py"), "w", encoding="utf-8").write("")
    except OSError:
        pass
    print("=" * 66)
    print("Проверено правил: %d | НЕМЫХ: %d (%.0f%%) | %.0f с"
          % (проверено, len(немые), 100.0 * len(немые) / max(1, проверено), time.time() - начало))
    if немые:
        print("\nЭти правила можно выключить, и вся проверка останется зелёной:")
        for имя in немые:
            print("   ·", имя)
    return 0


if __name__ == "__main__":
    sys.exit(main())
