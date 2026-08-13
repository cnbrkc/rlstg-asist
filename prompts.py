import os
_PROMPT_DIR=os.path.join(os.path.dirname(__file__),"prompts")
def _oku(ad):
    with open(os.path.join(_PROMPT_DIR,ad),encoding="utf-8") as f:return f.read()
def forensic_analiz_promptunu_olustur(ek_notlar="",sure_saniye=0):
    template=_oku("forensic_analysis_prompt.txt")
    return template.replace("{ek_notlar_bolumu}", ek_notlar or "").replace("{sure_saniye}", str(sure_saniye))
def research_promptunu_olustur(): return _oku("research_prompt.txt")+"\n"+_oku("guncellik_talimati.txt")
def editorial_promptunu_olustur(): return _oku("editorial_prompt.txt")
def reels_creative_promptunu_olustur(sure_saniye,icerik_tonu,kelime_hizi_orani=None): return _oku("reels_creative_prompt.txt")+f"\nHedef süre: {sure_saniye} saniye. Ton: {icerik_tonu}. Kelime oranı: {kelime_hizi_orani or 2.4}."
def caption_promptunu_olustur(): return _oku("caption_prompt.txt")
def threads_promptunu_olustur(): return _oku("threads_promptu.txt")
def qa_promptunu_olustur(): return _oku("qa_prompt.txt")
def durumu_metne_donustur(baslik,deger): return f"### {baslik}\n{deger}"
def girdi_birlestir(*parcalar): return "\n\n".join(str(x) for x in parcalar if x is not None)