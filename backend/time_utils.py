"""
Time utility functions - core logic

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from datetime import datetime
from typing import Optional
import pytz


def get_current_time(timezone: Optional[str] = None) -> dict:
    """
    Get the current time
    
    Args:
        timezone: Timezone name, e.g. "Asia/Shanghai", "America/New_York"
    
    Returns:
        A dictionary containing time information
    """
    try:
        if timezone:
            tz = pytz.timezone(timezone)
            current_time = datetime.now(tz)
        else:
            tz = pytz.UTC
            timezone = "UTC"
            current_time = datetime.now(tz)
        
        return {
            "success": True,
            "timezone": timezone,
            "datetime": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            "date": current_time.strftime("%Y-%m-%d"),
            "time": current_time.strftime("%H:%M:%S"),
            "timezone_abbr": current_time.strftime("%Z"),
            "timestamp": int(current_time.timestamp()),
            "message": f"Current time ({timezone}): {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        }
    except pytz.exceptions.UnknownTimeZoneError:
        return {
            "success": False,
            "error": f"Unknown timezone '{timezone}'",
            "message": f"Error: Unknown timezone '{timezone}'. Please use a valid timezone name such as 'Asia/Shanghai', 'America/New_York', etc."
        }


def convert_timezone(time_str: str, from_timezone: str, to_timezone: str) -> dict:
    """
    Convert time from one timezone to another
    
    Args:
        time_str: Time string in "YYYY-MM-DD HH:MM:SS" format
        from_timezone: Source timezone
        to_timezone: Target timezone
    
    Returns:
        A dictionary containing the conversion result
    """
    try:
        # Parse the time string
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        
        # Set source timezone
        from_tz = pytz.timezone(from_timezone)
        dt_with_tz = from_tz.localize(dt)
        
        # Convert to target timezone
        to_tz = pytz.timezone(to_timezone)
        converted_dt = dt_with_tz.astimezone(to_tz)
        
        return {
            "success": True,
            "original": {
                "datetime": time_str,
                "timezone": from_timezone
            },
            "converted": {
                "datetime": converted_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": to_timezone,
                "timezone_abbr": converted_dt.strftime("%Z")
            },
            "message": (
                f"Timezone conversion result:\n"
                f"  Original: {time_str} ({from_timezone})\n"
                f"  Converted: {converted_dt.strftime('%Y-%m-%d %H:%M:%S')} ({to_timezone})"
            )
        }
    except ValueError:
        return {
            "success": False,
            "error": "Invalid time format",
            "message": "Error: Invalid time format. Please use 'YYYY-MM-DD HH:MM:SS' format, e.g. '2025-12-13 14:30:00'"
        }
    except pytz.exceptions.UnknownTimeZoneError as e:
        return {
            "success": False,
            "error": f"Unknown timezone: {str(e)}",
            "message": "Error: Unknown timezone. Please verify the timezone name. Common timezones: Asia/Shanghai, America/New_York, Europe/London, Asia/Tokyo"
        }


def list_timezones(region: Optional[str] = None) -> dict:
    """
    List available timezones
    
    Args:
        region: Region filter, e.g. "Asia", "America", "Europe"
    
    Returns:
        A dictionary containing the timezone list
    """
    common_timezones = {
        "Asia/Shanghai": "China - Shanghai",
        "Asia/Hong_Kong": "China - Hong Kong",
        "Asia/Taipei": "China - Taipei",
        "Asia/Tokyo": "Japan - Tokyo",
        "Asia/Seoul": "South Korea - Seoul",
        "Asia/Singapore": "Singapore",
        "America/New_York": "USA - New York",
        "America/Los_Angeles": "USA - Los Angeles",
        "America/Chicago": "USA - Chicago",
        "Europe/London": "UK - London",
        "Europe/Paris": "France - Paris",
        "Europe/Berlin": "Germany - Berlin",
        "Australia/Sydney": "Australia - Sydney",
        "Pacific/Auckland": "New Zealand - Auckland",
        "UTC": "Coordinated Universal Time",
    }
    
    if region:
        timezones = [tz for tz in pytz.all_timezones if tz.startswith(region)]
        if not timezones:
            return {
                "success": False,
                "error": f"No timezones found starting with '{region}'",
                "message": f"No timezones found starting with '{region}'. Try: Asia, America, Europe, Africa, Australia, Pacific"
            }
        return {
            "success": True,
            "region": region,
            "timezones": timezones[:50],
            "total": len(timezones),
            "message": f"Timezones in {region}:\n" + "\n".join(f"  - {tz}" for tz in timezones[:30])
        }
    else:
        return {
            "success": True,
            "timezones": common_timezones,
            "message": "Common timezones:\n" + "\n".join(f"  - {tz} ({name})" for tz, name in common_timezones.items())
        }

