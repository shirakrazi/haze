#!/usr/bin/env python3
"""Refresh data.json for the haze page.

Pulls Singapore PSI and PM2.5 from NEA's open feed (data.gov.sg) and haze
headlines from news RSS feeds. Standard library only. On GitHub it is run
by the workflow in .github/workflows/update.yml.

  python3 update.py            normal run
  HAZE_DATA=/path/data.json    override where the data file lives
"""
import json, os, re, sys, tempfile, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("HAZE_DATA", os.path.join(HERE, "data.json"))
PINNED = os.path.join(HERE, "pinned.json")
SGT = timezone(timedelta(hours=8))
REGIONS = ["west", "east", "central", "south", "north"]
PSI_URL = "https://api-open.data.gov.sg/v2/real-time/api/psi"
PM25_URL = "https://api-open.data.gov.sg/v2/real-time/api/pm25"
UA = "Mozilla/5.0 (compatible; haze-page-updater/1.0)"

# News: direct outlet feeds first, then a Google News search that covers other outlets.
FEEDS = [
    ("CNA", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416"),
    ("The Straits Times", "https://www.straitstimes.com/news/singapore/rss.xml"),
    (None, "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": "(haze OR PSI) (Singapore OR Malaysia OR Indonesia OR Sumatra OR Kalimantan) when:2d",
        "hl": "en-SG", "gl": "SG", "ceid": "SG:en"})),
]
KEYWORDS = re.compile(r"\bhaze\b|\bhazy\b|\bPSI\b|hot ?spots?\b|transboundary|air quality|\bsmog\b", re.I)
MAX_NEWS, MAX_PER_SOURCE, MAX_AGE_H, MAX_HIST = 14, 3, 48, 48


def log(msg):
    print(datetime.now(SGT).strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def find(obj, key):
    """First value stored under `key` anywhere inside nested dicts/lists."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = find(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find(v, key)
            if hit is not None:
                return hit
    return None


def region_values(raw):
    if not isinstance(raw, dict):
        return None
    out = {}
    for r in REGIONS:
        v = raw.get(r)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 600:
            return None
        out[r] = int(round(v))
    return out


def latest_item(payload):
    items = find(payload, "items") or []
    items = [i for i in items if isinstance(i, dict) and i.get("timestamp")]
    return max(items, key=lambda i: i["timestamp"]) if items else None


def parse_psi(text):
    item = latest_item(json.loads(text))
    if not item:
        return None, None
    return item["timestamp"], region_values(find(item, "psi_twenty_four_hourly"))


def parse_pm25(text):
    item = latest_item(json.loads(text))
    return region_values(find(item, "pm25_one_hourly")) if item else None


def iso_sgt(dt):
    return dt.astimezone(SGT).replace(microsecond=0).isoformat()


def to_dt(s):
    try:
        d = datetime.fromisoformat(s if len(s) > 10 else s + "T12:00:00+08:00")
        return d if d.tzinfo else d.replace(tzinfo=SGT)
    except Exception:
        return None


def title_key(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())[:60]


def parse_feed(xml_bytes, default_source):
    out = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = default_source or (src_el.text.strip() if src_el is not None and src_el.text else "")
        if not default_source and source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if title and source and link.startswith("https://") and KEYWORDS.search(title):
            out.append({"source": source, "title": title, "url": link, "time": iso_sgt(when), "summary": ""})
    return out


def refresh_news(existing, now):
    pinned = []
    try:
        with open(PINNED, encoding="utf-8") as f:
            pinned = [dict(p, pinned=True) for p in json.load(f) if str(p.get("url", "")).startswith("https://")]
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"pinned.json ignored: {e}")
    fresh, ok = [], False
    for source, url in FEEDS:
        try:
            got = parse_feed(get(url), source)
            fresh += got
            ok = True
            log(f"news feed {source or 'Google News'}: {len(got)} haze items")
        except Exception as e:
            log(f"news feed {source or 'Google News'} failed: {e}")
    # keep earlier items (they may carry written summaries), then add new ones
    pool = [n for n in existing if not n.get("pinned")] + fresh
    cutoff = now - timedelta(hours=MAX_AGE_H)
    seen = {title_key(p.get("title", "")) for p in pinned} | {p.get("url") for p in pinned}
    kept, per_source = [], {}
    pool.sort(key=lambda n: to_dt(n.get("time", "")) or cutoff, reverse=True)
    for n in pool:
        d = to_dt(n.get("time", ""))
        k = title_key(n.get("title", ""))
        if not d or d < cutoff or d > now + timedelta(hours=1) or k in seen or n.get("url") in seen:
            continue
        if per_source.get(n["source"], 0) >= MAX_PER_SOURCE:
            continue
        seen |= {k, n.get("url")}
        per_source[n["source"]] = per_source.get(n["source"], 0) + 1
        kept.append(n)
    return pinned + kept[: max(0, MAX_NEWS - len(pinned))], ok


def main():
    now = datetime.now(SGT)
    try:
        with open(DATA, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    snap = lambda d: json.dumps({k: v for k, v in d.items() if k != "newsUpdated"}, sort_keys=True)
    before = snap(data)
    stamp = urllib.parse.quote(now.strftime("%Y-%m-%dT%H:%M:00"))

    try:
        ts, psi = parse_psi(get(f"{PSI_URL}?date_time={stamp}"))
        when = to_dt(ts) if ts else None
        if psi and when and timedelta(0) <= now - when + timedelta(minutes=5) <= timedelta(hours=6):
            data["updated"], data["psi"] = iso_sgt(when), psi
            hist = {h["t"]: h for h in data.get("hist", []) if isinstance(h, dict) and "t" in h}
            hist[data["updated"]] = {"t": data["updated"], "psi": psi}
            data["hist"] = [hist[t] for t in sorted(hist, key=lambda t: to_dt(t) or now)][-MAX_HIST:]
            log(f"PSI {data['updated']}: {psi}")
        else:
            log(f"PSI reading rejected (timestamp {ts}, values {psi})")
    except Exception as e:
        log(f"PSI fetch failed: {e}")

    try:
        pm = parse_pm25(get(f"{PM25_URL}?date_time={stamp}"))
        if pm:
            data["pm25"] = pm
        else:
            log("PM2.5 reading rejected")
    except Exception as e:
        log(f"PM2.5 fetch failed: {e}")

    news, ok = refresh_news(data.get("news", []), now)
    data["news"] = news
    if ok:
        data["newsUpdated"] = iso_sgt(now)

    if snap(data) == before:
        log("no change")
        return 0
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(DATA), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, DATA)
    log(f"wrote {DATA} ({len(news)} news items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
