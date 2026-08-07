"""Normalize raw Garmin JSON into flat rows matching schema.sql.

Garmin's response shapes vary across firmware/library versions and some fields
are simply absent on some days. Every extractor here is defensive: it probes a
few likely key paths and returns None rather than raising. Missing != zero, so
downstream charts won't plot fabricated values.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional


# --- small helpers -----------------------------------------------------------
def _get(d: Any, *keys, default=None):
    """Return the first present, non-None value among top-level `keys`."""
    if not isinstance(d, Mapping):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def dig(d: Any, path: str, default=None):
    """Nested lookup by dotted path, e.g. dig(x, 'dailySleepDTO.sleepScores.overall.value')."""
    cur = d
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur if cur is not None else default


def _iso_secs(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _epoch_ms_to_local(v):
    """Garmin '...Local' timestamps are epoch millis expressed in local wall time.
    Render as 'YYYY-MM-DD HH:MM'. Passes through non-numeric values unchanged."""
    import datetime as _dt

    try:
        ms = int(v)
    except (TypeError, ValueError):
        return v if isinstance(v, str) else None
    return _dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


# --- daily wellness ----------------------------------------------------------
def sleep_row(date: str, raw: Any) -> Optional[dict]:
    dto = _get(raw, "dailySleepDTO", default=raw) or {}
    total = _iso_secs(dig(dto, "sleepTimeSeconds"))
    row = {
        "date": date,
        "total_sleep_s": total,
        "deep_s": _iso_secs(dig(dto, "deepSleepSeconds")),
        "light_s": _iso_secs(dig(dto, "lightSleepSeconds")),
        "rem_s": _iso_secs(dig(dto, "remSleepSeconds")),
        "awake_s": _iso_secs(dig(dto, "awakeSleepSeconds")),
        "sleep_score": dig(dto, "sleepScores.overall.value"),
        "avg_spo2": _get(raw, "averageSpO2Value", "avgSpO2"),
        "avg_resp": _get(raw, "avgRespirationValue", "averageRespirationValue"),
        "avg_hr": dig(raw, "restingHeartRate") or _get(raw, "averageHR"),
        "restless_count": dig(dto, "restlessMomentsCount"),
        "bedtime_local": _epoch_ms_to_local(_get(dto, "sleepStartTimestampLocal")),
        "wake_local": _epoch_ms_to_local(_get(dto, "sleepEndTimestampLocal")),
    }
    # A day with genuinely no sleep record: everything None → let caller mark missing.
    if row["total_sleep_s"] is None and row["sleep_score"] is None:
        return None
    return row


def hrv_row(date: str, raw: Any) -> Optional[dict]:
    s = _get(raw, "hrvSummary", default={}) or {}
    if not s:
        return None
    return {
        "date": date,
        "last_night_avg": _get(s, "lastNightAvg"),
        "last_night_5m_high": _get(s, "lastNight5MinHigh"),
        "weekly_avg": _get(s, "weeklyAvg"),
        "status": _get(s, "status"),
        "baseline_low": dig(s, "baseline.lowUpper") or dig(s, "baseline.balancedLow"),
        "baseline_high": dig(s, "baseline.balancedUpper") or dig(s, "baseline.markerValue"),
    }


def stress_row(date: str, raw: Any) -> Optional[dict]:
    avg = _get(raw, "avgStressLevel", "averageStressLevel")
    mx = _get(raw, "maxStressLevel")
    if avg is None and mx is None:
        return None
    return {
        "date": date,
        "avg_stress": avg,
        "max_stress": mx,
        "rest_min": _min(_get(raw, "restStressDuration")),
        "low_min": _min(_get(raw, "lowStressDuration")),
        "med_min": _min(_get(raw, "mediumStressDuration")),
        "high_min": _min(_get(raw, "highStressDuration")),
    }


def _min(seconds):
    return round(seconds / 60) if isinstance(seconds, (int, float)) else None


def body_battery_row(date: str, raw: Any) -> Optional[dict]:
    # get_body_battery returns a list (one entry per day). Accept list or dict.
    entry = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(entry, Mapping):
        return None
    # Garmin no longer exposes high/low/start/end directly; derive them from the
    # bodyBatteryValuesArray of [timestamp, level] pairs.
    high = _get(entry, "highestBodyBattery", "bodyBatteryHighestValue")
    low = _get(entry, "lowestBodyBattery", "bodyBatteryLowestValue")
    start_val = _get(entry, "startBodyBattery")
    end_val = _get(entry, "endBodyBattery")
    arr = _get(entry, "bodyBatteryValuesArray")
    if isinstance(arr, list) and arr:
        levels = [p[1] for p in arr if isinstance(p, (list, tuple)) and len(p) > 1 and p[1] is not None]
        if levels:
            high = high if high is not None else max(levels)
            low = low if low is not None else min(levels)
            start_val = start_val if start_val is not None else levels[0]
            end_val = end_val if end_val is not None else levels[-1]
    return {
        "date": date,
        "charged": _get(entry, "charged", "bodyBatteryChargedValue"),
        "drained": _get(entry, "drained", "bodyBatteryDrainedValue"),
        "high": high,
        "low": low,
        "start_val": start_val,
        "end_val": end_val,
    }


def rhr_row(date: str, raw: Any) -> Optional[dict]:
    # get_rhr_day: {'allMetrics': {'metricsMap': {'WELLNESS_RESTING_HEART_RATE':[{'value':..}]}}}
    rhr = dig(raw, "allMetrics.metricsMap.WELLNESS_RESTING_HEART_RATE")
    val = None
    if isinstance(rhr, list) and rhr:
        val = rhr[0].get("value")
    val = val or _get(raw, "restingHeartRate")
    if val is None:
        return None
    return {
        "date": date,
        "resting_hr": val,
        "min_hr": _get(raw, "minHeartRate"),
        "max_hr": _get(raw, "maxHeartRate"),
        "avg_hr": _get(raw, "averageHeartRate"),
    }


def readiness_row(date: str, raw: Any) -> Optional[dict]:
    entry = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(entry, Mapping):
        return None
    score = _get(entry, "score")
    if score is None:
        return None
    return {
        "date": date,
        "score": score,
        "level": _get(entry, "level"),
        "feedback": _get(entry, "feedbackShort", "feedbackLong"),
        "sleep_score": _get(entry, "sleepScore"),
        "recovery_time_h": _hours(_get(entry, "recoveryTime")),
        "hrv_factor": _get(entry, "hrvFactorPercent", "hrvWeeklyAverage"),
        "acute_load": _get(entry, "acuteLoad"),
        "stress_factor": _get(entry, "stressHistoryFactorPercent"),
    }


def _hours(minutes):
    return round(minutes / 60) if isinstance(minutes, (int, float)) else None


def training_status_row(date: str, raw: Any) -> Optional[dict]:
    # Deeply nested & device-keyed. Probe the common 'mostRecent*' paths.
    latest = dig(raw, "mostRecentTrainingStatus.latestTrainingStatusData")
    status_val = None
    load_ratio = None
    acute = None
    if isinstance(latest, Mapping) and latest:
        # keyed by deviceId; take the first device's payload
        first = next(iter(latest.values()), {})
        status_val = _get(first, "trainingStatus", "trainingStatusFeedbackPhrase")
        load_ratio = _get(first, "acuteTrainingLoadDTO") and dig(
            first, "acuteTrainingLoadDTO.dailyAcuteChronicWorkloadRatio"
        )
        acute = dig(first, "acuteTrainingLoadDTO.acwrPercent")
    vo2 = dig(raw, "mostRecentVO2Max.generic.vo2MaxValue") or dig(
        raw, "mostRecentVO2Max.vo2MaxValue"
    )
    if status_val is None and vo2 is None:
        return None
    return {
        "date": date,
        "status": status_val,
        "vo2max": vo2,
        "acute_load": acute,
        "load_ratio": load_ratio,
        "fitness_trend": _get(raw, "fitnessTrend"),
    }


def daily_stats_row(date: str, raw: Any) -> Optional[dict]:
    if not isinstance(raw, Mapping) or not raw:
        return None
    steps = _get(raw, "totalSteps", "steps")
    if steps is None and _get(raw, "totalKilocalories") is None:
        return None
    return {
        "date": date,
        "steps": steps,
        "calories": _get(raw, "totalKilocalories", "calories"),
        "distance_m": _get(raw, "totalDistanceMeters"),
        "intensity_min": (_get(raw, "moderateIntensityMinutes") or 0)
        + (_get(raw, "vigorousIntensityMinutes") or 0),
        "floors": _get(raw, "floorsAscended"),
        "resting_hr": _get(raw, "restingHeartRate"),
        "bb_max": _get(raw, "bodyBatteryHighestValue"),
    }


# --- activities --------------------------------------------------------------
def _pace_s_per_km(distance_m, duration_s):
    if distance_m and duration_s and distance_m > 0:
        return round(duration_s / (distance_m / 1000.0), 1)
    return None


def activity_row(a: Mapping) -> Optional[dict]:
    aid = _get(a, "activityId")
    if aid is None:
        return None
    start = _get(a, "startTimeLocal")
    date = start.split(" ")[0].split("T")[0] if isinstance(start, str) else None
    dist = _get(a, "distance")
    dur = _get(a, "duration", "elapsedDuration")
    atype = dig(a, "activityType.typeKey") or _get(a, "activityType")
    import json as _json

    return {
        "activity_id": aid,
        "start_time_local": start,
        "date": date,
        "type": atype,
        "name": _get(a, "activityName"),
        "distance_m": dist,
        "duration_s": dur,
        "moving_s": _get(a, "movingDuration"),
        "avg_pace_s_per_km": _pace_s_per_km(dist, _get(a, "movingDuration") or dur),
        "avg_hr": _get(a, "averageHR"),
        "max_hr": _get(a, "maxHR"),
        "avg_cadence": _get(a, "averageRunningCadenceInStepsPerMinute", "averageBikingCadenceInRevPerMinute"),
        "avg_power": _get(a, "avgPower"),
        "elev_gain_m": _get(a, "elevationGain"),
        "avg_temp": _get(a, "minTemperature"),
        "calories": _get(a, "calories"),
        "training_effect_aerobic": _get(a, "aerobicTrainingEffect"),
        "training_effect_anaerobic": _get(a, "anaerobicTrainingEffect"),
        "training_load": _get(a, "activityTrainingLoad", "trainingLoad"),
        "raw_json": _json.dumps(a)[:200000],
    }


def split_rows(activity_id: int, splits_raw: Any):
    laps = _get(splits_raw, "lapDTOs", default=splits_raw)
    if not isinstance(laps, list):
        return
    for i, lap in enumerate(laps):
        dist = _get(lap, "distance")
        dur = _get(lap, "duration")
        yield {
            "activity_id": activity_id,
            "split_idx": i,
            "distance_m": dist,
            "duration_s": dur,
            "avg_pace_s_per_km": _pace_s_per_km(dist, dur),
            "avg_hr": _get(lap, "averageHR"),
            "avg_cadence": _get(lap, "averageRunCadence"),
            "elev_gain_m": _get(lap, "elevationGain"),
        }


# --- fitness snapshot --------------------------------------------------------
def fitness_row(date, max_metrics, race_pred, fitnessage, endurance, hill) -> Optional[dict]:
    """Combine several fitness endpoints into one daily row."""
    mm = max_metrics[0] if isinstance(max_metrics, list) and max_metrics else max_metrics
    generic = dig(mm, "generic") or {}
    vo2 = _get(generic, "vo2MaxValue")
    vo2p = _get(generic, "vo2MaxPreciseValue")
    fage = _get(fitnessage, "fitnessAge") if isinstance(fitnessage, Mapping) else None
    row = {
        "date": date,
        "vo2max": vo2,
        "vo2max_precise": vo2p,
        "fitness_age": fage,
        "race_5k_s": _get(race_pred, "time5K"),
        "race_10k_s": _get(race_pred, "time10K"),
        "race_half_s": _get(race_pred, "timeHalfMarathon"),
        "race_marathon_s": _get(race_pred, "timeMarathon"),
        "endurance_score": _get(endurance, "avg")
        or dig(endurance, "enduranceScoreDTO.overallScore"),
        "hill_score": _get(hill, "overallScore") or dig(hill, "hillScoreDTO.overallScore"),
    }
    # Only skip if literally nothing came back.
    if all(v is None for k, v in row.items() if k != "date"):
        return None
    return row


# --- recovery extras merged into daily_stats --------------------------------
def recovery_fields(spo2, respiration, hydration) -> dict:
    """SpO2 / respiration / hydration fields to UPDATE onto the daily_stats row."""
    return {
        "spo2_avg": _get(spo2, "averageSpO2", "avgSleepSpO2") if isinstance(spo2, Mapping) else None,
        "resp_waking": _get(respiration, "avgWakingRespirationValue") if isinstance(respiration, Mapping) else None,
        "resp_sleep": _get(respiration, "avgSleepRespirationValue") if isinstance(respiration, Mapping) else None,
        "hydration_ml": _get(hydration, "valueInML") if isinstance(hydration, Mapping) else None,
        "hydration_goal_ml": _get(hydration, "goalInML") if isinstance(hydration, Mapping) else None,
    }


# --- activity weather --------------------------------------------------------
def _f_to_c(f):
    return round((f - 32) * 5.0 / 9.0, 1) if isinstance(f, (int, float)) else None


def weather_fields(weather_raw) -> dict:
    """Weather fields to merge onto an activity row. Garmin temps are Fahrenheit."""
    if not isinstance(weather_raw, Mapping):
        return {}
    return {
        "temp_c": _f_to_c(_get(weather_raw, "temp")),
        "feels_like_c": _f_to_c(_get(weather_raw, "apparentTemp")),
        "humidity": _get(weather_raw, "relativeHumidity"),
        "weather_desc": dig(weather_raw, "weatherTypeDTO.desc"),
    }


# --- strength sets -----------------------------------------------------------
def strength_set_rows(activity_id, exercise_sets_raw):
    sets = _get(exercise_sets_raw, "exerciseSets", default=[])
    if not isinstance(sets, list):
        return
    for i, s in enumerate(sets):
        exs = _get(s, "exercises", default=[]) or []
        ex0 = exs[0] if exs else {}
        weight_g = _get(s, "weight")
        yield {
            "activity_id": activity_id,
            "set_idx": i,
            "set_type": _get(s, "setType"),
            "exercise_category": _get(ex0, "category"),
            "exercise_name": _get(ex0, "name"),
            "reps": _get(s, "repetitionCount"),
            "weight_kg": round(weight_g / 1000.0, 2) if isinstance(weight_g, (int, float)) and weight_g else None,
            "duration_s": _get(s, "duration"),
        }


# --- training plan & workouts ------------------------------------------------
def training_plan_row(plans_raw) -> Optional[dict]:
    lst = _get(plans_raw, "trainingPlanList", default=[]) if isinstance(plans_raw, Mapping) else plans_raw
    if not isinstance(lst, list) or not lst:
        return None
    p = lst[0]  # the active plan
    return {
        "plan_id": _get(p, "trainingPlanId"),
        "name": _get(p, "name"),
        "category": _get(p, "trainingPlanCategory"),
        "sport": dig(p, "trainingType.typeKey"),
        "level": dig(p, "trainingLevel.levelKey"),
        "start_date": (_get(p, "startDate") or "").split("T")[0] or None,
        "end_date": (_get(p, "endDate") or "").split("T")[0] or None,
    }


def workout_rows(workouts_raw):
    if not isinstance(workouts_raw, list):
        return
    for w in workouts_raw:
        wid = _get(w, "workoutId")
        if wid is None:
            continue
        yield {
            "workout_id": wid,
            "name": _get(w, "workoutName"),
            "sport": dig(w, "sportType.sportTypeKey"),
            "description": _get(w, "description"),
            "est_duration_s": _get(w, "estimatedDurationInSecs"),
        }


_PLANNED_ITEM_TYPES = {"fbtAdaptiveWorkout", "workout", "trainingPlanWorkout"}


def planned_workout_rows(calendar_raw):
    items = _get(calendar_raw, "calendarItems", default=[])
    if not isinstance(items, list):
        return
    for it in items:
        # Keep only prescribed/planned items, not logged activities.
        if _get(it, "itemType") not in _PLANNED_ITEM_TYPES:
            continue
        iid = _get(it, "id")
        if iid is None:
            continue
        yield {
            "id": iid,
            "date": _get(it, "date"),
            "title": _get(it, "title"),
            "sport": _get(it, "sportTypeKey"),
            "item_type": _get(it, "itemType"),
            "training_plan_id": _get(it, "trainingPlanId"),
        }


def intraday_hr_rows(hr_raw, date, step_s=300):
    """Downsample all-day HR (heartRateValues [[ms, hr],...]) to ~step_s cadence."""
    arr = _get(hr_raw, "heartRateValues") if isinstance(hr_raw, Mapping) else None
    if not isinstance(arr, list) or not arr:
        return
    t0 = None
    last = None
    for pair in arr:
        if not (isinstance(pair, (list, tuple)) and len(pair) >= 2):
            continue
        ms, hr = pair[0], pair[1]
        if hr is None or ms is None:
            continue
        if t0 is None:
            t0 = ms
        off = int((ms - t0) / 1000)
        if last is None or off - last >= step_s:
            last = off
            yield {"date": date, "offset_s": off, "hr": int(hr)}


def ftp_fields(lactate_raw) -> dict:
    """Running power FTP from get_lactate_threshold (power block)."""
    if not isinstance(lactate_raw, Mapping):
        return {}
    power = _get(lactate_raw, "power") or {}
    return {
        "ftp_watts": _get(power, "functionalThresholdPower"),
        "ftp_w_per_kg": round(_get(power, "powerToWeight"), 2)
        if isinstance(_get(power, "powerToWeight"), (int, float)) else None,
    }


# --- personal records --------------------------------------------------------
_PR_LABELS = {
    1: "1 km", 2: "1 mile", 3: "5 km", 4: "10 km", 7: "Longest run",
    8: "Longest ride", 9: "Max avg power", 10: "Best pace",
    12: "Most steps (day)", 13: "Most steps (week)", 14: "Most steps (month)",
    15: "Most floors (day)", 16: "Longest activity",
}


def pr_rows(pr_raw):
    if not isinstance(pr_raw, list):
        return
    for p in pr_raw:
        pid = _get(p, "id")
        if pid is None:
            continue
        tid = _get(p, "typeId")
        ts = _get(p, "prStartTimeGmtFormatted", "activityStartDateTimeInGMT")
        date = ts.split("T")[0] if isinstance(ts, str) else None
        yield {
            "pr_id": pid,
            "type_id": tid,
            "label": _PR_LABELS.get(tid, f"Type {tid}"),
            "value": _get(p, "value"),
            "activity_id": _get(p, "activityId"),
            "activity_name": _get(p, "activityName"),
            "date": date,
        }


def zone_seconds(hr_zones_raw: Any) -> dict:
    """Map get_activity_hr_in_timezones output to z1_s..z5_s."""
    out = {f"z{i}_s": None for i in range(1, 6)}
    if not isinstance(hr_zones_raw, list):
        return out
    for z in hr_zones_raw:
        num = _get(z, "zoneNumber")
        secs = _get(z, "secsInZone")
        if isinstance(num, int) and 1 <= num <= 5:
            out[f"z{num}_s"] = round(secs) if isinstance(secs, (int, float)) else None
    return out


def zone_bounds(hr_zones_raw: Any) -> Optional[dict]:
    """Extract the configured zone LOW boundaries (dynamic per activity)."""
    if not isinstance(hr_zones_raw, list):
        return None
    out = {}
    for z in hr_zones_raw:
        num = _get(z, "zoneNumber")
        low = _get(z, "zoneLowBoundary")
        if isinstance(num, int) and 1 <= num <= 5 and low is not None:
            out[f"z{num}_low"] = low
    return out or None
