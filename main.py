from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session, relationship, declarative_base
from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, ForeignKey, CheckConstraint, or_, create_engine
from sqlalchemy.orm import sessionmaker
from passlib.hash import bcrypt
from apscheduler.schedulers.background import BackgroundScheduler
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime, timedelta
from logic import Board
import secrets, smtplib, os, base64


SECRET_KEY = os.environ.get("SECRET_KEY")
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates/9xt3")

Base = declarative_base()
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(150), unique=True, nullable=False)
    email = Column(String(254), unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    verified = Column(Boolean, nullable=False)


class Game(Base):
    __tablename__ = "game"
    id = Column(Integer, primary_key=True, index=True)
    player_x_id = Column(Integer, ForeignKey("users.id"))
    player_o_id = Column(Integer, ForeignKey("users.id"))
    code = Column(String(6), unique=True, nullable=False)
    status = Column(Boolean, nullable=False)
    player_x = relationship("User", foreign_keys=[player_x_id])
    player_o = relationship("User", foreign_keys=[player_o_id])
    cp_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_activity = Column(DateTime, nullable=True)
    notify = Column(Boolean, nullable=True)
    warned = Column(Boolean, nullable=True)
    state = Column(JSON, nullable=False)
    jf = Column(Boolean, nullable=True)
    winner = Column(String(6), nullable=True)
    resign = Column(Boolean, nullable=True)
    __table_args__ = (CheckConstraint("player_x_id != player_o_id", name="check_different_players"),)


class VerificationSession(Base):
    __tablename__ = "verification_sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code = Column(String(8), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User")


class PasswordResetSession(Base):
    __tablename__ = "password_reset_sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code = Column(String(8), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User")


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    return db.query(User).filter(User.id == uid).first() if uid else None


def gencode(db, model):
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(6))
        if not db.query(model).filter(model.code == code).first():
            return code


def _load_mascot():
    try:
        with open("static/9xt3/mascot.png", "rb") as f:
            return f.read()
    except:
        return None

MASCOT_BYTES = _load_mascot()


def _email_html(body_html: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#09090f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#09090f;padding:40px 0;">
    <tr><td align="center">
      <table width="420" cellpadding="0" cellspacing="0" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:16px;overflow:hidden;">
        <tr>
          <td align="center" style="padding:32px 32px 0;">
            <img src="cid:mascot" width="90" height="90" style="object-fit:cover;display:block;" alt="Okie"/>
            <p style="margin:14px 0 0;font-size:22px;font-weight:600;color:rgba(255,255,255,0.92);letter-spacing:-0.3px;">Okie</p>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 36px 36px;">
            {body_html}
          </td>
        </tr>
        <tr>
          <td style="padding:20px 36px;border-top:1px solid rgba(255,255,255,0.07);">
            <p style="margin:0;font-size:11.5px;color:rgba(255,255,255,0.28);text-align:center;">
              9x9 Tic-Tac-Toe &nbsp;·&nbsp; <a href="https://okie9xt3.com" style="color:rgba(130,158,255,0.6);text-decoration:none;">okie9xt3.com</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _code_block(code: str) -> str:
    letters = "".join(
        f'<span style="display:inline-block;width:36px;height:44px;line-height:44px;text-align:center;'
        f'background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.12);border-radius:7px;'
        f'font-size:20px;font-weight:700;color:rgba(255,255,255,0.9);margin:0 3px;">{c}</span>'
        for c in code
    )
    return f'<div style="text-align:center;margin:22px 0;">{letters}</div>'


def _p(text: str, muted: bool = False) -> str:
    color = "rgba(255,255,255,0.5)" if muted else "rgba(255,255,255,0.78)"
    return f'<p style="margin:0 0 12px;font-size:14px;line-height:1.7;color:{color};">{text}</p>'


def _h(text: str) -> str:
    return f'<p style="margin:0 0 16px;font-size:17px;font-weight:600;color:rgba(255,255,255,0.92);">{text}</p>'


def send_email(to: str, subject: str, html: str):
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to
    msg["Reply-To"] = EMAIL_USER
    alt = MIMEMultipart("alternative")
    msg.attach(alt)
    alt.attach(MIMEText(html, "html"))
    if MASCOT_BYTES:
        img = MIMEImage(MASCOT_BYTES, "png")
        img.add_header("Content-ID", "<mascot>")
        img.add_header("Content-Disposition", "inline", filename="mascot.png")
        msg.attach(img)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.sendmail(EMAIL_USER, to, msg.as_string())


def send_verification_email(email: str, code: str):
    body = (
        _h("Verify your email") +
        _p("Enter this code to verify your Okie account. It expires in 10 minutes.") +
        _code_block(code) +
        _p("If you didn't create an account, you can safely ignore this email.", muted=True)
    )
    send_email(email, "Verify your Okie account", _email_html(body))


def send_reset_email(email: str, code: str):
    body = (
        _h("Reset your password") +
        _p("Enter this code to set a new password. It expires in 10 minutes.") +
        _code_block(code) +
        _p("If you didn't request this, your account is safe — just ignore this email.", muted=True)
    )
    send_email(email, "Reset your Okie password", _email_html(body))


def send_result_email(game, db):
    px = db.query(User).filter(User.id == game.player_x_id).first()
    po = db.query(User).filter(User.id == game.player_o_id).first()
    if not px or not po:
        return
    for player, opponent in [(px, po), (po, px)]:
        is_x = player.id == game.player_x_id
        if game.winner == "TIE":
            headline = "It's a draw."
            detail = f"Your game vs <strong>{opponent.username}</strong> ended with no winner. Good game."
        elif (game.winner == "X" and is_x) or (game.winner == "O" and not is_x):
            headline = "You won!"
            detail = f"You beat <strong>{opponent.username}</strong>{'  by resignation' if game.resign else ''}. Well played."
        else:
            headline = "You lost."
            detail = f"<strong>{opponent.username}</strong> got you{'  — they resigned' if game.resign else ''}. There's always a rematch."
        body = (
            _h(f"Hi {player.username},") +
            _h(headline) +
            _p(detail) +
            _p(f"Game code: <strong style='color:rgba(255,255,255,0.85);letter-spacing:0.06em;'>{game.code}</strong>") +
            _p('<a href="https://okie9xt3.com/home" style="color:rgba(130,158,255,0.85);">Back to your games →</a>', muted=False)
        )
        send_email(player.email, f"Okie — Game {game.code} result", _email_html(body))


def send_expiry_warning(game, db):
    for uid in [game.player_x_id, game.player_o_id]:
        user = db.query(User).filter(User.id == uid).first()
        if user:
            other_id = game.player_o_id if uid == game.player_x_id else game.player_x_id
            other = db.query(User).filter(User.id == other_id).first()
            opp = other.username if other else "your opponent"
            body = (
                _h(f"Hi {user.username},") +
                _p(f"Your game vs <strong>{opp}</strong> has been inactive for 48 hours.") +
                _p(f"If neither of you makes a move, it'll be automatically deleted in <strong style='color:rgba(255,255,255,0.85);'>24 hours</strong>.") +
                _p(f"Game code: <strong style='color:rgba(255,255,255,0.85);letter-spacing:0.06em;'>{game.code}</strong>") +
                _p('<a href="https://okie9xt3.com/home" style="color:rgba(130,158,255,0.85);">Resume the game →</a>')
            )
            send_email(user.email, f"Okie — Game {game.code} expires in 24 hours", _email_html(body))


def send_move_notification(game, db):
    cp = db.query(User).filter(User.id == game.cp_id).first()
    other_id = game.player_o_id if game.cp_id == game.player_x_id else game.player_x_id
    other = db.query(User).filter(User.id == other_id).first()
    if cp and other:
        body = (
            _h(f"Hi {cp.username},") +
            _p(f"<strong>{other.username}</strong> just made a move. It's your turn.") +
            _p(f"Game code: <strong style='color:rgba(255,255,255,0.85);letter-spacing:0.06em;'>{game.code}</strong>") +
            _p('<a href="https://okie9xt3.com/home" style="color:rgba(130,158,255,0.85);">Make your move →</a>')
        )
        send_email(cp.email, f"Okie — Your turn in game {game.code}", _email_html(body))


def send_deletion_email(email: str, username: str):
    body = (
        _h(f"Hi {username},") +
        _p("Your Okie account has been permanently deleted. All your data and game history have been removed.") +
        _p("If you didn't do this or think something's wrong, reply to this email.", muted=True)
    )
    send_email(email, "Your Okie account has been deleted", _email_html(body))


def create_verification(user, db):
    db.query(VerificationSession).filter_by(user_id=user.id).delete()
    code = gencode(db, VerificationSession)
    db.add(VerificationSession(user_id=user.id, code=code, created_at=datetime.utcnow()))
    db.commit()
    send_verification_email(user.email, code)


def cleanup_games():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff_72 = now - timedelta(hours=72)
        cutoff_48 = now - timedelta(hours=48)
        for g in db.query(Game).filter(Game.status == False).all():
            db.delete(g)
        for g in db.query(Game).filter(Game.status == True, Game.last_activity <= cutoff_72, Game.winner == None).all():
            db.delete(g)
        for g in db.query(Game).filter(
            Game.status == True, Game.warned == None, Game.winner == None,
            Game.last_activity <= cutoff_48, Game.last_activity > cutoff_72
        ).all():
            if g.player_x_id and g.player_o_id:
                try: send_expiry_warning(g, db)
                except: pass
            g.warned = True
        db.commit()
    finally:
        db.close()


def notify_players():
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=15)
        for g in db.query(Game).filter(Game.last_activity <= cutoff, Game.notify == True, Game.status == True).all():
            send_move_notification(g, db)
            g.notify = False
        db.commit()
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(cleanup_games, 'interval', hours=3)
scheduler.add_job(notify_players, 'interval', minutes=5)
scheduler.start()


def r(name, req, **ctx):
    return templates.TemplateResponse(f"{name}.html", {"request": req, **ctx})


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request, user=Depends(get_current_user)):
    if user and user.verified:
        return RedirectResponse("/home", 302)
    return r("landing", request, user=user)


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, user=Depends(get_current_user)):
    if user and user.verified:
        return RedirectResponse("/home", 302)
    return r("login", request, user=user)


@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if user and bcrypt.verify(password, user.hashed_password):
        request.session["user_id"] = user.id
        return RedirectResponse("/verify" if not user.verified else "/home", 302)
    return r("login", request, user=None, message="Invalid username or password.")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 302)


@app.get("/register", response_class=HTMLResponse)
async def register_get(request: Request, user=Depends(get_current_user)):
    if user and user.verified:
        return RedirectResponse("/home", 302)
    return r("register", request, user=user)


@app.post("/register")
async def register_post(request: Request, username: str = Form(...), email: str = Form(...),
                        password: str = Form(...), confirmation: str = Form(...), db: Session = Depends(get_db)):
    if password != confirmation:
        return r("register", request, user=None, message="Passwords must match.")
    if db.query(User).filter(User.username == username).first():
        return r("register", request, user=None, message="Username already taken.")
    if db.query(User).filter(User.email == email).first():
        return r("register", request, user=None, message="Email already registered.")
    user = User(username=username, email=email, hashed_password=bcrypt.hash(password), verified=False)
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse("/verify", 302)


@app.get("/verify", response_class=HTMLResponse)
async def verify_get(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/login", 302)
    if user.verified:
        return RedirectResponse("/home", 302)
    create_verification(user, db)
    return r("verify", request, user=user)


@app.post("/verify")
async def verify_post(request: Request, code: str = Form(...), db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/login", 302)
    vs = db.query(VerificationSession).filter_by(user_id=user.id, code=code).first()
    if not vs:
        return r("verify", request, user=user, message="Invalid code.")
    if vs.created_at <= datetime.utcnow() - timedelta(minutes=10):
        db.delete(vs)
        db.commit()
        create_verification(user, db)
        return r("verify", request, user=user, message="Code expired. A new one has been sent.")
    user.verified = True
    db.delete(vs)
    db.commit()
    return RedirectResponse("/home", 302)


@app.post("/cemailv")
async def cemailv_post(request: Request, email: str = Form(...), db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/login", 302)
    existing = db.query(User).filter(User.email == email).first()
    if existing and existing.id != user.id:
        return r("verify", request, user=user, message="Email already registered.", show_email_form=True)
    user.email = email
    db.commit()
    return RedirectResponse("/verify", 302)


@app.get("/sreg")
async def sreg(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if user:
        db.query(VerificationSession).filter_by(user_id=user.id).delete()
        db.delete(user)
        db.commit()
    request.session.clear()
    return RedirectResponse("/", 302)


@app.get("/forgot", response_class=HTMLResponse)
async def forgot_get(request: Request):
    return r("forgot", request, user=None)


@app.post("/forgot")
async def forgot_post(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if user:
        db.query(PasswordResetSession).filter_by(user_id=user.id).delete()
        code = gencode(db, PasswordResetSession)
        db.add(PasswordResetSession(user_id=user.id, code=code, created_at=datetime.utcnow()))
        db.commit()
        send_reset_email(email, code)
    request.session["reset_email"] = email
    return RedirectResponse("/reset", 302)


@app.get("/reset", response_class=HTMLResponse)
async def reset_get(request: Request):
    email = request.session.get("reset_email", "")
    if not email:
        return RedirectResponse("/forgot", 302)
    return r("reset", request, user=None, email=email)


@app.post("/reset")
async def reset_post(request: Request, code: str = Form(...), password: str = Form(...),
                     confirmation: str = Form(...), db: Session = Depends(get_db)):
    email = request.session.get("reset_email", "")
    if not email:
        return RedirectResponse("/forgot", 302)
    if password != confirmation:
        return r("reset", request, user=None, email=email, message="Passwords must match.")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return r("reset", request, user=None, email=email, message="Invalid request.")
    rs = db.query(PasswordResetSession).filter_by(user_id=user.id, code=code).first()
    if not rs:
        return r("reset", request, user=None, email=email, message="Invalid code.")
    if rs.created_at <= datetime.utcnow() - timedelta(minutes=10):
        db.delete(rs)
        db.commit()
        return r("reset", request, user=None, email=email, message="Code expired. Request a new one.")
    user.hashed_password = bcrypt.hash(password)
    db.delete(rs)
    request.session.pop("reset_email", None)
    db.commit()
    return RedirectResponse("/login", 302)

@app.get("/home", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user or not user.verified:
        return RedirectResponse("/", 302)
    games = db.query(Game).filter(
        or_(Game.player_x_id == user.id, Game.player_o_id == user.id),
        Game.status == True, Game.winner == None
    ).order_by(Game.last_activity.desc()).all()
    return r("home", request, user=user, games=games)


@app.get("/play", response_class=HTMLResponse)
async def play(request: Request, user=Depends(get_current_user)):
    if not user or not user.verified:
        return RedirectResponse("/", 302)
    return r("play", request, user=user)


@app.get("/make", response_class=HTMLResponse)
async def make_get(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user or not user.verified:
        return RedirectResponse("/", 302)
    code = gencode(db, Game)
    board = Board()
    game = Game(code=code, player_o_id=None, player_x_id=None, jf=True, state=board.serialize(),
                notify=None, warned=None, cp_id=None, status=True, winner=None, resign=None,
                last_activity=datetime.utcnow())
    db.add(game)
    db.commit()
    return r("make", request, user=user, code=code)


@app.post("/make")
async def make_post(request: Request, code: str = Form(...), db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    game = db.query(Game).filter(Game.code == code.upper()).first()
    if not game:
        return RedirectResponse("/make", 302)
    game.player_o_id = user.id
    game.last_activity = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/game/{game.id}", 302)


@app.get("/join", response_class=HTMLResponse)
async def join_get(request: Request, user=Depends(get_current_user)):
    if not user or not user.verified:
        return RedirectResponse("/", 302)
    return r("join", request, user=user)


@app.post("/join")
async def join_post(request: Request, code: str = Form(...), db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    code = code.upper()
    game = db.query(Game).filter(Game.code == code).first()
    if not game:
        return r("join", request, user=user, message="No game with that code.")
    if datetime.utcnow() - game.last_activity > timedelta(hours=72):
        return r("join", request, user=user, message="Game has expired.")
    if user.id in [game.player_o_id, game.player_x_id]:
        game.jf = True
        game.last_activity = datetime.utcnow()
        db.commit()
        return RedirectResponse(f"/game/{game.id}", 302)
    if game.player_o_id is not None and game.player_x_id is None:
        game.player_x_id = user.id
        game.cp_id = user.id
        game.jf = True
        game.last_activity = datetime.utcnow()
        db.commit()
        return RedirectResponse(f"/game/{game.id}", 302)
    return r("join", request, user=user, message="Cannot join this game.")


@app.get("/game/{game_id}", response_class=HTMLResponse)
async def game_get(request: Request, game_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404)
    if user.id not in [game.player_o_id, game.player_x_id]:
        return r("game", request, user=user, msg=True)
    result = None
    if game.winner:
        if game.winner == "TIE":
            result = "tie"
        elif (game.winner == "X" and user.id == game.player_x_id) or (game.winner == "O" and user.id == game.player_o_id):
            result = "win"
        else:
            result = "loss"
    return r("game", request, user=user, game=game, state=game.state, flag=game.jf, code=game.code, result=result)


@app.get("/game/{game_id}/status")
async def game_status(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        return {"error": "not found"}
    return {
        "cp_id": game.cp_id,
        "status": game.status,
        "last_activity": game.last_activity.isoformat() if game.last_activity else None,
        "player_o_name": game.player_o.username if game.player_o else None
    }


@app.post("/move")
async def move_post(data: dict, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return {"error": "not authenticated"}
    game = db.query(Game).filter(Game.id == data["game_id"]).first()
    if not game:
        return {"error": "game not found"}
    board = Board(game.state)
    try:
        board.make_move(int(data["board"]), int(data["cell"]))
    except ValueError as e:
        return {"error": str(e)}
    board.upd_lm(int(data["board"]), int(data["cell"]))
    game.jf = False
    if game.notify is None:
        game.notify = True
    game.state = board.serialize()
    game.last_activity = datetime.utcnow()
    game.cp_id = game.player_x_id if game.cp_id == game.player_o_id else game.player_o_id
    if board.winner:
        game.status = False
        game.notify = None
        game.winner = board.winner
        db.commit()
        try: send_result_email(game, db)
        except: pass
    else:
        db.commit()
    return {"ok": True, "winner": board.winner}


@app.get("/resign/{code}")
async def resign(request: Request, code: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    game = db.query(Game).filter(Game.code == code.upper()).first()
    if not game:
        raise HTTPException(404)
    game.winner = "O" if user.id == game.player_x_id else "X"
    game.status = False
    game.notify = None
    game.resign = True
    db.commit()
    send_result_email(game, db)
    return RedirectResponse(f"/game/{game.id}", 302)



@app.get("/profile", response_class=HTMLResponse)
async def profile_get(request: Request, user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    return r("profile", request, user=user)


@app.post("/cusern")
async def cusern_post(request: Request, username: str = Form(...), password: str = Form(...),
                      db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    existing = db.query(User).filter(User.username == username).first()
    if existing and existing.id != user.id:
        return r("profile", request, user=user, message="Username already taken.", open_form="username")
    if not bcrypt.verify(password, user.hashed_password):
        return r("profile", request, user=user, message="Incorrect password.", open_form="username")
    user.username = username
    db.commit()
    return RedirectResponse("/profile", 302)


@app.post("/cpwd")
async def cpwd_post(request: Request, password: str = Form(...), confirmation: str = Form(...),
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    if password != confirmation:
        return r("profile", request, user=user, message="Passwords must match.", open_form="password")
    user.hashed_password = bcrypt.hash(password)
    db.commit()
    return RedirectResponse("/profile", 302)


@app.post("/cemail")
async def cemail_post(request: Request, email: str = Form(...), confirmation: str = Form(...),
                      password: str = Form(...), db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    if email != confirmation:
        return r("profile", request, user=user, message="Emails must match.", open_form="email")
    if not bcrypt.verify(password, user.hashed_password):
        return r("profile", request, user=user, message="Incorrect password.", open_form="email")
    existing = db.query(User).filter(User.email == email).first()
    if existing and existing.id != user.id:
        return r("profile", request, user=user, message="Email already registered.", open_form="email")
    user.email = email
    user.verified = False
    db.commit()
    return RedirectResponse("/verify", 302)


@app.post("/delete")
async def delete_post(request: Request, password: str = Form(...),
                      db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/", 302)
    if not bcrypt.verify(password, user.hashed_password):
        return r("profile", request, user=user, message="Incorrect password.", open_form="delete")
    saved_email, saved_username = user.email, user.username
    db.delete(user)
    db.commit()
    request.session.clear()
    send_deletion_email(saved_email, saved_username)
    return RedirectResponse("/", 302)


@app.get("/rules", response_class=HTMLResponse)
async def rules(request: Request, user=Depends(get_current_user)):
    return r("rules", request, user=user)


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request, user=Depends(get_current_user)):
    return r("about", request, user=user)


@app.exception_handler(404)
@app.exception_handler(405)
async def handler_404(request, __):
    return r("404", request, user=None)
