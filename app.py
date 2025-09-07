# app.py
import os
import sqlite3
import uuid
import base64
import asyncio
from datetime import datetime
from io import BytesIO

import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, abort

app = Flask(__name__)

# Load .env using open()
A4F_API_KEY = ""
try:
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("A4F_API_KEY="):
                    A4F_API_KEY = line.split("=", 1)[1].strip()
except Exception:
    A4F_API_KEY = os.environ.get("A4F_API_KEY", "")

DB_PATH = "compliments.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS compliments (
                id TEXT PRIMARY KEY,
                name TEXT,
                mood TEXT,
                compliment TEXT,
                image BLOB,
                timestamp TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

init_db()

def db_insert(compliment_id, name, mood, compliment_text, image_bytes):
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO compliments (id, name, mood, compliment, image, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (compliment_id, name, mood, compliment_text, image_bytes, datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

def db_get(compliment_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, name, mood, compliment, image, timestamp FROM compliments WHERE id=?",
            (compliment_id,),
        )
        row = c.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "mood": row[2],
            "compliment": row[3],
            "image": row[4],
            "timestamp": row[5],
        }
    finally:
        conn.close()

def generate_compliment(name, mood):
    if not A4F_API_KEY:
        raise RuntimeError("Missing A4F_API_KEY")
    # System prompt and examples to constrain output
    system_prompt = (
        "You generate a single-sentence compliment in the requested mood. "
        "Rules: 1) Output ONLY the compliment sentence. 2) Max 28 words. "
        "3) Natural, specific imagery. 4) No disclaimers, no options, no extra text. "
        "5) If a name is provided, weave it smoothly. 6) No emojis."
    )
    examples = [
        {"mood": "uplifting", "name": "Akhilesh", "out": "Akhilesh, your curiosity lights up challenges like sunrise dissolving fog—quiet and unstoppable."},
        {"mood": "funny", "name": "", "out": "You’re the Wi‑Fi everyone wants—strong signal, zero buffering, and somehow always finds the password."},
        {"mood": "poetic", "name": "Meera", "out": "Meera, your kindness moves like rain on parched soil—soft, steady, and life-brightening."},
        {"mood": "motivational", "name": "", "out": "Your grit turns detours into maps—keep going, the road is bowing to your pace."},
        {"mood": "romantic", "name": "Ravi", "out": "Ravi, your presence feels like warm chai on a monsoon evening—comforting, vivid, and impossible to rush."},
    ]

    user_instruction = f"Mood: {mood}. Name: {name or 'None'}."

    payload = {
        "model": "provider-3/gemini-2.0-flash",
        "messages": [
            {"role": "system", "content": system_prompt + " Examples: " + str(examples)},
            {"role": "user", "content": user_instruction},
        ],
        "temperature": 0.8,
        "max_tokens": 80,
    }

    resp = requests.post(
        "https://api.a4f.co/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {A4F_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"A4F text API error: {resp.status_code} {resp.text}")
    data = resp.json()
    # Expect OpenAI-like structure
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    text = (text or "").strip().strip('"').strip()
    if not text:
        raise RuntimeError("Empty compliment from model")
    return text

def generate_image(mood, compliment_text):
    if not A4F_API_KEY:
        raise RuntimeError("Missing A4F_API_KEY")
    # Construct a concise prompt matching the mood
    art_style = {
        "uplifting": "soft sunrise gradients, gentle light beams, airy bokeh, pastel palette",
        "funny": "bold pop-art, playful doodles, vibrant colors, dynamic shapes",
        "poetic": "ink-wash textures, subtle grain, muted tones, minimalist composition",
        "motivational": "clean geometric lines, high-contrast light, energetic hues",
        "romantic": "warm tones, soft focus, floral motifs, cinematic vignette",
        "witty": "neon accents, abstract shapes, quirky contrasts",
        "gentle": "calming blues, soft watercolor blends, minimal noise",
        "bold": "striking contrast, sharp lines, deep shadows",
        "quirky": "hand-drawn motifs, surprising color pairings",
        "serene": "misty gradients, spacious negative space, cool palette",
    }
    style = art_style.get(mood.lower(), "expressive yet minimal, modern, soft gradients")
    image_prompt = f"Background art only. {style}. No text. Complement this compliment vibe: '{compliment_text}'."

    payload = {
        "model": "provider-4/imagen-3",
        "prompt": image_prompt,
        "size": "1024x1024",
        "response_format": "b64_json",
    }

    resp = requests.post(
        "https://api.a4f.co/v1/images/generations",
        headers={
            "Authorization": f"Bearer {A4F_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"A4F image API error: {resp.status_code} {resp.text}")
    data = resp.json()
    items = data.get("data", [])
    if not items:
        raise RuntimeError("Empty image response")
    b64 = items[0].get("b64_json", "")
    if not b64:
        raise RuntimeError("Missing image base64")
    return base64.b64decode(b64)

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = (data.get("name") or "").strip()
        mood = (data.get("mood") or "uplifting").strip()
        if not mood:
            mood = "uplifting"

        compliment_text = generate_compliment(name, mood)
        image_bytes = generate_image(mood, compliment_text)
        compliment_id = uuid.uuid4().hex[:12]

        try:
            db_insert(compliment_id, name, mood, compliment_text, image_bytes)
        except Exception as db_err:
            return jsonify({"ok": False, "error": "DB_ERROR", "message": str(db_err)}), 500

        return jsonify({"ok": True, "redirect_url": url_for("view_compliment", compliment_id=compliment_id)})

    except RuntimeError as api_err:
        return jsonify({"ok": False, "error": "A4F_API_ERROR", "message": str(api_err)}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": "UNKNOWN", "message": str(e)}), 500

@app.route("/view/<compliment_id>", methods=["GET"])
def view_compliment(compliment_id):
    row = db_get(compliment_id)
    if not row:
        abort(404)
    return render_template(
        "view.html",
        compliment_id=row["id"],
        mood=row["mood"],
        compliment=row["compliment"],
        name=row["name"] or "",
    )

@app.route("/image/<compliment_id>", methods=["GET"])
def get_image(compliment_id):
    row = db_get(compliment_id)
    if not row:
        abort(404)
    img_bytes = row["image"]
    if not img_bytes:
        abort(404)
    return send_file(BytesIO(img_bytes), mimetype="image/png")

# TTS using edge-tts (Python)
@app.route("/tts", methods=["POST"])
def tts():
    try:
        data = request.get_json(force=True, silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            # fallback by id
            cid = (data.get("compliment_id") or "").strip()
            row = db_get(cid) if cid else None
            text = (row.get("compliment") if row else "").strip()
        if not text:
            return jsonify({"ok": False, "error": "NO_TEXT"}), 400

        voice = data.get("voice") or "en-US-AriaNeural"
        rate = data.get("rate") or "+0%"
        pitch = data.get("pitch") or "+0Hz"

        # Synthesize with edge-tts
        import edge_tts  # Requires `pip install edge-tts`

        async def synth(ssml_text):
            communicate = edge_tts.Communicate(ssml_text, voice=voice, rate=rate, pitch=pitch)
            audio_chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
            return b"".join(audio_chunks)

        # Simple SSML wrapping
        audio_data = asyncio.run(synth(text))

        return send_file(BytesIO(audio_data), mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"ok": False, "error": "TTS_ERROR", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
