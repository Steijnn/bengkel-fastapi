import sqlite3, os
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from db import q, ex, one, sha, conn, init, report

init()
app = FastAPI(title="Bengkel")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "ganti-dengan-rahasia-anda"))
T = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
def page(request, name, **ctx): return T.TemplateResponse(request, name, ctx)

class Redir(Exception): pass
@app.exception_handler(Redir)
async def _redir(request, exc): return RedirectResponse("/", 302)
def auth(request: Request):
    if not request.session.get("u"): raise Redir()
    return request.session["u"]

# ---------- AUTH ----------
@app.get("/")
def login_page(request: Request, err: str = ""): return page(request, "auth.html", mode="login", err=err)
@app.post("/login")
async def login(request: Request):
    f = await request.form()
    r = q("SELECT * FROM users WHERE username=? AND password=?", (f["username"], sha(f["password"])))
    if not r: return RedirectResponse("/?err=Username/password salah", 303)
    request.session["u"] = {"id": r[0]["id"], "nama": r[0]["nama"], "role": r[0]["role"]}
    return RedirectResponse("/dashboard", 303)
@app.get("/register")
def reg_page(request: Request, err: str = ""): return page(request, "auth.html", mode="register", err=err)
@app.post("/register")
async def register(request: Request):
    f = await request.form()
    try: ex("INSERT INTO users(nama,username,password,role) VALUES(?,?,?,'customer')", (f["nama"], f["username"], sha(f["password"])))
    except sqlite3.IntegrityError: return RedirectResponse("/register?err=Username sudah dipakai", 303)
    return RedirectResponse("/", 303)
@app.get("/logout")
def logout(request: Request): request.session.clear(); return RedirectResponse("/", 302)

# ---------- DASHBOARD ----------
@app.get("/dashboard")
def dashboard(request: Request, u=Depends(auth)):
    now = datetime.now()
    today = now.strftime("%Y-%m-%d") + "%"
    cards = {"Customer": one("SELECT COUNT(*) FROM customer"), "Sparepart": one("SELECT COUNT(*) FROM sparepart"),
             "Booking Pending": one("SELECT COUNT(*) FROM booking WHERE status='Pending'"),
             "Pendapatan Hari Ini": "Rp {:,}".format(one("SELECT COALESCE(SUM(total),0) FROM transaksi WHERE tgl LIKE ?", (today,))).replace(",", ".")}
    low = q('SELECT kode AS "Kode",nama AS "Nama",stok AS "Stok" FROM sparepart WHERE stok<=5')
    # pendapatan 7 hari terakhir (hari tanpa transaksi tetap tampil, nilainya 0)
    chart = []
    for i in range(6, -1, -1):
        d = now - timedelta(days=i)
        chart.append({"label": d.strftime("%d/%m"),
                      "val": one("SELECT COALESCE(SUM(total),0) FROM transaksi WHERE tgl LIKE ?", (d.strftime("%Y-%m-%d") + "%",))})
    return page(request, "dashboard.html", cards=cards, low=low, chart=chart)

# ---------- MASTER (generic CRUD) ----------
def f(n, l, ty="text", x=None): return (n, l, ty, x)
M = {
 "customer": ("Customer", [f("nama","Nama"), f("telp","Telepon"), f("plat","Plat Nomor"), f("tipe","Tipe Kendaraan")]),
 "sparepart": ("Sparepart", [f("kode","Kode"), f("nama","Nama"), f("stok","Stok","number"), f("harga_beli","Harga Beli","number"), f("harga_jual","Harga Jual","number")]),
 "jasa": ("Jasa / Layanan", [f("nama","Nama Layanan"), f("harga","Harga","number")]),
 "users": ("User / Staff", [f("nama","Nama"), f("username","Username"), f("password","Password","password"), f("role","Role","select",["admin","kasir","mekanik","customer"])]),
 "booking": ("Booking Servis", [f("kode","Kode Booking"), f("tgl","Tanggal/Jam","datetime-local"), f("plat","Plat Kendaraan"), f("jasa_id","Layanan","ref","jasa"), f("mekanik_id","Mekanik","ref","users"), f("status","Status","select",["Pending","Diproses","Selesai"])]),
}
def chk(t):
    if t not in M: raise HTTPException(404)
@app.get("/m/{t}")
def master(request: Request, t: str, edit: int = 0, err: str = "", u=Depends(auth)):
    chk(t); title, F = M[t]
    ed = (q(f"SELECT * FROM {t} WHERE id=?", (edit,)) or [{}])[0]
    ref = {x[0]: {r["id"]: r["nama"] for r in q(f"SELECT id,nama FROM {x[3]}")} for x in F if x[2] == "ref"}
    return page(request, "master.html", t=t, title=title, F=F, ed=ed, ref=ref, rows=q(f"SELECT * FROM {t} ORDER BY id DESC"), err=err)
@app.post("/m/{t}")
async def master_save(request: Request, t: str, u=Depends(auth)):
    chk(t); form = await request.form(); id = form.get("id"); cols, vals = [], []
    for n, l, ty, x in M[t][1]:
        v = form.get(n, "")
        if ty == "password":
            if not v: continue
            v = sha(v)
        if ty == "ref" and v == "": v = None
        cols.append(n); vals.append(v)
    try:
        if id: ex(f"UPDATE {t} SET " + ",".join(c + "=?" for c in cols) + " WHERE id=?", (*vals, id))
        else: ex(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
    except sqlite3.Error as e: return RedirectResponse(f"/m/{t}?err=Gagal menyimpan: {e}", 303)
    return RedirectResponse(f"/m/{t}", 303)
@app.get("/m/{t}/del/{id}")
def master_del(t: str, id: int, u=Depends(auth)):
    chk(t)
    try: ex(f"DELETE FROM {t} WHERE id=?", (id,))
    except sqlite3.IntegrityError: return RedirectResponse(f"/m/{t}?err=Data dipakai di tabel lain, tidak bisa dihapus", 303)
    return RedirectResponse(f"/m/{t}", 303)

# ---------- TRANSAKSI ----------
def trx_page(request, err="", id=0):
    nota = det = None
    if id:
        n = q("SELECT t.*,c.nama cn,c.plat FROM transaksi t JOIN customer c ON c.id=t.customer_id WHERE t.id=?", (id,))
        if n: nota = n[0]; det = q('SELECT nama AS "Item",jenis AS "Jenis",qty AS "Qty",harga AS "Harga",subtotal AS "Subtotal" FROM detail WHERE transaksi_id=?', (id,))
    return page(request, "transaksi.html", err=err, nota=nota, det=det,
        cust=q("SELECT id,nama,plat FROM customer"), mek=q("SELECT id,nama FROM users WHERE role='mekanik'"),
        sp=q("SELECT * FROM sparepart"), js=q("SELECT * FROM jasa"),
        last=q('SELECT t.id,t.nota AS "Nota",t.tgl AS "Tanggal",c.nama AS "Customer",t.total AS "Total",t.metode AS "Bayar" FROM transaksi t JOIN customer c ON c.id=t.customer_id ORDER BY t.id DESC LIMIT 10'))
@app.get("/transaksi")
def trx(request: Request, id: int = 0, u=Depends(auth)): return trx_page(request, id=id)
@app.post("/transaksi")
async def trx_save(request: Request, u=Depends(auth)):
    form = await request.form(); c = conn()
    try:
        items, tot = [], 0
        for code, qty in zip(form.getlist("it"), form.getlist("qty")):
            qty = int(qty or 0)
            if not code or qty < 1: continue
            j, i = code.split(":")
            if j == "s":
                r = c.execute("SELECT nama,harga_jual h,stok FROM sparepart WHERE id=?", (i,)).fetchone()
                if r["stok"] < qty: raise ValueError(f"Stok {r['nama']} tidak cukup (sisa {r['stok']})")
                c.execute("UPDATE sparepart SET stok=stok-? WHERE id=?", (qty, i)); jenis = "sparepart"
            else:
                r = c.execute("SELECT nama,harga h FROM jasa WHERE id=?", (i,)).fetchone(); jenis = "jasa"
            items.append((jenis, i, r["nama"], qty, r["h"])); tot += qty * r["h"]
        if not items: raise ValueError("Pilih minimal satu item")
        now = datetime.now()
        tid = c.execute("INSERT INTO transaksi(nota,tgl,customer_id,mekanik_id,total,metode) VALUES(?,?,?,?,?,?)",
            ("NT" + now.strftime("%y%m%d%H%M%S"), now.strftime("%Y-%m-%d %H:%M:%S"), form["cust"], form.get("mek") or None, tot, form["metode"])).lastrowid
        c.executemany("INSERT INTO detail(transaksi_id,jenis,item_id,nama,qty,harga,subtotal) VALUES(?,?,?,?,?,?,?)",
            [(tid, j, i, n, k, h, k * h) for j, i, n, k, h in items])
        c.commit(); return RedirectResponse(f"/transaksi?id={tid}", 303)
    except ValueError as e:
        c.rollback(); return trx_page(request, str(e))
    finally: c.close()

# ---------- LAPORAN ----------
@app.get("/laporan")
def laporan(request: Request, r: str = "1", plat: str = "", m: str = "harian", u=Depends(auth)):
    title, rows = report(r, plat, m)
    return page(request, "laporan.html", r=r, plat=plat, title=title, rows=rows)
