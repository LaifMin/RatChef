

# Server side per la gestione di più workers contemporaneamente 
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import re
import logging
import json
import time
import requests
import uuid



app = Flask(__name__, template_folder="templates")
CORS(app)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

workerDatabase = {}
chat_sessions = {}  # Dizionario per mantenere lo storico: { session_id: [{"role": "human", "content": "..."}] }

@app.route('/registerWorker', methods=['POST'])
def registerWorker() -> str:

    data = request.get_json()

    workerID = data.get("worker_id")
    workerURL = data.get("worker_url")

    if not workerID or not workerURL:
        return jsonify({'error': 'data missing!!'}), 400

    workerDatabase[workerID] = {
        'url' : workerURL,
        'last_seen' : time.time(),
        'status' : 'ready'

    }

    logging.info(f"Worker connected - ID: {workerID} | URL: {workerURL}")
    return jsonify({'status' : 'Registered without problems'}), 200


def load_workers():
    return list(workerDatabase.keys())


def who_is_ready() -> str:

    workers = load_workers()
    if not workers:
        logging.warning("Workers currently connected -> 0")
        return None

    for worker_id in workers:
        worker = workerDatabase.get(worker_id)
        if worker and worker.get('status') == 'ready':
            return worker_id

    return None



@app.route("/")
def index():
    return render_template("index.html")

@app.route('/chat', methods=['POST'])
def chat():
  
    data = request.get_json()
    
    if not data or "message" not in data:
        return jsonify({"error": "Nessun messaggio fornito"}), 400
        
    message = data["message"]
    session_id = data.get("session_id")
    
    # Se non c'è una sessione, ne creiamo una nuova
    if not session_id:
        session_id = str(uuid.uuid4())
        
    if session_id not in chat_sessions:
        chat_sessions[session_id] = []
        
    # Aggiungiamo il messaggio dell'utente allo storico
    chat_sessions[session_id].append({"role": "human", "content": message})
    
    worker_id = who_is_ready()
    if not worker_id:
        # Se fallisce rimuoviamo l'ultimo messaggio per permettere il retry
        chat_sessions[session_id].pop()
        return jsonify({"error": "Tutti i worker sono offline o occupati"}), 503
        
    worker_url = workerDatabase[worker_id]["url"]
    
    logging.info(f"Inoltro richiesta a {worker_id} ({worker_url}) per sessione {session_id}")
    
    try:
        worker_response = requests.post(
            f"{worker_url}/chat", 
            json={"history": chat_sessions[session_id]}, # Inviamo lo STORICO, non solo il messaggio
            timeout=300
        )
        
        # Se la richiesta va a buon fine, ritorniamo la risposta all'utente
        if worker_response.status_code == 200:
            resp_data = worker_response.json()
            resp_data["worker_used"] = worker_id  # Aggiungiamo il worker che ha gestito la richiesta
            resp_data["session_id"] = session_id  # Restituiamo il session_id al frontend
            
            # Aggiungiamo la risposta dell'AI allo storico
            if "answer" in resp_data:
                chat_sessions[session_id].append({"role": "ai", "content": resp_data["answer"]})
                
            return jsonify(resp_data), 200
        else:
            chat_sessions[session_id].pop() # Revert
            return jsonify({"error": f"Worker ha restituito un errore {worker_response.status_code}"}), 500
            
    except requests.exceptions.RequestException as e:
       
        logging.error(f"Worker {worker_id} non raggiungibile: {e}")
        workerDatabase[worker_id]["status"] = "offline"
        return jsonify({"error": "Worker disconnesso durante l'elaborazione, riprovare"}), 500

@app.route('/upload', methods=['POST'])
def upload():
    """
    Riceve un PDF via multipart/form-data e lo inoltra a un worker disponibile.
    """
    if 'file' not in request.files:
        return jsonify({"error": "Nessun file allegato"}), 400

    pdf_file = request.files['file']
    if pdf_file.filename == '':
        return jsonify({"error": "Nome file vuoto"}), 400

    worker_id = who_is_ready()
    if not worker_id:
        return jsonify({"error": "Nessun worker disponibile"}), 503

    worker_url = workerDatabase[worker_id]["url"]
    logging.info(f"Inoltro PDF '{pdf_file.filename}' a {worker_id} ({worker_url})")

    try:
        worker_response = requests.post(
            f"{worker_url}/upload",
            files={"file": (pdf_file.filename, pdf_file.stream, pdf_file.content_type)},
            timeout=None
        )

        if worker_response.status_code == 200:
            resp_data = worker_response.json()
            resp_data["worker_used"] = worker_id
            return jsonify(resp_data), 200
        else:
            return jsonify({"error": f"Worker ha restituito errore {worker_response.status_code}"}), 500

    except requests.exceptions.RequestException as e:
        logging.error(f"Worker {worker_id} non raggiungibile durante upload: {e}")
        workerDatabase[worker_id]["status"] = "offline"
        return jsonify({"error": "Worker disconnesso durante l'elaborazione"}), 500


if __name__ == '__main__':
  
    app.run(debug=True, port=5000, use_reloader=False)

    