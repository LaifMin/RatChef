import os
import yaml
import logging
import re
import asyncio
import time
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS 
from langchain_ollama import ChatOllama
from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import InMemoryVectorStore



with open("config.yml", 'r') as ymlFiles:
    config = yaml.safe_load(ymlFiles)


app = Flask(__name__, template_folder=".")
logging.basicConfig(level = logging.INFO)

#loading the AI model
agentModel = ChatOllama(model = config['models']['ai'], temperature = 0.7, reasoning = False)

#Rag db 
embeddings = OllamaEmbeddings(model = config['models']['embed'])
vectorStoring = InMemoryVectorStore.load(config['rag_db_path'], embeddings)
retriever = vectorStoring.as_retriever()

#TTS not used now but goes here


#STT not used now but goes here

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
   """
   if not is_prompt_safe(user_prompt):
      logging.warning("Unsafe message detected from user.")
      unsafeString = "Your message was detected as unsafe, thus I cannot answer it; please rephrase it or change the content."
      return {"answer": unsafeString
              }
   
   """
   logging.info("Generating answer for user prompt:" + user_prompt)
   
   context.append(('human', user_prompt))

   #Retrieve relevant documents from RAG
   relevantDocs = retriever.invoke(user_prompt)
   doc_text = "\n".join([d.page_content for d in relevantDocs])
   doc_text = "Usa queste informazioni sulle ricette per rispondere alla domanda:\n" + doc_text + "\nSe non conosci la risposta, dì che non lo sai.\n"
   context[1] = ('system', doc_text)

   answer = agentModel.invoke(context).content
   context.append(('ai', answer))
   return answer

def generate_answerMC(user_prompt, character_name, is_private=False):
   """
   if not is_prompt_safe(user_prompt):
      logging.warning("Unsafe message detected from user.")
      unsafeString = "Your message was detected as unsafe, thus I cannot answer it; please rephrase it or change the content."
      return {"answer": unsafeString
              }
   
   """
   logging.info("Generating answer for user prompt:" + user_prompt)
   target_context = private_contexts[character_name] if is_private else contextDictionary[character_name]
   target_context.append(('human', user_prompt))

   relevantDocs = retriever.invoke(user_prompt)
   doc_text = "\n".join([d.page_content for d in relevantDocs])
   doc_text = "Usa queste informazioni sulle ricette per rispondere alla domanda:\n" + doc_text + "\nSe non conosci la risposta, dì che non lo sai.\n"
   
   if not is_private:
      other_contexts = get_contexts(character_name)
      other_contexts_text = format_contexts(other_contexts)
      doc_text += other_contexts_text
    
   target_context[1] = ('system', doc_text)

   answer = agentModel.invoke(target_context).content
   target_context.append(('ai', answer))
   return answer




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
   userPrompt = request.get_json()
   answer = userPrompt.get('question', 'No question')
   is_private = userPrompt.get('private', False)
   responseText = generate_answerMC(answer, 'Ade', is_private)
   return jsonify({'answer': responseText,
                   'char': 'ade'
                   })

CORS(app)
CORS(app, resources={r"/*": {"origins": "*"}})

if __name__ == "__main__":
   app.run(host='0.0.0.0', port=config['server_portc'], debug=True)
