import re
import os
import json
import time
from xml.sax.saxutils import escape

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
from pypdf import PdfReader

# Import untuk pembuatan PDF
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

app = Flask(__name__)
app.secret_key = 'kunci_rahasia_kamu_yang_sangat_aman'

# SISTEM PENYIMPANAN DATA PERMANEN
DB_FILE = "database_user.txt"
HISTORY_FILE = "database_riwayat.txt"


def muat_data_user():
    if not os.path.exists(DB_FILE):
        data_awal = {"admin": "admin123"}
        simpan_data_user(data_awal)
        return data_awal

    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"admin": "admin123"}


def simpan_data_user(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f)


def muat_riwayat():
    if not os.path.exists(HISTORY_FILE):
        return []

    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def simpan_riwayat(riwayat_list):
    with open(HISTORY_FILE, "w") as f:
        json.dump(riwayat_list, f)


# Stemmer Sastrawi
factory = StemmerFactory()
stemmer = factory.create_stemmer()


def preprocess_text(text):
    if not text:
        return ""

    text = text.lower()

    # Ambil hanya karakter alphanumeric
    words = re.findall(r'[a-z0-9]+', text)
    stemmed_words = []

    for word in words:
        # Jika bukan angka dan panjang > 2, lakukan stemming
        if len(word) > 2 and not word.isdigit():
            stemmed_words.append(stemmer.stem(word))
        else:
            stemmed_words.append(word)

    # Gabungkan dengan SPASI
    return " ".join(stemmed_words)


def make_kgrams(text, k):
    kgrams = []

    for i in range(len(text) - k + 1):
        kgrams.append(text[i:i+k])

    return kgrams


def hash_kgrams(kgrams):
    hashes = []

    for gram in kgrams:
        h = 0

        for char in gram:
            h = (h * 31 + ord(char)) % 2**32

        hashes.append(h)

    return hashes


def winnowing(hashes, w):
    fingerprints = set()

    window_count = len(hashes) - w + 1

    if window_count <= 0:
        if hashes:
            fingerprints.add(min(hashes))

        return fingerprints

    for i in range(window_count):
        window = hashes[i:i+w]
        min_val = min(window)
        fingerprints.add(min_val)

    return fingerprints


def calculate_similarity(fp1, fp2):
    if not fp1 or not fp2:
        return 0.0

    intersection = fp1.intersection(fp2)
    union = fp1.union(fp2)

    return (len(intersection) / len(union)) * 100


# =========================================================
# TAMBAHAN:
# MENCARI KALIMAT YANG TERINDIKASI PLAGIARISME
# =========================================================

def split_sentences(text):
    """
    Memecah dokumen menjadi beberapa kalimat.
    Pemisah menggunakan tanda titik, tanda tanya,
    tanda seru, dan pergantian baris.
    """

    if not text:
        return []

    # Memecah berdasarkan tanda akhir kalimat
    # atau pergantian baris
    sentences = re.split(
        r'(?<=[.!?])\s+|\n+',
        text
    )

    hasil = []

    for sentence in sentences:

        sentence = sentence.strip()

        # Hanya mengambil kalimat yang cukup panjang
        # agar tidak mengambil judul atau potongan teks pendek
        if len(sentence) >= 20:
            hasil.append(sentence)

    return hasil


def cari_kalimat_plagiat(text1, text2, threshold=50):
    """
    Membandingkan setiap kalimat dari dokumen asli
    dengan seluruh kalimat pada dokumen uji.

    Metode yang digunakan tetap:
    Preprocessing -> K-Gram -> Hash -> Winnowing
    -> Jaccard Similarity
    """

    kalimat_asli = split_sentences(text1)
    kalimat_uji = split_sentences(text2)

    hasil = []

    # Periksa setiap kalimat dari dokumen asli
    for nomor, kalimat1 in enumerate(kalimat_asli, start=1):

        teks1 = preprocess_text(kalimat1)

        if not teks1:
            continue

        # Fingerprint kalimat dokumen asli
        fp1 = winnowing(
            hash_kgrams(
                make_kgrams(teks1, 15)
            ),
            12
        )

        skor_tertinggi = 0
        kalimat_terdekat = ""

        # Bandingkan dengan semua kalimat dokumen uji
        for kalimat2 in kalimat_uji:

            teks2 = preprocess_text(kalimat2)

            if not teks2:
                continue

            # Fingerprint kalimat dokumen uji
            fp2 = winnowing(
                hash_kgrams(
                    make_kgrams(teks2, 15)
                ),
                12
            )

            # Hitung kemiripan
            skor = calculate_similarity(
                fp1,
                fp2
            )

            # Simpan pasangan dengan skor tertinggi
            if skor > skor_tertinggi:
                skor_tertinggi = skor
                kalimat_terdekat = kalimat2

        # Hanya mengambil kalimat dengan kemiripan
        # lebih dari 50%, sesuai dengan status utama sistem
        if skor_tertinggi > threshold and kalimat_terdekat:

            hasil.append({
                "nomor": nomor,
                "kalimat_asli": kalimat1,
                "kalimat_uji": kalimat_terdekat,
                "skor": round(skor_tertinggi, 2)
            })

    return hasil


def extract_text_from_file(file):
    if not file or file.filename == '':
        return ""

    filename = file.filename.lower()

    if filename.endswith('.txt'):

        return file.read().decode(
            'utf-8',
            errors='ignore'
        )

    elif filename.endswith('.pdf'):

        try:
            reader = PdfReader(file)
            text = ""

            for page in reader.pages:

                page_text = page.extract_text()

                if page_text:
                    text += page_text + " "

            return text

        except Exception as e:
            return ""

    return ""


# =========================================================
# ROUTING REGISTER & LOGIN
# =========================================================

@app.route('/register', methods=['GET', 'POST'])
def register():

    if 'logged_in' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':

        username = request.form.get(
            'username',
            ''
        ).strip()

        password = request.form.get(
            'password',
            ''
        )

        confirm_password = request.form.get(
            'confirm_password',
            ''
        )

        if not username or not password:

            flash(
                'Username dan Password tidak boleh kosong!',
                'danger'
            )

            return render_template(
                'register.html'
            )

        user_data = muat_data_user()

        if username in user_data:

            flash(
                'Username sudah terdaftar!',
                'danger'
            )

            return render_template(
                'register.html'
            )

        if password != confirm_password:

            flash(
                'Konfirmasi password tidak cocok!',
                'danger'
            )

            return render_template(
                'register.html'
            )

        user_data[username] = password

        simpan_data_user(user_data)

        flash(
            'Registrasi berhasil! Silakan masuk.',
            'success'
        )

        return redirect(
            url_for('login')
        )

    return render_template(
        'register.html'
    )


@app.route('/login', methods=['GET', 'POST'])
def login():

    if 'logged_in' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':

        username = request.form.get(
            'username',
            ''
        ).strip()

        password = request.form.get(
            'password',
            ''
        )

        user_data = muat_data_user()

        if (
            username in user_data
            and user_data[username] == password
        ):

            session['logged_in'] = True
            session['username'] = username

            flash(
                'Login Berhasil!',
                'success'
            )

            return redirect(
                url_for('index')
            )

        else:

            flash(
                'Username atau Password salah!',
                'danger'
            )

    return render_template(
        'login.html'
    )


@app.route('/logout')
def logout():

    session.pop(
        'logged_in',
        None
    )

    session.pop(
        'username',
        None
    )

    flash(
        'Anda telah keluar sistem.',
        'info'
    )

    return redirect(
        url_for('login')
    )


# =========================================================
# ROUTING DASHBOARD UTAMA (HOME)
# =========================================================

@app.route('/', methods=['GET', 'POST'])
def index():

    if 'logged_in' not in session:
        return redirect(
            url_for('login')
        )

    if request.method == 'POST':

        input_type = request.form.get(
            'input_type',
            'text'
        )

        # 1. PENANGANAN INPUT TEKS MANUAL LANGSUNG

        if input_type == 'text':

            nama_dok1 = request.form.get(
                'nama_dok1',
                ''
            ).strip() or "Teks Manual Asli"

            nama_dok2 = request.form.get(
                'nama_dok2',
                ''
            ).strip() or "Teks Manual Uji"

            doc1 = request.form.get(
                'doc1',
                ''
            )

            doc2 = request.form.get(
                'doc2',
                ''
            )

            if doc1.strip() and doc2.strip():

                proses_dan_simpan_riwayat(
                    doc1,
                    doc2,
                    nama_dok1,
                    nama_dok2
                )

                return redirect(
                    url_for('riwayat')
                )

            else:

                flash(
                    'Mohon isi kedua kolom teks manual terlebih dahulu.',
                    'danger'
                )


        elif input_type == 'file':

            files = request.files.getlist(
                'multiple_files'
            )

            valid_multiple_files = [
                f for f in files
                if f and f.filename != ''
            ]

            if valid_multiple_files:

                if len(valid_multiple_files) < 2:

                    flash(
                        'Mohon unggah minimal 2 file untuk perbandingan silang massal.',
                        'danger'
                    )

                    return render_template(
                        'index.html'
                    )

                # Langkah 1:
                # Ekstrak semua teks dokumen massal

                daftar_dokumen = []

                for file in valid_multiple_files:

                    nama_file = file.filename

                    teks_ekstraksi = extract_text_from_file(
                        file
                    )

                    if teks_ekstraksi.strip():

                        daftar_dokumen.append({
                            "nama": nama_file,
                            "isi": teks_ekstraksi
                        })

                if len(daftar_dokumen) < 2:

                    flash(
                        'Gagal memproses dokumen massal. Pastikan file tidak kosong atau rusak.',
                        'danger'
                    )

                    return render_template(
                        'index.html'
                    )

                # Langkah 2:
                # Perbandingan bersarang All-to-All

                jumlah_dokumen = len(
                    daftar_dokumen
                )

                proses_berhasil = False

                for i in range(
                    jumlah_dokumen
                ):

                    for j in range(
                        i + 1,
                        jumlah_dokumen
                    ):

                        dok_asli = daftar_dokumen[i]
                        dok_uji = daftar_dokumen[j]

                        proses_dan_simpan_riwayat(
                            dok_asli["isi"],
                            dok_uji["isi"],
                            dok_asli["nama"],
                            dok_uji["nama"]
                        )

                        proses_berhasil = True

                if proses_berhasil:

                    flash(
                        'Pengecekan silang antar seluruh dokumen selesai! Silakan cek hasil di riwayat.',
                        'success'
                    )

                    return redirect(
                        url_for('riwayat')
                    )


            # Jika multiple_files kosong,
            # beralih ke Metode A

            else:

                file1 = request.files.get(
                    'file1'
                )

                file2 = request.files.get(
                    'file2'
                )

                if (
                    file1
                    and file1.filename != ''
                ) and (
                    file2
                    and file2.filename != ''
                ):

                    nama_dok1 = file1.filename
                    nama_dok2 = file2.filename

                    doc1 = extract_text_from_file(
                        file1
                    )

                    doc2 = extract_text_from_file(
                        file2
                    )

                    if doc1.strip() and doc2.strip():

                        proses_dan_simpan_riwayat(
                            doc1,
                            doc2,
                            nama_dok1,
                            nama_dok2
                        )

                        flash(
                            'Perbandingan dua dokumen berhasil dilakukan!',
                            'success'
                        )

                        return redirect(
                            url_for('riwayat')
                        )

                    else:

                        flash(
                            'Gagal mengekstrak teks. Mohon periksa kembali file Anda.',
                            'danger'
                        )

                else:

                    flash(
                        'Mohon isi kedua dokumen terlebih dahulu pada Metode A atau gunakan Metode B.',
                        'danger'
                    )

    return render_template(
        'index.html'
    )


# =========================================================
# FUNGSI BANTUAN UNTUK MEMPERSINGKAT PROSES HITUNG
# & SIMPAN DATABASE
# =========================================================

def proses_dan_simpan_riwayat(
    doc1,
    doc2,
    nama_dok1,
    nama_dok2
):

    k, w = 15, 12

    fp1 = winnowing(
        hash_kgrams(
            make_kgrams(
                preprocess_text(doc1),
                k
            )
        ),
        w
    )

    fp2 = winnowing(
        hash_kgrams(
            make_kgrams(
                preprocess_text(doc2),
                k
            )
        ),
        w
    )

    similarity = round(
        calculate_similarity(
            fp1,
            fp2
        ),
        2
    )

    status_plagiarisme = (
        "Tinggi (Plagiat)"
        if similarity > 50
        else "Aman"
    )

    riwayat_list = muat_riwayat()

    data_baru = {

        "id": str(
            int(time.time())
        ),

        "username": session[
            'username'
        ],

        "waktu": time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "dokumen_asli":
            nama_dok1[:30] + "..."
            if len(nama_dok1) > 30
            else nama_dok1,

        "dokumen_uji":
            nama_dok2[:30] + "..."
            if len(nama_dok2) > 30
            else nama_dok2,

        "isi_asli": doc1,

        "isi_uji": doc2,

        "skor": similarity,

        "status": status_plagiarisme
    }

    riwayat_list.insert(
        0,
        data_baru
    )

    simpan_riwayat(
        riwayat_list
    )


# =========================================================
# ROUTING HALAMAN RIWAYAT
# =========================================================

@app.route('/riwayat')
def riwayat():

    if 'logged_in' not in session:
        return redirect(
            url_for('login')
        )

    semua_riwayat = muat_riwayat()

    # Filter riwayat berdasarkan user
    riwayat_user = [
        r for r in semua_riwayat
        if r['username'] == session['username']
    ]

    # Mengurutkan dari skor tertinggi ke terendah
    riwayat_user = sorted(
        riwayat_user,
        key=lambda x: x['skor'],
        reverse=True
    )

    return render_template(
        'riwayat.html',
        riwayat=riwayat_user
    )


# =========================================================
# ROUTING HALAMAN ABOUT
# =========================================================

@app.route('/about')
def about():

    if 'logged_in' not in session:
        return redirect(
            url_for('login')
        )

    return render_template(
        'about.html'
    )


# =========================================================
# ROUTING DOWNLOAD PDF
# =========================================================

@app.route('/download-pdf')
def download_pdf():

    if 'logged_in' not in session:
        return redirect(
            url_for('login')
        )

    id_riwayat = request.args.get(
        'id'
    )

    semua_riwayat = muat_riwayat()

    item = next(
        (
            r for r in semua_riwayat
            if r['id'] == id_riwayat
            and r['username'] == session['username']
        ),
        None
    )

    if not item:

        flash(
            "Data tidak ditemukan!",
            "danger"
        )

        return redirect(
            url_for('riwayat')
        )


    # =====================================================
    # TAMBAHAN:
    # CARI KALIMAT YANG MENGANDUNG INDIKASI PLAGIARISME
    # =====================================================

    kalimat_plagiat = cari_kalimat_plagiat(
        item["isi_asli"],
        item["isi_uji"],
        threshold=50
    )


    # =====================================================
    # PEMBUATAN PDF
    # =====================================================

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    story = []

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=22,
        textColor=colors.HexColor('#4f46e5'),
        spaceAfter=15
    )

    normal_style = styles['Normal']

    bold_style = ParagraphStyle(
        'BoldStyle',
        parent=normal_style,
        fontName='Helvetica-Bold'
    )


    # =====================================================
    # JUDUL LAPORAN
    # =====================================================

    story.append(
        Paragraph(
            "LAPORAN HASIL CEK PLAGIARISME",
            title_style
        )
    )

    story.append(
        Paragraph(
            f"Diunduh oleh: {item['username']} | "
            f"Waktu Analisis: {item['waktu']}",
            normal_style
        )
    )

    story.append(
        Spacer(1, 15)
    )


    # =====================================================
    # TABEL HASIL ANALISIS
    # =====================================================

    data_tabel = [

        [
            Paragraph(
                "Parameter Berkas",
                bold_style
            ),

            Paragraph(
                "Keterangan Analisis",
                bold_style
            )
        ],

        [
            "Dokumen Kontrol (Asli)",
            item['dokumen_asli']
        ],

        [
            "Dokumen Pembanding (Uji)",
            item['dokumen_uji']
        ],

        [
            "Hasil Skor Kemiripan",
            f"{item['skor']}%"
        ],

        [
            "Status Dokumen",
            item['status']
        ]
    ]


    t = Table(
        data_tabel,
        colWidths=[180, 320]
    )


    t.setStyle(
        TableStyle([

            (
                'BACKGROUND',
                (0, 0),
                (1, 0),
                colors.HexColor('#4f46e5')
            ),

            (
                'TEXTCOLOR',
                (0, 0),
                (1, 0),
                colors.whitesmoke
            ),

            (
                'ALIGN',
                (0, 0),
                (-1, -1),
                'LEFT'
            ),

            (
                'BOTTOMPADDING',
                (0, 0),
                (-1, -1),
                8
            ),

            (
                'TOPPADDING',
                (0, 0),
                (-1, -1),
                8
            ),

            (
                'ROWBACKGROUNDS',
                (0, 1),
                (-1, -1),
                [
                    colors.HexColor('#f8fafc'),
                    colors.white
                ]
            ),

            (
                'GRID',
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor('#cbd5e1')
            )
        ])
    )


    story.append(t)


    # =====================================================
    # TAMBAHAN:
    # DETAIL KALIMAT YANG TERINDIKASI PLAGIARISME
    # =====================================================

    story.append(
        Spacer(1, 25)
    )


    detail_title_style = ParagraphStyle(
        'DetailTitleStyle',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=colors.HexColor('#4f46e5'),
        spaceAfter=12
    )


    story.append(
        Paragraph(
            "DETAIL KALIMAT YANG TERINDIKASI PLAGIARISME",
            detail_title_style
        )
    )


    story.append(
        Paragraph(
            "Bagian berikut menampilkan kalimat dari "
            "dokumen asli yang memiliki kemiripan lebih "
            "dari 50% dengan kalimat pada dokumen uji.",
            normal_style
        )
    )


    story.append(
        Spacer(1, 12)
    )


    # =====================================================
    # JIKA DITEMUKAN KALIMAT PLAGIARISME
    # =====================================================

    if kalimat_plagiat:

        for i, detail in enumerate(
            kalimat_plagiat,
            start=1
        ):

            # ---------------------------------------------
            # JUDUL KALIMAT
            # ---------------------------------------------

            kalimat_title = Paragraph(
                f"<b>Kalimat {i} "
                f"- Kemiripan {detail['skor']}%</b>",
                normal_style
            )

            story.append(
                kalimat_title
            )

            story.append(
                Spacer(1, 6)
            )


            # ---------------------------------------------
            # Membersihkan karakter khusus HTML/XML
            # agar aman dimasukkan ke Paragraph ReportLab
            # ---------------------------------------------

            kalimat_asli = escape(
                detail["kalimat_asli"]
            )

            kalimat_uji = escape(
                detail["kalimat_uji"]
            )


            # ---------------------------------------------
            # TABEL PERBANDINGAN KALIMAT
            # ---------------------------------------------

            detail_data = [

                [
                    Paragraph(
                        "<b>Dokumen Asli</b>",
                        bold_style
                    ),

                    Paragraph(
                        "<b>Dokumen Uji</b>",
                        bold_style
                    )
                ],

                [
                    Paragraph(
                        kalimat_asli,
                        normal_style
                    ),

                    Paragraph(
                        kalimat_uji,
                        normal_style
                    )
                ]
            ]


            detail_table = Table(
                detail_data,
                colWidths=[250, 250]
            )


            detail_table.setStyle(
                TableStyle([

                    # Header kiri
                    (
                        'BACKGROUND',
                        (0, 0),
                        (0, 0),
                        colors.HexColor('#4f46e5')
                    ),

                    # Header kanan
                    (
                        'BACKGROUND',
                        (1, 0),
                        (1, 0),
                        colors.HexColor('#4f46e5')
                    ),

                    # Warna tulisan header
                    (
                        'TEXTCOLOR',
                        (0, 0),
                        (-1, 0),
                        colors.white
                    ),

                    # Posisi teks
                    (
                        'VALIGN',
                        (0, 0),
                        (-1, -1),
                        'TOP'
                    ),

                    # Garis tabel
                    (
                        'GRID',
                        (0, 0),
                        (-1, -1),
                        0.5,
                        colors.HexColor('#cbd5e1')
                    ),

                    # Background isi
                    (
                        'BACKGROUND',
                        (0, 1),
                        (-1, 1),
                        colors.HexColor('#f8fafc')
                    ),

                    # Padding kiri
                    (
                        'LEFTPADDING',
                        (0, 0),
                        (-1, -1),
                        8
                    ),

                    # Padding kanan
                    (
                        'RIGHTPADDING',
                        (0, 0),
                        (-1, -1),
                        8
                    ),

                    # Padding atas
                    (
                        'TOPPADDING',
                        (0, 0),
                        (-1, -1),
                        8
                    ),

                    # Padding bawah
                    (
                        'BOTTOMPADDING',
                        (0, 0),
                        (-1, -1),
                        8
                    )
                ])
            )


            story.append(
                detail_table
            )

            story.append(
                Spacer(1, 15)
            )


    # =====================================================
    # JIKA TIDAK DITEMUKAN KALIMAT PLAGIARISME
    # =====================================================

    else:

        aman_style = ParagraphStyle(
            'AmanStyle',
            parent=normal_style,
            textColor=colors.HexColor('#16a34a')
        )

        story.append(
            Paragraph(
                "Tidak ditemukan kalimat yang memiliki "
                "kemiripan lebih dari 50%.",
                aman_style
            )
        )


    # =====================================================
    # MEMBUAT PDF
    # =====================================================

    doc.build(
        story
    )

    buffer.seek(0)


    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Laporan_Plagiarisme_{item['id']}.pdf",
        mimetype='application/pdf'
    )


# =========================================================
# DAFTAR USER
# =========================================================

@app.route('/daftar-user')
def lihat_user():
    return muat_data_user()


# =========================================================
# RUNNING APPLICATION
# =========================================================

if __name__ == '__main__':
    app.run(debug=True)