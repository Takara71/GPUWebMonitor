#!/usr/bin/env python3
import argparse,hashlib,ipaddress,json,os,subprocess,tempfile,time,urllib.request
from pathlib import Path
BASE='https://raw.githubusercontent.com/lionsoul2014/ip2region/master/data/'
def parse(path,version):
    networks=[];count=0
    with Path(path).open() as f:
        for line in f:
            if not line.strip():continue
            fields=line.strip().split('|');assert len(fields)>=7
            first,last=map(ipaddress.ip_address,fields[:2]);assert first.version==last.version==version and first<=last
            count+=1
            if fields[2]=='中国' and fields[3]=='安徽省' and fields[4]=='合肥市':networks.extend(ipaddress.summarize_address_range(first,last))
    assert int(last)==(2**(32 if version==4 else 128))-1, 'Truncated city database'
    assert count>(400000 if version==4 else 500000), 'Incomplete city database'
    collapsed=[str(n) for n in ipaddress.collapse_addresses(networks)]
    assert len(collapsed)>10 and sum(ipaddress.ip_network(n).num_addresses for n in collapsed)>1000
    return collapsed,{'rows':count,'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'url':BASE+f'ipv{version}_source.txt'}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--v4');ap.add_argument('--v6');ap.add_argument('--output',default='/var/lib/frp-ssh-guard/geo.json');a=ap.parse_args()
    d={'updated_at':time.time(),'source':'ip2region public city data; refreshed weekly; source updates are irregular','metadata':{}}
    with tempfile.TemporaryDirectory(prefix='frp-geo-') as tmp:
        for v,existing in [(4,a.v4),(6,a.v6)]:
            p=Path(existing) if existing else Path(tmp)/f'v{v}.txt'
            if not existing:
                with urllib.request.urlopen(BASE+f'ipv{v}_source.txt',timeout=120) as r,p.open('wb') as f:
                    total=0;expected=r.headers.get('Content-Length')
                    while chunk:=r.read(1024*1024):
                        total+=len(chunk);assert total<150*1024*1024;f.write(chunk)
                    if expected:assert total==int(expected), 'Incomplete database download'
            d[str(v)],d['metadata'][str(v)]=parse(p,v)
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix('.new');tmp.write_text(json.dumps(d));tmp.chmod(0o600);tmp.replace(dest)
    # Update only geographic sets, keeping bans, counters and active sessions intact.
    if dest==Path('/var/lib/frp-ssh-guard/geo.json'):
        cmd=''
        for v in ['4','6']:
            cmd+=f'flush set inet frp_ssh_guard hefei{v}\nadd element inet frp_ssh_guard hefei{v} {{ '+', '.join(d[v])+' }\n'
        subprocess.run(['nft','-c','-f','-'],input=cmd,text=True,check=True)
        subprocess.run(['nft','-f','-'],input=cmd,text=True,check=True)
    print(json.dumps({'networks':{v:len(d[v]) for v in ['4','6']},'metadata':d['metadata']},ensure_ascii=False))
if __name__=='__main__':main()
