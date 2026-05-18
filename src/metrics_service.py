from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local_date(dt: datetime, tz: ZoneInfo) -> date:
    return _to_utc(dt).astimezone(tz).date()


def to_local_hour(dt: datetime, tz: ZoneInfo) -> int:
    return _to_utc(dt).astimezone(tz).hour


def distinct_sorted_dates(submitted_ats: list[datetime], tz: ZoneInfo) -> list[date]:
    dates = {to_local_date(d, tz) for d in submitted_ats}
    return sorted(dates)


def streak_ending_on_last_submission(sorted_dates: list[date]) -> int:
    """Dias consecutivos com daily, contando a partir do último dia em que a pessoa enviou."""
    if not sorted_dates:
        return 0
    streak = 1
    expected = sorted_dates[-1] - timedelta(days=1)
    for d in reversed(sorted_dates[:-1]):
        if d == expected:
            streak += 1
            expected -= timedelta(days=1)
        elif d < expected:
            break
    return streak


def longest_streak_ever(sorted_dates: list[date]) -> int:
    if not sorted_dates:
        return 0
    best = 1
    current = 1
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def streak_including_today(sorted_dates: list[date], today_local: date) -> int:
    """Sequência que precisa incluir o dia de hoje (0 se não mandou hoje)."""
    if not sorted_dates or sorted_dates[-1] != today_local:
        return 0
    streak = 1
    expected = today_local - timedelta(days=1)
    for d in reversed(sorted_dates[:-1]):
        if d == expected:
            streak += 1
            expected -= timedelta(days=1)
        elif d < expected:
            break
    return streak


def hour_stats(submitted_ats: list[datetime], tz: ZoneInfo) -> tuple[str, list[tuple[int, int]]]:
    """Retorna (resumo em texto, top horas (hora, count))."""
    if not submitted_ats:
        return ("Sem dados.", [])
    hours = [to_local_hour(d, tz) for d in submitted_ats]
    ctr = Counter(hours)
    most_common = ctr.most_common(3)
    mode_hour, mode_count = most_common[0]
    total = len(hours)
    avg_block = sum(h * c for h, c in ctr.items()) / total
    avg_h = int(avg_block)
    avg_m = int((avg_block - avg_h) * 60)
    summary = (
        f"**Horário mais comum:** {mode_hour:02d}h–{mode_hour:02d}h59 ({mode_count}×)\n"
        f"**Média ponderada:** ~{avg_h:02d}:{avg_m:02d} ({tz.key})"
    )
    return summary, most_common
