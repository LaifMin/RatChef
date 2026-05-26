import re
import json
import logging
import requests
import yaml
import pymysql
from flask import Flask, request, jsonify
from flask_cors import CORS
from langchain_ollama import ChatOllama
import sys
import pypdf
import tempfile
import os

with open("config.yml", "r") as f:
    config = yaml.safe_load(f)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

app = Flask(__name__)
CORS(app)

ai_model = ChatOllama(model="gemma2:2b", temperature=0)
master_server = "http://127.0.0.1:5000/registerWorker"
worker_id =  "test_number_one"
worker_url = "http://127.0.0.1:5001"



SCHEMA = """
    


CREATE DATABASE IF NOT EXISTS CUCINA
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE CUCINA;



DROP TABLE IF EXISTS recipeIngredients;
DROP TABLE IF EXISTS prep;
DROP TABLE IF EXISTS meals;
DROP TABLE IF EXISTS ingredients;
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS areas;


CREATE TABLE categories (
    idCategory  INT          NOT NULL AUTO_INCREMENT,
    strCategory VARCHAR(255) NOT NULL,
    PRIMARY KEY (idCategory)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ingredients (
    idIngredient  INT          NOT NULL AUTO_INCREMENT,
    strIngredient VARCHAR(255) NOT NULL,
    PRIMARY KEY (idIngredient)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE areas (
    idArea  INT          NOT NULL AUTO_INCREMENT,
    strArea VARCHAR(255) NOT NULL,
    PRIMARY KEY (idArea)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE meals (
    idMeal          INT          NOT NULL AUTO_INCREMENT,
    strMeal         VARCHAR(255) NOT NULL,
    strInstructions TEXT,
    strTime         VARCHAR(50),
    strDifficulty   VARCHAR(50),
    idCategory      INT,
    PRIMARY KEY (idMeal),
    CONSTRAINT fk_meals_category
        FOREIGN KEY (idCategory) REFERENCES categories(idCategory)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE recipeIngredients (
    idIngredient INT         NOT NULL,
    idMeal       INT         NOT NULL,
    strQta       VARCHAR(50),
    strUnit      VARCHAR(50),
    PRIMARY KEY (idIngredient, idMeal),
    CONSTRAINT fk_ri_ingredient
        FOREIGN KEY (idIngredient) REFERENCES ingredients(idIngredient),
    CONSTRAINT fk_ri_meal
        FOREIGN KEY (idMeal) REFERENCES meals(idMeal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE prep (
    idPrep         INT  NOT NULL AUTO_INCREMENT,
    strDescription TEXT,
    intProgressive INT,
    idMeal         INT  NOT NULL,
    PRIMARY KEY (idPrep),
    CONSTRAINT fk_prep_meal
        FOREIGN KEY (idMeal) REFERENCES meals(idMeal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

"""


def get_db():
    return pymysql.connect(
        host        = config["db"]["host"],
        user        = config["db"]["user"],
        password    = config["db"]["pass"],
        database    = config["db"]["name"],
        charset     = "utf8mb4",
        cursorclass = pymysql.cursors.DictCursor,
    )

def ask_ai(system: str, user: str = None, history: list = None) -> str:
    context = [("system", system)]
    if history:
        for msg in history:
            context.append((msg["role"], msg["content"]))
    elif user:
        context.append(("human", user))
    return ai_model.invoke(context).content.strip()

def run_query(sql: str) -> list:
    if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        raise ValueError("Solo SELECT permesse")
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    conn.close()
    return rows

def run_write(sql: str):
    if not re.match(r"^\s*(INSERT|UPDATE)\b", sql, re.IGNORECASE):
        raise ValueError("Solo INSERT/UPDATE permessi")
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    conn.close()



@app.route("/chat", methods=["POST"])
def chat():
    # Ora riceviamo l'intero storico invece del singolo messaggio
    history = request.json.get("history", [])
    if not history:
        return jsonify({"error": "history vuota"}), 400

    # L'ultima domanda dell'utente è l'ultimo elemento della history
    question = history[-1]["content"].strip()

    # Step 1 — genera SQL
    system_sql = f"""Sei un assistente che traduce conversazioni in SQL MariaDB. Schema: {SCHEMA}
Tieni conto del contesto della conversazione per capire a cosa si riferisce l'utente.
Rispondi SOLO con JSON, senza markdown.
Se l'utente vuole leggere:              {{"sql": "SELECT ...", "possible": true, "type": "read"}}
Se l'utente vuole inserire (aggiungi/crea/inserisci): {{"sql": "INSERT INTO ...", "possible": true, "type": "write"}}
Se l'utente vuole modificare:           {{"sql": "UPDATE ...", "possible": true, "type": "write"}}
Se non puoi rispondere o non riguarda il DB: {{"sql": null, "possible": false}}
Usa solo tabelle e colonne dello schema."""

    raw = ask_ai(system_sql, history=history)

    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(raw)
    except Exception:
        return jsonify({"answer": "Non ho capito la domanda.", "sql": None})

    if not parsed.get("possible") or not parsed.get("sql"):
        return jsonify({"answer": "Questa informazione non è nel database.", "sql": None})

    sql = parsed["sql"]

    # Step 2 — esegui
    if parsed.get("type") == "write":
        try:
            run_write(sql)
        except Exception as e:
            return jsonify({"answer": f"Errore DB: {e}", "sql": sql})
        return jsonify({"answer": "Operazione completata.", "sql": sql})

    try:
        rows = run_query(sql)
    except Exception as e:
        return jsonify({"answer": f"Errore DB: {e}", "sql": sql})

    # Step 3 — formula risposta
    system_answer = f"""Sei un assistente che risponde in italiano basandosi sui dati forniti.
Dati estratti dal database per rispondere all'ultima domanda: {json.dumps(rows, ensure_ascii=False, default=str)}
Rispondi chiaramente. Se la lista è vuota, dì che non ci sono risultati per l'ultima richiesta."""

    answer = ask_ai(system_answer, history=history)

    return jsonify({"answer": answer, "sql": sql})


def process_pdf_page(page_text: str, page_num: int) -> list:
    """
    Pipeline a 3 step per estrarre ricette da una singola pagina PDF.
    Step 1: Conta quante ricette reali ci sono
    Step 2: Estrai i dati strutturati
    Step 3: Valida e rimuovi allucinazioni
    """
    if not page_text or len(page_text.strip()) < 30:
        logging.info(f"Pagina {page_num}: testo troppo corto, skip")
        return []

    # STEP 1 — Conta ricette reali nella pagina
    count_raw = ask_ai(
        "Sei un contatore di ricette. Rispondi SOLO con un numero intero, nient'altro.",
        f"Quante ricette complete (con un nome chiaro e almeno degli ingredienti) ci sono in questo testo? Se non ci sono ricette rispondi 0.\nTesto:\n{page_text[:3000]}"
    )

    try:
        count = int(count_raw.strip())
    except ValueError:
        logging.warning(f"Pagina {page_num}: AI ha risposto '{count_raw}' invece di un numero, skip")
        return []

    if count == 0:
        logging.info(f"Pagina {page_num}: nessuna ricetta trovata")
        return []

    logging.info(f"Pagina {page_num}: AI ha trovato {count} ricette, estraggo...")

    # STEP 2 — Estrai dati strutturati
    extract_raw = ask_ai(
        "Sei un estrattore di dati da testi di cucina. Rispondi SOLO con un array JSON valido, senza markdown.",
        f"""Estrai {count} ricette dal testo seguente.
Per ogni ricetta restituisci un oggetto JSON con questi campi:
- "nome": il nome della ricetta (stringa)
- "categoria": tipo di piatto es. Primo, Secondo, Dolce, Antipasto (stringa o null)
- "tempo": tempo di preparazione (stringa o null)
- "difficolta": facile/media/difficile (stringa o null)
- "ingredienti": array di oggetti {{"nome": "...", "quantita": "...", "unita": "..."}}
- "passaggi": array di stringhe con i passi della preparazione

Non inventare nulla. Se un campo non è presente nel testo, usa null.
Formato: [{{...}}, {{...}}]
Testo:
{page_text[:3000]}"""
    )

    extract_raw = extract_raw.replace("```json", "").replace("```", "").strip()

    try:
        recipes = json.loads(extract_raw)
        if not isinstance(recipes, list):
            recipes = [recipes]
    except Exception:
        logging.warning(f"Pagina {page_num}: JSON di estrazione non valido, skip")
        return []

    # STEP 3 — Valida e rimuovi allucinazioni
    validate_raw = ask_ai(
        "Sei un validatore di dati. Rispondi SOLO con un array JSON valido, senza markdown.",
        f"""Controlla questo array di ricette estratte da un PDF.
Rimuovi le voci che NON sono ricette vere, ad esempio:
- Se il 'nome' e' una singola parola generica come 'procedura', 'uovo', 'ingrediente', 'passo'
- Se il 'nome' e' vuoto o null
- Se non ha nessun ingrediente

Restituisci SOLO le ricette valide nello stesso formato JSON.
Se nessuna e' valida, restituisci []
Dati: {json.dumps(recipes, ensure_ascii=False)}"""
    )

    validate_raw = validate_raw.replace("```json", "").replace("```", "").strip()

    try:
        validated = json.loads(validate_raw)
        if not isinstance(validated, list):
            validated = [validated]
    except Exception:
        logging.warning(f"Pagina {page_num}: JSON di validazione non valido, skip")
        return []

    logging.info(f"Pagina {page_num}: {len(validated)} ricette validate")
    return validated


def insert_recipes(recipes: list) -> dict:
    """
    Inserisce le ricette validate nel database.
    Gestisce le FK per categories, ingredients, meals, recipeIngredients, prep.
    """
    conn = get_db()
    imported = 0
    errors = []

    try:
        with conn.cursor() as cur:
            for recipe in recipes:
                try:
                    nome = recipe.get("nome")
                    
                    # Filtra nomi non validi
                    if not nome or str(nome).strip().lower() in ("null", "none", "nessuna", "sconosciuto", "ricetta"):
                        logging.warning(f"Ricetta saltata per nome non valido: {nome}")
                        continue
                        
                    # Filtra se non ha ingredienti (spesso segno di allucinazione)
                    raw_ingredienti = recipe.get("ingredienti", [])
                    if not isinstance(raw_ingredienti, list) or len(raw_ingredienti) == 0:
                        logging.warning(f"Ricetta '{nome}' saltata perché non ha ingredienti.")
                        continue

                    # 1. Categoria
                    categoria = recipe.get("categoria") or "Altro"
                    cur.execute("SELECT idCategory FROM categories WHERE strCategory = %s", (categoria,))
                    row = cur.fetchone()
                    if row:
                        id_category = row["idCategory"]
                    else:
                        cur.execute("INSERT INTO categories (strCategory) VALUES (%s)", (categoria,))
                        id_category = cur.lastrowid

                    # 2. Meal
                    tempo = recipe.get("tempo")
                    difficolta = recipe.get("difficolta")
                    
                    # Normalizza i passaggi: l'AI a volte restituisce una lista di dict invece di stringhe
                    raw_passaggi = recipe.get("passaggi", [])
                    passaggi_str = []
                    if isinstance(raw_passaggi, list):
                        for p in raw_passaggi:
                            if isinstance(p, dict):
                                # Cerca di estrarre il valore testuale dal dict
                                val = p.get("step") or p.get("descrizione") or p.get("testo") or (list(p.values())[0] if p.values() else "")
                                passaggi_str.append(str(val))
                            else:
                                passaggi_str.append(str(p))
                    
                    istruzioni = "\n".join(passaggi_str) if passaggi_str else None

                    cur.execute(
                        "INSERT INTO meals (strMeal, strInstructions, strTime, strDifficulty, idCategory) VALUES (%s, %s, %s, %s, %s)",
                        (nome, istruzioni, tempo, difficolta, id_category)
                    )
                    id_meal = cur.lastrowid

                    # 3. Ingredienti
                    for ing in raw_ingredienti:
                        ing_nome = ing.get("nome") if isinstance(ing, dict) else str(ing)
                        if not ing_nome:
                            continue

                        cur.execute("SELECT idIngredient FROM ingredients WHERE strIngredient = %s", (ing_nome,))
                        row = cur.fetchone()
                        if row:
                            id_ingredient = row["idIngredient"]
                        else:
                            cur.execute("INSERT INTO ingredients (strIngredient) VALUES (%s)", (ing_nome,))
                            id_ingredient = cur.lastrowid

                        cur.execute(
                            "INSERT INTO recipeIngredients (idIngredient, idMeal, strQta, strUnit) VALUES (%s, %s, %s, %s)",
                            (id_ingredient, id_meal, ing.get("quantita"), ing.get("unita"))
                        )

                    # 4. Prep steps
                    for i, step in enumerate(passaggi_str, 1):
                        cur.execute(
                            "INSERT INTO prep (strDescription, intProgressive, idMeal) VALUES (%s, %s, %s)",
                            (step, i, id_meal)
                        )

                    imported += 1
                    logging.info(f"Inserita ricetta: {nome}")

                except Exception as e:
                    errors.append(f"Errore per '{recipe.get('nome', '?')}': {str(e)}")
                    logging.error(f"Errore inserimento ricetta: {e}")

        conn.commit()
    finally:
        conn.close()

    return {"imported": imported, "errors": errors}


@app.route("/upload", methods=["POST"])
def upload():
    """
    Riceve un PDF, lo processa pagina per pagina con la pipeline AI a 3 step,
    e inserisce le ricette trovate nel database.
    """
    if "file" not in request.files:
        return jsonify({"error": "Nessun file allegato"}), 400

    pdf = request.files["file"]
    if pdf.filename == '':
        return jsonify({"error": "Nome file vuoto"}), 400

    # Salva il PDF in un file temporaneo
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    try:
        pdf.save(tmp.name)
        tmp.close()

        reader = pypdf.PdfReader(tmp.name)
        total_pages = len(reader.pages)
        logging.info(f"Worker {worker_id}: ricevuto PDF '{pdf.filename}' con {total_pages} pagine")

        all_recipes = []
        skipped_pages = 0

        for i, page in enumerate(reader.pages):
            page_num = i + 1
            logging.info(f"Worker {worker_id} sta leggendo pagina {page_num} di {total_pages}")

            page_text = page.extract_text()
            recipes = process_pdf_page(page_text, page_num)

            if recipes:
                all_recipes.extend(recipes)
            else:
                skipped_pages += 1

        logging.info(f"Worker {worker_id}: totale ricette estratte: {len(all_recipes)}, pagine saltate: {skipped_pages}")

        # Inserisci nel DB
        if all_recipes:
            result = insert_recipes(all_recipes)
        else:
            result = {"imported": 0, "errors": []}

        return jsonify({
            "imported": result["imported"],
            "total_found": len(all_recipes),
            "skipped_pages": skipped_pages,
            "total_pages": total_pages,
            "errors": result["errors"]
        }), 200

    except Exception as e:
        logging.error(f"Errore durante elaborazione PDF: {e}")
        return jsonify({"error": f"Errore elaborazione PDF: {str(e)}"}), 500
    finally:
        os.unlink(tmp.name)



def register_as_worker() -> str:
    

    payload = {

        "worker_id" : worker_id,
        "worker_url" : worker_url
    }

    print(f"Connecting to master server: {master_server}; worker ID: {worker_id}")

    try:
        response = requests.post(master_server, json=payload)

        if response.status_code == 200:
            print("Code: OK")
        else:
            print(f"FAIL! Master response: {response.status_code} - {response.text}")

    except requests.exceptions.RequestException as e:
        print(f"Master not reachable; ERROR: {e}")




if __name__ == "__main__":
    register_as_worker()
    app.run(host='0.0.0.0', debug=True, port=5001, use_reloader=False)