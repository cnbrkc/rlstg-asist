from pathlib import Path

p = Path('pipeline.py')
s = p.read_text(encoding='utf-8')

old1 = """        temp_dosya_temizle(ses_dosyasi)
        if deneme < VOICE_REGEN_MAX:
            ek_talimat=(
                f'TTS önceki metni {ses_suresi:.2f} saniye üretti; hedef video {sure_saniye:.2f} saniye. '
                'Metni yeniden yaz ve konuşma süresini belirgin biçimde kısalt. '
                f'Hedef kelime sayısı yaklaşık {hedef}, kesin aralık {minimum}-{maksimum}. '
                'Bilgi yağdırma; yalnızca en güçlü gerçekleri bırak, yorum ve doğal akış için alan bırak.'
            )
            log(f'⚠️ TTS video süresine sığmadı; metin yeniden üretiliyor ({deneme+1}/{VOICE_REGEN_MAX}).')
            continue

        log('❌ TTS süresi güvenli aralığa girmedi; yarım/uygunsuz video üretmemek için render durduruldu.')
        return reels_state,model_reels,duo_plan,duo_script,False,None,mod,''
"""

new1 = """        if deneme < VOICE_REGEN_MAX:
            temp_dosya_temizle(ses_dosyasi)
            ek_talimat=(
                f'TTS önceki metni {ses_suresi:.2f} saniye üretti; hedef video {sure_saniye:.2f} saniye. '
                'Metni yeniden yaz ve konuşma süresini belirgin biçimde kısalt. '
                f'Hedef kelime sayısı yaklaşık {hedef}, kesin aralık {minimum}-{maksimum}. '
                'Bilgi yağdırma; yalnızca en güçlü gerçekleri bırak, yorum ve doğal akış için alan bırak.'
            )
            log(f'⚠️ TTS video süresine sığmadı; metin yeniden üretiliyor ({deneme+1}/{VOICE_REGEN_MAX}).')
            continue

        log('⚠️ TTS süresi ideal aralığa girmedi; ancak geçerli WAV mevcut. Mevcut ses FFmpeg senkronunda kullanılacak; yeni TTS çağrısı yapılmayacak.')
        return reels_state,model_reels,duo_plan,duo_script,True,info,mod,ses_dosyasi
"""

old2 = """            if recovery_uyumlu:
                ses_dosyasi=recovery_path
                kullanilan_ses_modeli=recovery_info
                ses_modu=recovery_mode
                state['ses_modu']=ses_modu
                log_ekle(f'✅ TTS recovery başarılı: {ses_modu} → {_ses_modu_sesi(ses_modu)}')
            else:
                temp_dosya_temizle(recovery_path)
                ses_basarili=False
                ses_dosyasi=''
                log_ekle('❌ Recovery TTS gerçek süre aralığına girmedi; render durduruldu.')
"""

new2 = """            if recovery_sure > 0:
                ses_dosyasi=recovery_path
                kullanilan_ses_modeli=recovery_info
                ses_modu=recovery_mode
                state['ses_modu']=ses_modu
                if recovery_uyumlu:
                    log_ekle(f'✅ TTS recovery başarılı ve süre aralığında: {ses_modu} → {_ses_modu_sesi(ses_modu)}')
                else:
                    log_ekle(f'⚠️ TTS recovery dosyası geçerli ancak oran {recovery_oran:.2f}x; mevcut ses FFmpeg senkronunda kullanılacak, yeni TTS çağrısı yapılmayacak.')
            else:
                temp_dosya_temizle(recovery_path)
                ses_basarili=False
                ses_dosyasi=''
                log_ekle('❌ TTS recovery geçerli bir WAV üretemedi; render durduruldu.')
"""

if old1 not in s:
    raise SystemExit('PATCH_GUARD_1_FAILED: exact TTS final-gate block not found')
if old2 not in s:
    raise SystemExit('PATCH_GUARD_2_FAILED: exact recovery gate block not found')

patched = s.replace(old1, new1, 1).replace(old2, new2, 1)
compile(patched, 'pipeline.py', 'exec')
p.write_text(patched, encoding='utf-8')
print('STAGE4_TTS_PATCH_OK')
