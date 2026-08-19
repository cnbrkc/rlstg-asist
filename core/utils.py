import json, re

def guvenli_json_yukle(metin: str) -> dict:
    if not metin: raise ValueError("Model boş yanıt verdi.")
    metin=metin.strip()
    if metin.startswith("```"):
        metin=re.sub(r"^```(?:json)?\s*|\s*```$","",metin,flags=re.I|re.S).strip()
    try: return json.loads(metin)
    except json.JSONDecodeError:
        m=re.search(r"\{.*\}",metin,re.S)
        if m: return json.loads(m.group(0))
        raise
