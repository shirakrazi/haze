#!/usr/bin/env python3
"""Refresh data.json for the haze page.

Pulls Singapore PSI and PM2.5 from NEA's open feed (data.gov.sg) and haze
headlines from news RSS feeds into data.json. Also writes regional.json:
Malaysia API readings (Department of Environment), Indonesia ISPU readings
(Ministry of Environment) and NASA FIRMS fire detections counted by
province, with an hourly record under history/. Standard library only. On GitHub it is run
by the workflow in .github/workflows/update.yml.

  python3 update.py            normal run
  HAZE_DATA=/path/data.json    override where the data file lives
"""
import csv, io, json, os, re, sys, tempfile, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("HAZE_DATA", os.path.join(HERE, "data.json"))
PINNED = os.path.join(HERE, "pinned.json")
REGIONAL = os.path.join(os.path.dirname(DATA), "regional.json")
HISTORY = os.path.join(os.path.dirname(DATA), "history")
PROVINCES = os.path.join(HERE, "provinces.json")
SGT = timezone(timedelta(hours=8))
REGIONS = ["west", "east", "central", "south", "north"]
PSI_URL = "https://api-open.data.gov.sg/v2/real-time/api/psi"
PM25_URL = "https://api-open.data.gov.sg/v2/real-time/api/pm25"
UA = "Mozilla/5.0 (compatible; haze-page-updater/1.0)"

# Regional sources. All are public and need no key.
APIMS_URL = ("https://eqms.doe.gov.my/api3/publicmapproxy/PUBLIC_DISPLAY/CAQM_MCAQM_Current_Reading/MapServer/0/query?"
             "f=json&where=1%3D1&outFields=STATION_ID,STATION_LOCATION,STATE_NAME,API,CLASS,DATETIME,LONGITUDE,LATITUDE,"
             "PARAM_SELECTED,PM25_CONC&returnGeometry=false")
ISPU_URL = "https://ispu.kemenlh.go.id/apimobile/v1/getStations"
FIRMS_24H = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_SouthEast_Asia_24h.csv"
FIRMS_7D = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_SouthEast_Asia_7d.csv"
BBOX = (93.0, -9.5, 121.0, 9.0)  # lon0, lat0, lon1, lat1: the area the map shows
ID_TZ = {"WIB": 7, "WITA": 8, "WIT": 9}
ID_PARAMS = [("a_pm25", "PM2.5"), ("a_pm10", "PM10"), ("a_so2", "SO2"), ("a_co", "CO"), ("a_o3", "O3"), ("a_no2", "NO2"), ("a_hc", "HC")]
MAX_STATION_AGE_H = 12

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


# ---------- regional readings and fire detections ----------

def num(v):
    try:
        f = float(str(v).strip())
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def in_bbox(lon, lat):
    return lon is not None and lat is not None and BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def tidy(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip(" ,")
    return name.title() if name.isupper() or name.islower() else name


def iso_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


DEBUG = {}


def fetch_malaysia(now):
    raw = get(APIMS_URL)
    feats = json.loads(raw).get("features") or []
    DEBUG["my"] = {"bytes": len(raw), "feats": len(feats), "f": feats[:3], "now": str(now)}
    rows = []
    for f in feats:
        a = f.get("attributes") or {}
        v, lon, lat, ms = num(a.get("API")), num(a.get("LONGITUDE")), num(a.get("LATITUDE")), num(a.get("DATETIME"))
        if v is None or not 0 <= v <= 2000 or not in_bbox(lon, lat) or not ms:
            continue
        rows.append((a, int(round(v)), lon, lat, datetime.fromtimestamp(ms / 1000, timezone.utc)))
    if not rows:
        return []
    # The feed stores Malaysian clock time as if it were UTC. Detect that and correct it.
    shift = timedelta(hours=8) if max(r[4] for r in rows) > now + timedelta(minutes=20) else timedelta(0)
    out = {}
    for a, v, lon, lat, dt in rows:
        dt -= shift
        if now - dt > timedelta(hours=MAX_STATION_AGE_H):
            continue
        sid = str(a.get("STATION_ID") or "").strip()
        if not sid:
            continue
        out[sid] = {"id": sid, "n": tidy(a.get("STATION_LOCATION")), "s": tidy(a.get("STATE_NAME")),
                    "lon": round(lon, 3), "lat": round(lat, 3), "v": v, "t": iso_utc(dt)}
    return sorted(out.values(), key=lambda r: r["id"])


def fetch_indonesia(now):
    payload = json.loads(get(ISPU_URL))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    out = {}
    for r in rows or []:
        if not isinstance(r, dict) or str(r.get("stasiun_show", "1")) not in ("1", "True", "true"):
            continue
        lon, lat = num(r.get("lon")), num(r.get("lat"))
        if not in_bbox(lon, lat):
            continue
        best, param = None, ""
        for key, label in ID_PARAMS:
            x = num(r.get(key))
            if x is not None and 0 <= x <= 5000 and (best is None or x > best):
                best, param = x, label
        if best is None:
            continue
        try:
            local = datetime.strptime(str(r.get("waktu"))[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        dt = local.replace(tzinfo=timezone(timedelta(hours=ID_TZ.get(str(r.get("time_z") or "WIB").strip().upper(), 7))))
        if not timedelta(minutes=-20) <= now - dt <= timedelta(hours=MAX_STATION_AGE_H):
            continue
        sid = str(r.get("id_stasiun") or "").strip()
        if not sid:
            continue
        row = {"id": sid, "n": tidy(r.get("nama")), "k": tidy(r.get("kota")), "s": tidy(r.get("provinsi")),
               "lon": round(lon, 3), "lat": round(lat, 3), "v": int(round(best)), "p": param, "t": iso_utc(dt),
               "dbg": {k: r.get(k) for k in list(r) if k[:2] in ("a_", "c_") or k == "param"}}
        if sid not in out or row["t"] > out[sid]["t"]:
            out[sid] = row
    return sorted(out.values(), key=lambda r: r["id"])


def load_provinces():
    with open(PROVINCES, encoding="utf-8") as f:
        provs = json.load(f)
    for p in provs:
        xs = [c[0] for r in p["rings"] for c in r]
        ys = [c[1] for r in p["rings"] for c in r]
        p["box"] = (min(xs) - .05, min(ys) - .05, max(xs) + .05, max(ys) + .05)
    return provs


def in_ring(x, y, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def province_of(lon, lat, provs):
    for p in provs:
        b = p["box"]
        if b[0] <= lon <= b[2] and b[1] <= lat <= b[3] and any(in_ring(lon, lat, r) for r in p["rings"]):
            return p
    return None


def firms_points(url):
    """Detections inside the map area, low-confidence ones dropped. Yields (lon, lat, utc datetime)."""
    text = get(url).decode("utf-8", "replace")
    for row in csv.DictReader(io.StringIO(text)):
        lon, lat = num(row.get("longitude")), num(row.get("latitude"))
        if not in_bbox(lon, lat) or str(row.get("confidence", "")).strip().lower() in ("l", "low"):
            continue
        try:
            dt = datetime.strptime(f"{row['acq_date']} {str(row['acq_time']).zfill(4)}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        yield lon, lat, dt


def fetch_fires(now):
    provs = load_provinces()
    stat = {p["id"]: {"id": p["id"], "n": p["name"], "n24": 0, "sx": 0.0, "sy": 0.0, "days": {}} for p in provs}
    total24 = 0
    for lon, lat, dt in firms_points(FIRMS_24H):
        total24 += 1
        p = province_of(lon, lat, provs)
        if p:
            s = stat[p["id"]]
            s["n24"] += 1
            s["sx"] += lon
            s["sy"] += lat
    # Daily totals use Western Indonesia days (UTC+7), which keeps each afternoon pass in one day.
    wib = timezone(timedelta(hours=7))
    days = [(now.astimezone(wib) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    for lon, lat, dt in firms_points(FIRMS_7D):
        d = dt.astimezone(wib).strftime("%Y-%m-%d")
        if d not in days:
            continue
        p = province_of(lon, lat, provs)
        if p:
            stat[p["id"]]["days"][d] = stat[p["id"]]["days"].get(d, 0) + 1
    out = []
    for p in provs:
        s = stat[p["id"]]
        if s["n24"] >= 5:
            lon, lat = s["sx"] / s["n24"], s["sy"] / s["n24"]
        else:  # too few detections to place the marker by them; use the middle of the largest land mass
            ring = p["rings"][0]
            lon, lat = sum(c[0] for c in ring) / len(ring), sum(c[1] for c in ring) / len(ring)
        out.append({"id": s["id"], "n": s["n"], "lon": round(lon, 2), "lat": round(lat, 2), "n24": s["n24"],
                    "d": [s["days"].get(d, 0) for d in days]})
    return {"days": days, "all24": total24, "prov": sorted(out, key=lambda r: -r["n24"])}


def write_json(path, obj, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def refresh_regional(now, sg_psi):
    """Malaysia and Indonesia station readings plus fire detections by province.
    A source that fails keeps its previous values; its own timestamp shows how old they are."""
    try:
        with open(REGIONAL, encoding="utf-8") as f:
            reg = json.load(f)
    except (FileNotFoundError, ValueError):
        reg = {}
    before = json.dumps(reg, sort_keys=True)
    stamp = iso_utc(now)
    for key, label, fn, ok in (
        ("my", "Malaysia APIMS", fetch_malaysia, lambda r: len(r) >= 10),
        ("id", "Indonesia ISPU", fetch_indonesia, lambda r: len(r) >= 5),
        ("fires", "NASA FIRMS", fetch_fires, lambda r: bool(r.get("prov"))),
    ):
        try:
            res = fn(now)
            if ok(res):
                reg[key], reg[key + "Updated"] = res, stamp
                log(f"{label}: {len(res['prov']) if key == 'fires' else len(res)} "
                    + (f"provinces, {sum(p['n24'] for p in res['prov'])} detections in 24 h" if key == "fires" else "stations"))
            else:
                log(f"{label}: result too small, kept previous values")
        except Exception as e:
            log(f"{label} failed: {e}")
            reg.setdefault("errors", {})[key] = repr(e)[:300]
    if json.dumps(reg, sort_keys=True) == before:
        log("regional: no change")
        return
    reg["updated"] = stamp
    reg["debug"] = DEBUG
    write_json(REGIONAL, reg)
    log(f"wrote {REGIONAL}")

    # One compact snapshot per hour, kept as the open record behind the map.
    try:
        day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(HISTORY, day + ".json")
        try:
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        except (FileNotFoundError, ValueError):
            hist = []
        hour = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00Z")
        hist = [h for h in hist if h.get("t") != hour]
        hist.append({"t": hour, "sg": sg_psi or {},
                     "my": {r["id"]: r["v"] for r in reg.get("my", [])},
                     "id": {r["id"]: r["v"] for r in reg.get("id", [])},
                     "fires": {p["id"]: p["n24"] for p in (reg.get("fires") or {}).get("prov", [])}})
        write_json(path, sorted(hist, key=lambda h: h["t"]))
    except Exception as e:
        log(f"history write failed: {e}")


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

    try:
        refresh_regional(now, data.get("psi"))
    except Exception as e:
        log(f"regional step failed: {e}")

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
