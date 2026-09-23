"""Authentication enforcement, using only uniquely correlated password failures."""
import collections, ipaddress, json, sqlite3, time
from pathlib import Path

DB = Path('/var/lib/frp-ssh-features/events.sqlite')

def candidates(rows, now, geo, prior_bans):
    grouped = collections.defaultdict(list)
    for r in rows:
        if r['status'] != 'exact_cookie_pair' or r['outcome'] != 'failure' or r['method'] != 'password':
            continue
        try:
            addr = ipaddress.ip_address(r['source'])
        except (ValueError, TypeError):
            continue
        if not addr.is_global or not now-86400 <= r['ts'] <= now-30:
            continue
        # Never reuse evidence from before a prior ban expired.
        prior = prior_bans.get('forward:'+str(addr), {})
        if prior and (prior['until'] == 0 or r['ts'] <= prior['until']):
            continue
        grouped[str(addr)].append(r)
    nets = []
    if now-geo.get('updated_at', 0) <= 45*86400:
        nets = [ipaddress.ip_network(n) for v in ('4','6') for n in geo.get(v, [])]
    for ip, events in grouped.items():
        addr = ipaddress.ip_address(ip)
        tier = 'HF' if any(addr.version == n.version and addr in n for n in nets) else 'OTHER'
        # Shared school exits have higher tolerance; geography never exempts attacks.
        thresholds = (12,30,60) if tier == 'HF' else (5,10,20)
        reasons = []
        counts = {}
        for seconds, limit in zip((600,3600,86400), thresholds):
            count = sum(r['ts'] >= now-seconds for r in events)
            counts[str(seconds)] = count
            if count >= limit: reasons.append('password_failures_'+str(seconds))
        invalid = {r['username'] for r in events if r['invalid'] and r['ts'] >= now-3600}
        if len(invalid) >= (8 if tier == 'HF' else 3):
            reasons.append('invalid_username_spray_3600')
        if reasons:
            yield ip, tier, {'reasons':reasons, 'counts':counts, 'invalid_usernames_1h':len(invalid)}

def scan(guard):
    now = time.time()
    with sqlite3.connect('file:'+str(DB)+'?mode=ro', uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT source,ts,status,outcome,method,username,invalid FROM auth WHERE ts>=? AND ts<=? AND status='exact_cookie_pair' AND outcome='failure' AND method='password'", (now-86400, now-30)).fetchall()
    geo = json.loads((guard.path.parent/'geo.json').read_text())
    for ip, tier, evidence in candidates(rows, now, geo, guard.state['bans']):
        guard.ban('forward', ip, tier, now, evidence)
