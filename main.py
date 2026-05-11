import os
import re
import json
import base64
import logging
import tempfile

import yaml
import pymysql
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from langchain_ollama import ChatOllama
from langchain_community.document_loaders import PyPDFLoader


with open("config.yml", "r") as f:
    config = yaml.safe_load(f)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, template_folder=".")
CORS(app, resources={r"/*": {"origins": "*"}})

ai_model         = ChatOllama(model=config["models"]["ai"],         temperature=0.7)
classifier_model = ChatOllama(model=config["models"]["classifier"], temperature=0)
MAX_CHARS        = config["models"].get("max_chars_per_block", 4000)



def get_db():
    return pymysql.connect(
        host     = config["db"]["host"],
        user     = config["db"]["user"],
        password = config["db"]["pass"],
        database = config["db"]["name"],
        charset  = "utf8mb4",
        cursorclass = pymysql.cursors.DictCursor,
    )


def db_get_context(query: str) -> str:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            like = f"%{query[:60]}%"
            cur.execute(
                """
                SELECT m.strMeal, m.strInstructions,
                       GROUP_CONCAT(i.strIngredient ORDER BY i.strIngredient SEPARATOR ', ') AS ingredients
                FROM meals m
                LEFT JOIN recipeIngredients ri ON ri.idMeal  = m.idMeal
                LEFT JOIN ingredients i        ON i.idIngredient = ri.idIngredient
                WHERE m.strMeal LIKE %s OR m.strInstructions LIKE %s
                GROUP BY m.idMeal
                LIMIT 5
                """,
                (like, like),
            )
            rows = cur.fetchall()
        if not rows:
            return ""
        return "\n\n".join(
            f"Recipe: {r['strMeal']}\n"
            f"Ingredients: {r['ingredients'] or 'n/a'}\n"
            f"Instructions: {(r['strInstructions'] or '')[:300]}"
            for r in rows
        )
    finally:
        conn.close()


def db_is_duplicate(name: str) -> bool:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT idMeal FROM meals WHERE strMeal = %s", (name,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def db_insert_recipe(data: dict) -> int:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cat_name = data.get("category") or "General"
            cur.execute("SELECT idCategory FROM categories WHERE strCategory = %s", (cat_name,))
            row = cur.fetchone()
            if row:
                cat_id = row["idCategory"]
            else:
                cur.execute("INSERT INTO categories (strCategory) VALUES (%s)", (cat_name,))
                cat_id = cur.lastrowid

            cur.execute(
                "INSERT INTO meals (strMeal, strInstructions, strTime, strDifficulty, idCategory) "
                "VALUES (%s, %s, %s, %s, %s)",
                (data["name"], data.get("instructions", ""), data.get("time", ""),
                 data.get("difficulty", ""), cat_id),
            )
            meal_id = cur.lastrowid

            for ing in data.get("ingredients", []):
                ing_name = (ing.get("name") or "").strip()
                if not ing_name:
                    continue
                cur.execute("SELECT idIngredient FROM ingredients WHERE strIngredient = %s", (ing_name,))
                row = cur.fetchone()
                ing_id = row["idIngredient"] if row else None
                if not ing_id:
                    cur.execute("INSERT INTO ingredients (strIngredient) VALUES (%s)", (ing_name,))
                    ing_id = cur.lastrowid
                cur.execute(
                    "INSERT IGNORE INTO recipeIngredients (idIngredient, idMeal, strQta, strUnit) "
                    "VALUES (%s, %s, %s, %s)",
                    (ing_id, meal_id, ing.get("quantity", ""), ing.get("unit", "")),
                )

            for step in data.get("steps", []):
                cur.execute(
                    "INSERT INTO prep (strDescription, intProgressive, idMeal) VALUES (%s, %s, %s)",
                    (step.get("description", ""), step.get("number", 0), meal_id),
                )

        conn.commit()
        return meal_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def _classify(prompt: str) -> str:
    return classifier_model.invoke([("human", prompt)]).content.strip().upper()


def is_safe(text: str) -> bool:
    result = _classify(config["prompts"]["secure"].format(message=text[:500].lower()))
    return "UNSAFE" not in result


def route_intent(text: str) -> str:
    msg = config["prompts"]["router"].format(message=text)
    for _ in range(2):
        result = _classify(msg)
        if "SAVE" in result:
            return "SAVE"
        if "CHAT" in result:
            return "CHAT"
    log.warning("Router ambiguous — defaulting to CHAT")
    return "CHAT"



def _sanitize(text: str) -> str:
    """Replace curly quotes and other characters that break JSON generation."""
    return (
        text
        .replace("\u2018", "'").replace("\u2019", "'")   # ' '
        .replace("\u201c", '"').replace("\u201d", '"')   # " "
        .replace("\u2013", "-").replace("\u2014", "-")   # – —
        .replace("\u00e0", "a'").replace("\u00e8", "e'") # à è (common in Italian)
        .replace("\u00e9", "e").replace("\u00ec", "i")
        .replace("\u00f2", "o'").replace("\u00f9", "u'")
    )


def _clean_json(raw: str) -> str:
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw, flags=re.MULTILINE)
    raw = raw.replace("```", "").strip()
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    return raw


def _parse_recipes(raw: str) -> list[dict] | None:
    """Try to parse JSON. Returns list on success, None on failure."""
    raw = _clean_json(raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # single recipe object or {"recipes": [...]}
            return parsed.get("recipes", [parsed])
    except json.JSONDecodeError:
        return None


RETRY_PROMPT = """Extract the recipe from the text below.
Return ONLY a single JSON object (not an array), no markdown, no extra text.
Use this structure exactly:
{{
  "name": "dish name",
  "category": "",
  "instructions": "brief summary",
  "time": "",
  "difficulty": "",
  "ingredients": [{{"name": "ingredient", "quantity": "amount", "unit": "unit"}}],
  "steps": [{{"number": 1, "description": "step"}}]
}}

Text:
{text}"""


def _extract_block(block: str, idx: int, total: int) -> list[dict]:
    """Extract recipes from one block, with one retry on JSON failure."""
    log.info(f"Block {idx + 1}/{total} ({len(block)} chars)")
    clean = _sanitize(block)

    # First attempt — full array prompt
    raw     = ai_model.invoke([("human", config["prompts"]["extract"].format(text=clean))]).content.strip()
    recipes = _parse_recipes(raw)

    if recipes is not None:
        log.info(f"Block {idx + 1}: {len(recipes)} recipe(s)")
        return recipes

    # Retry — simplified single-object prompt
    log.warning(f"Block {idx + 1}: JSON failed, retrying with simplified prompt")
    raw     = ai_model.invoke([("human", RETRY_PROMPT.format(text=clean))]).content.strip()
    recipes = _parse_recipes(raw)

    if recipes is not None:
        log.info(f"Block {idx + 1} retry: {len(recipes)} recipe(s)")
        return recipes

    log.warning(f"Block {idx + 1}: retry also failed, skipping")
    return []


def _extract_from_text(text: str) -> list[dict]:
    blocks = [text[i : i + MAX_CHARS] for i in range(0, len(text), MAX_CHARS)]
    result = []
    for idx, block in enumerate(blocks):
        result.extend(_extract_block(block, idx, len(blocks)))
    return result


def _extract_from_pdf(pdf_file) -> list[dict]:
    suffix = os.path.splitext(pdf_file.filename)[1] or ".pdf"
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    pdf_file.save(tmp)
    try:
        pages = PyPDFLoader(tmp).load()
        text  = "\n\n".join(p.page_content for p in pages)
    finally:
        os.unlink(tmp)
    if not text.strip():
        log.warning("PDF produced no extractable text")
        return []
    return _extract_from_text(text)


def _extract_from_image(image_file) -> list[dict]:
    b64  = base64.b64encode(image_file.read()).decode("utf-8")
    mime = image_file.mimetype or "image/jpeg"
    msg  = {
        "role": "human",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text",      "text": config["prompts"]["extract"].format(text="[see image above]")},
        ],
    }
    raw     = ai_model.invoke([msg]).content.strip()
    recipes = _parse_recipes(raw)
    if recipes is None:
        log.warning("Image extraction JSON failed")
        return []
    return recipes


ALLOWED_TOP  = {"name", "category", "instructions", "time", "difficulty", "ingredients", "steps"}
ALLOWED_ING  = {"name", "quantity", "unit"}
ALLOWED_STEP = {"number", "description"}

JUNK_NAMES = {
    "impasto", "biga", "condimento", "preparazione", "ripieno", "cottura",
    "raddoppio", "schiacciatura", "cuocare", "finitura", "assemblaggio",
    "sfoglia", "mantecatura", "servizio", "cond", "ingredienti", "procedimento",
}


def validate_recipe(data: dict):
    if not isinstance(data, dict):
        raise ValueError("Recipe must be a JSON object")
    missing = {"name", "ingredients", "steps"} - data.keys()
    if missing:
        raise ValueError(f"Missing required fields: {missing}")
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("'name' is empty")
    if name.lower() in JUNK_NAMES:
        raise ValueError(f"'name' is a section header, not a dish: {name!r}")
    if re.search(r"(step|procedure|passaggio|passo)\s*\d", name, re.IGNORECASE):
        raise ValueError(f"'name' looks like a step fragment: {name!r}")
    extra = set(data.keys()) - ALLOWED_TOP
    if extra:
        raise ValueError(f"Unexpected fields: {extra}")
    for ing in data.get("ingredients", []):
        if bad := set(ing.keys()) - ALLOWED_ING:
            raise ValueError(f"Unexpected ingredient fields: {bad}")
    for step in data.get("steps", []):
        if bad := set(step.keys()) - ALLOWED_STEP:
            raise ValueError(f"Unexpected step fields: {bad}")



def save_pipeline(recipes: list[dict]) -> dict:
    saved, duplicates, errors = [], [], []
    for recipe in recipes:
        try:
            validate_recipe(recipe)
            name = recipe["name"].strip()
            if db_is_duplicate(name):
                duplicates.append(name)
                log.info(f"Duplicate: {name}")
            else:
                meal_id = db_insert_recipe(recipe)
                saved.append(f"{name} (ID {meal_id})")
                log.info(f"Saved: {name} ID={meal_id}")
        except Exception as e:
            errors.append(str(e))
            log.warning(f"Skipped — {e}")

    parts = []
    if saved:      parts.append(f"Saved {len(saved)}: "     + ", ".join(saved))
    if duplicates: parts.append("Already in DB: "           + ", ".join(duplicates))
    if errors:     parts.append(f"{len(errors)} rejected: " + "; ".join(errors))
    if not parts:  parts.append("No valid recipes found.")

    return {
        "status": "success" if saved else ("duplicate" if duplicates else "error"),
        "answer": " | ".join(parts),
    }


def chat_pipeline(prompt: str, history: list) -> str:
    system = config["prompts"]["default"]
    ctx    = db_get_context(prompt)
    if ctx:
        system += "\n\nRelevant recipes from the database:\n" + ctx
    messages = [("system", system)]
    for msg in history:
        messages.append(("human" if msg.get("role") == "human" else "ai", msg.get("content", "")))
    messages.append(("human", prompt))
    return ai_model.invoke(messages).content



@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    pdf_file   = request.files.get("pdf")
    image_file = request.files.get("image")
    data       = request.get_json(silent=True) or request.form.to_dict()
    prompt     = (data.get("question") or "").strip()
    history    = data.get("history", [])

    if pdf_file:
        return jsonify(save_pipeline(_extract_from_pdf(pdf_file)))

    if image_file:
        return jsonify(save_pipeline(_extract_from_image(image_file)))

    if not prompt:
        return jsonify({"error": "Empty message."}), 400
    if not is_safe(prompt):
        return jsonify({"error": "Message flagged as unsafe."}), 400

    intent = route_intent(prompt)
    log.info(f"Intent: {intent}")

    if intent == "SAVE":
        recipes = _extract_from_text(prompt)
        if recipes:
            return jsonify(save_pipeline(recipes))
        log.info("Nothing extracted — falling back to CHAT")

    return jsonify({"status": "chat", "answer": chat_pipeline(prompt, history)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config["server_port"], debug=True)
