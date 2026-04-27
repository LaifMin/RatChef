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
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from langchain_ollama import ChatOllama
from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import InMemoryVectorStore



with open("config.yml", 'r') as ymlFiles:
    config = yaml.safe_load(ymlFiles)


db_conf = config['db']


def save_recipe_to_db(recipe_json):
    conn = None
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

        with conn.cursor() as cursor:
        
            meal_data = recipe_json.get('meal', {})
            ingredients_list = recipe_json.get('ingredients', [])
            prep_list = recipe_json.get('prep', [])

            
            category_name = meal_data.get('idCategory', 'Generica')
            cursor.execute("SELECT idCategory FROM categories WHERE strCategory = %s", (category_name,))
            cat_result = cursor.fetchone()

            if cat_result:
                id_category = cat_result['idCategory']
            else:
                cursor.execute("INSERT INTO categories (strCategory) VALUES (%s)", (category_name,))
                id_category = cursor.lastrowid

            
            cursor.execute("""
                INSERT INTO meals (strMeal, strInstructions, strTime, strDifficulty, idCategory)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                meal_data.get('strMeal', ''),
                meal_data.get('strInstructions', ''),
                meal_data.get('strTime', ''),
                meal_data.get('strDifficulty', ''),
                id_category
            ))

            id_meal = cursor.lastrowid
            logging.info(f"Ricetta inserita con ID: {id_meal}")

            
            for ing in ingredients_list:
                ing_name = ing.get('strIngredient', ing.get('strMeal', ''))
                str_qta = ing.get('strQta', '')
                str_unit = ing.get('strUnit', '')

                if not ing_name:
                    continue

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

            # --- 5. INSERIMENTO STEP PREPARAZIONE (PREP) ---
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

        meal_name = meal_data.get('strMeal', 'Sconosciuta')
        logging.info(f"Ricetta '{meal_name}' salvata nel DB con ID {id_meal}")
        return True, f"Ricetta '{meal_name}' salvata con successo!"

    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Errore salvataggio DB: {str(e)}")
        traceback.print_exc()
        return False, f"Errore nel salvataggio: {str(e)}"
    finally:
        if conn:
            conn.close()


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
            logging.error(f"generate_answerMC ha restituito un tipo non valido: {type(responseText)}")
            responseText = str(responseText)

        json_match = re.search(r'\{.*\}', responseText, re.DOTALL)

        if json_match:
            try:
                recipe_json = json.loads(json_match.group())
                success, message = save_recipe_to_db(recipe_json)
                if success:
                    return jsonify({'answer': message, 'char': 'ade'})
                else:
                    return jsonify({'answer': f"Errore DB: {message}", 'char': 'ade'})
            except json.JSONDecodeError as e:
                logging.warning(f"JSON non valido: {str(e)}")
                return jsonify({'answer': "Formato JSON non valido. Per favore invia la ricetta in formato corretto.", 'char': 'ade'})
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

CORS(app)
CORS(app, resources={r"/*": {"origins": "*"}})

if __name__ == "__main__":
   app.run(host='0.0.0.0', port=config['server_portc'], debug=True)
