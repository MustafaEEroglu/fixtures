#!/usr/bin/env python3
"""
FC 27 Ultimate Team takvimi:
  1) fut.gg  -> aktif SBC / Evolution / Objective bitiş saatleri  (otomatik)
  2) fc27_events.json -> promo başlangıç/bitişleri (elle bakılan liste)
  3) haftalık ritüeller (TOTW, Rivals/Champs ödülleri...)          (RRULE)
Çıktı: fc27.ics
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from icalendar import Alarm, Calendar, Event, vRecur

ROOT = Path(__file__).parent
CFG = json.loads((ROOT / "fc27_events.json").read_text(encoding="utf-8"))
OUT = ROOT / "fc27.ics"
DEBUG = ROOT / "debug"
TZ = ZoneInfo(CFG.get("timezone", "Europe/Istanbul"))
UA = {"User-Agent": "Mozilla/5.0 (fixture-calendar fc27)"}
GAME = str(CFG.get("game", "27"))

DEADLINE_ALERTS = CFG.get("deadline_alerts_hours_before", [24, 3])
RITUAL_ALERTS = CFG.get("ritual_alerts_minutes_before", [0])
PROMO_ALERTS = CFG.get("promo_alerts_hours_before", [24])

# ---------------------------------------------------------------- yardımcılar
def add_alarms(ev: Event, deltas: list[timedelta], text: str):
    for d in deltas:
        a = Alarm()
        a.add("ACTION", "DISPLAY")
        a.add("DESCRIPTION", text)
        a.add("TRIGGER", -d)
        ev.add_component(a)


def parse_dt(v):
    """ISO string / epoch (s veya ms) -> aware datetime; olmazsa None."""
    if isinstance(v, (int, float)):
        if v > 1e12:
            v /= 1000
        if 1.5e9 < v < 2.5e9:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return None
    if isinstance(v, str) and len(v) >= 10:
        s = v.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def find_dt(obj, key_regex: str, exclude: str = "^$"):
    """Sözlük ağacında adı regex'e uyan ilk tarih alanını bul."""
    pat, exc = re.compile(key_regex, re.I), re.compile(exclude, re.I)
    if isinstance(obj, dict):
        for k, v in obj.items():
            if pat.search(k) and not exc.search(k):
                dt = parse_dt(v)
                if dt:
                    return dt
        for v in obj.values():
            r = find_dt(v, key_regex, exclude)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_dt(v, key_regex, exclude)
            if r:
                return r
    return None


def get_json_pages(url: str, label: str):
    """fut.gg sayfalı API: {"data":[...], "next": 2|null}"""
    items, page = [], 1
    while page and page < 30:
        r = requests.get(f"{url}?page={page}", headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
        chunk = data.get("data", data if isinstance(data, list) else [])
        items.extend(chunk)
        page = data.get("next") if isinstance(data, dict) else None
    DEBUG.mkdir(exist_ok=True)
    (DEBUG / f"{label}.json").write_text(json.dumps(items[:50], indent=1, default=str), encoding="utf-8")
    return items


def get_ssr_blocks(url: str, label: str, marker: str):
    """API yoksa: sayfa içindeki SSR veri bloklarını kaba regex ile ayır."""
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    html = r.text
    DEBUG.mkdir(exist_ok=True)
    (DEBUG / f"{label}.html").write_text(html[:400000], encoding="utf-8")
    starts = [m.start() for m in re.finditer(marker, html)]
    return [html[s:(starts[i + 1] if i + 1 < len(starts) else s + 20000)] for i, s in enumerate(starts)]


def deadline_event(uid: str, title: str, when: datetime, desc: str, url: str | None = None) -> Event:
    ev = Event()
    ev.add("UID", uid)
    ev.add("SUMMARY", title)
    ev.add("DTSTART", when.astimezone(TZ))
    ev.add("DTEND", (when + timedelta(minutes=30)).astimezone(TZ))
    ev.add("DESCRIPTION", desc + (f"\n{url}" if url else ""))
    if url:
        ev.add("URL", url)
    add_alarms(ev, [timedelta(hours=h) for h in DEADLINE_ALERTS], title)
    return ev


# ---------------------------------------------------------------- 1) fut.gg
def collect_futgg(now: datetime) -> list[Event]:
    events: list[Event] = []

    # --- SBC (JSON API, futcli'nin kullandığı uç) ---
    try:
        for it in get_json_pages("https://www.fut.gg/api/fut/sbc/", "sbc"):
            end = find_dt(it, r"(expir|end|deadline)", exclude=r"(start|repeat|refresh)")
            if not end or end < now:
                continue
            name = it.get("name", "SBC")
            cat = (it.get("category") or {}).get("name", "")
            slug = it.get("url") or it.get("slug") or ""
            link = f"https://www.fut.gg{slug}" if slug.startswith("/") else None
            cost = it.get("cost")
            desc = f"SBC bitiş · {cat}" + (f" · ~{cost:,} coin" if isinstance(cost, int) and cost else "")
            events.append(deadline_event(f"futgg-sbc-{it.get('id', name)}", f"⏳ SBC bitiyor: {name}", end, desc, link))
        print(f"[OK]   SBC: {sum(1 for e in events)} bitiş")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] SBC alınamadı: {exc}", file=sys.stderr)

    # --- Evolutions: önce API dene, yoksa SSR ---
    n0 = len(events)
    try:
        try:
            items = get_json_pages("https://www.fut.gg/api/fut/evolutions/", "evolutions")
        except Exception:  # noqa: BLE001
            items = []
            for b in get_ssr_blocks("https://www.fut.gg/evolutions/", "evolutions",
                                    rf'\{{id:\d+,game:"{GAME}",eaId:\d+,url:"/evolutions/'):
                item = {"raw": True}
                m = re.search(r'name:"([^"]+)"', b); item["name"] = m.group(1) if m else "Evolution"
                m = re.search(r"\bid:(\d+)", b); item["id"] = m.group(1) if m else item["name"]
                m = re.search(r'url:"([^"]+)"', b); item["url"] = m.group(1) if m else ""
                for key in re.findall(r'(\w*(?:[Ee]nd|[Ee]xpir|[Dd]eadline)\w*):"([^"]+)"', b):
                    item[key[0]] = key[1]
                items.append(item)
        for it in items:
            name = it.get("name", "Evolution")
            slug = it.get("url") or ""
            link = f"https://www.fut.gg{slug}" if str(slug).startswith("/") else None
            unlock_end = find_dt(it, r"unlock.*(end|expir)|(end|expir).*unlock")
            end = find_dt(it, r"(expir|end|deadline)", exclude=r"(start|unlock|repeat|refresh)")
            if unlock_end and unlock_end > now:
                events.append(deadline_event(f"futgg-evo-unlock-{it.get('id', name)}",
                                             f"⏳ EVO açma son gün: {name}", unlock_end, "Evolution'ı bu saate kadar AÇMAN gerekiyor", link))
            if end and end > now:
                events.append(deadline_event(f"futgg-evo-{it.get('id', name)}",
                                             f"⏳ EVO tamamlama son gün: {name}", end, "Evolution'ı bu saate kadar tamamlaman gerekiyor", link))
        print(f"[OK]   Evolutions: {len(events) - n0} bitiş")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Evolutions alınamadı: {exc}", file=sys.stderr)

    # --- Objectives ---
    n0 = len(events)
    try:
        try:
            items = get_json_pages("https://www.fut.gg/api/fut/objectives/", "objectives")
        except Exception:  # noqa: BLE001
            items = []
            for b in get_ssr_blocks("https://www.fut.gg/objectives/", "objectives",
                                    rf'\{{id:\d+,game:"{GAME}",eaId:\d+,url:"/objectives/'):
                item = {}
                m = re.search(r'name:"([^"]+)"', b); item["name"] = m.group(1) if m else "Objective"
                m = re.search(r"\bid:(\d+)", b); item["id"] = m.group(1) if m else item["name"]
                m = re.search(r'url:"([^"]+)"', b); item["url"] = m.group(1) if m else ""
                for key in re.findall(r'(\w*(?:[Ee]nd|[Ee]xpir|[Dd]eadline)\w*):"([^"]+)"', b):
                    item[key[0]] = key[1]
                items.append(item)
        for it in items:
            end = find_dt(it, r"(expir|end|deadline)", exclude=r"(start|repeat|refresh)")
            if not end or end < now:
                continue
            name = it.get("name", "Objective")
            slug = it.get("url") or ""
            link = f"https://www.fut.gg{slug}" if str(slug).startswith("/") else None
            events.append(deadline_event(f"futgg-obj-{it.get('id', name)}", f"⏳ Objective bitiyor: {name}", end, "Objective bitiş", link))
        print(f"[OK]   Objectives: {len(events) - n0} bitiş")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Objectives alınamadı: {exc}", file=sys.stderr)

    return events


# ---------------------------------------------------------------- 2) promolar
def collect_promos() -> list[Event]:
    events = []
    for p in CFG.get("promos", []):
        start = datetime.fromisoformat(p["start"]).replace(tzinfo=TZ) if "T" in p["start"] else None
        tag = " (tahmini)" if p.get("estimated") else ""
        for kind, field, emoji in (("başlıyor", "start", "🚀"), ("bitiyor", "end", "🏁")):
            if not p.get(field):
                continue
            ev = Event()
            ev.add("UID", f"fc27-promo-{p['id']}-{field}")
            ev.add("SUMMARY", f"{emoji} {p['name']} {kind}{tag}")
            if "T" in p[field]:
                dt = datetime.fromisoformat(p[field]).replace(tzinfo=TZ)
                ev.add("DTSTART", dt); ev.add("DTEND", dt + timedelta(hours=1))
            else:  # tüm gün
                d = datetime.fromisoformat(p[field]).date()
                ev.add("DTSTART", d); ev.add("DTEND", d + timedelta(days=1))
            ev.add("DESCRIPTION", p.get("note", "") + ("\nTarih tahmini; duyuru gelince fc27_events.json'ı güncelle." if p.get("estimated") else ""))
            add_alarms(ev, [timedelta(hours=h) for h in PROMO_ALERTS], f"{p['name']} {kind}")
            events.append(ev)
    print(f"[OK]   Promo: {len(events)} etkinlik")
    return events


# ---------------------------------------------------------------- 3) ritüeller
def collect_rituals() -> list[Event]:
    events = []
    season_start = datetime.fromisoformat(CFG["season_start"]).replace(tzinfo=TZ)
    season_end = datetime.fromisoformat(CFG["season_end"]).replace(tzinfo=TZ)
    for r in CFG.get("rituals", []):
        hh, mm = map(int, r["time"].split(":"))
        first = season_start.replace(hour=hh, minute=mm)
        while first.strftime("%a").upper()[:2] != r["day"]:
            first += timedelta(days=1)
        ev = Event()
        ev.add("UID", f"fc27-ritual-{r['id']}")
        ev.add("SUMMARY", r["name"])
        ev.add("DTSTART", first); ev.add("DTEND", first + timedelta(minutes=30))
        ev.add("RRULE", vRecur(FREQ="WEEKLY", BYDAY=r["day"], UNTIL=season_end.astimezone(timezone.utc)))
        ev.add("DESCRIPTION", r.get("note", ""))
        add_alarms(ev, [timedelta(minutes=m) for m in RITUAL_ALERTS], r["name"])
        events.append(ev)
    print(f"[OK]   Ritüel: {len(events)} haftalık kural")
    return events


# ---------------------------------------------------------------- main
def main() -> int:
    now = datetime.now(timezone.utc)
    events = collect_futgg(now) + collect_promos() + collect_rituals()

    cal = Calendar()
    cal.add("PRODID", "-//fixture-calendar//fc27//TR")
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("METHOD", "PUBLISH")
    cal.add("X-WR-CALNAME", CFG.get("calendar_name", "FC 27 Ultimate Team"))
    cal.add("X-WR-TIMEZONE", str(TZ))
    cal.add("REFRESH-INTERVAL;VALUE=DURATION", "PT3H")
    cal.add("X-PUBLISHED-TTL", "PT3H")
    for ev in events:
        cal.add_component(ev)
    OUT.write_bytes(cal.to_ical())
    print(f"Yazıldı: {OUT.name} ({len(events)} etkinlik)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
