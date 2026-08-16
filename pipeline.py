"""
pipeline.py — Ultimate Content Engine orkestratörü.

Streamlit bağımlılığı yoktur; Telegram/GitHub Actions tarafından doğrudan çağrılabilir.
"""
import os, re
from config import KELIME_HIZI_ORANI, SES_HIZ_CARPANI, PIPELINE_ADIMLARI
from schemas import VIDEO_ANALYSIS_SCHEMA, FACT_LOCK_SCHEMA, EDITORIAL_SCHEMA, REELS_CREATIVE_SCHEMA, CAPTION_SCHEMA, THREADS_SCHEMA, QA_SCHEMA, DUO_SCRIPT_SCHEMA
from prompts import (forensic_analiz_promptunu_olustur, research_promptunu_olustur, editorial_promptunu_olustur,
                     reels_creative_promptunu_olustur, caption_promptunu_olustur, threads_promptunu_olustur,
                     qa_promptunu_olustur, durumu_metne_donustur, girdi_birlestir,
                     _reels_kelime_ayarlarini_hazirla)
from media import gecici_ses_yolu, gecici_dosya_yolu, temp_dosya_temizle, video_ve_sesi_birlestir, _ses_suresini_al
from duo_strategy import normalize_duo_strategy
from duo_script import normalize_conversation_map
from duo_script_engine import build_duo_generation_contract, build_generation_prompt, validate_generated_duo
from duo_audio import duo_ses_uret

TOPLAM_ADIM = len(PIPELINE_ADIMLARI)
VOICE_REGEN_MAX = 2
VOICE_DURATION_MIN_RATIO = 0.85
VOICE_DURATION_MAX_RATIO = 1.15


def _ilerleme(cb, n, msg=None):
    if cb: cb(n, TOPLAM_ADIM, msg or PIPELINE_ADIMLARI[n-1])


def _secilen_hook_getir(reels_state):
    families=reels_state.get('hook_families') or []
    if not families:return {}
    idx=reels_state.get('secilen_aile_index',0)
    return families[idx] if isinstance(idx,int) and 0<=idx<len(families) else families[0]


def _forensic_analiz_calistir(router, video_bytes, mime_type, analiz_notlari, sure_saniye, log):
    ek=''
    if analiz_notlari and analiz_notlari.strip(): ek=f"\nÖNEMLİ VİDEO ANALİZ NOTLARI:\n{analiz_notlari.strip()}\n"
    return router.video_analiz_et(video_bytes,mime_type,forensic_analiz_promptunu_olustur(ek,sure_saniye),VIDEO_ANALYSIS_SCHEMA,log)


def _research_calistir(router, video_state, log):
    content=girdi_birlestir(durumu_metne_donustur('VIDEO IDENTITY',video_state.get('video_identity',{})),durumu_metne_donustur('OBSERVED FACTS',video_state.get('observed_facts',[])),durumu_metne_donustur('UNKNOWNS',video_state.get('unknowns',[])),durumu_metne_donustur('POSSIBLE INFERENCE',video_state.get('possible_inference',[])),durumu_metne_donustur('ARAŞTIRMA İHTİYAÇLARI',video_state.get('viral_arastirma_ihtiyaclari',[])))
    return router.metin_uret(content,research_promptunu_olustur(),FACT_LOCK_SCHEMA,log,arama_kullan=True)


def _editorial_calistir(router, video_state, fact_state, notes, log):
    content=girdi_birlestir(durumu_metne_donustur('VIDEO STATE',video_state),durumu_metne_donustur('FACT LOCK',fact_state),notes or '')
    return router.metin_uret(content,editorial_promptunu_olustur(),EDITORIAL_SCHEMA,log,arama_kullan=False)


def _reels_creative_calistir(router, editorial_state, fact_state, video_state, notes, sure_saniye, ton, log, kelime_hizi_orani=None, ek_talimat=""):
    content=girdi_birlestir(durumu_metne_donustur('VIDEO STATE',video_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state),notes or '')
    prompt=reels_creative_promptunu_olustur(sure_saniye,ton,kelime_hizi_orani,ek_talimat=ek_talimat)
    return router.metin_uret(content,prompt,REELS_CREATIVE_SCHEMA,log,arama_kullan=False)


def _kelime_sayisi(metin):
    return len(re.findall(r"\b[\wÇĞİÖŞÜçğıöşüÀ-ÿ]+(?:[-'][\wÇĞİÖŞÜçğıöşüÀ-ÿ]+)*\b", str(metin or ""), re.UNICODE))


def _reels_kelime_kontrolu(reels_state, sure_saniye, kelime_hizi_orani=None):
    hedef, minimum, maksimum, _, _ = _reels_kelime_ayarlarini_hazirla(sure_saniye, kelime_hizi_orani or KELIME_HIZI_ORANI)
    adet=_kelime_sayisi(reels_state.get('seslendirme_metni',''))
    return adet, hedef, minimum, maksimum


def _duo_kelime_sayisi(duo_script):
    if not duo_script or not duo_script.get('segments'): return 0
    return sum(_kelime_sayisi(seg.get('text','')) for seg in duo_script.get('segments',[]) if isinstance(seg,dict))


def _duo_plan_hazirla(reels_state):
    strategy = normalize_duo_strategy(reels_state)
    strategy["conversation_map"] = normalize_conversation_map(reels_state)
    return strategy


def _duo_script_calistir(router, duo_plan, editorial_state, fact_state, video_state, log):
    """Conversation Map'ten gerçek speaker script üretir."""
    contract = build_duo_generation_contract(duo_plan)
    editorial = durumu_metne_donustur('EDITORIAL', editorial_state)
    facts = durumu_metne_donustur('FACT LOCK', fact_state)
    video = durumu_metne_donustur('VIDEO', video_state)
    prompt = build_generation_prompt(contract, editorial_context=girdi_birlestir(editorial, video), fact_lock=facts)
    try:
        generated, model = router.metin_uret(girdi_birlestir(editorial, video, facts), prompt, DUO_SCRIPT_SCHEMA, log, arama_kullan=False)
        segments = validate_generated_duo(contract, generated)
        if not segments:
            raise ValueError('Duo script doğrulama sonrası boş kaldı.')
        return {"contract": contract, "segments": segments, "model": model, "status": "ready"}
    except Exception as exc:
        log(f'⚠️ Duo script üretimi başarısız; legacy tek sesli akış korunuyor: {str(exc)[:180]}')
        return {"contract": contract, "segments": [], "model": "hata", "status": "fallback", "error": str(exc)[:180]}


def _duo_ses_veya_legacy_uret(router, duo_script, legacy_text, legacy_voice, log, output_path):
    """Duo hazırsa Autonoe+Charon timeline üretir; hata halinde eski TTS'e döner."""
    if duo_script and duo_script.get('status') == 'ready' and duo_script.get('segments'):
        ok, info, _ = duo_ses_uret(router, duo_script['segments'], output_path, log, hiz_carpani=SES_HIZ_CARPANI)
        if ok and os.path.exists(output_path):
            return True, info, 'DUO'
    ok, info = router.ses_uret(legacy_text, legacy_voice, output_path, log, hiz_carpani=SES_HIZ_CARPANI)
    return ok, info, 'LEGACY'


def _ses_sure_uyumlu_mu(ses_dosyasi, video_suresi):
    ses_suresi=_ses_suresini_al(ses_dosyasi) if ses_dosyasi and os.path.exists(ses_dosyasi) else 0.0
    if video_suresi<=0 or ses_suresi<=0: return False, ses_suresi, 0.0
    oran=ses_suresi/video_suresi
    return VOICE_DURATION_MIN_RATIO <= oran <= VOICE_DURATION_MAX_RATIO, ses_suresi, oran


def _reels_ve_ses_uyumlu_uret(router, editorial_state, fact_state, video_state, notes, sure_saniye, ton, legacy_voice, log):
    """Seslendirme metnini ve gerçek TTS süresini birlikte doğrular.

    Eski akışta metin fazla uzadığında FFmpeg videoyu 0.50x'e kadar yavaşlatıp
    sesi -shortest ile kesebiliyordu. Burada render'dan ÖNCE en fazla iki kez
    metin/TTS yenilemesi yapılır. Hâlâ sığmıyorsa önce legacy TTS denenir; o da
    sığmıyorsa hatalı/yarım video üretmek yerine render engellenir.
    """
    ek_talimat=""
    son_reels={}
    son_model='hata'
    son_duo_plan={}
    son_duo_script={}
    son_ses=''
    son_info=None
    son_mod='LEGACY'

    for deneme in range(VOICE_REGEN_MAX+1):
        reels_state,model_reels=_reels_creative_calistir(router,editorial_state,fact_state,video_state,notes,sure_saniye,ton,log,KELIME_HIZI_ORANI,ek_talimat=ek_talimat)
        son_reels,son_model=reels_state,model_reels
        adet,hedef,minimum,maksimum=_reels_kelime_kontrolu(reels_state,sure_saniye,KELIME_HIZI_ORANI)
        log(f'📝 Seslendirme uzunluk kontrolü: {adet} kelime | hedef {hedef} | izin verilen {minimum}-{maksimum}')

        duo_plan=_duo_plan_hazirla(reels_state)
        log_ekle_duo='🗣️ Duo konuşma metni hazırlanıyor...' if deneme==0 else f'🗣️ Duo konuşma metni yenileniyor ({deneme}/{VOICE_REGEN_MAX})...'
        log(log_ekle_duo)
        duo_script=_duo_script_calistir(router,duo_plan,editorial_state,fact_state,video_state,log)
        son_duo_plan,son_duo_script=duo_plan,duo_script
        duo_adet=_duo_kelime_sayisi(duo_script)

        kelime_sorunu = adet < minimum or adet > maksimum
        if duo_script.get('status')=='ready' and duo_adet:
            kelime_sorunu = kelime_sorunu or duo_adet < minimum or duo_adet > maksimum
            if duo_adet != adet:
                log(f'🗣️ Duo script uzunluğu: {duo_adet} kelime | legacy metin: {adet} kelime')

        if kelime_sorunu and deneme < VOICE_REGEN_MAX:
            ek_talimat=(
                f'Önceki üretim {adet} kelimeydi; hedef {hedef}, izin verilen {minimum}-{maksimum}. '
                'Bu kez SESLENDİRME METNİNİ mutlaka bu aralıkta tut. Bilgi yığma, cümleleri kısalt, '
                'teknik detayları yalnızca hikâyeyi taşıyanları seç ve içerik tonunun dışına çıkma. '
                'Aynı bilgiyi tekrar etme. Video süresini aşacak uzunlukta metin üretme.'
            )
            log(f'⚠️ Seslendirme kelime aralığı dışında; yeniden üretim başlatılıyor ({deneme+1}/{VOICE_REGEN_MAX}).')
            continue
        if kelime_sorunu:
            log('⚠️ Seslendirme metni hâlâ hedef aralık dışında; TTS sonrası süre doğrulaması uygulanacak.')

        ses_dosyasi=gecici_ses_yolu()
        ok,info,mod=_duo_ses_veya_legacy_uret(router,duo_script,reels_state.get('seslendirme_metni',''),legacy_voice,log,ses_dosyasi)
        if not ok:
            temp_dosya_temizle(ses_dosyasi)
            if deneme < VOICE_REGEN_MAX:
                ek_talimat='Seslendirme doğal okunabilirlikte ve hedef kelime aralığında olsun. Önceki üretim TTS tarafında başarısız oldu; metni sadeleştir ve yeniden üret.'
                continue
            return reels_state,model_reels,duo_plan,duo_script,False,None,'LEGACY',''

        uyumlu,ses_suresi,oran=_ses_sure_uyumlu_mu(ses_dosyasi,sure_saniye)
        log(f'🎚️ TTS gerçek süre kontrolü: video {sure_saniye:.2f}s → ses {ses_suresi:.2f}s | oran {oran:.2f}x')
        if uyumlu:
            return reels_state,model_reels,duo_plan,duo_script,True,info,mod,ses_dosyasi

        # Duo sesi fazla uzunsa, önce aynı reels metniyle legacy Autonoe dene.
        # Bu, iki karakterli katman sorunlu olduğunda çalışan eski sistemi korur.
        if mod=='DUO':
            legacy_path=gecici_ses_yolu()
            legacy_ok,legacy_info=router.ses_uret(reels_state.get('seslendirme_metni',''),legacy_voice,legacy_path,log,hiz_carpani=SES_HIZ_CARPANI)
            legacy_uyum,legacy_sure,legacy_oran=_ses_sure_uyumlu_mu(legacy_path,sure_saniye)
            if legacy_ok and legacy_uyum:
                temp_dosya_temizle(ses_dosyasi)
                log(f'↩️ Duo TTS süreye sığmadı; legacy Autonoe güvenli geri dönüş kullanıldı ({legacy_oran:.2f}x).')
                return reels_state,model_reels,duo_plan,duo_script,True,legacy_info,'LEGACY',legacy_path
            temp_dosya_temizle(legacy_path)

        temp_dosya_temizle(ses_dosyasi)
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

    return son_reels,son_model,son_duo_plan,son_duo_script,False,son_info,son_mod,son_ses


def _qa_calistir(router,video_state,fact_state,editorial_state,reels_state,caption_state,threads_state,sure_saniye,log,duo_plan=None,duo_script=None):
    content=girdi_birlestir(durumu_metne_donustur('VIDEO',video_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state),durumu_metne_donustur('REELS',reels_state),durumu_metne_donustur('DUO PLAN',duo_plan or {}),durumu_metne_donustur('DUO SCRIPT',duo_script or {}),durumu_metne_donustur('CAPTION',caption_state),durumu_metne_donustur('THREADS',threads_state),f'VIDEO SÜRESİ: {sure_saniye}')
    return router.metin_uret(content,qa_promptunu_olustur(),QA_SCHEMA,log,arama_kullan=False)


def pipeline_calistir(router,video_bytes,mime_type,temp_input_video,video_analiz_notlari,metin_uretim_notlari,sure_saniye,icerik_tonu,secilen_ses_ingilizce,log_ekle,ilerlemeyi_guncelle=None):
    state={}
    _ilerleme(ilerlemeyi_guncelle,1); log_ekle('🎥 Video analiz ediliyor (Forensic)...')
    video_state,_=_forensic_analiz_calistir(router,video_bytes,mime_type,video_analiz_notlari,sure_saniye,log_ekle); state['video_state']=video_state
    _ilerleme(ilerlemeyi_guncelle,2); log_ekle('🔎 Gerçekler doğrulanıyor (Research / Fact Lock)...')
    fact_state,_=_research_calistir(router,video_state,log_ekle); state['fact_state']=fact_state
    _ilerleme(ilerlemeyi_guncelle,3); log_ekle('🧠 Hikâye seçiliyor (Editorial Brain)...')
    editorial_state,_=_editorial_calistir(router,video_state,fact_state,metin_uretim_notlari,log_ekle); state['editorial_state']=editorial_state
    _ilerleme(ilerlemeyi_guncelle,4); log_ekle('🎙️ Reels hazırlanıyor (Cover + Hook + Voiceover)...')
    legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce, str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi=_reels_ve_ses_uyumlu_uret(router,editorial_state,fact_state,video_state,metin_uretim_notlari,sure_saniye,icerik_tonu,legacy_voice,log_ekle)
    state['reels_state']=reels_state; state['duo_plan']=duo_plan; state['duo_script']=duo_script; state['ses_modu']=ses_modu
    if ses_basarili and ses_dosyasi and os.path.exists(ses_dosyasi): state['ses_dosyasi_son']=ses_dosyasi

    _ilerleme(ilerlemeyi_guncelle,5); log_ekle('📝 Caption + hashtag hazırlanıyor...')
    try: caption_state,model_caption=_caption_calistir(router,reels_state,fact_state,editorial_state,video_state,log_ekle)
    except Exception as e: log_ekle(f'⚠️ Caption üretilemedi: {str(e)[:150]}'); caption_state,model_caption={'reels_aciklamasi':'','reels_hashtagleri':[]},'hata'
    state['caption_state']=caption_state
    _ilerleme(ilerlemeyi_guncelle,6); log_ekle('🧵 Threads hazırlanıyor...')
    try: threads_state,model_threads=_threads_calistir(router,video_state,fact_state,editorial_state,log_ekle)
    except Exception as e: log_ekle(f'⚠️ Threads üretilemedi: {str(e)[:150]}'); threads_state,model_threads={'threads_aciklamasi':''},'hata'
    state['threads_state']=threads_state
    _ilerleme(ilerlemeyi_guncelle,7); log_ekle('🔍 Son kalite kontrol (QA)...')
    qa_state,_=_qa_calistir(router,video_state,fact_state,editorial_state,reels_state,caption_state,threads_state,sure_saniye,log_ekle,duo_plan,duo_script); state['qa_state_final']=qa_state
    _ilerleme(ilerlemeyi_guncelle,8); log_ekle('🎧 Ses üretiliyor...')
    if ses_basarili:
        log_ekle(f'🎧 Hazır ses kullanılıyor ({ses_modu}); tekrar TTS üretilmiyor.')
    else:
        log_ekle('❌ Güvenli TTS üretilemedi; final video render edilmeyecek.')
    _ilerleme(ilerlemeyi_guncelle,9); log_ekle('🎬 Videoya AI sesi ekleniyor (FFmpeg)...')
    output=gecici_dosya_yolu('output','mp4')
    render_ok=ses_basarili and video_ve_sesi_birlestir(temp_input_video,ses_dosyasi,output,log_ekle)
    final=output if render_ok and os.path.exists(output) else ''
    log_ekle('🏁 Pipeline tamamlandı.')
    return {'seslendirme_metni':reels_state.get('seslendirme_metni',''),'reels_aciklamasi':caption_state.get('reels_aciklamasi',''),'reels_hashtagleri':caption_state.get('reels_hashtagleri',[]),'kapak_basliklari':reels_state.get('kapak_basliklari',[]),'threads_aciklamasi':threads_state.get('threads_aciklamasi',''),'ses_basarili':ses_basarili,'ses_dosyasi':ses_dosyasi,'secilen_ses_ingilizce':legacy_voice,'kullanilan_metin_modeli':model_reels,'kullanilan_ses_modeli':kullanilan_ses_modeli,'kullanilan_threads_modeli':model_threads,'final_video':final,'temp_input_video':temp_input_video,'fact_lock':fact_state,'editorial_brief':editorial_state,'selected_hook':_secilen_hook_getir(reels_state),'duo_plan':duo_plan,'duo_script':duo_script,'qa_result':qa_state,'pipeline_state':state}


def metin_pipeline_calistir(router, metin, icerik_tonu, secilen_ses_ingilizce, log_ekle, ilerlemeyi_guncelle=None, sure_saniye=30):
    metin=(metin or '').strip()
    if not metin: raise ValueError('Metin girdisi boş.')
    state={}
    video_state={'video_identity':{'brand':'UNKNOWN','exact_model':'UNKNOWN','confidence':'unknown','source':'telegram_text'},'observed_facts':[metin],'unknowns':[],'possible_inference':[],'viral_arastirma_ihtiyaclari':['Metindeki araç/konu kimliğini ve güncel iddiaları doğrula.'],'visual_opportunities':['Metin tabanlı üretim; video görsel zaman çizelgesi yok.'],'timeline':[]}
    state['video_state']=video_state
    _ilerleme(ilerlemeyi_guncelle,1,'📝 Metin girdisi'); log_ekle('📝 Metin girdisi işleniyor (video analizi atlanıyor)...')
    _ilerleme(ilerlemeyi_guncelle,2,'🔎 Research / Fact Lock'); fact_state,_=_research_calistir(router,video_state,log_ekle); state['fact_state']=fact_state
    _ilerleme(ilerlemeyi_guncelle,3,'🧠 Editorial Brain'); editorial_state,_=_editorial_calistir(router,video_state,fact_state,metin,log_ekle); state['editorial_state']=editorial_state
    _ilerleme(ilerlemeyi_guncelle,4,'🎙️ Reels Creative'); legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce,str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi=_reels_ve_ses_uyumlu_uret(router,editorial_state,fact_state,video_state,metin,sure_saniye,icerik_tonu,legacy_voice,log_ekle)
    state['reels_state']=reels_state; state['duo_plan']=duo_plan; state['duo_script']=duo_script; state['ses_modu']=ses_modu
    if ses_basarili and ses_dosyasi and os.path.exists(ses_dosyasi): state['ses_dosyasi_son']=ses_dosyasi
    _ilerleme(ilerlemeyi_guncelle,5,'📝 Caption + hashtag')
    try: caption_state,model_caption=_caption_calistir(router,reels_state,fact_state,editorial_state,video_state,log_ekle)
    except Exception as e: log_ekle(f'⚠️ Caption üretilemedi: {str(e)[:150]}'); caption_state,model_caption={'reels_aciklamasi':'','reels_hashtagleri':[]},'hata'
    state['caption_state']=caption_state
    _ilerleme(ilerlemeyi_guncelle,6,'🧵 Threads')
    try: threads_state,model_threads=_threads_calistir(router,video_state,fact_state,editorial_state,log_ekle)
    except Exception as e: log_ekle(f'⚠️ Threads üretilemedi: {str(e)[:150]}'); threads_state,model_threads={'threads_aciklamasi':''},'hata'
    state['threads_state']=threads_state
    _ilerleme(ilerlemeyi_guncelle,7,'🔍 QA')
    qa_state,_=_qa_calistir(router,video_state,fact_state,editorial_state,reels_state,caption_state,threads_state,sure_saniye,log_ekle,duo_plan,duo_script); state['qa_state_final']=qa_state
    _ilerleme(ilerlemeyi_guncelle,8,'🎧 Ses üretiliyor...')
    if ses_basarili: log_ekle(f'🎧 Hazır ses kullanılıyor ({ses_modu}); tekrar TTS üretilmiyor.')
    else: log_ekle('❌ Güvenli TTS üretilemedi.')
    log_ekle('🏁 Metin üretimi tamamlandı; video render atlandı.')
    return {'mode':'text','seslendirme_metni':reels_state.get('seslendirme_metni',''),'reels_aciklamasi':caption_state.get('reels_aciklamasi',''),'reels_hashtagleri':caption_state.get('reels_hashtagleri',[]),'kapak_basliklari':reels_state.get('kapak_basliklari',[]),'threads_aciklamasi':threads_state.get('threads_aciklamasi',''),'ses_basarili':ses_basarili,'ses_dosyasi':ses_dosyasi,'secilen_ses_ingilizce':legacy_voice,'kullanilan_metin_modeli':model_reels,'kullanilan_ses_modeli':kullanilan_ses_modeli,'kullanilan_threads_modeli':model_threads,'final_video':'','temp_input_video':'','fact_lock':fact_state,'editorial_brief':editorial_state,'selected_hook':_secilen_hook_getir(reels_state),'duo_plan':duo_plan,'duo_script':duo_script,'qa_result':qa_state,'pipeline_state':state}
