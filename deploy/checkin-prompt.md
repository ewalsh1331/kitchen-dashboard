It's Eric's 8 PM evening check-in. Using only read-only tools, gather:

1. **Workout today?** Run `tools/workout.py pull`, then `tools/workout.py show "Workout Log" --last 10`. Was anything logged for today's date? If not, also check today's calendar for a workout event.
2. **Tomorrow's schedule:** `tools/gcal.py list --start "<tomorrow> 00:00" --end "<tomorrow> 23:59"`. Note the first non-routine event and anything notable (golf, appointments). Treat recurring "Work (...)" blocks as routine: mention only whether it's in-office or remote.
3. **Tasks due tomorrow:** the purple 12:00 task events from that same list.
4. **Recovery:** `tools/fitness.py read Oura --last 7` and `tools/fitness.py read Garmin --last 7`. One short observation on the sleep/HRV trend, labeling Oura and Garmin separately. Context only, not advice to rest.

Write the check-in as **plain text for a phone notification**:
- At most 6 short lines. No markdown, no tables, no headers.
- Start with the workout line. If nothing was logged today, ask: "Did you train today? Reply in Kitchen Pi to log it."
- Then tomorrow (first event, tasks), then the recovery line.
- If a tool fails or data is missing, say so in a few words; don't guess numbers.

Output only the check-in text, nothing else.
