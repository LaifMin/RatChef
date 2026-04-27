import os
import yaml
import logging
import re
import asyncio
import time
import json
import copy
import traceback
import pymysql
from pypdf import PdfReader
from io import BytesIO
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from langchain_ollama import ChatOllama
from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import InMemoryVectorStore



with open("config.yml", 'r') as ymlFiles:
    config = yaml.safe_load(ymlFiles)


db_conf = config['db']


def save_recipe_to_db(recipes):
    """Accept a single recipe dict or a list of recipe dicts.
    Checks for duplicates by name before inserting.
    Returns a list of result dicts: {meal, status, reason?}"""
    if isinstance(recipes, dict):
        recipes = [recipes]

    conn = None
    results = []

    try:
        conn = pymysql.connect(
            host=db_conf['host'],
            user=db_conf['user'],
            password=db_conf['pass'],
            database=db_conf['name'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False
        )
    except Exception as e:
        logging.error(f"Errore connessione DB: {str(e)}")
        return [{'meal': 'all', 'status': 'error', 'reason': f'DB connection failed: {str(e)}'}]

    for recipe_json in recipes:
        meal_name = ''
        id_meal = None
        try:
            meal_data        = recipe_json.get('meal', {})
            ingredients_list = recipe_json.get('ingredients', [])
            prep_list        = recipe_json.get('prep', [])
            meal_name        = meal_data.get('strMeal', '').strip()

            if not meal_name:
                results.append({'meal': '(senza nome)', 'status': 'skipped', 'reason': 'nome mancante'})
                continue

            with conn.cursor() as cursor:
                # --- DUPLICATE CHECK ---
                cursor.execute("SELECT idMeal FROM meals WHERE strMeal = %s", (meal_name,))
                if cursor.fetchone():
                    logging.info(f"Ricetta '{meal_name}' già presente, saltata.")
                    results.append({'meal': meal_name, 'status': 'skipped', 'reason': 'già esistente'})
                    continue

                # --- CATEGORY ---
                category_name = meal_data.get('idCategory', 'Generica')
                cursor.execute("SELECT idCategory FROM categories WHERE strCategory = %s", (category_name,))
                cat_result = cursor.fetchone()
                if cat_result:
                    id_category = cat_result['idCategory']
                else:
                    cursor.execute("INSERT INTO categories (strCategory) VALUES (%s)", (category_name,))
                    id_category = cursor.lastrowid

                # --- MEAL ---
                cursor.execute("""
                    INSERT INTO meals (strMeal, strInstructions, strTime, strDifficulty, idCategory)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    meal_name,
                    meal_data.get('strInstructions', ''),
                    meal_data.get('strTime', ''),
                    meal_data.get('strDifficulty', ''),
                    id_category
                ))
                id_meal = cursor.lastrowid
                logging.info(f"Ricetta inserita con ID: {id_meal}")

                # --- INGREDIENTS ---
                for ing in ingredients_list:
                    ing_name = ing.get('strIngredient', ing.get('strMeal', '')).strip()
                    if not ing_name:
                        continue
                    str_qta  = ing.get('strQta', '')
                    str_unit = ing.get('strUnit', '')

                    cursor.execute("SELECT idIngredient FROM ingredients WHERE strIngredient = %s", (ing_name,))
                    ing_result = cursor.fetchone()
                    if ing_result:
                        id_ingredient = ing_result['idIngredient']
                    else:
                        cursor.execute("INSERT INTO ingredients (strIngredient) VALUES (%s)", (ing_name,))
                        id_ingredient = cursor.lastrowid

                    cursor.execute("""
                        INSERT IGNORE INTO recipeIngredients (idIngredient, idMeal, strQta, strUnit)
                        VALUES (%s, %s, %s, %s)
                    """, (id_ingredient, id_meal, str_qta, str_unit))

                # --- PREP STEPS ---
                for i, step in enumerate(prep_list):
                    cursor.execute("""
                        INSERT INTO prep (strDescription, intProgressive, idMeal)
                        VALUES (%s, %s, %s)
                    """, (
                        step.get('strDescription', ''),
                        step.get('intProgressive', i + 1),
                        id_meal
                    ))

            conn.commit()
            logging.info(f"Ricetta '{meal_name}' salvata con ID {id_meal}")
            results.append({'meal': meal_name, 'status': 'saved'})

        except Exception as e:
            conn.rollback()
            logging.error(f"Errore salvataggio '{meal_name}': {str(e)}")
            traceback.print_exc()
            results.append({'meal': meal_name, 'status': 'error', 'reason': str(e)})

    if conn:
        conn.close()
    return results


def _format_save_results(results):
    """Turn a list of save results into a human-readable Italian string."""
    saved   = [r['meal'] for r in results if r['status'] == 'saved']
    skipped = [r['meal'] for r in results if r['status'] == 'skipped']
    errors  = [r         for r in results if r['status'] == 'error']
    parts = []
    if saved:   parts.append(f"Salvate: {', '.join(saved)}")
    if skipped: parts.append(f"Già presenti (saltate): {', '.join(skipped)}")
    if errors:  parts.append(f"Errori: {', '.join(r['meal'] for r in errors)}")
    return ' | '.join(parts) if parts else 'Nessuna ricetta processata.'


app = Flask(__name__, template_folder=".")
logging.basicConfig(level = logging.INFO)


agentModel = ChatOllama(model = config['models']['ai'], temperature = 0.7, reasoning = False)


embeddings = OllamaEmbeddings(model = config['models']['embed'])
vectorStoring = InMemoryVectorStore.load(config['rag_db_path'], embeddings)
retriever = vectorStoring.as_retriever()



def is_prompt_safe(prompt):
   prompt_lower = prompt.lower()
   message = config['prompts']['secure'].format(message = prompt_lower)
   answer = agentModel.invoke([('human', message)])
   return not ("unsafe" in answer.content.lower())


# Contesti chef
context = [
    ('system', config['prompts']['default']),
    ('system', '')
]

context1 = [
    ('system', config['prompts']['Cannavacciuolo']),
    ('system', '')
]

context2 = [
    ('system', config['prompts']['MysteryChef']),
    ('system', '')
]

context3 = [
    ('system', config['prompts']['ade']),
    ('system', '')
]


contextDictionary = {
   'Cannavacciuolo': context1,
   'MysteryChef': context2,
   'Ade': context3
}
private_contexts = {
   'Cannavacciuolo': [('system', config['prompts']['Cannavacciuolo']), ('system', '')],
   'MysteryChef': [('system', config['prompts']['MysteryChef']), ('system', '')],
   'Ade': [('system', config['prompts']['ade']), ('system', '')]
}


def get_contexts(character_name):
   contexts = []
   for name, char_ctx in contextDictionary.items():
      if name != character_name:
         conversation = char_ctx[2:]
         if conversation:
             contexts.append({
                    'character': name,
                    'messages': conversation
                })
   return contexts


def format_contexts(contexts):
   if not contexts:
      return ""

   formatted = "\n\n--- CONVERSAZIONI DEGLI ALTRI CHEF (per tua informazione) ---\n"
   for ctx in contexts:
      formatted += f"\n{ctx['character']}:\n"
      for role, content in ctx['messages']:
         if role == 'human':
            formatted += f"  Utente: {content}\n"
         elif role == 'ai':
            formatted += f"  {ctx['character']}: {content}\n"
   formatted += "\n--- FINE CONVERSAZIONI ALTRI CHEF ---\n"
   return formatted



def generate_answer(user_prompt):
   if not is_prompt_safe(user_prompt):
      logging.warning("Unsafe message detected from user.")
      unsafeString = "Your message was detected as unsafe, thus I cannot answer it; please rephrase it or change the content."
      return unsafeString

   logging.info("Generating answer for user prompt:" + user_prompt)

   context.append(('human', user_prompt))

   
   relevantDocs = retriever.invoke(user_prompt)
   doc_text = "\n".join([d.page_content for d in relevantDocs])
   doc_text = "Usa queste informazioni sulle ricette per rispondere alla domanda:\n" + doc_text + "\nSe non conosci la risposta, dì che non lo sai.\n"
   context[1] = ('system', doc_text)

   answer = agentModel.invoke(context).content
   context.append(('ai', answer))
   return answer


def generate_answerMC(user_prompt, character_name, is_private=False):
   if not is_prompt_safe(user_prompt):
      logging.warning("Unsafe message detected from user.")
      return "Messaggio rilevato come non sicuro. Non posso elaborare questa richiesta."

   logging.info("Generating answer for user prompt:" + user_prompt)

   target_context = copy.deepcopy(
       private_contexts[character_name] if is_private else contextDictionary[character_name]
   )

  
   relevantDocs = retriever.invoke(user_prompt)
   doc_text = "\n".join([d.page_content for d in relevantDocs])

   
   if character_name == 'Ade':
       doc_text = (
           "[ISTRUZIONI PRIORITARIE PER DATA MANAGER]\n"
           "Queste sono informazioni di riferimento.\n"
           "TU DEVI USARLE SOLO per estrarre dati JSON da salvare.\n"
           "NON rispondere a domande generiche.\n"
           "Se la richiesta non è un salvataggio ricetta, rispondi: "
           "'Non posso aiutarti con questa domanda'\n"
           "---\nInformazioni dal database:\n" + doc_text
       )
   else:
       doc_text = (
           "Usa queste informazioni sulle ricette per rispondere alla domanda:\n"
           + doc_text
           + "\nSe non conosci la risposta, dì che non lo sai.\n"
       )

   target_context[1] = ('system', doc_text)
   target_context.append(('human', user_prompt))

   answer = agentModel.invoke(target_context).content
   return answer  # always a string




@app.route('/')
def index():
   return render_template('index.html', history = context)

@app.route('/question', methods=['POST'])
def question():
   userPrompt = request.get_json()
   answer = userPrompt.get('question', 'No question')
   responseText = generate_answer(answer)
   return jsonify({'answer': responseText,
                   'ai': 'yes'
                   })

@app.route('/cannavacciuolo', methods=['POST'])
def cannavacciuolo():
   userPrompt = request.get_json()
   answer = userPrompt.get('question', 'No question')
   is_private = userPrompt.get('private', False)
   responseText = generate_answerMC(answer, 'Cannavacciuolo', is_private)
   return jsonify({'answer': responseText,
                   'char': 'cannavacciuolo'
                   })


@app.route('/mysterychef', methods=['POST'])
def mysterychef():
   userPrompt = request.get_json()
   answer = userPrompt.get('question', 'No question')
   is_private = userPrompt.get('private', False)
   responseText = generate_answerMC(answer, 'MysteryChef', is_private)
   return jsonify({'answer': responseText,
                   'char': 'mysterychef'
                   })

@app.route('/ade', methods=['POST'])
def ade():
    try:
        userPrompt = request.get_json()
        answer_text = userPrompt.get('question', 'No question')
        is_private = userPrompt.get('private', False)

        responseText = generate_answerMC(answer_text, 'Ade', is_private)

        if not isinstance(responseText, str):
            responseText = str(responseText)

        # Match JSON array OR single object
        json_match = re.search(r'(\[.*\]|\{.*\})', responseText, re.DOTALL)

        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, dict):
                    parsed = [parsed]

                results = save_recipe_to_db(parsed)
                return jsonify({'answer': _format_save_results(results), 'char': 'ade'})

            except json.JSONDecodeError as e:
                logging.warning(f"JSON non valido: {str(e)}")
                return jsonify({'answer': 'Formato JSON non valido. Riprova con la ricetta in formato corretto.', 'char': 'ade'})
        else:
            return jsonify({'answer': responseText, 'char': 'ade'})

    except Exception as e:
        logging.error(f"Errore endpoint /ade: {str(e)}")
        return jsonify({'answer': f"Errore interno: {str(e)}", 'char': 'ade'}), 500


@app.route('/dataManager', methods=['POST'])
def dataManager():
   userPrompt = request.get_json()
   answer = userPrompt.get('question', 'No question')
   responseText = generate_answer(answer)
   return jsonify({'answer': responseText,
                   'ai': 'yes'
                   })

@app.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    try:
        if 'pdf' not in request.files:
            return jsonify({'answer': 'Nessun file PDF fornito.', 'char': 'ade'}), 400

        pdf_file = request.files['pdf']
        reader = PdfReader(BytesIO(pdf_file.read()))
        text = ''.join(page.extract_text() or '' for page in reader.pages)

        if not text.strip():
            return jsonify({'answer': 'Impossibile estrarre testo dal PDF.', 'char': 'ade'}), 400

        extraction_prompt = (
            "Estrai TUTTE le ricette dal seguente testo e restituisci SOLO un array JSON, "
            "senza altro testo, backtick o commenti:\n"
            "[\n"
            "  {\n"
            '    "meal": {"strMeal": "", "strInstructions": "", "strTime": "", "strDifficulty": "", "idCategory": ""},\n'
            '    "ingredients": [{"strIngredient": "", "strQta": "", "strUnit": ""}],\n'
            '    "prep": [{"strDescription": "", "intProgressive": 1}]\n'
            "  }\n"
            "]\n\nTESTO:\n" + text[:8000]
        )

        response = agentModel.invoke([('human', extraction_prompt)])
        response_text = response.content

        json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if not json_match:
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)

        if not json_match:
            return jsonify({'answer': 'Nessuna ricetta trovata nel PDF.', 'char': 'ade'}), 400

        parsed = json.loads(json_match.group())
        if isinstance(parsed, dict):
            parsed = [parsed]

        results = save_recipe_to_db(parsed)
        return jsonify({'answer': _format_save_results(results), 'char': 'ade'})

    except Exception as e:
        logging.error(f"Errore upload PDF: {str(e)}")
        traceback.print_exc()
        return jsonify({'answer': f"Errore: {str(e)}", 'char': 'ade'}), 500


CORS(app)
CORS(app, resources={r"/*": {"origins": "*"}})

if __name__ == "__main__":
   app.run(host='0.0.0.0', port=config['server_portc'], debug=True)
