# Dutch Daily 🇳🇱

A tiny service for your **Raspberry Pi** that emails you one real **NOS.nl** news
article every morning, rewritten by AI into an **English study breakdown** for a
**total beginner (A1)** — vocabulary, useful phrases, grammar notes and a quick
self-test — delivered straight to your **Kindle**.

```mermaid
flowchart LR
    A[NOS RSS feed<br/>feeds.nos.nl/nosnieuwsalgemeen] -->|1. newest unseen article| B[Scraper<br/>full text from JSON-LD]
    B --> C[Claude<br/>writes beginner lesson in English]
    C --> D[Render HTML document]
    D --> E[Gmail SMTP<br/>App Password]
    E --> F[Kindle<br/>yourname@kindle.com]
    B -.-> G[(state.json<br/>remembers sent articles)]
```

## How it works

1. **05:00 UTC** the container wakes up (picked to sit in DeepSeek's off-peak
   window — verify the window on https://platform.deepseek.com).
2. It pulls the NOS general-news RSS feed, then picks the **newest article it
   hasn't already sent** (`data/state.json` remembers, so you never get repeats).
3. It grabs the full article text and sends it to the **LLM** (Anthropic Claude
   or DeepSeek), which produces a
   structured, beginner-friendly lesson **in English**: headline translation,
   summary, ~10 key words with example sentences, useful phrases with
   word-for-word translations, grammar points pulled from the real text, common
   traps, and a 3-question self-test with answers.
4. The lesson is rendered into a clean HTML document (reflowable on Kindle) and
   **emailed from your Gmail to your Kindle address**.
5. A copy of every lesson is also saved to `output/dutch-daily-YYYY-MM-DD.html`
   so you can preview/print it.

## Option A: use DeepSeek instead of Claude

If you're on DeepSeek (its API is Anthropic-compatible, so the same code works):

1. Get a key at https://platform.deepseek.com/ → *API Keys* → *Create key*.
2. In `.env` set:
   ```env
   ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
   ANTHROPIC_API_KEY=sk-<your-deepseek-key>
   CLAUDE_MODEL=deepseek-v4-pro        # or deepseek-v4-flash (cheaper/faster)
   ```
   `deepseek-v4-pro` gives the best lessons; `deepseek-v4-flash` is fine for a
   short daily article and costs less.

## Before you start — the two one-time accounts you need

1. **An LLM API key** — for writing the lessons:
   - **Anthropic**: https://console.anthropic.com/ → *API Keys* → *Create Key*, or
   - **DeepSeek** (if you use DeepSeek): https://platform.deepseek.com/ → *API
     Keys*, then follow the *Option A: DeepSeek* section above.
   Paste the key into `.env` as `ANTHROPIC_API_KEY`.

2. **A Gmail account + App Password** — for sending the email.
   - Turn on **2-Step Verification** on the Google account.
   - Google Account → **Security → App passwords** → create one for *Mail*.
   - Copy the 16-character password into `.env`.

3. **Whitelist that Gmail address in Amazon** *(critical — email is silently
   dropped otherwise)*:
   - amazon.com → **Account → Content & Devices → Preferences → Personal Document
     Settings → Approved Personal Document E-mail List → Add**
   - add your `youraddress@gmail.com`.

> 💡 Amazon delivers to `<name>@kindle.com`. If you ever want to be extra safe
> against any mobile-data delivery, also add `<name>@free.kindle.com` and send to
> that address instead (it allows Wi-Fi-only delivery).

## Install on the Raspberry Pi

Copy this whole folder to the Pi (e.g. `git clone …` or `scp`), then:

```bash
cd kp
cp .env.example .env
nano .env            # add ANTHROPIC_API_KEY, GMAIL_USER, GMAIL_APP_PASSWORD
```

Build & start it as a background service:

```bash
docker compose up -d --build
docker compose logs -f dutch-daily   # watch it work
```

The container restarts automatically (`restart: unless-stopped`) and fires once a
day at **05:00 UTC**.

## Test it immediately (before waiting for 05:00 UTC)

Send one lesson to your Kindle right now:

```bash
docker compose run --rm dutch-daily python -m app.main --once
```

…or just build the lesson and **save the preview without emailing**:

```bash
docker compose run --rm dutch-daily python -m app.main --once --no-email
```

Check `output/dutch-daily-*.html` — open it in a browser to see exactly what your
Kindle will receive.

## Running without Docker (optional)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env
python -m app.main --once          # test run
python -m app.main &               # scheduler mode
```

## Project layout

```
kp/
├── app/
│   ├── main.py        # CLI: --once / scheduler mode
│   ├── config.py      # reads .env
│   ├── nos.py         # NOS RSS feed + full-text scraper
│   ├── lesson.py      # Claude prompt + lesson parsing
│   ├── render.py      # lesson -> Kindle HTML
│   ├── kindle.py      # Gmail SMTP sender
│   ├── scheduler.py   # daily loop (05:00 UTC)
│   └── state.py       # remembers sent article ids
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example       # <- copy to .env and fill in
├── data/              # state.json (kept across rebuilds via volume)
└── output/            # daily .html previews
```

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | LLM API key (Anthropic **or** DeepSeek) — required |
| `ANTHROPIC_BASE_URL` | *(Anthropic default)* | Set to `https://api.deepseek.com/anthropic` to use DeepSeek |
| `CLAUDE_MODEL` | `claude-sonnet-4-5` (Anthropic) / `deepseek-v4-pro` (DeepSeek) | Model used for lessons |
| `GMAIL_USER` | – | Gmail "from" address (required to email) |
| `GMAIL_APP_PASSWORD` | – | Gmail App Password (required to email) |
| `KINDLE_EMAIL` | `yourname@kindle.com` | Destination Kindle address (comma-separate to send to several) |
| `TIMEZONE` | `UTC` | IANA zone for the schedule (use UTC so delivery is always at a fixed UTC hour) |
| `DELIVERY_TIME` | `05:00` | Daily delivery time (set to an hour inside DeepSeek's off-peak window) |
| `SEND_EMAIL` | `true` | `false` = build previews only |
| `MAX_ARTICLE_CHARS` | `1700` | Article length fed to Claude |
| `MAX_VOCAB` | `12` | Max vocab entries in each lesson |

## Troubleshooting

- **Nothing arrives on the Kindle** → check the Gmail address is on Amazon's
  *Approved Personal Document E-mail List* (this is the #1 cause), and confirm
  `SEND_EMAIL` isn't `false`.
- **`Gmail rejected the login`** → use an App Password, not your normal password.
- **`No items …`** → NOS feed unreachable; check the Pi's network/date.
- **Duplicates** → delete `data/state.json` to reset the "already sent" memory.
- **AI caveat** → grammar notes are AI-generated; spot-check important details.

## Ideas to extend later

- Switch feed to sport/tech: `NOS_FEED_URL=https://feeds.nos.nl/nosnieuwssport`
- Add audio by including a text-to-speech clip (e.g. NOS op 3 / Google TTS) in the email.
- Send an `.epub` instead of `.html` for nicer styling.
