import os
import re
import yaml
import logging
import json
import tempfile
import pymysql
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_community.vectorstores import InMemoryVectorStore
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter


with open("config.yml", 'r') as f:
    config = yaml.safe_load(f)

app = Flask(__name__, template_folder=".")
logging.basicConfig(level=logging.INFO)


agentModel      = ChatOllama(model=config['models']['ai'],         temperature=0.7, reasoning=False)
classifierModel = ChatOllama(model=config['models']['classifier'], temperature=0,   reasoning=False)

# RAG principale (ricette gia nel DB)
embeddings    = OllamaEmbeddings(model=config['models']['embed'])
vectorStoring = InMemoryVectorStore.load(config['rag_db_path'], embeddings)
retriever     = vectorStoring.as_retriever()


def get_db():
    return pymysql.connect(
        host=config['db']['host'],
        user=config['db']['user'],
        password=config['db']['pass'],
        database=config['db']['name'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


def is_prompt_safe(prompt: str) -> bool:
    message = config['prompts']['secure'].format(message=prompt.lower())
    answer  = classifierModel.invoke([('human', message)])
    return 'unsafe' not in answer.content.lower()


def classify_intent(prompt: str) -> str:
    """Restituisce 'SQL' oppure 'CHAT'."""
    message = config['prompts']['classifier'].format(message=prompt)
    answer  = classifierModel.invoke([('human', message)])
    return 'SQL' if 'SQL' in answer.content.strip().upper() else 'CHAT'


def pdf_to_recipe_chunks(pdf_path: str) -> list[str]:
    """Carica un PDF e restituisce una lista di testi, uno per ricetta.
    Strategia: divide per pagina, poi raggruppa pagine consecutive che
    appartengono alla stessa ricetta chiedendo all'AI di identificare i confini."""
    loader = PyPDFLoader(pdf_path)
    pages  = loader.load()   # una Document per pagina

    if not pages:
        return []

    # Splitter fine per identificare sezioni ricetta dentro una pagina
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1500, chunk_overlap=100,
        separators=['\n\n', '\n', ' ']
    )

    # Raggruppa il testo di ogni pagina; cerca titoli ricetta come separatori
    # Un titolo ricetta tipico inizia riga da solo, tutto maiuscolo o con pattern noto
    recipe_pattern = re.compile(
        r'^(?=[A-ZÀÈÌÒÙ][A-Za-zÀ-ÿ\s]{3,60}$)',
        re.MULTILINE
    )

    all_text  = '\n\n--- PAGINA ---\n\n'.join(p.page_content for p in pages)
    # Suddivide per separatore di pagina e poi cerca boundary ricetta
    raw_chunks = re.split(r'\n\n--- PAGINA ---\n\n', all_text)

    # Accumula testo finché non trova un nuovo titolo ricetta (euristica semplice)
    recipes   : list[str] = []
    current   : list[str] = []

    for page_text in raw_chunks:
        lines = page_text.strip().splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Heuristica: riga corta (< 60 char), non vuota, che sembra un titolo
            # e non è la prima riga in assoluto → nuovo boundary
            is_title = (
                stripped
                and len(stripped) < 60
                and stripped[0].isupper()
                and not stripped.endswith(',')
                and not stripped.endswith(':')
                and current  # non al primissimo elemento
            )
            if is_title and i == 0 and current:
                # Nuova pagina che inizia con titolo → salva ricetta precedente
                recipes.append('\n'.join(current))
                current = [line]
            else:
                current.append(line)

    if current:
        recipes.append('\n'.join(current))

    # Filtra chunk troppo corti (intestazioni, pagine bianche, ecc.)
    recipes = [r for r in recipes if len(r.strip()) > 150]

    logging.info(f'PDF suddiviso in {len(recipes)} chunk-ricetta')
    return recipes


def pdf_to_text(pdf_path: str) -> str:
    """Compatibilità: restituisce tutto il testo del PDF come stringa unica."""
    chunks = pdf_to_recipe_chunks(pdf_path)
    return '\n\n'.join(chunks)


def _clean_json(raw: str) -> str:
    """Rimuove trailing comma prima di ] o } per rendere il JSON valido."""
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    return raw


def extract_recipe_json(text: str) -> dict:
    """Chiede all'AI di estrarre dati strutturati in JSON dal testo."""
    prompt = config['prompts']['extract_json'].format(text=text)
    answer = agentModel.invoke([('human', prompt)])
    raw    = answer.content.strip()
    # Rimuove fence markdown se presenti
    raw    = re.sub(r'^```[a-zA-Z]*\n?', '', raw, flags=re.MULTILINE)
    raw    = raw.replace('```', '').strip()
    raw    = _clean_json(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logging.error(f"JSON non parsabile anche dopo cleanup: {e}\nRaw:\n{raw}")
        raise


def validate_recipe(data: dict):
    """Whitelist sui campi: nessun campo fuori schema puo passare."""
    required = {'name', 'ingredients', 'steps'}
    if not required.issubset(data.keys()):
        raise ValueError(f"Campi obbligatori mancanti: {required - data.keys()}")

    allowed_top  = {'name', 'category', 'instructions', 'time', 'difficulty', 'ingredients', 'steps'}
    allowed_ing  = {'name', 'quantity', 'unit'}
    allowed_step = {'number', 'description'}

    extra = set(data.keys()) - allowed_top
    if extra:
        raise ValueError(f"Campi non permessi nella ricetta: {extra}")

    for ing in data.get('ingredients', []):
        bad = set(ing.keys()) - allowed_ing
        if bad:
            raise ValueError(f"Campi non permessi nell'ingrediente: {bad}")

    for step in data.get('steps', []):
        bad = set(step.keys()) - allowed_step
        if bad:
            raise ValueError(f"Campi non permessi nello step: {bad}")

def is_duplicate(meal_name: str) -> bool:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT idMeal FROM meals WHERE strMeal = %s', (meal_name,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def insert_recipe(data: dict) -> int:
    """Insert completo con query parametrizzate.
    Nessuna stringa SQL generata dall'AI viene mai eseguita."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Categoria
            cat_name = data.get('category', 'Generale')
            cur.execute('SELECT idCategory FROM categories WHERE strCategory = %s', (cat_name,))
            row = cur.fetchone()
            if row:
                cat_id = row['idCategory']
            else:
                cur.execute('INSERT INTO categories (strCategory) VALUES (%s)', (cat_name,))
                cat_id = cur.lastrowid

            # Prossimo idMeal
            cur.execute('SELECT COALESCE(MAX(idMeal), 0) + 1 AS next_id FROM meals')
            meal_id = cur.fetchone()['next_id']

            # Meal
            cur.execute(
                'INSERT INTO meals (idMeal, strMeal, strInstructions, strTime, strDifficulty, idCategory) '
                'VALUES (%s, %s, %s, %s, %s, %s)',
                (meal_id, data['name'], data.get('instructions', ''),
                 data.get('time', ''), data.get('difficulty', ''), cat_id)
            )

            # Ingredienti
            for ing in data.get('ingredients', []):
                ing_name = ing.get('name', '').strip()
                cur.execute('SELECT idIngredient FROM ingredients WHERE strIngredient = %s', (ing_name,))
                ing_row = cur.fetchone()
                if ing_row:
                    ing_id = ing_row['idIngredient']
                else:
                    cur.execute('SELECT COALESCE(MAX(idIngredient), 0) + 1 AS next_id FROM ingredients')
                    ing_id = cur.fetchone()['next_id']
                    cur.execute('INSERT INTO ingredients (idIngredient, strIngredient) VALUES (%s, %s)',
                                (ing_id, ing_name))

                cur.execute(
                    'INSERT IGNORE INTO recipeIngredients (idIngredient, idMeal, strQta, strUnit) '
                    'VALUES (%s, %s, %s, %s)',
                    (ing_id, meal_id, ing.get('quantity', ''), ing.get('unit', ''))
                )

            # Step di preparazione
            for step in data.get('steps', []):
                cur.execute(
                    'INSERT INTO prep (strDescription, intProgressive, idMeal) VALUES (%s, %s, %s)',
                    (step.get('description', ''), step.get('number', 0), meal_id)
                )

        conn.commit()
        return meal_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_uploaded_pdf(pdf_file) -> str:
    """Salva il file Flask in un path temporaneo e restituisce il path.
    Il chiamante e responsabile di eliminare il file con os.unlink()."""
    suffix = os.path.splitext(pdf_file.filename)[1] or '.pdf'
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)                    # chiude subito il file descriptor
    pdf_file.save(tmp_path)         # Flask scrive nel path
    return tmp_path

def build_context(character: str, history: list) -> list:
    """history e una lista di {role: 'human'|'ai', content: '...'}"""
    base = config['prompts'].get(character, config['prompts']['default'])
    ctx  = [('system', base), ('system', '')]
    for msg in history:
        role = 'human' if msg.get('role') == 'human' else 'ai'
        ctx.append((role, msg.get('content', '')))
    return ctx


def generate_chat(user_prompt: str, character: str, history: list) -> str:
    ctx = build_context(character, history)
    ctx.append(('human', user_prompt))

    relevant = retriever.invoke(user_prompt)
    doc_text = ('Usa queste informazioni sulle ricette per rispondere:\n'
                + '\n'.join([d.page_content for d in relevant])
                + '\nSe non conosci la risposta, di che non lo sai.\n')
    ctx[1] = ('system', doc_text)

    return agentModel.invoke(ctx).content


@app.route('/')
def index():
    return render_template('index.html')


def chat_compat(data: dict, character: str):
    """Handler condiviso per tutte le vecchie route /cannavacciuolo ecc."""
    prompt  = (data.get('question') or '').strip()
    history = data.get('history', [])

    if not prompt:
        return jsonify({'error': 'Domanda vuota.'}), 400

    if not is_prompt_safe(prompt):
        logging.warning('Messaggio non sicuro bloccato.')
        return jsonify({'error': 'Il messaggio e stato rilevato come non sicuro.'}), 400

    answer = generate_chat(prompt, character, history)
    return jsonify({'answer': answer, 'character': character.lower()})


@app.route('/chat', methods=['POST'])
def chat():
    data      = request.get_json()
    character = data.get('character', 'default')
    valid     = ('default', 'Cannavacciuolo', 'MysteryChef', 'Ade')
    if character not in valid:
        return jsonify({'error': 'Personaggio non valido.'}), 400

    prompt = (data.get('question') or '').strip()
    if prompt and is_prompt_safe(prompt):
        intent = classify_intent(prompt)
        logging.info(f'Intent classificato: {intent}')
        if intent == 'SQL':
            try:
                result = process_and_save(text=prompt)
                return jsonify(result)
            except Exception as e:
                logging.warning(f'Salvataggio testo fallito, fallback a chat: {e}')
                # Se l'estrazione fallisce (testo troppo vago) cade in chat normalmente

    return chat_compat(data, character)

@app.route('/question',        methods=['POST'])
def question():        return chat_compat(request.get_json(), 'default')

@app.route('/cannavacciuolo',  methods=['POST'])
def cannavacciuolo():  return chat_compat(request.get_json(), 'Cannavacciuolo')

@app.route('/mysterychef',     methods=['POST'])
def mysterychef():     return chat_compat(request.get_json(), 'MysteryChef')

@app.route('/ade',             methods=['POST'])
def ade():             return chat_compat(request.get_json(), 'Ade')


def process_and_save(text: str = None, pdf_file=None):
    """Logica condivisa: estrae, valida e inserisce le ricette.
    Se arriva un PDF tenta di salvare TUTTE le ricette trovate.
    Se arriva testo salva quella singola ricetta."""
    if pdf_file:
        tmp_path = save_uploaded_pdf(pdf_file)
        try:
            recipe_chunks = pdf_to_recipe_chunks(tmp_path)
        finally:
            os.unlink(tmp_path)

        saved      = []
        duplicates = []
        errors     = []

        for chunk in recipe_chunks:
            try:
                recipe = extract_recipe_json(chunk)
                validate_recipe(recipe)
                if is_duplicate(recipe['name']):
                    duplicates.append(recipe['name'])
                else:
                    meal_id = insert_recipe(recipe)
                    saved.append(f"{recipe['name']} (ID {meal_id})")
                    logging.info(f"Salvata: {recipe['name']} ID={meal_id}")
            except Exception as e:
                logging.warning(f'Chunk saltato ({e}): {chunk[:80]}...')
                errors.append(str(e))

        parts = []
        if saved:      parts.append(f"Salvate {len(saved)} ricette: " + ', '.join(saved))
        if duplicates: parts.append(f"Già presenti: " + ', '.join(duplicates))
        if errors:     parts.append(f"{len(errors)} chunk non riconosciuti come ricette")
        if not parts:  parts.append('Nessuna ricetta trovata nel PDF.')

        status = 'success' if saved else ('duplicate' if duplicates else 'error')
        return {'status': status, 'answer': ' | '.join(parts)}

    # Flusso testo singolo
    recipe = extract_recipe_json(text)
    validate_recipe(recipe)

    if is_duplicate(recipe['name']):
        return {'status': 'duplicate',
                'answer': f"La ricetta '{recipe['name']}' e gia presente nel database."}

    meal_id = insert_recipe(recipe)
    return {'status': 'success',
            'answer': f"Ricetta '{recipe['name']}' salvata con successo (ID {meal_id})."}


@app.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    """Route chiamata dal frontend (pulsante PDF del Prof. Ade)."""
    pdf_file = request.files.get('pdf')
    if not pdf_file:
        return jsonify({'answer': 'Nessun file PDF ricevuto.'}), 400

    try:
        result = process_and_save(pdf_file=pdf_file)
        return jsonify(result)
    except json.JSONDecodeError:
        logging.exception('Estrazione JSON fallita')
        return jsonify({'answer': 'Non sono riuscito a estrarre una ricetta strutturata dal PDF. '
                                  'Prova con un ricettario piu semplice o inviami il testo direttamente.'}), 400
    except ValueError as e:
        return jsonify({'answer': f'Dati ricetta non validi: {e}'}), 400
    except Exception as e:
        logging.exception('Errore salvataggio PDF')
        return jsonify({'answer': 'Errore interno durante il salvataggio della ricetta.'}), 500


@app.route('/save_recipe', methods=['POST'])
def save_recipe():
    """Route alternativa: accetta JSON {question} oppure form-data con pdf."""
    pdf_file = request.files.get('pdf')
    prompt   = (request.form.get('question') or
                (request.get_json(silent=True) or {}).get('question', '')).strip()

    if not prompt and not pdf_file:
        return jsonify({'error': 'Invia testo o un file PDF.'}), 400
    if prompt and not is_prompt_safe(prompt):
        return jsonify({'error': 'Il messaggio e stato rilevato come non sicuro.'}), 400

    try:
        result = process_and_save(text=prompt or None, pdf_file=pdf_file)
        return jsonify(result)
    except json.JSONDecodeError:
        return jsonify({'error': 'Risposta AI non valida: JSON non parsabile.'}), 400
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logging.exception('Errore salvataggio ricetta')
        return jsonify({'error': 'Errore interno durante il salvataggio.'}), 500



CORS(app, resources={r'/*': {'origins': '*'}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=config['server_portc'], debug=True)
