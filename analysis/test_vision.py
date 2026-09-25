# -*- coding: utf-8 -*-
"""Тест зрения: прогоняет реальные фото клиентов через vision (Haiku, ужатые) и
проверяет, что бот верно определяет объект/направление. Максимально дёшево."""
import sys, io, os, re, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server, claude_api, paths  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠ ИСТОЧНИК СМЕНЁН 30.08 (этап 1 программы обучения выполнен): корпус собирается
# из боевой базы LeadChat — data/vision_corpus.jsonl (400 свежих фото с контекстом
# диалога; в git не попадает, содержит ПД). Прежний dialogs.jsonl эпохи Jivo исчез.
SRC = os.path.join(os.path.dirname(HERE), "data", "vision_corpus.jsonl")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
IMG_RX = re.compile(r"https?://\S+img\.avito\.st/\S+")

# --- собрать фото с контекстом (что клиент писал в этом диалоге = «правда») ---
cand = []
seen_ctx = set()
for line in open(SRC, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue
    url = d.get("url") or ""
    if not url.startswith("http"):
        continue
    title = d.get("title") or ""
    client_txt = re.sub(r"\s+", " ", d.get("client_text") or "").strip()
    ctx = (title + " | " + client_txt)[:220]
    key = ctx[:60]
    if key in seen_ctx:                       # разнообразие: не берём похожие контексты
        continue
    seen_ctx.add(key)
    cand.append({"url": url, "ctx": ctx, "title": title[:50]})

# перемешать детерминированно и взять N разнообразных
cand.sort(key=lambda x: x["ctx"])
step = max(1, len(cand) // N)
picked = cand[::step][:N]
print("Кандидатов с фото:", len(cand), "-> берём", len(picked))

SYS = ("Ты мастер по ремонту (БТ — бытовая техника; КП — компьютеры/ИТ; "
       "МНЧ — сантехника/мебель/электрика/мелкий ремонт). По фото определи, ЧТО на нём "
       "и к какому направлению относится. Ответ СТРОГО: «объект — bt|kp|mnc|other».")

rows = []
tok = []
for i, p in enumerate(picked, 1):
    block = server._fetch_image_block(p["url"])          # скачивает + ужимает
    if not block:
        print("  [%d] фото не скачалось, пропуск" % i)
        continue
    msgs = [{"role": "user", "content": [block, {"type": "text", "text": "Что на фото?"}]}]
    try:
        r = claude_api.messages(system=SYS, msgs=msgs,
                                model="claude-haiku-4-5-20251001", max_tokens=40)
        ans = claude_api.first_text(r)
        it = (r.get("usage") or {}).get("input_tokens")
        tok.append(it or 0)
    except Exception as e:
        ans, it = "ОШИБКА: %r" % e, None
    rows.append({"n": i, "ctx": p["ctx"], "title": p["title"], "answer": ans, "in_tokens": it})
    print("  [%d] in=%s | %s" % (i, it, ans.replace("\n", " ")))

# сохранить для судейского разбора
json.dump(rows, open(os.path.join(HERE, "test_vision.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
with open(os.path.join(HERE, "test_vision.md"), "w", encoding="utf-8") as f:
    f.write("# Тест зрения: фото → определение бота vs контекст диалога\n\n")
    for x in rows:
        f.write("**%d.** контекст: _%s_\n\n- Бот по фото: **%s**  (input=%s ток)\n\n" % (
            x["n"], x["ctx"], x["answer"].replace("\n", " "), x["in_tokens"]))

if tok:
    print("\nСредний input/фото: %.0f токенов (медиана %d)" % (
        sum(tok) / len(tok), sorted(tok)[len(tok) // 2]))
print("Готово: test_vision.md / .json (%d фото)" % len(rows))
