# GRIT AI Chatbot

A customer-facing Q&A chatbot for GRIT AI, an agentic AI services company. It answers prospect questions from an approved FAQ, hands off to a human when needed, captures leads, and emails the team when someone needs follow-up.

It is a retrieval-augmented generation (RAG) system: the model answers only from retrieved FAQ passages, so answers stay grounded and are updated by editing the FAQ, not by retraining anything.

```
Customer → web chat → server → retrieve top-5 FAQ chunks → OpenAI chat model (+ system prompt)
                                                              │
                                      tools: capture_lead / escalate_to_human
                                                              │
                                          saved to data/  +  email alert to your team
```

## Features

- **Grounded answers.** Responds only from retrieved FAQ content. Says "I don't have that" instead of guessing.
- **Never invents commitments.** No prices, timelines, discounts, certifications, contract terms, guarantees, or case studies unless the FAQ states them.
- **Human handoff.** Triggered by a request for a person, a complaint, frustration, sensitive topics, or low confidence. The team is emailed with the conversation so far.
- **Lead capture.** Collects name, contact, and business problem conversationally, then emails the team.
- **Transparency and safety.** Always identifies itself as an AI, resists prompt injection, never shares credentials or other customers' data.
- **Local vector store.** No external database. Embeddings live in a NumPy file.
- **Monitoring.** Conversation logs (with emails and phone numbers masked), thumbs up/down feedback, and a report that shows which questions the FAQ doesn't cover.

## Project layout

```
grit_ai_chatbot/
├── README.md
├── requirements.txt
├── env.example              copy to ".env" and fill in
├── src/
│   ├── chunk.py             numbered-FAQ markdown -> chunks
│   ├── chunk_structured.py  chunker for the structured KB format (future upgrade)
│   ├── embed.py             embed chunks with OpenAI, save local index
│   ├── retrieve.py          search the index
│   ├── eval_retrieval.py    retrieval-only evaluation (no chat model)
│   ├── chat.py              retrieval + OpenAI chat + tools; interactive CLI
│   ├── run_eval.py          full 50-question evaluation with an LLM judge
│   ├── server.py            FastAPI web server
│   ├── notify.py            email alerts (SMTP)
│   └── report.py            usage and improvement report
├── web/index.html           chat page
├── prompts/
│   ├── system_prompt.md     the bot's instructions
│   ├── config.json          handoff channel, hours, free-consultation setting
│   └── bot_rules.md         (generated) FAQ Q46-50 + internal rules
├── eval/
│   ├── test_questions.json  the 50 test questions
│   └── results.json         (generated) latest evaluation results
├── kb/
│   ├── faq_chunks.jsonl     (generated) chunks
│   └── index/               (generated) embeddings.npy + meta.json
├── docs/build_notes.md      (generated) KB categories and architecture notes
└── data/                    (generated at runtime) logs, leads, escalations
```

## Quick start (Windows PowerShell)

**1. Install**

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**2. Configure secrets.** Copy `env.example` to `.env` in the project root and fill it in (see [Configuration](#configuration)).

```
copy env.example .env
```

**3. Configure the bot.** Edit `prompts/config.json`:

| Key | Meaning |
|---|---|
| `HANDOFF_CHANNEL` | Where customers can reach a person, e.g. "our team at hello@gritai.com" |
| `HANDOFF_HOURS` | When someone is available, e.g. "Mon-Fri, 9am-5pm WAT" |
| `FREE_CONSULT` | `NOT ACTIVE`, or `ACTIVE: <offer details>`. The bot only mentions a free consultation when active. |

**4. Build the knowledge base** (quote paths that contain spaces):

```
python src\chunk.py "path\to\GRIT AI.md"
python src\embed.py
```

`chunk.py` should report **50 questions found, 45 indexed chunks, 5 bot-instruction entries**.

**5. Check retrieval and try the bot**

```
python src\eval_retrieval.py
python src\chat.py --debug
```

**6. Test email alerts**

```
python src\notify.py --test
```

**7. Run the web server**

```
python src\server.py
```

Open http://127.0.0.1:8000.

## How it works

1. **Chunking** (`chunk.py`). Each Q&A becomes one chunk with metadata (category, priority, escalation flag, and so on). Q46-50 and the internal rules are separated out so the bot can never quote its own instructions to a customer.
2. **Embedding** (`embed.py`). Each chunk's `embed_text` (category, question, answer, and customer-style keywords) is embedded with OpenAI and stored locally. The index records its model and a hash of the chunks file, and warns if they go out of sync.
3. **Retrieval** (`retrieve.py`). The customer's message is embedded and compared by cosine similarity. The top 5 chunks go to the model. Short follow-ups borrow the previous question for retrieval.
4. **Answering** (`chat.py`). The system prompt, with the retrieved chunks inserted, goes to the OpenAI chat model along with two tools.
5. **Tools.** `capture_lead` and `escalate_to_human` save to `data/` first (the source of truth), then email the team in the background.
6. **Serving** (`server.py`). Per-customer sessions, a rate limit, input limits, a safe fallback if OpenAI fails, and logging.

## Configuration

Set these in `.env` (or as environment variables).

| Variable | Required | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI access |
| `CHAT_MODEL` | No | Chat model (default `gpt-5.4-mini`). List what your key can use with `python src\chat.py --models`. Avoid the GPT-4 family, which OpenAI has scheduled to shut down on 2026-10-23. |
| `EMBED_MODEL` | No | Default `text-embedding-3-small`. Changing it requires re-running `embed.py`. |
| `JUDGE_MODEL` | No | Model that grades evaluation replies (defaults to `CHAT_MODEL`; a stronger model grades more fairly). |
| `NOTIFY_TO` | For email | Team address(es), comma-separated |
| `EMAIL_FROM` | For email | The bot's sender address (fixed; customers are never the sender) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | For email | Mail provider settings (`starttls` on 587, or `ssl` on 465) |
| `SMTP_USER`, `SMTP_PASSWORD` | For email | The bot mailbox login (usually an app password) |
| `NOTIFY_MAX_PER_HOUR` | No | Alert-email safety cap (default 30) |
| `ALLOWED_ORIGINS` | No | Websites allowed to call the API (default `*`; **set to your real site before going live**) |
| `RATE_LIMIT_PER_MIN` | No | Messages per IP per minute (default 20) |
| `HOST`, `PORT` | No | Server bind address (default `127.0.0.1:8000`) |

Never commit `.env`. Suggested `.gitignore`:

```
.env
.venv/
data/
kb/index/
eval/results.json
```

## Updating the knowledge base

1. Edit the FAQ markdown.
2. Re-run `chunk.py`, then `embed.py`.
3. Re-run `eval_retrieval.py` and `run_eval.py` and review any new failures.

In `chunk.py`, three dictionaries control chunk metadata: `CATEGORIES` (category and subcategory per question), `ESCALATE` (entries where the bot should lean toward a human), and `KEYWORDS` (customer-style phrasings added to the embedding text only, which improve matching when people word questions differently).

If the FAQ later moves to the structured, metadata-rich format (ID heading, Metadata block, Customer-Facing Answer), use `chunk_structured.py` instead. Both produce the same chunk fields, so nothing downstream changes.

## Evaluation

Two layers, both driven by `eval/test_questions.json` (50 questions across direct and paraphrased FAQ questions, commitments and "never invent", escalation and lead capture, prompt injection, and off-topic requests):

- **`eval_retrieval.py`** checks that the right FAQ entry is found (Hit@1, Hit@3, Hit@5). It uses only embedding calls, so it is quick and cheap.
- **`run_eval.py`** runs each question through the full bot. A test passes when the expected chunk was retrieved, the tool behavior was correct (escalate when expected, never save a lead with invented data), and an LLM judge agrees the reply meets the test's `expected_behavior`. Phrase checks (`must_include`, `must_not_include`) are shown as flags only. Use `--no-judge` for phrase checks alone.

Run the evaluation after every change to the prompt, FAQ, or model. A failure is not always a bad answer: read the reply and the judge's reason before changing anything. Tools run in dry-run mode during evaluation, so no emails are sent.

## Monitoring

```
python src\report.py
```

Shows usage, human-handoff rate, leads, alert-email health, thumbs-down answers, "weak matches" (real questions the FAQ barely covers, which are candidates for new entries), and the most-asked topics.

## Data and privacy

| File in `data/` | Contents |
|---|---|
| `conversations.jsonl` | Each turn, with emails and phone numbers masked. Tool calls are logged by name only. |
| `leads.jsonl` | Full lead details (name, contact, problem). Real personal data. |
| `escalations.jsonl` | Handoff requests |
| `feedback.jsonl` | Thumbs up/down and optional comments (masked) |
| `notifications.jsonl` | Every alert-email attempt: sent, failed, skipped, or not configured |

Alert emails contain real customer details and recent conversation text, so send them only to people who should see that. Decide a retention period for `data/`, restrict who can read it, and add a short privacy notice to the chat page if you collect personal data.

## Email alerts

- `capture_lead` sends a "New lead" email. The prospect's address, if given, is set as Reply-To.
- `escalate_to_human` sends a "Human requested" email with the reason and conversation. If the customer has shared no contact details, the subject says so and the bot asks for them.
- Emails send in the background, so a mail failure never delays or breaks the chat. Each lead or handoff is saved to `data/` first, so nothing is lost if email is down.
- Safeguards: duplicate suppression (10 minutes), an hourly cap, and header-injection protection.
- The sender (`EMAIL_FROM`) is always the bot's own mailbox. Customers are never the sender.

## Before going live

- [ ] Run `run_eval.py`, review every failure, and fix prompt, content, or test wording
- [ ] Fill the FAQ content gaps: pricing approach, timeline ranges, data-training policy, compliance certifications, contact details, case studies
- [ ] Fill in `prompts/config.json` (`HANDOFF_CHANNEL`, `HANDOFF_HOURS`, `FREE_CONSULT`)
- [ ] Set up the bot mailbox and confirm `python src\notify.py --test` works with your real settings
- [ ] Set `ALLOWED_ORIGINS` to your real website
- [ ] Host behind HTTPS (small cloud server or container); configure the proxy to pass `X-Forwarded-For`
- [ ] Choose a log retention period and add a privacy notice
- [ ] Soft-launch to a small group; review `report.py` weekly

## Troubleshooting

| Problem | Fix |
|---|---|
| `can't open file '...\src\chunk.py'` | The script isn't in `src\`. Move it there. |
| Path with spaces fails | Wrap the path in quotes: `"path\GRIT AI.md"` |
| `No index found. Run: python src/embed.py` | Run `chunk.py` first, then `embed.py` |
| `faq_chunks.jsonl changed since the index was built` | Re-run `embed.py` |
| `OPENAI_API_KEY is not set` | Check that the file is named exactly `.env` (not `.env.txt`) and sits in the project root |
| `chunk.py` finds fewer than 50 questions | Send the first 20 lines of your markdown. The parser may need adjusting for its format. |
| Test email fails | `notify.py --test` explains the cause. Gmail and Microsoft 365 usually need an app password. |
| Customer sees "I'm having trouble right now" | OpenAI call failed. Check the key, the model name (`--models`), and `data/conversations.jsonl` for the error. |

## Roadmap

- **Now:** finish evaluation review and content gaps, then host and soft-launch.
- **Next:** phase-2 agentic tools (order or status lookups, CRM updates, ticket creation) with approval steps and audit logs, only after the Q&A baseline is reliable.
- **Later:** hybrid search (keyword plus vector) and reranking, a hosted vector database if the knowledge base outgrows the local index, and per-topic email routing (for example sales versus support).