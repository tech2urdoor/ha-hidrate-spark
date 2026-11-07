"""Names and defaults. Each bottle's own values live in its config entry's options."""
DOMAIN = "hidrate_spark"

CONF_SIZE_ML = "size_ml"
CONF_SYNC_MINUTES = "sync_minutes"
CONF_MANAGE_REMINDERS = "manage_reminders"
CONF_GOAL_ML = "goal_ml"
CONF_WAKE = "wake_time"
CONF_SLEEP = "sleep_time"
CONF_FREQUENCY_MIN = "glow_frequency_min"
CONF_LIGHT = "glow_light"
CONF_GLOW_ALWAYS = "glow_always"
CONF_GLOW_ON_SIP = "glow_on_sip"
CONF_GLOW_ON_GOAL = "glow_on_goal"

# Reminders stay off until the real settings are in, because the rows they write replace whatever
# the bottle is holding. Sync every 30 minutes: often enough to keep reminders honest, and half the
# app's hourly connection is about the most a coin-sized battery should be asked for. Watch its
# battery sensor for the first week.
DEFAULTS = {
    CONF_SIZE_ML: 621,
    CONF_SYNC_MINUTES: 30,
    CONF_MANAGE_REMINDERS: False,
    CONF_GOAL_ML: 2000,
    CONF_WAKE: "07:00:00",
    CONF_SLEEP: "22:00:00",
    CONF_FREQUENCY_MIN: 60,
    CONF_LIGHT: "high",
    CONF_GLOW_ALWAYS: False,
    CONF_GLOW_ON_SIP: True,
    CONF_GLOW_ON_GOAL: True,
}

RETRY_SECONDS = 300
SESSION_TIMEOUT_SECONDS = 240
