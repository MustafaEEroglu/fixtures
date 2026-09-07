#!/usr/bin/env python3
"""
footcal feed'lerini birlestirir, her maca uyari (VALARM) ekler ve tek fixtures.ics uretir.
Ayarlar: teams.json
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

import requests
from icalendar import Alarm, Calendar, Event

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "teams.json").read_text(encoding="utf-8"))
OUTPUT = ROOT / "fixtures.ics"
BASE = "https://footcal.cbdm.app"


def fetch(kind: str, fid: str) -> Calendar:
    url = f"{BASE}/{kind}/{fid}/calendar.ics"
    r = requests.get(url, timeout=30, headers={"User-Agent": "fixture-calendar/1.0"})
    r.raise_for_status()
    return Calendar.from_ical(r.content)


def with_alarms(event: Event, minutes_before: list[int]) -> Event:
    # Kaynakta alarm varsa temizle, bizimkileri ekle
    for sub in list(event.subcomponents):
        if sub.name == "VALARM":
            event.subcomponents.remove(sub)
    summary = str(event.get("SUMMARY", "Maç"))
    for m in minutes_before:
        alarm = Alarm()
        alarm.add("ACTION", "DISPLAY")
        alarm.add("DESCRIPTION", f"{summary} — {m} dk sonra başlıyor" if m else f"{summary} başlıyor")
        alarm.add("TRIGGER", timedelta(minutes=-m))
        event.add_component(alarm)
    return event


def main() -> int:
    alerts = CONFIG.get("alerts_minutes_before", [60, 15])
    sources = [("team", str(t)) for t in CONFIG.get("teams", [])]
    sources += [("comp", str(c)) for c in CONFIG.get("competitions", [])]
    if not sources:
        print("teams.json içinde teams/competitions boş", file=sys.stderr)
        return 1

    events: dict[str, Event] = {}
    failures = 0
    for kind, fid in sources:
        try:
            cal = fetch(kind, fid)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[WARN] {kind}/{fid} alınamadı: {exc}", file=sys.stderr)
            continue
        count = 0
        for ev in cal.walk("VEVENT"):
            uid = str(ev.get("UID") or f"{kind}-{fid}-{ev.get('DTSTART').dt.isoformat()}")
            events[uid] = with_alarms(ev, alerts)  # ayni UID (derbi) -> tek etkinlik
            count += 1
        print(f"[OK]   {kind}/{fid}: {count} etkinlik")

    if failures == len(sources):
        print("Hiçbir kaynak alınamadı; mevcut fixtures.ics korunuyor.", file=sys.stderr)
        return 2

    out = Calendar()
    out.add("PRODID", "-//fixture-calendar//footcal merge//TR")
    out.add("VERSION", "2.0")
    out.add("CALSCALE", "GREGORIAN")
    out.add("METHOD", "PUBLISH")
    out.add("X-WR-CALNAME", CONFIG.get("calendar_name", "Maç Fikstürü"))
    out.add("X-WR-TIMEZONE", CONFIG.get("timezone", "Europe/Istanbul"))
    out.add("REFRESH-INTERVAL;VALUE=DURATION", "PT6H")
    out.add("X-PUBLISHED-TTL", "PT6H")
    for ev in sorted(events.values(), key=lambda e: e.get("DTSTART").dt.isoformat()):
        out.add_component(ev)

    OUTPUT.write_bytes(out.to_ical())
    print(f"Yazıldı: {OUTPUT.name} ({len(events)} etkinlik, uyarılar: {alerts} dk)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
