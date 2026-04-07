import os
import re
import io
import json
import glob
import uuid
from flask import Flask, request, jsonify, send_from_directory
from anthropic import Anthropic
from openai import OpenAI
from google import genai
from pypdf import PdfReader

app = Flask(__name__, static_folder="public", static_url_path="")

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


def parse_delegate_file(filepath):
    """Parse a delegate .md file into structured data."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    name_match = re.search(r"#\s*Agent Persona Profiling Script:\s*(.+)", content)
    name = name_match.group(1).strip() if name_match else os.path.splitext(os.path.basename(filepath))[0].replace("_", " ").title()

    identity_match = re.search(r"\*\*Identity:\*\*\s*(.+?)(?:\n|$)", content)
    bio = identity_match.group(1).strip() if identity_match else ""
    display_bio = re.sub(r"^You are\s+", "", bio)

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


def lookup_delegate(delegate_id):
    """Look up a delegate by ID from built-in or custom delegates."""
    if delegate_id in DELEGATE_MAP:
        return DELEGATE_MAP[delegate_id]
    for d in CUSTOM_DELEGATES:
        if d["id"] == delegate_id:
            return d
    return None


def build_delegate_name_map(delegate_ids):
    """Build a mapping of delegate IDs to names."""
    names = {}
    for did in delegate_ids:
        d = lookup_delegate(did)
        if d:
            names[did] = d["name"]
    return names


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
    provider_config = get_provider_config(data)

    delegate = lookup_delegate(delegate_id)
    if not delegate:
        return jsonify({"error": f"Unknown delegate: {delegate_id}"}), 400

    delegate_names = build_delegate_name_map(delegate_ids)

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
- Keep your response to 2-4 paragraphs. Be substantive but concise.
- If this is your first time speaking, introduce your position on the topic.
- OUTPUT ONLY THE WORDS YOU SPEAK. Do NOT include any narration, stage directions, action descriptions, or third-person commentary (e.g. no "*leans forward*", "*adjusts spectacles*", "*pauses thoughtfully*", "he said firmly", etc.). Write only direct speech as if you are speaking aloud at the convention.
- {"As this is one of the final turns, begin working toward areas of potential compromise or agreement where possible, while still staying true to your principles." if turn_number >= total_turns - 4 else ""}
"""

    messages = []
    for entry in history:
        speaker = delegate_names.get(entry["delegate_id"], entry["delegate_id"])
        messages.append({
            "role": "user",
            "content": f"[{speaker}]: {entry['text']}"
        })
    messages.append({
        "role": "user",
        "content": f"It is now your turn to speak, {delegate['name']}. Address the convention on the topic and respond to any points raised by other delegates."
    })

    try:
        text = chat_completion(
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=1024,
            provider_config=provider_config,
        )
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
    provider_config = get_provider_config(data)

    delegate_names = build_delegate_name_map(delegate_ids)

    transcript = "\n\n".join(
        f"**{delegate_names.get(e['delegate_id'], e['delegate_id'])}:** {e['text']}"
        for e in history
    )

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

Write in a formal, dignified tone appropriate for a constitutional or legislative document."""

    try:
        text = chat_completion(
            system_prompt=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Topic of the convention: {prompt}\n{doc_context}\nFull debate transcript:\n\n{transcript}\n\nNow produce the final document."
            }],
            max_tokens=4096,
            provider_config=provider_config,
        )
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
    provider_config = get_provider_config(data)

    delegate_names = build_delegate_name_map(delegate_ids)

    transcript = "\n\n".join(
        f"**{delegate_names.get(e['delegate_id'], e['delegate_id'])}:** {e['text']}"
        for e in history
    )

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
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    print(f"\n  Constitutional Convention Simulator")
    print(f"  Running at http://localhost:{port}")
    print(f"  Loaded {len(ALL_DELEGATES)} delegates from {DELEGATE_DIR}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
