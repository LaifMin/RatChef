import os
import yaml
import logging
import re
import json
import copy
import traceback
from io import BytesIO
from pypdf import PdfReader
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from langchain_ollama import ChatOllama
from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import InMemoryVectorStore
from db_utils import save_recipe_to_db, format_save_results, get_existing_meal_names


with open("config.yml", 'r') as f:
    config = yaml.safe_load(f)

db_conf = config['db']

app = Flask(__name__, template_folder=".")
logging.basicConfig(level=logging.INFO)

agentModel = ChatOllama(model=config['models']['ai'], temperature=0.7, reasoning=False)

embeddings  = OllamaEmbeddings(model=config['models']['embed'])
vectorStoring = InMemoryVectorStore.load(config['rag_db_path'], embeddings)
retriever   = vectorStoring.as_retriever()


context = [
    ('system', config['prompts']['default']),
    ('system', '')
]
context1 = [('system', config['prompts']['Cannavacciuolo']), ('system', '')]
context2 = [('system', config['prompts']['MysteryChef']),    ('system', '')]
context3 = [('system', config['prompts']['ade']),            ('system', '')]

contextDictionary = {
    'Cannavacciuolo': context1,
    'MysteryChef':    context2,
    'Ade':            context3
}
private_contexts = {
    'Cannavacciuolo': [('system', config['prompts']['Cannavacciuolo']), ('system', '')],
    'MysteryChef':    [('system', config['prompts']['MysteryChef']),    ('system', '')],
    'Ade':            [('system', config['prompts']['ade']),            ('system', '')]
}


def generate_answer(user_prompt):
    context.append(('human', user_prompt))
    docs = retriever.invoke(user_prompt)
    doc_text = "Usa queste informazioni:\n" + "\n".join(d.page_content for d in docs)
    context[1] = ('system', doc_text)
    answer = agentModel.invoke(context).content
    context.append(('ai', answer))
    return answer


def generate_answerMC(user_prompt, character_name, is_private=False):
    target_context = copy.deepcopy(
        private_contexts[character_name] if is_private else contextDictionary[character_name]
    )

    if character_name == 'Ade':
        existing = get_existing_meal_names(db_conf)
        existing_str = ', '.join(existing) if existing else 'nessuna'
        doc_text = (
            f"Ricette già nel database (NON reinserire queste): {existing_str}\n"
            "Estrai SOLO ricette non presenti in questa lista."
        )
    else:
        docs = retriever.invoke(user_prompt)
        doc_text = "Usa queste informazioni:\n" + "\n".join(d.page_content for d in docs)

    target_context[1] = ('system', doc_text)
    target_context.append(('human', user_prompt))
    return agentModel.invoke(target_context).content


def extract_json_from_text(text):
    text = re.sub(r'```(?:json)?', '', text).strip()
    match = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
        if isinstance(parsed, dict):
            parsed = [parsed]
        return [r for r in parsed if r.get('meal', {}).get('strMeal', '').strip()]
    except json.JSONDecodeError:
        return None


def extract_recipes_from_text(raw_text):
    existing = get_existing_meal_names(db_conf)
    existing_str = ', '.join(existing) if existing else 'nessuna'

    chunks = [c.strip() for c in re.split(r'\n{3,}|\f', raw_text) if len(c.strip()) > 80]
    if not chunks:
        chunks = [raw_text[:6000]]

    all_recipes = []
    for chunk in chunks:
        prompt = (
            "Ricette già nel database (non reinserire): " + existing_str + "\n\n"
            "Dal testo seguente estrai le ricette non ancora presenti e restituisci SOLO un array JSON, "
            "senza backtick né altro testo:\n"
            '[{"meal":{"strMeal":"","strInstructions":"","strTime":"","strDifficulty":"","idCategory":""},'
            '"ingredients":[{"strIngredient":"","strQta":"","strUnit":""}],'
            '"prep":[{"strDescription":"","intProgressive":1}]}]\n\n'
            "TESTO:\n" + chunk[:3000]
        )
        response = agentModel.invoke([('human', prompt)]).content
        parsed = extract_json_from_text(response)
        if parsed:
            all_recipes.extend(parsed)

    return all_recipes


@app.route('/')
def index():
    return render_template('index.html', history=context)


@app.route('/question', methods=['POST'])
def question():
    body = request.get_json()
    answer = generate_answer(body.get('question', ''))
    return jsonify({'answer': answer, 'ai': 'yes'})


@app.route('/cannavacciuolo', methods=['POST'])
def cannavacciuolo():
    body = request.get_json()
    answer = generate_answerMC(body.get('question', ''), 'Cannavacciuolo', body.get('private', False))
    return jsonify({'answer': answer, 'char': 'cannavacciuolo'})


@app.route('/mysterychef', methods=['POST'])
def mysterychef():
    body = request.get_json()
    answer = generate_answerMC(body.get('question', ''), 'MysteryChef', body.get('private', False))
    return jsonify({'answer': answer, 'char': 'mysterychef'})


@app.route('/ade', methods=['POST'])
def ade():
    try:
        body       = request.get_json()
        user_text  = body.get('question', '')
        is_private = body.get('private', False)

        response_text = generate_answerMC(user_text, 'Ade', is_private)
        parsed = extract_json_from_text(response_text)

        if parsed:
            results = save_recipe_to_db(parsed, db_conf)
            return jsonify({'answer': format_save_results(results), 'char': 'ade'})

        return jsonify({'answer': response_text, 'char': 'ade'})

    except Exception as e:
        logging.error(f"Errore /ade: {e}")
        return jsonify({'answer': f"Errore interno: {e}", 'char': 'ade'}), 500


@app.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    try:
        if 'pdf' not in request.files:
            return jsonify({'answer': 'Nessun file PDF fornito.', 'char': 'ade'}), 400

        reader = PdfReader(BytesIO(request.files['pdf'].read()))
        text   = ''.join(page.extract_text() or '' for page in reader.pages)

        if not text.strip():
            return jsonify({'answer': 'Impossibile estrarre testo dal PDF.', 'char': 'ade'}), 400

        recipes = extract_recipes_from_text(text)
        if not recipes:
            return jsonify({'answer': 'Nessuna ricetta trovata nel PDF.', 'char': 'ade'}), 400

        results = save_recipe_to_db(recipes, db_conf)
        return jsonify({'answer': format_save_results(results), 'char': 'ade'})

    except Exception as e:
        logging.error(f"Errore /upload_pdf: {e}")
        traceback.print_exc()
        return jsonify({'answer': f"Errore: {e}", 'char': 'ade'}), 500


@app.route('/dataManager', methods=['POST'])
def dataManager():
    body = request.get_json()
    answer = generate_answer(body.get('question', ''))
    return jsonify({'answer': answer, 'ai': 'yes'})


CORS(app, resources={r"/*": {"origins": "*"}})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=config['server_portc'], debug=True)
