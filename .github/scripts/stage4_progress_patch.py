from pathlib import Path

p = Path('telegram_pipeline_worker.py')
s = p.read_text(encoding='utf-8')

old_video = '''    def progress(n, total, msg):
        step_status[n - 1] = "🟢"
        current = PIPELINE_STEPS[n - 1] if 0 < n <= len(PIPELINE_STEPS) else str(msg)
        try:
            edit_message(loading_id, _loading_text(n - 1, f"Tamamlandı → {current}", len(warnings), len(errors)))
        except Exception as exc:
            print(f"Loading mesajı güncellenemedi: {exc}", flush=True)
'''
new_video = '''    def progress(n, total, msg):
        # pipeline.py çağrıyı aşama başlamadan hemen önce yapıyor.
        # Bu nedenle n mevcut aşamadır; n-1 aşama tamamlanmıştır.
        done = max(0, min(n - 1, len(PIPELINE_STEPS)))
        if done > 0:
            step_status[done - 1] = "🟢"
        current = PIPELINE_STEPS[n - 1] if 0 < n <= len(PIPELINE_STEPS) else str(msg)
        try:
            edit_message(loading_id, _loading_text(done, current, len(warnings), len(errors)))
        except Exception as exc:
            print(f"Loading mesajı güncellenemedi: {exc}", flush=True)
'''
old_text = '''    def progress(n, total, msg):
        step_status[n - 1] = "🟢"
        current = TEXT_PIPELINE_STEPS[n - 1] if 0 < n <= len(TEXT_PIPELINE_STEPS) else str(msg)
        try:
            edit_message(loading_id, _loading_text(n - 1, f"Tamamlandı → {current}", len(warnings), len(errors), steps=TEXT_PIPELINE_STEPS))
        except Exception as exc:
            print(f"Loading mesajı güncellenemedi: {exc}", flush=True)
'''
new_text = '''    def progress(n, total, msg):
        # pipeline.py çağrıyı aşama başlamadan hemen önce yapıyor.
        # Bu nedenle n mevcut aşamadır; n-1 aşama tamamlanmıştır.
        done = max(0, min(n - 1, len(TEXT_PIPELINE_STEPS)))
        if done > 0:
            step_status[done - 1] = "🟢"
        current = TEXT_PIPELINE_STEPS[n - 1] if 0 < n <= len(TEXT_PIPELINE_STEPS) else str(msg)
        try:
            edit_message(loading_id, _loading_text(done, current, len(warnings), len(errors), steps=TEXT_PIPELINE_STEPS))
        except Exception as exc:
            print(f"Loading mesajı güncellenemedi: {exc}", flush=True)
'''
if s.count(old_video) != 1:
    raise SystemExit('PATCH_GUARD_VIDEO_PROGRESS_FAILED')
if s.count(old_text) != 1:
    raise SystemExit('PATCH_GUARD_TEXT_PROGRESS_FAILED')
s = s.replace(old_video, new_video, 1).replace(old_text, new_text, 1)
s = s.replace("f\"🔁 QA regeneration: {result.get('qa_regeneration_rounds', 0)} / 2\"", "f\"🔁 QA regeneration: {result.get('qa_regeneration_rounds', 0)} / 1\"")
p.write_text(s, encoding='utf-8')
compile(s, 'telegram_pipeline_worker.py', 'exec')
print('STAGE4_PROGRESS_PATCH_OK')
