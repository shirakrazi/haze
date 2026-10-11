import json, urllib.request, time
UA = "Mozilla/5.0 (compatible; haze-page-updater/1.0)"
URLS = {
 "apims": "https://eqms.doe.gov.my/api3/publicmapproxy/PUBLIC_DISPLAY/CAQM_MCAQM_Current_Reading/MapServer/0/query?f=json&where=1%3D1&outFields=STATION_ID,STATION_LOCATION,STATE_NAME,API,CLASS,DATETIME,LONGITUDE,LATITUDE,PARAM_SELECTED,PM25_CONC&returnGeometry=false",
 "openmeteo_aq": "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=-2.21,-2.99,-0.03&longitude=113.92,104.75,109.34&current=pm2_5,pm10,us_aqi&timezone=Asia%2FSingapore",
 "openmeteo_wind": "https://api.open-meteo.com/v1/forecast?latitude=1.35,-3.5&longitude=103.82,105.3&current=wind_speed_10m,wind_direction_10m&wind_speed_unit=kmh&timezone=Asia%2FSingapore",
 "firms_n20_24h": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_SouthEast_Asia_24h.csv",
 "firms_n20_7d": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_SouthEast_Asia_7d.csv",
 "firms_n21_24h": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_SouthEast_Asia_24h.csv",
 "ispu_menlhk": "https://ispu.menlhk.go.id/apimobile/v1/getStations",
 "ispu_kemenlh": "https://ispu.kemenlh.go.id/apimobile/v1/getStations",
 "bmkg_pm25": "https://www.bmkg.go.id/kualitas-udara/informasi-partikulat-pm25.bmkg",
 "nea_psi": "https://api-open.data.gov.sg/v2/real-time/api/psi",
}
out = {}
for k, u in URLS.items():
    t = time.time()
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read()
        txt = b.decode("utf-8", "replace")
        rec = {"ok": True, "status": r.status, "bytes": len(b), "head": txt[:700], "secs": round(time.time() - t, 1)}
        if u.endswith(".csv"):
            lines = txt.strip().split("\n")
            dates = sorted({l.split(",")[5] for l in lines[1:] if l.count(",") > 6})
            sea = [l for l in lines[1:] if l.count(",") > 6 and 93 <= float(l.split(",")[1]) <= 121 and -9.5 <= float(l.split(",")[0]) <= 9]
            rec.update(rows=len(lines) - 1, dates=dates, rows_in_map=len(sea), tail=lines[-1])
        if k == "apims":
            j = json.loads(txt); f = j.get("features", [])
            rec.update(n=len(f), sample=[x["attributes"] for x in f[:3]], exceeded=j.get("exceededTransferLimit"))
        out[k] = rec
    except Exception as e:
        out[k] = {"ok": False, "error": repr(e)[:300], "secs": round(time.time() - t, 1)}
json.dump(out, open("probe.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps({k: (v.get("status"), v.get("bytes"), v.get("error")) for k, v in out.items()}, indent=1))
