"""Calendar data loaded at startup. Verify against the NSE, BSE and RBI circulars each year."""

from datetime import date, time

from coolcandle.domain.events import MarketEvent

# NSE Futures & Options trading holidays, copied from the exchange's circulars. BSE closes on the
# same days. Days on a weekend are listed too, so the calendar can show why. Startup makes the
# database match this list exactly, so a correction here also removes a wrong date.
#   2025: NSE/FAOP/65588 (13 Dec 2024)
#   2026: NSE/FAOP/71777 (12 Dec 2025), plus NSE/FAOP/72262 (12 Jan 2026) for 15 January
#   2027: add from NSE's circular when it is published, usually in December. Never guess a date.
HOLIDAYS: tuple[tuple[date, str], ...] = (
    (date(2025, 1, 26), "Republic Day"),
    (date(2025, 2, 26), "Mahashivratri"),
    (date(2025, 3, 14), "Holi"),
    (date(2025, 3, 31), "Id-Ul-Fitr (Ramadan Eid)"),
    (date(2025, 4, 6), "Shri Ram Navami"),
    (date(2025, 4, 10), "Shri Mahavir Jayanti"),
    (date(2025, 4, 14), "Dr. Baba Saheb Ambedkar Jayanti"),
    (date(2025, 4, 18), "Good Friday"),
    (date(2025, 5, 1), "Maharashtra Day"),
    (date(2025, 6, 7), "Bakri Id"),
    (date(2025, 7, 6), "Muharram"),
    (date(2025, 8, 15), "Independence Day"),
    (date(2025, 8, 27), "Ganesh Chaturthi"),
    (date(2025, 10, 2), "Mahatma Gandhi Jayanti/Dussehra"),
    (date(2025, 10, 21), "Diwali Laxmi Pujan (Muhurat trading)"),
    (date(2025, 10, 22), "Diwali-Balipratipada"),
    (date(2025, 11, 5), "Prakash Gurpurb Sri Guru Nanak Dev"),
    (date(2025, 12, 25), "Christmas"),
    (date(2026, 1, 15), "Municipal Corporation Election in Maharashtra"),
    (date(2026, 1, 26), "Republic Day"),
    (date(2026, 2, 15), "Mahashivratri"),
    (date(2026, 3, 3), "Holi"),
    (date(2026, 3, 21), "Id-Ul-Fitr (Ramadan Eid)"),
    (date(2026, 3, 26), "Shri Ram Navami"),
    (date(2026, 3, 31), "Shri Mahavir Jayanti"),
    (date(2026, 4, 3), "Good Friday"),
    (date(2026, 4, 14), "Dr. Baba Saheb Ambedkar Jayanti"),
    (date(2026, 5, 1), "Maharashtra Day"),
    (date(2026, 5, 28), "Bakri Id"),
    (date(2026, 6, 26), "Muharram"),
    (date(2026, 8, 15), "Independence Day"),
    (date(2026, 9, 14), "Ganesh Chaturthi"),
    (date(2026, 10, 2), "Mahatma Gandhi Jayanti"),
    (date(2026, 10, 20), "Dussehra"),
    (date(2026, 11, 8), "Diwali Laxmi Pujan (Muhurat trading)"),
    (date(2026, 11, 10), "Diwali-Balipratipada"),
    (date(2026, 11, 24), "Prakash Gurpurb Sri Guru Nanak Dev"),
    (date(2026, 12, 25), "Christmas"),
)

EVENTS: tuple[MarketEvent, ...] = (
    MarketEvent(day=date(2026, 12, 4), name="RBI policy decision", at=time(10, 0)),
    MarketEvent(day=date(2027, 2, 5), name="RBI policy decision", at=time(10, 0)),
)
