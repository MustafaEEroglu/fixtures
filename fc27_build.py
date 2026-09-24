#!/usr/bin/env python3
"""
FC 27 Ultimate Team takvimi:
  1) fut.gg  -> aktif SBC / Evolution / Objective bitiş saatleri  (otomatik)
  2) fc27_events.json -> promo başlangıç/bitişleri (elle bakılan liste)
  3) haftalık ritüeller (TOTW, Rivals/Champs ödülleri...)          (RRULE)
Çıktı: fc27.ics
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Alarm, Calendar, Event, vRecur

ROOT = Path(__file__).parent
CFG = json.loads((ROOT / "fc27_events.json").read_text(encoding="utf-8"))
OUT = ROOT / "fc27.ics"
STATE = ROOT / "fc27_state.json"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or CFG.get("ntfy_topic") or ""
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NEW_ALERT_KEEP_HOURS = CFG.get("new_alert_keep_hours", 48)
CATALOG: list[dict] = []  # bu çalışmada görülen tüm içerik (yeni içerik tespiti için)
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


def register(kind: str, key: str, name: str, link: str | None, end: datetime | None) -> None:
    """Yeni içerik tespiti için katalog. key sabit kalmalı; haftalık sıfırlanan içerikte bitiş tarihi key'e dahil."""
    CATALOG.append({"kind": kind, "key": f"{kind}-{key}", "name": name, "link": link, "end": end.isoformat() if end else None})


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
            name = it.get("name", "SBC")
            if not end:  # süresiz SBC: takvime bitiş düşmez ama "yeni SBC" tespitine girer
                slug0 = it.get("url") or it.get("slug") or ""
                register("sbc", str(it.get("id", name)), name, f"https://www.fut.gg{slug0}" if str(slug0).startswith("/") else None, None)
                continue
            if end < now:
                continue
            cat = (it.get("category") or {}).get("name", "")
            slug = it.get("url") or it.get("slug") or ""
            link = f"https://www.fut.gg{slug}" if slug.startswith("/") else None
            cost = it.get("cost")
            desc = f"SBC bitiş · {cat}" + (f" · ~{cost:,} coin" if isinstance(cost, int) and cost else "")
            register("sbc", str(it.get("id", name)), name, link, end)
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
            if (end and end > now) or (unlock_end and unlock_end > now):
                register("evo", str(it.get("id", name)), name, link, end or unlock_end)
            if unlock_end and unlock_end > now:
                events.append(deadline_event(f"futgg-evo-unlock-{it.get('id', name)}",
                                             f"⏳ EVO açma son gün: {name}", unlock_end, "Evolution'ı bu saate kadar AÇMAN gerekiyor", link))
            if end and end > now:
                events.append(deadline_event(f"futgg-evo-{it.get('id', name)}",
                                             f"⏳ EVO tamamlama son gün: {name}", end, "Evolution'ı bu saate kadar tamamlaman gerekiyor", link))
        print(f"[OK]   Evolutions: {len(events) - n0} bitiş")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Evolutions alınamadı: {exc}", file=sys.stderr)

    # --- Objectives: FUTGenie sayfası (SSR, geri sayım metni içerir) ---
    n0 = len(events)
    try:
        fetched_at = datetime.now(timezone.utc)
        r = requests.get("https://www.futgenie.gg/objectives", headers=UA, timeout=30)
        r.raise_for_status()
        DEBUG.mkdir(exist_ok=True)
        (DEBUG / "objectives_futgenie.html").write_text(r.text[:600000], encoding="utf-8")
        soup = BeautifulSoup(r.text, "html.parser")
        seen_ids: set[str] = set()
        cd_re = re.compile(r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m)?\s*(\d+)\s+objectives", re.I)
        for a in soup.find_all("a", href=True):
            m = re.search(r"/objectives/(\d+)/?$", a["href"])
            if not m:
                continue
            oid = m.group(1)
            text = " ".join(a.stripped_strings)
            cm = cd_re.search(text)
            noexp = re.search(r"no expiry", text, re.I)
            if not cm and not noexp:
                continue  # ödül linkleri vb.
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
            name = (a.find(string=True) or "Objective").strip() or "Objective"
            link = f"https://www.futgenie.gg/objectives/{oid}"
            catm = re.search(r"\d+\s+objectives\s*([A-Za-z][A-Za-z ]*?)(?:\s*\d+\s+rewards?|\s*$|\s*[🎁A-Z])", text)
            cat = catm.group(1).strip() if catm else ""
            if noexp or not cm or not any(cm.group(i) for i in (1, 2, 3)):
                register("obj", f"{oid}-noexp", name, link, None)
                continue
            d, h, mi = (int(cm.group(i) or 0) for i in (1, 2, 3))
            end = fetched_at + timedelta(days=d, hours=h, minutes=mi)
            end = end + timedelta(minutes=(5 - end.minute % 5) % 5)  # en yakın 5 dk'ya yuvarla
            end = end.replace(second=0, microsecond=0)
            key = f"{oid}-{end.strftime('%Y%m%d')}"  # haftalık sıfırlananlar her hafta yeni sayılır
            register("obj", key, name, link, end)
            events.append(deadline_event(f"futgenie-obj-{key}", f"⏳ Objective bitiyor: {name}", end,
                                         f"Objective bitiş · {cat}".rstrip(" ·"), link))
        print(f"[OK]   Objectives: {len(events) - n0} bitiş ({len(seen_ids)} grup)")
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
KIND_LABEL = {"sbc": "SBC", "evo": "Evolution", "obj": "Objective"}


def notify(title: str, body: str, link: str | None) -> None:
    if not NTFY_TOPIC:
        return
    try:
        headers = {"Title": title.encode("utf-8"), "Tags": "soccer", "Priority": "default"}
        if link:
            headers["Click"] = link
        requests.post(f"{NTFY_SERVER}/{NTFY_TOPIC}", data=body.encode("utf-8"), headers=headers, timeout=20).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] ntfy gönderilemedi: {exc}", file=sys.stderr)


def collect_new_content(now: datetime) -> list[Event]:
    """Katalogda ilk kez görülen içerik -> ntfy bildirimi + takvime '🆕' etkinliği (alarm başlangıçta)."""
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"seen": {}, "new": []}
    seen: dict = state.setdefault("seen", {})
    first_run = not seen
    fresh = []
    for it in CATALOG:
        if it["key"] in seen:
            continue
        seen[it["key"]] = now.isoformat()
        if first_run:
            continue  # ilk çalışmada 60 bildirim atma; sadece "görüldü" işaretle
        fresh.append(it)

    # takvim etkinliği: bir sonraki tam saat + 1 sa (abonelik yenilemesi yetişsin), alarm tam başlangıçta
    start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for it in fresh:
        label = KIND_LABEL.get(it["kind"], it["kind"])
        end_txt = ""
        if it.get("end"):
            end_txt = " · bitiş " + datetime.fromisoformat(it["end"]).astimezone(TZ).strftime("%d.%m %H:%M")
        notify(f"🆕 Yeni {label}", it["name"] + end_txt, it.get("link"))
        state["new"].append({"uid": f"fc27-new-{it['key']}", "title": f"🆕 Yeni {label}: {it['name']}",
                             "start": start.isoformat(), "link": it.get("link"), "desc": f"Yeni {label} yayında{end_txt}"})
    if fresh:
        print(f"[NEW]  {len(fresh)} yeni içerik: " + ", ".join(i["name"] for i in fresh[:8]) + (" …" if len(fresh) > 8 else ""))
    elif first_run:
        print(f"[INFO] İlk çalışma: {len(seen)} içerik görüldü olarak işaretlendi, bildirim yok.")

    # eski 'yeni' etkinliklerini düşür, kalanları takvime yaz
    keep_after = now - timedelta(hours=NEW_ALERT_KEEP_HOURS)
    state["new"] = [n for n in state["new"] if datetime.fromisoformat(n["start"]) > keep_after]
    events = []
    for n in state["new"]:
        ev = Event()
        ev.add("UID", n["uid"]); ev.add("SUMMARY", n["title"])
        st = datetime.fromisoformat(n["start"]).astimezone(TZ)
        ev.add("DTSTART", st); ev.add("DTEND", st + timedelta(minutes=15))
        ev.add("DESCRIPTION", n.get("desc", "") + (f"\n{n['link']}" if n.get("link") else ""))
        if n.get("link"):
            ev.add("URL", n["link"])
        add_alarms(ev, [timedelta(0)], n["title"])
        events.append(ev)
    # seen'i şişirme: 60 günden eski anahtarları at
    cutoff = (now - timedelta(days=60)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=0), encoding="utf-8")
    return events


def main() -> int:
    now = datetime.now(timezone.utc)
    events = collect_futgg(now) + collect_promos() + collect_rituals()
    events += collect_new_content(now)

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
