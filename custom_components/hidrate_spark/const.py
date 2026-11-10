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
CONF_PHONE_RELAY = "phone_relay"
# Kept in the entry's data, not its options: saving the Configure screen replaces every option.
CONF_WEBHOOK_ID = "webhook_id"

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
    CONF_PHONE_RELAY: False,
}

# Lowest, highest and step, shared by the Configure screen and the number entities.
GOAL_RANGE = (100, 10000, 50)
FREQUENCY_RANGE = (0, 720, 5)

RETRY_SECONDS = 300
# On its charger a connection costs the bottle nothing, so it's checked this often, and lifting it off
# shows within minutes. Never slower than the bottle's own setting.
CHARGER_SYNC_SECONDS = 300
# A full bottle on its charger can stop charging for a minute or so and then top up again; keep syncing often through that.
CHARGER_GRACE_SECONDS = 900
SESSION_TIMEOUT_SECONDS = 240
# How often to look for a due sync, and how recently the bottle must have been heard to count as in
# range. The one bottle measured so far advertises about every 2 seconds, so 120 is plenty.
DUE_CHECK_SECONDS = 60
HEARD_WITHIN_SECONDS = 120
