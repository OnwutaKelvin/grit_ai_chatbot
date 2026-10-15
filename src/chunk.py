#!/usr/bin/env python3
#!/usr/bin/env python3
"""Chunk the numbered GRIT AI FAQ (markdown or text) into RAG-ready chunks.

Usage:
    python src/chunk.py path/to/GRIT_AI.md

Handles common PDF/markdown conversion artifacts: bold wrappers, escaped
characters ("1\\."), glued bullets, zero-width spaces, and questions glued to
the end of the previous answer.

Outputs (relative to the repo root):
    kb/faq_chunks.jsonl    customer-facing chunks to embed and index
    prompts/bot_rules.md   Q46-50 + "Internal Chatbot Rules" (feeds the system prompt)
    docs/build_notes.md    KB categories + architecture (never indexed)

Chunk fields follow the structured KB schema (knowledge_id, category, priority,
requires_escalation, ...) so the content can be upgraded later without changing
the downstream pipeline.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_WORDS = 350  # ~450 tokens; longer answers are split by paragraph
OWNER = "GRIT AI"  # TODO: set the real owner per category
SOURCE = "GRIT AI FAQ v1 (PDF)"

# --- Category per question number (adjust freely) ---------------------------
CATEGORIES = {
    1: ("Company", "Overview"), 2: ("AI Agents", "Definition"),
    3: ("Services", "Industries"), 4: ("Services", "Use Cases"),
    5: ("Chatbots", "Interfaces"), 6: ("AI Agents", "Chatbot vs Agent"),
    7: ("AI Agents", "Actions"), 8: ("Data & Privacy", "Data Access"),
    9: ("Security", "Access Control"), 10: ("Integrations", "Methods"),
    11: ("Integrations", "Supported Software"),
    12: ("Technical Capabilities", "Company Documents"),
    13: ("Technical Capabilities", "RAG"),
    14: ("Technical Capabilities", "Reliability"),
    15: ("Technical Capabilities", "Hallucination"),
    16: ("Escalation Procedures", "Human Takeover"),
    17: ("Escalation Procedures", "Autonomy"),
    18: ("AI Agents", "Agentic Meaning"), 19: ("AI Agents", "Multiple Agents"),
    20: ("AI Agents", "Multi-Agent Systems"),
    21: ("Services", "Automation Fit"), 22: ("Deployment", "Timeline"),
    23: ("Pricing", "General Pricing"), 24: ("Pricing", "Billing Structure"),
    25: ("Policies", "Ownership"), 26: ("Deployment", "Hosting"),
    27: ("Support", "Maintenance"), 28: ("Support", "Monitoring"),
    29: ("Services", "Success Metrics"), 30: ("Policies", "Workforce Impact"),
    31: ("Security", "Data Security"), 32: ("Data & Privacy", "Model Training"),
    33: ("Integrations", "WhatsApp"),
    34: ("Technical Capabilities", "Voice"), 35: ("Automation", "Email"),
    36: ("Integrations", "CRM"), 37: ("Security", "Action Limits"),
    38: ("Escalation Procedures", "Fallback"), 39: ("Policies", "AI Disclosure"),
    40: ("Technical Capabilities", "Terminology"),
    41: ("Technical Capabilities", "Learning"), 42: ("Chatbots", "Personality"),
    43: ("Technical Capabilities", "Languages"),
    44: ("Sales", "Requirements"), 45: ("Sales", "Process"),
}
PRIORITY = {"Pricing": "critical", "Security": "critical", "Data & Privacy": "critical",
            "Company": "high", "Services": "high", "AI Agents": "high"}
# Entries where the bot should lean toward a human for specifics
ESCALATE = {22, 23, 24, 25, 26, 31, 32}
# Customer-style phrasings added to each chunk's EMBEDDING text only (never shown to the
# model or the customer). Improves matching when people word questions differently.
KEYWORDS = {
    1: ["about GRIT AI", "what do you do", "company overview", "who are you", "services offered"],
    2: ["what are agents", "define AI agent", "agentic AI explained"],
    3: ["industries", "sectors", "do you work with my industry", "logistics", "healthcare", "fintech"],
    4: ["use cases", "what can AI automate", "problems solved", "can you help us"],
    5: ["only chatbots", "channels", "WhatsApp Slack voice", "beyond chatbots"],
    6: ["chatbot vs agent", "difference between chatbot and agent", "ChatGPT-style bot"],
    7: ["can agents take actions", "do tasks", "update CRM", "just talk", "send emails"],
    8: ["access our data", "private information", "company data", "confidential data"],
    9: ["least privilege", "access to everything", "permissions", "what can the AI see"],
    10: ["integrate", "connect to our software", "API", "webhooks", "SAP", "Paystack"],
    11: ["supported software", "CRM ERP helpdesk", "payment systems", "Salesforce", "HubSpot"],
    12: ["use our PDFs", "company documents", "knowledge base", "policies and manuals"],
    13: ["what is RAG", "retrieval augmented generation"],
    14: ["wrong answers", "errors", "reliability", "AI mistakes"],
    15: ["hallucination", "making things up", "false information", "accuracy", "stop hallucinating"],
    16: ["human handoff", "transfer to a person", "live agent", "human takeover"],
    17: ["autonomous", "approval", "without human", "decisions on its own"],
    18: ["what does agentic mean", "agentic AI"],
    19: ["multiple agents", "several agents", "specialized agents"],
    20: ["multi-agent", "supervisor agent", "agents collaborating"],
    21: ["do we need AI", "normal automation", "Zapier", "rules-based", "alternative to AI"],
    22: ["how long", "timeline", "delivery time", "turnaround", "when will it be ready"],
    23: ["price", "pricing", "cost", "how much", "rates", "quote", "budget", "ballpark"],
    24: ["monthly fee", "subscription", "one-time payment", "billing", "maintenance fees"],
    25: ["ownership", "own the code", "intellectual property", "source code"],
    26: ["self host", "on-premise", "our own servers", "private cloud", "hosting"],
    27: ["maintenance", "after launch", "ongoing support", "updates"],
    28: ["monitoring", "logs", "tracking performance", "audit trail"],
    29: ["ROI", "success metrics", "measure results", "KPIs"],
    30: ["replace employees", "jobs", "layoffs", "staff", "workforce"],
    31: ["data safe", "security", "encryption", "privacy", "protect our data"],
    32: ["train on our data", "model training", "fine-tuning", "data usage"],
    33: ["WhatsApp", "WhatsApp Business API", "messaging"],
    34: ["voice agent", "phone calls", "call center", "speech"],
    35: ["send emails", "email automation", "follow-up emails"],
    36: ["CRM", "Salesforce", "HubSpot", "customer records", "leads"],
    37: ["limit actions", "restrict the agent", "guardrails", "control what AI can do"],
    38: ["don't know the answer", "fallback", "can't answer", "unknown questions"],
    39: ["are you a robot", "real person or AI", "human or bot", "talking to AI", "AI disclosure", "transparency"],
    40: ["company terminology", "jargon", "brand terms", "product names"],
    41: ["learn from conversations", "self-improving", "continuous learning", "memory"],
    42: ["tone", "personality", "brand voice", "style"],
    43: ["languages", "multilingual", "French", "Yoruba", "Hausa", "translation"],
    44: ["what do you need from us", "requirements", "get started", "information needed", "start a project"],
    45: ["process", "how it works", "steps", "what happens next", "onboarding", "discovery"],
}

# Entries that are instructions to the bot, not customer-facing answers
BOT_INSTRUCTION_IDS = set(range(46, 51))

SECTIONS = ("Internal Chatbot Rules", "Suggested Knowledge-Base Categories",
            "Recommended Chatbot Architecture")
SECTION_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(" + "|".join(SECTIONS) + r")\s*$", re.I)
GLUED_SECTION_RE = re.compile(r"(\S)(" + "|".join(SECTIONS) + r")\s*$")
Q_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(\d{1,3})[.)]\s+(.+?\?)\s*$")
INLINE_Q_RE = re.compile(r"^(.*\S)(\d{1,3})[.)]\s+([A-Z].{4,200}\?)\s*$")


def clean_line(raw: str) -> str:
    """Strip conversion noise from one line."""
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", raw)       # zero-width chars
    s = re.sub(r"\\(.)", r"\1", s)                            # \. \_ \- escapes
    s = s.replace("**", "")                                   # bold wrappers
    s = re.sub(r"^(\s*)[*\u2022\u25cf]\s*", r"\1- ", s)       # *, •, ● bullets at line start
    s = re.sub(r"\s*[\u2022\u25cf]\s*", "\n- ", s)            # glued bullets mid-line
    s = re.sub(r"^(\s*)- \s+", r"\1- ", s)
    return s.rstrip()


def parse(md: str):
    entries, sections = {}, {}
    cur_q = cur_section = None
    expected, prev = 1, ""
    # clean_line may emit several lines (glued bullets), so flatten first
    lines = []
    for raw in md.replace("\r\n", "\n").replace("\f", "\n").split("\n"):  # \f = PDF page break
        lines.extend(clean_line(raw).split("\n"))

    def add_to_current(text):
        if cur_section is not None:
            sections[cur_section].append(text)
        elif cur_q is not None:
            entries[cur_q]["lines"].append(text)

    for line in lines:
        # Section title glued to the end of the previous sentence
        g = GLUED_SECTION_RE.search(line)
        if g and not SECTION_RE.match(line):
            add_to_current(line[: g.start(2)].rstrip())
            line = g.group(2)

        sec = SECTION_RE.match(line)
        if sec:
            cur_section, cur_q = sec.group(1).lower(), None
            sections[cur_section] = []
            prev = line
            continue

        if cur_section is None:
            m = Q_RE.match(line)
            inline = None if m else INLINE_Q_RE.match(line)
            if inline and int(inline.group(2)) == expected:
                # "...permissions granted to it.3. What types of businesses?" -> split
                add_to_current(inline.group(1))
                m = re.match(r"^(\d+)[.)]\s+(.+)$", f"{inline.group(2)}. {inline.group(3)}")
            if m and int(m.group(1)) == expected:
                is_heading = line.lstrip().startswith("#") or inline is not None
                pm = re.match(r"^\s*(\d{1,3})[.)]\s", prev)
                continues_list = bool(pm and int(pm.group(1)) == int(m.group(1)) - 1)
                if is_heading or not continues_list:
                    cur_q = expected
                    entries[cur_q] = {"question": m.group(2).strip(), "lines": []}
                    expected += 1
                    prev = line
                    continue
        add_to_current(line)
        if line.strip():
            prev = line
    return entries, sections


def clean(lines):
    text = "\n".join(l.strip() for l in "\n".join(lines).split("\n")).strip()  # drop PDF indentation
    return re.sub(r"\n{3,}", "\n\n", text)


def split_long(answer: str):
    if len(answer.split()) <= MAX_WORDS:
        return [answer]
    parts, buf = [], []
    for para in answer.split("\n\n"):
        if buf and len(" ".join(buf + [para]).split()) > MAX_WORDS:
            parts.append("\n\n".join(buf))
            buf = []
        buf.append(para)
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def main(path: str):
    entries, sections = parse(Path(path).read_text(encoding="utf-8"))
    today = date.today().isoformat()
    chunks, bot_lines, warnings = [], ["# Bot behavior (from FAQ Q46-50)\n"], []

    for qid, e in entries.items():
        answer = clean(e["lines"])
        if qid in BOT_INSTRUCTION_IDS:
            bot_lines.append(f"## {e['question']}\n{answer}\n")
            continue
        if not answer:
            warnings.append(f"Q{qid}: empty answer")
            continue
        category, sub = CATEGORIES.get(qid, ("General", "General"))
        pieces = split_long(answer)
        kid = f"GRIT-AI-FAQ-{qid:03d}"
        for i, piece in enumerate(pieces, 1):
            chunks.append({
                "knowledge_id": kid,
                "chunk_id": f"{kid}-C{i:02d}",
                "document_id": kid,
                "chunk_index": i,
                "chunk_total": len(pieces),
                "title": e["question"],
                "question": e["question"],
                "answer": piece,
                "category": category,
                "subcategory": sub,
                "section": sub,
                "keywords": KEYWORDS.get(qid, []),
                "synonyms": [],
                "intent": "",
                "audience": ["customer", "prospect"],
                "status": "active",
                "source": SOURCE,
                "source_type": "official_company_document",
                "owner": OWNER,
                "version": "1.0",
                "created_at": today,
                "last_updated": today,
                "priority": PRIORITY.get(category, "medium"),
                "customer_visible": True,
                "internal_only": False,
                "requires_escalation": qid in ESCALATE,
                "requires_human_approval": False,
                # What the LLM is shown / what gets embedded:
                "text": f"{e['question']}\n{piece}",
                "embed_text": (f"[{category} > {sub}] {e['question']}\n{piece}"
                               + (f"\nKeywords: {', '.join(KEYWORDS[qid])}" if KEYWORDS.get(qid) else "")),
            })

    rules = clean(sections.get("internal chatbot rules", []))
    bot_lines.append("# Internal Chatbot Rules\n" + rules + "\n")
    notes = "\n\n".join(f"# {k.title()}\n{clean(v)}" for k, v in sections.items()
                        if k != "internal chatbot rules")

    for rel in ("kb", "prompts", "docs"):
        (ROOT / rel).mkdir(exist_ok=True)
    with open(ROOT / "kb/faq_chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (ROOT / "prompts/bot_rules.md").write_text("\n".join(bot_lines), encoding="utf-8")
    (ROOT / "docs/build_notes.md").write_text(notes, encoding="utf-8")

    # --- Sanity report ------------------------------------------------------
    found = sorted(entries)
    missing = [n for n in range(1, 51) if n not in entries]
    words = [len(c["text"].split()) for c in chunks]
    print(f"Questions found: {len(found)} (expected 50) | missing: {missing or 'none'}")
    print(f"Indexed chunks: {len(chunks)} | bot-instruction entries: {len(BOT_INSTRUCTION_IDS & set(found))}")
    if words:
        print(f"Chunk size (words): min {min(words)}, max {max(words)}, avg {sum(words)//len(words)}")
    print(f"Sections captured: {list(sections) or 'none'}")
    for w in warnings:
        print(f"WARNING: {w}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python src/chunk.py path/to/GRIT_AI.md")
    main(sys.argv[1])