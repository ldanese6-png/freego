import os
import re
import time
import urllib.request
import urllib.error
import flask
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

app = Flask(__name__)

# --- 1. CONFIGURAZIONE E SICUREZZA ---
uri = os.getenv("DATABASE_URL", "sqlite:///freego.db")
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = uri
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", 'chiave_segreta_freego_produzione_blindata_123') 
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# Variabili di Ambiente
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

def password_sicura(password):
    if len(password) < 8: return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[0-9]", password): return False
    return True

# --- 2. UPLOAD FOTO (BYPASS DIRETTO SUPABASE) ---
def carica_foto_diretta(file_foto, nome_univoco):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return "Errore critico: SUPABASE_URL o SUPABASE_KEY non trovati nelle variabili di ambiente di Vercel."
        
    clean_url = SUPABASE_URL.strip().strip("'").strip('"').rstrip('/')
    clean_key = SUPABASE_KEY.strip().strip("'").strip('"')
    
    upload_url = f"{clean_url}/storage/v1/object/uploads/{nome_univoco}"
    
    req = urllib.request.Request(upload_url, data=file_foto.read(), method='POST')
    req.add_header('apikey', clean_key)
    req.add_header('Authorization', f'Bearer {clean_key}')
    req.add_header('Content-Type', file_foto.content_type)
    
    try:
        urllib.request.urlopen(req)
        return None 
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        return f"ERRORE API {e.code}: {error_body}"
    except Exception as e:
        return f"ERRORE DI RETE/SISTEMA: {str(e)}"

# --- 3. MODELLI DATABASE ---
class Utente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    is_verificato = db.Column(db.Boolean, default=False)
    
    annunci = db.relationship('Annuncio', backref='autore', foreign_keys='Annuncio.utente_id', lazy=True)
    recensioni_ricevute = db.relationship('Recensione', backref='destinatario', foreign_keys='Recensione.destinatario_id', lazy=True)

    @property
    def media_voti(self):
        if not self.recensioni_ricevute:
            return 0
        totale = sum(r.voto for r in self.recensioni_ricevute)
        return round(totale / len(self.recensioni_ricevute), 1)

class Recensione(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    voto = db.Column(db.Integer, nullable=False)
    commento = db.Column(db.Text, nullable=True)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    mittente_id = db.Column(db.Integer, db.ForeignKey('utente.id'), nullable=False)
    destinatario_id = db.Column(db.Integer, db.ForeignKey('utente.id'), nullable=False)
    mittente = db.relationship('Utente', foreign_keys=[mittente_id])

class Annuncio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titolo = db.Column(db.String(100), nullable=False)
    luogo = db.Column(db.String(50), nullable=False)
    descrizione = db.Column(db.Text, nullable=True)
    spedizione = db.Column(db.Boolean, default=False)
    immagine = db.Column(db.String(255), nullable=True) 
    categoria = db.Column(db.String(50), nullable=False, default='Altro')
    utente_id = db.Column(db.Integer, db.ForeignKey('utente.id'), nullable=False)
    acquirente_id = db.Column(db.Integer, db.ForeignKey('utente.id'), nullable=True)
    acquirente = db.relationship('Utente', foreign_keys=[acquirente_id])

class Messaggio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    testo = db.Column(db.Text, nullable=False)
    data_invio = db.Column(db.DateTime, default=datetime.utcnow)
    letto = db.Column(db.Boolean, default=False) 
    mittente_id = db.Column(db.Integer, db.ForeignKey('utente.id'), nullable=False)
    destinatario_id = db.Column(db.Integer, db.ForeignKey('utente.id'), nullable=False)
    annuncio_id = db.Column(db.Integer, db.ForeignKey('annuncio.id'), nullable=True)
    mittente = db.relationship('Utente', foreign_keys=[mittente_id])
    annuncio_rif = db.relationship('Annuncio')

# --- 4. CONTEXT PROCESSORS (Gestione Globale UI) ---
@app.context_processor
def conta_non_letti():
    non_letti = 0
    if session.get('utente_id'):
        non_letti = Messaggio.query.filter_by(destinatario_id=session['utente_id'], letto=False).count()
    return dict(messaggi_non_letti=non_letti)

@app.context_processor
def override_url_for():
    def custom_url_for(endpoint, **values):
        if endpoint == 'static' and 'filename' in values:
            if values['filename'].startswith('uploads/'):
                nome_file = values['filename'].replace('uploads/', '')
                if nome_file == 'default.jpg' or not SUPABASE_URL:
                    return "https://placehold.co/600x400/e2e8f0/94a3b8?text=Nessuna+Foto"
                
                clean_url = SUPABASE_URL.strip().strip("'").strip('"').rstrip('/')
                return f"{clean_url}/storage/v1/object/public/uploads/{nome_file}"
        return flask.url_for(endpoint, **values)
    return dict(url_for=custom_url_for)

# --- 5. ROTTE PUBBLICHE E RICERCA ---
@app.route('/')
def home():
    annunci_dal_db = Annuncio.query.filter_by(acquirente_id=None).order_by(Annuncio.id.desc()).all()
    return render_template('index.html', annunci=annunci_dal_db)

@app.route('/cerca')
def cerca():
    parola_chiave = request.args.get('q', '').strip()
    luogo_chiave = request.args.get('luogo', '').strip()
    categoria_chiave = request.args.get('categoria', '')
    
    ricerca = Annuncio.query.filter_by(acquirente_id=None)
    
    if parola_chiave:
        for parola in parola_chiave.split():
            termine = f"%{parola}%"
            ricerca = ricerca.filter(Annuncio.titolo.ilike(termine) | Annuncio.descrizione.ilike(termine))
    if luogo_chiave:
        ricerca = ricerca.filter(Annuncio.luogo.ilike(f"%{luogo_chiave}%"))
    if categoria_chiave and categoria_chiave != 'Tutte':
        ricerca = ricerca.filter(Annuncio.categoria == categoria_chiave)
    
    annunci_trovati = ricerca.order_by(Annuncio.id.desc()).all()
    return render_template('index.html', annunci=annunci_trovati)

# --- 6. AUTENTICAZIONE E GESTIONE UTENTE ---
@app.route('/registrati', methods=['GET', 'POST'])
def registrati():
    errore = None
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        if Utente.query.filter_by(email=email).first():
            errore = "Questa email è già registrata nel sistema."
        elif not password_sicura(password):
            errore = "Password debole: usa almeno 8 caratteri, una maiuscola, una minuscola e un numero."
        else:
            nuovo_utente = Utente(nome=nome, email=email, password=generate_password_hash(password))
            db.session.add(nuovo_utente)
            db.session.commit()
            
            token = s.dumps(email, salt='email-confirm')
            link = url_for('conferma_email', token=token, _external=True)
            print(f"[DEBUG LOG] Link di conferma email generato: {link}")
            
            return render_template('login.html', messaggio="Registrazione completata con successo! Ora puoi accedere.")
            
    return render_template('registrazione.html', errore=errore)

@app.route('/conferma_email/<token>')
def conferma_email(token):
    try:
        email = s.loads(token, salt='email-confirm', max_age=3600)
    except (SignatureExpired, BadTimeSignature):
        return "Il link di verifica è scaduto o non valido. Richiedine uno nuovo dal tuo profilo."
    
    utente = Utente.query.filter_by(email=email).first()
    if utente:
        utente.is_verificato = True
        db.session.commit()
        return redirect(url_for('profilo'))
    return redirect(url_for('home'))

@app.route('/verifica_email')
def verifica_email():
    if 'utente_id' not in session: 
        return redirect(url_for('login'))
        
    utente = Utente.query.get(session['utente_id'])
    if not utente.is_verificato:
        token = s.dumps(utente.email, salt='email-confirm')
        link = url_for('conferma_email', token=token, _external=True)
        print(f"[DEBUG LOG] Link di rinvio conferma: {link}")
        
    return redirect(url_for('profilo'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    errore = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        utente = Utente.query.filter_by(email=email).first()
        if utente and check_password_hash(utente.password, password):
            session['utente_id'] = utente.id
            session['utente_nome'] = utente.nome
            return redirect(url_for('home'))
        errore = "Credenziali non valide."
    return render_template('login.html', errore=errore)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/profilo')
def profilo():
    if not session.get('utente_id'): return redirect(url_for('login'))
    utente_corrente = Utente.query.get_or_404(session['utente_id'])
    miei_annunci = Annuncio.query.filter_by(utente_id=session['utente_id']).order_by(Annuncio.id.desc()).all()
    return render_template('profilo.html', utente=utente_corrente, annunci=miei_annunci)

@app.route('/cambia_password', methods=['GET', 'POST'])
def cambia_password():
    if not session.get('utente_id'): return redirect(url_for('login'))
    errore, messaggio = None, None
    if request.method == 'POST':
        utente = Utente.query.get(session['utente_id'])
        vecchia_pass = request.form.get('vecchia_password')
        nuova_pass = request.form.get('nuova_password')
        
        if not check_password_hash(utente.password, vecchia_pass):
            errore = "La password attuale non è corretta."
        elif not password_sicura(nuova_pass):
            errore = "La nuova password non rispetta i criteri di sicurezza."
        else:
            utente.password = generate_password_hash(nuova_pass)
            db.session.commit()
            messaggio = "Password aggiornata con successo."
    return render_template('cambia_password.html', errore=errore, messaggio=messaggio)

# --- 7. GESTIONE ANNUNCI CORE ---
@app.route('/nuovo_annuncio', methods=['GET', 'POST'])
def nuovo_annuncio():
    if not session.get('utente_id'): return redirect(url_for('login'))
    
    if request.method == 'POST':
        file_foto = request.files.get('immagine')
        nome_immagine_db = "default.jpg" 
        
        if file_foto and file_foto.filename != '':
            estensione = file_foto.filename.rsplit('.', 1)[1].lower() if '.' in file_foto.filename else 'jpg'
            nome_univoco = f"img_{int(time.time())}_{session['utente_id']}.{estensione}"
            
            errore_upload = carica_foto_diretta(file_foto, nome_univoco)
            if errore_upload:
                return f"""
                <div style="font-family: sans-serif; padding: 40px; color: #333; max-width: 800px; margin: auto;">
                    <h1 style="color: #d32f2f;">Errore in fase di caricamento immagine</h1>
                    <p>Il server ha bloccato l'operazione. Dettagli tecnici:</p>
                    <pre style="background: #f4f4f4; padding: 15px; border-left: 4px solid #d32f2f; overflow-x: auto;">{errore_upload}</pre>
                    <a href="/nuovo_annuncio" style="display: inline-block; margin-top: 20px; padding: 10px 20px; background: #007bff; color: white; text-decoration: none; border-radius: 5px;">Torna Indietro</a>
                </div>
                """
            
            nome_immagine_db = nome_univoco

        nuovo = Annuncio(
            titolo=request.form.get('titolo', '').strip(), 
            luogo=request.form.get('luogo', '').strip(), 
            categoria=request.form.get('categoria', 'Altro'),
            descrizione=request.form.get('descrizione', '').strip(), 
            spedizione=True if request.form.get('spedizione') else False,
            immagine=nome_immagine_db, 
            utente_id=session['utente_id']
        )
        db.session.add(nuovo)
        db.session.commit()
        return redirect(url_for('profilo'))
        
    return render_template('nuovo_annuncio.html')

@app.route('/modifica_annuncio/<int:id>', methods=['GET', 'POST'])
def modifica_annuncio(id):
    if not session.get('utente_id'): return redirect(url_for('login'))
    
    annuncio = Annuncio.query.get_or_404(id)
    if annuncio.utente_id != session['utente_id']: 
        return redirect(url_for('profilo'))
    
    if request.method == 'POST':
        annuncio.titolo = request.form.get('titolo', '').strip()
        annuncio.luogo = request.form.get('luogo', '').strip()
        annuncio.categoria = request.form.get('categoria', 'Altro')
        annuncio.descrizione = request.form.get('descrizione', '').strip()
        annuncio.spedizione = True if request.form.get('spedizione') else False
        
        file_foto = request.files.get('immagine')
        if file_foto and file_foto.filename != '':
            estensione = file_foto.filename.rsplit('.', 1)[1].lower() if '.' in file_foto.filename else 'jpg'
            nome_univoco = f"img_{int(time.time())}_{session['utente_id']}.{estensione}"
            
            errore_upload = carica_foto_diretta(file_foto, nome_univoco)
            if errore_upload:
                return f"""
                <div style="font-family: sans-serif; padding: 40px; color: #333; max-width: 800px; margin: auto;">
                    <h1 style="color: #d32f2f;">Errore in fase di modifica immagine</h1>
                    <pre style="background: #f4f4f4; padding: 15px; border-left: 4px solid #d32f2f; overflow-x: auto;">{errore_upload}</pre>
                    <a href="/modifica_annuncio/{id}" style="display: inline-block; margin-top: 20px; padding: 10px 20px; background: #007bff; color: white; text-decoration: none; border-radius: 5px;">Torna Indietro</a>
                </div>
                """
            
            annuncio.immagine = nome_univoco
            
        db.session.commit()
        return redirect(url_for('profilo'))
        
    return render_template('modifica_annuncio.html', annuncio=annuncio)

@app.route('/annuncio/<int:id>')
def mostra_annuncio(id):
    annuncio_trovato = Annuncio.query.get_or_404(id)
    return render_template('dettaglio.html', annuncio=annuncio_trovato)

@app.route('/elimina/<int:id>', methods=['POST'])
def elimina_annuncio(id):
    annuncio = Annuncio.query.get_or_404(id)
    if annuncio.utente_id == session.get('utente_id'):
        db.session.delete(annuncio)
        db.session.commit()
    return redirect(url_for('profilo'))

@app.route('/conferma_regalo/<int:annuncio_id>/<int:acquirente_id>', methods=['POST'])
def conferma_regalo(annuncio_id, acquirente_id):
    if not session.get('utente_id'): return redirect(url_for('login'))
    annuncio = Annuncio.query.get_or_404(annuncio_id)
    
    if annuncio.utente_id == session['utente_id']: 
        annuncio.acquirente_id = acquirente_id
        db.session.commit()
        
    return redirect(url_for('chat', interlocutore_id=acquirente_id))

# --- 8. PROFILI PUBBLICI E RECENSIONI ---
@app.route('/utente/<int:id>')
def profilo_pubblico(id):
    utente_cercato = Utente.query.get_or_404(id)
    annunci_utente = Annuncio.query.filter_by(utente_id=id, acquirente_id=None).order_by(Annuncio.id.desc()).all()
    recensioni = Recensione.query.filter_by(destinatario_id=id).order_by(Recensione.data.desc()).all()
    
    puo_recensire = False
    ha_comprato = False
    
    if session.get('utente_id'):
        io = session['utente_id']
        oggetti_ricevuti = Annuncio.query.filter_by(utente_id=id, acquirente_id=io).count()
        recensioni_lasciate = Recensione.query.filter_by(mittente_id=io, destinatario_id=id).count()
        
        if oggetti_ricevuti > 0:
            ha_comprato = True
        if oggetti_ricevuti > recensioni_lasciate:
            puo_recensire = True

    return render_template('profilo_pubblico.html', utente_pubblico=utente_cercato, annunci=annunci_utente, recensioni=recensioni, puo_recensire=puo_recensire, ha_comprato=ha_comprato)

@app.route('/lascia_recensione/<int:destinatario_id>', methods=['POST'])
def lascia_recensione(destinatario_id):
    if not session.get('utente_id'): return redirect(url_for('login'))
    
    io = session['utente_id']
    oggetti_ricevuti = Annuncio.query.filter_by(utente_id=destinatario_id, acquirente_id=io).count()
    recensioni_lasciate = Recensione.query.filter_by(mittente_id=io, destinatario_id=destinatario_id).count()
    
    if oggetti_ricevuti > recensioni_lasciate:
        voto = request.form.get('voto')
        commento = request.form.get('commento', '').strip()
        
        if voto and voto.isdigit():
            nuova_recensione = Recensione(voto=int(voto), commento=commento, mittente_id=io, destinatario_id=destinatario_id)
            db.session.add(nuova_recensione)
            db.session.commit()
        
    return redirect(url_for('profilo_pubblico', id=destinatario_id))

# --- 9. SISTEMA DI MESSAGGISTICA (CHAT) ---
@app.route('/invia_messaggio/<int:annuncio_id>', methods=['POST'])
def invia_messaggio(annuncio_id):
    if not session.get('utente_id'): return redirect(url_for('login'))
    
    annuncio = Annuncio.query.get_or_404(annuncio_id)
    testo = request.form.get('testo', '').strip()
    
    if testo:
        nuovo_msg = Messaggio(
            testo=testo, 
            mittente_id=session['utente_id'], 
            destinatario_id=annuncio.utente_id, 
            annuncio_id=annuncio.id, 
            letto=False
        )
        db.session.add(nuovo_msg)
        db.session.commit()
        
    return redirect(url_for('chat', interlocutore_id=annuncio.utente_id))

@app.route('/messaggi')
def messaggi():
    if not session.get('utente_id'): return redirect(url_for('login'))
    io = session['utente_id']
    
    tutti_i_messaggi = Messaggio.query.filter(
        (Messaggio.mittente_id == io) | (Messaggio.destinatario_id == io)
    ).order_by(Messaggio.data_invio.desc()).all()
    
    conversazioni = {}
    for msg in tutti_i_messaggi:
        altro_utente_id = msg.destinatario_id if msg.mittente_id == io else msg.mittente_id
        if altro_utente_id not in conversazioni:
            altro_utente = Utente.query.get(altro_utente_id)
            if altro_utente:
                da_leggere = True if (msg.destinatario_id == io and not msg.letto) else False
                conversazioni[altro_utente_id] = {
                    'interlocutore': altro_utente, 
                    'ultimo_messaggio': msg, 
                    'da_leggere': da_leggere
                }
                
    return render_template('messaggi.html', conversazioni=list(conversazioni.values()))

@app.route('/chat/<int:interlocutore_id>', methods=['GET', 'POST'])
def chat(interlocutore_id):
    if not session.get('utente_id'): return redirect(url_for('login'))
    
    io = session['utente_id']
    interlocutore = Utente.query.get_or_404(interlocutore_id)
    
    if request.method == 'POST':
        testo = request.form.get('testo', '').strip()
        if testo:
            nuovo_msg = Messaggio(testo=testo, mittente_id=io, destinatario_id=interlocutore_id, annuncio_id=None, letto=False)
            db.session.add(nuovo_msg)
            db.session.commit()
        return redirect(url_for('chat', interlocutore_id=interlocutore_id))
    
    conversazione = Messaggio.query.filter(
        ((Messaggio.mittente_id == io) & (Messaggio.destinatario_id == interlocutore_id)) | 
        ((Messaggio.mittente_id == interlocutore_id) & (Messaggio.destinatario_id == io))
    ).order_by(Messaggio.data_invio.asc()).all()
    
    # Segna come letti
    aggiornato = False
    for msg in conversazione:
        if msg.destinatario_id == io and not msg.letto: 
            msg.letto = True
            aggiornato = True
            
    if aggiornato:
        db.session.commit()
        
    annunci_ids = set([m.annuncio_id for m in conversazione if m.annuncio_id])
    in_trattativa = Annuncio.query.filter(Annuncio.id.in_(annunci_ids), Annuncio.utente_id == io, Annuncio.acquirente_id == None).all()
    ha_ricevuto_da_me = Annuncio.query.filter_by(utente_id=interlocutore_id, acquirente_id=io).first()

    return render_template('chat.html', messaggi=conversazione, interlocutore=interlocutore, in_trattativa=in_trattativa, ha_ricevuto=ha_ricevuto_da_me)

# --- INIZIALIZZAZIONE DATABASE ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)