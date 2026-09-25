# ПОЛНЫЙ ПРОГОН РЕГРЕССИИ. Работает и на Windows (Git Bash), и на macOS.
# ⚠ Пункты 3-6 идут ЧЕРЕЗ ШЛЮЗ и стоят денег; шлюз пускает МАКСИМУМ 2 запроса параллельно.
# Запуск:  bash analysis/_run_all.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

# интерпретатор: сохранённый setup-скриптом → python3 → windows-сборка
PY="$(cat "$ROOT/mac/.python-path" 2>/dev/null)"
[ -x "$PY" ] || PY="$(command -v python3)"
[ -x "$PY" ] || PY="$HOME/AppData/Local/Python/pythoncore-3.14-64/python.exe"
export PYTHONIOENCODING=utf-8

echo "=== 0/7 гарды роутеров ===" && "$PY" analysis/test_router_guards.py 2>&1 | tail -8
echo "=== 1/7 callouts ==="   && "$PY" analysis/test_callouts.py 2>&1 | tail -1
echo "=== 2/7 junk ==="       && "$PY" analysis/test_junk.py 2>&1 | tail -1
echo "=== 2h/7 очередь CRM ===" && "$PY" analysis/test_crm_queue.py test_crm_addr.py test_persona_unlink.py 2>&1 | tail -1
echo "=== 2g/7 CRM-форма ===" && "$PY" analysis/test_crm_form.py 2>&1 | tail -1
echo "=== 2f/7 адаптивный TTL ===" && "$PY" analysis/test_adaptive_ttl.py 2>&1 | tail -1
echo "=== 2e/7 время городов ===" && "$PY" analysis/test_city_time.py 2>&1 | tail -1
echo "=== 2d/7 филиалы КП ===" && "$PY" analysis/test_filials_kp.py 2>&1 | tail -1
echo "=== 2c/7 пинг и «Решён» ===" && "$PY" analysis/test_ping_resolve.py 2>&1 | tail -1
echo "=== 2b/7 правило трёх сообщений ===" && "$PY" analysis/test_three_msg_rule.py 2>&1 | tail -1
echo "=== 3/7 hardcore ==="   && "$PY" analysis/test_hardcore.py 2>&1 | grep -E "^(✓|✗|ИТОГО|Тире)"
echo "=== 4/7 stress ==="     && "$PY" analysis/test_stress.py 2>&1 | grep -E "^(✓|✗|Провер)"
echo "=== 4b/7 unforeseen ===" && "$PY" analysis/test_unforeseen.py 2>&1 | grep -E "^(✓|✗|Провер)"
echo "=== 5/7 accept ==="     && "$PY" analysis/test_reglament_accept.py 2>&1 | tail -6
echo "=== 6/7 context ==="    && "$PY" analysis/test_context_liveness.py 2>&1 | tail -3
echo "=== ГОТОВО ==="
