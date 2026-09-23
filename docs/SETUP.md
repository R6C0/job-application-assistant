# Setup

About 20 minutes, most of it waiting for one API key.

---

## 1. Install

```bash
git clone https://github.com/R6C0/job-application-assistant
cd job-application-assistant

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -e ".[llm,browser]"
playwright install chromium     # only needed for assisted form filling
```

Python 3.11 or newer. Without the `llm` extra you get the template drafter;
without `browser` you get everything except assisted fill.

---

## 2. Keys

Four, three of them instant and all free.

### Adzuna (instant)

1. https://developer.adzuna.com/ then "Sign up"
2. You get an **Application ID** and an **Application Key** immediately
3. Free tier is generous; this tool uses roughly 5 calls per cycle

### Reed (usually instant, sometimes a day)

1. https://www.reed.co.uk/developers/jobseeker
2. Sign in and request a key
3. Note the unusual auth: the key is the HTTP Basic **username**, with an empty
   password. The adapter handles it; worth knowing if you debug a 401.

### ntfy (2 minutes, no account)

1. Install "ntfy" from the App Store or Play Store
2. Generate a topic nobody will guess:

   ```bash
   python -c "import secrets; print('jobs-' + secrets.token_urlsafe(16))"
   ```

3. In the app: **Subscribe to topic**, paste that string
4. Put the same string in `.env` as `JOBHUNT_NTFY_TOPIC`

The topic name is the only secret. Anyone who knows it can read your
notifications, so do not use `joshua-jobs`.

### Companies House (instant, optional)

1. https://developer.company-information.service.gov.uk/
2. Register, create an application, create a **REST API** key

Without it, company research is skipped and the rest still works.

### Anthropic (optional)

https://console.anthropic.com/ for an API key. Without it, letters come from the
template drafter. With it, expect a few pence per letter.

---

## 3. Configure

```bash
cp .env.example .env                    # the keys
cp config.example.yaml config.yaml      # preferences
cp profile.example.yaml profile.yaml    # your CV
```

`profile.yaml` is the one that matters, and the example is already filled in with
a realistic version you can edit rather than start from nothing.

Three things to get right:

**`evidence`** is the list of facts the letter drafter is allowed to use. Each
should be something you can defend in an interview. Include the number where
there is one: bullets with metrics are ranked ahead of bullets without.

```yaml
- id: ev-pipeline-scale
  text: >-
    I own a production ETL pipeline processing around 380,000 EV charging
    session records a month
  metric: 380,000 records a month
  context: EV charging network operator, hardware validation team
  skills: [Python, SQL, ETL, data pipeline, AWS, S3]
```

`skills` decides which postings this bullet is offered for, so tag generously but
honestly.

**`voice_sample`** is a paragraph in your own writing. The drafter matches its
register. Skip it and letters drift toward the same neutral corporate voice.

**`preferences`** drives the hard filters. `min_salary`, `contract_ok` and
`exclude_title_terms` do the most work. Note that a missing salary never blocks,
because most UK postings hide it.

Put your CV at the path in `config.yaml` (`cv.pdf` by default) for assisted fill
to attach it.

---

## 4. Check before you run

```bash
jobhunt check
```

This validates config, profile and keys without calling anything. It tells you
everything that is wrong at once:

```
profile      Joshua Charles <you@example.com>
             11 evidence entries, 5 core skills
sources      adzuna, reed
notify       ntfy
letters      auto (claude available)
research     companies house
cv           cv.pdf found

Ready. Run: jobhunt run --dry-run
```

---

## 5. First run

```bash
jobhunt run --dry-run     # everything except sending notifications
jobhunt review            # http://127.0.0.1:8765
```

Look at what came back. If the scores are wrong, tune
`scoring.notify_threshold` and `scoring.weights` in `config.yaml` before you turn
notifications on. Expect a day or two of this.

When it looks right:

```bash
jobhunt run               # for real
jobhunt watch             # every 90 minutes until you stop it
```

---

## 6. Running it in the background

**Windows, Task Scheduler:**

Create a task, trigger "At log on", action:

```
Program:   C:\path\to\job-application-assistant\.venv\Scripts\pythonw.exe
Arguments: -m jobhunt.cli watch
Start in:  C:\path\to\job-application-assistant
```

`pythonw.exe` rather than `python.exe` so it runs without a console window.

**macOS / Linux:** a `launchd` plist or a systemd user unit running
`jobhunt watch` with `WorkingDirectory` set to the repo.

The review interface is separate and only needs to run when you are reviewing:

```bash
jobhunt review
```

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `401` from Reed | The key is the Basic auth *username*, password empty. Check for a trailing newline in `.env`. |
| `429` from Adzuna | Free tier rate limit. Reduce `queries` or raise `interval_minutes`. |
| No notifications | Check the topic matches the app exactly, and that you are not inside `quiet_hours`. |
| Everything scores low | `must_have_skills` is too long. It is the denominator: five core skills, not fifteen. |
| Nothing gets through | Check `jobhunt status` for rejection reasons. Usually `min_salary` or an `exclude_title_terms` entry that is too broad. |
| Letters are generic | No `voice_sample`, or thin `evidence`. The drafter can only use what you gave it. |
| Assisted fill misses fields | Expected on an unfamiliar ATS. It fills what it recognises; type the rest. |
