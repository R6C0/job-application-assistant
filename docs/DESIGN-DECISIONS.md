# Design decisions

What the problem was, what was built, why, and what the choice cost.

1. [The human gate is a type, not a setting](#1-the-human-gate-is-a-type-not-a-setting)
2. [Official APIs only](#2-official-apis-only)
3. [Blockers are separate from points](#3-blockers-are-separate-from-points)
4. [The scorer explains itself](#4-the-scorer-explains-itself)
5. [A closed set of facts for letters](#5-a-closed-set-of-facts-for-letters)
6. [The CV is structured data, not a parsed PDF](#6-the-cv-is-structured-data-not-a-parsed-pdf)
7. [Notification budget and quiet hours](#7-notification-budget-and-quiet-hours)
8. [SQLite and no ORM](#8-sqlite-and-no-orm)

---

## 1. The human gate is a type, not a setting

### The problem

The brief was "auto apply". Everything else in this tool is recoverable: a bad
score wastes a notification, a failed fetch retries in 90 minutes. A wrongly
submitted application is seen by a real person at a company you wanted to work
for, and cannot be recalled.

### What was built

`Stage.SUBMITTED` is reachable only from `Stage.APPROVED`, and the check lives in
`Application.advance` rather than at the call sites:

```python
if to is Stage.SUBMITTED and self.stage is not SUBMIT_REQUIRES:
    raise ValueError(...)
```

`APPROVED` is set in exactly one place: a POST handler in the review interface,
reached by a button on a page showing the letter. There is no `auto_submit`
option in the config, because an option is a thing someone sets at 1am when they
are tired of clicking.

The last mile is assisted instead: a visible browser, fields filled from your
profile, CV attached, letter pasted, and the submit button left alone.

### What it cost

It is slower. On a good day you might click through fifteen applications instead
of having forty sent for you. That is the trade, and the forty would be worse
applications.

It also means the tool cannot run truly unattended. `jobhunt watch` finds and
prepares work; it does not clear the queue.

---

## 2. Official APIs only

### The problem

LinkedIn and Indeed carry the most UK listings. Both prohibit automated access in
their terms, and both actively detect it.

### What was built

Adzuna and Reed, which have official APIs, free tiers and terms that permit this.
Adzuna aggregates a large share of the UK market including many listings that
originate elsewhere, so the coverage gap is smaller than the source count
suggests.

Every request carries an identifying user agent. Adzuna queries are issued one
per second; Reed detail fetches are capped at four concurrent.

### What it cost

Genuine coverage loss, mostly for roles posted only to LinkedIn. That is a real
downside and the honest mitigation is that this tool is a filter on top of your
search, not a replacement for it.

### What it bought

The thing that would actually end a job search badly is a restricted LinkedIn
account halfway through it.

---

## 3. Blockers are separate from points

### The problem

A job asking for twelve years of experience can still match on Python, SQL, ETL,
AWS, London and a recent posting date. Score it as a single number and it clears
any sensible threshold.

### What was built

Two independent outputs. `Match.blockers` is a list of hard disqualifications:
excluded company, excluded title term, contract when you want permanent, salary
ceiling below your floor, years-of-experience beyond your level, remote or onsite
against your stated preference. Any entry means the application is rejected
regardless of score.

`Match.score` then ranks only what survived.

### Why

A threshold cannot express "never, at any score". Trying to make it do so leads
to weight tuning that breaks something else.

### What it cost

Blockers are blunt. The years check reads the largest plausible "N years" in the
description, which misfires on a posting that says "5 years of SQL or equivalent
experience". A posting like that is silently rejected and you will not see it
unless you check `jobhunt status`. Documented in
[ENGINEERING-NOTES.md](ENGINEERING-NOTES.md#4-the-years-of-experience-blocker-is-blunt).

---

## 4. The scorer explains itself

### The problem

A number cannot be acted on. "78" tells you nothing about whether to apply, and
gives the letter drafter nothing to write from.

### What was built

Every component returns a `Reason`: a name, a signed point contribution, a
human-readable sentence and any supporting evidence. The same objects feed three
consumers: the notification body, the review page table, and the prompt used to
draft the letter.

```python
Reason("skills", 32.0, "4/4 core skills present, plus 3 secondary", ["python", "sql"])
```

### Why one structure for three consumers

They need the same thing. Writing a separate summary for the prompt would let the
letter cite something the page never showed you, which is exactly how a tool
loses your trust.

### What it cost

Component functions have to produce prose, not just arithmetic. That constraint
keeps them simple, which is mostly a benefit, but it does mean the scorer cannot
easily become a learned model later without inventing an explanation layer.

---

## 5. A closed set of facts for letters

### The problem

Handing a model your CV and a job description produces a fluent letter that
occasionally invents a number. You will not catch it every time, and a fabricated
achievement in a cover letter is the kind of mistake that ends a process.

### What was built

The drafter may use only `Evidence` entries from your profile. The system prompt
states it. The validator then checks the returned draft for numbers that do not
appear in the evidence supplied, rejects on a hit, regenerates once with the
problem named, and falls back to the deterministic template drafter if it fails
twice.

Plus a ban list of twenty phrases that mark a letter as generated, and an opening
move chosen from the job id so consecutive letters do not share a shape.

### Why the number check specifically

It is cheap and it catches the realistic failure. Models do not usually invent a
whole employer; they round "380,000 records a month" to "over half a million"
because it reads better.

### What it cost

False positives. The validator flags any number not present in the evidence
string, so a model writing "three years" when the evidence says "3 years" trips
it and wastes a regeneration. Tightening that is on the list.

The bigger cost is that thin evidence produces a thin letter. That is arguably
correct behaviour, but it means the profile has to be maintained.

---

## 6. The CV is structured data, not a parsed PDF

### The problem

Parsing a CV PDF gives you a wall of text that something then has to interpret on
every single job: expensive, and an invitation to paraphrase you inaccurately.

### What was built

`profile.yaml`: skills, target roles, preferences, and a list of evidence bullets
each tagged with the skills it demonstrates and the metric if there is one.
Written once, by hand.

`Profile.evidence_for(skills)` ranks by how many of the requested skills a bullet
covers, then by whether it has a number. That ordering is most of why the letters
read as specific.

### What it cost

Setup effort, and an ongoing maintenance burden: a CV update means editing the
YAML too. The loader refuses to start without evidence entries, which turns "I
will fill that in later" into an error at `jobhunt check` rather than a vague
letter three weeks on.

---

## 7. Notification budget and quiet hours

### The problem

A tool that pushes forty notifications on its first good day gets muted on its
second, and a muted tool is worse than no tool.

### What was built

Three limits: per run (10), per day (25), and quiet hours (22:00 to 07:00).
Over-budget jobs are still scored, researched, drafted and queued; they simply do
not push. Notifications are recorded per job per day, so a rerun never pushes the
same job twice.

### What it cost

You have to open the queue to see everything, and a genuinely great job found at
23:30 waits until morning. Both are deliberate: the alternative erodes the
signal that makes the push worth anything.

---

## 8. SQLite and no ORM

### The problem

State that has to survive restarts: which jobs have been seen, what they scored,
which drafts exist, what has been notified and approved.

### What was built

One SQLite file, plain `sqlite3`, hand-written SQL, `CREATE TABLE IF NOT EXISTS`
plus a schema version row. Derived structures (reasons, briefs, letters) are
stored as JSON in columns; anything queried or sorted on gets a real column.

### Why no ORM

The schema is three tables. A migration framework and a model layer would be more
code than the thing they manage.

### What it cost

Hydration is manual: `_hydrate` rebuilds dataclasses from rows, and adding a
field means touching both directions. A schema change beyond adding a nullable
column would need a migration written by hand, which the version row exists to
make possible but does not do for you.

There is also a real concurrency constraint. The review interface runs endpoints
in a thread pool, so the connection is opened with `check_same_thread=False` and
every access goes through an `RLock`. That is correct for one user on one laptop
and would need rethinking for anything else. The tests caught this: see
[ENGINEERING-NOTES.md](ENGINEERING-NOTES.md#bugs-the-tests-caught).
