import os
import re
import io
import json
import glob
import uuid
import logging
import traceback
from flask import Flask, request, jsonify, send_from_directory
from anthropic import Anthropic
from openai import OpenAI
from google import genai
from pypdf import PdfReader

app = Flask(__name__, static_folder="public", static_url_path="")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}\n{traceback.format_exc()}")
    return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {e}\n{traceback.format_exc()}")
    return jsonify({"error": f"Server error: {str(e)}"}), 500

DELEGATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Delegate_data")

ANTHROPIC_MODELS = {
    "claude-sonnet-4-20250514": "Claude Sonnet 4",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
}

OPENAI_MODELS = {
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o Mini",
    "gpt-4.1": "GPT-4.1",
    "gpt-4.1-mini": "GPT-4.1 Mini",
    "gpt-4.1-nano": "GPT-4.1 Nano",
}

GOOGLE_MODELS = {
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "gemini-2.0-flash": "Gemini 2.0 Flash",
}

# Cache clients by API key to avoid recreating on every request
_anthropic_clients = {}
_openai_clients = {}
_google_clients = {}


def get_anthropic_client(api_key):
    if api_key not in _anthropic_clients:
        _anthropic_clients[api_key] = Anthropic(api_key=api_key)
    return _anthropic_clients[api_key]


def get_openai_client(api_key):
    if api_key not in _openai_clients:
        _openai_clients[api_key] = OpenAI(api_key=api_key)
    return _openai_clients[api_key]


def get_google_client(api_key):
    if api_key not in _google_clients:
        _google_clients[api_key] = genai.Client(api_key=api_key)
    return _google_clients[api_key]


def chat_completion(system_prompt, messages, max_tokens, provider_config):
    """Unified chat completion that works with both Anthropic and OpenAI."""
    provider = provider_config.get("provider", "anthropic")
    api_key = provider_config.get("api_key", "")
    model = provider_config.get("model", "")

    if not api_key:
        raise ValueError(f"No API key provided for {provider}")

    if provider == "anthropic":
        client = get_anthropic_client(api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text.strip()

    elif provider == "openai":
        client = get_openai_client(api_key)
        # OpenAI format: system message + user/assistant messages
        oai_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            oai_messages.append({"role": msg["role"], "content": msg["content"]})
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=oai_messages,
        )
        return response.choices[0].message.content.strip()

    elif provider == "google":
        client = get_google_client(api_key)
        # Google Gemini format: system instruction + contents
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(genai.types.Content(
                role=role,
                parts=[genai.types.Part(text=msg["content"])],
            ))
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=max_tokens,
            ),
        )
        return response.text.strip()

    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_provider_config(data):
    """Extract provider config from request data."""
    return {
        "provider": data.get("provider", "anthropic"),
        "api_key": data.get("api_key", ""),
        "model": data.get("model", "claude-sonnet-4-20250514"),
    }


SHORT_BIOS = {
    # Economists
    "adam_smith": "Scottish economist and moral philosopher, known as the Father of Capitalism.",
    "david_ricardo": "Wealthy British political economist, stockbroker, and Member of Parliament.",
    "elinor_ostrom": "American political economist and first woman to win the Nobel Prize in Economics.",
    "friedrich_hayek": "Nobel Prize-winning Austrian-British economist and philosopher.",
    "john_maynard_keynes": "British economist who revolutionized macroeconomics during the Great Depression.",
    "milton_friedman": "Nobel Prize-winning American economist and leader of the Chicago School.",
    "thomas_sowell": "Prolific American economist, social theorist, and Chicago School intellectual.",
    # Founding Fathers
    "alexander_hamilton": "First Secretary of the Treasury and champion of a strong federal government.",
    "benjamin_franklin": "American polymath, scientist, diplomat, publisher, and elder statesman.",
    "george_washington": "Commander-in-Chief of the Continental Army and first President of the United States.",
    "james_madison": "Father of the Constitution and principal author of the Bill of Rights.",
    "john_adams": "Leading Founding Father, diplomat, and second President of the United States.",
    "thomas_jefferson": "Author of the Declaration of Independence and third President of the United States.",
    # Futurists
    "isaac_asimov": "Prolific American author, biochemist, and grandfather of modern science fiction.",
    # Jurists
    "antonin_scalia": "Combative Supreme Court Justice who championed Originalism and Textualism.",
    "clarence_thomas": "Supreme Court Justice known as the most staunch originalist on the modern court.",
    "earl_warren": "14th Chief Justice who presided over one of the most transformative Supreme Courts.",
    "john_jay": "Founding Father, co-author of The Federalist Papers, and first Chief Justice.",
    "john_marshall": "Fourth Chief Justice, widely considered the most influential jurist in American history.",
    "louis_brandeis": "Pioneering Supreme Court Justice known as The People's Lawyer.",
    "oliver_wendell_holmes_jr": "Civil War veteran and one of the most influential American jurists of the 20th century.",
    "ruth_bader_ginsburg": "Pioneering feminist litigator and Supreme Court Justice, the Notorious R.B.G.",
    # Philosophers
    "aristotle": "Ancient Greek philosopher, polymath, and founder of the Lyceum.",
    "buddha": "The Awakened One, founder of Buddhism and teacher of the Middle Way.",
    "confucius": "Ancient Chinese philosopher and founder of Confucianism.",
    "edmund_burke": "Irish statesman and foundational philosopher of modern conservatism.",
    "frantz_fanon": "Martinican psychiatrist, philosopher, and anti-colonial revolutionary.",
    "friedrich_nietzsche": "Provocative German philosopher, cultural critic, and philologist.",
    "immanuel_kant": "Central figure in modern philosophy, known for deontological ethics.",
    "jean_jacques_rousseau": "Genevan philosopher whose radical ideas helped inspire the French Revolution.",
    "john_locke": "The Father of Liberalism, foundational Enlightenment philosopher.",
    "john_rawls": "Preeminent American political philosopher who revived the social contract tradition.",
    "john_stuart_mill": "Brilliant British philosopher, political economist, and champion of utilitarianism.",
    "karl_marx": "Revolutionary German philosopher, economist, and author of Das Kapital.",
    "martha_nussbaum": "Prominent contemporary philosopher specializing in political philosophy and ethics.",
    "mary_wollstonecraft": "Pioneering English writer and founding mother of modern Western feminism.",
    "niccolo_machiavelli": "Florentine diplomat and father of modern political realism.",
    "plato": "Ancient Athenian philosopher, founder of the Academy, and student of Socrates.",
    "simone_de_beauvoir": "Influential French existentialist philosopher, feminist theorist, and author.",
    "socrates": "Foundational figure of Western moral philosophy.",
    "soren_kierkegaard": "Danish philosopher and theologian, widely considered the father of existentialism.",
    "thomas_hobbes": "English philosopher best known for his masterpiece, Leviathan.",
    "vine_deloria_jr": "Standing Rock Sioux author and activist who reshaped understanding of Native American history.",
    # Political Leaders and Reformers
    "abraham_lincoln": "16th President who led the nation through the Civil War and abolished slavery.",
    "barry_goldwater": "Arizona Senator and intellectual father of the modern American conservative movement.",
    "franklin_roosevelt": "32nd President who led America through the Great Depression and World War II.",
    "frederick_douglass": "Formerly enslaved abolitionist, orator, and most prominent Black American of his era.",
    "john_f_kennedy": "35th President, representing the idealism and challenges of the mid-20th century.",
    "lyndon_b_johnson": "36th President, Master of the Senate, and architect of the Great Society.",
    "martin_luther_king_jr": "Baptist minister and leader of the civil rights movement.",
    "ronald_reagan": "40th President, the Great Communicator, and architect of modern conservative politics.",
    "susan_b_anthony": "Fierce American social reformer, abolitionist, and champion of women's suffrage.",
    "theodore_roosevelt": "26th President, driving force of the Progressive Era, explorer, and naturalist.",
}


def parse_delegate_file(filepath):
    """Parse a delegate .md file into structured data."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    name_match = re.search(r"#\s*Agent Persona Profiling Script:\s*(.+)", content)
    name = name_match.group(1).strip() if name_match else os.path.splitext(os.path.basename(filepath))[0].replace("_", " ").title()

    delegate_id = os.path.splitext(os.path.basename(filepath))[0]
    display_bio = SHORT_BIOS.get(delegate_id, "")
    if not display_bio:
        # Fallback for delegates not in the lookup (e.g. future additions)
        identity_match = re.search(r"\*\*Identity:\*\*\s*(.+?)(?:\n|$)", content)
        bio = identity_match.group(1).strip() if identity_match else ""
        display_bio = re.sub(r"^You are\s+", "", bio)
        if len(display_bio) > 120:
            display_bio = display_bio[:117].rsplit(" ", 1)[0] + "."

    philosophy_section = ""
    phil_match = re.search(r"## 2\. Core Philosophy.*?\n(.*?)(?=## 3\.)", content, re.DOTALL)
    if phil_match:
        bullets = re.findall(r"\*\*\s*(.+?)\s*(?::\*\*|\*\*:)", phil_match.group(1))
        philosophy_section = "; ".join(bullets)

    rel_path = os.path.relpath(filepath, DELEGATE_DIR)
    category = os.path.dirname(rel_path).replace("_", " ").title()

    return {
        "id": os.path.splitext(os.path.basename(filepath))[0],
        "name": name,
        "bio": display_bio,
        "leanings": philosophy_section,
        "category": category,
        "file_path": filepath,
        "full_content": content,
    }


def load_all_delegates():
    delegates = []
    for md_file in sorted(glob.glob(os.path.join(DELEGATE_DIR, "**", "*.md"), recursive=True)):
        try:
            delegates.append(parse_delegate_file(md_file))
        except Exception as e:
            print(f"Error parsing {md_file}: {e}")
    return delegates


ALL_DELEGATES = load_all_delegates()
DELEGATE_MAP = {d["id"]: d for d in ALL_DELEGATES}

# Custom delegates created at runtime (per-session, stored in memory)
CUSTOM_DELEGATES = []


@app.route("/")
def index():
    return send_from_directory("public", "index.html")


@app.route("/api/delegates")
def get_delegates():
    all_dels = ALL_DELEGATES + CUSTOM_DELEGATES
    return jsonify([
        {
            "id": d["id"],
            "name": d["name"],
            "bio": d["bio"],
            "leanings": d["leanings"],
            "category": d["category"],
            "custom": d.get("custom", False),
        }
        for d in all_dels
    ])


@app.route("/api/models")
def get_models():
    return jsonify({
        "anthropic": ANTHROPIC_MODELS,
        "openai": OPENAI_MODELS,
        "google": GOOGLE_MODELS,
    })


@app.route("/api/auto-select", methods=["POST"])
def auto_select():
    data = request.json
    prompt = data.get("prompt", "")
    count = data.get("count", 5)
    provider_config = get_provider_config(data)

    all_dels = ALL_DELEGATES + CUSTOM_DELEGATES
    delegate_summaries = "\n".join(
        f"- {d['id']}: {d['name']} ({d['category']}) — {d['leanings']}"
        for d in all_dels
    )

    try:
        text = chat_completion(
            system_prompt="You are a convention organizer. Given a topic and a list of available delegates, select the most relevant and diverse set of delegates who would produce the most insightful debate. Return ONLY a JSON array of delegate IDs. No other text.",
            messages=[{
                "role": "user",
                "content": f"Topic: {prompt}\n\nSelect exactly {count} delegates from this list:\n{delegate_summaries}\n\nReturn a JSON array of delegate IDs, e.g. [\"thomas_jefferson\", \"john_rawls\"]"
            }],
            max_tokens=1024,
            provider_config=provider_config,
        )
        json_match = re.search(r"\[.*\]", text, re.DOTALL)
        if json_match:
            selected_ids = json.loads(json_match.group())
            selected_ids = [sid for sid in selected_ids if sid in DELEGATE_MAP]
            return jsonify({"selected": selected_ids})
        return jsonify({"selected": [], "error": "Could not parse AI response"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def lookup_delegate(delegate_id, client_custom_delegates=None):
    """Look up a delegate by ID from built-in, server custom, or client-provided custom delegates."""
    if delegate_id in DELEGATE_MAP:
        return DELEGATE_MAP[delegate_id]
    for d in CUSTOM_DELEGATES:
        if d["id"] == delegate_id:
            return d
    # Fall back to client-provided custom delegates (stateless support)
    if client_custom_delegates:
        for d in client_custom_delegates:
            if d.get("id") == delegate_id:
                return d
    return None


def build_delegate_name_map(delegate_ids, client_custom_delegates=None):
    """Build a mapping of delegate IDs to names."""
    names = {}
    for did in delegate_ids:
        d = lookup_delegate(did, client_custom_delegates)
        if d:
            names[did] = d["name"]
    return names


# Rough estimate: 1 token ≈ 4 characters
MAX_INPUT_CHARS = 120000  # ~30k tokens, leaves room for system prompt + output


def condense_transcript(history, delegate_names, max_chars=MAX_INPUT_CHARS):
    """If the full transcript exceeds max_chars, summarize early turns and keep recent ones verbatim.

    Returns a string combining a summary block (if needed) and the recent verbatim turns.
    """
    # Build the full transcript
    full_entries = []
    for e in history:
        name = delegate_names.get(e["delegate_id"], e["delegate_id"])
        full_entries.append(f"**{name}:** {e['text']}")

    full_text = "\n\n".join(full_entries)
    if len(full_text) <= max_chars:
        return full_text

    # Need to split: keep recent turns verbatim, summarize earlier ones
    # Work backwards to find how many recent turns fit in ~60% of budget
    recent_budget = int(max_chars * 0.6)
    recent_entries = []
    recent_len = 0
    for entry_text in reversed(full_entries):
        if recent_len + len(entry_text) + 2 > recent_budget:
            break
        recent_entries.insert(0, entry_text)
        recent_len += len(entry_text) + 2

    # Summarize the earlier turns that didn't fit
    split_idx = len(full_entries) - len(recent_entries)
    early_entries = full_entries[:split_idx]

    # Build a condensed summary of early entries — extract key points per speaker
    summary_lines = []
    speaker_points = {}
    for entry_text in early_entries:
        # Parse "**Name:** text"
        match = re.match(r"\*\*(.+?):\*\*\s*(.*)", entry_text, re.DOTALL)
        if match:
            name, text = match.group(1), match.group(2)
            if name not in speaker_points:
                speaker_points[name] = []
            # Take first 200 chars of each turn as a summary
            speaker_points[name].append(text[:200].strip() + ("..." if len(text) > 200 else ""))

    for name, points in speaker_points.items():
        summary_lines.append(f"**{name}** argued: " + " | ".join(points))

    summary_text = "\n".join(summary_lines)
    # Trim summary if still too long
    summary_budget = max_chars - recent_len - 200
    if len(summary_text) > summary_budget:
        summary_text = summary_text[:summary_budget] + "\n[...earlier arguments truncated...]"

    recent_text = "\n\n".join(recent_entries)

    return (
        f"=== SUMMARY OF EARLIER DEBATE (turns 1-{split_idx}) ===\n"
        f"{summary_text}\n\n"
        f"=== RECENT DEBATE (turns {split_idx + 1}-{len(full_entries)}) ===\n"
        f"{recent_text}"
    )


def condense_history_messages(history, delegate_names, max_chars=MAX_INPUT_CHARS):
    """For debate turns: condense the message list if it's too long.

    Returns a list of message dicts for the chat API.
    """
    # Build full messages and measure total size
    full_messages = []
    total_len = 0
    for entry in history:
        speaker = delegate_names.get(entry["delegate_id"], entry["delegate_id"])
        content = f"[{speaker}]: {entry['text']}"
        full_messages.append({"role": "user", "content": content})
        total_len += len(content)

    if total_len <= max_chars:
        return full_messages

    # Keep recent messages verbatim, summarize earlier ones into a single message
    recent_budget = int(max_chars * 0.6)
    recent_messages = []
    recent_len = 0
    for msg in reversed(full_messages):
        if recent_len + len(msg["content"]) + 2 > recent_budget:
            break
        recent_messages.insert(0, msg)
        recent_len += len(msg["content"]) + 2

    split_idx = len(full_messages) - len(recent_messages)
    early_messages = full_messages[:split_idx]

    # Condense early messages into a summary
    summary_parts = []
    for msg in early_messages:
        text = msg["content"]
        # Truncate each entry
        if len(text) > 250:
            text = text[:250] + "..."
        summary_parts.append(text)

    summary = (
        f"[SUMMARY OF EARLIER DEBATE - turns 1 through {split_idx}]\n"
        + "\n".join(summary_parts)
    )
    # Trim if needed
    summary_budget = max_chars - recent_len - 200
    if len(summary) > summary_budget:
        summary = summary[:summary_budget] + "\n[...truncated...]"

    return [{"role": "user", "content": summary}] + recent_messages


def ensure_complete_sentence(text):
    """If text was cut off mid-sentence by the token limit, end it cleanly.

    Looks for a final sentence-ending punctuation (.!?") and either
    truncates to that point or appends an em dash to indicate interruption.
    """
    text = text.strip()
    if not text:
        return text
    # Already ends with sentence-ending punctuation — all good
    if text[-1] in '.!?""\u201d':
        return text
    # Check if the last char is a closing quote/paren after punctuation
    if len(text) >= 2 and text[-2] in '.!?' and text[-1] in ')\u201d"\'':
        return text
    # Find the last sentence-ending punctuation
    last_period = -1
    for i in range(len(text) - 1, -1, -1):
        if text[i] in '.!?':
            # Make sure it's not an abbreviation (e.g. "Dr." or "U.S.")
            # Simple heuristic: if followed by a space and uppercase, it's a sentence end
            if i == len(text) - 1 or (i + 1 < len(text) and text[i + 1] in ' \n""\u201d'):
                last_period = i
                break
    if last_period > len(text) * 0.7:
        # The last complete sentence is reasonably close to the end — truncate there
        return text[:last_period + 1]
    else:
        # The cutoff happened too far from any sentence end — append em dash
        # Trim trailing whitespace and partial words
        trimmed = text.rstrip()
        return trimmed + "\u2014"


@app.route("/api/debate/turn", methods=["POST"])
def debate_turn():
    data = request.json
    prompt = data.get("prompt", "")
    delegate_id = data.get("delegate_id", "")
    history = data.get("history", [])
    delegate_ids = data.get("all_delegate_ids", [])
    turn_number = data.get("turn_number", 0)
    total_turns = data.get("total_turns", 15)
    reference_document = data.get("reference_document", "")
    custom_delegates = data.get("custom_delegates", [])
    provider_config = get_provider_config(data)

    delegate = lookup_delegate(delegate_id, custom_delegates)
    if not delegate:
        return jsonify({"error": f"Unknown delegate: {delegate_id}"}), 400

    delegate_names = build_delegate_name_map(delegate_ids, custom_delegates)

    # Build reference document context if provided
    doc_context = ""
    if reference_document:
        doc_context = f"""

REFERENCE DOCUMENT:
The convention has been provided with the following document for evaluation and discussion:
---
{reference_document[:15000]}
---
You should reference specific parts of this document in your arguments where relevant.
"""

    system_prompt = f"""{delegate['full_content']}

---
CONVENTION CONTEXT:
You are participating in a constitutional convention. The topic under deliberation is:
"{prompt}"
{doc_context}
Other delegates present: {', '.join(delegate_names[did] for did in delegate_ids if did != delegate_id)}

This is turn {turn_number + 1} of approximately {total_turns} total turns in the debate.

INSTRUCTIONS:
- Stay completely in character as {delegate['name']}.
- Respond to specific points made by other delegates — agree, disagree, build upon, or challenge their arguments.
- Do NOT simply give a monologue. Engage directly with what others have said.
- Keep your response to 1-4 paragraphs. Be substantive but concise. Shorter responses are fine when the point is simple.
- If this is your first time speaking, introduce your position on the topic.
- OUTPUT ONLY THE WORDS YOU SPEAK. Do NOT include any narration, stage directions, action descriptions, or third-person commentary (e.g. no "*leans forward*", "*adjusts spectacles*", "*pauses thoughtfully*", "he said firmly", etc.). Write only direct speech as if you are speaking aloud at the convention.
- {"As this is one of the final turns, begin working toward areas of potential compromise or agreement where possible, while still staying true to your principles." if turn_number >= total_turns - 4 else ""}
"""

    messages = condense_history_messages(history, delegate_names)
    messages.append({
        "role": "user",
        "content": f"It is now your turn to speak, {delegate['name']}. Address the convention on the topic and respond to any points raised by other delegates."
    })

    try:
        text = chat_completion(
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=2048,
            provider_config=provider_config,
        )
        text = ensure_complete_sentence(text)
        return jsonify({"text": text, "delegate_id": delegate_id, "name": delegate["name"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debate/document", methods=["POST"])
def generate_document():
    data = request.json
    prompt = data.get("prompt", "")
    history = data.get("history", [])
    delegate_ids = data.get("all_delegate_ids", [])
    reference_document = data.get("reference_document", "")
    custom_delegates = data.get("custom_delegates", [])
    provider_config = get_provider_config(data)

    delegate_names = build_delegate_name_map(delegate_ids, custom_delegates)

    transcript = condense_transcript(history, delegate_names)

    doc_context = ""
    if reference_document:
        doc_context = f"\n\nREFERENCE DOCUMENT PROVIDED TO THE CONVENTION:\n---\n{reference_document[:15000]}\n---\n"

    system_prompt = """You are a skilled constitutional drafter and political scribe. Your task is to synthesize a deliberative debate into a formal document.

Read the full transcript of the convention debate below. Identify the areas of consensus, key principles agreed upon, notable compromises, and any unresolved disagreements.

Produce a formal document (a resolution, constitution, framework, or set of principles — whatever format best suits the topic). The document should:
1. Have a preamble explaining the purpose and context
2. Be organized into clearly numbered articles or sections
3. Reflect the actual substance of the debate — not generic platitudes
4. Note where compromises were reached and what the competing views were
5. Include a dissent or minority opinion section if there were fundamental disagreements

IMPORTANT: Keep the document under 2000 words. Be thorough but concise. Every sentence must be complete.

Write in a formal, dignified tone appropriate for a constitutional or legislative document."""

    try:
        text = chat_completion(
            system_prompt=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Topic of the convention: {prompt}\n{doc_context}\nFull debate transcript:\n\n{transcript}\n\nNow produce the final document. Keep it under 2000 words."
            }],
            max_tokens=4096,
            provider_config=provider_config,
        )
        text = ensure_complete_sentence(text)
        return jsonify({"document": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debate/progress-document", methods=["POST"])
def progress_document():
    data = request.json
    prompt = data.get("prompt", "")
    history = data.get("history", [])
    delegate_ids = data.get("all_delegate_ids", [])
    reference_document = data.get("reference_document", "")
    custom_delegates = data.get("custom_delegates", [])
    provider_config = get_provider_config(data)

    delegate_names = build_delegate_name_map(delegate_ids, custom_delegates)

    transcript = condense_transcript(history, delegate_names)

    doc_context = ""
    if reference_document:
        doc_context = f"\n\nReference document provided:\n---\n{reference_document[:10000]}\n---\n"

    system_prompt = """You are a convention clerk taking notes and drafting an emerging document based on the debate so far.

Based on the debate transcript provided, produce a WORKING DRAFT of the emerging document. This is not the final version — the debate is still ongoing. Mark areas of consensus as firm, and areas still under debate as tentative.

Format as a brief, evolving outline with:
- Points of emerging consensus (marked as agreed)
- Points still under active debate (marked as [UNDER DEBATE])
- Key tensions identified

Keep it concise — this is a progress snapshot, not the final document. Use 200-400 words."""

    try:
        text = chat_completion(
            system_prompt=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Topic: {prompt}\n{doc_context}\nDebate so far:\n\n{transcript}"
            }],
            max_tokens=1024,
            provider_config=provider_config,
        )
        return jsonify({"document": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload-document", methods=["POST"])
def upload_document():
    """Extract text from an uploaded file (PDF, TXT, MD)."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    filename = file.filename.lower()

    try:
        if filename.endswith(".pdf"):
            reader = PdfReader(io.BytesIO(file.read()))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        elif filename.endswith((".txt", ".md")):
            text = file.read().decode("utf-8")
        else:
            return jsonify({"error": "Unsupported file type. Please upload PDF, TXT, or MD files."}), 400

        return jsonify({"text": text.strip(), "filename": file.filename})
    except Exception as e:
        return jsonify({"error": f"Failed to read file: {str(e)}"}), 500


@app.route("/api/create-delegate", methods=["POST"])
def create_delegate():
    """Create a custom delegate from source material using AI."""
    data = request.json
    name = data.get("name", "").strip()
    source_text = data.get("source_text", "").strip()
    provider_config = get_provider_config(data)

    if not name:
        return jsonify({"error": "Delegate name is required"}), 400
    if not source_text:
        return jsonify({"error": "Source material is required"}), 400

    # Use AI to generate a delegate persona in the same format as existing delegates
    system_prompt = """You are an expert at creating detailed AI persona profiles for historical and intellectual figures.

Given source material about a person, create a comprehensive persona profile in EXACTLY this markdown format:

# Agent Persona Profiling Script: [Full Name]

## 1. System Prompt / Persona Identity
**Identity:** You are [Full Name] ([birth-death years if applicable]), [brief description of who they are and what they're known for].
**Core Instruction:** NEVER BREAK CHARACTER. [2-3 sentences about how they approach debates and what drives them.]

## 2. Core Philosophy & Worldview
* **[Key Belief 1]:** [Detailed explanation]
* **[Key Belief 2]:** [Detailed explanation]
* **[Key Belief 3]:** [Detailed explanation]

## 3. Role in a Constitutional Convention
* [How they would contribute to debates]
* [What unique perspective they bring]
* [How they interact with opposing views]

## 4. Key Political and Economic Stances
* **View on [Topic 1]:** [Their position]
* **View on [Topic 2]:** [Their position]
* **View on [Topic 3]:** [Their position]

## 5. Communication Style & Tone
* **Tone:** [Description of how they speak]
* **Technique:** [How they argue and persuade]
* **Vocabulary:** [Characteristic phrases and terms they use]

## 6. Detailed Sources & Citations
* [Key works, writings, speeches they draw from]

Make the persona vivid, detailed, and faithful to the actual views and style of the person. The profile should allow an AI to authentically roleplay this person in a constitutional debate."""

    try:
        persona_content = chat_completion(
            system_prompt=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Create a detailed delegate persona profile for: {name}\n\nSource material:\n{source_text[:12000]}"
            }],
            max_tokens=4096,
            provider_config=provider_config,
        )

        # Parse the generated content to extract bio and leanings
        delegate_id = f"custom_{uuid.uuid4().hex[:8]}"

        identity_match = re.search(r"\*\*Identity:\*\*\s*(.+?)(?:\n|$)", persona_content)
        bio = identity_match.group(1).strip() if identity_match else f"Custom delegate: {name}"
        display_bio = re.sub(r"^You are\s+", "", bio)

        philosophy_section = ""
        phil_match = re.search(r"## 2\. Core Philosophy.*?\n(.*?)(?=## 3\.)", persona_content, re.DOTALL)
        if phil_match:
            bullets = re.findall(r"\*\*\s*(.+?)\s*(?::\*\*|\*\*:)", phil_match.group(1))
            philosophy_section = "; ".join(bullets)

        delegate = {
            "id": delegate_id,
            "name": name,
            "bio": display_bio,
            "leanings": philosophy_section,
            "category": "Custom",
            "full_content": persona_content,
            "custom": True,
        }

        CUSTOM_DELEGATES.append(delegate)
        DELEGATE_MAP[delegate_id] = delegate

        return jsonify({
            "id": delegate_id,
            "name": name,
            "bio": display_bio,
            "leanings": philosophy_section,
            "category": "Custom",
            "custom": True,
            "full_content": persona_content,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def create_app():
    """App factory for gunicorn."""
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    print(f"\n  Constitutional Convention Simulator")
    print(f"  Running at http://localhost:{port}")
    print(f"  Loaded {len(ALL_DELEGATES)} delegates from {DELEGATE_DIR}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
