# Daily Brief

A plain-text morning brief for your family, built from your calendars' private
iCal feeds. Every morning it emails you today's schedule — events pulled from
each family member's calendar, deduplicated, sorted, with a weather line,
tappable map links, conflict flags, and a heads-up section for tomorrow's
big items (tournaments, travel, appointments). No server, no OAuth, no API
keys: it runs on GitHub Actions' free scheduler.

Example output:

```
☀️ 75° / 53°, Sunny — Maplewood, NJ

📅 Today's Schedule — Monday, October 12

3:00 PM – 5:00 PM
🤽 Jake — Practice @ North Field
[📍 North Field](https://www.google.com/maps/search/?api=1&query=North%20Field)
```

## 15-minute setup

**1. Fork this repo.** Click **Fork** (or **Use this template**), then clone
your fork if you want to edit the config locally.

**2. Get your calendars' private iCal URLs.** You need one per calendar you
want in the brief (family calendar, each kid's team calendar, ...):

- **Google Calendar:** Settings → pick the calendar on the left →
  *Secret address in iCal format* → copy it.
- **Apple Calendar:** in the Calendar app, share the calendar and use the
  *Public Calendar* link.
- **Outlook:** Calendar → Share → Publish calendar → copy the **ICS** link.

Treat these URLs like passwords — anyone with the link can read the
calendar.

**3. Copy the example config and fill it in.**

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`: your timezone, where you live, one entry per calendar
with its iCal URL, who each calendar belongs to, and your SMTP details for
sending the email. Every secret field uses a `${VARIABLE}` placeholder that
is read from the environment, so nothing private lives in the file
(`config.yaml` is gitignored and must never be committed).

**4. Add your secrets to GitHub.** In your fork: **Settings → Secrets and
variables → Actions → New repository secret**. Add one secret per
placeholder used in the config:

| Secret | What it is |
|---|---|
| `ICAL_FAMILY` | Private iCal URL of the family calendar |
| `ICAL_KID1` | Private iCal URL of kid 1's calendar |
| `ICAL_KID2` | Private iCal URL of kid 2's calendar |
| `SMTP_HOST` | SMTP server, e.g. `smtp.gmail.com` |
| `SMTP_USER` | SMTP username, e.g. `you@gmail.com` |
| `SMTP_PASS` | SMTP password — for Gmail, use an **App Password** (Google Account → Security → 2-Step Verification → App passwords), not your login password |
| `SMTP_FROM` | Sender shown on the email, e.g. `Daily Brief <you@gmail.com>` |
| `EMAIL_TO` | Who receives the brief |

**5. Set the schedule for your timezone.** Open
`.github/workflows/daily-brief.yml` and edit the `cron` line. GitHub
Actions runs on **UTC**, so convert your local morning time:

| You want | Cron line |
|---|---|
| 6:30 AM Pacific Daylight (UTC−7) | `30 13 * * *` |
| 6:30 AM Pacific Standard (UTC−8) | `30 14 * * *` |
| 6:30 AM Eastern Daylight (UTC−4) | `30 10 * * *` |
| 6:30 AM Eastern Standard (UTC−5) | `30 11 * * *` |
| 6:30 AM Central Daylight (UTC−5) | `30 11 * * *` |

Cron format is `minute hour * * *`. Scheduled runs can start a few minutes
late — fine for a morning brief.

**6. Test it.** Go to the **Actions** tab, pick **Daily Brief**, and hit
**Run workflow**. Check your inbox. From then on it runs every morning on
its own.

## Running it locally

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # fill in your values (or export the env vars)
python brief.py                        # print today's brief
python brief.py --date 2026-10-12     # brief for a specific date (testing)
python brief.py --send                 # email it via SMTP
python brief.py --no-weather           # skip weather (offline testing)
```

Run the offline test suite (synthetic fixtures, no network):

```bash
bash tests/run_tests.sh
```

## FAQ

**Why iCal URLs instead of the Google Calendar API?**
Your calendar's private feed URL is all the script needs — no OAuth, no API
keys, no Google Cloud project.

**Does this work with Apple / Outlook calendars?**
Yes — anything that can publish a private iCal feed, which is all three.

**Can I add more than three calendars?**
Yes — add entries under `calendars:` in `config.yaml` and matching
`ICAL_...` secrets in the workflow.

**How does deduping work?**
When the same event appears on two calendars (e.g. a game on the team
calendar and the family calendar), the script shows it once, preferring the
title from the calendar marked `primary: true`.

**What shows up in the Tomorrow section?**
Only tomorrow's events matching your `tomorrow_keywords` (default:
tournament, flight, appointment, surgery, holiday, travel) — routine
practices stay out.

## Troubleshooting

- **"Config not found"** — you need a `config.yaml` next to `brief.py`
  (the workflow creates it from the example automatically).
- **Recurring events missing or duplicated** — make sure the feed URL is
  the *secret* iCal address; the public "HTML" address won't parse.
- **Times off by an hour** — check `timezone:` in your config, and remember
  the Actions cron is UTC: when daylight saving time starts/ends you must
  move the cron line by one hour (see the table above).
- **Brief says "No events on the calendar today"** — that's the script
  telling you the feeds genuinely returned nothing for today, not an error.
  Double-check the iCal URLs if you expected events.
- **Email never arrives** — check the Actions run log for SMTP errors; for
  Gmail the #1 cause is using your login password instead of an App
  Password. Also check spam.
