from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import secrets
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Boolean, create_engine, ForeignKey, DateTime, JSON
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from passlib.hash import bcrypt
from sqlalchemy.exc import IntegrityError
import math
import random
import os
from sqlalchemy import CheckConstraint
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from logic import Board
import smtplib
from email.message import EmailMessage

# Config
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL",  "sqlite:///./db.sqlite3")

# App Init
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# DB Setup
Base = declarative_base()
connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Models
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
    total_game_time = Column(DateTime, nullable=True)
    notify = Column(Boolean, nullable = True)
    state = Column(JSON, nullable=False)
    jf = Column(Boolean, nullable=True)
    winner = Column(String(6), nullable = True)
    resign = Column(Boolean, nullable=True)
    __table_args__ = (CheckConstraint("player_x_id != player_o_id", name="check_different_players"),)
    
class VerificationSession(Base):
    __tablename__ = "verification_sessions"

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
        
def getcuser(request: Request, db: Session):
    user_id = request.session.get("user_id")
    if user_id:
        return db.query(User).filter(User.id == user_id).first()
    return None
  
@app.get("/login", response_class=HTMLResponse)
async def loginv(request: Request):
    return templates.TemplateResponse("9xt3/login.html", {"request": request})

@app.post("/login")
async def loginp(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if user and bcrypt.verify(password, user.hashed_password):
        request.session["user_id"] = user.id
        if not user.verified:
            return RedirectResponse(url="/verify/login", status_code=302)
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("9xt3/login.html", {"request": request, "message": "Invalid username and/or password."})

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)

@app.get("/register", response_class=HTMLResponse)
async def registerv(request: Request):
    return templates.TemplateResponse("9xt3/register.html", {"request": request})

@app.post("/register")
async def registerp(request: Request, username: str = Form(...), email: str = Form(...), password: str = Form(...), confirmation: str = Form(...), db: Session = Depends(get_db)):
    if password != confirmation:
        return templates.TemplateResponse("9xt3/register.html", {"request": request, "message": "Passwords must match."})
    if db.query(User).filter(User.username == username).first():
        return templates.TemplateResponse("9xt3/register.html", {"request": request, "message": "Username already taken. Login or try another username."})
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("9xt3/register.html", {"request": request, "message": "Email already registered. Login or try another email."})
    user = User(username=username, email=email, hashed_password=bcrypt.hash(password), verified=False)
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/verify", status_code=302)
    
    
VCODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def genvcode(db: Session):
    while True:
        code = "".join(secrets.choice(VCODE_ALPHABET) for _ in range(6))
        exists = db.query(VerificationSession).filter_by(code=code).first()
        if not exists:
            return code

def send_verification_email(email, code):
    msg = EmailMessage()
    msg["Subject"] = "Verify your email for Okie 9x9 TicTacToe game account"
    msg["From"] = EMAIL_FROM
    msg["To"] = email
    msg["Reply-To"] = EMAIL_USER
    body = f"Hi, \n Your verification code is: {code} \n This code expires in 10 minutes. \n If you didn’t request this, you can safely ignore this email."
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)
            
@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, db: Session = Depends(get_db)):
    user = getcuser(request, db)
    
    if not user:
        return templates.TemplateResponse("9xt3/verify.html", {"request": request, "flag": True})

    if user.verified:
        return RedirectResponse("/", status_code=302)

    db.query(VerificationSession).filter_by(user_id=user.id).delete()
    code = genvcode(db)
    vs = VerificationSession(user_id=user.id, code=code, created_at = datetime.utcnow())
    db.add(vs)
    db.commit()
    send_verification_email(user.email, code)
    msg = "Please verify your email to signup."
    return templates.TemplateResponse("9xt3/verify.html", {"request": request, "email": user.email, "msg": msg, "user": user})
    
@app.get("/verify/login", response_class=HTMLResponse)
async def verify_page(request: Request, db: Session = Depends(get_db)):
    user = getcuser(request, db)
    
    if not user:
        return templates.TemplateResponse("9xt3/verify.html", {"request": request, "flag": True})

    if user.verified:
        return RedirectResponse("/", status_code=302)

    db.query(VerificationSession).filter_by(user_id=user.id).delete()
    code = genvcode(db)
    vs = VerificationSession(user_id=user.id, code=code, created_at = datetime.utcnow())
    db.add(vs)
    db.commit()
    send_verification_email(user.email, code)
    msg = "Please verify your email to login."
    return templates.TemplateResponse("9xt3/verify.html", {"request": request, "email": user.email, "msg": msg, "user": user})
    
@app.get("/verify/msg", response_class=HTMLResponse)
async def verify_page(request: Request, db: Session = Depends(get_db)):
    user = getcuser(request, db)
    
    if not user:
        return templates.TemplateResponse("9xt3/verify.html", {"request": request, "flag": True})

    if user.verified:
        return RedirectResponse("/", status_code=302)

    db.query(VerificationSession).filter_by(user_id=user.id).delete()
    code = genvcode(db)
    vs = VerificationSession(user_id=user.id, code=code, created_at = datetime.utcnow())
    db.add(vs)
    db.commit()
    send_verification_email(user.email, code)
    msg = "Please verify your email to login."
    msg2 = "Expired code."
    return templates.TemplateResponse("9xt3/verify.html", {"request": request, "email": user.email, "msg": msg, "user": user, "message": msg2})

@app.post("/verify", response_class=HTMLResponse)
async def verify_submit(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    user = getcuser(request, db)
    vs = db.query(VerificationSession).filter_by(user_id=user.id,code=code).first()

    if not vs:
        return templates.TemplateResponse("9xt3/verify.html", {"request": request, "message": "Invalid code."} )
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    if vs.created_at <= cutoff:
        db.delete(vs)
        db.commit()
        return RedirectResponse("/verify/msg", status_code=302)

    user.verified = True
    db.delete(vs)
    db.commit()

    return RedirectResponse("/", status_code=302)
    
@app.get("/cpwd", response_class=HTMLResponse)
async def cpwdg(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/cpwd.html", {"request": request, "user": getcuser(request, db)})
    
@app.post("/cpwd")
async def cpwdp(request: Request, password: str = Form(...), confirmation: str = Form(...), email: str = Form(...), db: Session = Depends(get_db)):
    if password != confirmation:
        return templates.TemplateResponse("9xt3/cpwd.html", {"request": request, "message": "Passwords must match."})
    user = getcuser(request, db)
    if email != user.email:
                return templates.TemplateResponse("9xt3/cpwd.html", {"request": request, "message": "No account with this email."})
    user.hashed_password = bcrypt.hash(password)
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/login", status_code=302)
    
@app.get("/cusern", response_class=HTMLResponse)
async def cuserng(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/cusern.html", {"request": request, "user": getcuser(request, db)})
    
@app.post("/cusern")
async def cusernp(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = getcuser(request, db)
    if db.query(User).filter(User.username == username).first() and user.username != db.query(User).filter(User.username == username).first():
        return templates.TemplateResponse("9xt3/cusern.html", {"request": request, "message": "Username is already taken ."})
    if not bcrypt.verify(password, user.hashed_password):
        return templates.TemplateResponse("9xt3/cusern.html", {"request": request, "message": "Password is already wrong."})
    user.username = username
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/login", status_code=302)
    
@app.get("/cemail", response_class=HTMLResponse)
async def cemailg(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/cemail.html", {"request": request, "user": getcuser(request, db)})
    
@app.post("/cemail")
async def cemailp(request: Request, email: str = Form(...), confirmation: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = getcuser(request, db)
    if not bcrypt.verify(password, user.hashed_password):
        return templates.TemplateResponse("9xt3/cemail.html", {"request": request, "message": "Password is already wrong."})
    if db.query(User).filter(User.email == email).first() and user.email != db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("9xt3/cemail.html", {"request": request, "message": "Email is already registered."})
    user.email = email
    user.verified = False
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/verify", status_code=302)
    
@app.get("/cemailv", response_class=HTMLResponse)
async def cemailvg(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/cemailv.html", {"request": request, "user": getcuser(request, db)})
    
@app.post("/cemailv")
async def cemailvp(request: Request, email: str = Form(...), confirmation: str = Form(...), db: Session = Depends(get_db)):
    user = getcuser(request, db)
    if db.query(User).filter(User.email == email).first() and user.email != db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("9xt3/cemail.html", {"request": request, "message": "Email is already registered."})
    user.email = email
    user.verified = False
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/verify", status_code=302)
    
@app.get("/delete", response_class=HTMLResponse)
async def cemailg(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/delete.html", {"request": request, "user": getcuser(request, db)})
    
@app.post("/delete")
async def cemailp(request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    user = getcuser(request, db)
    if not bcrypt.verify(password, user.hashed_password):
        return templates.TemplateResponse("9xt3/delete.html", {"request": request, "message": "Password is already wrong."})
    u = db.query(User).filter(User.id == user.id).first()
    db.delete(u)
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=302)
    
@app.get("/sreg", response_class=HTMLResponse)
async def sreg(request: Request, db: Session = Depends(get_db)):
    user = getcuser(request, db)
    u = db.query(User).filter(User.id == user.id).first()
    db.query(VerificationSession).filter_by(user_id=user.id).delete()
    db.delete(u)
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=302)
    
def cleanup_archived_games():
    db = SessionLocal()
    try:
        games_to_delete = db.query(Game).filter(Game.status == False).all()
        for g in games_to_delete:
            db.delete(g)
        db.commit()
    finally:
        db.close()
        
def send_la_email(email, code, other):
    msg = EmailMessage()
    msg["Subject"] = f"Continue Okie 9x9 TicTacToe game {code} with {other.username}."
    msg["From"] = "Okie <noreply.okie9x9tictactoe@gmail.com>"
    msg["To"] = email
    msg["Reply-To"] = "noreply.okie9x9tictactoe@gmail.com"
    body = f"Hi, \n Player {other.username} has made a move in the Okie 9x9 TicTacToe game with game code {code}. \n It's your turn... \n"
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login("noreply.okie9x9tictactoe@gmail.com", "pknc rolj kswm xvvl")
        smtp.send_message(msg)
        
def notify():
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=15)
        games_to_notify = db.query(Game).filter(Game.last_activity <= cutoff, Game.notify == True).all()
        games_to_reset = db.query(Game).filter(Game.last_activity > cutoff, Game.notify == False).all()
        for g in games_to_notify:
            user = db.query(User).filter(User.id == g.cp_id).first()
            other_id = g.player_o_id if g.cp_id == g.player_x_id else g.player_x_id
            other_u = db.query(User).filter(User.id == other_id).first()
            send_la_email(user.email, g.code, other_u)
            g.notify = False
        for g in games_to_reset:
            g.notify = True
        db.commit()
    finally:
        db.close()

scheduler = BackgroundScheduler()
scheduler.add_job(cleanup_archived_games, 'interval', hours=3)
scheduler.add_job(notify, 'interval', minutes=5)
scheduler.start()
    
    
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def gencode(db: Session):
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(6))
        code = code.upper()
        exists = db.query(Game).filter(Game.code == code).first()
        if not exists:
            return code
            
        
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/home.html", {"request": request, "user": getcuser(request, db)})
    
@app.get("/home", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/home.html", {"request": request, "user": getcuser(request, db)})
    
@app.get("/rules", response_class=HTMLResponse)
async def rules(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/rules.html", {"request": request, "user": getcuser(request, db)})
    
@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/profile.html", {"request": request, "user": getcuser(request, db)})

@app.get("/join", response_class=HTMLResponse)
async def join(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("9xt3/join.html", {"request": request, "user": getcuser(request, db)})

@app.post("/join", response_class=HTMLResponse)
async def joinp(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    user = getcuser(request, db)
    code = code.upper()
    game = db.query(Game).filter(Game.code == code).first()

    if not game:
        m = True
        return templates.TemplateResponse( "9xt3/join.html",  {"request": request, "m": m, "user": user, "message":"No game with that join code."})
        
    if game.last_activity:
        if datetime.utcnow() - game.last_activity > timedelta(hours=72):
            game.status = False
            m = True
            return templates.TemplateResponse( "9xt3/join.html",  {"request": request, "m": m, "user": user, "message":"Game expired due to inactivity."})

    if game.player_o_id == user.id:
        game.jf = True
        game.last_activity = datetime.utcnow()
        db.commit()
    elif game.player_x_id == user.id:
        game.jf = True
        game.last_activity = datetime.utcnow()
        db.commit()
    elif game.player_x_id is None:
        game.player_x_id = user.id
        game.cp_id = user.id
        game.status = True
        game.jf = True
        game.last_activity = datetime.utcnow()
        db.commit()
    else:
        m = True
        return templates.TemplateResponse( "9xt3/join.html",{"request": request, "m": m, "user": user, "message": "Misjoining attempt to another private game."})

    return RedirectResponse(url=f"/game/{game.id}", status_code=302)
    
@app.get("/join/{code}")
async def join_with_code(request: Request, code: str,db: Session = Depends(get_db)):
    code = code.upper()
    game = db.query(Game).filter(Game.code == code).first()
    return RedirectResponse(url=f"/game/{game.id}", status_code=302)

@app.get("/resign/{code}")
async def join_with_code(request: Request, code: str,db: Session = Depends(get_db)):
    code = code.upper()
    game = db.query(Game).filter(Game.code == code).first()
    user = getcuser(request, db)
    game.winner = "O" if  user.id == game.player_x_id else "X"
    game.status = False
    game.notify = None
    game.resign = True
    db.commit()
    return RedirectResponse(url=f"/game/{game.id}", status_code=302)

@app.get("/make", response_class=HTMLResponse)
async def make(request: Request, db: Session = Depends(get_db)):
    user = getcuser(request, db)
    code = gencode(db)
    board = Board()
    game = Game(
        code=code,
        player_o_id=None,
        player_x_id=None,
        jf=True,
        state=board.serialize(),
        notify = None,
        cp_id=None,
        status=True,
        winner=None,
        resign = None,
        last_activity=datetime.utcnow(),
        total_game_time = datetime.utcnow(),
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return templates.TemplateResponse("9xt3/make.html",{ "request": request, "code": game.code, "user": user,})

@app.post("/make", response_class=HTMLResponse)
async def makep(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    user = getcuser(request, db)
    code = code.upper()
    game = db.query(Game).filter(Game.code == code).first()
    game.player_o_id=user.id
    game.last_activity = datetime.utcnow()
    db.commit()
    db.refresh(game)
    return RedirectResponse(url=f"/join/{game.code}", status_code=302)


@app.get("/game/{game_id}", response_class=HTMLResponse)
async def game(request: Request, game_id: int, db: Session = Depends(get_db)):
    user = getcuser(request, db)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code = 404)
        
    if user.id == game.player_o_id or user.id == game.player_x_id:
        pass
    else:
        return templates.TemplateResponse( "9xt3/game.html",{ "request": request, "msg": True})
        
    return templates.TemplateResponse( "9xt3/game.html",{ "request": request, "code": game.code, "user": user, "game": game, "state": game.state, "flag": game.jf})
    

@app.get("/game/{game_id}/status")
async def game_status(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        return {"error": "Game not found"}
    
    return {
        "cp_id": game.cp_id,
        "status": game.status,
        "last_activity": game.last_activity.isoformat() if game.last_activity else None,
        "player_o_name": game.player_o.username if game.player_o else None
    }
    
@app.post("/move")
async def make_move(data: dict, request: Request, db: Session = Depends(get_db)):
    user = getcuser(request, db)
    game = db.query(Game).filter(Game.id == data["game_id"]).first()
    board = Board(game.state)

    try:
        board.make_move(int(data["board"]), int(data["cell"]))
    except ValueError as e:
        return {"error": str(e)}

    game.jf = False
    game.notify = True
    board.upd_lm(int(data["board"]), int(data["cell"]))
    game.state = board.serialize()
    game.last_activity = datetime.utcnow()
    game.cp_id = (
        game.player_x_id if game.cp_id == game.player_o_id
        else game.player_o_id
    )
    
    if board.winner:
        game.status = False
        game.notify = None
        game.winner = board.winner

    db.commit()

    return {
        "ok": True,
        "winner": board.winner,
    }
    
@app.exception_handler(404)
async def custom_404_handler(request, __):
    return templates.TemplateResponse("9xt3/404.html", {"request": request})


