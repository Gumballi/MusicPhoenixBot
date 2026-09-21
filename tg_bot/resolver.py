"""Resolve music queries through JioSaavn, SoundCloud, then YouTube."""
import glob,json,logging,os,re,urllib.parse,urllib.request
from difflib import SequenceMatcher
from typing import Optional
import yt_dlp
LOGGER=logging.getLogger(__name__); MIN_DURATION=45
_YDL_COMMON={"format":"bestaudio/best","noplaylist":True,"quiet":True,"noprogress":True,"no_warnings":True,"nocheckcertificate":True,"socket_timeout":15,"retries":2,"outtmpl":"/tmp/mp_%(id)s.%(ext)s"}
_YOUTUBE_SPOOF={"extractor_args":{"youtube":{"player_client":["android_vr","tv_embedded","android_creator","mweb","android","ios"]}}}
GATED_HOSTS=("youtube.com","youtu.be","googlevideo.com","ggpht.com","ytimg.com")
BAD_TERMS=("unreleased","demo","snippet","leak","acapella","instrumental","karaoke","cover","remix","live","tribute","pitch","slowed","sped up","speed up","reverb","8d audio","mashup","bootleg","edit","type beat","prod","prod by","produced by","freestyle","flip","sample")
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

def _is_gated_host(h:str)->bool:
 h=(h or "").lower(); return any(h==x or h.endswith("."+x) for x in GATED_HOSTS)

def _clean(q:str)->str:
 q=re.sub(r"\[[^]]*\]|\([^)]*\)"," ",q.lower()); return re.sub(r"\s+"," ",re.sub(r"[^\w\s]"," ",q,flags=re.UNICODE)).strip()

def _query_variants(q:str):
 c=_clean(q); w=c.split(); out=[c]
 if len(w)>2: out += [" ".join(w[-3:])," ".join(w[-2:])]
 for a,b in (("tlahum","tilahun"),("tlahun","tilahun"),("gesese","gessesse"),("gessese","gessesse")):
  if a in c: out.insert(1,c.replace(a,b))
 return list(dict.fromkeys(x for x in out if x))

def _artist(info:dict)->str:
 return str(info.get("uploader") or info.get("uploader_id") or info.get("artist") or info.get("creator") or info.get("channel") or "").lower()

def _requested_artist(q:str)->str:
 w=_clean(q).split()
 # Search convention is title first, artist last; only gate clearly multi-token artist queries.
 return " ".join(w[-2:]) if len(w)>=4 else ""

def _candidate_artist(info:dict)->str:
 a=_artist(info); title=str(info.get("title") or "").lower()
 tail=re.search(r"\s[-–—]\s*([^|]+)$",title)
 if tail: a=(tail.group(1).strip()+" "+a).strip()
 return a

def _artist_mismatch(info:dict,q:str)->bool:
 wanted=_requested_artist(q)
 if not wanted:return False
 wa=set(wanted.split()); title=str(info.get("title") or "").lower(); ca=_candidate_artist(info)
 # Explicit trailing " - Other Artist" identifies the actual artist and is a hard reject.
 tail=re.search(r"\s[-–—]\s*([^|]+)$",title)
 if tail and not wa.issubset(set(_clean(tail.group(1)).split())): return True
 # A clear conflicting uploader is rejected unless the title is an explicit curator upload
 # such as "Michael Jackson - Thriller" and has no producer/type-beat indicators.
 known=ca and not any(x in ca for x in ("soundcloud","official","vault","archive","records","music"))
 if known and not wa.issubset(set(_clean(ca).split())):
  direct=bool(re.search(r"\b"+re.escape(wanted)+r"\b",title)) and not any(x in title for x in BAD_TERMS)
  return not direct
 return False

def _score(info:dict,q:str)->float:
 title=str(info.get("title") or "").lower(); target=_clean(q).split(); wanted=_requested_artist(q)
 # Compare title primarily with the leading song-title portion, never with artist tokens.
 target_title=" ".join(target[:-2]) if wanted else " ".join(target)
 main=re.split(r"\s[-–—]\s",title,1)[0]; ratio=SequenceMatcher(None," ".join(sorted(target_title.split()))," ".join(sorted(re.findall(r"[a-z0-9]+",main)))).ratio() if target_title and main else 0
 score=ratio*100
 if target_title and set(target_title.split()).issubset(set(re.findall(r"[a-z0-9]+",main))): score+=35
 if any(x in title for x in BAD_TERMS): score-=90
 for token in re.findall(r"[a-z0-9]+",_clean(q)):
  if token in _artist(info): score+=10
 if _artist_mismatch(info,q): score-=1000
 if ratio<0.35: score-=80
 return score

def _search_jiosaavn(q:str)->Optional[dict]:
 u="https://www.jiosaavn.com/api.php?"+urllib.parse.urlencode({"__call":"search.getResults","_format":"json","_marker":0,"query":q,"n":5})
 try:
  with urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=12) as r:p=json.loads(r.read().decode("utf-8","replace"))
 except Exception as e:LOGGER.warning("JioSaavn search failed: %s",e);return None
 es=p if isinstance(p,list) else (p.get("results") or []) if isinstance(p,dict) else []
 for x in es:
  if not isinstance(x,dict):continue
  m=x.get("media_url") or x.get("download_url") or ""
  if not isinstance(m,str) or not m.startswith("https://"):continue
  try:d=int(x.get("duration") or 0)
  except (TypeError,ValueError):d=0
  if d and d<MIN_DURATION:continue
  return {"title":x.get("title") or q,"url":m,"webpage":x.get("perma_url")}
 return None

def _search_ytdlp(extractor:str,q:str,count:int=5)->dict:
 opts=dict(_YDL_COMMON)
 if extractor.startswith("ytsearch"):opts.update(_YOUTUBE_SPOOF)
 with yt_dlp.YoutubeDL(opts) as y:
  try:listing=y.extract_info("{}:{}".format(extractor,q),download=False)
  except Exception as e:raise ResolveError("yt-dlp %s search failed for %r: %s"%(extractor,q,e)) from e
  es=[x for x in (listing.get("entries") or []) if x][:count] if listing else []
  if not es and listing:es=[listing]
  scored=sorted(((_score(x,q),i,x) for i,x in enumerate(es)),key=lambda z:z[0],reverse=True); errors=[]
  for rank,(score,_,x) in enumerate(scored,1):
   title=x.get("title") or q; artist=x.get("uploader") or x.get("channel") or ""; dur=x.get("duration") or 0
   LOGGER.info("%s candidate=%d title=%s artist=%s duration=%s score=%.2f artist_mismatch=%s",extractor,rank,title,artist,dur,score,_artist_mismatch(x,q))
   if score<=-500:errors.append("%s artist mismatch"%title);continue
   if dur and dur<MIN_DURATION:errors.append("%s is only %ss"%(title,dur));continue
   src=x.get("webpage_url") or x.get("original_url") or x.get("url")
   if not src:continue
   try:
    info=y.extract_info(src,download=True)
    if info and info.get("entries"):info=next((z for z in info["entries"] if z),None)
    path=_downloaded_path(info or {})
    if not path:raise ResolveError("download did not materialize")
    actual=(info.get("duration") or dur) if info else dur
    if actual and actual<MIN_DURATION:os.remove(path);raise ResolveError("only %ss preview"%actual)
    return {"title":info.get("title",title) if info else title,"url":path,"webpage":info.get("webpage_url",src) if info else src}
   except Exception as e:errors.append("%s: %s"%(title,e));LOGGER.warning("%s candidate=%d failed; trying next: %s",extractor,rank,e)
  raise ResolveError("no playable %s candidate for %r (%s)"%(extractor,q,"; ".join(errors)))

def _search_soundcloud(q:str)->dict:
 errors=[]
 for v in _query_variants(q):
  try:return _search_ytdlp("scsearch5",v,5)
  except ResolveError as e:errors.append(str(e));LOGGER.warning("SoundCloud variant failed query=%r: %s",v,e)
 raise ResolveError("SoundCloud exhausted query variants: %s"%" | ".join(errors))

def resolve_track(q:str)->dict:
 q=(q or "").strip()
 if not q:raise ResolveError("Empty music query")
 if q.startswith(("http://","https://")):
  if _is_gated_host(urllib.parse.urlparse(q).hostname or ""):raise ResolveError("YouTube links are blocked from this host")
  return {"title":q,"url":q,"webpage":q}
 for v in _query_variants(q):
  x=_search_jiosaavn(v)
  if x:return x
 errors=[]
 try:return _search_soundcloud(q)
 except ResolveError as e:errors.append(str(e))
 for v in _query_variants(q):
  try:return _search_ytdlp("ytsearch5",v,5)
  except ResolveError as e:errors.append(str(e))
 raise ResolveError("No free stream resolved for %r -- %s"%(q," | ".join(errors)))
