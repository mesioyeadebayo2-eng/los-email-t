"""
Natural language datetime parser for MailPilot snooze and reminder functionality.
Built with Python standard library (re, datetime) for robustness across platforms.
"""

import re
from datetime import datetime, timedelta

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6
}

def parse_snooze_time(text: str, base_time: datetime = None) -> datetime | None:
    """
    Parse a natural language time expression into a future datetime.
    Examples:
      - "in 2 hours", "in 30 minutes", "in 1 day"
      - "tomorrow 9am", "tomorrow at 9am", "tomorrow 5pm"
      - "next Monday", "next Monday 9am", "next Friday at 3pm"
      - "today at 5pm", "tonight at 8pm", "tonight"
    """
    if not text:
        return None

    if base_time is None:
        base_time = datetime.now()

    clean_text = text.strip().lower()

    # Strip prefixes like "remind me about this", "remind me", "snooze for", "snooze until", "in"
    clean_text = re.sub(r'^(?:remind\s+me(?:\s+about\s+(?:this|#?\w+))?\s*(?:in|at|until|on)?|snooze(?:\s+#?\w+)?\s*(?:for|until|to|at)?)\s*', '', clean_text)
    clean_text = clean_text.strip()

    # 1. Relative offset: "in X hours/minutes/days" or "X hours", "2h", "30m"
    rel_match = re.search(r'^(?:in\s+)?(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|days?|d)$', clean_text)
    if rel_match:
        val = float(rel_match.group(1))
        unit = rel_match.group(2)
        if unit.startswith('h'):
            return base_time + timedelta(hours=val)
        elif unit.startswith('m'):
            return base_time + timedelta(minutes=val)
        elif unit.startswith('d'):
            return base_time + timedelta(days=val)

    # Helper to parse time like "9am", "9:30am", "5pm", "17:00", "9"
    def parse_time_of_day(time_str: str) -> tuple[int, int] | None:
        time_str = time_str.strip().lower()
        tm = re.search(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$', time_str)
        if not tm:
            return None
        hour = int(tm.group(1))
        minute = int(tm.group(2)) if tm.group(2) else 0
        ampm = tm.group(3)
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        return hour, minute

    # 2. "tomorrow [at] [9am|9:00|5pm]"
    tom_match = re.search(r'^tomorrow(?:\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?$', clean_text)
    if tom_match:
        time_part = tom_match.group(1)
        target_day = base_time + timedelta(days=1)
        if time_part:
            tod = parse_time_of_day(time_part)
            if tod:
                return target_day.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0)
        # Default to 9:00 AM tomorrow if time not specified
        return target_day.replace(hour=9, minute=0, second=0, microsecond=0)

    # 3. "tonight [at] [8pm]" or "today at [5pm]"
    tonight_match = re.search(r'^(?:tonight|today)(?:\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?$', clean_text)
    if tonight_match:
        time_part = tonight_match.group(1)
        if time_part:
            tod = parse_time_of_day(time_part)
            if tod:
                target = base_time.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0)
                if target <= base_time:
                    target += timedelta(days=1)
                return target
        # Default "tonight" to 20:00 (8 PM)
        target = base_time.replace(hour=20, minute=0, second=0, microsecond=0)
        if target <= base_time:
            target += timedelta(days=1)
        return target

    # 4. "next <weekday> [at] [9am]" or "<weekday> [at] [9am]"
    weekday_match = re.search(r'^(?:next\s+)?([a-z]+)(?:\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?$', clean_text)
    if weekday_match:
        w_name = weekday_match.group(1)
        if w_name in WEEKDAYS:
            target_weekday = WEEKDAYS[w_name]
            current_weekday = base_time.weekday()
            days_ahead = (target_weekday - current_weekday) % 7
            if days_ahead == 0:
                days_ahead = 7  # "next Monday" on a Monday means 7 days later
            target_date = base_time + timedelta(days=days_ahead)
            time_part = weekday_match.group(2)
            if time_part:
                tod = parse_time_of_day(time_part)
                if tod:
                    return target_date.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0)
            return target_date.replace(hour=9, minute=0, second=0, microsecond=0)

    # 5. Direct time of day today/tomorrow: "at 9am", "9am", "5pm"
    time_only_match = re.search(r'^(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm))$', clean_text)
    if time_only_match:
        tod = parse_time_of_day(time_only_match.group(1))
        if tod:
            target = base_time.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0)
            if target <= base_time:
                target += timedelta(days=1)
            return target

    # 6. Fallback: ISO format or dateutil if available
    try:
        from dateutil import parser as du_parser
        return du_parser.parse(text, default=base_time)
    except Exception:
        pass

    try:
        return datetime.fromisoformat(text)
    except Exception:
        pass

    return None
