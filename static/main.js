function setbusy(busy) {
  document.getElementById('input').disabled    = busy;
  const btn = document.getElementById('send-btn');
  const uploadBtn = document.getElementById('upload-btn');
  btn.disabled    = busy;
  uploadBtn.disabled = busy;
  btn.textContent = busy ? 'Attendi...' : 'Invia';
}

function addMsg(content, type) {
  const wrap = document.getElementById('messages');
  const p    = document.createElement('p');
  p.className = `msg ${type}`;
  if (type === 'user' || type === 'ai') {
    const lbl = document.createElement('span');
    lbl.className   = 'msg-label';
    lbl.textContent = type === 'user' ? 'Tu' : 'AI';
    p.appendChild(lbl);
  }
  p.appendChild(document.createTextNode(content));
  wrap.appendChild(p);
  wrap.scrollTop = wrap.scrollHeight;
  return p;
}

let sessionId = null;

async function sendMessage(e) {
  e.preventDefault();
  const input   = document.getElementById('input');
  const message = input.value.trim();
  if (!message) return;

  addMsg(message, 'user');
  input.value = '';
  setbusy(true);

  const loader = addMsg('Cerco nel database...', 'system');

  try {
    const reqBody = { message };
    if (sessionId) {
      reqBody.session_id = sessionId;
    }

    const res  = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody)
    });
    const data = await res.json();
    loader.remove();

    if (data.session_id) {
      sessionId = data.session_id;
    }

    const reply = data.answer || data.error || 'Nessuna risposta.';
    const msg   = addMsg(reply, data.error ? 'error' : 'ai');

    if (data.sql) {
      const note = document.createElement('span');
      note.className   = 'sql-note';
      note.textContent = 'SQL: ' + data.sql;
      msg.appendChild(note);
    }
  } catch {
    loader.remove();
    addMsg('Errore di connessione.', 'error');
  } finally {
    setbusy(false);
    document.getElementById('input').focus();
  }
}

async function uploadPDF(fileInput) {
  const file = fileInput.files[0];
  if (!file) return;

  addMsg(`📎 Upload: ${file.name}`, 'user');
  setbusy(true);

  const loader = addMsg('Elaborazione PDF in corso... Potrebbe richiedere qualche minuto.', 'system');

  try {
    const formData = new FormData();
    formData.append('file', file);

    const res = await fetch('/upload', {
      method: 'POST',
      body: formData
      // NON impostare Content-Type, il browser lo gestisce automaticamente con il boundary
    });
    const data = await res.json();
    loader.remove();

    if (data.error) {
      addMsg(data.error, 'error');
    } else {
      let summary = `✅ Importate ${data.imported} ricette da ${data.total_pages} pagine.`;
      if (data.skipped_pages > 0) {
        summary += `\n📄 Pagine senza ricette: ${data.skipped_pages}`;
      }
      if (data.errors && data.errors.length > 0) {
        summary += `\n⚠️ Errori: ${data.errors.length}`;
        data.errors.forEach(err => { summary += `\n   - ${err}`; });
      }
      addMsg(summary, 'ai');
    }
  } catch {
    loader.remove();
    addMsg('Errore di connessione durante upload.', 'error');
  } finally {
    setbusy(false);
    fileInput.value = ''; // Reset per permettere re-upload dello stesso file
    document.getElementById('input').focus();
  }
}