#!/usr/bin/env python3
import argparse, atexit, json, os, re, shutil, struct, sys, tempfile, urllib.request
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

if sys.platform == 'win32':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    DEFAULT_GAME = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Steam' / 'steamapps' / 'common' / 'CLANNAD'
else:
    DEFAULT_GAME = Path.home() / '.local/share/Steam/steamapps/common/CLANNAD'

# -------------------- crypto / lzss / dbs / gameexe --------------------
CLANNAD_KEY = bytes([0x3B,0x54,0xDC,0x74,0x2F,0xBA,0x0C,0xD6,0xAC,0x08,0xD2,0x23,0xEC,0x60,0xA9,0x2E])
DBS_KEY = bytes([0x2D,0x62,0xF4,0x89]*4)

def _crypt1_xor(buf, key):
    b=bytearray(buf); klen=len(key)
    for i in range(len(b)): b[i]^=key[i%klen]
    return bytes(b)

def _c2(buf, table):
    b=bytearray(buf)
    for i in range(len(b)): b[i]^=table[i%256]
    return bytes(b)

DECRYPT3_K0=(14,199,144,113)
DECRYPT3_K1=(53,241,155,73)
DECRYPT3_R=(0,1,1,0,0,0,1,1,0,0,0,1,1,0,0,0,1,1,0,0,1,1,1,0,0,1,1,1,0,0,1,1,0,0,0,1,0,0,0,0,1,0,0,0,0,1,0,0,1,1,0,1,1,1,1,0,1,1,1,1,0,1,1,1,1,1,1,0,0,1,1,1,0,0,1,1,1,0,0,1)

def _crypt3(buf):
    b=bytearray(buf); kindex=0; rindex=0
    for i in range(len(b)):
        key=DECRYPT3_K1 if DECRYPT3_R[rindex] else DECRYPT3_K0
        b[i]^=key[kindex]; kindex+=1
        if kindex>3:
            kindex=0; rindex+=1
            if rindex>=len(DECRYPT3_R): rindex=0
    return bytes(b)

def lzss_decompress(src, tolerant=True):
    arc_size, org_size = struct.unpack('II', src[:8])
    out=bytearray(); pos=8; end=min(arc_size,len(src))
    try:
        while len(out)<org_size and pos<end:
            flags=src[pos]; pos+=1
            for _ in range(8):
                if len(out)>=org_size or pos>=end: raise StopIteration
                if flags&1:
                    out.append(src[pos]); pos+=1
                else:
                    if pos+2>end: raise StopIteration
                    token=src[pos]|(src[pos+1]<<8); pos+=2
                    off=token>>4; ln=(token&0xF)+2
                    if off==0 or off>len(out): raise StopIteration
                    for _ in range(ln):
                        if len(out)>=org_size: break
                        out.append(out[len(out)-off])
                flags>>=1
    except StopIteration:
        pass
    if len(out)<org_size: out+=b'\x00'*(org_size-len(out))
    return bytes(out[:org_size])

def lzss_compress_literals(data):
    org=len(data); out=bytearray(); i=0; n=len(data)
    while i<n:
        flag_pos=len(out); out.append(0); flags=0
        for bit in range(8):
            if i>=n: break
            flags |= (1<<bit); out.append(data[i]); i+=1
        out[flag_pos]=flags
    return struct.pack('II', len(out)+8, org)+bytes(out)

def lzss32_decompress(src):
    arc_size, org_size = struct.unpack('II', src[:8])
    out=bytearray(); pos=8; end=min(arc_size,len(src))
    while len(out)<org_size and pos<end:
        flags=src[pos]; pos+=1
        for _ in range(8):
            if len(out)>=org_size or pos>=end: break
            if flags&1:
                out+=src[pos:pos+3]; out.append(255); pos+=3
            else:
                if pos+2>end: break
                token=src[pos]|(src[pos+1]<<8); pos+=2
                off=(token>>4)<<2; length=((token&0xF)+1)<<2
                for _ in range(length):
                    if len(out)>=org_size: break
                    out.append(out[len(out)-off])
            flags>>=1
    if len(out)!=org_size: out=out[:org_size]+b'\x00'*(max(0,org_size-len(out)))
    return bytes(out)

def lzss_compress(data, literal_size=1):
    """Greedy LZSS encoder compatible with Siglus (byte LZSS or 24bpp LZSS32)."""
    bgra_indexing = literal_size == 3
    unit = literal_size
    if not bgra_indexing:
        org_size=len(data); max_units=17; min_units=2; max_off=0xFFF
    else:
        org_size=(len(data)//3)*4; max_units=16; min_units=1; max_off=0xFFF*3
    key_bytes=3 if bgra_indexing else 2
    buckets=defaultdict(list); out=bytearray(); flag_pos=None; bit=8
    n=len(data); dw_total=n//3; good_enough=max_units-4; MAX_CHAIN_SCAN=64
    def record(pos):
        if pos>=unit and pos+key_bytes<=n: buckets[data[pos:pos+key_bytes]].append(pos)
    def new_block():
        nonlocal flag_pos,bit; flag_pos=len(out); out.append(0); bit=0
    if data: new_block()
    ptr=0
    while ptr<n:
        if bit==8: new_block()
        best_off=0; best_len=0
        if ptr>=(3 if bgra_indexing else 1) and ptr+key_bytes<=n:
            key=data[ptr:ptr+key_bytes]; chain=buckets.get(key)
            if chain:
                i=bisect_left(chain, ptr-max_off); j=len(chain)
                if j-i>MAX_CHAIN_SCAN: i=j-MAX_CHAIN_SCAN
                if bgra_indexing:
                    remaining_dw=dw_total-ptr//3
                    stop_u=max_units if remaining_dw>max_units else remaining_dw-1
                    if remaining_dw<=1: stop_u=0
                else:
                    stop_u=max_units
                    if ptr+stop_u>n: stop_u=n-ptr
                if stop_u>=min_units:
                    stop_b=stop_u*unit; p2=ptr+stop_b
                    for k in range(j-1,i-1,-1):
                        cand_pos=chain[k]; off=ptr-cand_pos
                        if bgra_indexing and (off%3 or off<3): continue
                        if data[cand_pos:cand_pos+stop_b]==data[ptr:p2]: m=stop_b
                        else:
                            lo_,hi_=0,stop_b
                            while lo_<hi_:
                                mid=(lo_+hi_+1)>>1
                                if data[cand_pos:cand_pos+mid]==data[ptr:ptr+mid]: lo_=mid
                                else: hi_=mid-1
                            m=lo_
                        n_units=m//unit
                        if n_units>=min_units and n_units>best_len:
                            best_len=n_units; best_off=off if not bgra_indexing else off//3
                            if best_len>=max_units or best_len>=good_enough: break
        use_copy=best_len>=min_units
        if use_copy and (ptr//unit)+best_len>=org_size//unit: use_copy=False
        if use_copy:
            if not bgra_indexing:
                v=(best_off<<4)|(best_len-2); out.append(v&0xFF); out.append(v>>8)
                for i in range(best_len): record(ptr+i)
                ptr+=best_len
            else:
                v=((best_off&0xFFF)<<4)|(best_len-1); out.append(v&0xFF); out.append(v>>8)
                for i in range(best_len): record(ptr+i*3)
                ptr+=best_len*3
        else:
            out[flag_pos]|=1<<bit; out+=data[ptr:ptr+unit]; record(ptr); ptr+=unit
        bit+=1
    return struct.pack('II', len(out)+8, org_size)+bytes(out)

DBS_HDR='7I'
class Dbs:
    def __init__(self, blob, is_utf):
        self.is_utf=is_utf
        vals=struct.unpack(DBS_HDR, blob[:28])
        (self.file_size,self.line_count,self.data_count,self.line_index_offset,
         self.data_format_offset,self.line_data_index_offset,self.text_offset)=vals
        off=self.line_index_offset
        self.line_index=list(struct.unpack('%di'%self.line_count, blob[off:off+self.line_count*4]))
        off=self.data_format_offset
        self.data_index=[]; self.data_type=[]
        for _ in range(self.data_count):
            idx,ty=struct.unpack('II',blob[off:off+8]); off+=8
            self.data_index.append(idx); self.data_type.append(ty)
        off=self.line_data_index_offset
        raw=struct.unpack('%dI'%(self.line_count*self.data_count), blob[off:off+self.line_count*self.data_count*4])
        self.rows=[]
        for m in range(self.line_count):
            row=[]
            for n in range(self.data_count):
                v=raw[m*self.data_count+n]
                if self.data_type[n]==0x53:
                    p=self.text_offset+v
                    if is_utf:
                        end=blob.index(b'\x00\x00',p)
                        if (end-p)%2: end+=1
                        row.append(blob[p:end].decode('utf-16-le','replace'))
                    else:
                        end=blob.index(b'\x00',p)
                        row.append(blob[p:end].decode('cp932','replace'))
                else:
                    row.append(v)
            self.rows.append(row)
    def to_blob(self):
        strings=[]; offsets={}
        for m in range(self.line_count):
            for n in range(self.data_count):
                if self.data_type[n]==0x53:
                    strings.append(((m,n), self.rows[m][n]))
        text_off=28 + self.line_count*4 + self.data_count*8 + self.line_count*self.data_count*4
        blob=bytearray(text_off)
        struct.pack_into(DBS_HDR, blob, 0, 0, self.line_count,self.data_count,28,
                         28+self.line_count*4, 28+self.line_count*4+self.data_count*8, text_off)
        for i,li in enumerate(self.line_index): struct.pack_into('i',blob,28+i*4,li)
        for n,(di,ty) in enumerate(zip(self.data_index,self.data_type)):
            struct.pack_into('II',blob,28+self.line_count*4+n*8,di,ty)
        cursor=0
        for key,s in strings:
            offsets[key]=cursor
            raw=s.encode('utf-16-le' if self.is_utf else 'cp932','replace')
            term=b'\x00\x00' if self.is_utf else b'\x00'
            blob+=raw+term; cursor+=len(raw)+len(term)
        base=28+self.line_count*4+self.data_count*8
        for m in range(self.line_count):
            for n in range(self.data_count):
                v=offsets[(m,n)] if self.data_type[n]==0x53 else self.rows[m][n]
                struct.pack_into('I',blob,base+(m*self.data_count+n)*4,v)
        struct.pack_into('I',blob,0,text_off+cursor)
        pad=(-len(blob))%32 or 32
        blob+=b'\x00'*pad
        return bytes(blob)

def decrypt_dbs(path):
    raw=Path(path).read_bytes(); is_utf = raw[:4]!=b'\x00\x00\x00\x00'
    body=_crypt1_xor(raw[4:], DBS_KEY)
    comp,decomp=struct.unpack('II',body[:8])
    data=lzss_decompress(body[:8]+body[8:comp], tolerant=True)
    return is_utf,_crypt3(data)

def encrypt_dbs(blob, is_utf=True):
    data=lzss_compress_literals(_crypt3(blob))
    full=_crypt1_xor(data, DBS_KEY)
    return (b'\x01\x00\x00\x00' if is_utf else b'\x00\x00\x00\x00')+full

DECRYPT2_T1=bytes([216,41,185,22,61,26,118,208,135,155,45,12,123,209,169,25,34,159,145,115,106,53,177,126,209,181,231,230,213,245,6,214,186,191,243,69,63,241,97,221,76,103,106,111,116,236,122,111,38,116,14,219,39,76,165,241,14,45,112,196,64,93,79,218,158,197,73,123,189,232,223,238,202,244,146,222,228,118,16,221,42,82,220,115,78,84,140,48,61,154,178,155,184,147,41,85,250,122,201,218,16,151,229,182,35,2,221,56,76,155,31,154,213,73,233,52,15,40,45,27,82,57,92,54,137,86,167,150,20,190,46,197,62,8,95,71,169,223,136,159,212,204,105,31,48,159,231,205,128,69,243,231,42,29,22,178,241,84,200,108,43,13,212,101,247,227,54,212,165,59,209,121,76,84,240,42,180,178,86,69,46,171,123,136,197,250,116,173,3,184,158,213,245,111,220,250,68,73,49,246,131,50,255,194,177,233,225,152,61,111,49,13,172,177,8,131,157,13,16,209,65,249,0,186,26,207,19,113,228,134,33,47,35,101,195,69,160,195,146,72,157,234,221,49,44,233,226,16,34,170,225,173,44,196,45,127])

def gameexe_unpack(path):
    raw=Path(path).read_bytes()
    data=_c2(raw[8:], DECRYPT2_T1)
    data=_crypt1_xor(data, CLANNAD_KEY)
    comp,decomp=struct.unpack('II', data[:8])
    return lzss_decompress(data[:8]+data[8:comp], tolerant=True)

def gameexe_pack(raw):
    body=lzss_compress_literals(raw)
    data=_crypt1_xor(body, CLANNAD_KEY)
    data=_c2(data, DECRYPT2_T1)
    return b'\x00\x00\x00\x00\x01\x00\x00\x00' + data

# -------------------- conversion logic --------------------
KANA=re.compile(r'[\u3040-\u30ff]'); HAN=re.compile(r'[\u4e00-\u9fff]')
SC_ONLY=re.compile('[设显键选项载读档录话语声乐样关闭击页辑结继续应战图标题队这个们说时对开]')
QUOTED=re.compile(r'"([^"]*)"')
OVERRIDES={('text03.dbs',11513):'',('text03.dbs',11514):''}

def classify(s):
    if SC_ONLY.search(s): return 'ZH!'
    if HAN.search(s) and not KANA.search(s) and len(s)>1: return 'ZH?'
    return 'ok'

def atomic_write(dst,data):
    dst=Path(dst); dst.parent.mkdir(parents=True,exist_ok=True)
    if dst.exists() and dst.read_bytes()==data: return False
    tmp=dst.with_name(dst.name+'.tmp'); tmp.write_bytes(data); tmp.replace(dst); return True

def convert_gameexe_text(text):
    lines=text.splitlines(); keys={}
    for no,ln in enumerate(lines):
        s=ln.strip()
        if s.startswith('#') and '=' in s: keys.setdefault(s.split('=',1)[0].strip(),[]).append(no)
    for key,nos in keys.items():
        if len(nos)<2: continue
        first=QUOTED.findall(lines[nos[0]])
        for no in nos[1:]:
            strs=QUOTED.findall(lines[no])
            if not strs or all(classify(s)=='ok' for s in strs): continue
            if len(strs)!=len(first): continue
            out,pos,qi=[],0,0
            for m in QUOTED.finditer(lines[no]):
                out.append(lines[no][pos:m.start()]); out.append('"%s"'%first[qi]); qi+=1; pos=m.end()
            out.append(lines[no][pos:]); lines[no]=''.join(out)
    repl=[('NotoSansMonoCJKsc-Regular.otf','NotoSansMonoCJKjp-Regular.otf'),
          ('Noto Sans Mono CJK SC Regular','Noto Sans Mono CJK JP'),
          ('Noto Sans Mono CJK SC','Noto Sans Mono CJK JP'),
          ('Noto Sans Mono CJK JP Regular','Noto Sans Mono CJK JP'),
          ('畫面暗転','画面暗転'),('聲の再生中','声の再生中')]
    for o,r in repl: lines=[ln.replace(o,r) for ln in lines]
    new='\n'.join(lines)+('\n' if text.endswith('\n') else '')
    return new

def convert_dbs(path):
    name=Path(path).name
    lang_target = (name.startswith('text') and name.endswith('.dbs')) or name in ('config_table.dbs','cgmodetbl.dbs','config_parts.dbs','name_replace.dbs')
    if not lang_target: return None,0,0
    is_utf,blob=decrypt_dbs(path); d=Dbs(blob,is_utf)
    changed=warn=0
    if name=='name_replace.dbs':
        # patch準拠: 列2(置換先プレースホルダ)を列0(表示名)に合わせて日本語化
        for r,row in enumerate(d.rows):
            if len(row)>2 and isinstance(row[0],str) and isinstance(row[2],str) and row[2]!=row[0]:
                row[2]=row[0]; changed+=1
        if changed==0: return None,0,0
        out=encrypt_dbs(d.to_blob(), is_utf)
        return out,changed,0
    if name=='config_parts.dbs':
        # フルスクリーンのトグルスイッチ(行6)を少し左に移動 (x: 312 -> 287)
        if len(d.rows)>6 and d.rows[6][0]=='_SYSTEM_RADIO_BUTTON' and d.rows[6][1]==312:
            d.rows[6][1]=287; changed+=1
        if changed==0: return None,0,0
        out=encrypt_dbs(d.to_blob(), is_utf)
        return out,changed,0
    JP,CN,STR=0,2,0x53
    if JP not in d.data_index or CN not in d.data_index: return None,0,0
    c0=d.data_index.index(JP); c2=d.data_index.index(CN)
    if d.data_type[c0]!=STR or d.data_type[c2]!=STR: return None,0,0
    for r,row in enumerate(d.rows):
        if row[c0]=='' and row[c2]!='':
            ov=OVERRIDES.get((name,r+1))
            if ov is None: warn+=1; continue
            row[c0]=ov
        if row[c2]!=row[c0]:
            row[c2]=row[c0]; changed+=1
    if changed==0: return None,0,warn
    out=encrypt_dbs(d.to_blob(), is_utf)
    fd,tmp=tempfile.mkstemp(suffix='.dbs'); os.close(fd)
    Path(tmp).write_bytes(out)
    u2,b2=decrypt_dbs(tmp); os.unlink(tmp)
    d2=Dbs(b2,u2); c02=d2.data_index.index(JP); c22=d2.data_index.index(CN)
    for rr,row in enumerate(d2.rows):
        if row[c02]=='' and row[c22]!='': continue
        assert row[c22]==row[c02], (name, rr+1)
    return out,changed,warn

# -------------------- network assets --------------------
PROXY_REPO='Madotsukanai/CLANNAD-Steam-JP'
PROXY_ASSET='steam_api.dll'
FONT_URL='https://github.com/notofonts/noto-cjk/raw/refs/heads/main/Sans/Mono/NotoSansMonoCJKjp-Regular.otf'
FONT_NAME='NotoSansMonoCJKjp-Regular.otf'

def _pick_unit(n):
    units=('B','KB','MB','GB')
    v=float(n); u=0
    while v>=1024 and u<len(units)-1:
        v/=1024; u+=1
    return units[u]

def _fmt_size(n, unit):
    units=('B','KB','MB','GB')
    v=float(n)/(1024**units.index(unit))
    if unit=='B':
        return f'{int(v)} {unit}'
    return f'{v:.1f} {unit}'

def _download_to_file(url, dest, headers=None, min_size=0):
    req=urllib.request.Request(url, headers=headers or {'User-Agent':'clannad-jp-installer'})
    with urllib.request.urlopen(req) as r:
        total=r.getheader('Content-Length')
        try: total=int(total) if total else None
        except Exception: total=None
        if total is not None and total <= 0:
            total=None
        data=bytearray(); read=0; chunk=1024*64
        u=_pick_unit(total) if total else 'B'
        while True:
            b=r.read(chunk)
            if not b: break
            data.extend(b); read+=len(b)
            if not total: u=_pick_unit(read or 1)
            if total:
                pct=min(100, read*100//total)
                msg=f'  {_fmt_size(read,u)}/{_fmt_size(total,u)} ({pct}%)'
            else:
                msg=f'  {_fmt_size(read,u)}'
            print(f'\r{msg:<60}', end='', flush=True)
        print()
    if len(data) < min_size:
        raise RuntimeError('downloaded file too small')
    atomic_write(dest, bytes(data))

def download_latest_proxy(dest):
    api=f'https://api.github.com/repos/{PROXY_REPO}/releases/latest'
    req=urllib.request.Request(api, headers={'User-Agent':'clannad-jp-installer','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req) as r:
        rel=json.load(r)
    url=None
    for a in rel.get('assets',[]):
        if a.get('name')==PROXY_ASSET:
            url=a.get('browser_download_url'); break
    if not url:
        raise RuntimeError(f'{PROXY_ASSET} not found in latest release of {PROXY_REPO}')
    tag=rel.get('tag_name','?')
    print(f'[proxy] latest {PROXY_REPO} {tag} -> {PROXY_ASSET}')
    _download_to_file(url, dest, headers={'User-Agent':'clannad-jp-installer'}, min_size=1024)

def ensure_proxy(game):
    orig=game/'steam_api_original.dll'; dst=game/'steam_api.dll'
    if not orig.exists():
        if not dst.exists():
            raise RuntimeError('steam_api.dll not found in game folder')
        shutil.copy2(dst, orig)
        print('[proxy] backup steam_api.dll -> steam_api_original.dll')
    local_proxy = Path(__file__).resolve().parent / 'steam_api.dll'
    if local_proxy.exists():
        shutil.copy2(local_proxy, dst)
        print(f'[proxy] installed local {local_proxy} -> {dst}')
        return
    with tempfile.TemporaryDirectory() as td:
        tmp=Path(td)/PROXY_ASSET
        download_latest_proxy(tmp)
        data=tmp.read_bytes()
    atomic_write(dst, data)
    print(f'[proxy] installed {dst}')

def download_font(dest):
    print(f'[font] downloading {FONT_NAME}')
    _download_to_file(FONT_URL, dest, headers={'User-Agent':'clannad-jp-installer'}, min_size=1024*1024)

_font_path_cache=None
def ensure_font(game):
    global _font_path_cache
    for d in [game/'dat_jp', game/'dat']:
        existing = d/FONT_NAME
        if existing.exists() and existing.stat().st_size > 1024*1024:
            fdir = Path(tempfile.mkdtemp(prefix='clannad_font_'))
            atexit.register(shutil.rmtree, fdir, True)
            fpath = fdir/FONT_NAME
            shutil.copy2(existing, fpath)
            _font_path_cache = fpath
            print(f'[font] using existing {existing}')
            return fpath
    with tempfile.TemporaryDirectory() as td:
        tmp=Path(td)/FONT_NAME
        download_font(tmp)
        data=tmp.read_bytes()
    for d in [game/'dat_jp', game/'dat']:
        atomic_write(d/FONT_NAME, data)
    # UI描画用のフォントは一時ディレクトリに置き、終了時に削除する（カレントディレクトリには残さない）
    fdir=Path(tempfile.mkdtemp(prefix='clannad_font_'))
    atexit.register(shutil.rmtree, fdir, True)
    fpath=fdir/FONT_NAME
    atomic_write(fpath, data)
    _font_path_cache=fpath
    print(f'[font] installed {FONT_NAME}')
    return fpath

# -------------------- g00 UI rendering (integrated from make_jp_ui.py) --------------------

UI_SPEC = {
"files": [
{"name":"_system_config_ZH","items":[{"box":[10,8,130,32],"text":"環境設定","size":16,"align":"center"}]},
{"name":"_system_config_base_ZH","items":[
 {"box":[140,38,246,70],"text":"環境設定","size":24,"align":"center"},
 {"box":[142,123,250,146],"text":"画面モード","size":20,"maxw":130},
 {"box":[142,247,250,268],"text":"画面切り替え","size":20,"maxw":130},
 {"box":[142,370,250,391],"text":"オートモード","size":20,"maxw":130},
 {"box":[142,495,250,516],"text":"文字速度","size":20,"maxw":130},
 {"box":[667,123,770,146],"text":"その他の設定","size":20,"maxw":140},
 {"box":[667,553,770,577],"text":"背景色","size":20,"maxw":140},
 {"box":[168,168,236,198],"text":"ウィンドウ","size":20,"maxw":150},
 {"box":[328,168,450,198],"text":"フルスクリーン","size":20,"maxw":250,"dx":-25},
 {"box":[168,296,236,326],"text":"遅い","size":20},
 {"box":[328,296,386,326],"text":"速い","size":20},
 {"box":[488,296,596,326],"text":"ウェイトなし","size":20,"maxw":115},
 {"box":[168,419,236,449],"text":"遅い","size":20},
 {"box":[328,419,386,449],"text":"速い","size":20},
 {"box":[488,419,596,449],"text":"ウェイトなし","size":20,"maxw":115},
 {"box":[150,520,184,543],"text":"遅","size":14},
 {"box":[566,520,598,543],"text":"速","size":14,"align":"right"},
 {"box":[670,582,708,606],"text":"減","size":14},
 {"box":[1088,582,1126,606],"text":"増","size":14,"align":"right"},
 {"box":[704,168,1110,199],"text":"日付を表示する","size":20},
 {"box":[704,219,1110,249],"text":"未読の文章も早送りできるようにする","size":20},
 {"box":[704,271,1110,301],"text":"ホイールの下回しで文章を読み進める","size":20},
 {"box":[704,324,1110,354],"text":"他のプログラムの動作を優先する","size":20},
 {"box":[704,376,1110,406],"text":"音に雑音が入る場合はチェックする","size":20},
 {"box":[704,429,1110,459],"text":"クリックで画面暗転をスキップする","size":20}
]},
{"name":"_system_all_reset_ZH","items":[{"box":[5,8,135,32],"text":"初期設定に戻す","size":16,"align":"center","maxw":120,"bgfile":"_system_return_ZH","bgx":[124,130]}]},
{"name":"_system_button_cgmode_ZH","items":[{"box":[10,8,130,32],"text":"CG鑑賞","size":16,"align":"center"}]},
{"name":"_system_button_musicmode_ZH","items":[{"box":[10,8,130,32],"text":"音楽鑑賞","size":16,"align":"center"}]},
{"name":"_system_button_no_ZH","items":[{"box":[10,8,130,32],"text":"いいえ","size":16,"align":"center"}]},
{"name":"_system_button_yes_ZH","items":[{"box":[10,8,130,32],"text":"はい","size":16,"align":"center"}]},
{"name":"_system_display_ZH","items":[{"box":[10,8,130,32],"text":"画面設定","size":16,"align":"center"}]},
{"name":"_system_font_ZH","items":[{"box":[10,8,130,32],"text":"フォント設定","size":16,"align":"center"}]},
{"name":"_system_font_reset_ZH","items":[{"box":[10,8,130,32],"text":"フォント初期化","size":16,"align":"center"}]},
{"name":"_system_load_btn_ZH","items":[{"box":[10,8,130,32],"text":"ロード","size":16,"align":"center"}]},
{"name":"_system_movie_ZH","items":[{"box":[5,8,135,32],"text":"ムービー再生設定","size":16,"align":"center","maxw":120,"bgfile":"_system_return_ZH","bgx":[124,130]}]},
{"name":"_system_return_ZH","items":[{"box":[10,8,130,32],"text":"戻る","size":16,"align":"center"}]},
{"name":"_system_save_btn_ZH","items":[{"box":[10,8,130,32],"text":"セーブ","size":16,"align":"center"}]},
{"name":"_system_sound_ZH","items":[{"box":[10,8,130,32],"text":"音量設定","size":16,"align":"center"}]},
{"name":"_system_sound__ZH","items":[{"box":[10,8,130,32],"text":"音量設定","size":16,"align":"center"}]},
{"name":"_system_voice_ZH","items":[{"box":[10,8,130,32],"text":"キャラボイス設定","size":16,"align":"center"}]},
{"name":"_system_rmenu_auto_ZH","items":[{"box":[20,8,280,42],"text":"オートモード","size":20,"align":"center"}]},
{"name":"_system_rmenu_cancel_ZH","items":[{"box":[20,8,280,42],"text":"キャンセル","size":20,"align":"center"}]},
{"name":"_system_rmenu_config_ZH","items":[{"box":[20,8,280,42],"text":"環境設定","size":20,"align":"center"}]},
{"name":"_system_rmenu_load_ZH","items":[{"box":[20,8,280,42],"text":"ロード","size":20,"align":"center"}]},
{"name":"_system_rmenu_return_ZH","items":[{"box":[20,8,280,42],"text":"タイトルに戻る","size":20,"align":"center"}]},
{"name":"_system_rmenu_save_ZH","items":[{"box":[20,8,280,42],"text":"セーブ","size":20,"align":"center"}]},
{"name":"_system_rmenu_select_ZH","items":[{"box":[20,8,280,42],"text":"前の選択肢に戻る","size":20,"align":"center"}]},
{"name":"_system_rmenu_skip_ZH","items":[{"box":[20,8,280,42],"text":"既読文章を早送り","size":20,"align":"center"}]},
{"name":"_system_cgmode_base_ZH","items":[{"box":[56,28,172,64],"text":"CG鑑賞","size":22,"align":"center"},{"box":[218,44,342,76],"text":"CG達成率","size":22}]},
{"name":"_system_load_base_ZH","items":[{"box":[56,28,172,64],"text":"ロード","size":22,"align":"center"}]},
{"name":"_system_save_base_ZH","items":[{"box":[56,28,172,64],"text":"セーブ","size":22,"align":"center"}]},
{"name":"_system_musicmode_base_ZH","items":[{"box":[56,28,172,64],"text":"音楽鑑賞","size":22,"align":"center"}]},
{"name":"_system_sound_base_ZH","items":[
 {"box":[140,38,246,70],"text":"音量設定","size":24,"align":"center"},
 {"box":[142,123,250,146],"text":"音量","size":20,"maxw":130},
 {"box":[180,184,330,210],"text":"全体音量","size":18,"maxw":140},
 {"box":[180,403,264,429],"text":"ボイス","size":18,"maxw":140},
 {"box":[180,549,330,575],"text":"効果音","size":18,"maxw":140},
 {"box":[180,622,330,648],"text":"システム音","size":18,"maxw":140},
 {"box":[180,695,330,721],"text":"ムービー音声","size":18,"maxw":140},
 {"box":[410,330,600,356],"text":"BGMフェード","size":18,"maxw":170},
 {"box":[410,476,760,502],"text":"次の文章に進んでもボイスを止めない","size":18,"maxw":330},
 {"box":[1046,184,1110,210],"text":"消音","size":18},{"box":[1046,257,1110,283],"text":"消音","size":18},
 {"box":[1046,403,1110,429],"text":"消音","size":18},{"box":[1046,549,1110,575],"text":"消音","size":18},
 {"box":[1046,622,1110,648],"text":"消音","size":18},{"box":[1046,695,1110,721],"text":"消音","size":18}
]},
{"name":"_system_voice_base_ZH","items":[{"box":[140,38,246,70],"text":"ボイス設定","size":22,"align":"center"},{"box":[142,123,250,146],"text":"キャラボイス","size":18,"maxw":125}]},
{"name":"_system_dialog_base_ZH","items":[
 {"cuts":[0],"box":[20,62,503,102],"text":"タイトルに戻ってもよろしいですか？","size":26,"align":"center","maxw":470},
 {"cuts":[1],"box":[20,62,503,102],"text":"ロードしてもよろしいですか？","size":26,"align":"center","maxw":470},
 {"cuts":[2,4],"box":[20,62,503,102],"text":"セーブしてもよろしいですか？","size":26,"align":"center","maxw":470},
 {"cuts":[3],"box":[20,62,503,102],"text":"ゲームを終了してもよろしいですか？","size":26,"align":"center","maxw":470},
 {"cuts":[0,1,2,3,4],"box":[118,196,503,228],"text":"今後このメッセージを表示しない","size":18,"maxw":380},
 {"cuts":[6],"box":[20,29,503,59],"text":"【AFTER STORY】の開始時に","size":22,"align":"center","maxw":470},
 {"cuts":[6],"box":[20,66,503,96],"text":"渚ルートのゲーム進行状況を読み込みます。","size":22,"align":"center","maxw":470},
 {"cuts":[6],"box":[20,103,503,133],"text":"今回の進行状況をセーブしますか？","size":22,"align":"center","maxw":470},
 {"cuts":[7],"box":[20,63,503,99],"text":"ゲームの進行状況をセーブしました。","size":26,"align":"center","maxw":470},
 {"cuts":[8],"box":[20,63,503,99],"text":"ゲームは渚ルートの進行状況から始まります。","size":26,"align":"center","maxw":470}
]},
{"name":"_system_saveload_none_ZH","items":[
 {"cuts":[0],"box":[140,140,340,192],"text":"データなし","size":26,"align":"center","absdiff":True,"shadow":[2,2,[20,70,120]]},
 {"cuts":[1],"box":[140,140,340,192],"text":"データなし","size":26,"align":"center","absdiff":True,"shadow":[2,2,[90,30,30]]}
]},
{"name":"_system_vo_fuko_ZH","items":[{"style":"vo","box":[96,10,158,50],"text":"風子","size":19,"maxw":62,"ow":2,"cw":0,"bold":False,"bgfile":"_system_vo_nagisa_ZH"}]},
{"name":"_system_vo_kappei_ZH","items":[{"style":"vo","box":[96,10,158,50],"text":"勝平","size":19,"maxw":62,"ow":2,"cw":0,"bold":False,"bgfile":"_system_vo_nagisa_ZH"}]},
{"name":"_system_vo_yukine_ZH","items":[{"style":"vo","box":[96,10,158,50],"text":"有紀寧","size":19,"maxw":62,"ow":2,"cw":0,"bold":False,"bgfile":"_system_vo_nagisa_ZH"}]},
{"name":"_system_vo_sonota_ZH","items":[{"style":"vo","box":[96,10,158,50],"text":"その他","size":19,"maxw":62,"ow":2,"cw":0,"bold":False,"bgfile":"_system_vo_nagisa_ZH"}]},
{"name":"_system_vo_botan_ZH","items":[{"style":"vo","box":[96,10,158,50],"text":"ボタン","size":19,"maxw":62,"ow":2,"cw":0,"bold":False,"bgfile":"_system_vo_nagisa_ZH"}]},
{"name":"_system_vo_kotomi_ZH","items":[{"style":"vo","box":[96,10,158,50],"text":"ことみ","size":19,"maxw":62,"ow":2,"cw":0,"bold":False,"bgfile":"_system_vo_nagisa_ZH"}]},
{"name":"_system_vo_naouyuki_ZH","items":[{"style":"vo","box":[96,10,158,50],"text":"直幸","size":19,"maxw":62,"ow":2,"cw":0,"bold":False,"bgfile":"_system_vo_nagisa_ZH"}]},
{"name":"_system_smenu_04_ZH","items":[{"box":[20,8,450,42],"text":"町、時の流れ、人","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_06_ZH","items":[{"box":[20,8,450,42],"text":"渚〜坂の下の別れ","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_07_ZH","items":[{"box":[20,8,450,42],"text":"それは風のように","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_10_ZH","items":[{"box":[20,8,450,42],"text":"は～りぃすたーふぃっしゅ","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_11_ZH","items":[{"box":[20,8,450,42],"text":"彼女の本気","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_12_ZH","items":[{"box":[20,8,450,42],"text":"資料室のお茶会","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_13_ZH","items":[{"box":[20,8,450,42],"text":"東風","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_14_ZH","items":[{"box":[20,8,450,42],"text":"東風 -afternoon-","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_15_ZH","items":[{"box":[20,8,450,42],"text":"東風 -tempo up-","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_16_ZH","items":[{"box":[20,8,450,42],"text":"東風 -piano-","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_17_ZH","items":[{"box":[20,8,450,42],"text":"田舎小径","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_18_ZH","items":[{"box":[20,8,450,42],"text":"有意義な時間の過ごし方","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_19_ZH","items":[{"box":[20,8,450,42],"text":"有意義な時間の過ごし方 -guitar-","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_20_ZH","items":[{"box":[20,8,450,42],"text":"有意義な時間の過ごし方 -sax-","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_21_ZH","items":[{"box":[20,8,450,42],"text":"日々の遑","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_22_ZH","items":[{"box":[20,8,450,42],"text":"ダム","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_23_ZH","items":[{"box":[20,8,450,42],"text":"馬鹿ふたり","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_24_ZH","items":[{"box":[20,8,450,42],"text":"灰燼に帰す","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_28_ZH","items":[{"box":[20,8,450,42],"text":"月の位相","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_29_ZH","items":[{"box":[20,8,450,42],"text":"無間","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_30_ZH","items":[{"box":[20,8,450,42],"text":"潮鳴り","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_31_ZH","items":[{"box":[20,8,450,42],"text":"潮鳴りに","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_32_ZH","items":[{"box":[20,8,450,42],"text":"同じ高みへ","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_33_ZH","items":[{"box":[20,8,450,42],"text":"空に光る","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_34_ZH","items":[{"box":[20,8,450,42],"text":"願いが叶う場所","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_35_ZH","items":[{"box":[20,8,450,42],"text":"願いが叶う場所に","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_36_ZH","items":[{"box":[20,8,450,42],"text":"白詰草","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_37_ZH","items":[{"box":[20,8,450,42],"text":"遥かな年月","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_38_ZH","items":[{"box":[20,8,450,42],"text":"遥かな年月 -piano-","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_39_ZH","items":[{"box":[20,8,450,42],"text":"夏時間","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_40_ZH","items":[{"box":[20,8,450,42],"text":"カントリートレイン","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_42_ZH","items":[{"box":[20,8,450,42],"text":"メグメル","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_43_ZH","items":[{"box":[20,8,450,42],"text":"影二つ","size":20,"align":"center","maxw":430}]},
{"name":"_system_smenu_45_ZH","items":[{"box":[20,8,450,42],"text":"小さな手のひら","size":20,"align":"center","maxw":430}]}
]}

class G00Type2:
    def __init__(self, raw):
        self.raw=raw
        n=struct.unpack_from('I',raw,5)[0]
        self.off=9+n*24
        arc,_org=struct.unpack_from('II',raw,self.off)
        self.debuf=bytearray(lzss_decompress(raw[self.off:self.off+arc], tolerant=True))
        d=self.debuf; entries=struct.unpack_from('I',d,0)[0]; self.cuts={}
        for i in range(min(n,entries)):
            p_off,p_size=struct.unpack_from('II',d,4+i*8)
            if p_off==0 or p_size==0 or p_off>=len(d): continue
            dw,dh=struct.unpack_from('ii',d,p_off+0x0C)
            cnt=struct.unpack_from('H',d,p_off+2)[0]
            chips,o=[],p_off+116
            for _ in range(cnt):
                cx,cy=struct.unpack_from('HH',d,o); w,h=struct.unpack_from('HH',d,o+6)
                chips.append((o+92,cx,cy,w,h)); o+=92+w*h*4
            if chips and dw>0 and dh>0: self.cuts[i]=(dw,dh,chips)
    def get_image(self,i):
        from PIL import Image
        dw,dh,chips=self.cuts[i]; canvas=bytearray(dw*dh*4)
        for pos,cx,cy,w,h in chips:
            for y in range(h):
                ty=cy+y
                if not 0<=ty<dh: continue
                x0,x1=max(cx,0),min(cx+w,dw)
                if x1<=x0: continue
                s=pos+(y*w+(x0-cx))*4; t=(ty*dw+x0)*4
                canvas[t:t+(x1-x0)*4]=self.debuf[s:s+(x1-x0)*4]
        return Image.frombytes('RGBA',(dw,dh),bytes(canvas),'raw','BGRA')
    def set_image(self,i,img):
        dw,dh,chips=self.cuts[i]; data=img.tobytes('raw','BGRA')
        for pos,cx,cy,w,h in chips:
            for y in range(h):
                ty=cy+y
                if not 0<=ty<dh: continue
                x0,x1=max(cx,0),min(cx+w,dw)
                if x1<=x0: continue
                s=(ty*dw+x0)*4; t=pos+(y*w+(x0-cx))*4
                self.debuf[t:t+(x1-x0)*4]=data[s:s+(x1-x0)*4]
    def build(self):
        body=bytes(self.debuf); comp=lzss_compress(body)
        if lzss_decompress(comp, tolerant=True)!=body: raise RuntimeError('type2 recompress verify failed')
        return self.raw[:self.off]+comp

class G00Type0:
    def __init__(self, raw):
        self.raw=raw; self.w,self.h=struct.unpack_from('HH',raw,1)
        arc=struct.unpack_from('I',raw,5)[0]
        self.bgra=bytearray(lzss32_decompress(raw[5:5+arc]))
        self.cuts={0:(self.w,self.h,None)}
    def get_image(self,i):
        from PIL import Image
        return Image.frombytes('RGBA',(self.w,self.h),bytes(self.bgra),'raw','BGRA')
    def set_image(self,i,img):
        self.bgra=bytearray(img.tobytes('raw','BGRA'))
        self.bgra[3::4]=b'\xff'*(len(self.bgra)//4)
    def build(self):
        bgr=bytearray(len(self.bgra)//4*3)
        bgr[0::3]=self.bgra[0::4]; bgr[1::3]=self.bgra[1::4]; bgr[2::3]=self.bgra[2::4]
        comp=lzss_compress(bytes(bgr), literal_size=3)
        if lzss32_decompress(comp)!=bytes(self.bgra): raise RuntimeError('type0 recompress verify failed')
        return self.raw[:5]+comp

def load_g00(raw):
    if raw[0]==2: return G00Type2(raw)
    if raw[0]==0: return G00Type0(raw)
    raise RuntimeError('unsupported g00 type %d'%raw[0])

_fonts={}
def get_font(path,size):
    from PIL import ImageFont
    k=(str(path),size)
    if k not in _fonts: _fonts[k]=ImageFont.truetype(str(path),size)
    return _fonts[k]

def bg_rows(px,x0,x1,y0,y1,n=6):
    rows={}
    for y in range(y0,y1):
        pts=[px[x,y] for x in range(max(x0,x1-n),x1)]
        rows[y]=tuple(sorted(p[k] for p in pts)[len(pts)//2] for k in range(4))
    return rows
def bg_rows_mode(px,x0,x1,y0,y1):
    rows={}
    for y in range(y0,y1):
        rows[y]=Counter(px[x,y] for x in range(x0,x1)).most_common(1)[0][0]
    return rows
def erase_text(img,mask,box,rows,grow=1,cpx=None):
    from PIL import ImageFilter
    W,H=img.size
    if grow>0: mask=mask.filter(ImageFilter.MaxFilter(2*grow+1))
    mp,px=mask.load(),img.load(); x0,y0,x1,y1=box
    for y in range(y0,y1):
        for x in range(x0,x1):
            if mp[x,y]: px[x,y]=cpx[x,y] if cpx is not None else rows[y]

def auto_color(samples, top=0.3):
    if not samples: return (255,255,255)
    samples=sorted(samples,key=lambda s:s[0],reverse=True)
    core=[c for _,c in samples[:max(5,int(len(samples)*top))]]
    return tuple(sorted(c[k] for c in core)[len(core)//2] for k in range(3))

def auto_stroke(msamples, core, frac=0.4, min_dist=80):
    if not msamples: return None
    mx=max(d for d,_ in msamples)
    cand=[c for d,c in msamples if d>=mx*frac and sum((c[k]-core[k])**2 for k in range(3))>min_dist**2]
    if len(cand)<max(8,len(msamples)//10): return None
    return tuple(sorted(c[k] for c in cand)[len(cand)//2] for k in range(3))

def auto_layers_vo(samples, min_dist=40):
    white=(255,255,255); cnt=Counter(c for _,c in samples)
    if cnt.get(white,0)<5:
        top=[c for c,_ in cnt.most_common(6)]
        white=max(top,key=sum) if top else white
    others=[c for c,_ in cnt.most_common(20) if sum((c[i]-white[i])**2 for i in range(3))>min_dist**2]
    if not others: return white,white,white
    outline=others[0]
    cream=next((c for c in others[1:] if sum((c[i]-outline[i])**2 for i in range(3))>min_dist**2), outline)
    return outline,cream,white

def _seg_dist(p,a,b):
    ab=[b[i]-a[i] for i in range(3)]; den=sum(v*v for v in ab)
    t=0.0 if den==0 else max(0.0,min(1.0,sum((p[i]-a[i])*ab[i] for i in range(3))/den))
    return sum((p[i]-(a[i]+t*ab[i]))**2 for i in range(3))**0.5

def apply_vo(img,it,fpath,log,refimg):
    from PIL import ImageDraw
    if refimg is None or refimg.size!=img.size: return False
    W,H=img.size; x0,y0,x1,y1=it['box']
    x0,y0,x1,y1=max(0,x0),max(0,y0),min(W,x1),min(H,y1)
    px=img.load(); bx=it.get('bgx',[150,156])
    rows=bg_rows(refimg.load(),bx[0],bx[1],y0,y1); tol=it.get('tol',4); cand={}
    for y in range(y0,y1):
        bg=rows[y]
        for x in range(x0,x1):
            r,g,b,a=px[x,y]
            if max(abs(r-bg[0]),abs(g-bg[1]),abs(b-bg[2]))>tol: cand[(x,y)]=(r,g,b)
    if not cand: return False
    plate=rows[(y0+y1)//2][:3]; ranked=[c for c,_ in Counter(cand.values()).most_common(12)]
    white=(255,255,255); others=[c for c in ranked if c!=white]; outline=others[0] if others else white
    cream=next((c for c in others[1:] if sum((c[i]-outline[i])**2 for i in range(3))>40**2), outline) if others else white
    pal=[plate,outline,cream,white]; segs=[(pal[i],pal[j]) for i in range(4) for j in range(i+1,4)]
    seg_tol=it.get('seg',22)
    mask={p for p,c in cand.items() if min(_seg_dist(c,s0,s1) for s0,s1 in segs)<seg_tol}
    erase=set()
    for (x,y) in mask:
        for ddx in (-1,0,1):
            for ddy in (-1,0,1):
                q=(x+ddx,y+ddy)
                if q in cand: erase.add(q)
    for (x,y) in erase: px[x,y]=rows[y]
    text=it['text']; size=it.get('size',18); ow,cw=it.get('ow',2),it.get('cw',1); maxw=it.get('maxw',60)
    f=get_font(fpath,size)
    while size>9 and f.getlength(text)+2*(ow+cw)>maxw:
        size-=1; f=get_font(fpath,size)
    w=f.getlength(text); bold=1 if it.get('bold',True) else 0
    cx=it.get('cx',128); cy=it.get('cy',30)+it.get('dy',0); x=cx-(w+bold)/2
    d=ImageDraw.Draw(img)
    for sw,col in ((ow+cw,outline),(cw,cream),(0,white)):
        for off in range(bold+1):
            d.text((x+off,cy),text,font=f,fill=col+(255,),anchor='lm',stroke_width=sw,stroke_fill=col+(255,))
    return True

def apply_item(img,it,fpath,log,refimg=None,clean=None):
    from PIL import Image, ImageDraw
    if it.get('style')=='vo': return apply_vo(img,it,fpath,log,refimg)
    W,H=img.size; x0,y0,x1,y1=it['box']
    x0,y0,x1,y1=max(0,x0),max(0,y0),min(W,x1),min(H,y1)
    px=img.load(); bgx=it.get('bgx',[x0,x1]); src_px=refimg.load() if refimg is not None else px
    if it.get('bgmode')=='mode': rows=bg_rows_mode(px,x0,x1,y0,y1)
    else: rows=bg_rows(src_px,bgx[0],bgx[1],y0,y1)
    cpx=clean.load() if (it.get('bgclean') and clean is not None and clean.size==img.size) else None
    if cpx is not None:
        mid_x=(x0+x1)//2; rows={y:cpx[mid_x,y] for y in range(y0,y1)}
    refpx=refimg.load() if (it.get('refdiff') and refimg is not None and refimg.size==img.size) else None
    tol_ref=it.get('tol_ref',30); tol=it.get('tol',30); tol_bb=it.get('tol_bbox',60); absdiff=it.get('absdiff',False)
    mask=Image.new('L',(W,H),0); mp=mask.load(); xs=[]; ys=[]; samples=[]; msamples=[]
    for y in range(y0,y1):
        for x in range(x0,x1):
            r,g,b,a=px[x,y]
            if refpx is not None:
                q=refpx[x,y]
                if abs(r-q[0])+abs(g-q[1])+abs(b-q[2])<=tol_ref: continue
            if cpx is not None:
                c_=cpx[x,y]; d=max(abs(r-c_[0]),abs(g-c_[1]),abs(b-c_[2]))
            else:
                bg=rows[y]; d=(r+g+b)-(bg[0]+bg[1]+bg[2])
                if absdiff: d=abs(d)
            if a>=128 and d>tol:
                mp[x,y]=255; msamples.append((d,(r,g,b)))
                if d>tol_bb: xs.append(x); ys.append(y); samples.append((d,(r,g,b)))
    if not xs: return False
    bx0,bx1,by0,by1=min(xs),max(xs)+1,min(ys),max(ys)+1
    erase_text(img,mask,(x0,y0,x1,y1),rows,it.get('grow',1),cpx)
    text=it['text']; size=it.get('size') or max(10, round((by1-by0)/0.88)); maxw=it.get('maxw',x1-x0)
    f=get_font(fpath,size)
    while size>9 and f.getlength(text)>maxw:
        size-=1; f=get_font(fpath,size)
    w=f.getlength(text); align=it.get('align','left')
    if align=='center': x=(bx0+bx1)/2-w/2
    elif align=='right': x=bx1-w
    else: x=bx0
    x+=it.get('dx',0); cy=(by0+by1)/2+it.get('dy',0)
    color=it.get('color') or (255,255,255); mode_stroke=None; layers3=None
    if color=='auto':
        if cpx is not None:
            layers3=auto_layers_vo(samples); color,mode_stroke=layers3[2],layers3[0]
        else: color=auto_color(samples)
    color=tuple(color); sw=it.get('stroke',0); scs=it.get('stroke_color') or (40,100,210)
    if scs=='auto':
        scs=mode_stroke if cpx is not None else auto_stroke(msamples,color)
        if scs is None: sw,scs=0,(0,0,0)
    if sw=='auto': sw=1
    sc=tuple(scs)+(255,); d=ImageDraw.Draw(img)
    if layers3:
        ow,cw=it.get('ow',2),it.get('cw',1); outline,cream,white=layers3
        for lw,col in ((ow+cw,outline),(cw,cream),(0,white)):
            for off in range(2 if it.get('bold') else 1):
                d.text((x+off,cy),text,font=f,fill=col+(255,),anchor='lm',stroke_width=lw,stroke_fill=col+(255,))
        return True
    sh=it.get('shadow')
    if sh: d.text((x+sh[0],cy+sh[1]),text,font=f,fill=tuple(sh[2])+(255,),anchor='lm')
    d.text((x,cy),text,font=f,fill=color+(255,),anchor='lm',stroke_width=sw,stroke_fill=sc)
    if it.get('bold'): d.text((x+1,cy),text,font=f,fill=color+(255,),anchor='lm',stroke_width=sw,stroke_fill=sc)
    return True

_clean_cache={}
def _src_g00(game,name):
    # prefer original CN from g00_cn if exists else g00
    p=game/'g00_cn'/(name+'.g00')
    if p.exists(): return p
    return game/'g00'/(name+'.g00')

def get_ref_img(game,it,c):
    n=it.get('bgfile')
    if not n: return None
    p=game/'g00_cn'/(n+'.g00')
    if not p.exists(): p=game/'g00'/(n+'.g00')
    if not p.exists(): return None
    o=load_g00(p.read_bytes())
    return o.get_image(c if c in o.cuts else 0)

def get_clean_img(game,it,c,size,log):
    pre=it.get('bgclean')
    if not pre: return None
    key=(pre,c,size)
    if key in _clean_cache: return _clean_cache[key]
    base=game/'g00_cn' if (game/'g00_cn').exists() else game/'g00'
    ims=[]
    for p in sorted(base.glob(pre+'*_ZH.g00')):
        try: o=load_g00(p.read_bytes())
        except Exception: continue
        if c in o.cuts:
            im=o.get_image(c)
            if im.size==size: ims.append(im)
    if len(ims)<3:
        _clean_cache[key]=None
    else:
        pxs=[im.load() for im in ims]; out=__import__('PIL.Image',fromlist=['Image']).new('RGBA',size); op=out.load()
        for yy in range(size[1]):
            for xx in range(size[0]):
                op[xx,yy]=Counter(q[xx,yy] for q in pxs).most_common(1)[0][0]
        _clean_cache[key]=out
    return _clean_cache[key]

def process_ui_file(ent, game, out_dir, fpath, log):
    name=ent['name']; src=_src_g00(game,name)
    if not src.exists():
        log(f'  [ui] {name}: source missing'); return False
    dst=out_dir/(name+'.g00')
    try:
        obj=load_g00(src.read_bytes())
    except Exception as e:
        log(f'  [ui] {name}: load failed {e}'); return False
    cuts=ent.get('cuts') or sorted(obj.cuts.keys())
    changed=False
    for c in cuts:
        if c not in obj.cuts: continue
        img=obj.get_image(c); ok=False
        for it in ent['items']:
            if c in it.get('cuts',[c]):
                try:
                    if apply_item(img,it,fpath,log,get_ref_img(game,it,c),get_clean_img(game,it,c,img.size,log)): ok=True
                except Exception as e:
                    log(f'  [ui] {name} cut{c} err {e}')
        if ok:
            obj.set_image(c,img); changed=True
    if not changed: return False
    try:
        data=obj.build()
        atomic_write(dst,data)
        log(f'  [ui] {name}: written')
        return True
    except Exception as e:
        log(f'  [ui] {name}: build failed {e}'); return False

def apply_ui(game, font_path):
    out_dir=game/'g00_jp'; out_dir.mkdir(exist_ok=True)
    # copy all *_ZH.g00 baseline first if missing? they may already be there from step 3
    def log(m): print(m)
    written=0
    for ent in UI_SPEC['files']:
        if process_ui_file(ent,game,out_dir,font_path,log): written+=1
    # ensure all other _system_*_ZH.g00 exist (copy from g00_cn/g00 if not localized/present)
    src_dir=game/'g00_cn' if (game/'g00_cn').exists() else game/'g00'
    copied=0
    for p in src_dir.glob('_system_*_ZH.g00'):
        dst=out_dir/p.name
        if not dst.exists():
            atomic_write(dst,p.read_bytes()); copied+=1
    log(f'[ui] localized={written} copied_others={copied}')

# -------------------- main --------------------
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('game', nargs='?', type=Path, default=DEFAULT_GAME, help='install directory')
    ap.add_argument('--remove', action='store_true', help='remove JP localization and restore original files')
    a=ap.parse_args(); game=a.game
    assert (game/'SiglusEngine_Steam.exe').exists(), 'game not found'
    if a.remove:
        orig=game/'steam_api_original.dll'
        proxy=game/'steam_api.dll'
        if orig.exists():
            atomic_write(proxy, orig.read_bytes())
            try: orig.unlink()
            except Exception: pass
            print('[proxy] restored steam_api.dll')
        elif not proxy.exists():
            print('[proxy] not installed')
        for f in [game/'GameexeJP.dat', game/'dat'/FONT_NAME]:
            if f.exists():
                f.unlink(); print(f'[remove] {f}')
        for d in [game/'dat_jp', game/'g00_jp']:
            if d.exists():
                shutil.rmtree(d); print(f'[remove] {d}/')
        print('[done] JP localization removed')
        return
    assert (game/'GameexeZH.dat').exists(), 'GameexeZH.dat missing'
    (game/'g00_jp').mkdir(exist_ok=True); (game/'dat_jp').mkdir(exist_ok=True)
    # 1 Gameexe
    text=gameexe_unpack(game/'GameexeZH.dat').decode('utf-16-le')
    newt=convert_gameexe_text(text)
    packed=gameexe_pack(newt.encode('utf-16-le'))
    dec_data=_crypt1_xor(_c2(packed[8:], DECRYPT2_T1), CLANNAD_KEY)
    comp,_decomp=struct.unpack('II', dec_data[:8])
    dec=lzss_decompress(dec_data[:8]+dec_data[8:comp], tolerant=True)
    assert dec.decode('utf-16-le','strict')==newt
    atomic_write(game/'GameexeJP.dat', packed)
    print('[GameexeJP.dat] written' if newt!=text else '[GameexeJP.dat] unchanged')
    # 2 dbs
    total_c=total_w=0
    for p in sorted((game/'dat').iterdir()):
        if p.suffix.lower()=='.dbs':
            out,ch,w=convert_dbs(p); total_c+=ch; total_w+=w
            data=out if out is not None else p.read_bytes()
            if ch: print(f'[dbs] {p.name} changed={ch} warn={w}')
        else:
            data=p.read_bytes()
        atomic_write(game/'dat_jp'/p.name, data)
    font_path=ensure_font(game)
    print(f'[dbs] total changed={total_c} warn={total_w}')
    # 3 g00 twins / fallback
    files={p.name.lower():p for p in (game/'g00').iterdir() if p.is_file()}
    zh=[p for n,p in files.items() if n.endswith('_zh.g00')]
    tw=twin_n=0
    for z in zh:
        twin=files.get(z.name.lower()[:-len('_zh.g00')]+'.g00')
        src=twin if twin else z
        if twin: twin_n+=1
        atomic_write(game/'g00_jp'/z.name, src.read_bytes())
        tw+=1
    print(f'[g00_jp] placed {tw} *_ZH.g00 (twins={twin_n}, fallback={tw-twin_n})')
    # 3b UI localization integrated
    try:
        apply_ui(game, font_path)
    except Exception as e:
        print(f'[ui] WARNING: UI localization failed: {e}')
    # 4 steam_api proxy
    ensure_proxy(game)
    print('done')

if __name__=='__main__':
    main()
