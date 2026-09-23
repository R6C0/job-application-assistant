# Engineering notes

Known limitations, and the bugs found while building it. Written now, while they
are fresh, rather than discovered by whoever reads the code next.

---

## Bugs the tests caught

Both of these were real and both would have reached a user.

### SQLite across threads

`Store` opened its connection in the main thread. FastAPI runs synchronous
endpoint handlers in a thread pool, so the first request to the review interface
hit:

```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in
that same thread.
```

Thirteen tests failed at once the moment the review interface got its first test.
Nothing before that point had exercised the store from more than one thread, so
the unit tests all passed while the application was broken.

**Fixed** by opening with `check_same_thread=False` and serialising every access
through an `RLock`. Correct for one user on one machine; a genuinely concurrent
deployment would want a connection pool.

### Starlette changed its template signature

`TemplateResponse("queue.html", {"request": request, ...})` is the old form. The
current signature takes the request first, and passing a dict where a template
name is expected surfaces as:

```
TypeError: unhashable type: 'dict'
```

**Fixed**, and worth noting as an argument for rendering every page in a test:
this would not have appeared until someone opened the app in a browser.

### A test that was wrong, not the code

`test_daily_budget_stops_notifications` created five jobs that differed only by
id, and expected two notifications. It got one, because all five shared a company
and title, so the deduplicator correctly collapsed them into one role seen five
times.

The code was right. The test now uses genuinely distinct jobs and asserts
`report.duplicates == 0` so it fails loudly if it ever starts testing
deduplication by accident again.

---

## Limitations

### 1. Coverage is two boards

Adzuna and Reed. Many roles are posted only to LinkedIn, or to a company's own
Greenhouse or Lever board, and this tool will never see them.

The fix for the second half is straightforward: Greenhouse, Lever, Ashby and
Workable all publish job board JSON at predictable URLs with no key needed. A
watchlist of companies you care about would add real coverage inside the same
"official endpoints only" rule. That is the first thing I would build next.

LinkedIn stays out. See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md#2-official-apis-only).

### 2. Scoring is heuristic, and tuned to one person

The weights are a guess that looked reasonable. There is no feedback loop: the
tool never learns that you skipped nine "Data Engineer" roles at agencies.

A better version would score on outcomes, using your own approve and skip
decisions as labels. With a few hundred decisions that is a real model. With
twenty it is overfitting, which is why it is not in v1.

### 3. The years-of-experience blocker is blunt

`_required_years` takes the largest plausible number followed by "years" in the
description. It handles "3 years of Python, 5 years of SQL" correctly by taking
5, and ignores anything above 15 so "founded 1998" does not read as a
requirement.

It still misfires on "5 years or equivalent experience", "5 years of combined
team experience", and on a posting listing a career ladder. Those get silently
rejected. `jobhunt status` shows rejections with reasons, which is the only
current mitigation.

### 4. Company name matching is fuzzy

Companies House is matched on a normalised company name, which is genuinely
ambiguous: agencies advertise on behalf of unnamed clients, trading names differ
from registered names, and "Apple" returns orchards.

The brief reports its own confidence rather than hiding the problem, and the
letter drafter is told to use at most one company fact and only when it bears on
the application. But a `medium` confidence brief can still be the wrong company,
and the summary says so in words.

### 5. Assisted fill breaks when ATS markup changes

Selectors match on field semantics (autocomplete attributes, input types, name
fragments) rather than CSS paths, which survives redesigns better than the
alternative. It will still miss fields, and some ATS flows are multi-step wizards
or render inside iframes that this does not follow.

A miss leaves the field blank for you to type, which is a normal Tuesday rather
than a failure. But "it filled 3 of 9 fields" is a plausible outcome on an
unfamiliar ATS.

### 6. The number validator has false positives

It flags any number in a draft that does not appear in the evidence string, so
"three years" against evidence saying "3 years" trips it and burns a
regeneration. Normalising written numbers and tolerating rounding within a
sensible band would fix it.

### 7. No retry or backoff on source failures

A rate limit or a timeout means that source contributes nothing this cycle. With
a 90 minute loop that self-corrects, so it has not been worth the complexity, but
a long outage is currently invisible unless you read the logs.

### 8. ntfy topics are the only secret

Anyone who knows your topic name can read your notifications. The tool warns
below 16 characters and the setup doc tells you to generate a random one, but the
underlying model is security by obscure URL. ntfy supports authentication; this
does not use it yet.

### 9. Letters are drafted before you look at the job

The pipeline drafts for everything above the threshold, which costs an API call
for jobs you will skip in two seconds. Drafting lazily on first open would be
cheaper but would make the review page slow at exactly the wrong moment. At a few
pence per letter the current trade is fine; at a lower threshold it would not be.

### 10. Single user, single machine

SQLite with a lock, a localhost web app with no authentication, secrets in a
`.env`. All correct for a personal tool and all wrong for anything shared. The
localhost binding is deliberate and not configurable: a remotely reachable review
interface is the first step toward removing the human from the loop.

---

## What I would build next, in order

1. **ATS board sources.** Greenhouse, Lever, Ashby, Workable. Biggest coverage
   gain available inside the rules this tool follows.
2. **Outcome-based scoring.** Use approve and skip as labels once there are
   enough of them to mean something.
3. **Application tracking beyond submit.** Responses, interviews, rejections.
   The schema already has the stage machinery for it.
4. **Fix the number validator's false positives.**
5. **Lazy drafting** behind a config flag, for people running a low threshold.
