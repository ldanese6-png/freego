import os
import re
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

app = Flask(__name__)

# --- CONFIGURAZIONE ---
# Cerca la variabile di Vercel, se non c'è usa il database locale SQLite
uri = os.getenv("DATABASE_URL", "sqlite:///freego.db")
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = uri
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", 'chiave_segreta_freego_123') 
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Generatore di Token sicuri per l'email
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# --- FUNZIONE CONTROLLO PASSWORD ---
def password_sicura(password):
    # Almeno 8 caratteri, 1 maiuscola, 1 minuscola, 1 numero
    if len(password) < 8: return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[0-9]", password): return False
    return True

# --- TABELLE DEL DATABASE ---

class Utente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
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

@app.context_processor
def conta_non_letti():
    non_letti = 0
    if session.get('utente_id'):
        non_letti = Messaggio.query.filter_by(destinatario_id=session['utente_id'], letto=False).count()
    return dict(messaggi_non_letti=non_letti)

# --- ROTTE PRINCIPALI ---

@app.route('/')
def home():
    annunci_dal_db = Annuncio.query.filter_by(acquirente_id=None).order_by(Annuncio.id.desc()).all()
    return render_template('index.html', annunci=annunci_dal_db)

@app.route('/cerca')
def cerca():
    parola_chiave = request.args.get('q', '')
    luogo_chiave = request.args.get('luogo', '')
    categoria_chiave = request.args.get('categoria', '')
    
    ricerca = Annuncio.query.filter_by(acquirente_id=None)
    
    if parola_chiave:
        parole = parola_chiave.split()
        for parola in parole:
            termine = f"%{parola}%"
            ricerca = ricerca.filter(Annuncio.titolo.ilike(termine) | Annuncio.descrizione.ilike(termine))
    if luogo_chiave:
        ricerca = ricerca.filter(Annuncio.luogo.ilike(f"%{luogo_chiave}%"))
    if categoria_chiave and categoria_chiave != 'Tutte':
        ricerca = ricerca.filter(Annuncio.categoria == categoria_chiave)
    
    annunci_trovati = ricerca.order_by(Annuncio.id.desc()).all()
    return render_template('index.html', annunci=annunci_trovati)

@app.route('/registrati', methods=['GET', 'POST'])
def registrati():
    errore = None
    messaggio = None
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        password = request.form['password']
        
        if Utente.query.filter_by(email=email).first():
            errore = "Questa email è già registrata!"
        elif not password_sicura(password):
            errore = "La password deve contenere almeno 8 caratteri, una lettera maiuscola, una minuscola e un numero."
        else:
            nuovo_utente = Utente(nome=nome, email=email, password=generate_password_hash(password))
            db.session.add(nuovo_utente)
            db.session.commit()
            
            # Generazione Token Email
            token = s.dumps(email, salt='email-confirm')
            link = url_for('conferma_email', token=token, _external=True)
            
            # STAMPA IL LINK NEL TERMINALE DI VS CODE PER POTERLO CLICCARE
            print("\n" + "="*60)
            print(f" SIMULAZIONE EMAIL INVIATA A: {email} ")
            print(" Clicca su questo link per verificare l'account:")
            print(f" {link} ")
            print("="*60 + "\n")
            
            messaggio = "Registrazione completata! Controlla il terminale di VS Code per il link di verifica."
            return render_template('login.html', messaggio=messaggio)
            
    return render_template('registrazione.html', errore=errore)

@app.route('/conferma_email/<token>')
def conferma_email(token):
    try:
        # Il token scade dopo 3600 secondi (1 ora)
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
    if 'utente_id' not in session: return redirect(url_for('login'))
    utente = Utente.query.get(session['utente_id'])
    
    if not utente.is_verificato:
        token = s.dumps(utente.email, salt='email-confirm')
        link = url_for('conferma_email', token=token, _external=True)
        print("\n" + "="*60)
        print(f" NUOVA SIMULAZIONE EMAIL INVIATA A: {utente.email} ")
        print(" Clicca su questo link per verificare l'account:")
        print(f" {link} ")
        print("="*60 + "\n")
        
    return redirect(url_for('profilo'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    errore = None
    if request.method == 'POST':
        utente = Utente.query.filter_by(email=request.form['email']).first()
        if utente and check_password_hash(utente.password, request.form['password']):
            session['utente_id'] = utente.id
            session['utente_nome'] = utente.nome
            return redirect(url_for('home'))
        errore = "Email o password errati."
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
        nuova_pass = request.form['nuova_password']
        
        if not check_password_hash(utente.password, request.form['vecchia_password']):
            errore = "Vecchia password non corretta."
        elif not password_sicura(nuova_pass):
            errore = "La nuova password deve contenere almeno 8 caratteri, una maiuscola, una minuscola e un numero."
        else:
            utente.password = generate_password_hash(nuova_pass)
            db.session.commit()
            messaggio = "Password aggiornata con successo!"
    return render_template('cambia_password.html', errore=errore, messaggio=messaggio)

# --- ROTTE ANNUNCI ---

@app.route('/nuovo_annuncio', methods=['GET', 'POST'])
def nuovo_annuncio():
    if not session.get('utente_id'): return redirect(url_for('login'))
    if request.method == 'POST':
        file_foto = request.files.get('immagine')
        nome_immagine_db = "default.jpg" 
        if file_foto and file_foto.filename != '':
            nome_file = secure_filename(file_foto.filename)
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
            percorso_completo = os.path.join(app.config['UPLOAD_FOLDER'], nome_file)
            file_foto.save(percorso_completo)
            nome_immagine_db = nome_file 

        nuovo = Annuncio(
            titolo=request.form['titolo'], luogo=request.form['luogo'], 
            categoria=request.form.get('categoria', 'Altro'),
            descrizione=request.form.get('descrizione', ''), 
            spedizione=True if request.form.get('spedizione') else False,
            immagine=nome_immagine_db, utente_id=session['utente_id']
        )
        db.session.add(nuovo)
        db.session.commit()
        return redirect(url_for('profilo'))
    return render_template('nuovo_annuncio.html')

@app.route('/modifica_annuncio/<int:id>', methods=['GET', 'POST'])
def modifica_annuncio(id):
    if not session.get('utente_id'): return redirect(url_for('login'))
    annuncio = Annuncio.query.get_or_404(id)
    if annuncio.utente_id != session['utente_id']: return redirect(url_for('profilo'))
    if request.method == 'POST':
        annuncio.titolo = request.form['titolo']
        annuncio.luogo = request.form['luogo']
        annuncio.categoria = request.form.get('categoria', 'Altro')
        annuncio.descrizione = request.form.get('descrizione', '')
        annuncio.spedizione = True if request.form.get('spedizione') else False
        file_foto = request.files.get('immagine')
        if file_foto and file_foto.filename != '':
            nome_file = secure_filename(file_foto.filename)
            file_foto.save(os.path.join(app.config['UPLOAD_FOLDER'], nome_file))
            annuncio.immagine = nome_file
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

# --- ROTTE RECENSIONI E PROFILO PUBBLICO ---

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
        nuova_recensione = Recensione(
            voto=request.form.get('voto'), commento=request.form.get('commento'), 
            mittente_id=io, destinatario_id=destinatario_id
        )
        db.session.add(nuova_recensione)
        db.session.commit()
        
    return redirect(url_for('profilo_pubblico', id=destinatario_id))

# --- CHAT E MESSAGGI ---

@app.route('/invia_messaggio/<int:annuncio_id>', methods=['POST'])
def invia_messaggio(annuncio_id):
    if not session.get('utente_id'): return redirect(url_for('login'))
    annuncio = Annuncio.query.get_or_404(annuncio_id)
    testo = request.form.get('testo')
    if testo:
        db.session.add(Messaggio(testo=testo, mittente_id=session['utente_id'], destinatario_id=annuncio.utente_id, annuncio_id=annuncio.id, letto=False))
        db.session.commit()
    return redirect(url_for('chat', interlocutore_id=annuncio.utente_id))

@app.route('/messaggi')
def messaggi():
    if not session.get('utente_id'): return redirect(url_for('login'))
    io = session['utente_id']
    tutti_i_messaggi = Messaggio.query.filter((Messaggio.mittente_id == io) | (Messaggio.destinatario_id == io)).order_by(Messaggio.data_invio.desc()).all()
    conversazioni = {}
    for msg in tutti_i_messaggi:
        altro_utente_id = msg.destinatario_id if msg.mittente_id == io else msg.mittente_id
        if altro_utente_id not in conversazioni:
            altro_utente = Utente.query.get(altro_utente_id)
            if altro_utente:
                da_leggere = True if (msg.destinatario_id == io and not msg.letto) else False
                conversazioni[altro_utente_id] = {'interlocutore': altro_utente, 'ultimo_messaggio': msg, 'da_leggere': da_leggere}
    return render_template('messaggi.html', conversazioni=list(conversazioni.values()))

@app.route('/chat/<int:interlocutore_id>', methods=['GET', 'POST'])
def chat(interlocutore_id):
    if not session.get('utente_id'): return redirect(url_for('login'))
    io = session['utente_id']
    interlocutore = Utente.query.get_or_404(interlocutore_id)
    if request.method == 'POST':
        if request.form.get('testo'):
            db.session.add(Messaggio(testo=request.form.get('testo'), mittente_id=io, destinatario_id=interlocutore_id, annuncio_id=None, letto=False))
            db.session.commit()
        return redirect(url_for('chat', interlocutore_id=interlocutore_id))
    
    conversazione = Messaggio.query.filter(
        ((Messaggio.mittente_id == io) & (Messaggio.destinatario_id == interlocutore_id)) | 
        ((Messaggio.mittente_id == interlocutore_id) & (Messaggio.destinatario_id == io))
    ).order_by(Messaggio.data_invio.asc()).all()
    
    for msg in conversazione:
        if msg.destinatario_id == io and not msg.letto: msg.letto = True
        
    annunci_ids = set([m.annuncio_id for m in conversazione if m.annuncio_id])
    in_trattativa = Annuncio.query.filter(Annuncio.id.in_(annunci_ids), Annuncio.utente_id == io, Annuncio.acquirente_id == None).all()
    ha_ricevuto_da_me = Annuncio.query.filter_by(utente_id=interlocutore_id, acquirente_id=io).first()

    # Vercel ha bisogno di leggere questo comando direttamente
    with app.app_context():
        db.create_all()

    db.session.commit()
    return render_template('chat.html', messaggi=conversazione, interlocutore=interlocutore, in_trattativa=in_trattativa, ha_ricevuto=ha_ricevuto_da_me)

# Vercel ha bisogno di leggere questo comando direttamente
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)