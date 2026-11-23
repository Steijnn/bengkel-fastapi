import sqlite3, hashlib, os, shutil
BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "bengkel.db")
if os.environ.get("VERCEL"):  # folder project read-only di Vercel, salin DB ke /tmp
    _tmp = "/tmp/bengkel.db"
    if not os.path.exists(_tmp) and os.path.exists(DB): shutil.copy(DB, _tmp)
    DB = _tmp
def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def q(sql, p=()):
    c = conn()
    try: return [dict(x) for x in c.execute(sql, p).fetchall()]
    finally: c.close()
def ex(sql, p=()):
    c = conn()
    try: cur = c.execute(sql, p); c.commit(); return cur.lastrowid
    finally: c.close()
def one(sql, p=()): return list(q(sql, p)[0].values())[0]

SCHEMA = """
CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT,nama TEXT,username TEXT UNIQUE,password TEXT,role TEXT DEFAULT 'customer' CHECK(role IN('admin','kasir','mekanik','customer')));
CREATE TABLE customer(id INTEGER PRIMARY KEY AUTOINCREMENT,nama TEXT,telp TEXT,plat TEXT,tipe TEXT);
CREATE TABLE sparepart(id INTEGER PRIMARY KEY AUTOINCREMENT,kode TEXT UNIQUE,nama TEXT,stok INTEGER DEFAULT 0,harga_beli INTEGER,harga_jual INTEGER);
CREATE TABLE jasa(id INTEGER PRIMARY KEY AUTOINCREMENT,nama TEXT,harga INTEGER);
CREATE TABLE booking(id INTEGER PRIMARY KEY AUTOINCREMENT,kode TEXT,tgl TEXT,plat TEXT,jasa_id INTEGER REFERENCES jasa(id),mekanik_id INTEGER REFERENCES users(id),status TEXT DEFAULT 'Pending' CHECK(status IN('Pending','Diproses','Selesai')));
CREATE TABLE transaksi(id INTEGER PRIMARY KEY AUTOINCREMENT,nota TEXT,tgl TEXT,customer_id INTEGER REFERENCES customer(id),mekanik_id INTEGER REFERENCES users(id),total INTEGER,metode TEXT);
CREATE TABLE detail(id INTEGER PRIMARY KEY AUTOINCREMENT,transaksi_id INTEGER REFERENCES transaksi(id) ON DELETE CASCADE,jenis TEXT,item_id INTEGER,nama TEXT,qty INTEGER,harga INTEGER,subtotal INTEGER);
"""
def init():
    if os.path.exists(DB): return
    c = conn(); c.executescript(SCHEMA)
    c.executemany("INSERT INTO users(nama,username,password,role) VALUES(?,?,?,?)",
        [("Administrator","admin",sha("admin123"),"admin"),("Kasir Satu","kasir",sha("kasir123"),"kasir"),("Budi Mekanik","budi",sha("budi123"),"mekanik")])
    c.executemany("INSERT INTO jasa(nama,harga) VALUES(?,?)",[("Cuci Motor",20000),("Cuci Mobil",50000),("Ganti Oli",35000),("Tune Up",150000),("Servis Rutin",100000)])
    c.executemany("INSERT INTO sparepart(kode,nama,stok,harga_beli,harga_jual) VALUES(?,?,?,?,?)",[("SP001","Oli Mesin 1L",50,40000,55000),("SP002","Kampas Rem Depan",20,45000,70000),("SP003","Busi",30,15000,25000),("SP004","Filter Udara",15,30000,50000)])
    c.executemany("INSERT INTO customer(nama,telp,plat,tipe) VALUES(?,?,?,?)",[("Andi","08123456789","B 1234 ABC","Honda Beat"),("Sari","08129876543","B 5678 XYZ","Toyota Avanza")])
    c.commit(); c.close()

def report(r, plat="", m="harian"):
    if r == "1":
        return "Penjualan Sparepart (Fast-moving)", q('''SELECT nama AS "Sparepart",SUM(qty) AS "Terjual",SUM(subtotal) AS "Omzet" FROM detail WHERE jenis='sparepart' GROUP BY item_id,nama ORDER BY 2 DESC''')
    if r == "2":
        rows = q('''SELECT t.tgl AS "Tanggal",t.nota AS "Nota",d.nama AS "Item",d.qty AS "Qty",d.subtotal AS "Subtotal" FROM detail d JOIN transaksi t ON t.id=d.transaksi_id JOIN customer c ON c.id=t.customer_id WHERE c.plat=? ORDER BY t.tgl DESC''', (plat,)) if plat else []
        return "Riwayat Servis Kendaraan", rows
    if r == "3":
        f = "%Y-%m" if m == "bulanan" else "%Y-%m-%d"
        return "Pendapatan " + m.title(), q('''SELECT strftime(?,t.tgl) AS "Periode",COALESCE(SUM(CASE WHEN d.jenis='sparepart' THEN d.subtotal END),0) AS "Sparepart",COALESCE(SUM(CASE WHEN d.jenis='jasa' THEN d.subtotal END),0) AS "Jasa",SUM(d.subtotal) AS "Total" FROM transaksi t JOIN detail d ON d.transaksi_id=t.id GROUP BY 1 ORDER BY 1 DESC''', (f,))
    return "Booking Selesai & Layanan per Mekanik", q('''SELECT u.nama AS "Mekanik",j.nama AS "Layanan",COUNT(*) AS "Booking Selesai" FROM booking b JOIN users u ON u.id=b.mekanik_id JOIN jasa j ON j.id=b.jasa_id WHERE b.status='Selesai' GROUP BY u.id,j.id ORDER BY u.nama''')
