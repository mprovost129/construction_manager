from calendar import Calendar
from datetime import date


def parse_month(value):
    value = value.strip()
    if len(value) != 7:
        return None
    try:
        month = date.fromisoformat(f'{value}-01')
    except ValueError:
        return None
    return month if month.strftime('%Y-%m') == value else None


def shift_month(month, offset):
    month_index = month.year * 12 + month.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    try:
        return date(year, zero_based_month + 1, 1)
    except ValueError:
        return month


def choose_calendar_month(milestones, requested_month, *, today):
    requested = parse_month(requested_month)
    if requested:
        return requested
    if not milestones:
        return today.replace(day=1)

    active = [
        milestone
        for milestone in milestones
        if milestone.start_date <= today <= milestone.effective_end_date
    ]
    if active:
        return today.replace(day=1)

    upcoming = [
        milestone for milestone in milestones if milestone.start_date > today
    ]
    if upcoming:
        return min(upcoming, key=lambda milestone: milestone.start_date).start_date.replace(
            day=1
        )

    latest = max(milestones, key=lambda milestone: milestone.effective_end_date)
    return latest.effective_end_date.replace(day=1)


def build_month_calendar(milestones, month, *, today):
    weeks = []
    for week_dates in Calendar(firstweekday=6).monthdatescalendar(
        month.year,
        month.month,
    ):
        week = []
        for day in week_dates:
            events = [
                {
                    'milestone': milestone,
                    'is_start': day == milestone.start_date,
                    'is_end': day == milestone.effective_end_date,
                }
                for milestone in milestones
                if milestone.start_date <= day <= milestone.effective_end_date
            ]
            week.append(
                {
                    'date': day,
                    'in_month': day.month == month.month,
                    'is_today': day == today,
                    'events': events,
                }
            )
        weeks.append(week)
    return weeks
