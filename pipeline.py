"""
pipeline.py — Ultimate Content Engine orkestratörü.

Streamlit bağımlılığı yoktur; Telegram/GitHub Actions tarafından doğrudan çağrılabilir.
"""
import os
from config import KELIME_HIZI_ORANI, SES_HIZ_CARPANI, PIPELINE_ADIMLARI
from schemas import VIDEO_ANALYSIS_SCHEMA, FACT_LOCK_SCHEMA, EDITORIAL_SCHEMA, REELS_CREATIVE_SCHEMA, CAPTION_SCHEMA, THREADS_SCHEMA, QA_SCHEMA, DUO_SCRIPT_SCHEMA
from prompts import (forensic_analiz_promptunu_olustur, research_promptunu_olustur, editorial_promptunu_olustur,
                     reels_creative_promptunu_olustur, caption_promptunu_olustur, threads_promptunu_olustur,
                     qa_promptunu_olustur, durumu_metne_donustur, girdi_birlestir)
from media import gecici_ses_yolu, gecici_dosya_yolu, temp_dosya_temizle, video_ve_sesi_birlestir, _ses_suresini_al
from duo_strategy import normalize_duo_strategy
from duo_script import normalize_conversation_map
from duo_script_engine import build_duo_generation_contract, build_generation_prompt, validate_generated_duo

TOPLAM_ADIM = len(PIPELINE_ADIMLARI)

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

def _reels_creative_calistir(router, editorial_state, fact_state, video_state, notes, sure_saniye, ton, log, kelime_hizi_orani=None):
    content=girdi_birlestir(durumu_metne_donustur('VIDEO STATE',video_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state),notes or '')
    return router.metin_uret(content,reels_creative_promptunu_olustur(sure_saniye,ton,kelime_hizi_orani),REELS_CREATIVE_SCHEMA,log,arama_kullan=False)

def _caption_calistir(router,reels_state,fact_state,editorial_state,video_state,log):
    content=girdi_birlestir(durumu_metne_donustur('REELS',reels_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state),durumu_metne_donustur('VIDEO',video_state))
    return router.metin_uret(content,caption_promptunu_olustur(),CAPTION_SCHEMA,log,arama_kullan=False)

def _threads_calistir(router,video_state,fact_state,editorial_state,log):
    content=girdi_birlestir(durumu_metne_donustur('VIDEO',video_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state))
    return router.metin_uret(content,threads_promptunu_olustur(),THREADS_SCHEMA,log,arama_kullan=False)

def _duo_plan_hazirla(reels_state):
    strategy = normalize_duo_strategy(reels_state)
    strategy["conversation_map"] = normalize_conversation_map(reels_state)
    return strategy

def _duo_script_calistir(router, duo_plan, editorial_state, fact_state, video_state, log):
    """Conversation Map'ten gerçek speaker script üretir; legacy VO'yu değiştirmez."""
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
    reels_state,model_reels=_reels_creative_calistir(router,editorial_state,fact_state,video_state,metin_uretim_notlari,sure_saniye,icerik_tonu,log_ekle); state['reels_state']=reels_state
    duo_plan = _duo_plan_hazirla(reels_state); state['duo_plan'] = duo_plan
    log_ekle('🗣️ Duo konuşma metni hazırlanıyor...')
    duo_script = _duo_script_calistir(router,duo_plan,editorial_state,fact_state,video_state,log_ekle); state['duo_script'] = duo_script
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
    ses_adi = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce, str) and secilen_ses_ingilizce.strip() else 'Puck'
    ses_dosyasi=gecici_ses_yolu(); ses_basarili,kullanilan_ses_modeli=router.ses_uret(reels_state.get('seslendirme_metni',''),ses_adi,ses_dosyasi,log_ekle,hiz_carpani=SES_HIZ_CARPANI)
    if ses_basarili and os.path.exists(ses_dosyasi): state['ses_dosyasi_son']=ses_dosyasi
    _ilerleme(ilerlemeyi_guncelle,9); log_ekle('🎬 Videoya AI sesi ekleniyor (FFmpeg)...')
    output=gecici_dosya_yolu('output','mp4')
    render_ok=ses_basarili and video_ve_sesi_birlestir(temp_input_video,ses_dosyasi,output,log_ekle)
    final=output if render_ok and os.path.exists(output) else ''
    log_ekle('🏁 Pipeline tamamlandı.')
    return {'seslendirme_metni':reels_state.get('seslendirme_metni',''),'reels_aciklamasi':caption_state.get('reels_aciklamasi',''),'reels_hashtagleri':caption_state.get('reels_hashtagleri',[]),'kapak_basliklari':reels_state.get('kapak_basliklari',[]),'threads_aciklamasi':threads_state.get('threads_aciklamasi',''),'ses_basarili':ses_basarili,'ses_dosyasi':ses_dosyasi,'secilen_ses_ingilizce':ses_adi,'kullanilan_metin_modeli':model_reels,'kullanilan_ses_modeli':kullanilan_ses_modeli,'kullanilan_threads_modeli':model_threads,'final_video':final,'temp_input_video':temp_input_video,'fact_lock':fact_state,'editorial_brief':editorial_state,'selected_hook':_secilen_hook_getir(reels_state),'duo_plan':duo_plan,'duo_script':duo_script,'qa_result':qa_state,'pipeline_state':state}

def metin_pipeline_calistir(router, metin, icerik_tonu, secilen_ses_ingilizce, log_ekle, ilerlemeyi_guncelle=None, sure_saniye=30):
    metin=(metin or '').strip()
    if not metin: raise ValueError('Metin girdisi boş.')
    state={}
    video_state={'video_identity':{'brand':'UNKNOWN','exact_model':'UNKNOWN','confidence':'unknown','source':'telegram_text'},'observed_facts':[metin],'unknowns':[],'possible_inference':[],'viral_arastirma_ihtiyaclari':['Metindeki araç/konu kimliğini ve güncel iddiaları doğrula.'],'visual_opportunities':['Metin tabanlı üretim; video görsel zaman çizelgesi yok.'],'timeline':[]}
    state['video_state']=video_state
    _ilerleme(ilerlemeyi_guncelle,1,'📝 Metin girdisi'); log_ekle('📝 Metin girdisi işleniyor (video analizi atlanıyor)...')
    _ilerleme(ilerlemeyi_guncelle,2,'🔎 Research / Fact Lock'); fact_state,_=_research_calistir(router,video_state,log_ekle); state['fact_state']=fact_state
    _ilerleme(ilerlemeyi_guncelle,3,'🧠 Editorial Brain'); editorial_state,_=_editorial_calistir(router,video_state,fact_state,metin,log_ekle); state['editorial_state']=editorial_state
    _ilerleme(ilerlemeyi_guncelle,4,'🎙️ Reels Creative'); reels_state,model_reels=_reels_creative_calistir(router,editorial_state,fact_state,video_state,metin,sure_saniye,icerik_tonu,log_ekle); state['reels_state']=reels_state
    duo_plan = _duo_plan_hazirla(reels_state); state['duo_plan'] = duo_plan
    duo_script = _duo_script_calistir(router,duo_plan,editorial_state,fact_state,video_state,log_ekle); state['duo_script'] = duo_script
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
    _ilerleme(ilerlemeyi_guncelle,8,'🎧 Autonoe TTS')
    ses_adi = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce,str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    ses_dosyasi=gecici_ses_yolu(); ses_basarili,kullanilan_ses_modeli=router.ses_uret(reels_state.get('seslendirme_metni',''),ses_adi,ses_dosyasi,log_ekle,hiz_carpani=SES_HIZ_CARPANI)
    if ses_basarili and os.path.exists(ses_dosyasi): state['ses_dosyasi_son']=ses_dosyasi
    log_ekle('🏁 Metin üretimi tamamlandı; video render atlandı.')
    return {'mode':'text','seslendirme_metni':reels_state.get('seslendirme_metni',''),'reels_aciklamasi':caption_state.get('reels_aciklamasi',''),'reels_hashtagleri':caption_state.get('reels_hashtagleri',[]),'kapak_basliklari':reels_state.get('kapak_basliklari',[]),'threads_aciklamasi':threads_state.get('threads_aciklamasi',''),'ses_basarili':ses_basarili,'ses_dosyasi':ses_dosyasi,'secilen_ses_ingilizce':ses_adi,'kullanilan_metin_modeli':model_reels,'kullanilan_ses_modeli':kullanilan_ses_modeli,'kullanilan_threads_modeli':model_threads,'final_video':'','temp_input_video':'','fact_lock':fact_state,'editorial_brief':editorial_state,'selected_hook':_secilen_hook_getir(reels_state),'duo_plan':duo_plan,'duo_script':duo_script,'qa_result':qa_state,'pipeline_state':state}
