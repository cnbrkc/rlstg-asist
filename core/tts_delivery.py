"""Seslendirme yönergesini konuşulan metinden ayırır.

Üretim sesi `gemini-2.5-flash-preview-tts`. Bu model transkripti kelimesi
kelimesine okur; köşeli parantez etiketleri Gemini 3.1'in İngilizce tag
setine aittir. `[vurgulu]`, `[alaycı]`, `[savunarak]` gibi Türkçe sahne
yönergeleri 2.5'te (ve 3.1'in eğitilmediği etiketlerde) yüksek sesle
okunur. Vurgu kalsın, kelime okunmasın: yönerge İngilizce teslimat notuna
çıkar, transkriptte yalnız duyulacak sözler kalır.
"""
import re

_FOLD = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")


def _fold(metin) -> str:
    return str(metin or "").casefold().translate(_FOLD)


# Bilinen sahne yönergeleri → modelin OKUMAMASI gereken, prosodiye uygulanacak not.
# Anahtarlar katlanmış (ş→s, ü→u) tam ifade veya tek kelimedir.
_STIL = (
    (("vurgulu", "vurgulayarak", "vurgu", "emphasis", "emphatic"),
     "stress the key claim; slightly slower and firmer on the important words, then return to conversational pace"),
    (("alayci", "alay ederek", "alayla", "ironik", "igneleyici", "sarkastik", "muzip"),
     "dry sarcasm, underplayed, a small smirk in the voice; do not turn this into laughter or extra words"),
    (("savunarak", "savunmaci", "itiraz ederek", "karsi cikarak"),
     "defensive but calm, pushing back without raising volume"),
    (("sasirarak", "saskin", "sok olmus", "hayretle", "sasirmis"),
     "a brief genuine surprise on the first words, then settle back to conversation"),
    (("gulerek", "gulumsayarak", "kahkaha", "kikirdayarak", "gulerek soyle"),
     "a short dry chuckle as a non-speech reaction, then speak only the transcript words"),
    (("kizgin", "sinirli", "ofkeyle", "sert"),
     "controlled irritation, clipped, not yelling"),
    (("fisildayarak", "fisilti", "alcak sesle", "fisildar"),
     "slightly quieter and conspiratorial, not a stage whisper"),
    (("ciddi", "agir", "net"),
     "grounded and serious, no smile in the voice"),
    (("merakli", "merakla", "sorar gibi"),
     "curious, a slight rise if the line is a question"),
    (("heyecanli", "coskulu"),
     "a notch more energy, still conversational, not a commercial"),
    (("duraksayarak", "duraksama", "bekleyerek", "nefes"),
     "a short thoughtful beat before the line, then natural pace"),
    (("kabul ederek", "anlayarak", "farkina vararak"),
     "the realization lands, then the line is said plainly"),
    (("tepki", "tepkiyle", "aninda tepki"),
     "instant genuine reaction to the previous line — a quick laugh, scoff or beat of surprise as the words imply — then speak only the transcript words"),
    (("onaylayarak", "tasdikle", "sicak onay"),
     "warm quick approval, a nod in the voice, then the line"),
    (("umursamaz", "kayitsiz", "bosvererek", "ilgisizce"),
     "dry, dismissive, unimpressed; let the indifference color the whole line"),
    (("abartarak", "abartiyla", "abartili"),
     "playful exaggeration for comic effect, still credible"),
    (("israrla", "ustune giderek", "baski kurarak"),
     "pressing and insistent, leaning into the point without yelling"),
    (("eglenerek", "keyifle", "eglenceli"),
     "clearly amused, smiling warmth in the voice"),
)

_STIL_HARITASI = {anahtar: stil for anahtarlar, stil in _STIL for anahtar in anahtarlar}
_YONERGE_KELIMELERI = frozenset(_STIL_HARITASI)


def _stil_bul(ham: str) -> str:
    """Yönerge metnini İngilizce teslimat notuna çevirir. Bilinmeyen kısa etiket
    de okunmaz; genel bir vurgu notuna düşer (Türkçe kelime nota yazılmaz,
    modele sızıp okunmasın)."""
    kat = _fold(ham)
    kat = re.sub(r"[^a-z0-9\s]", " ", kat)
    kat = re.sub(r"\s+", " ", kat).strip()
    if not kat or kat in {"...", "…"}:
        return _STIL_HARITASI["duraksama"]
    if kat in _STIL_HARITASI:
        return _STIL_HARITASI[kat]
    for anahtar, stil in _STIL_HARITASI.items():
        if anahtar in kat:
            return stil
    kelimeler = kat.split()
    if kelimeler and all(k in _YONERGE_KELIMELERI or k in {"bir", "olarak", "sekilde", "tonunda", "tonuyla"} for k in kelimeler):
        return "slightly more colored delivery on this turn, still conversational; do not speak the direction"
    return ""


def _yenerge_parcasi_mi(ic: str) -> bool:
    """Köşeli/parantez içi sahne yönergesi mi, yoksa duyulacak bir bilgi mi?"""
    ham = str(ic or "").strip()
    if not ham or len(ham) > 48:
        return False
    if re.search(r"\d", ham):
        return False
    if _stil_bul(ham):
        return True
    kelimeler = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşüâîû]+", ham)
    return 0 < len(kelimeler) <= 4 and all(_fold(k) in _YONERGE_KELIMELERI for k in kelimeler)


def replik_tts_hazirla(metin: str, tts_tag: str = "") -> tuple:
    """Konuşulacak metin + İngilizce teslimat notu.

    `tts_tag` ve metnin içindeki `[vurgulu]` / `(alaycı)` yönergeleri transkriptten
    çıkarılır. Düz cümle içindeki 'savunarak söylüyorum' gibi gerçek sözler kalır.
    """
    ham_tag = str(tts_tag or "").strip()
    ham_metin = str(metin or "").strip()
    stiller = []
    if ham_tag:
        stil = _stil_bul(ham_tag) or _stil_bul(re.sub(r"[\[\]\(\)\{\}]", " ", ham_tag))
        if stil:
            stiller.append(stil)
        elif _yenerge_parcasi_mi(re.sub(r"[\[\]\(\)\{\}]", " ", ham_tag)):
            stiller.append("slightly more colored delivery on this turn, still conversational")

    def _degistir(eslesme):
        ic = eslesme.group(1)
        if not _yenerge_parcasi_mi(ic):
            return eslesme.group(0)
        stil = _stil_bul(ic)
        if stil:
            stiller.append(stil)
        return " "

    konusma = re.sub(r"[\[\(\{]([^\[\]\(\)\{\}]{1,48})[\]\)\}]", _degistir, ham_metin)
    konusma = re.sub(r"\s+", " ", konusma).strip(" \t-–—")
    # Yalnız "Vurgulu:" / "Alay ederek," gibi saf sahne öneki. "Savunarak söylüyorum," gerçek sözdür.
    onek = re.match(
        r"^\s*((?:[A-Za-zÇĞİÖŞÜçğıöşüâîû]+\s+){0,2}[A-Za-zÇĞİÖŞÜçğıöşüâîû]+)\s*[:,]\s*",
        konusma,
    )
    if onek and _fold(onek.group(1)) in _STIL_HARITASI:
        stiller.append(_STIL_HARITASI[_fold(onek.group(1))])
        konusma = konusma[onek.end():]
    konusma = re.sub(r"\s+", " ", konusma).strip(" \t-–—")
    konusma = re.sub(r"\s+([,.;:!?])", r"\1", konusma)
    # Aynı notu iki kez yazma.
    benzersiz = []
    for stil in stiller:
        if stil and stil not in benzersiz:
            benzersiz.append(stil)
    return konusma, "; ".join(benzersiz)


def script_metnini_konusmaya_cek(script_state: dict) -> dict:
    """Segment `text` alanından sahne yönergesini siler; `tts_tag` durur."""
    if not isinstance(script_state, dict):
        return script_state or {}
    yeni = dict(script_state)
    segments = []
    for seg in script_state.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        kopya = dict(seg)
        konusma, _ = replik_tts_hazirla(kopya.get("text"), "")
        if konusma:
            kopya["text"] = konusma
            segments.append(kopya)
    yeni["segments"] = segments
    soru, _ = replik_tts_hazirla(script_state.get("yorum_tetikleyici_soru"), "")
    if soru:
        yeni["yorum_tetikleyici_soru"] = soru
    return yeni


def segment_konusma(segment: dict) -> tuple:
    """TTS segmentinden (text + tts_tag veya hazır style) konuşma ve notu alır."""
    if not isinstance(segment, dict):
        return "", ""
    konusma, cikarilan = replik_tts_hazirla(segment.get("text"), segment.get("tts_tag"))
    hazir = str(segment.get("style") or "").strip()
    if hazir and cikarilan:
        stil = hazir if cikarilan in hazir else f"{hazir}; {cikarilan}"
    else:
        stil = hazir or cikarilan
    return konusma, stil


def teslimat_blogu(segments) -> str:
    """Konuşmacı turlarına bağlı, okunmayacak İngilizce teslimat notu."""
    satirlar = []
    for i, seg in enumerate(segments or [], 1):
        if not isinstance(seg, dict):
            continue
        _, stil = segment_konusma(seg)
        if not stil:
            continue
        speaker = str(seg.get("speaker") or "").strip()
        etiket = f"Turn {i}" + (f" ({speaker})" if speaker else "")
        satirlar.append(f"{etiket}: {stil}")
    return "\n".join(satirlar)


def transkripti_ayikla(metin: str) -> tuple:
    """Tek veya çok satırlı transkriptten yönergeleri ayıklar.

    `Autonoe: [vurgulu] ...` satırlarında konuşmacı etiketi korunur.
    Dönüş: (temiz transkript, teslimat notu).
    """
    satirlar = []
    notlar = []
    for i, satir in enumerate(str(metin or "").splitlines(), 1):
        m = re.match(r"^(Autonoe|Charon|female|male)\s*:\s*(.*)$", satir.strip(), re.I)
        if m:
            konusma, stil = replik_tts_hazirla(m.group(2), "")
            satirlar.append(f"{m.group(1)}: {konusma}".rstrip())
            if stil:
                notlar.append(f"Turn {i} ({m.group(1)}): {stil}")
        else:
            konusma, stil = replik_tts_hazirla(satir, "")
            if konusma:
                satirlar.append(konusma)
            if stil:
                notlar.append(f"Turn {i}: {stil}")
    return "\n".join(satirlar).strip(), "\n".join(notlar)


def teslimatlari_birlestir(*parcalar) -> str:
    gorulen = []
    for parca in parcalar:
        for satir in str(parca or "").splitlines():
            satir = satir.strip()
            if satir and satir not in gorulen:
                gorulen.append(satir)
    return "\n".join(gorulen)
