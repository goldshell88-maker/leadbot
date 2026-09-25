# -*- coding: utf-8 -*-
"""Тонкий клиент к Claude через ПРЯМОЙ API Anthropic (api.anthropic.com).

С 31.07.2026 работаем только напрямую: шлюз claude-gateway.ru отключён (баланс исчерпан,
HTTP 402 insufficient_credits). Ключ берётся ИЗ ОКРУЖЕНИЯ `LEADBOT_API_KEY` и в config.json
не хранится. Адрес и стиль авторизации при необходимости перекрываются теми же env-переменными.
Только stdlib (urllib) — никаких внешних зависимостей.
"""
import json
import os
import re
import time
import urllib.request
import urllib.error

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG = None


def config():
    """Читает config.json один раз и кэширует.

    Переменные окружения ПЕРЕКРЫВАЮТ файл — удобно при переезде на другую машину
    и при смене биллинга (шлюз ↔ прямой API Anthropic), не трогая config.json:
      LEADBOT_API_BASE  — по умолчанию https://api.anthropic.com
      LEADBOT_API_KEY   — ключ/токен
      LEADBOT_AUTH      — bearer | x-api-key (по умолчанию определяется по адресу)
    """
    global _CONFIG
    if _CONFIG is None:
        with open(os.path.join(_ROOT, "config.json"), "r", encoding="utf-8") as f:
            _CONFIG = json.load(f)
        for env, key in (("LEADBOT_API_BASE", "gateway_base"),
                         ("LEADBOT_API_KEY", "auth_token"),
                         ("LEADBOT_AUTH", "auth_style")):
            if os.environ.get(env):
                _CONFIG[key] = os.environ[env]
    return _CONFIG


def _auth_headers(cfg):
    """Шлюз ждёт «authorization: Bearer …», прямой API Anthropic — «x-api-key».
    Стиль берём из config/env, иначе угадываем по адресу и виду ключа."""
    style = (cfg.get("auth_style") or "").lower()
    if not style:
        base = (cfg.get("gateway_base") or "").lower()
        key = cfg.get("auth_token") or ""
        style = "x-api-key" if ("api.anthropic.com" in base or key.startswith("sk-ant-")) else "bearer"
    if style == "x-api-key":
        return {"x-api-key": cfg["auth_token"]}
    return {"authorization": "Bearer " + cfg["auth_token"]}


class ClaudeError(Exception):
    pass


# Модели, которые НЕ принимают thinking/effort — присылают 400 (Haiku 4.5 и старее).
_NO_THINKING_RX = re.compile(r"haiku|sonnet-4-5|sonnet-3|opus-4-5|opus-4-1", re.IGNORECASE)


def _thinking_params(model, cfg):
    """Параметры мышления и «усилия» — только для моделей, которые их понимают.

    ⚠ ВАЖНО для прямого API: у Sonnet 5 и Opus 5 адаптивное мышление включено ПО
    УМОЛЧАНИЮ, когда поле `thinking` не передано, а `max_tokens` ограничивает
    мышление И ответ вместе. При нашем max_tokens=768 размышление могло съесть
    лимит и обрезать вызов инструмента на середине. Диспетчерский ход короткий и
    типовой — мышление там не нужно, поэтому по умолчанию выключаем явно.
    Haiku 4.5 на эти параметры отвечает 400, туда не шлём ничего.
    """
    if _NO_THINKING_RX.search(model or ""):
        return {}
    out = {}
    mode = (cfg.get("thinking") or "disabled").lower()
    effort = cfg.get("effort")
    # у Opus 5 выключенное мышление допустимо только при effort ≤ high
    if mode == "disabled" and effort in ("xhigh", "max"):
        mode = "adaptive"
    if mode in ("disabled", "adaptive"):
        out["thinking"] = {"type": mode}
    if effort:
        out["output_config"] = {"effort": effort}
    return out


#: ⚠ КОДЫ, КОТОРЫЕ СТОИТ ПОВТОРИТЬ. Это НЕ «все ошибки»: на 400/401/403 повтор бесполезен
#: (ключ, формат, доступ — они не починятся за секунду) и только съедает бюджет хода.
#: 429 — троттлинг, 5xx — сторона провайдера, 529 — «overloaded» у Anthropic.
_ПОВТОРИТЬ_КОДЫ = frozenset((408, 429, 500, 502, 503, 504, 529))
#: пауза перед попыткой N (секунды). Две попытки — потолок по умолчанию: третья уже не
#: успевает в бюджет хода, а брошенная нить и так догреет кэш ответов.
_ПАУЗЫ = (0.0, 0.7, 2.1)


def _послать(req, timeout, cfg, retries=None):
    """POST с повтором на временных отказах. Возвращает тело ответа строкой.

    ⚠ ЗАЧЕМ ПОВТОР (замер 24.08). Ретраев не было НИ ОДНОГО: любой 429 от провайдера
    сразу поднимался ClaudeError, наверху превращался в 502, и клиент получал молчание.
    А 429 у нас не редкость по устройству обхода: опрос раз в 12 секунд, до 30 чатов на
    аккаунт, два аккаунта — замеренный пик 33 вызова в минуту.

    ⚠ ПОЧЕМУ ЭТО НЕ ЛОМАЕТ БЮДЖЕТ ХОДА. Ответ LeadChat ограничен девятью секундами
    снаружи, через `th.join` в server.leadchat_answer: нить, не успевшую к сроку, там
    БРОСАЮТ, а не убивают — она дорабатывает и кладёт ответ в кэш, который пригодится
    на следующем ходу. То есть худшее, что делает повтор, — переносит пользу на ход
    вперёд; ничего не задерживает.
    """
    попыток = 1 + int((cfg.get("retries", 1) if retries is None else retries) or 0)
    попыток = max(1, min(попыток, len(_ПАУЗЫ)))
    последняя = None
    for n in range(попыток):
        if n:
            time.sleep(_пауза(n, последняя))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # ⚠ getattr, А НЕ r.headers: объект ответа не обязан их иметь (прокси,
                # заглушка, чужая обёртка), и падать из-за замера расходов недопустимо.
                _запомнить_лимиты(getattr(r, "headers", None))
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            последняя = e
            if e.code not in _ПОВТОРИТЬ_КОДЫ or n == попыток - 1:
                raise ClaudeError("HTTP %s: %s" % (e.code, detail[:600]))
            _шум("повтор после HTTP %s (попытка %d из %d)" % (e.code, n + 2, попыток))
        except urllib.error.URLError as e:
            последняя = None
            if n == попыток - 1:
                raise ClaudeError("Сеть: %s (проверь доступ к api.anthropic.com)" % e.reason)
            _шум("повтор после сетевого отказа: %s (попытка %d из %d)" % (e.reason, n + 2, попыток))
    raise ClaudeError("не удалось отправить запрос")   # недостижимо, но контракт один


#: ⚠ ЛИМИТЫ ПРОВАЙДЕРА МЫ САМИ ВЫБРАСЫВАЛИ (24.08). Anthropic присылает их в ЗАГОЛОВКАХ
#: каждого ответа: сколько запросов и токенов в минуту нам положено и сколько осталось.
#: Читалось только тело, а заголовки уходили в мусор — и вопрос «упираемся ли мы в потолок»
#: приходилось гадать по косвенным следам вроде пиков в журнале расходов. Гадание вышло
#: неверным: пики в локальном журнале оказались следом прогонов, а не боя.
#: Теперь последние известные лимиты лежат здесь, и ход кладёт их в журнал расходов —
#: замер получается даром, из ответов, которые и так приходят.
ЛИМИТЫ = {}
_ЗАГОЛОВКИ_ЛИМИТОВ = (
    "anthropic-ratelimit-requests-limit",
    "anthropic-ratelimit-requests-remaining",
    "anthropic-ratelimit-requests-reset",
    "anthropic-ratelimit-input-tokens-limit",
    "anthropic-ratelimit-input-tokens-remaining",
    "anthropic-ratelimit-output-tokens-limit",
    "anthropic-ratelimit-output-tokens-remaining",
    "anthropic-ratelimit-tokens-limit",
    "anthropic-ratelimit-tokens-remaining",
)


def _запомнить_лимиты(headers):
    """Снять с ответа лимиты провайдера. Ошибка здесь не должна стоить хода."""
    try:
        свежие = {}
        for имя in _ЗАГОЛОВКИ_ЛИМИТОВ:
            зн = (headers or {}).get(имя)
            if зн:
                # короткое имя: «requests-remaining», а не весь префикс вендора
                свежие[имя.replace("anthropic-ratelimit-", "")] = зн
        if свежие:
            ЛИМИТЫ.clear()
            ЛИМИТЫ.update(свежие)
    except Exception:                                    # noqa: BLE001
        pass


def _пауза(n, ошибка):
    """Пауза перед попыткой n. Retry-After провайдера главнее нашей лестницы."""
    свой = _ПАУЗЫ[min(n, len(_ПАУЗЫ) - 1)]
    try:
        ra = float((ошибка.headers or {}).get("retry-after") or 0)
    except (AttributeError, TypeError, ValueError):
        ra = 0.0
    # больше пяти секунд не ждём: бюджет хода девять, а брошенная нить всё равно догреет кэш
    return min(max(свой, ra), 5.0)


def _шум(текст):
    try:
        print("[ШЛЮЗ] %s" % текст, flush=True)
    except Exception:
        pass


def messages(system, msgs, tools=None, tool_choice=None,
             model=None, max_tokens=None, timeout=90, retries=None):
    """Вызов POST /v1/messages. Возвращает разобранный JSON ответа Anthropic.

    system      — строка системного промпта (или уже готовый список блоков)
    msgs        — список {"role": "user"|"assistant", "content": ...}
    tools       — список описаний инструментов (JSON Schema)
    tool_choice — напр. {"type": "tool", "name": "dispatcher_turn"}

    ⚠ Кэширование промпта делает ВЫЗЫВАЮЩИЙ: server.py собирает system списком
    блоков с cache_control (там же живёт cache_ttl из config) — плейбук читается
    из кэша за ~10% цены. Здесь его нет: параметр cache= был мёртвым (все вызовы
    шли с cache=False), как и temperature (Sonnet 5/Opus 5 отклоняют её с 400).
    """
    cfg = config()
    sys_field = system
    payload = {
        "model": model or cfg.get("model_hard") or cfg.get("model", "claude-sonnet-5"),
        "max_tokens": max_tokens or cfg.get("max_tokens", 1024),
        "system": sys_field,
        "messages": msgs,
    }
    payload.update(_thinking_params(payload["model"], cfg))
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    headers.update(_auth_headers(cfg))
    # Часовой кэш на официальном API уже GA — бета-заголовок там не нужен и может
    # дать 400 как неизвестный. Нужен только нестандартной прокси-базе, если её зададут.
    if cfg.get("cache_ttl") == "1h" and "api.anthropic.com" not in (cfg.get("gateway_base") or ""):
        headers["anthropic-beta"] = "extended-cache-ttl-2025-04-11"

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg["gateway_base"].rstrip("/") + "/v1/messages",
        data=body,
        headers=headers,
        method="POST",
    )
    raw = _послать(req, timeout, cfg, retries)
    # ⚠ МУСОР ВМЕСТО JSON — ТОЖЕ ОТКАЗ ШЛЮЗА, А НЕ НАША ВНУТРЕННЯЯ ПОЛОМКА (этап 5).
    # Тело с кодом 200 не обязано быть JSON: прокси и балансировщик отдают HTML-страницу
    # ошибки, а оборванное соединение — половину структуры. Раньше отсюда летел
    # JSONDecodeError, то есть МИМО контракта: пять мест в server.py ловят именно
    # ClaudeError и отвечают 502, а всё прочее обработчик HTTP не ловит вовсе — ответ
    # клиенту не отправляется, соединение рвётся, и в журнале не остаётся ни строки.
    try:
        return json.loads(raw)
    except ValueError:
        raise ClaudeError("Шлюз вернул не JSON (%d байт): %s" % (len(raw), raw[:200].strip()))


def first_tool_input(response, name=None):
    """Достаёт input первого tool_use блока (или блока с нужным именем)."""
    for block in response.get("content", []):
        if block.get("type") == "tool_use" and (name is None or block.get("name") == name):
            return block.get("input", {})
    return None


def first_text(response):
    """Склеивает текстовые блоки ответа."""
    return "".join(b.get("text", "") for b in response.get("content", [])
                   if b.get("type") == "text").strip()
