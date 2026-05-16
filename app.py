
from flask import Flask, request, redirect, session, render_template_string, jsonify, send_file
import sqlite3
from datetime import datetime, date
import hashlib
import re
from functools import wraps
import os
import uuid
from werkzeug.utils import secure_filename
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
import base64

app = Flask(__name__)
app.secret_key = "orion_university_super_secret_key_2024"
import os

# Détection automatique de l'environnement
if os.environ.get('RENDER'):
    # Sur Render, utiliser le chemin du disque persistant
    BASE_DIR = '/opt/render/project/src'
else:
    # En local
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, 'orion_university.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
EDT_FOLDER = os.path.join(BASE_DIR, 'edt_photos')
DOCUMENTS_FOLDER = os.path.join(BASE_DIR, 'documents')

# Créer les dossiers
for folder in [UPLOAD_FOLDER, STATIC_FOLDER, EDT_FOLDER, DOCUMENTS_FOLDER]:
    os.makedirs(folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
os.makedirs(EDT_FOLDER, exist_ok=True)
os.makedirs(DOCUMENTS_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}

# Frais par niveau (Licence 1 = 1 500 000, +50 000 par niveau)
FRAIS_PAR_NIVEAU = {
    'Licence 1': 1500000,
    'Licence 2': 1550000,
    'Licence 3': 1600000,
    'Master 1':  1650000,
    'Master 2':  1700000,
}
FRAIS_DEFAULT = 1500000

# Enseignants par filière
ENSEIGNANTS = {
    "informatique": {"password": "info2024",  "nom": "Rakoto",  "prenom": "Mickael", "matiere": "Informatique",  "filiere": "Informatique"},
    "communication": {"password": "comm2024",  "nom": "Rasolofo","prenom": "Marie",   "matiere": "Communication","filiere": "Marketing"},
    "finance":       {"password": "fin2024",   "nom": "Andriamanana","prenom": "Paul","matiere": "Finance",      "filiere": "Finance"},
}

def format_mga(amount):
    return f"{int(amount):,}".replace(",", " ")

def get_frais(niveau):
    return FRAIS_PAR_NIVEAU.get(niveau, FRAIS_DEFAULT)

def allowed_file(filename, allowed=ALLOWED_EXTENSIONS):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_matricule():
    import random
    year = datetime.now().year
    return f"ORN{year}{random.randint(1000,9999)}"

def validate_email(email):
    return re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email) is not None

def login_required(f):
    @wraps(f)
    def deco(*a, **kw):
        if 'user_id' not in session: return redirect('/login')
        return f(*a, **kw)
    return deco

def enseignant_required(f):
    @wraps(f)
    def deco(*a, **kw):
        if not session.get('enseignant_logged_in'): return redirect('/enseignant/login')
        return f(*a, **kw)
    return deco

def admin_required(f):
    @wraps(f)
    def deco(*a, **kw):
        if not session.get('admin_logged_in'): return redirect('/admin/login')
        return f(*a, **kw)
    return deco

def get_user_dict(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def check_payment_notifications():
    """Envoyer rappels de paiement avant le 5 du mois"""
    today = date.today()
    if today.day > 5: return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, prenom, nom, niveau FROM users")
    users = cursor.fetchall()
    mois_actuel = today.strftime("%m/%Y")
    for uid, prenom, nom, niveau in users:
        frais = get_frais(niveau)
        cursor.execute("SELECT COALESCE(SUM(montant),0) FROM paiements WHERE user_id=? AND statut='paye'", (uid,))
        total_paye = cursor.fetchone()[0]
        solde = frais - total_paye
        if solde > 0:
            titre = "⚠️ Rappel paiement mensuel"
            cursor.execute("SELECT id FROM notifications WHERE destinataire_id=? AND titre=? AND date_envoi LIKE ?",
                           (uid, titre, f"%{mois_actuel}%"))
            if not cursor.fetchone():
                msg = (f"Bonjour {prenom}, votre solde restant est de {format_mga(solde)} Ar. "
                       f"Date limite de paiement: le 5 du mois.")
                cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                               (titre, msg, 'warning', uid, datetime.now().strftime("%d/%m/%Y %H:%M"), 0))
    # Notif admin
    cursor.execute("SELECT COUNT(*) FROM users u LEFT JOIN paiements p ON u.id=p.user_id AND p.statut='paye' GROUP BY u.id HAVING COALESCE(SUM(p.montant),0)=0")
    non_payants = cursor.rowcount
    if non_payants > 0:
        cursor.execute("SELECT id FROM notifications WHERE destinataire_id IS NULL AND titre=? AND date_envoi LIKE ?",
                       ("📊 Rapport paiements mensuel", f"%{mois_actuel}%"))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                           ("📊 Rapport paiements mensuel",
                            f"Rappel: {today.strftime('%d/%m/%Y')} - Plusieurs étudiants ont un solde en retard.",
                            'warning', None, datetime.now().strftime("%d/%m/%Y %H:%M"), 0))
    conn.commit()
    conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        matricule TEXT UNIQUE, nom TEXT NOT NULL, prenom TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL, telephone TEXT, filiere TEXT, niveau TEXT,
        password TEXT NOT NULL, date_inscription DATETIME, photo_profil TEXT,
        statut_inscription TEXT DEFAULT 'en_attente'
    )""")
    cursor.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cursor.fetchall()]
    for col, typ in [('photo_profil','TEXT'),('statut_inscription',"TEXT DEFAULT 'en_attente'")]:
        if col not in cols:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")

    cursor.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, titre TEXT NOT NULL, message TEXT NOT NULL,
        type TEXT DEFAULT 'info', destinataire_id INTEGER, date_envoi DATETIME, lu INTEGER DEFAULT 0
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS assiduite (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date DATE,
        statut TEXT CHECK(statut IN ('present','absent','retard')), heure_arrivee TIME
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS paiements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, montant DECIMAL(10,2),
        date DATE, mode TEXT, statut TEXT CHECK(statut IN ('paye','attente','en_attente_validation')),
        reference TEXT, preuve_paiement TEXT, commentaire_etudiant TEXT
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS bibliotheque (
        id INTEGER PRIMARY KEY AUTOINCREMENT, titre TEXT NOT NULL, auteur TEXT,
        type_document TEXT, filiere TEXT, niveau TEXT, fichier TEXT,
        date_ajout DATETIME, description TEXT, enseignant_username TEXT
    )""")
    cursor.execute("PRAGMA table_info(bibliotheque)")
    bcols = [c[1] for c in cursor.fetchall()]
    if 'enseignant_username' not in bcols:
        cursor.execute("ALTER TABLE bibliotheque ADD COLUMN enseignant_username TEXT")

    cursor.execute("""CREATE TABLE IF NOT EXISTS edt_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, filiere TEXT, niveau TEXT, fichier TEXT,
        annee_academique TEXT, semestre INTEGER, date_upload DATETIME
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS cours (
        id INTEGER PRIMARY KEY AUTOINCREMENT, matiere TEXT, enseignant TEXT, salle TEXT,
        jour TEXT, heure_debut TIME, heure_fin TIME, semestre INTEGER, filiere TEXT,
        niveau TEXT DEFAULT 'Licence 1'
    )""")
    cursor.execute("PRAGMA table_info(cours)")
    cours_cols = [c[1] for c in cursor.fetchall()]
    if 'niveau' not in cours_cols:
        cursor.execute("ALTER TABLE cours ADD COLUMN niveau TEXT DEFAULT 'Licence 1'")

    cursor.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, matiere TEXT,
        note DECIMAL(4,2), coefficient DECIMAL(3,1), semestre INTEGER,
        annee_academique TEXT, type_note TEXT DEFAULT 'Examen'
    )""")
    cursor.execute("PRAGMA table_info(notes)")
    ncols = [c[1] for c in cursor.fetchall()]
    if 'type_note' not in ncols:
        cursor.execute("ALTER TABLE notes ADD COLUMN type_note TEXT DEFAULT 'Examen'")

    cursor.execute("SELECT COUNT(*) FROM cours")
    if cursor.fetchone()[0] == 0:
        cours_data = [
            ('Algorithmique Avancée','Dr. MAHERA','L1-V2','LUNDI','08:00','10:00',1,'Informatique','Licence 1'),
            ('Base de Données','M. DIARY','L1-V2','LUNDI','10:15','12:15',1,'Informatique','Licence 1'),
            ('Développement Web','M. DIARY','L1-V2','MARDI','08:00','10:00',1,'Informatique','Licence 1'),
            ('Réseaux','Dr. ANTOINNE','L1-V2','MARDI','10:15','12:15',1,'Informatique','Licence 1'),
            ('Programmation OO','Dr. MAHERA','L1-V2','MERCREDI','08:00','10:00',1,'Informatique','Licence 1'),
            ('Mathématiques','Dr. HARTAMNE','L1-V2','JEUDI','08:00','10:00',1,'Informatique','Licence 1'),
            ('Anglais Technique','Mme HENINTSOA','L1-V2','JEUDI','10:15','12:15',1,'Informatique','Licence 1'),
            ('Projet Tutoré','M. DIARY','L1-V2','VENDREDI','08:00','12:00',1,'Informatique','Licence 1'),
            ('Marketing Digital','Mme RASOA','Comm-V1','LUNDI','08:00','10:00',1,'Marketing','Licence 1'),
            ('Communication Digitale','M. HERY','Comm-V1','MARDI','08:00','10:00',1,'Marketing','Licence 1'),
            ('Finance d\'Entreprise','Dr. PAUL','Fin-V1','LUNDI','08:00','10:00',1,'Finance','Licence 1'),
            ('Comptabilité','M. JEAN','Fin-V1','MARDI','10:15','12:15',1,'Finance','Licence 1'),
        ]
        for d in cours_data:
            cursor.execute("INSERT INTO cours (matiere,enseignant,salle,jour,heure_debut,heure_fin,semestre,filiere,niveau) VALUES (?,?,?,?,?,?,?,?,?)", d)

    conn.commit()
    conn.close()

def generate_stats_graph():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT u.filiere, COUNT(DISTINCT u.id), AVG(n.note), COALESCE(SUM(p.montant),0)
        FROM users u LEFT JOIN notes n ON u.id=n.user_id
        LEFT JOIN paiements p ON u.id=p.user_id AND p.statut='paye'
        GROUP BY u.filiere""")
    stats_par_filiere = cursor.fetchall()
    cursor.execute("""SELECT strftime('%Y-%m',date) as mois, COALESCE(SUM(montant),0)
        FROM paiements WHERE statut='paye' GROUP BY mois ORDER BY mois LIMIT 12""")
    paiements_mensuels = cursor.fetchall()
    conn.close()
    graphs = {}

    if stats_par_filiere:
        fig, axes = plt.subplots(1, 2, figsize=(10, 4), facecolor='#0a0a14')
        filieres = [s[0] or 'N/A' for s in stats_par_filiere]
        nb = [s[1] for s in stats_par_filiere]
        moyen = [round(s[2],1) if s[2] else 0 for s in stats_par_filiere]

        bars = axes[0].bar(filieres, nb, color=['#ffd700','#ff9800','#4caf50'][:len(filieres)], alpha=0.85, edgecolor='#ffffff22')
        axes[0].set_title("Étudiants par filière", color='#ffd700', fontsize=10, fontweight='bold')
        axes[0].set_facecolor('#111828')
        axes[0].tick_params(colors='#ccc', labelsize=8)
        axes[0].spines['bottom'].set_color('#333')
        axes[0].spines['left'].set_color('#333')
        axes[0].spines['top'].set_visible(False)
        axes[0].spines['right'].set_visible(False)
        for bar, val in zip(bars, nb):
            axes[0].text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.1, str(val), ha='center', va='bottom', color='#fff', fontsize=8)

        axes[1].bar(filieres, moyen, color=['#ffd700','#ff9800','#4caf50'][:len(filieres)], alpha=0.85)
        axes[1].set_title("Moyenne par filière", color='#ffd700', fontsize=10, fontweight='bold')
        axes[1].set_ylim(0, 20)
        axes[1].set_facecolor('#111828')
        axes[1].tick_params(colors='#ccc', labelsize=8)
        axes[1].spines['bottom'].set_color('#333')
        axes[1].spines['left'].set_color('#333')
        axes[1].spines['top'].set_visible(False)
        axes[1].spines['right'].set_visible(False)

        plt.tight_layout(pad=1.5)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor='#0a0a14')
        buf.seek(0)
        graphs['filieres'] = base64.b64encode(buf.getvalue()).decode()
        plt.close()

    if paiements_mensuels:
        fig, ax = plt.subplots(figsize=(9, 3.5), facecolor='#0a0a14')
        mois = [p[0] for p in paiements_mensuels]
        montants = [p[1]/1000 for p in paiements_mensuels]
        ax.fill_between(range(len(mois)), montants, alpha=0.3, color='#ffd700')
        ax.plot(range(len(mois)), montants, marker='o', color='#ffd700', linewidth=2.5, markersize=6)
        ax.set_title("Évolution des paiements mensuels (en milliers Ar)", color='#ffd700', fontsize=10, fontweight='bold')
        ax.set_xticks(range(len(mois)))
        ax.set_xticklabels(mois, rotation=45, fontsize=7, color='#ccc')
        ax.set_facecolor('#111828')
        ax.tick_params(colors='#ccc')
        ax.spines['bottom'].set_color('#333')
        ax.spines['left'].set_color('#333')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.yaxis.tick_right()
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor='#0a0a14')
        buf.seek(0)
        graphs['paiements'] = base64.b64encode(buf.getvalue()).decode()
        plt.close()

    return graphs

# ─────────────────────────────────────────────
# ROUTES STATIQUES
# ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template_string(LANDING_TEMPLATE)

@app.route('/static/<path:filename>')
def serve_static(filename):
    from flask import send_from_directory
    return send_from_directory(STATIC_FOLDER, filename)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/edt_photo/<path:filename>')
def edt_photo_file(filename):
    from flask import send_from_directory
    return send_from_directory(EDT_FOLDER, filename)

@app.route('/documents/<path:filename>')
def document_file(filename):
    from flask import send_from_directory
    return send_from_directory(DOCUMENTS_FOLDER, filename)

# ─────────────────────────────────────────────
# AUTH ÉTUDIANTS
# ─────────────────────────────────────────────
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        nom = request.form['nom'].strip()
        prenom = request.form['prenom'].strip()
        email = request.form['email'].strip().lower()
        telephone = request.form['telephone'].strip()
        filiere = request.form['filiere']
        niveau = request.form['niveau']
        password = request.form['password']
        confirm = request.form['confirm_password']
        if not all([nom,prenom,email,telephone,filiere,niveau,password]):
            return render_template_string(REGISTER_TEMPLATE, error="Tous les champs sont obligatoires.")
        if not validate_email(email):
            return render_template_string(REGISTER_TEMPLATE, error="Email invalide.")
        if password != confirm:
            return render_template_string(REGISTER_TEMPLATE, error="Mots de passe différents.")
        if len(password) < 6:
            return render_template_string(REGISTER_TEMPLATE, error="Mot de passe trop court (min 6).")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM users WHERE email=?", (email,))
            if cursor.fetchone():
                conn.close()
                return render_template_string(REGISTER_TEMPLATE, error="Email déjà utilisé.")
            for _ in range(10):
                mat = generate_matricule()
                cursor.execute("SELECT id FROM users WHERE matricule=?", (mat,))
                if not cursor.fetchone(): break
            cursor.execute(
                "INSERT INTO users (matricule,nom,prenom,email,telephone,filiere,niveau,password,date_inscription,photo_profil,statut_inscription) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (mat,nom,prenom,email,telephone,filiere,niveau,hash_password(password),datetime.now().strftime("%d/%m/%Y"),None,'en_attente')
            )
            conn.commit()
            conn.close()
            return render_template_string(REGISTER_TEMPLATE,
                success="Inscription soumise ! En attente de validation par l'administration.")
        except sqlite3.IntegrityError:
            conn.close()
            return render_template_string(REGISTER_TEMPLATE, error="Email déjà utilisé.")
    return render_template_string(REGISTER_TEMPLATE)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        identifier = request.form['identifier'].strip()
        password = request.form['password']
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id,matricule,nom,prenom,email,password,statut_inscription FROM users WHERE matricule=? OR email=?",
                       (identifier, identifier))
        user = cursor.fetchone()
        conn.close()
        if user and user[5] == hash_password(password):
            if user[6] == 'en_attente':
                return render_template_string(LOGIN_TEMPLATE, error="Votre compte est en attente de validation par l'administration.")
            if user[6] == 'rejete':
                return render_template_string(LOGIN_TEMPLATE, error="Votre demande d'inscription a été refusée.")
            session['user_id'] = user[0]
            session['user_matricule'] = user[1]
            session['user_name'] = f"{user[3]} {user[2]}"
            return redirect('/dashboard')
        return render_template_string(LOGIN_TEMPLATE, error="Identifiants incorrects.")
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ─────────────────────────────────────────────
# AUTH ENSEIGNANTS
# ─────────────────────────────────────────────
@app.route('/enseignant/login', methods=['GET','POST'])
def enseignant_login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if username in ENSEIGNANTS and ENSEIGNANTS[username]['password'] == password:
            e = ENSEIGNANTS[username]
            session.update({
                'enseignant_logged_in': True,
                'enseignant_username': username,
                'enseignant_nom': e['nom'],
                'enseignant_prenom': e['prenom'],
                'enseignant_matiere': e['matiere'],
                'enseignant_filiere': e['filiere'],
            })
            return redirect('/enseignant/dashboard')
        return render_template_string(ENSEIGNANT_LOGIN_TEMPLATE, error="Identifiants incorrects")
    return render_template_string(ENSEIGNANT_LOGIN_TEMPLATE)

@app.route('/enseignant/logout')
def enseignant_logout():
    for k in ['enseignant_logged_in','enseignant_username','enseignant_nom','enseignant_prenom','enseignant_matiere','enseignant_filiere']:
        session.pop(k, None)
    return redirect('/')

@app.route('/enseignant/dashboard')
@enseignant_required
def enseignant_dashboard():
    filiere = session['enseignant_filiere']
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Notes par filière/niveau classés
    cursor.execute("""
        SELECT n.id, n.user_id, u.nom, u.prenom, u.filiere, u.niveau, n.matiere, n.note, n.coefficient, n.semestre, n.type_note
        FROM notes n JOIN users u ON n.user_id=u.id
        WHERE u.filiere=?
        ORDER BY u.niveau, u.nom
    """, (filiere,))
    notes = cursor.fetchall()
    # Documents de sa filière
    cursor.execute("SELECT * FROM bibliotheque WHERE filiere=? OR filiere='Toutes' ORDER BY date_ajout DESC", (filiere,))
    documents = cursor.fetchall()
    # EDT photos de sa filière
    cursor.execute("SELECT * FROM edt_photos WHERE filiere=? ORDER BY date_upload DESC", (filiere,))
    edt_photos = cursor.fetchall()
    # Cours de sa filière classés par niveau
    cursor.execute("""SELECT id,matiere,enseignant,salle,jour,heure_debut,heure_fin,semestre,filiere,niveau
        FROM cours WHERE filiere=?
        ORDER BY niveau, CASE jour WHEN 'LUNDI' THEN 1 WHEN 'MARDI' THEN 2 WHEN 'MERCREDI' THEN 3 WHEN 'JEUDI' THEN 4 WHEN 'VENDREDI' THEN 5 END, heure_debut""", (filiere,))
    cours_list = cursor.fetchall()
    # Étudiants de sa filière
    cursor.execute("SELECT id,nom,prenom,niveau,matricule FROM users WHERE filiere=? AND statut_inscription='valide' ORDER BY niveau,nom", (filiere,))
    etudiants = cursor.fetchall()
    # Stats moyennes par niveau
    cursor.execute("""
        SELECT u.niveau, COUNT(DISTINCT u.id), AVG(n.note)
        FROM users u LEFT JOIN notes n ON u.id=n.user_id
        WHERE u.filiere=? AND u.statut_inscription='valide'
        GROUP BY u.niveau
    """, (filiere,))
    stats_niveau = cursor.fetchall()
    conn.close()
    # Graph notes par niveau
    graph_niveau = None
    if stats_niveau:
        fig, ax = plt.subplots(figsize=(6, 3), facecolor='#0d0f1e')
        niveaux = [s[0] for s in stats_niveau]
        moyennes = [round(s[2],1) if s[2] else 0 for s in stats_niveau]
        bars = ax.bar(niveaux, moyennes, color='#ffd700', alpha=0.8, edgecolor='#ffffff22')
        ax.set_ylim(0, 20)
        ax.set_facecolor('#141830')
        ax.set_title("Moyenne par niveau", color='#ffd700', fontsize=9)
        ax.tick_params(colors='#ccc', labelsize=7)
        ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
        ax.spines['bottom'].set_color('#333'); ax.spines['left'].set_color('#333')
        for bar, val in zip(bars, moyennes):
            ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.2, f"{val}", ha='center', color='#fff', fontsize=8)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor='#0d0f1e')
        buf.seek(0)
        graph_niveau = base64.b64encode(buf.getvalue()).decode()
        plt.close()
    return render_template_string(ENSEIGNANT_DASHBOARD_TEMPLATE,
        enseignant=session, notes=notes, documents=documents,
        edt_photos=edt_photos, cours_list=cours_list,
        etudiants=etudiants, stats_niveau=stats_niveau, graph_niveau=graph_niveau)

@app.route('/enseignant/note/add', methods=['POST'])
@enseignant_required
def enseignant_add_note():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO notes (user_id,matiere,note,coefficient,semestre,type_note,annee_academique) VALUES (?,?,?,?,?,?,?)",
                   (request.form['user_id'], request.form['matiere'], float(request.form['note']),
                    float(request.form['coefficient']), int(request.form['semestre']),
                    request.form['type_note'], "2024-2025"))
    conn.commit(); conn.close()
    return redirect('/enseignant/dashboard')

@app.route('/enseignant/note/edit/<int:note_id>', methods=['POST'])
@enseignant_required
def enseignant_edit_note(note_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE notes SET note=?,type_note=? WHERE id=?",
                   (float(request.form['note']), request.form['type_note'], note_id))
    conn.commit(); conn.close()
    return redirect('/enseignant/dashboard')

@app.route('/enseignant/note/delete/<int:note_id>')
@enseignant_required
def enseignant_delete_note(note_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit(); conn.close()
    return redirect('/enseignant/dashboard')

@app.route('/enseignant/bibliotheque/add', methods=['POST'])
@enseignant_required
def enseignant_add_bibliotheque():
    filiere = session['enseignant_filiere']
    titre = request.form['titre']
    auteur = session.get('enseignant_prenom','') + ' ' + session.get('enseignant_nom','')
    type_document = request.form['type_document']
    niveau = request.form['niveau']
    description = request.form.get('description','')
    fichier = None
    if 'fichier' in request.files:
        file = request.files['fichier']
        if file and file.filename and allowed_file(file.filename):
            ext = os.path.splitext(secure_filename(file.filename))[1].lower()
            filename = f"doc_{uuid.uuid4().hex[:10]}{ext}"
            file.save(os.path.join(DOCUMENTS_FOLDER, filename))
            fichier = filename
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bibliotheque (titre,auteur,type_document,filiere,niveau,fichier,date_ajout,description,enseignant_username) VALUES (?,?,?,?,?,?,?,?,?)",
                   (titre,auteur,type_document,filiere,niveau,fichier,datetime.now().strftime("%d/%m/%Y %H:%M"),description,session['enseignant_username']))
    conn.commit(); conn.close()
    return redirect('/enseignant/dashboard')

@app.route('/enseignant/bibliotheque/delete/<int:bid>')
@enseignant_required
def enseignant_delete_bibliotheque(bid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT fichier FROM bibliotheque WHERE id=?", (bid,))
    r = cursor.fetchone()
    if r and r[0]:
        try: os.remove(os.path.join(DOCUMENTS_FOLDER, r[0]))
        except: pass
    cursor.execute("DELETE FROM bibliotheque WHERE id=?", (bid,))
    conn.commit(); conn.close()
    return redirect('/enseignant/dashboard')

# ─────────────────────────────────────────────
# DASHBOARD ÉTUDIANT
# ─────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    check_payment_notifications()
    user_id = session['user_id']
    user = get_user_dict(user_id)
    if not user: return redirect('/logout')
    pay_message = session.pop('pay_message', None)
    frais_total = get_frais(user.get('niveau','Licence 1'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id,titre,message,type,date_envoi,lu FROM notifications WHERE destinataire_id IS NULL OR destinataire_id=? ORDER BY date_envoi DESC", (user_id,))
    notifications = [{'id':r[0],'titre':r[1],'message':r[2],'type':r[3],'date_envoi':r[4],'lu':r[5]} for r in cursor.fetchall()]
    cursor.execute("SELECT COUNT(*) FROM notifications WHERE (destinataire_id IS NULL OR destinataire_id=?) AND lu=0", (user_id,))
    notif_count = cursor.fetchone()[0]

    cursor.execute("SELECT statut,COUNT(*) FROM assiduite WHERE user_id=? GROUP BY statut", (user_id,))
    stats = {'presents':0,'absences':0,'retards':0}
    for s,c in cursor.fetchall():
        if s=='present': stats['presents']=c
        elif s=='absent': stats['absences']=c
        elif s=='retard': stats['retards']=c

    cursor.execute("SELECT date,statut,heure_arrivee FROM assiduite WHERE user_id=? ORDER BY date DESC LIMIT 90", (user_id,))
    assiduite = [{'date':r[0],'statut':r[1],'heure_arrivee':r[2]} for r in cursor.fetchall()]

    cursor.execute("SELECT COALESCE(SUM(montant),0) FROM paiements WHERE user_id=? AND statut='paye'", (user_id,))
    total_paye = cursor.fetchone()[0]
    solde = frais_total - total_paye
    pct = round((total_paye/frais_total)*100,1) if frais_total else 0
    cursor.execute("SELECT montant,date,mode,statut,reference FROM paiements WHERE user_id=? ORDER BY date DESC", (user_id,))
    historique_paiements = [{'montant':r[0],'date':r[1],'mode':r[2],'statut':r[3],'reference':r[4]} for r in cursor.fetchall()]

    cursor.execute("SELECT matiere,enseignant,salle,jour,heure_debut,heure_fin FROM cours WHERE filiere=? AND niveau=? ORDER BY CASE jour WHEN 'LUNDI' THEN 1 WHEN 'MARDI' THEN 2 WHEN 'MERCREDI' THEN 3 WHEN 'JEUDI' THEN 4 WHEN 'VENDREDI' THEN 5 END, heure_debut",
                   (user['filiere'], user['niveau']))
    emploi_du_temps = [{'matiere':r[0],'enseignant':r[1],'salle':r[2],'jour':r[3],'heure_debut':r[4],'heure_fin':r[5]} for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM edt_photos WHERE filiere=? AND niveau=? ORDER BY annee_academique DESC, semestre DESC", (user['filiere'],user['niveau']))
    edt_photos = [{'id':r[0],'filiere':r[1],'niveau':r[2],'fichier':r[3],'annee_academique':r[4],'semestre':r[5]} for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM bibliotheque WHERE filiere=? OR filiere='Toutes' ORDER BY date_ajout DESC", (user['filiere'],))
    bibliotheque = [{'id':r[0],'titre':r[1],'auteur':r[2],'type_document':r[3],'filiere':r[4],'niveau':r[5],'fichier':r[6],'date_ajout':r[7],'description':r[8]} for r in cursor.fetchall()]

    cursor.execute("SELECT matiere,note,coefficient,semestre,type_note FROM notes WHERE user_id=?", (user_id,))
    notes_data = cursor.fetchall()
    details = [{'matiere':r[0],'note':r[1],'coefficient':r[2],'semestre':r[3],'type_note':r[4]} for r in notes_data]
    semestre_avg = {}
    for s in [1,2]:
        sn = [n for n in details if n['semestre']==s]
        if sn:
            tot = sum(n['note']*n['coefficient'] for n in sn)
            coeff = sum(n['coefficient'] for n in sn)
            semestre_avg[s] = round(tot/coeff,2) if coeff else 0
        else:
            semestre_avg[s] = 0
    if details:
        tot = sum(n['note']*n['coefficient'] for n in details)
        coeff = sum(n['coefficient'] for n in details)
        moyenne = round(tot/coeff,2) if coeff else 0
    else:
        moyenne = 0
    notes = {'moyenne':moyenne,'details':details,'semestre_avg':semestre_avg}
    conn.close()

    return render_template_string(DASHBOARD_TEMPLATE,
        user=user, stats=stats,
        paiements={'total_paye':total_paye,'solde_restant':solde,'pourcentage':pct,'frais_total':frais_total},
        assiduite=assiduite, historique_paiements=historique_paiements,
        emploi_du_temps=emploi_du_temps, notes=notes,
        notifications=notifications, notif_count=notif_count,
        bibliotheque=bibliotheque, edt_photos=edt_photos,
        pay_message=pay_message, format_mga=format_mga, CHATBOT_TEMPLATE=CHATBOT_TEMPLATE,)

@app.route('/soumettre_paiement', methods=['POST'])
@login_required
def soumettre_paiement():
    user_id = session['user_id']
    user = get_user_dict(user_id)
    frais_total = get_frais(user.get('niveau','Licence 1'))
    try:
        montant = float(request.form.get('montant','0'))
    except ValueError:
        session['pay_message'] = {'type':'error','text':'Montant invalide.'}
        return redirect('/dashboard')
    if montant <= 0:
        session['pay_message'] = {'type':'error','text':'Montant doit être positif.'}
        return redirect('/dashboard')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(SUM(montant),0) FROM paiements WHERE user_id=? AND statut='paye'", (user_id,))
    total_paye = cursor.fetchone()[0]
    solde = frais_total - total_paye
    if montant > solde:
        conn.close()
        session['pay_message'] = {'type':'error','text':f'Montant dépasse le solde ({format_mga(solde)} Ar).'}
        return redirect('/dashboard')
    mode = request.form.get('mode','Autre')
    reference = request.form.get('reference','')
    now = datetime.now()
    try:
        cursor.execute("INSERT INTO paiements (user_id,montant,date,mode,statut,reference) VALUES (?,?,?,?,?,?)",
                       (user_id,montant,now.strftime("%Y-%m-%d"),mode,'attente',reference))
        cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                       ("📤 Paiement soumis",f"Demande de {format_mga(montant)} Ar via {mode} envoyée. En attente de validation.","info",user_id,now.strftime("%d/%m/%Y %H:%M"),0))
        conn.commit()
        session['pay_message'] = {'type':'success','text':f'Demande de {format_mga(montant)} Ar soumise !'}
    except Exception as e:
        conn.rollback()
        session['pay_message'] = {'type':'error','text':f'Erreur: {str(e)}'}
    finally:
        conn.close()
    return redirect('/dashboard')

@app.route('/upload_photo', methods=['POST'])
@login_required
def upload_photo():
    if 'photo' not in request.files: return redirect('/dashboard')
    file = request.files['photo']
    if not file or file.filename == '' or not allowed_file(file.filename): return redirect('/dashboard')
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    filename = f"user_{session['user_id']}_{uuid.uuid4().hex[:10]}{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET photo_profil=? WHERE id=?", (filename, session['user_id']))
    conn.commit(); conn.close()
    return redirect('/dashboard')

@app.route('/mark_notification_read/<int:notif_id>', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET lu=1 WHERE id=?", (notif_id,))
    conn.commit(); conn.close()
    return jsonify({'success': True})

@app.route('/export_bulletin/<int:semestre>')
@login_required
def export_bulletin(semestre):
    user_id = session['user_id']
    user = get_user_dict(user_id)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT matiere,note,coefficient,type_note FROM notes WHERE user_id=? AND semestre=? ORDER BY type_note,matiere", (user_id,semestre))
    notes_data = cursor.fetchall()
    conn.close()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Heading1'], alignment=TA_CENTER, textColor=colors.HexColor('#ffd700'), fontSize=14)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], alignment=TA_CENTER, fontSize=10)
    story = []
    story.append(Paragraph("ORION University", title_style))
    story.append(Paragraph(f"Bulletin de notes — Semestre {semestre}", sub_style))
    story.append(Spacer(1,0.2*inch))
    info = f"<b>Étudiant:</b> {user['prenom']} {user['nom']} | <b>Matricule:</b> {user['matricule']} | <b>Filière:</b> {user['filiere']} | <b>Niveau:</b> {user['niveau']}"
    story.append(Paragraph(info, styles['Normal']))
    story.append(Spacer(1,0.15*inch))
    data = [['Matière','Type','Note /20','Coeff','Points']]
    total_coeff = total_points = 0
    for m,n,c,t in notes_data:
        pts = n*c
        total_coeff += c; total_points += pts
        data.append([m,t or 'Examen',f"{n:.2f}",f"{c}",f"{pts:.2f}"])
    moy = total_points/total_coeff if total_coeff else 0
    data.append(['','','','MOYENNE',f"{moy:.2f}/20"])
    t = Table(data, colWidths=[2.5*inch,0.8*inch,0.8*inch,0.7*inch,0.7*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#ffd700')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.black),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('GRID',(0,0),(-1,-2),0.5,colors.grey),
        ('BACKGROUND',(0,1),(-1,-2),colors.beige),
        ('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#ffd700')),
        ('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'),
    ]))
    story.append(t)
    story.append(Spacer(1,0.2*inch))
    appr = "Excellent" if moy>=16 else "Très bien" if moy>=14 else "Bien" if moy>=12 else "Passable" if moy>=10 else "Insuffisant"
    story.append(Paragraph(f"<b>Appréciation:</b> {appr}", styles['Normal']))
    story.append(Spacer(1,0.1*inch))
    story.append(Paragraph(f"<i>Document édité le {datetime.now().strftime('%d/%m/%Y')}</i>", styles['Italic']))
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"bulletin_{user['matricule']}_S{semestre}.pdf", mimetype='application/pdf')

# ─────────────────────────────────────────────
# ADMIN
# ─────────────────────────────────────────────
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        if request.form['username']=='admin' and request.form['password']=='orion2024':
            session['admin_logged_in'] = True
            return redirect('/admin')
        return render_template_string(ADMIN_LOGIN_TEMPLATE, error="Identifiants incorrects")
    return render_template_string(ADMIN_LOGIN_TEMPLATE)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect('/admin/login')

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE statut_inscription='valide'"); total_e = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE statut_inscription='en_attente'"); pending_reg = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM cours"); total_c = cursor.fetchone()[0]
    cursor.execute("SELECT AVG(note) FROM notes"); avg = cursor.fetchone()[0]
    moy = round(avg,2) if avg else 0
    cursor.execute("SELECT COALESCE(SUM(montant),0) FROM paiements WHERE statut='paye'"); rev = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM paiements WHERE statut='attente'"); pending_pay = cursor.fetchone()[0]

    # Inscriptions en attente
    cursor.execute("SELECT id,matricule,nom,prenom,email,telephone,filiere,niveau,date_inscription FROM users WHERE statut_inscription='en_attente' ORDER BY date_inscription DESC")
    inscriptions_attente = [{'id':r[0],'matricule':r[1],'nom':r[2],'prenom':r[3],'email':r[4],'telephone':r[5],'filiere':r[6],'niveau':r[7],'date_inscription':r[8]} for r in cursor.fetchall()]

    # Stats par filière
    cursor.execute("SELECT u.filiere,COUNT(DISTINCT u.id),AVG(n.note) FROM users u LEFT JOIN notes n ON u.id=n.user_id WHERE u.statut_inscription='valide' GROUP BY u.filiere")
    stats_filiere = cursor.fetchall()

    # Paiements en retard
    cursor.execute("SELECT u.id,u.nom,u.prenom,u.filiere,u.email,u.niveau,COALESCE(SUM(p.montant),0) as tp FROM users u LEFT JOIN paiements p ON u.id=p.user_id AND p.statut='paye' WHERE u.statut_inscription='valide' GROUP BY u.id")
    paiements_retard = [r for r in cursor.fetchall() if r[6] < get_frais(r[5])]

    # Étudiants par filière/niveau
    cursor.execute("SELECT id,matricule,nom,prenom,email,telephone,filiere,niveau FROM users WHERE statut_inscription='valide' ORDER BY filiere,niveau,nom")
    etudiants = [{'id':r[0],'matricule':r[1],'nom':r[2],'prenom':r[3],'email':r[4],'telephone':r[5],'filiere':r[6],'niveau':r[7]} for r in cursor.fetchall()]

    # Cours par filière/niveau
    cursor.execute("SELECT id,matiere,enseignant,salle,jour,heure_debut,heure_fin,semestre,filiere,niveau FROM cours ORDER BY filiere,niveau,CASE jour WHEN 'LUNDI' THEN 1 WHEN 'MARDI' THEN 2 WHEN 'MERCREDI' THEN 3 WHEN 'JEUDI' THEN 4 WHEN 'VENDREDI' THEN 5 END")
    cours_list = [{'id':r[0],'matiere':r[1],'enseignant':r[2],'salle':r[3],'jour':r[4],'heure_debut':r[5],'heure_fin':r[6],'semestre':r[7],'filiere':r[8],'niveau':r[9]} for r in cursor.fetchall()]

    # Assiduité par filière/niveau
    cursor.execute("SELECT a.id,a.date,a.statut,a.heure_arrivee,u.nom,u.prenom,u.filiere,u.niveau FROM assiduite a JOIN users u ON a.user_id=u.id ORDER BY u.filiere,u.niveau,a.date DESC")
    assiduite_list = [{'id':r[0],'date':r[1],'statut':r[2],'heure_arrivee':r[3],'nom':f"{r[5]} {r[4]}",'filiere':r[6],'niveau':r[7]} for r in cursor.fetchall()]

    # Notes par filière/niveau
    cursor.execute("SELECT n.id,n.matiere,n.note,n.coefficient,n.semestre,n.type_note,u.nom,u.prenom,u.filiere,u.niveau,u.id FROM notes n JOIN users u ON n.user_id=u.id ORDER BY u.filiere,u.niveau,u.nom")
    notes_list = [{'id':r[0],'matiere':r[1],'note':r[2],'coefficient':r[3],'semestre':r[4],'type_note':r[5],'nom':f"{r[7]} {r[6]}",'filiere':r[8],'niveau':r[9],'user_id':r[10]} for r in cursor.fetchall()]

    # Paiements par filière/niveau
    cursor.execute("SELECT p.id,p.montant,p.date,p.mode,p.statut,p.reference,p.commentaire_etudiant,u.nom,u.prenom,u.id,u.filiere,u.niveau FROM paiements p JOIN users u ON p.user_id=u.id ORDER BY u.filiere,u.niveau,CASE p.statut WHEN 'attente' THEN 0 ELSE 1 END,p.date DESC")
    paiements_list = [{'id':r[0],'montant':r[1],'date':r[2],'mode':r[3],'statut':r[4],'reference':r[5],'commentaire':r[6],'nom':f"{r[8]} {r[7]}",'user_id':r[9],'filiere':r[10],'niveau':r[11]} for r in cursor.fetchall()]

    # Notifications
    cursor.execute("SELECT n.id,n.titre,n.message,n.type,n.date_envoi,n.destinataire_id,u.nom,u.prenom FROM notifications n LEFT JOIN users u ON n.destinataire_id=u.id ORDER BY n.date_envoi DESC")
    notifications_list = [{'id':r[0],'titre':r[1],'message':r[2],'type':r[3],'date_envoi':r[4],'destinataire_id':r[5],'destinataire_nom':f"{r[7]} {r[6]}" if r[5] else None} for r in cursor.fetchall()]

    # Bibliothèque par filière
    cursor.execute("SELECT * FROM bibliotheque ORDER BY filiere,date_ajout DESC")
    bibliotheque_list = [{'id':r[0],'titre':r[1],'auteur':r[2],'type_document':r[3],'filiere':r[4],'niveau':r[5],'fichier':r[6],'date_ajout':r[7],'description':r[8]} for r in cursor.fetchall()]

    # EDT Photos
    cursor.execute("SELECT * FROM edt_photos ORDER BY filiere,niveau,date_upload DESC")
    edt_photos_list = [{'id':r[0],'filiere':r[1],'niveau':r[2],'fichier':r[3],'annee_academique':r[4],'semestre':r[5],'date_upload':r[6]} for r in cursor.fetchall()]

    conn.close()
    graphs = generate_stats_graph()

    return render_template_string(ADMIN_DASHBOARD_TEMPLATE,
        stats={'total_etudiants':total_e,'total_cours':total_c,'moyenne_generale':moy,
               'revenus_totaux':format_mga(rev),'paiements_en_attente':pending_pay,'inscriptions_en_attente':pending_reg},
        stats_filiere=stats_filiere, paiements_retard=paiements_retard,
        inscriptions_attente=inscriptions_attente,
        etudiants=etudiants, cours_list=cours_list, assiduite_list=assiduite_list,
        notes_list=notes_list, paiements_list=paiements_list,
        notifications_list=notifications_list, bibliotheque_list=bibliotheque_list,
        edt_photos_list=edt_photos_list, graphs=graphs, format_mga=format_mga,
        get_frais=get_frais,CHATBOT_TEMPLATE=CHATBOT_TEMPLATE)

# Admin: Inscriptions
@app.route('/admin/inscription/valider/<int:uid>')
@admin_required
def admin_valider_inscription(uid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT nom,prenom,matricule FROM users WHERE id=?", (uid,))
    u = cursor.fetchone()
    if u:
        cursor.execute("UPDATE users SET statut_inscription='valide' WHERE id=?", (uid,))
        cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                       ("✅ Inscription validée",f"Félicitations {u[1]} {u[0]} ! Votre compte est activé. Matricule: {u[2]}","success",uid,datetime.now().strftime("%d/%m/%Y %H:%M"),0))
        conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/admin/inscription/rejeter/<int:uid>')
@admin_required
def admin_rejeter_inscription(uid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT nom,prenom FROM users WHERE id=?", (uid,))
    u = cursor.fetchone()
    if u:
        cursor.execute("UPDATE users SET statut_inscription='rejete' WHERE id=?", (uid,))
        cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                       ("❌ Inscription refusée","Votre demande d'inscription a été refusée par l'administration.","warning",uid,datetime.now().strftime("%d/%m/%Y %H:%M"),0))
        conn.commit()
    conn.close()
    return redirect('/admin')

# Admin: Students
@app.route('/admin/student/add', methods=['POST'])
@admin_required
def admin_add_student():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for _ in range(10):
        mat = generate_matricule()
        cursor.execute("SELECT id FROM users WHERE matricule=?", (mat,))
        if not cursor.fetchone(): break
    try:
        cursor.execute("INSERT INTO users (matricule,nom,prenom,email,telephone,filiere,niveau,password,date_inscription,photo_profil,statut_inscription) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (mat,request.form['nom'],request.form['prenom'],request.form['email'],
                        request.form.get('telephone',''),request.form['filiere'],request.form['niveau'],
                        hash_password(request.form['password']),datetime.now().strftime("%d/%m/%Y"),None,'valide'))
        uid = cursor.lastrowid
        cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                       ("Bienvenue",f"Compte créé par l'administration. Matricule: {mat}","success",uid,datetime.now().strftime("%d/%m/%Y %H:%M"),0))
        conn.commit()
    except Exception as e:
        print(f"Erreur: {e}")
    finally:
        conn.close()
    return redirect('/admin')

@app.route('/admin/student/edit/<int:uid>', methods=['GET','POST'])
@admin_required
def admin_edit_student(uid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if request.method == 'POST':
        pw = request.form.get('password','')
        if pw:
            cursor.execute("UPDATE users SET nom=?,prenom=?,email=?,telephone=?,filiere=?,niveau=?,password=? WHERE id=?",
                           (request.form['nom'],request.form['prenom'],request.form['email'],request.form.get('telephone',''),request.form['filiere'],request.form['niveau'],hash_password(pw),uid))
        else:
            cursor.execute("UPDATE users SET nom=?,prenom=?,email=?,telephone=?,filiere=?,niveau=? WHERE id=?",
                           (request.form['nom'],request.form['prenom'],request.form['email'],request.form.get('telephone',''),request.form['filiere'],request.form['niveau'],uid))
        conn.commit(); conn.close(); return redirect('/admin')
    cursor.execute("SELECT id,nom,prenom,email,telephone,filiere,niveau FROM users WHERE id=?", (uid,))
    r = cursor.fetchone(); conn.close()
    if not r: return redirect('/admin')
    sd = {'id':r[0],'nom':r[1],'prenom':r[2],'email':r[3],'telephone':r[4],'filiere':r[5],'niveau':r[6]}
    return render_template_string(EDIT_STUDENT_TEMPLATE, student=sd)

@app.route('/admin/student/delete/<int:uid>')
@admin_required
def admin_delete_student(uid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit(); conn.close()
    return redirect('/admin')

# Admin: Cours
@app.route('/admin/course/add', methods=['POST'])
@admin_required
def admin_add_course():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO cours (matiere,enseignant,salle,jour,heure_debut,heure_fin,semestre,filiere,niveau) VALUES (?,?,?,?,?,?,?,?,?)",
                   (request.form['matiere'],request.form['enseignant'],request.form['salle'],request.form['jour'],
                    request.form['heure_debut'],request.form['heure_fin'],int(request.form['semestre']),
                    request.form['filiere'],request.form.get('niveau','Licence 1')))
    conn.commit(); conn.close(); return redirect('/admin')

@app.route('/admin/course/edit/<int:cid>', methods=['GET','POST'])
@admin_required
def admin_edit_course(cid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if request.method == 'POST':
        cursor.execute("UPDATE cours SET matiere=?,enseignant=?,salle=?,jour=?,heure_debut=?,heure_fin=?,semestre=?,filiere=?,niveau=? WHERE id=?",
                       (request.form['matiere'],request.form['enseignant'],request.form['salle'],request.form['jour'],
                        request.form['heure_debut'],request.form['heure_fin'],int(request.form['semestre']),
                        request.form['filiere'],request.form.get('niveau','Licence 1'),cid))
        conn.commit(); conn.close(); return redirect('/admin')
    cursor.execute("SELECT id,matiere,enseignant,salle,jour,heure_debut,heure_fin,semestre,filiere,niveau FROM cours WHERE id=?", (cid,))
    r = cursor.fetchone(); conn.close()
    if not r: return redirect('/admin')
    cd = {'id':r[0],'matiere':r[1],'enseignant':r[2],'salle':r[3],'jour':r[4],'heure_debut':r[5],'heure_fin':r[6],'semestre':r[7],'filiere':r[8],'niveau':r[9] or 'Licence 1'}
    return render_template_string(EDIT_COURSE_TEMPLATE, cours=cd)

@app.route('/admin/course/delete/<int:cid>')
@admin_required
def admin_delete_course(cid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cours WHERE id=?", (cid,))
    conn.commit(); conn.close(); return redirect('/admin')

# Admin: Assiduité
@app.route('/admin/assiduite/add', methods=['POST'])
@admin_required
def admin_add_assiduite():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO assiduite (user_id,date,statut,heure_arrivee) VALUES (?,?,?,?)",
                   (request.form['user_id'],request.form['date'],request.form['statut'],request.form.get('heure_arrivee','')))
    conn.commit(); conn.close(); return redirect('/admin')

@app.route('/admin/assiduite/delete/<int:aid>')
@admin_required
def admin_delete_assiduite(aid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM assiduite WHERE id=?", (aid,))
    conn.commit(); conn.close(); return redirect('/admin')

# Admin: Notes
@app.route('/admin/note/add', methods=['POST'])
@admin_required
def admin_add_note():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO notes (user_id,matiere,note,coefficient,semestre,annee_academique,type_note) VALUES (?,?,?,?,?,?,?)",
                   (request.form['user_id'],request.form['matiere'],float(request.form['note']),
                    float(request.form['coefficient']),int(request.form['semestre']),
                    request.form.get('annee_academique','2024-2025'),request.form.get('type_note','Examen')))
    conn.commit(); conn.close(); return redirect('/admin')

@app.route('/admin/note/edit/<int:nid>', methods=['GET','POST'])
@admin_required
def admin_edit_note(nid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if request.method == 'POST':
        cursor.execute("UPDATE notes SET matiere=?,note=?,coefficient=?,semestre=?,annee_academique=?,type_note=? WHERE id=?",
                       (request.form['matiere'],float(request.form['note']),float(request.form['coefficient']),
                        int(request.form['semestre']),request.form.get('annee_academique','2024-2025'),
                        request.form.get('type_note','Examen'),nid))
        conn.commit(); conn.close(); return redirect('/admin')
    cursor.execute("SELECT id,matiere,note,coefficient,semestre,annee_academique,type_note FROM notes WHERE id=?", (nid,))
    r = cursor.fetchone(); conn.close()
    if not r: return redirect('/admin')
    nd = {'id':r[0],'matiere':r[1],'note':r[2],'coefficient':r[3],'semestre':r[4],'annee_academique':r[5],'type_note':r[6] or 'Examen'}
    return render_template_string(EDIT_NOTE_TEMPLATE, note=nd)

@app.route('/admin/note/delete/<int:nid>')
@admin_required
def admin_delete_note(nid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notes WHERE id=?", (nid,))
    conn.commit(); conn.close(); return redirect('/admin')

# Admin: Paiements
@app.route('/admin/paiement/add', methods=['POST'])
@admin_required
def admin_add_paiement():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO paiements (user_id,montant,date,mode,statut,reference) VALUES (?,?,?,?,?,?)",
                   (request.form['user_id'],float(request.form['montant']),request.form['date'],
                    request.form['mode'],request.form['statut'],request.form.get('reference','')))
    conn.commit(); conn.close(); return redirect('/admin')

@app.route('/admin/paiement/valider/<int:pid>')
@admin_required
def admin_valider_paiement(pid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id,montant,mode FROM paiements WHERE id=?", (pid,))
    p = cursor.fetchone()
    if p:
        cursor.execute("UPDATE paiements SET statut='paye' WHERE id=?", (pid,))
        cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                       ("✅ Paiement validé",f"Votre paiement de {format_mga(p[1])} Ar via {p[2]} a été validé.","success",p[0],datetime.now().strftime("%d/%m/%Y %H:%M"),0))
        conn.commit()
    conn.close(); return redirect('/admin')

@app.route('/admin/paiement/rejeter/<int:pid>')
@admin_required
def admin_rejeter_paiement(pid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id,montant,mode FROM paiements WHERE id=?", (pid,))
    p = cursor.fetchone()
    if p:
        cursor.execute("UPDATE paiements SET statut='attente' WHERE id=?", (pid,))
        cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                       ("❌ Paiement rejeté",f"Votre demande de {format_mga(p[1])} Ar a été rejetée. Contactez l'administration.","warning",p[0],datetime.now().strftime("%d/%m/%Y %H:%M"),0))
        conn.commit()
    conn.close(); return redirect('/admin')

@app.route('/admin/paiement/delete/<int:pid>')
@admin_required
def admin_delete_paiement(pid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM paiements WHERE id=?", (pid,))
    conn.commit(); conn.close(); return redirect('/admin')

# Admin: Notifications
@app.route('/admin/send_notification', methods=['POST'])
@admin_required
def admin_send_notification():
    titre = request.form['titre']
    message = request.form['message']
    dest = request.form['destinataire']
    typ = request.form['type']
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    if dest == 'all':
        cursor.execute("SELECT id FROM users")
        for (uid,) in cursor.fetchall():
            cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)", (titre,message,typ,uid,now,0))
    else:
        cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)", (titre,message,typ,int(dest),now,0))
    conn.commit(); conn.close(); return redirect('/admin')

@app.route('/admin/send_payment_reminder', methods=['POST'])
@admin_required
def admin_send_payment_reminder():
    user_id = request.form['user_id']
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT nom,prenom FROM users WHERE id=?", (user_id,))
    u = cursor.fetchone()
    if u:
        cursor.execute("INSERT INTO notifications (titre,message,type,destinataire_id,date_envoi,lu) VALUES (?,?,?,?,?,?)",
                       ("⚠️ Rappel paiement",f"Cher/Chère {u[1]} {u[0]}, votre paiement est en retard. Merci de régulariser.","warning",user_id,datetime.now().strftime("%d/%m/%Y %H:%M"),0))
        conn.commit()
    conn.close(); return redirect('/admin')

@app.route('/admin/notification/delete/<int:nid>')
@admin_required
def admin_delete_notification(nid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notifications WHERE id=?", (nid,))
    conn.commit(); conn.close(); return redirect('/admin')

# Admin: Bibliothèque
@app.route('/admin/bibliotheque/add', methods=['POST'])
@admin_required
def admin_add_bibliotheque():
    fichier = None
    if 'fichier' in request.files:
        file = request.files['fichier']
        if file and file.filename and allowed_file(file.filename):
            ext = os.path.splitext(secure_filename(file.filename))[1].lower()
            filename = f"doc_{uuid.uuid4().hex[:10]}{ext}"
            file.save(os.path.join(DOCUMENTS_FOLDER, filename))
            fichier = filename
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bibliotheque (titre,auteur,type_document,filiere,niveau,fichier,date_ajout,description) VALUES (?,?,?,?,?,?,?,?)",
                   (request.form['titre'],request.form.get('auteur',''),request.form['type_document'],
                    request.form['filiere'],request.form['niveau'],fichier,
                    datetime.now().strftime("%d/%m/%Y %H:%M"),request.form.get('description','')))
    conn.commit(); conn.close(); return redirect('/admin')

@app.route('/admin/bibliotheque/delete/<int:bid>')
@admin_required
def admin_delete_bibliotheque(bid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT fichier FROM bibliotheque WHERE id=?", (bid,))
    r = cursor.fetchone()
    if r and r[0]:
        try: os.remove(os.path.join(DOCUMENTS_FOLDER, r[0]))
        except: pass
    cursor.execute("DELETE FROM bibliotheque WHERE id=?", (bid,))
    conn.commit(); conn.close(); return redirect('/admin')

# Admin: EDT Photos
@app.route('/admin/edt_photo/add', methods=['POST'])
@admin_required
def admin_add_edt_photo():
    if 'photo' not in request.files: return redirect('/admin')
    file = request.files['photo']
    if file and file.filename and allowed_file(file.filename):
        ext = os.path.splitext(secure_filename(file.filename))[1].lower()
        filiere = request.form['filiere']
        niveau = request.form['niveau']
        filename = f"edt_{filiere}_{niveau}_{uuid.uuid4().hex[:8]}{ext}"
        file.save(os.path.join(EDT_FOLDER, filename))
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO edt_photos (filiere,niveau,fichier,annee_academique,semestre,date_upload) VALUES (?,?,?,?,?,?)",
                       (filiere,niveau,filename,request.form['annee_academique'],int(request.form['semestre']),datetime.now().strftime("%d/%m/%Y %H:%M")))
        conn.commit(); conn.close()
    return redirect('/admin')

@app.route('/admin/edt_photo/delete/<int:eid>')
@admin_required
def admin_delete_edt_photo(eid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT fichier FROM edt_photos WHERE id=?", (eid,))
    r = cursor.fetchone()
    if r and r[0]:
        try: os.remove(os.path.join(EDT_FOLDER, r[0]))
        except: pass
    cursor.execute("DELETE FROM edt_photos WHERE id=?", (eid,))
    conn.commit(); conn.close(); return redirect('/admin')



# ─────────────────────────────────────────────────────────────────────
# TEMPLATES HTML
# ─────────────────────────────────────────────────────────────────────

LANDING_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ORION University</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root{--gold:#ffd700;--gold2:#ffb300;--dark:#060810;--card:rgba(14,17,35,.92)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sora',sans-serif;background:var(--dark);color:#fff;min-height:100vh}
nav{position:fixed;top:0;width:100%;background:rgba(6,8,16,.9);backdrop-filter:blur(12px);border-bottom:1px solid rgba(255,215,0,.15);padding:.7rem 1rem;z-index:100;display:flex;justify-content:space-between;align-items:center}
.logo-nav{display:flex;align-items:center;gap:10px}
.logo-nav img{width:36px;height:36px;border-radius:50%;border:1px solid var(--gold)}
.logo-nav span{font-size:1rem;font-weight:700;color:var(--gold)}
.menu-toggle{background:none;border:none;color:var(--gold);font-size:1.3rem;cursor:pointer;display:block}
.nav-links{display:none;flex-direction:column;position:fixed;top:58px;left:0;width:100%;background:rgba(6,8,16,.97);padding:1rem;gap:.4rem;border-bottom:1px solid rgba(255,215,0,.2)}
.nav-links.open{display:flex}
.nav-link{color:#ccc;text-decoration:none;padding:.65rem 1rem;border-radius:10px;font-size:.88rem;transition:all .2s}
.nav-link:hover,.nav-link.cta{background:rgba(255,215,0,.12);color:var(--gold)}
.hero{min-height:100vh;background:linear-gradient(to bottom,rgba(6,8,16,.7),rgba(6,8,16,.85)),url('https://images.unsplash.com/photo-1562774053-701939374585?w=1200') center/cover;display:flex;align-items:center;justify-content:center;text-align:center;padding:80px 1rem 2rem}
.hero-inner{max-width:680px}
.hero-logo{width:90px;height:90px;margin:0 auto 1.2rem;border-radius:50%;border:3px solid var(--gold);overflow:hidden;display:flex;align-items:center;justify-content:center;background:rgba(255,215,0,.1)}
.hero-logo img{width:72px;height:72px;object-fit:contain;border-radius:50%}
h1.hero-title{font-family:'DM Serif Display',serif;font-size:clamp(2rem,6vw,3.5rem);line-height:1.1;margin-bottom:1rem}
h1.hero-title span{color:var(--gold)}
.hero-sub{font-size:.95rem;color:#b0bec5;margin-bottom:2rem;line-height:1.6}
.hero-btns{display:flex;flex-wrap:wrap;gap:.7rem;justify-content:center}
.btn{padding:.75rem 1.5rem;border-radius:50px;border:none;font-size:.88rem;font-weight:600;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:8px;transition:all .2s}
.btn-gold{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#060810}
.btn-outline{border:1px solid var(--gold);color:var(--gold);background:transparent}
.btn:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(255,215,0,.25)}
.features{padding:4rem 1rem;background:linear-gradient(180deg,transparent,rgba(13,17,40,.6))}
.sec-title{text-align:center;font-family:'DM Serif Display',serif;font-size:clamp(1.5rem,4vw,2.2rem);margin-bottom:2.5rem}
.sec-title span{color:var(--gold)}
.feat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;max-width:1100px;margin:0 auto}
.feat-card{background:var(--card);border:1px solid rgba(255,215,0,.1);border-radius:18px;padding:1.5rem;text-align:center;transition:all .25s}
.feat-card:hover{border-color:rgba(255,215,0,.4);transform:translateY(-4px)}
.feat-card i{font-size:2rem;color:var(--gold);margin-bottom:.8rem}
.feat-card h3{font-size:.95rem;margin-bottom:.4rem}
.feat-card p{font-size:.78rem;color:#aaa;line-height:1.5}
footer{text-align:center;padding:2rem 1rem;border-top:1px solid rgba(255,215,0,.1);color:#666;font-size:.78rem}
.admin-badge{position:fixed;bottom:16px;right:16px;background:rgba(0,0,0,.7);border:1px solid rgba(255,215,0,.3);border-radius:30px;padding:6px 14px;font-size:10px;color:var(--gold);text-decoration:none;z-index:50}
@media(min-width:768px){.menu-toggle{display:none}.nav-links{display:flex;flex-direction:row;position:static;width:auto;background:none;padding:0;gap:.5rem;border:none}}
</style></head>
<body>
<nav>
  <div class="logo-nav"><img src="/static/logo.png" alt="ORION"><span>ORION University</span></div>
  <button class="menu-toggle" onclick="this.nextElementSibling.classList.toggle('open')"><i class="fas fa-bars"></i></button>
  <div class="nav-links">
    <a href="#" class="nav-link">Accueil</a>
    <a href="#features" class="nav-link">À propos</a>
    <a href="/login" class="nav-link"><i class="fas fa-user"></i> Étudiant</a>
    <a href="/enseignant/login" class="nav-link"><i class="fas fa-chalkboard-teacher"></i> Enseignant</a>
    <a href="/register" class="nav-link cta"><i class="fas fa-user-plus"></i> S'inscrire</a>
  </div>
</nav>
<section class="hero">
  <div class="hero-inner">
    <div class="hero-logo"><img src="/static/logo.png" alt="ORION"></div>
    <h1 class="hero-title">L'excellence à portée de <span>demain</span></h1>
    <p class="hero-sub">Plateforme académique intégrée — gestion des notes, paiements, cours et bien plus</p>
    <div class="hero-btns">
      <a href="/login" class="btn btn-gold"><i class="fas fa-sign-in-alt"></i> Espace Étudiant</a>
      <a href="/enseignant/login" class="btn btn-outline"><i class="fas fa-chalkboard-teacher"></i> Enseignant</a>
      <a href="/register" class="btn btn-outline"><i class="fas fa-user-plus"></i> Inscription</a>
    </div>
  </div>
</section>
<section class="features" id="features">
  <div><h2 class="sec-title">Pourquoi <span>ORION</span> ?</h2>
  <div class="feat-grid">
    <div class="feat-card"><i class="fas fa-globe"></i><h3>International</h3><p>Partenariats avec des universités en Europe, Inde et Maurice</p></div>
    <div class="feat-card"><i class="fas fa-briefcase"></i><h3>Insertion pro.</h3><p>92% de nos diplômés embauchés dans les 6 mois</p></div>
    <div class="feat-card"><i class="fas fa-trophy"></i><h3>Excellence</h3><p>Top 3 des universités de la région</p></div>
    <div class="feat-card"><i class="fas fa-laptop-code"></i><h3>Moderne</h3><p>Plateforme e-learning et équipements de pointe</p></div>
  </div></div>
</section>
<footer><p>&copy; 2025 ORION University | contact@orionuniv.com | +261 38 78 076 89</p></footer>
<a href="/admin/login" class="admin-badge"><i class="fas fa-shield-alt"></i> Admin</a>
<script>
    window.userContext = {
        role: "administrateur",
        name: "Administrateur ORION"
    };
</script>
{{ CHATBOT_TEMPLATE|safe }}
</body></html>'''

LOGIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Connexion — ORION</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810,#0d1228,#0a1520);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:rgba(14,17,35,.95);border-radius:24px;padding:2rem 1.5rem;border:1px solid rgba(255,215,0,.2);width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.hd{text-align:center;margin-bottom:1.8rem}
.logo{width:64px;height:64px;margin:0 auto .8rem;border-radius:50%;border:2px solid #ffd700;overflow:hidden;display:flex;align-items:center;justify-content:center;background:rgba(255,215,0,.1)}
.logo img{width:50px;height:50px;object-fit:contain;border-radius:50%}
h1{font-size:1.4rem;color:#fff}p.sub{color:#ffd700;font-size:.8rem;margin-top:.2rem}
.fg{margin-bottom:1rem}
label{display:block;color:#9aa;font-size:.78rem;margin-bottom:.3rem}
.inp-wrap{position:relative}
.inp-wrap input{width:100%;padding:.8rem 2.8rem .8rem 1rem;border:1px solid rgba(255,255,255,.12);border-radius:12px;background:rgba(255,255,255,.05);color:#fff;font-size:.9rem;font-family:'Sora',sans-serif;transition:border-color .2s}
.inp-wrap input:focus{outline:none;border-color:#ffd700}
.inp-wrap i.ico{position:absolute;right:.9rem;top:50%;transform:translateY(-50%);color:#666;cursor:pointer;font-size:.9rem}
input[type="text"]{width:100%;padding:.8rem 1rem;border:1px solid rgba(255,255,255,.12);border-radius:12px;background:rgba(255,255,255,.05);color:#fff;font-size:.9rem;font-family:'Sora',sans-serif}
input[type="text"]:focus{outline:none;border-color:#ffd700}
.btn-submit{width:100%;padding:.85rem;background:linear-gradient(135deg,#ffd700,#ffb300);color:#060810;border:none;border-radius:12px;font-size:.95rem;font-weight:700;cursor:pointer;margin-top:.5rem;font-family:'Sora',sans-serif;transition:transform .2s}
.btn-submit:hover{transform:translateY(-2px)}
.footer-links{text-align:center;margin-top:1.2rem;padding-top:.8rem;border-top:1px solid rgba(255,255,255,.07);font-size:.8rem;color:#888}
.footer-links a{color:#ffd700;text-decoration:none}
.error{background:rgba(244,67,54,.12);border:1px solid #f44336;color:#ff8a80;padding:.65rem 1rem;border-radius:10px;margin-bottom:1rem;font-size:.8rem;text-align:center}
</style></head>
<body>
<div class="card">
  <div class="hd">
    <div class="logo"><img src="/static/logo.png" alt="ORION"></div>
    <h1>Connexion Étudiant</h1>
    <p class="sub">ORION University — Portail étudiant</p>
  </div>
  {% if error %}<div class="error"><i class="fas fa-exclamation-triangle"></i> {{ error }}</div>{% endif %}
  <form method="post">
    <div class="fg"><label>Matricule ou Email</label><input type="text" name="identifier" placeholder="ORN2024001 ou email" required></div>
    <div class="fg"><label>Mot de passe</label>
      <div class="inp-wrap"><input type="password" name="password" id="pw" placeholder="••••••••" required>
        <i class="fas fa-eye ico" onclick="togglePw('pw',this)"></i></div></div>
    <button type="submit" class="btn-submit"><i class="fas fa-arrow-right-to-bracket"></i> Se connecter</button>
  </form>
  <div class="footer-links">Pas de compte ? <a href="/register">S'inscrire</a> &nbsp;|&nbsp; <a href="/">Accueil</a></div>
</div>
<script>function togglePw(id,ico){const i=document.getElementById(id);i.type=i.type==='password'?'text':'password';ico.className=i.type==='password'?'fas fa-eye ico':'fas fa-eye-slash ico';}</script>
</body></html>'''

REGISTER_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Inscription — ORION</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810,#0d1228,#0a1520);min-height:100vh;padding:1.5rem 1rem}
.card{background:rgba(14,17,35,.95);border-radius:24px;padding:2rem 1.5rem;border:1px solid rgba(255,215,0,.2);width:100%;max-width:520px;margin:0 auto;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.hd{text-align:center;margin-bottom:1.5rem}
.logo{width:60px;height:60px;margin:0 auto .7rem;border-radius:50%;border:2px solid #ffd700;overflow:hidden;display:flex;align-items:center;justify-content:center;background:rgba(255,215,0,.1)}
.logo img{width:48px;height:48px;object-fit:contain;border-radius:50%}
h1{font-size:1.4rem;color:#fff}p.sub{color:#ffd700;font-size:.8rem;margin-top:.2rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:.8rem}
.fg{margin-bottom:.75rem}
label{display:block;color:#9aa;font-size:.75rem;margin-bottom:.25rem}
input,select{width:100%;padding:.7rem 1rem;border:1px solid rgba(255,255,255,.12);border-radius:11px;background:rgba(255,255,255,.05);color:#fff;font-size:.88rem;font-family:'Sora',sans-serif}
input:focus,select:focus{outline:none;border-color:#ffd700}
select option{background:#0d1228;color:#fff}
.inp-wrap{position:relative}
.inp-wrap input{padding-right:2.5rem}
.inp-wrap i{position:absolute;right:.8rem;top:50%;transform:translateY(-50%);color:#666;cursor:pointer;font-size:.85rem}
.btn-submit{width:100%;padding:.85rem;background:linear-gradient(135deg,#ffd700,#ffb300);color:#060810;border:none;border-radius:12px;font-size:.95rem;font-weight:700;cursor:pointer;margin-top:.5rem;font-family:'Sora',sans-serif}
.footer-links{text-align:center;margin-top:1rem;padding-top:.8rem;border-top:1px solid rgba(255,255,255,.07);font-size:.8rem;color:#888}
.footer-links a{color:#ffd700;text-decoration:none}
.error{background:rgba(244,67,54,.12);border:1px solid #f44336;color:#ff8a80;padding:.65rem;border-radius:10px;margin-bottom:.8rem;font-size:.8rem;text-align:center}
.success{background:rgba(76,175,80,.12);border:1px solid #4caf50;color:#69f0ae;padding:.65rem;border-radius:10px;margin-bottom:.8rem;font-size:.8rem;text-align:center}
.info-box{background:rgba(255,215,0,.08);border:1px solid rgba(255,215,0,.3);border-radius:10px;padding:.65rem;margin-bottom:.8rem;font-size:.78rem;color:#ffd700}
</style></head>
<body>
<div class="card">
  <div class="hd"><div class="logo"><img src="/static/logo.png" alt="ORION"></div><h1>Inscription</h1><p class="sub">Rejoignez ORION University</p></div>
  <div class="info-box"><i class="fas fa-info-circle"></i> Votre inscription sera validée par l'administration avant l'activation de votre compte.</div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  {% if success %}<div class="success">{{ success }}</div>{% endif %}
  <form method="post">
    <div class="row">
      <div class="fg"><label>Nom</label><input name="nom" required></div>
      <div class="fg"><label>Prénom</label><input name="prenom" required></div>
    </div>
    <div class="fg"><label>Email</label><input type="email" name="email" required></div>
    <div class="fg"><label>Téléphone</label><input type="tel" name="telephone" required></div>
    <div class="row">
      <div class="fg"><label>Filière</label><select name="filiere" required><option value="">Choisir</option><option>Informatique</option><option>Finance</option><option>Marketing</option></select></div>
      <div class="fg"><label>Niveau</label><select name="niveau" required><option value="">Choisir</option><option>Licence 1</option><option>Licence 2</option><option>Licence 3</option><option>Master 1</option><option>Master 2</option></select></div>
    </div>
    <div class="row">
      <div class="fg"><label>Mot de passe</label><div class="inp-wrap"><input type="password" name="password" id="pw1" required minlength="6"><i class="fas fa-eye" onclick="togglePw('pw1',this)"></i></div></div>
      <div class="fg"><label>Confirmer</label><div class="inp-wrap"><input type="password" name="confirm_password" id="pw2" required><i class="fas fa-eye" onclick="togglePw('pw2',this)"></i></div></div>
    </div>
    <button type="submit" class="btn-submit"><i class="fas fa-check"></i> Soumettre</button>
  </form>
  <div class="footer-links">Déjà inscrit ? <a href="/login">Se connecter</a></div>
</div>
<script>function togglePw(id,ico){const i=document.getElementById(id);i.type=i.type==='password'?'text':'password';ico.className=i.type==='password'?'fas fa-eye':'fas fa-eye-slash';}</script>
</body></html>'''

ADMIN_LOGIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Admin — ORION</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810,#0d1228);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:rgba(14,17,35,.97);border-radius:24px;padding:2rem 1.5rem;border:1px solid rgba(255,215,0,.25);width:100%;max-width:380px}
.hd{text-align:center;margin-bottom:1.6rem}
.ico{width:64px;height:64px;margin:0 auto .8rem;background:linear-gradient(135deg,#ffd700,#ffb300);border-radius:16px;display:flex;align-items:center;justify-content:center}
.ico i{font-size:28px;color:#060810}
h1{color:#ffd700;font-size:1.4rem}p.sub{color:#666;font-size:.78rem;margin-top:.2rem}
.fg{margin-bottom:.9rem}
label{display:block;color:#9aa;font-size:.78rem;margin-bottom:.3rem}
.inp-wrap{position:relative}
.inp-wrap input{width:100%;padding:.8rem 2.8rem .8rem 1rem;border:1px solid rgba(255,255,255,.12);border-radius:12px;background:rgba(255,255,255,.05);color:#fff;font-size:.9rem;font-family:'Sora',sans-serif}
.inp-wrap input:focus{outline:none;border-color:#ffd700}
.inp-wrap i{position:absolute;right:.9rem;top:50%;transform:translateY(-50%);color:#666;cursor:pointer;font-size:.9rem}
input[type="text"]{width:100%;padding:.8rem 1rem;border:1px solid rgba(255,255,255,.12);border-radius:12px;background:rgba(255,255,255,.05);color:#fff;font-size:.9rem;font-family:'Sora',sans-serif}
.btn{width:100%;padding:.85rem;background:linear-gradient(135deg,#ffd700,#ffb300);color:#060810;border:none;border-radius:12px;font-size:.95rem;font-weight:700;cursor:pointer;font-family:'Sora',sans-serif}
.error{background:rgba(244,67,54,.12);border:1px solid #f44336;color:#ff8a80;padding:.65rem;border-radius:10px;margin-bottom:.8rem;font-size:.8rem;text-align:center}
</style></head>
<body>
<div class="card">
  <div class="hd"><div class="ico"><i class="fas fa-shield-alt"></i></div><h1>Administration</h1><p class="sub">ORION University — Accès restreint</p></div>
  {% if error %}<div class="error"><i class="fas fa-exclamation-triangle"></i> {{ error }}</div>{% endif %}
  <form method="post">
    <div class="fg"><label>Identifiant</label><input type="text" name="username" placeholder="admin" required></div>
    <div class="fg"><label>Mot de passe</label><div class="inp-wrap"><input type="password" name="password" id="pw" required><i class="fas fa-eye" onclick="const i=document.getElementById('pw');i.type=i.type==='password'?'text':'password';this.className=i.type==='password'?'fas fa-eye':'fas fa-eye-slash'"></i></div></div>
    <button type="submit" class="btn"><i class="fas fa-sign-in-alt"></i> Accéder</button>
  </form>
</div>
</body></html>'''

ENSEIGNANT_LOGIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Enseignant — ORION</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810,#0d1228,#0a1820);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:rgba(14,17,35,.97);border-radius:24px;padding:2rem 1.5rem;border:1px solid rgba(255,215,0,.2);width:100%;max-width:390px}
.hd{text-align:center;margin-bottom:1.6rem}
.ico{width:64px;height:64px;margin:0 auto .8rem;background:linear-gradient(135deg,#1a237e,#283593);border-radius:16px;border:2px solid rgba(255,215,0,.4);display:flex;align-items:center;justify-content:center}
.ico i{font-size:28px;color:#ffd700}
h1{color:#fff;font-size:1.4rem}p.sub{color:#ffd700;font-size:.78rem;margin-top:.2rem}
.fg{margin-bottom:.9rem}label{display:block;color:#9aa;font-size:.78rem;margin-bottom:.3rem}
.inp-wrap{position:relative}
.inp-wrap input{width:100%;padding:.8rem 2.8rem .8rem 1rem;border:1px solid rgba(255,255,255,.12);border-radius:12px;background:rgba(255,255,255,.05);color:#fff;font-size:.9rem;font-family:'Sora',sans-serif}
.inp-wrap input:focus{outline:none;border-color:#ffd700}
.inp-wrap i{position:absolute;right:.9rem;top:50%;transform:translateY(-50%);color:#666;cursor:pointer;font-size:.9rem}
input[type="text"]{width:100%;padding:.8rem 1rem;border:1px solid rgba(255,255,255,.12);border-radius:12px;background:rgba(255,255,255,.05);color:#fff;font-size:.9rem;font-family:'Sora',sans-serif}
.btn{width:100%;padding:.85rem;background:linear-gradient(135deg,#ffd700,#ffb300);color:#060810;border:none;border-radius:12px;font-size:.95rem;font-weight:700;cursor:pointer;font-family:'Sora',sans-serif}
.error{background:rgba(244,67,54,.12);border:1px solid #f44336;color:#ff8a80;padding:.65rem;border-radius:10px;margin-bottom:.8rem;font-size:.8rem;text-align:center}
.hint{background:rgba(255,215,0,.06);border:1px solid rgba(255,215,0,.2);border-radius:10px;padding:.6rem;margin-bottom:.8rem;font-size:.72rem;color:#aaa}
.hint b{color:#ffd700}
</style></head>
<body>
<div class="card">
  <div class="hd"><div class="ico"><i class="fas fa-chalkboard-teacher"></i></div><h1>Espace Enseignant</h1><p class="sub">Un compte par filière</p></div>
  <div class="hint">Comptes disponibles:<br><b>informatique</b> / <b>communication</b> / <b>finance</b></div>
  {% if error %}<div class="error"><i class="fas fa-exclamation-triangle"></i> {{ error }}</div>{% endif %}
  <form method="post">
    <div class="fg"><label>Identifiant filière</label><input type="text" name="username" placeholder="informatique / communication / finance" required></div>
    <div class="fg"><label>Mot de passe</label><div class="inp-wrap"><input type="password" name="password" id="pw" required><i class="fas fa-eye" onclick="const i=document.getElementById('pw');i.type=i.type==='password'?'text':'password';this.className=i.type==='password'?'fas fa-eye':'fas fa-eye-slash'"></i></div></div>
    <button type="submit" class="btn"><i class="fas fa-sign-in-alt"></i> Accéder</button>
  </form>
  <div style="text-align:center;margin-top:1rem;font-size:.75rem"><a href="/" style="color:#ffd700;text-decoration:none">← Accueil</a></div>
</div>
</body></html>'''

EDIT_STUDENT_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Modifier étudiant</title>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810,#0d1228);min-height:100vh;padding:1.5rem 1rem}
.c{background:rgba(14,17,35,.95);border-radius:20px;padding:1.5rem;max-width:480px;margin:0 auto;border:1px solid rgba(255,215,0,.2)}
h1{color:#ffd700;font-size:1.2rem;margin-bottom:1.2rem;text-align:center}
.fg{margin-bottom:.8rem}label{display:block;color:#9aa;font-size:.78rem;margin-bottom:.25rem}
input,select{width:100%;padding:.7rem 1rem;border-radius:11px;border:1px solid rgba(255,215,0,.2);background:rgba(255,255,255,.04);color:#fff;font-size:.88rem;font-family:'Sora',sans-serif}
input:focus,select:focus{outline:none;border-color:#ffd700}select option{background:#0d1228}
.btn{width:100%;padding:.75rem;border:none;border-radius:11px;font-weight:700;cursor:pointer;margin-top:.4rem;font-family:'Sora',sans-serif}
.bs{background:linear-gradient(135deg,#ffd700,#ffb300);color:#060810}.bg{background:rgba(255,255,255,.1);color:#fff}</style></head>
<body><div class="c"><h1>Modifier étudiant</h1><form method="post">
<div class="fg"><label>Nom</label><input name="nom" value="{{ student.nom }}" required></div>
<div class="fg"><label>Prénom</label><input name="prenom" value="{{ student.prenom }}" required></div>
<div class="fg"><label>Email</label><input type="email" name="email" value="{{ student.email }}" required></div>
<div class="fg"><label>Téléphone</label><input name="telephone" value="{{ student.telephone }}"></div>
<div class="fg"><label>Filière</label><select name="filiere">{% for f in ['Informatique','Finance','Marketing'] %}<option {% if student.filiere==f %}selected{% endif %}>{{ f }}</option>{% endfor %}</select></div>
<div class="fg"><label>Niveau</label><select name="niveau">{% for n in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}<option {% if student.niveau==n %}selected{% endif %}>{{ n }}</option>{% endfor %}</select></div>
<div class="fg"><label>Nouveau mot de passe (vide = inchangé)</label><input type="password" name="password"></div>
<button type="submit" class="btn bs">Enregistrer</button>
<button type="button" class="btn bg" onclick="window.location.href='/admin'">Retour</button>
</form></div></body></html>'''

EDIT_COURSE_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Modifier cours</title>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810,#0d1228);min-height:100vh;padding:1.5rem 1rem}
.c{background:rgba(14,17,35,.95);border-radius:20px;padding:1.5rem;max-width:480px;margin:0 auto;border:1px solid rgba(255,215,0,.2)}
h1{color:#ffd700;font-size:1.2rem;margin-bottom:1.2rem;text-align:center}
.fg{margin-bottom:.8rem}label{display:block;color:#9aa;font-size:.78rem;margin-bottom:.25rem}
input,select{width:100%;padding:.7rem 1rem;border-radius:11px;border:1px solid rgba(255,215,0,.2);background:rgba(255,255,255,.04);color:#fff;font-size:.88rem;font-family:'Sora',sans-serif}
input:focus,select:focus{outline:none;border-color:#ffd700}select option{background:#0d1228}
.btn{width:100%;padding:.75rem;border:none;border-radius:11px;font-weight:700;cursor:pointer;margin-top:.4rem;font-family:'Sora',sans-serif}
.bs{background:linear-gradient(135deg,#ffd700,#ffb300);color:#060810}.bg{background:rgba(255,255,255,.1);color:#fff}</style></head>
<body><div class="c"><h1>Modifier cours</h1><form method="post">
<div class="fg"><label>Matière</label><input name="matiere" value="{{ cours.matiere }}" required></div>
<div class="fg"><label>Enseignant</label><input name="enseignant" value="{{ cours.enseignant }}" required></div>
<div class="fg"><label>Salle</label><input name="salle" value="{{ cours.salle }}" required></div>
<div class="fg"><label>Jour</label><select name="jour">{% for j in ['LUNDI','MARDI','MERCREDI','JEUDI','VENDREDI'] %}<option {% if cours.jour==j %}selected{% endif %}>{{ j }}</option>{% endfor %}</select></div>
<div class="fg"><label>Heure début</label><input type="time" name="heure_debut" value="{{ cours.heure_debut }}" required></div>
<div class="fg"><label>Heure fin</label><input type="time" name="heure_fin" value="{{ cours.heure_fin }}" required></div>
<div class="fg"><label>Semestre</label><input type="number" name="semestre" value="{{ cours.semestre }}" required></div>
<div class="fg"><label>Filière</label><select name="filiere">{% for f in ['Informatique','Finance','Marketing'] %}<option {% if cours.filiere==f %}selected{% endif %}>{{ f }}</option>{% endfor %}</select></div>
<div class="fg"><label>Niveau</label><select name="niveau">{% for n in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}<option {% if cours.niveau==n %}selected{% endif %}>{{ n }}</option>{% endfor %}</select></div>
<button type="submit" class="btn bs">Enregistrer</button>
<button type="button" class="btn bg" onclick="window.location.href='/admin'">Retour</button>
</form></div></body></html>'''

EDIT_NOTE_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Modifier note</title>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810,#0d1228);min-height:100vh;padding:1.5rem 1rem}
.c{background:rgba(14,17,35,.95);border-radius:20px;padding:1.5rem;max-width:440px;margin:0 auto;border:1px solid rgba(255,215,0,.2)}
h1{color:#ffd700;font-size:1.2rem;margin-bottom:1.2rem;text-align:center}
.fg{margin-bottom:.8rem}label{display:block;color:#9aa;font-size:.78rem;margin-bottom:.25rem}
input,select{width:100%;padding:.7rem 1rem;border-radius:11px;border:1px solid rgba(255,215,0,.2);background:rgba(255,255,255,.04);color:#fff;font-size:.88rem;font-family:'Sora',sans-serif}
input:focus,select:focus{outline:none;border-color:#ffd700}select option{background:#0d1228}
.btn{width:100%;padding:.75rem;border:none;border-radius:11px;font-weight:700;cursor:pointer;margin-top:.4rem;font-family:'Sora',sans-serif}
.bs{background:linear-gradient(135deg,#ffd700,#ffb300);color:#060810}.bg{background:rgba(255,255,255,.1);color:#fff}</style></head>
<body><div class="c"><h1>Modifier note</h1><form method="post">
<div class="fg"><label>Matière</label><input name="matiere" value="{{ note.matiere }}" required></div>
<div class="fg"><label>Type</label><select name="type_note"><option {% if note.type_note=='Examen' %}selected{% endif %}>Examen</option><option {% if note.type_note=='DS' %}selected{% endif %}>DS</option><option {% if note.type_note=='Bonus' %}selected{% endif %}>Bonus</option></select></div>
<div class="fg"><label>Note (/20)</label><input type="number" step="0.01" name="note" value="{{ note.note }}" min="0" max="20" required></div>
<div class="fg"><label>Coefficient</label><input type="number" step="0.5" name="coefficient" value="{{ note.coefficient }}" required></div>
<div class="fg"><label>Semestre</label><select name="semestre"><option value="1" {% if note.semestre==1 %}selected{% endif %}>Semestre 1</option><option value="2" {% if note.semestre==2 %}selected{% endif %}>Semestre 2</option></select></div>
<div class="fg"><label>Année académique</label><input name="annee_academique" value="{{ note.annee_academique }}"></div>
<button type="submit" class="btn bs">Enregistrer</button>
<button type="button" class="btn bg" onclick="window.location.href='/admin'">Retour</button>
</form></div></body></html>'''

ENSEIGNANT_DASHBOARD_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Enseignant — ORION University</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--gold:#ffd700;--gold2:#ffb300;--dark:#060810;--card:rgba(14,17,35,.95);--accent:#1a237e}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,var(--dark),#0d1228,#0a1520);color:#fff;min-height:100vh}
.topbar{background:rgba(10,12,26,.97);padding:.8rem 1rem;display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid rgba(255,215,0,.2);position:sticky;top:0;z-index:100}
.topbar-left{display:flex;align-items:center;gap:12px}
.topbar-left img{width:38px;height:38px;border-radius:50%;border:2px solid var(--gold)}
.topbar-left .brand{font-size:1rem;font-weight:700;color:var(--gold)}
.user-pill{display:flex;align-items:center;gap:10px;background:rgba(255,215,0,.08);padding:.4rem .9rem;border-radius:30px;border:1px solid rgba(255,215,0,.2)}
.user-pill i{color:var(--gold)}
.user-pill .name{font-size:.82rem;font-weight:600}
.filiere-badge{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#060810;padding:.2rem .7rem;border-radius:20px;font-size:.7rem;font-weight:700}
.logout-btn{background:rgba(244,67,54,.15);color:#ff6b6b;padding:.4rem .9rem;border-radius:8px;text-decoration:none;font-size:.78rem;border:1px solid rgba(244,67,54,.3)}
.main{max-width:1400px;margin:0 auto;padding:1.5rem 1rem}
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.8rem;margin-bottom:1.5rem}
.stat-card{background:var(--card);border-radius:16px;padding:1rem;border:1px solid rgba(255,215,0,.1);text-align:center;position:relative;overflow:hidden}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--gold),var(--gold2))}
.stat-card h3{font-size:.72rem;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.4rem}
.stat-card .val{font-size:1.6rem;font-weight:800;color:var(--gold)}
.graph-card{background:var(--card);border-radius:16px;padding:1.2rem;border:1px solid rgba(255,215,0,.1);margin-bottom:1.5rem}
.graph-card img{width:100%;height:auto;border-radius:8px}
.tabs-nav{display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:1rem}
.tab-btn{background:rgba(255,215,0,.07);border:1px solid rgba(255,215,0,.2);color:#ccc;padding:.55rem 1.1rem;border-radius:10px;cursor:pointer;font-size:.82rem;font-family:'Sora',sans-serif;transition:all .2s}
.tab-btn.active{background:var(--gold);color:#060810;border-color:var(--gold);font-weight:700}
.tab-pane{display:none;background:var(--card);border-radius:20px;padding:1.5rem;border:1px solid rgba(255,215,0,.08)}
.tab-pane.active{display:block}
.section-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;flex-wrap:wrap;gap:.5rem}
.section-hd h2{font-size:1rem;color:var(--gold)}
.btn-add{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#060810;border:none;padding:.5rem 1.1rem;border-radius:10px;cursor:pointer;font-size:.8rem;font-weight:700;font-family:'Sora',sans-serif;display:inline-flex;align-items:center;gap:6px}
.niveau-section{margin-bottom:1.5rem}
.niveau-header{background:linear-gradient(135deg,rgba(26,35,126,.6),rgba(21,28,100,.4));border:1px solid rgba(255,215,0,.2);border-radius:12px;padding:.6rem 1rem;margin-bottom:.5rem;font-size:.85rem;font-weight:600;color:var(--gold);display:flex;align-items:center;gap:.5rem}
.niveau-header i{font-size:.9rem}
.tbl-wrap{overflow-x:auto;border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:.78rem}
th,td{padding:.7rem .6rem;text-align:left;border-bottom:1px solid rgba(255,255,255,.05)}
th{color:var(--gold);font-weight:600;font-size:.73rem;text-transform:uppercase;letter-spacing:.04em;background:rgba(255,215,0,.05)}
tr:hover td{background:rgba(255,255,255,.02)}
.note-input{width:65px;padding:.3rem .5rem;border-radius:6px;border:1px solid rgba(255,215,0,.3);background:rgba(255,215,0,.05);color:#fff;text-align:center;font-family:'Sora',sans-serif;font-size:.8rem}
.type-sel{width:auto;padding:.3rem .5rem;border-radius:6px;border:1px solid rgba(255,215,0,.2);background:rgba(0,0,0,.3);color:#fff;font-size:.72rem;font-family:'Sora',sans-serif}
.btn-save{background:rgba(76,175,80,.2);color:#69f0ae;border:none;padding:.3rem .7rem;border-radius:6px;cursor:pointer;font-size:.72rem;font-family:'Sora',sans-serif}
.btn-del{background:rgba(244,67,54,.15);color:#ff8a80;border:none;padding:.3rem .6rem;border-radius:6px;cursor:pointer;font-size:.72rem;font-family:'Sora',sans-serif}
.note-badge{display:inline-block;padding:.2rem .55rem;border-radius:20px;font-size:.72rem;font-weight:600}
.note-good{background:rgba(76,175,80,.2);color:#69f0ae}
.note-ok{background:rgba(255,152,0,.2);color:#ffcc80}
.note-bad{background:rgba(244,67,54,.2);color:#ff8a80}
.doc-item{display:flex;justify-content:space-between;align-items:center;padding:.8rem;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:12px;margin-bottom:.5rem;gap:.5rem}
.doc-info h4{font-size:.88rem;color:var(--gold);margin-bottom:.2rem}
.doc-info p{font-size:.72rem;color:#888}
.doc-actions{display:flex;gap:.4rem;flex-shrink:0}
.doc-actions a,.doc-actions button{padding:.3rem .7rem;border-radius:7px;font-size:.72rem;text-decoration:none;border:none;cursor:pointer;font-family:'Sora',sans-serif}
.doc-view{background:rgba(33,150,243,.2);color:#64b5f6}
.doc-del{background:rgba(244,67,54,.15);color:#ff8a80}
.edt-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:1rem;margin-top:.5rem}
.edt-card{background:rgba(255,255,255,.02);border:1px solid rgba(255,215,0,.1);border-radius:14px;overflow:hidden;transition:border-color .2s}
.edt-card:hover{border-color:rgba(255,215,0,.4)}
.edt-card img{width:100%;height:160px;object-fit:cover}
.edt-info{padding:.8rem}
.edt-info .filiere{color:var(--gold);font-weight:600;font-size:.82rem}
.edt-info .meta{font-size:.7rem;color:#888;margin-top:.2rem}
.cours-table-wrap{overflow-x:auto;border-radius:12px}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:1000;align-items:center;justify-content:center}
.modal.show{display:flex}
.modal-box{background:rgba(14,17,35,.99);border-radius:20px;padding:1.5rem;max-width:480px;width:92%;max-height:85vh;overflow-y:auto;border:1px solid var(--gold)}
.modal-box h3{color:var(--gold);margin-bottom:1rem;font-size:1rem}
.fg{margin-bottom:.75rem}
.fg label{display:block;color:#9aa;font-size:.75rem;margin-bottom:.25rem}
.fg input,.fg select,.fg textarea{width:100%;padding:.65rem .8rem;border-radius:9px;border:1px solid rgba(255,215,0,.2);background:rgba(255,255,255,.04);color:#fff;font-family:'Sora',sans-serif;font-size:.85rem}
.fg input:focus,.fg select:focus{outline:none;border-color:var(--gold)}
.fg select option{background:#0d1228}
.modal-btns{display:flex;gap:.7rem;margin-top:1rem;justify-content:flex-end}
.btn-prim{background:var(--gold);color:#060810;border:none;padding:.55rem 1.2rem;border-radius:8px;cursor:pointer;font-weight:700;font-family:'Sora',sans-serif;font-size:.85rem}
.btn-sec{background:rgba(255,255,255,.1);color:#fff;border:none;padding:.55rem 1.2rem;border-radius:8px;cursor:pointer;font-family:'Sora',sans-serif;font-size:.85rem}
</style></head>
<body>
<div class="topbar">
  <div class="topbar-left">
    <img src="/static/logo.png" alt="ORION">
    <div>
      <div class="brand">ORION University</div>
      <div style="font-size:.68rem;color:#666">Espace Enseignant</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
    <div class="user-pill">
      <i class="fas fa-chalkboard-teacher"></i>
      <span class="filiere-badge">{{ enseignant.enseignant_filiere }}</span>
    </div>
    <a href="/enseignant/logout" class="logout-btn"><i class="fas fa-sign-out-alt"></i> Déco</a>
  </div>
</div>

<div class="main">
  <div class="stats-row">
    <div class="stat-card"><h3>Étudiants</h3><div class="val">{{ etudiants|length }}</div></div>
    <div class="stat-card"><h3>Notes enregistrées</h3><div class="val">{{ notes|length }}</div></div>
    <div class="stat-card"><h3>Documents</h3><div class="val">{{ documents|length }}</div></div>
    <div class="stat-card"><h3>Cours planifiés</h3><div class="val">{{ cours_list|length }}</div></div>
  </div>

  {% if graph_niveau %}
  <div class="graph-card">
    <div style="font-size:.85rem;color:var(--gold);margin-bottom:.8rem;font-weight:600"><i class="fas fa-chart-bar"></i> Performances par niveau — {{ enseignant.enseignant_filiere }}</div>
    <img src="data:image/png;base64,{{ graph_niveau }}" alt="Graph">
  </div>
  {% endif %}

  <div class="tabs-nav">
    <button class="tab-btn active" data-t="notes"><i class="fas fa-chart-line"></i> Notes</button>
    <button class="tab-btn" data-t="cours"><i class="fas fa-calendar-alt"></i> Emploi du temps</button>
    <button class="tab-btn" data-t="biblio"><i class="fas fa-book"></i> Bibliothèque</button>
    <button class="tab-btn" data-t="edt"><i class="fas fa-image"></i> Photos EDT</button>
  </div>

  <!-- ONGLET NOTES -->
  <div id="tp-notes" class="tab-pane active">
    <div class="section-hd">
      <h2><i class="fas fa-chart-line"></i> Gestion des notes — {{ enseignant.enseignant_filiere }}</h2>
      <button class="btn-add" onclick="document.getElementById('addNoteModal').classList.add('show')"><i class="fas fa-plus"></i> Ajouter</button>
    </div>
    {% set niveaux_vus = [] %}
    {% for n in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}
      {% set notes_niveau = notes|selectattr(5,'equalto',n)|list %}
      {% if notes_niveau %}
      <div class="niveau-section">
        <div class="niveau-header"><i class="fas fa-graduation-cap"></i> {{ n }} ({{ notes_niveau|length }} note(s))</div>
        <div class="tbl-wrap">
          <table>
            <thead><tr><th>Étudiant</th><th>Matière</th><th>Type</th><th>Note</th><th>Coeff</th><th>S.</th><th>Actions</th></tr></thead>
            <tbody>
            {% for note in notes_niveau %}
            <tr>
              <td>{{ note[3] }} {{ note[2] }}</td>
              <td>{{ note[7] }}</td>
              <td><span style="font-size:.72rem;background:rgba(255,215,0,.1);color:#ffd700;padding:.15rem .45rem;border-radius:10px">{{ note[10] }}</span></td>
              <td>
                <span class="note-badge {% if note[8]>=12 %}note-good{% elif note[8]>=10 %}note-ok{% else %}note-bad{% endif %}">{{ note[8] }}/20</span>
              </td>
              <td>{{ note[9] }}</td>
              <td>S{{ note[9] }}</td>
              <td>
                <form action="/enseignant/note/edit/{{ note[0] }}" method="post" style="display:inline-flex;gap:.3rem;align-items:center">
                  <input type="number" step="0.01" name="note" value="{{ note[8] }}" class="note-input" min="0" max="20">
                  <select name="type_note" class="type-sel">
                    <option {% if note[10]=='Examen' %}selected{% endif %}>Examen</option>
                    <option {% if note[10]=='DS' %}selected{% endif %}>DS</option>
                    <option {% if note[10]=='Bonus' %}selected{% endif %}>Bonus</option>
                  </select>
                  <button type="submit" class="btn-save"><i class="fas fa-save"></i></button>
                </form>
                <a href="/enseignant/note/delete/{{ note[0] }}" class="btn-del" onclick="return confirm('Supprimer?')" style="display:inline-block;margin-left:.2rem"><i class="fas fa-trash"></i></a>
              </td>
            </tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      {% endif %}
    {% endfor %}
  </div>

  <!-- ONGLET COURS EDT -->
  <div id="tp-cours" class="tab-pane">
    <div class="section-hd"><h2><i class="fas fa-calendar-alt"></i> Emploi du temps — {{ enseignant.enseignant_filiere }}</h2></div>
    {% for n in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}
      {% set cours_n = cours_list|selectattr(9,'equalto',n)|list %}
      {% if cours_n %}
      <div class="niveau-section">
        <div class="niveau-header"><i class="fas fa-layer-group"></i> {{ n }}</div>
        <div class="cours-table-wrap">
          <table>
            <thead><tr><th>Matière</th><th>Enseignant</th><th>Jour</th><th>Horaire</th><th>Salle</th><th>Semestre</th></tr></thead>
            <tbody>
            {% for c in cours_n %}
            <tr><td>{{ c[1] }}</td><td>{{ c[2] }}</td><td>{{ c[4] }}</td><td>{{ c[5] }}–{{ c[6] }}</td><td>{{ c[3] }}</td><td>S{{ c[7] }}</td></tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      {% endif %}
    {% endfor %}
  </div>

  <!-- ONGLET BIBLIOTHEQUE -->
  <div id="tp-biblio" class="tab-pane">
    <div class="section-hd">
      <h2><i class="fas fa-book"></i> Bibliothèque — {{ enseignant.enseignant_filiere }}</h2>
      <button class="btn-add" onclick="document.getElementById('addDocModal').classList.add('show')"><i class="fas fa-plus"></i> Ajouter document</button>
    </div>
    {% for doc in documents %}
    <div class="doc-item">
      <div class="doc-info">
        <h4><i class="fas fa-{{ 'file-pdf' if doc[6] and doc[6].endswith('.pdf') else 'file-alt' }}" style="margin-right:.4rem"></i>{{ doc[1] }}</h4>
        <p>{{ doc[2] or 'Sans auteur' }} · {{ doc[3] }} · {{ doc[5] or 'Tous niveaux' }} · {{ doc[7] }}</p>
        {% if doc[8] %}<p style="color:#aaa;margin-top:.2rem">{{ doc[8][:80] }}</p>{% endif %}
      </div>
      <div class="doc-actions">
        {% if doc[6] %}<a href="/documents/{{ doc[6] }}" target="_blank" class="doc-view"><i class="fas fa-eye"></i></a>{% endif %}
        <a href="/enseignant/bibliotheque/delete/{{ doc[0] }}" class="doc-del" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a>
      </div>
    </div>
    {% else %}
    <div style="text-align:center;padding:2rem;color:#666"><i class="fas fa-book" style="font-size:2rem;margin-bottom:.8rem;display:block"></i>Aucun document</div>
    {% endfor %}
  </div>

  <!-- ONGLET EDT PHOTOS -->
  <div id="tp-edt" class="tab-pane">
    <div class="section-hd"><h2><i class="fas fa-image"></i> Photos emploi du temps</h2></div>
    <div class="edt-grid">
      {% for p in edt_photos %}
      <div class="edt-card">
        <img src="/edt_photo/{{ p[3] }}" alt="EDT">
        <div class="edt-info">
          <div class="filiere">{{ p[1] }} — {{ p[2] }}</div>
          <div class="meta">{{ p[4] }} · Semestre {{ p[5] }}</div>
          <div style="margin-top:.6rem;display:flex;gap:.4rem">
            <a href="/edt_photo/{{ p[3] }}" target="_blank" class="doc-view" style="padding:.3rem .7rem;border-radius:7px;font-size:.72rem;text-decoration:none;background:rgba(33,150,243,.2);color:#64b5f6"><i class="fas fa-expand"></i> Voir</a>
          </div>
        </div>
      </div>
      {% else %}
      <div style="text-align:center;padding:2rem;color:#666;grid-column:1/-1">Aucune photo EDT</div>
      {% endfor %}
    </div>
  </div>
</div>

<!-- Modal Ajouter Note -->
<div id="addNoteModal" class="modal">
  <div class="modal-box">
    <h3><i class="fas fa-plus"></i> Ajouter une note</h3>
    <form action="/enseignant/note/add" method="post">
      <div class="fg"><label>Étudiant</label><select name="user_id" required>{% for e in etudiants %}<option value="{{ e[0] }}">{{ e[2] }} {{ e[1] }} — {{ e[3] }}</option>{% endfor %}</select></div>
      <div class="fg"><label>Matière</label><input name="matiere" required></div>
      <div class="fg"><label>Type</label><select name="type_note"><option>Examen</option><option>DS</option><option>Bonus</option></select></div>
      <div class="fg"><label>Note (/20)</label><input type="number" step="0.01" name="note" min="0" max="20" required></div>
      <div class="fg"><label>Coefficient</label><input type="number" step="0.5" name="coefficient" value="1" required></div>
      <div class="fg"><label>Semestre</label><select name="semestre"><option value="1">S1</option><option value="2">S2</option></select></div>
      <div class="modal-btns">
        <button type="button" class="btn-sec" onclick="document.getElementById('addNoteModal').classList.remove('show')">Annuler</button>
        <button type="submit" class="btn-prim">Enregistrer</button>
      </div>
    </form>
  </div>
</div>

<!-- Modal Ajouter Document -->
<div id="addDocModal" class="modal">
  <div class="modal-box">
    <h3><i class="fas fa-upload"></i> Ajouter un document</h3>
    <form action="/enseignant/bibliotheque/add" method="post" enctype="multipart/form-data">
      <div class="fg"><label>Titre</label><input name="titre" required></div>
      <div class="fg"><label>Type</label><select name="type_document"><option value="cours">Cours</option><option value="exercice">Exercice</option><option value="corrige">Corrigé</option><option value="examen">Examen</option><option value="livre">Livre</option></select></div>
      <div class="fg"><label>Niveau</label><select name="niveau"><option value="Tous">Tous les niveaux</option><option>Licence 1</option><option>Licence 2</option><option>Licence 3</option><option>Master 1</option><option>Master 2</option></select></div>
      <div class="fg"><label>Fichier (PDF/image)</label><input type="file" name="fichier" accept=".pdf,.jpg,.jpeg,.png"></div>
      <div class="fg"><label>Description</label><textarea name="description" rows="2"></textarea></div>
      <div class="modal-btns">
        <button type="button" class="btn-sec" onclick="document.getElementById('addDocModal').classList.remove('show')">Annuler</button>
        <button type="submit" class="btn-prim">Ajouter</button>
      </div>
    </form>
  </div>
</div>

<script>
document.querySelectorAll('.tab-btn').forEach(b=>{
  b.addEventListener('click',()=>{
    document.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById('tp-'+b.dataset.t).classList.add('active');
  });
});
window.onclick = e=>{
  document.querySelectorAll('.modal').forEach(m=>{if(e.target===m)m.classList.remove('show');});
};
</script>
<script>
    window.userContext = {
        role: "enseignant",
        name: "{{ enseignant.enseignant_prenom }} {{ enseignant.enseignant_nom }}",
        filiere: "{{ enseignant.enseignant_filiere }}"
    };
</script>
{{ CHATBOT_TEMPLATE|safe }}
</body>
</html>'''

DASHBOARD_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,viewport-fit=cover">
<title>Dashboard — ORION University</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{--gold:#ffd700;--gold2:#ffb300;--dark:#060810;--card:rgba(14,17,35,.93);--sw:268px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810 0%,#0d1228 55%,#0a1520 100%);color:#fff;min-height:100vh}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
/* SIDEBAR */
.sidebar{position:fixed;left:-100%;top:0;width:var(--sw);height:100vh;background:rgba(10,12,26,.98);backdrop-filter:blur(16px);border-right:1px solid rgba(255,215,0,.12);transition:left .28s ease;z-index:1000;overflow-y:auto;display:flex;flex-direction:column}
.sidebar.open{left:0}
.sb-logo{padding:1rem;display:flex;align-items:center;gap:10px;border-bottom:1px solid rgba(255,215,0,.1)}
.sb-logo img{width:40px;height:40px;border-radius:50%;border:2px solid var(--gold)}
.sb-logo-text{font-size:.95rem;font-weight:700;color:var(--gold)}
.sb-user{padding:1.2rem;text-align:center;border-bottom:1px solid rgba(255,215,0,.08)}
.sb-avatar{width:80px;height:80px;margin:0 auto .7rem;border-radius:50%;border:2px solid var(--gold);overflow:hidden;cursor:pointer;background:rgba(255,215,0,.05);display:flex;align-items:center;justify-content:center}
.sb-avatar img{width:100%;height:100%;object-fit:cover}
.sb-avatar i{font-size:48px;color:#444}
.sb-name{font-weight:700;font-size:.92rem;color:var(--gold)}
.sb-sub{font-size:.68rem;color:#666;margin-top:.2rem}
.sb-matricule{font-size:.65rem;color:rgba(255,215,0,.5);margin-top:.15rem}
.sb-menu{padding:.5rem;flex:1}
.sb-label{font-size:.62rem;text-transform:uppercase;color:rgba(255,255,255,.25);padding:.4rem .6rem;letter-spacing:.08em}
.sb-item{display:flex;align-items:center;gap:11px;padding:.62rem .9rem;margin:.1rem 0;color:rgba(255,255,255,.65);border-radius:10px;cursor:pointer;transition:background .2s;font-size:.85rem}
.sb-item i{width:20px;font-size:.88rem}
.sb-item.active,.sb-item:hover{background:rgba(255,215,0,.12);color:var(--gold)}
.sb-item.logout{color:#ff6b6b;margin-top:.5rem;border-top:1px solid rgba(255,255,255,.06)}
/* TOPBAR */
.topbar{position:fixed;top:0;left:0;right:0;background:rgba(10,12,26,.97);backdrop-filter:blur(10px);padding:.7rem 1rem;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(255,215,0,.1);z-index:999}
.menu-btn{background:none;border:none;font-size:1.25rem;color:var(--gold);cursor:pointer}
.topbar-right{display:flex;align-items:center;gap:.7rem}
.notif-btn{position:relative;cursor:pointer;font-size:1.1rem;color:var(--gold);padding:.35rem;background:rgba(255,215,0,.08);border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center}
.notif-badge{position:absolute;top:-4px;right:-4px;background:#f44336;color:#fff;border-radius:50%;width:18px;height:18px;font-size:.6rem;font-weight:700;display:flex;align-items:center;justify-content:center;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(244,67,54,.4)}70%{box-shadow:0 0 0 6px transparent}}
/* NOTIFICATIONS PANEL */
.notif-panel{position:fixed;top:60px;right:.8rem;width:320px;max-width:calc(100vw - 1.6rem);max-height:400px;overflow-y:auto;background:rgba(14,17,35,.99);border-radius:16px;border:1px solid rgba(255,215,0,.25);display:none;z-index:1001;box-shadow:0 20px 60px rgba(0,0,0,.6)}
.notif-panel.show{display:block;animation:fadeIn .2s ease}
.np-header{padding:.8rem 1rem;border-bottom:1px solid rgba(255,215,0,.1);font-size:.85rem;font-weight:600;color:var(--gold);display:flex;justify-content:space-between;align-items:center}
.np-item{padding:.75rem 1rem;border-bottom:1px solid rgba(255,255,255,.05);cursor:pointer;transition:background .15s}
.np-item:hover{background:rgba(255,255,255,.03)}
.np-item.unread{border-left:3px solid var(--gold)}
.np-item-title{font-size:.82rem;font-weight:600;margin-bottom:.2rem}
.np-item-msg{font-size:.74rem;color:#9aa;line-height:1.4}
.np-item-date{font-size:.65rem;color:#666;margin-top:.25rem}
.np-empty{padding:1.5rem;text-align:center;color:#666;font-size:.82rem}
/* MAIN */
.main{margin-top:58px;padding:1rem;padding-bottom:76px}
.page{display:none}
.page.active{display:block;animation:fadeIn .3s ease}
/* CARDS */
.card{background:var(--card);border-radius:16px;padding:1rem 1.2rem;margin-bottom:1rem;border:1px solid rgba(255,215,0,.08)}
.card-title{color:var(--gold);font-size:.9rem;font-weight:700;margin-bottom:.9rem;padding-bottom:.5rem;border-bottom:1px solid rgba(255,215,0,.1);display:flex;align-items:center;gap:.5rem}
/* STATS */
.stats-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem}
.stat-box{text-align:center;padding:.8rem .4rem;background:rgba(255,255,255,.03);border-radius:12px;border:1px solid rgba(255,255,255,.05)}
.stat-val{font-size:1.5rem;font-weight:800;display:block}
.sv-green{color:#4caf50}.sv-red{color:#f44336}.sv-orange{color:#ff9800}
.stat-lbl{font-size:.65rem;color:#777;margin-top:.15rem}
/* ROW ITEMS */
.row-item{display:flex;justify-content:space-between;align-items:center;padding:.6rem .8rem;background:rgba(255,255,255,.03);border-radius:10px;margin-bottom:.4rem;font-size:.82rem}
/* BADGES */
.badge{display:inline-block;padding:.18rem .55rem;border-radius:20px;font-size:.68rem;font-weight:500}
.b-present,.b-paye{background:rgba(76,175,80,.2);color:#69f0ae}
.b-absent{background:rgba(244,67,54,.2);color:#ff8a80}
.b-retard,.b-attente{background:rgba(255,152,0,.2);color:#ffcc80}
/* PROGRESS */
.prog-bar{background:rgba(255,255,255,.07);border-radius:10px;height:8px;overflow:hidden;margin:.6rem 0}
.prog-fill{background:linear-gradient(90deg,var(--gold),var(--gold2));height:100%;transition:width .7s ease;border-radius:10px}
/* CHARTS */
.chart-wrap{position:relative;height:200px;margin-top:.6rem}
/* PAYMENT */
.pay-grid{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:.8rem}
.pay-card{padding:.85rem;border-radius:14px;border:2px solid rgba(255,215,0,.1);background:rgba(255,255,255,.03);cursor:pointer;text-align:center;transition:all .2s}
.pay-card:hover,.pay-card.sel{border-color:var(--gold);background:rgba(255,215,0,.08)}
.pay-card i{font-size:1.4rem;display:block;margin-bottom:.4rem;color:var(--gold)}
.pay-card span{font-size:.8rem;color:#ccc}
.pay-card.sel span{color:var(--gold)}
.pay-form{display:none}
.pay-form.show{display:block;animation:fadeIn .2s ease}
.pay-input{width:100%;padding:.7rem .9rem;border:1px solid rgba(255,215,0,.2);border-radius:10px;background:rgba(255,255,255,.04);color:#fff;font-family:'Sora',sans-serif;font-size:.88rem;margin-bottom:.6rem}
.pay-input:focus{outline:none;border-color:var(--gold)}
.pay-btn{width:100%;padding:.8rem;background:linear-gradient(135deg,var(--gold),var(--gold2));color:#060810;border:none;border-radius:10px;font-weight:700;cursor:pointer;font-family:'Sora',sans-serif;font-size:.9rem}
.pay-flash{padding:.7rem 1rem;border-radius:10px;margin-bottom:.8rem;font-size:.82rem}
.pay-flash.success{background:rgba(76,175,80,.12);border:1px solid #4caf50;color:#69f0ae}
.pay-flash.error{background:rgba(244,67,54,.12);border:1px solid #f44336;color:#ff8a80}
/* CALENDRIER ASSIDUITÉ */
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;margin-bottom:.6rem}
.cal-day{aspect-ratio:1;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:.62rem;cursor:pointer;transition:transform .1s;position:relative}
.cal-day:hover{transform:scale(1.1)}
.cal-p{background:rgba(76,175,80,.4);border:1px solid #4caf5055}
.cal-a{background:rgba(244,67,54,.4);border:1px solid #f4433655}
.cal-r{background:rgba(255,152,0,.4);border:1px solid #ff980055}
.cal-e{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06)}
.cal-day.today{box-shadow:0 0 0 2px var(--gold)}
.cal-legend{display:flex;gap:.8rem;flex-wrap:wrap;font-size:.7rem;margin-bottom:.8rem}
.cal-legend span{display:flex;align-items:center;gap:.3rem}
.cal-dot{width:10px;height:10px;border-radius:3px}
.cal-days-header{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;margin-bottom:3px}
.cal-days-header span{text-align:center;font-size:.6rem;color:#666}
.tooltip-cal{position:absolute;bottom:110%;left:50%;transform:translateX(-50%);background:#1a1a2e;border:1px solid var(--gold);border-radius:8px;padding:.3rem .6rem;font-size:.6rem;white-space:nowrap;pointer-events:none;opacity:0;transition:opacity .2s;z-index:10}
.cal-day:hover .tooltip-cal{opacity:1}
/* EMPLOI DU TEMPS TABLEAU */
.edt-table{width:100%;border-collapse:collapse;font-size:.75rem}
.edt-table th{background:rgba(255,215,0,.1);color:var(--gold);padding:.5rem .4rem;text-align:center;font-weight:600;font-size:.7rem;border:1px solid rgba(255,215,0,.1)}
.edt-table td{border:1px solid rgba(255,255,255,.05);padding:.4rem;vertical-align:top}
.edt-cell{background:rgba(255,215,0,.08);border-radius:8px;padding:.4rem .5rem;border-left:3px solid var(--gold)}
.edt-cell .mat{font-weight:600;font-size:.75rem;color:#fff}
.edt-cell .ens{font-size:.65rem;color:#9aa;margin-top:.15rem}
.edt-cell .salle{font-size:.62rem;color:#ffd700;margin-top:.1rem}
.edt-empty td{height:40px}
/* NOTES */
.avg-box{background:linear-gradient(135deg,rgba(20,30,70,.6),rgba(10,20,50,.5));text-align:center;padding:1rem;border-radius:14px;margin-bottom:.8rem;border:1px solid rgba(255,215,0,.15)}
.avg-val{font-size:2rem;font-weight:800;color:var(--gold)}
.sem-tabs{display:flex;gap:.4rem;margin-bottom:.8rem}
.sem-btn{padding:.4rem .9rem;border-radius:20px;border:1px solid rgba(255,215,0,.3);background:transparent;color:#ccc;font-size:.78rem;cursor:pointer;font-family:'Sora',sans-serif;transition:all .2s}
.sem-btn.active{background:var(--gold);color:#060810;border-color:var(--gold);font-weight:700}
/* PROFIL */
.profil-avatar{width:100px;height:100px;margin:0 auto 1rem;border-radius:50%;border:3px solid var(--gold);overflow:hidden;cursor:pointer;display:flex;align-items:center;justify-content:center;background:rgba(255,215,0,.05)}
.profil-avatar img{width:100%;height:100%;object-fit:cover}
.profil-avatar i{font-size:60px;color:#444}
.info-row{padding:.65rem .9rem;background:rgba(255,255,255,.03);border-radius:10px;margin-bottom:.5rem;border-left:3px solid rgba(255,215,0,.4)}
.info-row strong{color:var(--gold);display:block;margin-bottom:.1rem;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}
.info-row span{font-size:.88rem;color:#ddd}
/* BIBLIOTHEQUE */
.doc-card{background:rgba(255,255,255,.03);border-radius:12px;padding:.8rem;margin-bottom:.5rem;display:flex;justify-content:space-between;align-items:center;border:1px solid rgba(255,255,255,.05);gap:.5rem}
.doc-info h4{color:var(--gold);font-size:.85rem;margin-bottom:.15rem}
.doc-info p{font-size:.72rem;color:#888}
.doc-dl{color:var(--gold);padding:.3rem .7rem;border-radius:7px;background:rgba(255,215,0,.1);text-decoration:none;font-size:.75rem}
/* EDT PHOTOS */
.edt-photo-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:.8rem;margin-top:.5rem}
.edt-photo-card{background:rgba(255,255,255,.02);border:1px solid rgba(255,215,0,.1);border-radius:12px;overflow:hidden}
.edt-photo-card img{width:100%;height:140px;object-fit:cover}
.edt-photo-info{padding:.6rem;font-size:.72rem;color:#9aa}
/* BOTTOM NAV */
.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:rgba(10,12,26,.98);display:flex;justify-content:space-around;padding:.5rem .3rem calc(.5rem + env(safe-area-inset-bottom));border-top:1px solid rgba(255,215,0,.12);z-index:999}
.bnav-item{display:flex;flex-direction:column;align-items:center;gap:.12rem;background:none;border:none;color:rgba(255,255,255,.4);font-size:.58rem;cursor:pointer;font-family:'Sora',sans-serif;padding:.2rem .4rem;border-radius:8px;transition:color .2s}
.bnav-item i{font-size:1.15rem}
.bnav-item.active{color:var(--gold)}
/* MODAL */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:2000;align-items:center;justify-content:center}
.modal.show{display:flex}
.modal-box{background:rgba(14,17,35,.99);border-radius:20px;padding:1.5rem;border:1px solid var(--gold);text-align:center;width:85%;max-width:280px}
.modal-box h3{color:var(--gold);margin-bottom:1rem;font-size:1rem}
.btn-gold{background:var(--gold);color:#060810;border:none;padding:.6rem 1.2rem;border-radius:8px;cursor:pointer;font-family:'Sora',sans-serif;font-weight:700;margin:.3rem}
.btn-cancel{background:rgba(255,255,255,.1);color:#fff;border:none;padding:.6rem 1.2rem;border-radius:8px;cursor:pointer;font-family:'Sora',sans-serif;margin:.3rem}
/* export btn */
.export-btns{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.8rem}
.export-btn{padding:.55rem 1rem;background:rgba(255,215,0,.1);border:1px solid rgba(255,215,0,.3);color:var(--gold);border-radius:8px;text-decoration:none;font-size:.8rem;display:inline-flex;align-items:center;gap:.4rem;transition:all .2s}
.export-btn:hover{background:rgba(255,215,0,.2)}
@media(min-width:768px){.topbar,.bottom-nav{display:none}.sidebar{left:0}.main{margin-top:0;margin-left:var(--sw);padding:1.5rem}}
</style></head>
<body>

<!-- Sidebar -->
<div class="sidebar" id="sidebar">
  <div class="sb-logo"><img src="/static/logo.png"><span class="sb-logo-text">ORION University</span></div>
  <div class="sb-user">
    <div class="sb-avatar" onclick="openPhotoModal()">
      {% if user.photo_profil %}<img src="/uploads/{{ user.photo_profil }}">{% else %}<i class="fas fa-user-circle"></i>{% endif %}
    </div>
    <div class="sb-name">{{ user.prenom }} {{ user.nom }}</div>
    <div class="sb-sub">{{ user.filiere }} — {{ user.niveau }}</div>
    <div class="sb-matricule">{{ user.matricule }}</div>
  </div>
  <div class="sb-menu">
    <div class="sb-label">Navigation</div>
    {% for page, icon, label in [('dashboard','chart-pie','Tableau de bord'),('notifications','bell','Notifications'),('assiduite','calendar-check','Assiduité'),('paiement','credit-card','Paiements'),('cours','calendar-alt','Emploi du temps'),('bibliotheque','book','Bibliothèque'),('notes','chart-line','Notes'),('profil','user-circle','Mon profil')] %}
    <div class="sb-item{% if loop.first %} active{% endif %}" data-page="{{ page }}"><i class="fas fa-{{ icon }}"></i> {{ label }}</div>
    {% endfor %}
    <div class="sb-item logout" onclick="window.location.href='/logout'"><i class="fas fa-sign-out-alt"></i> Déconnexion</div>
  </div>
</div>

<!-- Topbar mobile -->
<div class="topbar">
  <button class="menu-btn" onclick="document.getElementById('sidebar').classList.toggle('open')"><i class="fas fa-bars"></i></button>
  <div style="font-size:.9rem">Bonjour <strong style="color:var(--gold)">{{ user.prenom }}</strong></div>
  <div class="topbar-right">
    <div class="notif-btn" onclick="document.getElementById('notifPanel').classList.toggle('show')">
      <i class="fas fa-bell"></i>
      {% if notif_count > 0 %}<span class="notif-badge">{{ notif_count }}</span>{% endif %}
    </div>
  </div>
</div>

<!-- Notifications Panel -->
<div class="notif-panel" id="notifPanel">
  <div class="np-header">
    <span><i class="fas fa-bell"></i> Notifications</span>
    {% if notif_count > 0 %}<span style="background:rgba(244,67,54,.2);color:#ff8a80;padding:.15rem .5rem;border-radius:20px;font-size:.7rem">{{ notif_count }} nouvelles</span>{% endif %}
  </div>
  {% for n in notifications[:10] %}
  <div class="np-item {% if not n.lu %}unread{% endif %}" onclick="markRead({{ n.id }})">
    <div class="np-item-title">
      {% if n.type=='warning' %}<span style="color:#ffcc80">⚠</span> {% elif n.type=='success' %}<span style="color:#69f0ae">✓</span> {% else %}<span style="color:#64b5f6">ℹ</span> {% endif %}
      {{ n.titre }}
    </div>
    <div class="np-item-msg">{{ n.message[:80] }}{% if n.message|length > 80 %}...{% endif %}</div>
    <div class="np-item-date">{{ n.date_envoi }}</div>
  </div>
  {% else %}
  <div class="np-empty"><i class="fas fa-bell-slash" style="font-size:1.5rem;margin-bottom:.5rem;display:block"></i>Aucune notification</div>
  {% endfor %}
</div>

<div class="main">

<!-- PAGE DASHBOARD -->
<div id="dashboard" class="page active">
  <div class="card">
    <div class="card-title"><i class="fas fa-chart-pie"></i> Vue d'ensemble</div>
    <div class="stats-grid">
      <div class="stat-box"><span class="stat-val sv-green">{{ stats.presents }}</span><div class="stat-lbl">Présences</div></div>
      <div class="stat-box"><span class="stat-val sv-red">{{ stats.absences }}</span><div class="stat-lbl">Absences</div></div>
      <div class="stat-box"><span class="stat-val sv-orange">{{ stats.retards }}</span><div class="stat-lbl">Retards</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-chart-line"></i> Notes</div>
    <div class="avg-box"><div class="avg-val">{{ notes.moyenne }}/20</div><div style="font-size:.8rem;color:#9aa;margin-top:.3rem">Moyenne générale</div></div>
    <div class="chart-wrap"><canvas id="chartNotes"></canvas></div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-wallet"></i> Finances</div>
    <div class="row-item"><span>Frais de scolarité</span><strong>{{ format_mga(paiements.frais_total) }} Ar</strong></div>
    <div class="row-item"><span>Payé</span><strong style="color:#4caf50">{{ format_mga(paiements.total_paye) }} Ar</strong></div>
    <div class="row-item"><span>Solde restant</span><strong style="color:#ff9800">{{ format_mga(paiements.solde_restant) }} Ar</strong></div>
    <div class="prog-bar"><div class="prog-fill" style="width:{{ paiements.pourcentage }}%"></div></div>
    <div style="text-align:right;font-size:.72rem;color:#888">{{ paiements.pourcentage }}% payé</div>
    <div class="chart-wrap"><canvas id="chartPay"></canvas></div>
  </div>
</div>

<!-- PAGE NOTIFICATIONS -->
<div id="notifications" class="page">
  <div class="card">
    <div class="card-title"><i class="fas fa-bell"></i> Toutes les notifications</div>
    {% for n in notifications %}
    <div class="row-item" style="flex-direction:column;align-items:flex-start;gap:.3rem;border-left:3px solid {% if n.type=='warning' %}#ff9800{% elif n.type=='success' %}#4caf50{% else %}#2196f3{% endif %}">
      <strong style="font-size:.85rem">{{ n.titre }}</strong>
      <span style="font-size:.8rem;color:#bbb">{{ n.message }}</span>
      <span style="font-size:.65rem;color:#666">{{ n.date_envoi }}</span>
    </div>
    {% else %}
    <div style="text-align:center;padding:2rem;color:#666">Aucune notification</div>
    {% endfor %}
  </div>
</div>

<!-- PAGE ASSIDUITE -->
<div id="assiduite" class="page">
  <div class="card">
    <div class="card-title"><i class="fas fa-calendar-check"></i> Assiduité — Calendrier</div>
    <div class="cal-legend">
      <span><div class="cal-dot" style="background:rgba(76,175,80,.5)"></div>Présent</span>
      <span><div class="cal-dot" style="background:rgba(244,67,54,.5)"></div>Absent</span>
      <span><div class="cal-dot" style="background:rgba(255,152,0,.5)"></div>Retard</span>
    </div>
    <div class="cal-days-header"><span>L</span><span>M</span><span>M</span><span>J</span><span>V</span><span>S</span><span>D</span></div>
    <div class="cal-grid" id="calGrid"></div>
    <div style="margin-top:1rem;font-size:.8rem;color:#9aa" id="calDetail"></div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-list"></i> Historique récent</div>
    {% for a in assiduite[:30] %}
    <div class="row-item">
      <span>{{ a.date }}</span>
      <span class="badge b-{{ a.statut }}">{{ a.statut }}</span>
      {% if a.heure_arrivee %}<span style="font-size:.72rem;color:#888">{{ a.heure_arrivee }}</span>{% endif %}
    </div>
    {% else %}
    <div style="text-align:center;padding:1.5rem;color:#666">Aucun enregistrement</div>
    {% endfor %}
  </div>
</div>

<!-- PAGE PAIEMENTS -->
<div id="paiement" class="page">
  {% if pay_message %}<div class="pay-flash {{ pay_message.type }}">{{ pay_message.text }}</div>{% endif %}
  <div class="card">
    <div class="card-title"><i class="fas fa-wallet"></i> Situation financière</div>
    <div class="row-item"><span>Frais totaux ({{ user.niveau }})</span><strong>{{ format_mga(paiements.frais_total) }} Ar</strong></div>
    <div class="row-item"><span>Payé</span><strong style="color:#4caf50">{{ format_mga(paiements.total_paye) }} Ar</strong></div>
    <div class="row-item"><span>Solde restant</span><strong style="color:#ff9800">{{ format_mga(paiements.solde_restant) }} Ar</strong></div>
    <div class="prog-bar"><div class="prog-fill" style="width:{{ paiements.pourcentage }}%"></div></div>
    <div style="font-size:.72rem;color:#888;text-align:right">{{ paiements.pourcentage }}% payé</div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-plus-circle"></i> Effectuer un paiement</div>
    <div class="pay-grid">
      <div class="pay-card" onclick="selPay('especes',this)"><i class="fas fa-money-bill-wave"></i><span>Espèces</span></div>
      <div class="pay-card" onclick="selPay('mobile',this)"><i class="fas fa-mobile-alt"></i><span>Mobile Money</span></div>
      <div class="pay-card" onclick="selPay('virement',this)"><i class="fas fa-university"></i><span>Virement</span></div>
      <div class="pay-card" onclick="selPay('carte',this)"><i class="fas fa-credit-card"></i><span>Carte</span></div>
    </div>
    <div class="pay-form" id="pf-especes"><form method="post" action="/soumettre_paiement"><input type="hidden" name="mode" value="Espèces"><input class="pay-input" type="number" name="montant" placeholder="Montant (Ar)" required><input class="pay-input" type="text" name="reference" placeholder="Référence (optionnel)"><button type="submit" class="pay-btn"><i class="fas fa-paper-plane"></i> Envoyer la demande</button></form></div>
    <div class="pay-form" id="pf-mobile"><form method="post" action="/soumettre_paiement"><input type="hidden" name="mode" value="Mobile Money"><input class="pay-input" type="number" name="montant" placeholder="Montant (Ar)" required><input class="pay-input" type="text" name="reference" placeholder="N° transaction requis" required><button type="submit" class="pay-btn"><i class="fas fa-paper-plane"></i> Envoyer la demande</button></form></div>
    <div class="pay-form" id="pf-virement"><form method="post" action="/soumettre_paiement"><input type="hidden" name="mode" value="Virement"><input class="pay-input" type="number" name="montant" placeholder="Montant (Ar)" required><input class="pay-input" type="text" name="reference" placeholder="Référence virement requis" required><button type="submit" class="pay-btn"><i class="fas fa-paper-plane"></i> Envoyer la demande</button></form></div>
    <div class="pay-form" id="pf-carte"><form method="post" action="/soumettre_paiement"><input type="hidden" name="mode" value="Carte"><input class="pay-input" type="number" name="montant" placeholder="Montant (Ar)" required><input class="pay-input" type="text" name="reference" placeholder="Référence carte" required><button type="submit" class="pay-btn"><i class="fas fa-paper-plane"></i> Envoyer la demande</button></form></div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-history"></i> Historique des paiements</div>
    {% for p in historique_paiements %}
    <div class="row-item">
      <span>{{ p.date }}</span>
      <span>{{ format_mga(p.montant) }} Ar</span>
      <span class="badge b-{{ p.statut }}">{{ p.statut }}</span>
    </div>
    {% else %}
    <div style="text-align:center;padding:1.5rem;color:#666">Aucun paiement</div>
    {% endfor %}
  </div>
</div>

<!-- PAGE COURS / EMPLOI DU TEMPS -->
<div id="cours" class="page">
  <div class="card">
    <div class="card-title"><i class="fas fa-table"></i> Emploi du temps — {{ user.filiere }} {{ user.niveau }}</div>
    <div style="overflow-x:auto">
      <table class="edt-table">
        <thead>
          <tr>
            <th>Heure</th>
            <th>LUNDI</th><th>MARDI</th><th>MERCREDI</th><th>JEUDI</th><th>VENDREDI</th>
          </tr>
        </thead>
        <tbody>
          {% set horaires = ['08:00-10:00','10:15-12:15','13:00-15:00','15:15-17:15'] %}
          {% for h in horaires %}
          {% set hd = h.split('-')[0] %}
          {% set hf = h.split('-')[1] %}
          <tr>
            <td style="color:var(--gold);font-size:.72rem;text-align:center;white-space:nowrap;background:rgba(255,215,0,.05)">{{ h }}</td>
            {% for jour in ['LUNDI','MARDI','MERCREDI','JEUDI','VENDREDI'] %}
              <td>
                {% set found = [] %}
                {% for c in emploi_du_temps if c.jour==jour and c.heure_debut==hd %}
                  {% set _ = found.append(c) %}
                {% endfor %}
                {% if found %}
                  {% set c = found[0] %}
                  <div class="edt-cell">
                    <div class="mat">{{ c.matiere }}</div>
                    <div class="ens"><i class="fas fa-user" style="font-size:.55rem"></i> {{ c.enseignant }}</div>
                    <div class="salle"><i class="fas fa-door-open" style="font-size:.55rem"></i> {{ c.salle }}</div>
                  </div>
                {% endif %}
              </td>
            {% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% if edt_photos %}
  <div class="card">
    <div class="card-title"><i class="fas fa-images"></i> Emploi du temps (photos)</div>
    <div class="edt-photo-grid">
      {% for p in edt_photos %}
      <div class="edt-photo-card">
        <a href="/edt_photo/{{ p.fichier }}" target="_blank">
          <img src="/edt_photo/{{ p.fichier }}" alt="EDT">
        </a>
        <div class="edt-photo-info">{{ p.annee_academique }} · S{{ p.semestre }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
</div>

<!-- PAGE BIBLIOTHEQUE -->
<div id="bibliotheque" class="page">
  <div class="card">
    <div class="card-title"><i class="fas fa-book"></i> Bibliothèque — {{ user.filiere }}</div>
    {% for doc in bibliotheque %}
    <div class="doc-card">
      <div class="doc-info">
        <h4><i class="fas fa-{{ 'file-pdf' if doc.fichier and doc.fichier.endswith('.pdf') else 'file-alt' }}" style="margin-right:.4rem"></i>{{ doc.titre }}</h4>
        <p>{{ doc.auteur or 'Sans auteur' }} · {{ doc.type_document }} · {{ doc.niveau or 'Tous niveaux' }}</p>
      </div>
      {% if doc.fichier %}<a href="/documents/{{ doc.fichier }}" target="_blank" class="doc-dl"><i class="fas fa-download"></i></a>{% endif %}
    </div>
    {% else %}
    <div style="text-align:center;padding:2rem;color:#666">Aucun document disponible</div>
    {% endfor %}
  </div>
</div>

<!-- PAGE NOTES -->
<div id="notes" class="page">
  <div class="card">
    <div class="card-title"><i class="fas fa-chart-line"></i> Mes notes</div>
    <div class="avg-box">
      <div class="avg-val">{{ notes.moyenne }}/20</div>
      <div style="font-size:.8rem;color:#9aa;margin-top:.3rem">Moyenne générale</div>
    </div>
    <div class="sem-tabs">
      <button class="sem-btn active" onclick="showSem(1,this)">Semestre 1 — {{ notes.semestre_avg[1] }}/20</button>
      <button class="sem-btn" onclick="showSem(2,this)">Semestre 2 — {{ notes.semestre_avg[2] }}/20</button>
    </div>
    <div id="s1">
      {% for n in notes.details if n.semestre==1 %}
      <div class="row-item">
        <span>{{ n.matiere }}</span>
        <span style="color:{% if n.note>=12 %}#4caf50{% elif n.note>=10 %}#ff9800{% else %}#f44336{% endif %};font-weight:600">{{ n.note }}/20</span>
        <span style="font-size:.7rem;color:#888">coeff.{{ n.coefficient }}</span>
      </div>
      {% else %}<div style="text-align:center;padding:1rem;color:#666">Pas de notes S1</div>{% endfor %}
    </div>
    <div id="s2" style="display:none">
      {% for n in notes.details if n.semestre==2 %}
      <div class="row-item">
        <span>{{ n.matiere }}</span>
        <span style="color:{% if n.note>=12 %}#4caf50{% elif n.note>=10 %}#ff9800{% else %}#f44336{% endif %};font-weight:600">{{ n.note }}/20</span>
        <span style="font-size:.7rem;color:#888">coeff.{{ n.coefficient }}</span>
      </div>
      {% else %}<div style="text-align:center;padding:1rem;color:#666">Pas de notes S2</div>{% endfor %}
    </div>
    <div class="chart-wrap"><canvas id="chartNotesBar"></canvas></div>
    <div class="export-btns">
      <a href="/export_bulletin/1" class="export-btn"><i class="fas fa-file-pdf"></i> Bulletin S1</a>
      <a href="/export_bulletin/2" class="export-btn"><i class="fas fa-file-pdf"></i> Bulletin S2</a>
    </div>
  </div>
</div>

<!-- PAGE PROFIL -->
<div id="profil" class="page">
  <div class="card">
    <div class="card-title"><i class="fas fa-user-circle"></i> Mon profil</div>
    <div style="text-align:center;margin-bottom:1.2rem">
      <div class="profil-avatar" onclick="openPhotoModal()">
        {% if user.photo_profil %}<img src="/uploads/{{ user.photo_profil }}">{% else %}<i class="fas fa-user-circle"></i>{% endif %}
      </div>
      <div style="font-size:.75rem;color:#888"><i class="fas fa-camera"></i> Cliquer pour changer la photo</div>
    </div>
    <div class="info-row"><strong>Nom complet</strong><span>{{ user.prenom }} {{ user.nom }}</span></div>
    <div class="info-row"><strong>Matricule</strong><span>{{ user.matricule }}</span></div>
    <div class="info-row"><strong>Email</strong><span>{{ user.email }}</span></div>
    <div class="info-row"><strong>Téléphone</strong><span>{{ user.telephone or '—' }}</span></div>
    <div class="info-row"><strong>Filière</strong><span>{{ user.filiere }}</span></div>
    <div class="info-row"><strong>Niveau</strong><span>{{ user.niveau }}</span></div>
    <div class="info-row"><strong>Inscrit le</strong><span>{{ user.date_inscription }}</span></div>
    <div class="info-row"><strong>Frais de scolarité</strong><span>{{ format_mga(paiements.frais_total) }} Ar</span></div>
  </div>
</div>

</div><!-- /main -->

<!-- Bottom Nav -->
<div class="bottom-nav">
  <button class="bnav-item active" data-page="dashboard"><i class="fas fa-chart-pie"></i><span>Accueil</span></button>
  <button class="bnav-item" data-page="cours"><i class="fas fa-calendar-alt"></i><span>Cours</span></button>
  <button class="bnav-item" data-page="notes"><i class="fas fa-chart-line"></i><span>Notes</span></button>
  <button class="bnav-item" data-page="paiement"><i class="fas fa-credit-card"></i><span>Paiements</span></button>
  <button class="bnav-item" data-page="profil"><i class="fas fa-user"></i><span>Profil</span></button>
</div>

<!-- Photo Modal -->
<div class="modal" id="photoModal">
  <div class="modal-box">
    <h3><i class="fas fa-camera"></i> Photo de profil</h3>
    <form action="/upload_photo" method="post" enctype="multipart/form-data">
      <input type="file" name="photo" id="photoInput" accept="image/*" style="display:none" onchange="this.form.submit()">
    </form>
    <button class="btn-gold" onclick="document.getElementById('photoInput').click()">Choisir une photo</button>
    <button class="btn-cancel" onclick="document.getElementById('photoModal').classList.remove('show')">Annuler</button>
  </div>
</div>

<script>
// ── Navigation
function goPage(id){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelectorAll('.sb-item[data-page]').forEach(m=>m.classList.toggle('active',m.dataset.page===id));
  document.querySelectorAll('.bnav-item[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===id));
  if(window.innerWidth<768)document.getElementById('sidebar').classList.remove('open');
  document.getElementById('notifPanel').classList.remove('show');
}
document.querySelectorAll('.sb-item[data-page]').forEach(i=>i.addEventListener('click',()=>goPage(i.dataset.page)));
document.querySelectorAll('.bnav-item[data-page]').forEach(i=>i.addEventListener('click',()=>goPage(i.dataset.page)));
// Fermer sidebar/notif en cliquant ailleurs
document.addEventListener('click',e=>{
  const sb=document.getElementById('sidebar');
  const np=document.getElementById('notifPanel');
  if(!sb.contains(e.target)&&!e.target.classList.contains('menu-btn')&&window.innerWidth<768)sb.classList.remove('open');
  if(!np.contains(e.target)&&!e.target.closest('.notif-btn'))np.classList.remove('show');
});
// ── Photo modal
function openPhotoModal(){document.getElementById('photoModal').classList.add('show')}
// ── Notifications
function markRead(id){fetch('/mark_notification_read/'+id,{method:'POST'}).then(()=>location.reload())}
// ── Paiements
function selPay(m,el){
  document.querySelectorAll('.pay-card').forEach(c=>c.classList.remove('sel'));
  el.classList.add('sel');
  document.querySelectorAll('.pay-form').forEach(f=>f.classList.remove('show'));
  document.getElementById('pf-'+m).classList.add('show');
}
// ── Semestres notes
function showSem(n,btn){
  document.querySelectorAll('.sem-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('s1').style.display=n===1?'block':'none';
  document.getElementById('s2').style.display=n===2?'block':'none';
}
// ── Calendrier assiduité
const assiduiteData = {{ assiduite|tojson }};
const calGrid = document.getElementById('calGrid');
const today = new Date();
const yr = today.getFullYear(), mo = today.getMonth();
const firstDay = new Date(yr,mo,1);
const daysInMonth = new Date(yr,mo+1,0).getDate();
// pad empty cells
let startDow = firstDay.getDay(); // 0=Sun
startDow = startDow===0?6:startDow-1; // Mon=0
const assiduiteMap = {};
assiduiteData.forEach(a=>{assiduiteMap[a.date]=a});
for(let i=0;i<startDow;i++){const d=document.createElement('div');d.className='cal-day cal-e';calGrid.appendChild(d);}
for(let d=1;d<=daysInMonth;d++){
  const dateStr = `${yr}-${String(mo+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
  const rec = assiduiteMap[dateStr];
  const el = document.createElement('div');
  const cls = rec ? (rec.statut==='present'?'cal-p':rec.statut==='absent'?'cal-a':'cal-r') : 'cal-e';
  el.className = `cal-day ${cls}`;
  if(d===today.getDate() && mo===today.getMonth()) el.classList.add('today');
  el.textContent = d;
  if(rec){
    const tt = document.createElement('div');
    tt.className = 'tooltip-cal';
    tt.textContent = rec.statut + (rec.heure_arrivee?' '+rec.heure_arrivee:'');
    el.appendChild(tt);
  }
  calGrid.appendChild(el);
}
// ── Charts
const notesLabels=[{% for n in notes.details %}'{{ n.matiere[:10] }}'{% if not loop.last %},{% endif %}{% endfor %}];
const notesVals=[{% for n in notes.details %}{{ n.note }}{% if not loop.last %},{% endif %}{% endfor %}];
const gradColors = notesVals.map(v=>v>=12?'rgba(76,175,80,.8)':v>=10?'rgba(255,152,0,.8)':'rgba(244,67,54,.8)');
if(document.getElementById('chartNotes')){
  new Chart(document.getElementById('chartNotes'),{type:'line',data:{labels:notesLabels,datasets:[{label:'Notes',data:notesVals,borderColor:'#ffd700',backgroundColor:'rgba(255,215,0,.08)',fill:true,tension:.3,pointBackgroundColor:'#ffd700'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{min:0,max:20,grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#888',font:{size:9}}},x:{grid:{color:'rgba(255,255,255,.03)'},ticks:{color:'#888',font:{size:8},maxRotation:30}}}}});
}
if(document.getElementById('chartNotesBar')){
  new Chart(document.getElementById('chartNotesBar'),{type:'bar',data:{labels:notesLabels,datasets:[{label:'Notes',data:notesVals,backgroundColor:gradColors,borderRadius:4}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{min:0,max:20,grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#888',font:{size:9}}},x:{grid:{color:'rgba(255,255,255,.02)'},ticks:{color:'#888',font:{size:8},maxRotation:30}}}}});
}
if(document.getElementById('chartPay')){
  new Chart(document.getElementById('chartPay'),{type:'doughnut',data:{labels:['Payé','Restant'],datasets:[{data:[{{ paiements.total_paye }},{{ paiements.solde_restant }}],backgroundColor:['#ffd700','rgba(255,255,255,.06)'],borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,cutout:'72%',plugins:{legend:{position:'bottom',labels:{color:'#888',font:{size:10}}}}}});
}
</script>
<script>
    window.userContext = {
        role: "étudiant",
        name: "{{ user.prenom }} {{ user.nom }}",
        filiere: "{{ user.filiere }}",
        niveau: "{{ user.niveau }}"
    };
</script>
{{ CHATBOT_TEMPLATE|safe }}
</body>
</html>'''

ADMIN_DASHBOARD_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Admin — ORION University</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--gold:#ffd700;--gold2:#ffb300;--dark:#060810;--card:rgba(14,17,35,.95)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sora',sans-serif;background:linear-gradient(145deg,#060810,#0d1228,#0a1520);color:#fff;min-height:100vh}
.aheader{background:rgba(10,12,26,.97);padding:.8rem 1.2rem;display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid rgba(255,215,0,.2);position:sticky;top:0;z-index:100}
.aheader-left{display:flex;align-items:center;gap:12px}
.aheader-left img{width:36px;height:36px;border-radius:50%;border:2px solid var(--gold)}
.aheader h1{color:var(--gold);font-size:1.05rem}
.lbtn{background:rgba(244,67,54,.15);color:#ff6b6b;padding:.4rem .9rem;border-radius:8px;text-decoration:none;font-size:.78rem;border:1px solid rgba(244,67,54,.3)}
.acontent{padding:1rem;max-width:1500px;margin:0 auto}
.tabs{display:flex;flex-wrap:wrap;gap:.35rem;margin-bottom:1rem}
.tab{background:rgba(255,215,0,.07);color:#ccc;border:1px solid rgba(255,215,0,.15);padding:.45rem .9rem;border-radius:8px;cursor:pointer;font-size:.75rem;font-family:'Sora',sans-serif;transition:all .2s}
.tab.active{background:var(--gold);color:#060810;border-color:var(--gold);font-weight:700}
.apage{display:none}.apage.active{display:block}
.sgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:.7rem;margin-bottom:1rem}
.sc{background:var(--card);border-radius:14px;padding:1rem;text-align:center;position:relative;overflow:hidden;border:1px solid rgba(255,215,0,.08)}
.sc::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--gold),var(--gold2))}
.sc h3{font-size:.7rem;color:#888;text-transform:uppercase;letter-spacing:.04em;margin-bottom:.35rem}
.sc .n{font-size:1.5rem;font-weight:800;color:var(--gold)}
.sc.alert-card .n{color:#ff9800}
.alert-banner{background:rgba(255,152,0,.1);border:1px solid rgba(255,152,0,.4);border-radius:12px;padding:.8rem 1rem;margin-bottom:.8rem;color:#ffcc80;font-size:.82rem}
.chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:1rem;margin-bottom:1rem}
.chart-card{background:var(--card);border-radius:14px;padding:1rem;border:1px solid rgba(255,215,0,.08)}
.chart-card h3{font-size:.82rem;color:var(--gold);margin-bottom:.7rem;font-weight:600}
.chart-card img{width:100%;height:auto;border-radius:8px}
.btn-add{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#060810;border:none;padding:.45rem 1rem;border-radius:8px;cursor:pointer;font-size:.78rem;font-weight:700;margin-bottom:.7rem;font-family:'Sora',sans-serif;display:inline-flex;align-items:center;gap:.4rem}
.srch{margin-bottom:.6rem}
.srch input{padding:.5rem .8rem;border-radius:8px;border:1px solid rgba(255,215,0,.2);background:rgba(255,255,255,.04);color:#fff;width:100%;font-family:'Sora',sans-serif;font-size:.82rem}
.tbl{background:var(--card);border-radius:14px;overflow-x:auto;border:1px solid rgba(255,215,0,.07)}
table{width:100%;border-collapse:collapse;font-size:.73rem}
th,td{padding:.65rem .6rem;text-align:left;border-bottom:1px solid rgba(255,255,255,.04)}
th{color:var(--gold);font-weight:600;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;background:rgba(255,215,0,.04)}
tr:hover td{background:rgba(255,255,255,.02)}
.be{background:rgba(33,150,243,.18);color:#64b5f6;padding:.22rem .55rem;border-radius:6px;cursor:pointer;font-size:.68rem;border:none;font-family:'Sora',sans-serif;text-decoration:none;display:inline-block}
.bd{background:rgba(244,67,54,.15);color:#ef9a9a;padding:.22rem .55rem;border-radius:6px;cursor:pointer;font-size:.68rem;border:none;font-family:'Sora',sans-serif;text-decoration:none;display:inline-block;margin-left:.2rem}
.bv{background:rgba(76,175,80,.18);color:#81c784;padding:.22rem .55rem;border-radius:6px;cursor:pointer;font-size:.68rem;border:none;font-family:'Sora',sans-serif;text-decoration:none;display:inline-block}
.br{background:rgba(255,152,0,.18);color:#ffb74d;padding:.22rem .55rem;border-radius:6px;cursor:pointer;font-size:.68rem;border:none;font-family:'Sora',sans-serif;text-decoration:none;display:inline-block}
.badge-paye{background:rgba(76,175,80,.2);color:#69f0ae;padding:.15rem .5rem;border-radius:10px;font-size:.65rem}
.badge-att{background:rgba(255,152,0,.2);color:#ffcc80;padding:.15rem .5rem;border-radius:10px;font-size:.65rem}
.badge-reg-att{background:rgba(33,150,243,.2);color:#82b1ff;padding:.15rem .5rem;border-radius:10px;font-size:.65rem}
.notif-form{background:var(--card);border-radius:14px;padding:1.2rem;margin-bottom:1rem;border:1px solid rgba(255,215,0,.08)}
.notif-form h3{color:var(--gold);font-size:.88rem;margin-bottom:.8rem}
/* Filière/Niveau classement */
.filiere-section{margin-bottom:1.5rem}
.filiere-header{background:linear-gradient(135deg,rgba(255,215,0,.15),rgba(255,215,0,.05));border:1px solid rgba(255,215,0,.25);border-radius:12px;padding:.7rem 1.2rem;margin-bottom:.5rem;font-size:.92rem;font-weight:700;color:var(--gold);cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.niveau-section{margin-bottom:.8rem;padding-left:.5rem}
.niveau-header{background:rgba(255,255,255,.03);border-left:3px solid rgba(255,215,0,.4);border-radius:0 8px 8px 0;padding:.5rem .9rem;margin-bottom:.4rem;font-size:.8rem;font-weight:600;color:#ccc;display:flex;justify-content:space-between;align-items:center}
.niveau-header span{background:rgba(255,215,0,.1);color:var(--gold);padding:.1rem .5rem;border-radius:10px;font-size:.7rem}
/* Inscriptions en attente */
.inscription-card{background:rgba(33,150,243,.06);border:1px solid rgba(33,150,243,.25);border-radius:12px;padding:.9rem;margin-bottom:.6rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem}
.inscription-info h4{font-size:.88rem;color:#fff;margin-bottom:.2rem}
.inscription-info p{font-size:.72rem;color:#9aa}
.inscription-actions{display:flex;gap:.4rem}
/* Modal */
.fmodal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:1000;align-items:center;justify-content:center}
.fmodal.show{display:flex}
.fmc{background:rgba(14,17,35,.99);border-radius:20px;padding:1.5rem;width:92%;max-width:520px;max-height:88vh;overflow-y:auto;border:1px solid var(--gold)}
.fmc h2{color:var(--gold);font-size:1rem;margin-bottom:1rem}
.fg{margin-bottom:.7rem}
.fg label{display:block;color:#9aa;font-size:.75rem;margin-bottom:.22rem}
.fg input,.fg select,.fg textarea{width:100%;padding:.6rem .8rem;border-radius:8px;border:1px solid rgba(255,215,0,.2);background:rgba(255,255,255,.04);color:#fff;font-family:'Sora',sans-serif;font-size:.85rem}
.fg input:focus,.fg select:focus{outline:none;border-color:var(--gold)}
.fg select option{background:#0d1228}
.mbtns{display:flex;gap:.7rem;margin-top:1rem}
.bs{background:var(--gold);color:#060810;padding:.55rem 1.1rem;border:none;border-radius:8px;cursor:pointer;font-family:'Sora',sans-serif;font-weight:700;font-size:.85rem}
.bc{background:rgba(255,255,255,.1);color:#fff;padding:.55rem 1.1rem;border:none;border-radius:8px;cursor:pointer;font-family:'Sora',sans-serif;font-size:.85rem}
@media(min-width:768px){.sgrid{grid-template-columns:repeat(4,1fr)}}
</style></head>
<body>
<div class="aheader">
  <div class="aheader-left"><img src="/static/logo.png" alt="ORION"><h1><i class="fas fa-crown"></i> ORION Administration</h1></div>
  <a href="/admin/logout" class="lbtn"><i class="fas fa-sign-out-alt"></i> Déconnexion</a>
</div>

<div class="acontent">
<div class="tabs">
    <button class="tab active" data-p="dashboard"><i class="fas fa-chart-line"></i> Dashboard</button>
    <button class="tab" data-p="inscriptions"><i class="fas fa-clipboard-list"></i> Inscriptions{% if stats.inscriptions_en_attente > 0 %} <span style="background:#f44336;color:#fff;border-radius:50%;padding:0 5px;font-size:.65rem">{{ stats.inscriptions_en_attente }}</span>{% endif %}</button>
    <button class="tab" data-p="notifications"><i class="fas fa-bell"></i> Notifs</button>
    <button class="tab" data-p="etudiants"><i class="fas fa-users"></i> Étudiants</button>
    <button class="tab" data-p="cours"><i class="fas fa-calendar-alt"></i> Cours</button>
    <button class="tab" data-p="assiduite"><i class="fas fa-check-circle"></i> Assiduité</button>
    <button class="tab" data-p="notes"><i class="fas fa-pen-alt"></i> Notes</button>
    <button class="tab" data-p="paiements"><i class="fas fa-credit-card"></i> Paiements</button>
    <button class="tab" data-p="bibliotheque"><i class="fas fa-book"></i> Bibliothèque</button>
    <button class="tab" data-p="edt"><i class="fas fa-images"></i> EDT Photos</button>

</div>

<!-- DASHBOARD -->
<div id="dashboard" class="apage active">
  <div class="sgrid">
    <div class="sc"><h3>Étudiants actifs</h3><div class="n">{{ stats.total_etudiants }}</div></div>
    <div class="sc alert-card"><h3>En attente inscription</h3><div class="n">{{ stats.inscriptions_en_attente }}</div></div>
    <div class="sc"><h3>Cours</h3><div class="n">{{ stats.total_cours }}</div></div>
    <div class="sc"><h3>Moyenne générale</h3><div class="n">{{ stats.moyenne_generale }}/20</div></div>
    <div class="sc"><h3>Revenus totaux</h3><div class="n" style="font-size:1rem">{{ stats.revenus_totaux }} Ar</div></div>
    <div class="sc alert-card"><h3>Paiements en attente</h3><div class="n">{{ stats.paiements_en_attente }}</div></div>
  </div>
  {% if stats.paiements_en_attente > 0 %}
  <div class="alert-banner"><i class="fas fa-exclamation-triangle"></i> {{ stats.paiements_en_attente }} paiement(s) nécessitent votre validation. <a href="#" onclick="switchTab('paiements')" style="color:var(--gold);text-decoration:underline">Voir →</a></div>
  {% endif %}
  <div class="chart-grid">
    {% if graphs.filieres %}<div class="chart-card"><h3><i class="fas fa-chart-bar"></i> Statistiques par filière</h3><img src="data:image/png;base64,{{ graphs.filieres }}"></div>{% endif %}
    {% if graphs.paiements %}<div class="chart-card"><h3><i class="fas fa-chart-line"></i> Évolution des paiements</h3><img src="data:image/png;base64,{{ graphs.paiements }}"></div>{% endif %}
  </div>
  <div class="tbl">
    <div style="padding:.8rem 1rem;color:var(--gold);font-size:.85rem;font-weight:600;border-bottom:1px solid rgba(255,215,0,.1)"><i class="fas fa-exclamation-circle"></i> Paiements en retard</div>
    <table><thead><tr><th>Étudiant</th><th>Filière</th><th>Niveau</th><th>Payé</th><th>Restant</th><th>Action</th></tr></thead><tbody>
    {% for p in paiements_retard %}
    <tr><td>{{ p[1] }} {{ p[2] }}</td><td>{{ p[3] }}</td><td>{{ p[5] }}</td><td>{{ format_mga(p[6]) }} Ar</td><td style="color:#ff9800">{{ format_mga(get_frais(p[5])-p[6]) }} Ar</td>
    <td><form method="post" action="/admin/send_payment_reminder" style="display:inline"><input type="hidden" name="user_id" value="{{ p[0] }}"><button type="submit" class="br"><i class="fas fa-bell"></i> Rappel</button></form></td></tr>
    {% else %}<tr><td colspan="6" style="text-align:center;color:#666;padding:1rem">Tous les paiements sont à jour</td></tr>{% endfor %}
    </tbody></table>
  </div>
</div>

<!-- INSCRIPTIONS EN ATTENTE -->
<div id="inscriptions" class="apage">
  <h2 style="color:var(--gold);margin-bottom:1rem;font-size:1rem"><i class="fas fa-user-clock"></i> Demandes d'inscription en attente</h2>
  {% for i in inscriptions_attente %}
  <div class="inscription-card">
    <div class="inscription-info">
      <h4>{{ i.prenom }} {{ i.nom }} <span class="badge-reg-att">{{ i.matricule }}</span></h4>
      <p>{{ i.email }} · {{ i.telephone }} · {{ i.filiere }} — {{ i.niveau }}</p>
      <p style="color:#666;font-size:.68rem">Soumis le {{ i.date_inscription }}</p>
    </div>
    <div class="inscription-actions">
      <a href="/admin/inscription/valider/{{ i.id }}" class="bv"><i class="fas fa-check"></i> Valider</a>
      <a href="/admin/inscription/rejeter/{{ i.id }}" class="bd" onclick="return confirm('Rejeter cette demande ?')"><i class="fas fa-times"></i> Rejeter</a>
    </div>
  </div>
  {% else %}
  <div style="text-align:center;padding:2rem;color:#666"><i class="fas fa-check-circle" style="font-size:2rem;color:#4caf50;display:block;margin-bottom:.8rem"></i>Aucune inscription en attente</div>
  {% endfor %}
</div>

<!-- NOTIFICATIONS -->
<div id="notifications" class="apage">
  <div class="notif-form">
    <h3><i class="fas fa-paper-plane"></i> Envoyer une notification</h3>
    <form method="post" action="/admin/send_notification">
      <div class="fg"><label>Titre</label><input name="titre" required></div>
      <div class="fg"><label>Message</label><textarea name="message" rows="2" required></textarea></div>
      <div class="fg"><label>Destinataire</label><select name="destinataire"><option value="all">Tous les étudiants</option>{% for e in etudiants %}<option value="{{ e.id }}">{{ e.prenom }} {{ e.nom }}</option>{% endfor %}</select></div>
      <div class="fg"><label>Type</label><select name="type"><option value="info">Info</option><option value="warning">Avertissement</option><option value="success">Succès</option></select></div>
      <button type="submit" class="btn-add"><i class="fas fa-paper-plane"></i> Envoyer</button>
    </form>
  </div>
  <div class="tbl"><table><thead><tr><th>Titre</th><th>Destinataire</th><th>Date</th><th>Action</th></tr></thead><tbody>
  {% for n in notifications_list %}<tr><td>{{ n.titre }}</td><td>{{ n.destinataire_nom or 'Tous' }}</td><td>{{ n.date_envoi }}</td><td><a href="/admin/notification/delete/{{ n.id }}" class="bd" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a></td></tr>{% endfor %}
  </tbody></table></div>
</div>

<!-- ÉTUDIANTS -->
<div id="etudiants" class="apage">
  <button class="btn-add" onclick="openModal('student')"><i class="fas fa-plus"></i> Ajouter étudiant</button>
  <div class="srch"><input type="text" id="srchS" placeholder="🔍 Rechercher..." onkeyup="filterTbl('tblS',this.value)"></div>
  {% for filiere in ['Informatique','Finance','Marketing'] %}
  {% set ets_fil = etudiants|selectattr('filiere','equalto',filiere)|list %}
  {% if ets_fil %}
  <div class="filiere-section">
    <div class="filiere-header" onclick="toggleSection('fil-{{ filiere|lower }}')">
      <span><i class="fas fa-graduation-cap"></i> {{ filiere }} ({{ ets_fil|length }})</span>
      <i class="fas fa-chevron-down"></i>
    </div>
    <div id="fil-{{ filiere|lower }}">
      {% for niveau in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}
      {% set ets_niv = ets_fil|selectattr('niveau','equalto',niveau)|list %}
      {% if ets_niv %}
      <div class="niveau-section">
        <div class="niveau-header"><span>{{ niveau }}</span><span>{{ ets_niv|length }} étudiant(s)</span></div>
        <div class="tbl">
          <table id="tblS"><thead><tr><th>Matricule</th><th>Nom</th><th>Email</th><th>Tél.</th><th>Actions</th></tr></thead><tbody>
          {% for e in ets_niv %}
          <tr><td>{{ e.matricule }}</td><td>{{ e.prenom }} {{ e.nom }}</td><td>{{ e.email }}</td><td>{{ e.telephone }}</td>
          <td><a href="/admin/student/edit/{{ e.id }}" class="be"><i class="fas fa-edit"></i></a><a href="/admin/student/delete/{{ e.id }}" class="bd" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a></td></tr>
          {% endfor %}
          </tbody></table>
        </div>
      </div>
      {% endif %}{% endfor %}
    </div>
  </div>{% endif %}{% endfor %}
</div>

<!-- COURS -->
<div id="cours" class="apage">
  <button class="btn-add" onclick="openModal('cours')"><i class="fas fa-plus"></i> Ajouter cours</button>
  {% for filiere in ['Informatique','Finance','Marketing'] %}
  {% set cours_fil = cours_list|selectattr('filiere','equalto',filiere)|list %}
  {% if cours_fil %}
  <div class="filiere-section">
    <div class="filiere-header" onclick="toggleSection('cf-{{ filiere|lower }}')">
      <span><i class="fas fa-book-open"></i> {{ filiere }} ({{ cours_fil|length }} cours)</span>
      <i class="fas fa-chevron-down"></i>
    </div>
    <div id="cf-{{ filiere|lower }}">
      {% for niveau in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}
      {% set cours_niv = cours_fil|selectattr('niveau','equalto',niveau)|list %}
      {% if cours_niv %}
      <div class="niveau-section">
        <div class="niveau-header"><span>{{ niveau }}</span><span>{{ cours_niv|length }} cours</span></div>
        <div class="tbl">
          <table><thead><tr><th>Matière</th><th>Enseignant</th><th>Jour</th><th>Horaire</th><th>Salle</th><th>S.</th><th>Actions</th></tr></thead><tbody>
          {% for c in cours_niv %}
          <tr><td>{{ c.matiere }}</td><td>{{ c.enseignant }}</td><td>{{ c.jour }}</td><td>{{ c.heure_debut }}–{{ c.heure_fin }}</td><td>{{ c.salle }}</td><td>S{{ c.semestre }}</td>
          <td><a href="/admin/course/edit/{{ c.id }}" class="be"><i class="fas fa-edit"></i></a><a href="/admin/course/delete/{{ c.id }}" class="bd" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a></td></tr>
          {% endfor %}
          </tbody></table>
        </div>
      </div>
      {% endif %}{% endfor %}
    </div>
  </div>{% endif %}{% endfor %}
</div>

<!-- ASSIDUITÉ -->
<div id="assiduite" class="apage">
  <button class="btn-add" onclick="openModal('assiduite')"><i class="fas fa-plus"></i> Ajouter présence</button>
  {% for filiere in ['Informatique','Finance','Marketing'] %}
  {% set ass_fil = assiduite_list|selectattr('filiere','equalto',filiere)|list %}
  {% if ass_fil %}
  <div class="filiere-section">
    <div class="filiere-header" onclick="toggleSection('af-{{ filiere|lower }}')">
      <span><i class="fas fa-calendar-check"></i> {{ filiere }} ({{ ass_fil|length }} enregistrements)</span>
      <i class="fas fa-chevron-down"></i>
    </div>
    <div id="af-{{ filiere|lower }}">
      {% for niveau in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}
      {% set ass_niv = ass_fil|selectattr('niveau','equalto',niveau)|list %}
      {% if ass_niv %}
      <div class="niveau-section">
        <div class="niveau-header"><span>{{ niveau }}</span><span>{{ ass_niv|length }}</span></div>
        <div class="tbl">
          <table><thead><tr><th>Étudiant</th><th>Date</th><th>Statut</th><th>Heure</th><th>Action</th></tr></thead><tbody>
          {% for a in ass_niv %}
          <tr><td>{{ a.nom }}</td><td>{{ a.date }}</td><td>
            <span class="{% if a.statut=='present' %}badge-paye{% elif a.statut=='absent' %}badge-att{% else %}badge-reg-att{% endif %}">{{ a.statut }}</span>
          </td><td>{{ a.heure_arrivee or '–' }}</td>
          <td><a href="/admin/assiduite/delete/{{ a.id }}" class="bd" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a></td></tr>
          {% endfor %}
          </tbody></table>
        </div>
      </div>
      {% endif %}{% endfor %}
    </div>
  </div>{% endif %}{% endfor %}
</div>

<!-- NOTES -->
<div id="notes" class="apage">
  <button class="btn-add" onclick="openModal('note')"><i class="fas fa-plus"></i> Ajouter note</button>
  {% for filiere in ['Informatique','Finance','Marketing'] %}
  {% set notes_fil = notes_list|selectattr('filiere','equalto',filiere)|list %}
  {% if notes_fil %}
  <div class="filiere-section">
    <div class="filiere-header" onclick="toggleSection('nf-{{ filiere|lower }}')">
      <span><i class="fas fa-chart-line"></i> {{ filiere }} ({{ notes_fil|length }} notes)</span>
      <i class="fas fa-chevron-down"></i>
    </div>
    <div id="nf-{{ filiere|lower }}">
      {% for niveau in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}
      {% set notes_niv = notes_fil|selectattr('niveau','equalto',niveau)|list %}
      {% if notes_niv %}
      <div class="niveau-section">
        <div class="niveau-header"><span>{{ niveau }}</span><span>{{ notes_niv|length }} notes</span></div>
        <div class="tbl">
          <table><thead><tr><th>Étudiant</th><th>Matière</th><th>Type</th><th>Note</th><th>Coeff</th><th>S.</th><th>Actions</th></tr></thead><tbody>
          {% for n in notes_niv %}
          <tr><td>{{ n.nom }}</td><td>{{ n.matiere }}</td><td>{{ n.type_note }}</td>
          <td style="color:{% if n.note>=12 %}#69f0ae{% elif n.note>=10 %}#ffcc80{% else %}#ff8a80{% endif %};font-weight:600">{{ n.note }}/20</td>
          <td>{{ n.coefficient }}</td><td>S{{ n.semestre }}</td>
          <td><a href="/admin/note/edit/{{ n.id }}" class="be"><i class="fas fa-edit"></i></a><a href="/admin/note/delete/{{ n.id }}" class="bd" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a></td></tr>
          {% endfor %}
          </tbody></table>
        </div>
      </div>
      {% endif %}{% endfor %}
    </div>
  </div>{% endif %}{% endfor %}
</div>

<!-- PAIEMENTS -->
<div id="paiements" class="apage">
  <button class="btn-add" onclick="openModal('paiement')"><i class="fas fa-plus"></i> Ajouter paiement</button>
  {% for filiere in ['Informatique','Finance','Marketing'] %}
  {% set pay_fil = paiements_list|selectattr('filiere','equalto',filiere)|list %}
  {% if pay_fil %}
  <div class="filiere-section">
    <div class="filiere-header" onclick="toggleSection('pf-{{ filiere|lower }}')">
      <span><i class="fas fa-credit-card"></i> {{ filiere }} ({{ pay_fil|length }})</span>
      <i class="fas fa-chevron-down"></i>
    </div>
    <div id="pf-{{ filiere|lower }}">
      {% for niveau in ['Licence 1','Licence 2','Licence 3','Master 1','Master 2'] %}
      {% set pay_niv = pay_fil|selectattr('niveau','equalto',niveau)|list %}
      {% if pay_niv %}
      <div class="niveau-section">
        <div class="niveau-header"><span>{{ niveau }}</span><span>{{ pay_niv|length }} paiements</span></div>
        <div class="tbl">
          <table><thead><tr><th>Étudiant</th><th>Montant</th><th>Date</th><th>Mode</th><th>Statut</th><th>Actions</th></tr></thead><tbody>
          {% for p in pay_niv %}
          <tr><td>{{ p.nom }}</td><td>{{ format_mga(p.montant) }} Ar</td><td>{{ p.date }}</td><td>{{ p.mode }}</td>
          <td>{% if p.statut=='paye' %}<span class="badge-paye">Payé</span>{% else %}<span class="badge-att">Attente</span>{% endif %}</td>
          <td>
            {% if p.statut=='attente' %}
            <a href="/admin/paiement/valider/{{ p.id }}" class="bv"><i class="fas fa-check"></i></a>
            <a href="/admin/paiement/rejeter/{{ p.id }}" class="br"><i class="fas fa-times"></i></a>
            {% endif %}
            <a href="/admin/paiement/delete/{{ p.id }}" class="bd" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a>
          </td></tr>
          {% endfor %}
          </tbody></table>
        </div>
      </div>
      {% endif %}{% endfor %}
    </div>
  </div>{% endif %}{% endfor %}
</div>

<!-- BIBLIOTHÈQUE -->
<div id="bibliotheque" class="apage">
  <button class="btn-add" onclick="openModal('bibliotheque')"><i class="fas fa-plus"></i> Ajouter document</button>
  {% for filiere in ['Informatique','Finance','Marketing','Toutes'] %}
  {% set docs_fil = bibliotheque_list|selectattr('filiere','equalto',filiere)|list %}
  {% if docs_fil %}
  <div class="filiere-section">
    <div class="filiere-header" onclick="toggleSection('bf-{{ filiere|lower }}')">
      <span><i class="fas fa-book"></i> {{ filiere }} ({{ docs_fil|length }})</span>
      <i class="fas fa-chevron-down"></i>
    </div>
    <div id="bf-{{ filiere|lower }}">
      <div class="tbl"><table><thead><tr><th>Titre</th><th>Auteur</th><th>Type</th><th>Niveau</th><th>Fichier</th><th>Action</th></tr></thead><tbody>
      {% for d in docs_fil %}
      <tr><td>{{ d.titre }}</td><td>{{ d.auteur or '–' }}</td><td>{{ d.type_document }}</td><td>{{ d.niveau or 'Tous' }}</td>
      <td>{% if d.fichier %}<a href="/documents/{{ d.fichier }}" target="_blank" class="be"><i class="fas fa-eye"></i></a>{% else %}–{% endif %}</td>
      <td><a href="/admin/bibliotheque/delete/{{ d.id }}" class="bd" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a></td></tr>
      {% endfor %}
      </tbody></table></div>
    </div>
  </div>{% endif %}{% endfor %}
</div>

<!-- EDT PHOTOS -->
<div id="edt" class="apage">
  <button class="btn-add" onclick="openModal('edt')"><i class="fas fa-plus"></i> Ajouter photo EDT</button>
  <div class="tbl"><table><thead><tr><th>Filière</th><th>Niveau</th><th>Année</th><th>Semestre</th><th>Photo</th><th>Date upload</th><th>Action</th></tr></thead><tbody>
  {% for e in edt_photos_list %}
  <tr><td>{{ e.filiere }}</td><td>{{ e.niveau }}</td><td>{{ e.annee_academique }}</td><td>S{{ e.semestre }}</td>
  <td><a href="/edt_photo/{{ e.fichier }}" target="_blank" class="be"><i class="fas fa-eye"></i> Voir</a></td>
  <td style="font-size:.68rem;color:#888">{{ e.date_upload }}</td>
  <td><a href="/admin/edt_photo/delete/{{ e.id }}" class="bd" onclick="return confirm('Supprimer?')"><i class="fas fa-trash"></i></a></td></tr>
  {% endfor %}
  </tbody></table></div>
</div>

</div>

<!-- Modal générique -->
<div class="fmodal" id="fmodal">
  <div class="fmc">
    <h2 id="mtitle"></h2>
    <form id="mform" method="post" enctype="multipart/form-data">
      <div id="mfields"></div>
      <div class="mbtns">
        <button type="submit" class="bs">Enregistrer</button>
        <button type="button" class="bc" onclick="closeModal()">Annuler</button>
      </div>
    </form>
  </div>
</div>

<script>
function switchTab(p){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.p===p));
  document.querySelectorAll('.apage').forEach(a=>a.classList.toggle('active',a.id===p));
}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>switchTab(t.dataset.p)));
function toggleSection(id){const el=document.getElementById(id);el.style.display=el.style.display==='none'?'block':'none';}
function filterTbl(id,v){const rows=document.getElementById(id)?.rows||[];for(let i=1;i<rows.length;i++)rows[i].style.display=rows[i].innerText.toLowerCase().includes(v.toLowerCase())?'':'none';}
function closeModal(){document.getElementById('fmodal').classList.remove('show');}
window.addEventListener('click',e=>{if(e.target===document.getElementById('fmodal'))closeModal();});
function openModal(type){
  const t=document.getElementById('mtitle'),f=document.getElementById('mfields'),fm=document.getElementById('mform');
  fm.enctype='application/x-www-form-urlencoded';
  const ets=`{% for e in etudiants %}<option value="{{ e.id }}">{{ e.prenom }} {{ e.nom }} — {{ e.filiere }} {{ e.niveau }}</option>{% endfor %}`;
  const filieres='<option>Informatique</option><option>Finance</option><option>Marketing</option>';
  const niveaux='<option>Licence 1</option><option>Licence 2</option><option>Licence 3</option><option>Master 1</option><option>Master 2</option>';
  if(type==='student'){
    t.innerText='Ajouter étudiant';fm.action='/admin/student/add';
    f.innerHTML=`<div class="fg"><label>Nom</label><input name="nom" required></div><div class="fg"><label>Prénom</label><input name="prenom" required></div><div class="fg"><label>Email</label><input type="email" name="email" required></div><div class="fg"><label>Téléphone</label><input name="telephone"></div><div class="fg"><label>Filière</label><select name="filiere">${filieres}</select></div><div class="fg"><label>Niveau</label><select name="niveau">${niveaux}</select></div><div class="fg"><label>Mot de passe</label><input type="password" name="password" required></div>`;
  }else if(type==='cours'){
    t.innerText='Ajouter cours';fm.action='/admin/course/add';
    f.innerHTML=`<div class="fg"><label>Matière</label><input name="matiere" required></div><div class="fg"><label>Enseignant</label><input name="enseignant" required></div><div class="fg"><label>Salle</label><input name="salle" required></div><div class="fg"><label>Jour</label><select name="jour"><option>LUNDI</option><option>MARDI</option><option>MERCREDI</option><option>JEUDI</option><option>VENDREDI</option></select></div><div class="fg"><label>Heure début</label><input type="time" name="heure_debut" required></div><div class="fg"><label>Heure fin</label><input type="time" name="heure_fin" required></div><div class="fg"><label>Semestre</label><input type="number" name="semestre" value="1"></div><div class="fg"><label>Filière</label><select name="filiere">${filieres}</select></div><div class="fg"><label>Niveau</label><select name="niveau">${niveaux}</select></div>`;
  }else if(type==='assiduite'){
    t.innerText='Ajouter présence';fm.action='/admin/assiduite/add';
    f.innerHTML=`<div class="fg"><label>Étudiant</label><select name="user_id">${ets}</select></div><div class="fg"><label>Date</label><input type="date" name="date" required></div><div class="fg"><label>Statut</label><select name="statut"><option value="present">Présent</option><option value="absent">Absent</option><option value="retard">Retard</option></select></div><div class="fg"><label>Heure arrivée</label><input type="time" name="heure_arrivee"></div>`;
  }else if(type==='note'){
    t.innerText='Ajouter note';fm.action='/admin/note/add';
    f.innerHTML=`<div class="fg"><label>Étudiant</label><select name="user_id">${ets}</select></div><div class="fg"><label>Matière</label><input name="matiere" required></div><div class="fg"><label>Type</label><select name="type_note"><option>Examen</option><option>DS</option><option>Bonus</option></select></div><div class="fg"><label>Note (/20)</label><input type="number" step="0.01" name="note" min="0" max="20" required></div><div class="fg"><label>Coefficient</label><input type="number" step="0.5" name="coefficient" value="1" required></div><div class="fg"><label>Semestre</label><select name="semestre"><option value="1">S1</option><option value="2">S2</option></select></div><div class="fg"><label>Année</label><input name="annee_academique" value="2024-2025"></div>`;
  }else if(type==='paiement'){
    t.innerText='Ajouter paiement';fm.action='/admin/paiement/add';
    f.innerHTML=`<div class="fg"><label>Étudiant</label><select name="user_id">${ets}</select></div><div class="fg"><label>Montant (Ar)</label><input type="number" name="montant" required></div><div class="fg"><label>Date</label><input type="date" name="date" required></div><div class="fg"><label>Mode</label><select name="mode"><option>Espèces</option><option>Mobile Money</option><option>Virement</option><option>Carte</option></select></div><div class="fg"><label>Statut</label><select name="statut"><option value="paye">Payé</option><option value="attente">En attente</option></select></div><div class="fg"><label>Référence</label><input name="reference"></div>`;
  }else if(type==='bibliotheque'){
    t.innerText='Ajouter document';fm.action='/admin/bibliotheque/add';fm.enctype='multipart/form-data';
    f.innerHTML=`<div class="fg"><label>Titre</label><input name="titre" required></div><div class="fg"><label>Auteur</label><input name="auteur"></div><div class="fg"><label>Type</label><select name="type_document"><option value="cours">Cours</option><option value="exercice">Exercice</option><option value="corrige">Corrigé</option><option value="examen">Examen</option><option value="livre">Livre</option></select></div><div class="fg"><label>Filière</label><select name="filiere"><option value="Toutes">Toutes</option>${filieres}</select></div><div class="fg"><label>Niveau</label><select name="niveau"><option value="Tous">Tous</option>${niveaux}</select></div><div class="fg"><label>Fichier</label><input type="file" name="fichier" accept=".pdf,.jpg,.jpeg,.png"></div><div class="fg"><label>Description</label><textarea name="description" rows="2"></textarea></div>`;
  }else if(type==='edt'){
    t.innerText='Ajouter photo EDT';fm.action='/admin/edt_photo/add';fm.enctype='multipart/form-data';
    f.innerHTML=`<div class="fg"><label>Filière</label><select name="filiere">${filieres}</select></div><div class="fg"><label>Niveau</label><select name="niveau">${niveaux}</select></div><div class="fg"><label>Année académique</label><input name="annee_academique" value="2024-2025" required></div><div class="fg"><label>Semestre</label><select name="semestre"><option value="1">Semestre 1</option><option value="2">Semestre 2</option></select></div><div class="fg"><label>Photo (image/PDF)</label><input type="file" name="photo" accept="image/*,.pdf" required></div>`;
  }
  document.getElementById('fmodal').classList.add('show');
}
</script>
<script>
    window.userContext = {
        role: "administrateur",
        name: "Administrateur ORION"
    };
</script>
{{ CHATBOT_TEMPLATE|safe }}
</body>
</html>'''

CHATBOT_TEMPLATE = '''<!-- Assistant Virtuel ORION -->
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&family=Exo+2:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

.chatbot-overlay {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 10000;
}

/* Styles du chatbot - Thème ORION */
.chat-fab {
  position: fixed;
  bottom: 80px;
  right: 28px;
  z-index: 10000;
  width: 58px;
  height: 58px;
  border-radius: 50%;
  background: linear-gradient(135deg, #ffd700, #ffb300);
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 0 0 rgba(255,215,0,.4);
  animation: pulse-fab 2.5s ease-in-out infinite;
  transition: transform .2s;
}
.chat-fab:hover { transform: scale(1.1); background: linear-gradient(135deg, #ffed4a, #ffc107); }
@keyframes pulse-fab {
  0% { box-shadow: 0 0 0 0 rgba(255,215,0,.4); }
  70% { box-shadow: 0 0 0 14px rgba(255,215,0,0); }
  100% { box-shadow: 0 0 0 0 rgba(255,215,0,0); }
}
.chat-fab .icon-open { width: 32px; height: 32px; object-fit: contain; }
.chat-fab .icon-close { display: none; width: 28px; height: 28px; fill: #060810; }
.chat-fab.open .icon-open { display: none; }
.chat-fab.open .icon-close { display: block; }

.fab-badge {
  position: absolute;
  top: -3px;
  right: -3px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #ff6b2b;
  color: #fff;
  font-family: 'Orbitron', monospace;
  font-size: .58rem;
  font-weight: 700;
  display: grid;
  place-items: center;
  border: 2px solid #060810;
}

.chat-popup {
  position: fixed;
  bottom: 150px;
  right: 28px;
  z-index: 9999;
  width: 380px;
  height: 560px;
  background: rgba(6,8,16,.98);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255,215,0,.25);
  border-radius: 20px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 24px 64px rgba(0,0,0,.6), 0 0 0 1px rgba(255,215,0,.1);
  overflow: hidden;
  transform: scale(.9) translateY(20px);
  opacity: 0;
  pointer-events: none;
  transition: transform .25s cubic-bezier(.34,1.56,.64,1), opacity .2s ease;
}
.chat-popup.open {
  transform: scale(1) translateY(0);
  opacity: 1;
  pointer-events: all;
}

.chat-header {
  padding: 16px 18px;
  background: linear-gradient(135deg, rgba(255,215,0,.12), rgba(255,215,0,.03));
  border-bottom: 1px solid rgba(255,215,0,.15);
  display: flex;
  align-items: center;
  gap: 12px;
}
.chat-avatar {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: linear-gradient(135deg, #ffd700, #ffb300);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  box-shadow: 0 0 14px rgba(255,215,0,.3);
}
.chat-avatar img {
  width: 32px;
  height: 32px;
  object-fit: contain;
}
.chat-agent-info h3 {
  font-size: .88rem;
  font-weight: 700;
  margin:0;
  color: #ffd700;
  font-family: 'Orbitron', monospace;
}
.chat-agent-info p {
  font-size: .65rem;
  color: #ffd700cc;
  font-family: 'JetBrains Mono', monospace;
  margin:0;
  display: flex;
  align-items: center;
  gap: 4px;
}
.chat-agent-info p::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #00e676;
  box-shadow: 0 0 6px #00e676;
  display: inline-block;
}
.chat-header-actions { margin-left: auto; display: flex; gap: 8px; }
.chat-action-btn {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,215,0,.2);
  color: #ffd700cc;
  cursor: pointer;
  font-size: .9rem;
  display: grid;
  place-items: center;
  transition: all .2s;
}
.chat-action-btn:hover { border-color: #ffd700; color: #ffd700; background: rgba(255,215,0,.1); }

#pipeline-bar {
  display: none;
  padding: 7px 16px;
  background: rgba(255,215,0,.05);
  border-bottom: 1px solid rgba(255,215,0,.1);
  font-family: 'JetBrains Mono', monospace;
  font-size: .65rem;
  color: #ffd700cc;
  align-items: center;
  gap: 8px;
}
#pipeline-bar.active { display: flex; }
.p-step.done { color: #00e676; }
.p-step.active { color: #ffd700; animation: blink-step 1s infinite; }
@keyframes blink-step { 0%,100%{opacity:.5} 50%{opacity:1} }

#chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  scrollbar-width: thin;
  background: rgba(6,8,16,.5);
}
#chat-messages::-webkit-scrollbar { width: 3px; }
#chat-messages::-webkit-scrollbar-thumb { background: rgba(255,215,0,.3); border-radius: 4px; }

.chat-welcome { text-align: center; padding: 16px 8px; }
.chat-welcome .w-icon { font-size: 2rem; margin-bottom: 8px; }
.chat-welcome h4 { font-size: .88rem; font-weight: 700; margin-bottom: 5px; color: #ffd700; font-family: 'Orbitron', monospace; }
.chat-welcome p { font-size: .75rem; color: #9aa; line-height: 1.6; }

.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: center;
  margin-top: 12px;
}
.suggestion-chip {
  font-size: .7rem;
  background: rgba(255,215,0,.07);
  border: 1px solid rgba(255,215,0,.2);
  border-radius: 20px;
  padding: 4px 10px;
  color: #ffd700;
  cursor: pointer;
  transition: background .2s;
}
.suggestion-chip:hover { background: rgba(255,215,0,.15); }

.chat-msg { display: flex; gap: 8px; }
.chat-msg.user { flex-direction: row-reverse; }
.chat-bubble-wrap { display: flex; flex-direction: column; gap: 3px; max-width: 82%; }
.chat-msg.user .chat-bubble-wrap { align-items: flex-end; }
.chat-bubble {
  padding: 10px 13px;
  border-radius: 14px;
  font-size: .82rem;
  line-height: 1.6;
  color: #dce4f5;
}
.chat-msg.bot .chat-bubble {
  background: rgba(255,215,0,.05);
  border: 1px solid rgba(255,215,0,.15);
  border-bottom-left-radius: 4px;
}
.chat-msg.user .chat-bubble {
  background: linear-gradient(135deg, rgba(255,215,0,.15), rgba(255,215,0,.08));
  border: 1px solid rgba(255,215,0,.2);
  border-bottom-right-radius: 4px;
}
.chat-bubble strong { font-weight: 600; color: #ffd700; }
.chat-msg-meta { font-size: .6rem; color: #ffd70099; padding: 0 2px; font-family: 'JetBrains Mono', monospace; }

.typing-indicator { display: flex; gap: 4px; align-items: center; padding: 3px 0; }
.typing-indicator span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  animation: tpulse 1.2s ease-in-out infinite;
}
.typing-indicator span:nth-child(1) { background: #ffd700; }
.typing-indicator span:nth-child(2) { background: #ffb300; animation-delay: .2s; }
.typing-indicator span:nth-child(3) { background: #ffd700; animation-delay: .4s; }
@keyframes tpulse { 0%,80%,100%{transform:scale(.7);opacity:.3} 40%{transform:scale(1.2);opacity:1} }

.chat-input-area {
  padding: 10px 14px 14px;
  border-top: 1px solid rgba(255,215,0,.1);
  background: rgba(0,0,0,.3);
}
#chat-file-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 7px; }
.c-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  background: rgba(255,215,0,.08);
  border: 1px solid rgba(255,215,0,.2);
  border-radius: 20px;
  padding: 2px 8px;
  font-size: .65rem;
  font-family: 'JetBrains Mono', monospace;
  color: #ffd700;
}
.c-chip button { background: none; border: none; cursor: pointer; color: #ffd700; font-size: 11px; }
.chat-input-row { display: flex; gap: 7px; align-items: flex-end; }
#chat-input {
  flex: 1;
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,215,0,.2);
  border-radius: 12px;
  padding: 10px 13px;
  color: #dce4f5;
  font-family: 'Exo 2', sans-serif;
  font-size: .82rem;
  outline: none;
  resize: none;
  min-height: 42px;
  max-height: 110px;
}
#chat-input:focus { border-color: #ffd700; }
#chat-input::placeholder { color: #ffd70066; }
.chat-btn {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  cursor: pointer;
  border: none;
  font-size: 16px;
}
#chat-attach {
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,215,0,.2);
  color: #ffd700;
}
#chat-attach:hover { border-color: #ffd700; background: rgba(255,215,0,.1); color: #ffd700; }
#chat-send {
  background: linear-gradient(135deg, #ffd700, #ffb300);
  color: #060810;
  font-weight: 700;
  box-shadow: 0 0 14px rgba(255,215,0,.2);
}
#chat-send:disabled { opacity: .35; cursor: default; }
#chat-file-input { display: none; }
.chat-hint { font-size: .6rem; color: #ffd70099; text-align: center; margin-top: 6px; font-family: 'JetBrains Mono', monospace; }

@media(max-width:768px) {
  .chat-popup { width: calc(100vw - 24px); right: 12px; bottom: 120px; height: 65vh; }
  .chat-fab { bottom: 70px; right: 12px; }
}
</style>

<div class="chatbot-overlay">
  <button class="chat-fab" id="chatFab" onclick="toggleChat()">
    <img class="icon-open" src="/static/logo.png" alt="ORION" style="width: 50px; height: 50px; object-fit: contain; border-radius: 50%">
  </button>

  <div class="chat-popup" id="chatPopup">
    <div class="chat-header">
      <div class="chat-avatar">
       <img src="/static/logo.png" alt="ORION" style="width: 32px; height: 32px; object-fit: contain; border-radius: 50%">
      </div>
      <div class="chat-agent-info">
        <h3>Assistant ORION</h3>
        <p>IA· En ligne 24/7</p>
      </div>
      <div class="chat-header-actions">
        <button class="chat-action-btn" onclick="clearChat()">🗑</button>
        <button class="chat-action-btn" onclick="toggleChat()">✕</button>
      </div>
    </div>

    <div id="pipeline-bar">
      <span class="p-step" id="p-read">📖 Lecture</span>
      <span class="p-arrow">→</span>
      <span class="p-step" id="p-analyse">🔬 Analyse</span>
      <span class="p-arrow">→</span>
      <span class="p-step" id="p-respond">💬 Réponse</span>
    </div>

    <div id="chat-messages">
      <div class="chat-welcome">
        <div class="w-icon">🧑‍💻</div>
        <h4>Bonjour ! Comment puis-je vous aider ?</h4>
        <p>Posez vos questions sur vos cours, notes, paiements ou l'université</p>
        <div class="suggestions">
          <span class="suggestion-chip" onclick="quickSend('Quels sont mes cours aujourd\\'hui ?')">📚 Mes cours</span>
          <span class="suggestion-chip" onclick="quickSend('Comment voir mes notes ?')">📊 Mes notes</span>
          <span class="suggestion-chip" onclick="quickSend('Quand dois-je payer mes frais ?')">💰 Frais de scolarité</span>
          <span class="suggestion-chip" onclick="quickSend('Comment contacter l\\'administration ?')">📞 Administration</span>
        </div>
      </div>
    </div>

    <div class="chat-input-area">
      <div id="chat-file-chips"></div>
      <div class="chat-input-row">
        <input type="file" id="chat-file-input" multiple accept=".pdf,.txt,.png,.jpg">
        <button class="chat-btn" id="chat-attach" onclick="document.getElementById('chat-file-input').click()">📎</button>
        <textarea id="chat-input" placeholder="Votre message…" rows="1"></textarea>
        <button class="chat-btn" id="chat-send" onclick="runPipeline()">➤</button>
      </div>
      <p class="chat-hint">Entrée = envoyer · Maj+Entrée = saut de ligne</p>
    </div>
  </div>
</div>

<script>
const GEMINI_API_KEY = 'AIzaSyAppxsI77c_BUIg3BV6xgWVmcPxh4WcfDg';  // Remplacez par votre vraie clé Gemini

let chatHistory = [];
let pendingFiles = [];
let isRunning = false;

function toggleChat() {
  const popup = document.getElementById('chatPopup');
  const fab = document.getElementById('chatFab');
  popup.classList.toggle('open');
  fab.classList.toggle('open');
  if (popup.classList.contains('open')) {
    document.getElementById('chat-input').focus();
  }
}

function clearChat() {
  chatHistory = [];
  const msgs = document.getElementById('chat-messages');
  msgs.innerHTML = `<div class="chat-welcome">
    <div class="w-icon">🧑‍💻</div>
    <h4>Conversation réinitialisée</h4>
    <p>Posez vos questions sur vos cours, notes, paiements ou l'université</p>
    <div class="suggestions">
      <span class="suggestion-chip" onclick="quickSend('Quels sont mes cours aujourd\\'hui ?')">📚 Mes cours</span>
      <span class="suggestion-chip" onclick="quickSend('Comment voir mes notes ?')">📊 Mes notes</span>
      <span class="suggestion-chip" onclick="quickSend('Quand dois-je payer mes frais ?')">💰 Frais de scolarité</span>
    </div>
  </div>`;
}

function quickSend(text) {
  document.getElementById('chat-input').value = text;
  runPipeline();
}

document.getElementById('chat-file-input').addEventListener('change', async function(e) {
  for (const file of Array.from(e.target.files)) {
    const reader = new FileReader();
    reader.onload = function(ev) {
      pendingFiles.push({ name: file.name, content: ev.target.result.split(',')[1] || ev.target.result, type: file.type });
      const chips = document.getElementById('chat-file-chips');
      const chip = document.createElement('div');
      chip.className = 'c-chip';
      chip.innerHTML = `📄 ${file.name} <button onclick="this.parentElement.remove()">×</button>`;
      chips.appendChild(chip);
    };
    reader.readAsDataURL(file);
  }
});

async function callGemini(userMessage) {
  try {
    // Construction correcte du payload pour l'API Gemini
    const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=${GEMINI_API_KEY}`, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        contents: [{
          parts: [{
            text: userMessage
          }],
          role: "user"
        }],
        generationConfig: {
          temperature: 0.7,
          maxOutputTokens: 800,
          topP: 0.9,
          topK: 40
        },
        safetySettings: [
          {
            category: "HARM_CATEGORY_HARASSMENT",
            threshold: "BLOCK_MEDIUM_AND_ABOVE"
          },
          {
            category: "HARM_CATEGORY_HATE_SPEECH",
            threshold: "BLOCK_MEDIUM_AND_ABOVE"
          }
        ]
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('API Error Status:', response.status, errorText);
      
      if (response.status === 403) {
        return "❌ Clé API invalide ou non activée. Vérifiez votre clé sur https://aistudio.google.com/apikey";
      } else if (response.status === 429) {
        return "⏳ Trop de requêtes. Attendez quelques secondes avant de réessayer.";
      } else if (response.status === 400) {
        return "⚠️ Requête invalide. Vérifiez le format du message.";
      }
      
      return "Désolé, le service IA est temporairement indisponible. Veuillez réessayer plus tard.";
    }

    const data = await response.json();
    
    // Extraction correcte de la réponse
    if (data.candidates && data.candidates[0] && data.candidates[0].content && data.candidates[0].content.parts) {
      return data.candidates[0].content.parts[0].text;
    } else if (data.error) {
      console.error('API Error:', data.error);
      return `Erreur API: ${data.error.message || 'Erreur inconnue'}`;
    } else {
      console.error('Unexpected response:', data);
      return "Je n'ai pas pu traiter votre demande. Format de réponse inattendu.";
    }
  } catch (error) {
    console.error('Erreur réseau:', error);
    return "❌ Erreur de connexion. Vérifiez votre connexion internet et réessayez.";
  }
}

function addMessage(text, sender) {
  const msgs = document.getElementById('chat-messages');
  const welcome = msgs.querySelector('.chat-welcome');
  if (welcome && sender === 'user') welcome.remove();

  const div = document.createElement('div');
  div.className = `chat-msg ${sender}`;
  div.innerHTML = `<div class="chat-bubble-wrap"><div class="chat-bubble">${text.replace(/\\n/g, '<br>')}</div><div class="chat-msg-meta">${sender === 'user' ? 'Vous' : 'Assistant ORION'}</div></div>`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function addTypingIndicator() {
  const msgs = document.getElementById('chat-messages');
  const id = 'typing-' + Date.now();
  const div = document.createElement('div');
  div.id = id;
  div.className = 'chat-msg bot';
  div.innerHTML = `<div class="chat-bubble-wrap"><div class="chat-bubble"><div class="typing-indicator"><span></span><span></span><span></span></div></div></div>`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return id;
}

function removeTypingIndicator(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

async function runPipeline() {
  if (isRunning) return;
  const input = document.getElementById('chat-input');
  const userText = input.value.trim();
  if (!userText && pendingFiles.length === 0) return;

  isRunning = true;
  document.getElementById('chat-send').disabled = true;

  addMessage(userText || 'Fichier joint', 'user');
  input.value = '';
  const typingId = addTypingIndicator();

  const userRole = window.userContext || { role: "étudiant" };
  
  // Construction du prompt système
  const systemPrompt = `Tu es l'assistant virtuel d'ORION University Madagascar.
  
RÈGLES IMPORTANTES :
- Réponds TOUJOURS en français
- Sois professionnel, amical et utile
- Utilise des émojis adaptés pour rendre les réponses plus agréables
- Si tu ne sais pas, dis-le honnêtement

CONTEXTE DE L'UNIVERSITÉ :
- Nom: ORION University Madagascar
- Filières: Informatique, Finance, Marketing
- L'utilisateur est un(e) ${userRole.role}
${userRole.filiere ? `- Filière de l'utilisateur: ${userRole.filiere}` : ''}
${userRole.niveau ? `- Niveau: ${userRole.niveau}` : ''}

SERVICES DISPONIBLES :
- Consultation des notes et moyennes
- Emploi du temps
- Paiements des frais de scolarité
- Bibliothèque numérique
- Demandes administratives

L'utilisateur demande: ${userText}${pendingFiles.length ? ` (Fichiers joints: ${pendingFiles.map(f=>f.name).join(', ')})` : ''}

RÉPONDRE EN FRANÇAIS, de manière claire et structurée.`;

  try {
    const response = await callGemini(systemPrompt);
    removeTypingIndicator(typingId);
    
    if (response && response.length > 0) {
      addMessage(response, 'bot');
    } else {
      addMessage("Je n'ai pas pu générer une réponse. Veuillez reformuler votre question.", 'bot');
    }
  } catch (error) {
    removeTypingIndicator(typingId);
    console.error('Pipeline error:', error);
    addMessage("❌ Désolé, une erreur s'est produite. Veuillez réessayer dans quelques instants.", 'bot');
  }

  isRunning = false;
  document.getElementById('chat-send').disabled = false;
  pendingFiles = [];
  document.getElementById('chat-file-chips').innerHTML = '';
}

const textarea = document.getElementById('chat-input');
textarea.addEventListener('input', function() { this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 110) + 'px'; });
textarea.addEventListener('keydown', function(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); runPipeline(); } });
</script>
'''

print("✅ ORION University v5 — Tous les templates chargés")
init_db()
print("="*60)
print("🎓 ORION University — Application démarrée (v5)")
print(f"📍 DB: {DB_PATH}")
print("🔐 Admin: admin / orion2024")
print("🔐 Enseignants: informatique/info2024 | communication/comm2024 | finance/fin2024")
print("="*60) enleve le code du chat et met en fichier html complet
