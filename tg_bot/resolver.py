"""Resolve music queries through JioSaavn, SoundCloud, then yt-dlp."""

import base64, glob, json, logging, os, re
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import yt_dlp
try:
    from Crypto.Cipher import DES as _PyCryptoDES
    from Crypto.Util.Padding import unpad as _crypto_unpad
except ImportError: _PyCryptoDES = _crypto_unpad = None
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError: Cipher = algorithms = modes = None
LOGGER=logging.getLogger(__name__); MIN_DURATION=45
YDL_OPTS={"format":"bestaudio/best","noplaylist":True,"quiet":True,"noprogress":True,"no_warnings":True,"skip_download":False,"outtmpl":"/tmp/mp_%(id)s.%(ext)s","nocheckcertificate":True,"socket_timeout":15,"retries":3,"extractor_args":{"youtube":{"player_client":["android","ios"]}}}
class ResolveError(Exception): pass

def _downloaded_path(info:dict)->Optional[str]:
    for e in info.get("requested_downloads") or []:
        p=e.get("filepath") or e.get("_filename")
        if p and os.path.exists(p): return p
    p=info.get("filepath") or info.get("_filename")
    if p and os.path.exists(p): return p
    for p in glob.glob("/tmp/mp_{}.*".format(info.get("id"))):
        if not p.endswith(".part"): return p
    return None

def _value(song:dict,*keys:str):
    for k in keys:
        v=song.get(k)
        if v not in (None,""): return v
    return None

def _decrypt_media_url(encrypted:str)->Optional[str]:
    try:
        raw=base64.b64decode(encrypted); key=b"38346591"
        if _PyCryptoDES: plain=_crypto_unpad(_PyCryptoDES.new(key,_PyCryptoDES.MODE_ECB).decrypt(raw),8)
        elif Cipher:
            d=Cipher(algorithms.TripleDES(key*3),modes.ECB()).decryptor(); p=d.update(raw)+d.finalize(); n=p[-1]
            if not n or n>8 or p[-n:]!=bytes([n])*n: raise ValueError("invalid PKCS padding")
            plain=p[:-n]
        else: LOGGER.error("No DES backend installed"); return None
        u=plain.decode().strip(); return u if u.startswith(("http://","https://")) else None
    except Exception as exc: LOGGER.warning("JioSaavn media URL decryption failed: %r",exc); return None

def _direct_url(song:dict)->Optional[str]:
    more=song.get("more_info") or {}; s=_value(song,"download_url","downloadUrl","media_url","mediaUrl") or _value(more,"download_url","downloadUrl","media_url","mediaUrl")
    if isinstance(s,list): s=(s[-1] or {}).get("url") if s else None
    if isinstance(s,dict): s=s.get("url")
    if isinstance(s,str) and s.startswith(("http://","https://")): return s
    e=_value(song,"encrypted_media_url") or _value(more,"encrypted_media_url")
    return _decrypt_media_url(e) if e else None

def _artist_text(song:dict)->str:
    m=song.get("more_info") or {}; a=_value(song,"primary_artists","singers","artist","artists") or _value(m,"music","primary_artists","singers","artist") or ""
    if isinstance(a,list): a=" ".join(str(x.get("name",x)) if isinstance(x,dict) else str(x) for x in a)
    mapped=(m.get("artistMap") or {}).get("primary_artists") or []
    return (str(a) + " " + " ".join(str(x.get("name", "")) for x in mapped if isinstance(x, dict))).lower()

def _is_junk(song:dict,query:str)->bool:
    terms=("karaoke","instrumental","tribute","cover","in the style of","originally performed","recreated version","re-recorded")
    text=(str(_value(song,"title","song","name") or "")+" "+_artist_text(song)).lower()
    return not any(t in query.lower() for t in terms) and any(t in text for t in terms)

def _score(song:dict,query:str)->float:
    title=str(_value(song,"title","song","name") or "").lower(); artist=_artist_text(song); tokens=re.findall(r"[a-z0-9]+",query.lower()); score=-1000 if _is_junk(song,query) else 0
    score+=sum(18 for t in tokens if t in title or t in artist)
    if tokens and all(t in title for t in tokens if t not in {"the","a","and"}): score+=45
    if "michael jackson" in query.lower() and "michael jackson" in artist: score+=100
    try: score+=min(float(_value(song,"play_count") or 0),100000000)/1000000
    except (TypeError,ValueError): pass
    return score

def _jiosaavn_search(query:str)->Optional[dict]:
    variants=[query]; words=query.split()
    if len(words)>1: variants.append(" ".join(words[:-2]) if len(words)>2 else words[0])
    endpoints=[]
    for q in variants: endpoints += [("song-results","search.getSongResults",q),("results","search.getResults",q)]
    headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Accept":"application/json,text/plain,*/*","Referer":"https://www.jiosaavn.com/"}; best=None
    for name,call,q in endpoints:
        params={"__call":call,"_format":"json","n":20,"p":1,"q":q,"_marker":0,"api_version":4,"ctx":"web6dot0"}; url="https://www.jiosaavn.com/api.php?"+urlencode(params)
        try:
            LOGGER.info("JioSaavn request provider=%s query=%s url=%s",name,q,url)
            with urlopen(Request(url,headers=headers),timeout=12) as r: status=getattr(r,"status",r.getcode()); payload=json.loads(r.read().decode("utf-8","replace"))
            songs=payload.get("results") or payload.get("data") or []
            if isinstance(songs,dict): songs=songs.get("results") or songs.get("songs") or []
            playable=[s for s in songs if _direct_url(s)]; LOGGER.info("JioSaavn parsed provider=%s query=%s status=%s results=%s playable=%s",name,q,status,len(songs),len(playable))
            if playable:
                ranked=sorted(playable,key=lambda s:_score(s,q),reverse=True); candidate=ranked[0]; LOGGER.info("JioSaavn candidate query=%s title=%s score=%.2f",q,_value(candidate,"title","song","name"),_score(candidate,q))
                if best is None or _score(candidate,q)>_score(best[1],best[0]): best=(q,candidate)
                if _score(candidate,q)>=0 and not _is_junk(candidate,q): break
        except Exception as exc: LOGGER.warning("JioSaavn request failed provider=%s query=%s error=%r",name,q,exc)
    if best:
        q,s=best; LOGGER.info("JioSaavn selected title=%s query=%s score=%.2f",_value(s,"title","song","name"),q,_score(s,q)); return {"title":_value(s,"song","title","name") or query,"url":_direct_url(s),"webpage":_value(s,"url","perma_url","permaUrl")}
    return None

def _extract(source:str,label:str,reject_short:bool=False)->Optional[dict]:
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as y: info=y.extract_info(source,download=True)
    except Exception as exc: LOGGER.info("%s resolution failed: %s",label,exc); return None
    if not info:return None
    if info.get("entries"): info=next((e for e in info["entries"] if e),None)
    if not info:return None
    path,duration=_downloaded_path(info),info.get("duration") or 0
    if reject_short and duration and duration<MIN_DURATION:
        if path:
            try: os.remove(path)
            except OSError: pass
        LOGGER.info("Ignoring %ss %s preview",duration,label); return None
    return {"title":info.get("title") or label,"url":path,"webpage":info.get("webpage_url")} if path else None

def resolve_track(query:str)->dict:
    query=(query or "").strip()
    if not query: raise ResolveError("Empty music query")
    if query.startswith(("http://","https://")): result=_extract(query,"direct URL")
    else:
        result=_jiosaavn_search(query)
        if result:return result
        result=_extract("scsearch1:"+query,"SoundCloud",True)
        if result is None: result=_extract("ytsearch1:"+query,"YouTube",True)
    if result is None: raise ResolveError("No full-length playable result found for {!r}".format(query))
    return result
